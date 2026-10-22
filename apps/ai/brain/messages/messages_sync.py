#!/usr/bin/env python3
"""brain-messages: sync allowlisted iMessage conversations into the Forgejo brain repo.

Reads the allowlist from the brain repo (.brain/messages.json) every run, pulls
only those conversations from a BlueBubbles server, and renders one Markdown
note per conversation per month under Messages/. Optionally makes sure every
member of a synced conversation who is in the Mac's Contacts also exists in
Meerkat (create only, never modify).

Incremental: Messages/.index.json keeps a per-thread cursor (start of the month
of the newest synced message). A thread is only re-fetched when the chat list
shows a newer message, and then only from that month on. First runs backfill
history in bounded chunks (MAX_MESSAGES_PER_RUN), cut at month boundaries.

Changes are pushed like brain-sync does: git blob hashes are compared with the
repo and only differences go out, through Forgejo's change-files API, as the
brain-sync user. Only Messages/ is ever written.

If BlueBubbles is unreachable the run logs a warning and exits 0; the next run
retries. Standard library only, so it runs on the stock python image.
"""

import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "America/New_York")
except Exception:  # no tzdata in the image: fall back to UTC
    LOCAL_TZ = timezone.utc

FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://forgejo.forgejo.svc.cluster.local").rstrip("/")
FORGEJO_TOKEN = os.environ["FORGEJO_TOKEN"]
REPO = os.environ.get("BRAIN_REPO", "WhiteHouse/brain")
BRANCH = os.environ.get("BRAIN_BRANCH", "main")
BB_URL = os.environ.get("BLUEBUBBLES_URL", "").rstrip("/")
BB_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")
MEERKAT_URL = os.environ.get("MEERKAT_URL", "http://meerkat.meerkat.svc.cluster.local").rstrip("/")
MEERKAT_TOKEN = os.environ.get("MEERKAT_TOKEN", "")
MAX_COMMIT_BYTES = int(os.environ.get("MAX_COMMIT_BYTES", str(4 * 1024 * 1024)))
# Messages fetched per run across all threads (backfill is spread over runs).
MAX_MESSAGES_PER_RUN = int(os.environ.get("MAX_MESSAGES_PER_RUN", "20000"))
PAGE_SIZE = int(os.environ.get("BB_PAGE_SIZE", "1000"))
MEERKAT_MAX_CREATES = int(os.environ.get("MEERKAT_MAX_CREATES", "50"))
# After creating Meerkat contacts, hold off rendering until their entity pages
# exist (so names get linked on the first render), but at most this long.
ENTITY_WAIT_SECONDS = int(os.environ.get("ENTITY_WAIT_SECONDS", "1800"))
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
# Dry run only: write the rendered tree (and the index) here instead of the repo.
OUT_DIR = os.environ.get("OUT_DIR", "")
AUTHOR = {"name": "brain-sync", "email": "brain-sync@whitematter.tech"}

MESSAGES_DIR = os.environ.get("MESSAGES_DIR", "Messages").strip("/")
INDEX_PATH = f"{MESSAGES_DIR}/.index.json"
CONFIG_FILE = ".brain/messages.json"
ENTITIES_DIR = os.environ.get("ENTITIES_DIR", "Entities").strip("/")

log = logging.getLogger("brain-messages")
STATS = {"redacted": {}}


class SourceDown(Exception):
    """BlueBubbles unreachable: skip this run, retry next time."""


# ---------------------------------------------------------------- HTTP helpers


