import hashlib
import http.client
import ipaddress
import json
import os
import re
import sqlite3
import ssl
import sys
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SUBCATEGORY = "SECURITY_INTRUSION_PREVENTION"
DAY_MS = 86_400_000
PAGE_SIZE = 100
GEO_DB = "/opt/MISP-Dashboard/data/GeoLite2-City/GeoLite2-City.mmdb"


class UniFi:
    def __init__(self):
        self.url = urlsplit(os.environ["UP_UNIFI_DEFAULT_URL"])
        if self.url.scheme != "https" or not self.url.hostname:
            raise ValueError("UniFi HTTPS URL required")
        self.pin = os.environ["UNIFI_TLS_SHA256"]
        if not re.fullmatch(r"[0-9a-f]{64}", self.pin):
            raise ValueError("UniFi certificate pin required")
        self.headers = {"Content-Type": "application/json"}
        self.cookies = SimpleCookie()

    def post(self, path, payload):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with_connection = http.client.HTTPSConnection(
            self.url.hostname, self.url.port or 443, timeout=12, context=context
        )
        try:
            with_connection.connect()
            cert = with_connection.sock.getpeercert(binary_form=True)
            if hashlib.sha256(cert).hexdigest() != self.pin:
                raise RuntimeError("UniFi TLS certificate changed; verify before updating pin")
            headers = {**self.headers, "Cookie": "; ".join(cookie.OutputString().split(";", 1)[0] for cookie in self.cookies.values())}
            with_connection.request("POST", path, json.dumps(payload), headers)
            response = with_connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"UniFi {path} returned HTTP {response.status}")
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    self.cookies.load(value)
                if name.lower() == "x-csrf-token":
                    self.headers["X-CSRF-Token"] = value
            return json.load(response)
        finally:
            with_connection.close()

    def events(self, start_ms, end_ms):
        self.post("/api/auth/login", {
            "username": os.environ["UP_UNIFI_DEFAULT_USER"],
            "password": os.environ["UP_UNIFI_DEFAULT_PASS"],
        })
        if not self.cookies:
            raise RuntimeError("UniFi login did not provide a session cookie")
        for window_start in range(start_ms, end_ms + 1, DAY_MS):
            yield from self.events_page(window_start, min(window_start + DAY_MS - 1, end_ms))

    def events_page(self, start_ms, end_ms):
        for page in range(10):
            response = self.post("/proxy/network/v2/api/site/default/system-log/all", {
                "timestampFrom": start_ms, "timestampTo": end_ms,
                "categories": ["SECURITY"], "subcategories": [SUBCATEGORY],
                "pageNumber": page, "pageSize": PAGE_SIZE,
            })
            if isinstance(response, list) and len(response) == 1 and isinstance(response[0], dict) and "data" in response[0]:
                response = response[0]
            rows = response.get("data") if isinstance(response, dict) else response
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise RuntimeError("Unexpected UniFi system-log response")
            for row in rows:
                if row.get("subcategory") == SUBCATEGORY and start_ms <= int(row["timestamp"]) <= end_ms:
                    yield row
            if len(rows) < PAGE_SIZE:
                return
        raise RuntimeError("UniFi threat query hit the page limit; delivery is incomplete")


def normalize(row, geo_db):
    identifier = row.get("id") or row.get("external_id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]{1,80}", identifier, re.ASCII):
        raise ValueError("Threat event has no usable ID")
    parameters = row.get("parameters") or {}
    source = parameters.get("SRC_IP") or {}
    scope, country, lat, lon = "unknown", "-", "-", "-"
    try:
        address = ipaddress.ip_address(source.get("id") or source.get("name") or "")
    except ValueError:
        if parameters.get("SRC_CLIENT"):
            scope = "local"
    else:
        address = getattr(address, "ipv4_mapped", None) or address
        if address.is_global and not address.is_multicast and not address.is_reserved:
            scope = "public"
            geo = geo_db.get(str(address)) or {}
            code = (geo.get("country") or {}).get("iso_code", "")
            location = geo.get("location") or {}
            if re.fullmatch(r"[A-Z]{2}", code):
                country = code
                latitude, longitude = location.get("latitude"), location.get("longitude")
                if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
                    if -90 <= latitude <= 90 and -180 <= longitude <= 180 and (latitude or longitude):
                        lat, lon = f"{latitude:.4f}", f"{longitude:.4f}"
        else:
            scope = "local"
    outcome = {"THREAT_BLOCKED": "blocked", "THREAT_DETECTED": "detected"}.get(row.get("event"), "other")
    severity = row.get("severity")
    if severity not in {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", "CRITICAL"}:
        severity = "UNKNOWN"
    line = f"country={country} asname=- asnumber=0 lat={lat} lon={lon} action={outcome} severity={severity} scope={scope} event_id={identifier}"
    return identifier, int(row["timestamp"]), line


def push(streams):
    request = Request(os.environ["LOKI_PUSH_URL"], data=json.dumps({"streams": streams}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        if response.status != 204:
            raise RuntimeError(f"Loki push returned HTTP {response.status}")


def main(check=False):
    import maxminddb

    end_ms = time.time_ns() // 1_000_000
    with sqlite3.connect(":memory:" if check else "/state/events.sqlite3") as database:
        database.execute("CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, timestamp_ms INTEGER NOT NULL)")
        database.execute("CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL)")
        database.execute("BEGIN IMMEDIATE")
        checkpoint = database.execute("SELECT timestamp_ms FROM cursor WHERE id=1").fetchone()
        floor = end_ms - 7 * DAY_MS + 600_000
        start_ms = max(floor, checkpoint[0] - 600_000) if checkpoint else floor
        with maxminddb.open_database(GEO_DB) as geo_db:
            events = {event[0]: event for event in (normalize(row, geo_db) for row in UniFi().events(start_ms, end_ms))}
        new = [event for event in sorted(events.values(), key=lambda row: row[1:])
               if not database.execute("SELECT 1 FROM sent WHERE id=?", (event[0],)).fetchone()]
        mapped = sum(" lon=- " not in row[2] for row in new)
        if not check:
            streams = [{"stream": {"job": "unifi_ips_health", "source": "unifi"},
                        "values": [[str(time.time_ns()), f"status=ok delivered={len(new)} mapped={mapped}"]]}]
            if new:
                streams.append({"stream": {"job": "unifi_ips", "source": "unifi"},
                                "values": [[str(stamp * 1_000_000), line] for _, stamp, line in new]})
            push(streams)
            database.executemany("INSERT INTO sent VALUES (?, ?)", [(key, stamp) for key, stamp, _ in new])
            database.execute("INSERT OR REPLACE INTO cursor VALUES (1, ?)", (end_ms,))
            database.execute("DELETE FROM sent WHERE timestamp_ms < ?", (floor,))
        print(f"UniFi IPS: {len(events)} records read; {len(new)} new; {mapped} geolocated public origins")


if __name__ == "__main__":
    try:
        main(check="--check" in sys.argv)
    except (RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as error:
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        sys.exit(f"UniFi IPS collection failed: {detail}")
