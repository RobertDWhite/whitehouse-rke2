"""Speaker labels for the home-videos site: who each voice on each tape is.

    GET  /api/labels   -> {"labels": {"<slug>/<voice>": {"name": ..., "ts": ...}}, "names": [...]}
    POST /api/labels   {"set": {"<slug>/<voice>": "<name>" | null}}  (null clears a label)

Labels live in DATA/labels.json (written atomically); every change is appended to
DATA/history.jsonl, so nothing is ever lost. Standard library only. The site's
label.html and player read this live, and the brain import reads it every run.
"""
import json, os, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("DATA", "/data")
LABELS = os.path.join(DATA, "labels.json")
HISTORY = os.path.join(DATA, "history.jsonl")
KEY = re.compile(r"^[a-z0-9-]{1,120}/v\d{1,3}$")
NAMES_FILE = os.environ.get("NAMES_FILE", "/config/names.json")  # family names, from the home-videos-people Secret
EXTRA = ["TV", "Not speech"]
lock = threading.Lock()


def load():
    try:
        with open(LABELS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(labels):
    tmp = LABELS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(labels, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, LABELS)


def names(labels):
    try:
        with open(NAMES_FILE) as f:
            base = json.load(f) + EXTRA
    except (OSError, ValueError):
        base = list(EXTRA)
    return base + sorted({v["name"] for v in labels.values()} - set(base))


class Handler(BaseHTTPRequestHandler):
    server_version = "home-videos-labels"

    def send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] in ("/healthz", "/api/healthz"):
            return self.send(200, {"ok": True})
        if self.path.split("?")[0] != "/api/labels":
            return self.send(404, {"error": "not found"})
        with lock:
            labels = load()
        self.send(200, {"labels": labels, "names": names(labels)})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/labels":
            return self.send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1 << 20:
                return self.send(413, {"error": "too large"})
            req = json.loads(self.rfile.read(n) or b"{}")
            changes = req.get("set") or {}
            if not isinstance(changes, dict) or not changes:
                return self.send(400, {"error": "expected {\"set\": {\"<slug>/<voice>\": name|null}}"})
            for k, v in changes.items():
                if not KEY.match(k) or not (v is None or (isinstance(v, str) and 0 < len(v.strip()) <= 80)):
                    return self.send(400, {"error": f"bad label {k!r}"})
        except (ValueError, TypeError):
            return self.send(400, {"error": "bad json"})
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with lock:
            labels = load()
            with open(HISTORY, "a") as h:
                for k, v in changes.items():
                    h.write(json.dumps({"ts": ts, "key": k, "name": v, "was": (labels.get(k) or {}).get("name"),
                                        "from": self.headers.get("X-Forwarded-For", self.client_address[0])}) + "\n")
                    if v is None:
                        labels.pop(k, None)
                    else:
                        labels[k] = {"name": v.strip(), "ts": ts}
            save(labels)
        self.send(200, {"labels": labels, "names": names(labels)})

    def log_message(self, fmt, *args):
        if not self.path.endswith("healthz"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
