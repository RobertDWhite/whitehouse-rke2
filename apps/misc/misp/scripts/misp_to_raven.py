#!/usr/bin/env python3
import asyncio
import collections
import ipaddress
import json
import os
import random
import ssl
import urllib.request

from aiohttp import WSMsgType, web
import zmq
import zmq.asyncio

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5678"))
MISP_API_URL = os.environ.get("MISP_API_URL", "https://nginx.misp.svc.cluster.local").rstrip("/")
MISP_API_KEY = os.environ["MISP_API_KEY"]
MISP_ZMQ_URL = os.environ.get("MISP_ZMQ_URL", "tcp://misp-zmq.misp.svc.cluster.local:50000")
BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "50"))
BACKFILL_INTERVAL_SECONDS = int(os.environ.get("BACKFILL_INTERVAL_SECONDS", "300"))
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "120"))
SEEN_LIMIT = int(os.environ.get("SEEN_LIMIT", "4000"))
REPLAY_INTERVAL_SECONDS = float(os.environ.get("REPLAY_INTERVAL_SECONDS", "4"))
TRICKLE_DELAY_SECONDS = float(os.environ.get("TRICKLE_DELAY_SECONDS", "0.5"))
DESTINATION_COORDS = os.environ.get("DESTINATION_COORDS", "39.491,-84.306").strip()
DESTINATION_LABEL = os.environ.get("DESTINATION_LABEL", "Home").strip()
MISP_NETWORK_TYPES = [
    item.strip()
    for item in os.environ.get(
        "MISP_NETWORK_TYPES",
        "ip-src,ip-dst,ip-src|port,ip-dst|port,domain|ip,hostname|ip,ip-src|ip-dst",
    ).split(",")
    if item.strip()
]

TYPE_COLORS = {
    "ip-src": "#ef476f",
    "ip-dst": "#ffd166",
    "ip-src|port": "#f4a261",
    "ip-dst|port": "#f4a261",
    "domain|ip": "#06d6a0",
    "hostname|ip": "#4cc9f0",
    "ip-src|ip-dst": "#ff6b6b",
}
CROWDSEC_COLOR = "#ef4444"

DST_LAT, DST_LNG = (float(x) for x in DESTINATION_COORDS.split(","))

CLIENTS = set()
HISTORY = collections.deque(maxlen=HISTORY_LIMIT)
SEEN_QUEUE = collections.deque()
SEEN_SET = set()
GEO_CACHE = {}
SSL_CONTEXT = ssl._create_unverified_context()


def remember(item_id):
    if item_id in SEEN_SET:
        return False
    SEEN_QUEUE.append(item_id)
    SEEN_SET.add(item_id)
    if len(SEEN_QUEUE) > SEEN_LIMIT:
        old = SEEN_QUEUE.popleft()
        SEEN_SET.discard(old)
    return True


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_private_ip(value):
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return True


# --- GeoIP via ip-api.com with caching ---

def _geolocate_ip(ip_str):
    if ip_str in GEO_CACHE:
        return GEO_CACHE[ip_str]
    if is_private_ip(ip_str):
        GEO_CACHE[ip_str] = None
        return None
    try:
        url = f"http://ip-api.com/json/{ip_str}?fields=status,lat,lon,country,countryCode,city"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                result = {
                    "lat": data["lat"],
                    "lng": data["lon"],
                    "country": data.get("country", ""),
                    "countryCode": data.get("countryCode", ""),
                    "city": data.get("city", ""),
                }
                GEO_CACHE[ip_str] = result
                return result
    except Exception:
        pass
    GEO_CACHE[ip_str] = None
    return None