def http_json(method, url, token, body=None, scheme="Bearer", retries=4, label=None):
    data = json.dumps(body).encode() if body is not None else None
    label = label or url
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("Authorization", f"{scheme} {token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 2)
                log.warning("%s %s -> %s, retrying in %ss", method, label, e.code, wait)
                time.sleep(wait)
                continue
            detail = e.read()[:300].decode(errors="replace")
            raise RuntimeError(f"{method} {label} -> {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"{method} {label} failed: {e}") from None


def forgejo(method, path, body=None):
    return http_json(method, f"{FORGEJO_URL}/api/v1{path}", FORGEJO_TOKEN, body, scheme="token")


def bb(method, path, body=None):
    """BlueBubbles call; the password travels as a query parameter and is never logged."""
    sep = "&" if "?" in path else "?"
    url = f"{BB_URL}{path}{sep}password={urllib.parse.quote(BB_PASSWORD)}"
    try:
        data = http_json(method, url, None, body, retries=3, label=f"bluebubbles {path}")
    except RuntimeError as e:
        raise SourceDown(str(e)) from None
    if not isinstance(data, dict) or data.get("status") != 200:
        raise SourceDown(f"bluebubbles {path}: unexpected response")
    return data


def meerkat(method, path, body=None):
    return http_json(method, f"{MEERKAT_URL}/api/v1{path}", MEERKAT_TOKEN, body, retries=3,
                     label=f"meerkat {path.split('?')[0]}")


# -------------------------------------------------------------- formatting


def safe_name(text, limit=110):
    text = re.sub(r"\s*:\s+", " - ", text or "")
    text = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', "-", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-")
    return (text[:limit].rstrip(" .-")) or "Untitled"


def yaml_value(v):
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return json.dumps(v)
    return json.dumps(str(v), ensure_ascii=False)


def frontmatter(fields):
    lines = ["---"]
    for k, v in fields.items():
        if v in (None, "", [], {}):
            continue
        lines.append(f"{k}: {yaml_value(v)}")
    lines.append("---")
    return "\n".join(lines)


GENERATED = ("<!-- Generated by brain-messages from iMessage (BlueBubbles). Edits here are overwritten; "
             "change the allowlist in .brain/messages.json instead. -->")


def local_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(LOCAL_TZ)


def month_key(ms):
    return local_dt(ms).strftime("%Y-%m")


def month_start_ms(ms):
    d = local_dt(ms)
    return int(d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def month_key_start(key):
    y, m = map(int, key.split("-"))
    return int(datetime(y, m, 1, tzinfo=LOCAL_TZ).timestamp() * 1000)


def since_ms(value):
    if not value:
        return 0
    try:
        d = datetime.fromisoformat(str(value)[:10])
    except ValueError:
        log.warning("config: ignoring unparseable 'since'")
        return 0
    return int(d.replace(tzinfo=LOCAL_TZ).timestamp() * 1000)


def short_hash(text, n=6):
    return hashlib.sha1(text.encode()).hexdigest()[:n]


# ------------------------------------------------------------------ redaction

SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    ("token", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{20,}"), "[REDACTED]"),
    ("token", re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9_\-]{20,})"), "[REDACTED]"),
    ("token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_\-]{20,})"),
     "[REDACTED]"),
    ("token", re.compile(r"\b(xox[abposr]-[A-Za-z0-9\-]{10,}|hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"), "[REDACTED]"),
    ("token", re.compile(r"\b(op_[A-Za-z0-9]{24,}|meerkat_[A-Za-z0-9_\-]{16,}|whsec_[A-Za-z0-9]{16,})"), "[REDACTED]"),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "[REDACTED JWT]"),
    ("token", re.compile(r"(?i)\b(authorization:\s*(?:bearer|token|basic)\s+)[A-Za-z0-9._~+/=\-]{12,}"),
     r"\1[REDACTED]"),
    ("token", re.compile(r"(?i)\b((?:api[_-]?key|token|secret)[\"']?\s*[:=]\s*[\"']?)(?![$<{\[])[^\s\"'`,;]{8,}"),
     r"\1[REDACTED]"),
    ("token", re.compile(r"(https?://[^/\s:@]+:)[^@\s/]{6,}@"), r"\1[REDACTED]@"),
    ("license_key", re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b"), "[REDACTED KEY]"),
    # Passwords given in a message: "password is X", "pw: X", "wifi password - X".
    ("password", re.compile(r"(?i)\b(password|passwd|passcode|pwd|pw|wifi pass)(\s*(?:is|was|:|=|-|→)\s*)"
                            r"[\"'“]?(?!\[REDACTED)[^\s\"'”]{3,}"), r"\1\2[REDACTED]"),
    # One-time / verification codes: "code is 123456", "123456 is your code", "G-123456".
    ("otp", re.compile(r"(?i)(?<!zip )(?<!area )(?<!postal )(?<!promo )\b(code|codes|verification|verify|otp|one[- ]time|2fa|mfa|pin)\b([^\n\d]{0,40}?)"
                       r"(?<![$\d])\b(?:[A-Z]{1,3}-)?\d{4,8}\b(?![-/.,]\d)"), r"\1\2[REDACTED CODE]"),
    ("otp", re.compile(r"(?i)\b(?:[A-Z]{1,3}-)?\d{4,8}\b(?=[^\n\d]{0,40}\b(?:code|verification|otp|passcode)\b)"),
     "[REDACTED CODE]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED SSN]"),
]
CARD_RE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _card(m):
    # Long numbers inside URLs, file names and IDs are not card numbers.
    text, start, end = m.string, m.start(), m.end()
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    if re.search(r"[/_=@]|[A-Za-z]\d|\d[A-Za-z]|\.\w", text[start:end].rstrip(".,;:!?)")):
        return m.group(0)
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and luhn_ok(digits):
        STATS["redacted"]["card"] = STATS["redacted"].get("card", 0) + 1
        return "[REDACTED CARD]"
    return m.group(0)


def redact(text):
    for kind, pat, repl in SECRET_PATTERNS:
        text, n = pat.subn(repl, text)
        if n:
            STATS["redacted"][kind] = STATS["redacted"].get(kind, 0) + n
    return CARD_RE.sub(_card, text)


# ------------------------------------------------------------- handles/contacts


def norm_handle(address):
    a = (address or "").strip().lower()
    if not a:
        return ""
    if "@" in a:
        return a
    digits = re.sub(r"\D", "", a)
    return digits[-10:] if len(digits) >= 10 else digits


def norm_person(name):
    return re.sub(r"[^\w ]+", "", re.sub(r"\s+", " ", (name or "").strip())).casefold()


def contact_display(c):
    if c.get("displayName"):
        return c["displayName"].strip()
    name = " ".join(x.strip() for x in (c.get("firstName"), c.get("lastName")) if x and x.strip())
    return name or (c.get("nickname") or "").strip()


class People:
    """Mac Contacts, merged per display name, keyed by normalized handle."""

    def __init__(self, contacts, cfg_people, self_handles):
        self.by_handle = {}  # norm handle -> person key
        self.persons = {}    # key -> {name, contact_name, first, last, nickname, phones, emails, listed}
        listed = {norm_person(p.get("contact")): p for p in cfg_people if p.get("contact") and p.get("name")}
        for c in contacts:
            display = contact_display(c)
            if not display:
                continue
            phones = [p.get("address") for p in c.get("phoneNumbers") or [] if p.get("address")]
            emails = [e.get("address") for e in c.get("emails") or [] if e.get("address")]
            handles = {norm_handle(x) for x in phones + emails} - {""}
            if handles & self_handles:
                continue  # Robert's own card
            key = norm_person(display)
            p = self.persons.get(key)
            if p is None:
                entry = listed.get(key)
                p = self.persons[key] = {
                    "key": key, "contact_name": display,
                    "name": entry["name"] if entry else display,
                    "first": (c.get("firstName") or "").strip(), "last": (c.get("lastName") or "").strip(),
                    "nickname": (c.get("nickname") or "").strip(),
                    "phones": [], "emails": [], "handles": set(), "listed": bool(entry),
                }
            for x in phones:
                if norm_handle(x) not in {norm_handle(y) for y in p["phones"]}:
                    p["phones"].append(x)
            for x in emails:
                if x.lower() not in {y.lower() for y in p["emails"]}:
                    p["emails"].append(x.lower())
            p["handles"] |= handles
            for h in handles:
                self.by_handle.setdefault(h, key)
        # Allowlist entries may list handles (phone numbers or iMessage emails)
        # directly, for people who aren't in Contacts or who use an extra handle.
        for entry in cfg_people:
            extra = [x for x in entry.get("handles") or [] if x]
            if not extra or not entry.get("name"):
                continue
            key = norm_person(entry.get("contact") or entry["name"])
            p = self.persons.get(key)
            if p is None:
                first, _, last = entry["name"].partition(" ")
                p = self.persons[key] = {
                    "key": key, "contact_name": entry.get("contact") or entry["name"], "name": entry["name"],
                    "first": first, "last": last, "nickname": "", "phones": [], "emails": [],
                    "handles": set(), "listed": True,
                }
            for x in extra:
                h = norm_handle(x)
                if not h:
                    continue
                if "@" in x and x.lower() not in p["emails"]:
                    p["emails"].append(x.lower())
                elif "@" not in x and h not in {norm_handle(y) for y in p["phones"]}:
                    p["phones"].append(x)
                p["handles"].add(h)
                self.by_handle[h] = key

    def person(self, address):
        key = self.by_handle.get(norm_handle(address))
        return self.persons.get(key) if key else None

    def name(self, address):
        p = self.person(address)
        return p["name"] if p else (address or "Unknown")

    def first_name(self, address):
        """Short label for unnamed group folders; unknown handles sort last."""
        p = self.person(address)
        if not p:
            a = address or "?"
            return "~" + (a.split("@")[0] if "@" in a else a[-4:])
        return p["name"].split()[0] if p["listed"] else (p["first"] or p["nickname"] or p["name"].split()[0])


# ---------------------------------------------------------------- repo access


def read_bytes(path):
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1/repos/{REPO}/raw/{urllib.parse.quote(path)}?ref={BRANCH}")
    req.add_header("Authorization", f"token {FORGEJO_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def list_tree(prefix):
    """{path: blob_sha} for everything under prefix on BRANCH ({} if missing)."""
    tree = forgejo("GET", f"/repos/{REPO}/git/trees/{BRANCH}")
    if not tree:
        raise RuntimeError(f"branch {BRANCH} of {REPO} not found")
    for part in prefix.split("/"):
        entry = next((e for e in tree.get("tree") or [] if e["path"] == part and e["type"] == "tree"), None)
        if not entry:
            return {}
        tree = forgejo("GET", f"/repos/{REPO}/git/trees/{entry['sha']}") or {}
    return {f"{prefix}/{p}": sha for p, sha in tree_blobs(tree["sha"]).items()}


def tree_blobs(tree_sha):
    out, page = {}, 1
    while True:
        data = forgejo("GET", f"/repos/{REPO}/git/trees/{tree_sha}?recursive=true&per_page=1000&page={page}")
        for e in data.get("tree") or []:
            if e["type"] == "blob":
                out[e["path"]] = e["sha"]
        if not data.get("truncated") or not data.get("tree"):
            return out
        page += 1


def local_tree(prefix):
    out, root = {}, os.path.join(OUT_DIR, prefix)
    for dirpath, _, names in os.walk(root):
        for n in names:
            full = os.path.join(dirpath, n)
            with open(full, "rb") as f:
                out[os.path.relpath(full, OUT_DIR)] = blob_sha(f.read())
    return out


def apply_changes(desired, existing, delete, label="messages"):
    """Create/update `desired` (by blob hash) and delete `delete`; batched under MAX_COMMIT_BYTES."""
    ops = []
    for path, text in sorted(desired.items()):
        content = text.encode()
        if existing.get(path) == blob_sha(content):
            continue
        op = {"operation": "update" if path in existing else "create", "path": path,
              "content": base64.b64encode(content).decode()}
        if path in existing:
            op["sha"] = existing[path]
        ops.append(op)
    for path in sorted(delete):
        if path in existing and path not in desired:
            ops.append({"operation": "delete", "path": path, "sha": existing[path]})
    if not ops:
        return 0
    counts = {k: sum(1 for o in ops if o["operation"] == k) for k in ("create", "update", "delete")}
    summary = ", ".join(f"{v} {k}d" for k, v in counts.items() if v)
    if DRY_RUN:
        if OUT_DIR:
            for o in ops:
                full = os.path.join(OUT_DIR, o["path"])
                if o["operation"] == "delete":
                    os.remove(full)
                else:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as f:
                        f.write(base64.b64decode(o["content"]))
        log.info("[dry-run] %s: %s", label, summary)
        return len(ops)
    batch, batches, size = [], [], 0
    for o in ops:
        n = len(o.get("content", ""))
        if batch and size + n > MAX_COMMIT_BYTES:
            batches.append(batch)
            batch, size = [], 0
        batch.append(o)
        size += n
    batches.append(batch)
    for i, b in enumerate(batches, 1):
        part = f" (part {i}/{len(batches)})" if len(batches) > 1 else ""
        forgejo("POST", f"/repos/{REPO}/contents", {
            "branch": BRANCH, "message": f"{label}: {summary}{part}",
            "author": AUTHOR, "committer": AUTHOR, "files": b,
        })
    log.info("%s: committed %s in %d commit(s)", label, summary, len(batches))
    return len(ops)


def load_index():
    raw = None
    if DRY_RUN and OUT_DIR:
        p = os.path.join(OUT_DIR, INDEX_PATH)
        if os.path.exists(p):
            with open(p, "rb") as f:
                raw = f.read()
    else:
        raw = read_bytes(INDEX_PATH)
    try:
        idx = json.loads(raw) if raw else {}
    except ValueError:
        log.warning("index unreadable, starting over")
        idx = {}
    idx.setdefault("threads", {})
    idx.setdefault("meerkat", {"created": {}, "last_create": 0})
    return idx


# ------------------------------------------------------------------ BlueBubbles


def fetch_chats():
    chats, offset = [], 0
    while True:
        data = bb("POST", "/chat/query", {"limit": PAGE_SIZE, "offset": offset,
                                          "with": ["participants", "lastMessage"]})
        batch = data.get("data") or []
        chats.extend(batch)
        total = int((data.get("metadata") or {}).get("total") or 0)
        offset += len(batch)
        if not batch or len(batch) < PAGE_SIZE or (total and offset >= total):
            return chats


def fetch_chat_messages(guid, after, budget):
    """Messages of one chat from `after` (inclusive), oldest first.

    Stops once `budget` is used up, but only after crossing into a second month,
    so every run makes progress. Returns (messages, cutoff_ms or None if complete)."""
    out, offset = [], 0
    while True:
        body = {"chatGuid": guid, "limit": PAGE_SIZE, "offset": offset, "sort": "ASC",
                "with": ["handle", "attachment"]}
        if after:
            body["after"] = after
        batch = bb("POST", "/message/query", body).get("data") or []
        out.extend(m for m in batch if m.get("dateCreated"))
        offset += len(batch)
        if len(batch) < PAGE_SIZE:
            return out, None
        if out and len(out) >= budget and month_key(out[-1]["dateCreated"]) > month_key(out[0]["dateCreated"]):
            return out, month_start_ms(out[-1]["dateCreated"])


# ------------------------------------------------------------------ threads


def discover_threads(cfg, chats, people, self_handles, index):
    """[{key, kind, name, folder, chats: [chat], participants: [address]}] for the allowlist only."""
    threads = []
    one_to_one = [c for c in chats if len(c.get("participants") or []) == 1]
    groups = [c for c in chats if len(c.get("participants") or []) > 1]
    listed_handles = set()
    for i, p in enumerate(cfg.get("people") or []):
        # Entries with only `handles` (not in Contacts) are keyed by their name.
        person = people.persons.get(norm_person(p.get("contact") or p.get("name")))
        if not person:
            log.warning("config people[%d]: contact not found in Contacts", i)
            continue
        listed_handles |= person["handles"]
        mine = [c for c in one_to_one if norm_handle(c["participants"][0].get("address")) in person["handles"]]
        if not mine:
            log.info("config people[%d]: no 1:1 conversations", i)
            continue
        threads.append({"key": f"person:{p['name']}", "kind": "person", "name": p["name"],
                        "folder": f"{MESSAGES_DIR}/People/{safe_name(p['name'])}", "chats": mine,
                        "participants": sorted({c["participants"][0]["address"] for c in mine})})
    picked = {}
    for g in cfg.get("groups") or []:
        found = [c for c in groups if c.get("displayName") == g.get("chat")]
        if not found:
            log.warning("config groups: a listed group chat was not found")
        for c in found:
            picked.setdefault(c["guid"], g.get("name") or g.get("chat"))
    if cfg.get("include_groups_with_people"):
        for c in groups:
            if c["guid"] not in picked and any(
                    norm_handle(x.get("address")) in listed_handles for x in c["participants"]):
                picked[c["guid"]] = None
    by_guid = {c["guid"]: c for c in groups}
    # Folder names: configured name, else the chat's name, else first names + a stable suffix.
    owners = {t["folder"]: k for k, t in index["threads"].items()}
    used = {t["folder"].casefold() for t in threads}
    group_threads = []
    for guid, name in sorted(picked.items()):
        c = by_guid[guid]
        addrs = sorted({x["address"] for x in c["participants"] if norm_handle(x.get("address")) not in self_handles})
        if not name:
            name = (c.get("displayName") or "").strip()
        if not name:
            firsts = [f.lstrip("~") for f in sorted({people.first_name(a) for a in addrs},
                                                      key=lambda f: (f.startswith("~"), f.casefold()))]
            more = f" +{len(firsts) - 4}" if len(firsts) > 4 else ""
            name = f"Group - {', '.join(firsts[:4])}{more} ({short_hash(guid)})"
        group_threads.append({"key": f"group:{guid}", "kind": "group", "name": name, "chats": [c],
                              "participants": addrs, "base": f"{MESSAGES_DIR}/Groups/{safe_name(name)}"})
    # A folder already owned by a thread stays with it; any other claimant gets a suffix.
    group_threads.sort(key=lambda t: (owners.get(t["base"]) != t["key"], t["key"]))
    for t in group_threads:
        folder = t["base"]
        if folder.casefold() in used:
            folder = f"{folder} ({short_hash(t['key'])})"
        used.add(folder.casefold())
        t["folder"] = folder
        threads.append(t)
    return threads


SYSTEM_EVENTS = {
    (1, 0): "added someone to the conversation",
    (1, 1): "removed someone from the conversation",
    (3, 0): "left the conversation",
    (3, 1): "changed the group photo",
}


def render_line(m, kind, thread_name, people, entity_names):
    if m.get("associatedMessageType") or m.get("dateRetracted"):
        return None  # tapbacks/reactions and unsent messages
    if m.get("isFromMe"):
        who = "Me"
    elif kind == "person":
        who = thread_name
    else:
        who = people.name((m.get("handle") or {}).get("address"))
    label = f"[[{ENTITIES_DIR}/People/{safe_name(who)}|{who}]]" if who != "Me" and safe_name(who) in entity_names else who
    who_md = f"**{label}:**"
    t = local_dt(m["dateCreated"]).strftime("%H:%M")
    item = int(m.get("itemType") or 0)
    if item:
        if kind != "group":
            return None
        if item == 2:
            title = redact((m.get("groupTitle") or "").strip())
            event = f'named the conversation "{title}"' if title else "removed the conversation name"
        else:
            event = SYSTEM_EVENTS.get((item, int(m.get("groupActionType") or 0)))
        if not event:
            return None
        return f"- {t} **{label}** _{event}_"
    text = (m.get("text") or "").replace("￼", "").replace("�", "").replace("\r\n", "\n").strip()
    subject = (m.get("subject") or "").strip()
    if subject:
        text = f"**{subject}** — {text}" if text else f"**{subject}**"
    parts = [redact(text)] if text else []
    for a in m.get("attachments") or []:
        name = (a.get("transferName") or "").strip()
        if not name or name.endswith(".pluginPayloadAttachment"):
            continue
        parts.append(f"[attachment: {redact(name)}]")
    if not parts:
        return None
    body = " ".join(parts)
    lines = [ln for ln in body.split("\n")]
    out = [f"- {t} {who_md} {lines[0]}".rstrip()]
    for ln in lines[1:]:
        ln = ln.rstrip()
        if not ln.strip():
            continue
        if ln.lstrip().startswith(("#", "---", "===")):
            ln = "\\" + ln.lstrip()
        out.append(f"  {ln}")
    return "\n".join(out)


def render_month(thread, month, msgs, people, entity_names):
    """(markdown, stats) for one thread-month, or (None, stats) if nothing renders."""
    days, count = {}, 0
    last_day = None
    for m in msgs:
        line = render_line(m, thread["kind"], thread["name"], people, entity_names)
        if line is None:
            continue
        d = local_dt(m["dateCreated"])
        day = d.strftime("%Y-%m-%d")
        days.setdefault(day, (d.strftime("%A"), []))[1].append(line)
        count += 1
        last_day = day
    if not count:
        return None, 0
    name = thread["name"]
    is_group = thread["kind"] == "group"
    participants = [people.name(a) for a in thread["participants"]]
    fm = {
        "source": "imessage",
        "name": name,
        "type": thread["kind"],
        "month": month,
        "date": last_day,
        "message_count": count,
        "participants": sorted(set(participants), key=str.casefold) if is_group else [],
        "tags": ["message", "imessage"],
    }
    label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    out = [frontmatter(fm), GENERATED, "", f"# {name}: {label}", ""]

    def link(n):
        return f"[[{ENTITIES_DIR}/People/{safe_name(n)}|{n}]]" if safe_name(n) in entity_names else n

    if is_group:
        out.append("**Participants:** Me, " + ", ".join(link(n) for n in sorted(set(participants), key=str.casefold)))
    else:
        out.append(f"**With:** {link(name)}")
    for day in sorted(days):
        weekday, lines = days[day]
        out += ["", f"### {day} ({weekday})", "", *lines]
    return "\n".join(out) + "\n", count


def sync_thread(t, st, budget, since, people, entity_names, existing):
    """Fetch and render one thread. Returns (files, deletions, new_state, fetched)."""
    cursor = st.get("cursor") or since
    msgs, cutoffs, fetched = [], [], 0
    for c in t["chats"]:
        got, cut = fetch_chat_messages(c["guid"], cursor, max(budget - fetched, 1))
        fetched += len(got)
        msgs.extend(got)
        if cut is not None:
            cutoffs.append(cut)
    cutoff = min(cutoffs) if cutoffs else None
    seen, kept = set(), []
    for m in sorted(msgs, key=lambda m: (m["dateCreated"], m.get("originalROWID") or 0)):
        if (since and m["dateCreated"] < since) or (cutoff and m["dateCreated"] >= cutoff):
            continue
        if m.get("guid") in seen:
            continue
        seen.add(m.get("guid"))
        kept.append(m)
    by_month = {}
    for m in kept:
        by_month.setdefault(month_key(m["dateCreated"]), []).append(m)
    lo = month_key(cursor) if cursor else "0000-00"
    hi = month_key(cutoff) if cutoff else "9999-99"
    files, months = {}, {k: v for k, v in (st.get("months") or {}).items() if not (lo <= k < hi)}
    for month, mm in sorted(by_month.items()):
        text, stats = render_month(t, month, mm, people, entity_names)
        if text:
            files[f"{t['folder']}/{month[:4]}/{month}.md"] = text
            months[month] = stats
    deletions = set()
    for path in existing:
        if not path.startswith(t["folder"] + "/") or path in files:
            continue
        mk = re.search(r"/(\d{4}-\d{2})\.md$", path)
        if mk and lo <= mk.group(1) < hi:
            deletions.add(path)
    new = dict(st)
    new["months"] = months
    if cutoff:
        new["cursor"], new["pending"] = cutoff, True
    else:
        newest = max((m["dateCreated"] for m in msgs), default=0)
        new["last"] = max(newest, st.get("last") or 0)
        new["cursor"] = month_start_ms(kept[-1]["dateCreated"]) if kept else cursor
        new["pending"] = False
    return files, deletions, new, fetched


# ------------------------------------------------------------------ Meerkat


def sync_meerkat(cfg, threads, people, index):
    """Create a Meerkat contact for every resolvable member without one. Never modifies existing ones."""
    # null or "" in the config means: create contacts without a circle
    circle = cfg.get("meerkat_circle", "iMessage")
    members = {}
    for t in threads:
        for a in t["participants"]:
            p = people.person(a)
            if p:
                members[p["key"]] = p
    contacts, page = [], 1
    while True:
        data = meerkat("GET", f"/contacts?limit=100&page={page}&include_archived=true") or {}
        batch = data.get("contacts") or []
        contacts.extend(batch)
        if not batch or len(contacts) >= int(data.get("total") or 0):
            break
        page += 1
    known_handles, known_names = set(), set()
    for c in contacts:
        for v in [c.get("email"), c.get("phone")] + [x.get("value") for x in (c.get("emails") or []) + (c.get("phones") or [])]:
            if v:
                known_handles.add(norm_handle(v))
        first, last, nick = (c.get(k) or "" for k in ("firstname", "lastname", "nickname"))
        known_names.add(norm_person(f"{first} {last}"))
        if nick:
            known_names.add(norm_person(f"{nick} {last}"))
    known_handles.discard("")
    created_map = index["meerkat"].setdefault("created", {})
    todo = []
    for key, p in sorted(members.items()):
        names = {norm_person(p["name"]), norm_person(p["contact_name"])}
        if key in created_map or p["handles"] & known_handles or names & known_names:
            continue
        todo.append(p)
    created = failed = 0
    for p in todo[:MEERKAT_MAX_CREATES]:
        if p["listed"]:
            first, _, last = p["name"].partition(" ")
        else:
            first, last = p["first"] or p["nickname"] or p["name"], p["last"]
        body = {
            "firstname": first[:100], "lastname": last[:100], "circles": [circle] if circle else [],
            "emails": [{"type": "home", "value": e} for e in p["emails"]
                       if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)][:25],
            "phones": [{"type": "cell", "value": x} for x in p["phones"]
                       if 5 <= len(re.sub(r"[^\d+]", "", x)) <= 20][:25],
        }
        if p["nickname"] and not p["listed"]:
            body["nickname"] = p["nickname"][:50]
        if DRY_RUN:
            created += 1
            continue
        try:
            res = meerkat("POST", "/contacts", body) or {}
        except RuntimeError as e:
            failed += 1
            log.warning("meerkat: create failed (%s)", str(e).split(":", 1)[0])
            continue
        cid = res.get("ID") or (res.get("contact") or {}).get("ID")
        created_map[p["key"]] = {"id": cid, "name": p["name"], "at": int(time.time())}
        created += 1
    if created and not DRY_RUN:
        index["meerkat"]["last_create"] = int(time.time())
    log.info("meerkat: %d members, %d already present, %d %s, %d failed, %d left for later runs",
             len(members), len(members) - len(todo), created, "would be created" if DRY_RUN else "created",
             failed, max(len(todo) - MEERKAT_MAX_CREATES, 0))
    return created


# ------------------------------------------------------------------ main


README = """# Messages

Selected iMessage conversations, one note per conversation per month:
`People/<name>/<year>/<year-month>.md` for 1:1 threads and
`Groups/<group>/<year>/<year-month>.md` for group chats.

Written by `brain-messages` (whitehouse-rke2 `apps/ai/brain/messages`) from
BlueBubbles. Which conversations are synced is set in `.brain/messages.json`;
removing an entry there removes its folder here. Reactions are left out and
one-time codes, passwords, keys and card numbers are redacted. Don't edit
these notes; they are regenerated.
"""


def run():
    cfg_raw = read_bytes(CONFIG_FILE)
    if not cfg_raw:
        log.info("no %s in the brain repo: nothing to do", CONFIG_FILE)
        return
    cfg = json.loads(cfg_raw)
    since = since_ms(cfg.get("since"))
    index = load_index()

    info = bb("GET", "/server/info").get("data") or {}
    contacts = bb("GET", "/contact").get("data") or []
    chats = fetch_chats()
    if not chats:
        raise SourceDown("chat list is empty")
    self_handles = {norm_handle(info.get(k)) for k in ("detected_icloud", "detected_imessage")}
    self_handles |= {norm_handle(c.get("lastAddressedHandle")) for c in chats}
    self_handles.discard("")
    people = People(contacts, cfg.get("people") or [], self_handles)
    threads = discover_threads(cfg, chats, people, self_handles, index)
    log.info("bluebubbles: %d contacts, %d chats; %d threads selected (%d person, %d group)",
             len(contacts), len(chats), len(threads), sum(t["kind"] == "person" for t in threads),
             sum(t["kind"] == "group" for t in threads))

    entity_names = {p.rsplit("/", 1)[-1][:-3] for p in list_tree(f"{ENTITIES_DIR}/People") if p.endswith(".md")}

    if cfg.get("add_to_meerkat") and MEERKAT_TOKEN:
        try:
            created = sync_meerkat(cfg, threads, people, index)
        except RuntimeError as e:
            log.warning("meerkat unavailable, contacts not checked this run: %s", str(e).split(":", 1)[0])
            created = 0
        waiting = [v for v in index["meerkat"]["created"].values()
                   if safe_name(v.get("name") or "") not in entity_names]
        recent = time.time() - (index["meerkat"].get("last_create") or 0) < ENTITY_WAIT_SECONDS
        if not DRY_RUN and (created or (waiting and recent)):
            log.info("waiting for entity pages of new Meerkat contacts before rendering messages")
            apply_changes({INDEX_PATH: json.dumps(index, indent=1, sort_keys=True) + "\n"},
                          local_tree(MESSAGES_DIR) if (DRY_RUN and OUT_DIR) else list_tree(MESSAGES_DIR), (),
                          label="messages index")
            return

    existing = local_tree(MESSAGES_DIR) if (DRY_RUN and OUT_DIR) else list_tree(MESSAGES_DIR)
    states, budget = index["threads"], MAX_MESSAGES_PER_RUN
    files, deletions, totals = {}, set(), {"fetched": 0, "synced": 0, "pending": 0}
    order = []
    for t in threads:
        st = states.get(t["key"]) or {}
        linked = sorted({people.name(a) for a in t["participants"]} & entity_names
                        | ({t["name"]} & entity_names))
        sig = short_hash(json.dumps([since, t["folder"], sorted(c["guid"] for c in t["chats"]), linked]), 12)
        if st.get("sig") != sig:
            # New thread, new chat, new name/folder or newly linkable names: render from scratch.
            # Old month files stay until their month is rendered again.
            st = {"sig": sig, "cursor": 0, "pending": True, "months": st.get("months") or {},
                  "last": 0} if st.get("folder") == t["folder"] else {"sig": sig, "cursor": 0, "pending": True}
        st["folder"], st["name"], st["kind"] = t["folder"], t["name"], t["kind"]
        newest = max(((c.get("lastMessage") or {}).get("dateCreated") or 0 for c in t["chats"]), default=0)
        due = st.get("pending") or newest > (st.get("last") or 0)
        order.append((bool(st.get("pending")), t["key"], t, st, due))
    new_states = {}
    for pending, key, t, st, due in sorted(order, key=lambda x: (x[0], x[1])):
        if due and budget > 0:
            f, d, st, fetched = sync_thread(t, st, budget, since, people, entity_names, existing)
            files.update(f)
            deletions |= d
            budget -= fetched
            totals["fetched"] += fetched
            totals["synced"] += 1
        totals["pending"] += bool(st.get("pending"))
        new_states[key] = st
    # Folders of threads no longer selected (removed from the allowlist) go away.
    live = {s["folder"] + "/" for s in new_states.values()}
    for path in existing:
        if path in (INDEX_PATH, f"{MESSAGES_DIR}/README.md"):
            continue
        if not any(path.startswith(f) for f in live):
            deletions.add(path)
    files[f"{MESSAGES_DIR}/README.md"] = README
    index["threads"] = new_states
    apply_changes(files, existing, deletions)
    apply_changes({INDEX_PATH: json.dumps(index, indent=1, sort_keys=True) + "\n"},
                  existing, (), label="messages index")
    log.info("messages: %d threads synced, %d messages fetched, %d threads still backfilling; redactions %s",
             totals["synced"], totals["fetched"], totals["pending"], STATS["redacted"] or "none")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BB_URL or not BB_PASSWORD:
        log.error("BLUEBUBBLES_URL / BLUEBUBBLES_PASSWORD not set")
        sys.exit(1)
    try:
        run()
    except SourceDown as e:
        log.warning("BlueBubbles unavailable, will retry next run: %s", e)


if __name__ == "__main__":
    main()