def _batch_geolocate(ip_list):
    uncached = [ip for ip in set(ip_list) if ip not in GEO_CACHE and not is_private_ip(ip)]
    if not uncached:
        return
    for i in range(0, len(uncached), 100):
        batch = uncached[i : i + 100]
        try:
            body = json.dumps(
                [{"query": ip, "fields": "status,query,lat,lon,country,countryCode,city"} for ip in batch]
            ).encode()
            req = urllib.request.Request(
                "http://ip-api.com/batch",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                results = json.loads(resp.read().decode())
                for result in results:
                    ip = result.get("query", "")
                    if result.get("status") == "success":
                        GEO_CACHE[ip] = {
                            "lat": result["lat"],
                            "lng": result["lon"],
                            "country": result.get("country", ""),
                            "countryCode": result.get("countryCode", ""),
                            "city": result.get("city", ""),
                        }
                    else:
                        GEO_CACHE[ip] = None
        except Exception as exc:
            print(f"batch geolocate error: {exc}", flush=True)


async def geolocate(ip_str):
    return await asyncio.to_thread(_geolocate_ip, ip_str)


async def batch_geolocate(ip_list):
    await asyncio.to_thread(_batch_geolocate, ip_list)


# --- Event construction (flat format for globe.gl) ---

def make_arc(src_ip, geo, metadata, color, source):
    return {
        "srcIp": src_ip,
        "srcLat": geo["lat"],
        "srcLng": geo["lng"],
        "srcCountry": geo.get("country", ""),
        "srcCity": geo.get("city", ""),
        "dstLat": DST_LAT,
        "dstLng": DST_LNG,
        "dstLabel": DESTINATION_LABEL,
        "color": color,
        "source": source,
        **metadata,
    }


# --- MISP attribute processing ---

def split_compound_value(attr):
    if attr.get("value1") and attr.get("value2"):
        return str(attr["value1"]), str(attr["value2"])
    value = str(attr.get("value", ""))
    if "|" not in value:
        return value, ""
    left, right = value.split("|", 1)
    return left.strip(), right.strip()


def extract_misp_metadata(attr, payload):
    event = payload.get("Event") or attr.get("Event") or {}
    md = {}
    attr_type = str(attr.get("type", ""))
    if attr_type:
        md["label"] = attr_type
    category = str(attr.get("category", ""))
    if category:
        md["category"] = category
    info = event.get("info", "")
    if info:
        md["event"] = info
    org = (event.get("Orgc") or {}).get("name", "")
    if org:
        md["org"] = org
    comment = attr.get("comment", "")
    if comment:
        md["comment"] = comment
    return md


def extract_ip(attr):
    attr_type = str(attr.get("type", "")).lower()
    if attr_type in {"ip-src", "ip-dst"}:
        value = str(attr.get("value", "")).strip()
        return value if is_ip(value) else None
    if attr_type in {"ip-src|port", "ip-dst|port"}:
        ip_value, _ = split_compound_value(attr)
        return ip_value if is_ip(ip_value) else None
    if attr_type in {"domain|ip", "hostname|ip"}:
        _, ip_value = split_compound_value(attr)
        return ip_value if is_ip(ip_value) else None
    if attr_type == "ip-src|ip-dst":
        src_value, _ = split_compound_value(attr)
        return src_value if is_ip(src_value) else None
    return None


def process_attribute(attr, payload):
    attr_type = str(attr.get("type", "")).lower()
    if attr_type not in MISP_NETWORK_TYPES:
        return None
    attr_id = str(attr.get("id", ""))
    if not attr_id or not remember(attr_id):
        return None
    ip = extract_ip(attr)
    if not ip:
        return None
    geo = GEO_CACHE.get(ip) if ip in GEO_CACHE else _geolocate_ip(ip)
    if not geo:
        return None
    color = TYPE_COLORS.get(attr_type, "#ffd166")
    metadata = extract_misp_metadata(attr, payload)
    return make_arc(ip, geo, metadata, color, "misp")


# --- CrowdSec processing ---

def process_crowdsec(payload):
    if not isinstance(payload, dict):
        return None
    source_ip = str(payload.get("ip", "")).strip()
    if not is_ip(source_ip):
        return None
    scenario = str(payload.get("scenario", "")).strip()
    decision_type = str(payload.get("type", "")).strip()
    scope = str(payload.get("scope", "")).strip()
    timestamp = str(payload.get("timestamp", "")).strip()

    event_id = str(payload.get("id", "")).strip() or "|".join(
        ["crowdsec", source_ip, scenario, decision_type, scope, timestamp]
    )
    if not remember(event_id):
        return None

    lat = payload.get("latitude")
    lng = payload.get("longitude")
    country = str(payload.get("country", "")).strip()

    if lat is not None and lng is not None:
        geo = {"lat": float(lat), "lng": float(lng), "country": country, "city": ""}
    else:
        geo = _geolocate_ip(source_ip)
        if not geo:
            return None

    metadata = {"label": scenario or "crowdsec", "category": decision_type}
    if scope:
        metadata["scope"] = scope
    return make_arc(source_ip, geo, metadata, CROWDSEC_COLOR, "crowdsec")


# --- MISP API ---

def fetch_recent_attributes():
    body = json.dumps(
        {
            "returnFormat": "json",
            "limit": BACKFILL_LIMIT,
            "sort": "timestamp",
            "direction": "desc",
            "type": MISP_NETWORK_TYPES,
        }
    ).encode()
    request = urllib.request.Request(
        f"{MISP_API_URL}/attributes/restSearch",
        data=body,
        headers={
            "Authorization": MISP_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, context=SSL_CONTEXT, timeout=30) as response:
        payload = json.loads(response.read().decode())
    return payload.get("response", {}).get("Attribute", [])


# --- Broadcast & loops ---

async def broadcast(events):
    if not events:
        return
    for event in events:
        HISTORY.append(event)
    if not CLIENTS:
        return
    message = json.dumps(events)
    stale = set()
    for client in list(CLIENTS):
        try:
            await client.send_str(message)
        except Exception:
            stale.add(client)
    CLIENTS.difference_update(stale)


async def backfill_loop():
    while True:
        try:
            attributes = await asyncio.to_thread(fetch_recent_attributes)
            ips = [extract_ip(attr) for attr in attributes]
            ips = [ip for ip in ips if ip]
            if ips:
                await batch_geolocate(ips)
            events = []
            for attr in reversed(attributes):
                ev = process_attribute(attr, {"Attribute": attr, "Event": attr.get("Event", {})})
                if ev:
                    events.append(ev)
            await broadcast(events)
        except Exception as exc:
            print(f"backfill error: {exc}", flush=True)
        await asyncio.sleep(BACKFILL_INTERVAL_SECONDS)


async def zmq_loop():
    context = zmq.asyncio.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(MISP_ZMQ_URL)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    while True:
        try:
            raw_message = await sock.recv()
            topic, _, body = raw_message.decode("utf-8", "ignore").partition(" ")
            if topic not in {"misp_json_attribute", "misp_json_sighting"}:
                continue
            payload = json.loads(body)
            attr = payload.get("Attribute")
            if isinstance(attr, dict):
                ip = extract_ip(attr)
                if ip:
                    await geolocate(ip)
                ev = process_attribute(attr, payload)
                if ev:
                    await broadcast([ev])
        except Exception as exc:
            print(f"zmq error: {exc}", flush=True)
            await asyncio.sleep(2)


async def replay_loop():
    while True:
        await asyncio.sleep(REPLAY_INTERVAL_SECONDS)
        if not CLIENTS or not HISTORY:
            continue
        event = random.choice(list(HISTORY))
        message = json.dumps([event])
        stale = set()
        for client in list(CLIENTS):
            try:
                await client.send_str(message)
            except Exception:
                stale.add(client)
        CLIENTS.difference_update(stale)


# --- HTTP handlers ---

async def websocket_handler(request):
    websocket = web.WebSocketResponse(heartbeat=30)
    await websocket.prepare(request)
    CLIENTS.add(websocket)
    try:
        if HISTORY:
            for event in list(HISTORY):
                try:
                    await websocket.send_str(json.dumps([event]))
                except Exception:
                    break
                await asyncio.sleep(TRICKLE_DELAY_SECONDS)
        async for message in websocket:
            if message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                break
    finally:
        CLIENTS.discard(websocket)
    return websocket


def decode_objects(raw_body):
    payload = raw_body.strip()
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        items = []
        index = 0
        length = len(payload)
        while index < length:
            while index < length and payload[index].isspace():
                index += 1
            if index >= length:
                break
            item, index = decoder.raw_decode(payload, index)
            if isinstance(item, list):
                items.extend(item)
            else:
                items.append(item)
        return items


async def crowdsec_handler(request):
    raw_body = await request.text()
    try:
        payloads = decode_objects(raw_body)
    except json.JSONDecodeError as exc:
        return web.json_response({"status": "error", "error": str(exc)}, status=400)

    events = []
    for payload in payloads:
        ev = process_crowdsec(payload)
        if ev:
            events.append(ev)
    await broadcast(events)
    return web.json_response({"status": "ok", "received": len(payloads), "emitted": len(events)})


async def health_handler(_request):
    return web.json_response({
        "status": "ok",
        "clients": len(CLIENTS),
        "history": len(HISTORY),
        "geo_cache": len(GEO_CACHE),
    })


async def main():
    app = web.Application()
    app.router.add_post("/crowdsec", crowdsec_handler)
    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/raven-ws", websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"bridge listening on {HOST}:{PORT}", flush=True)

    try:
        await asyncio.gather(backfill_loop(), zmq_loop(), replay_loop(), asyncio.Future())
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
