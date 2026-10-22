#!/usr/bin/env python3
"""brain-sync: mirror Meerkat, Riffado, HubSpot, Google Calendar and Gmail into the Forgejo
brain repo as Markdown.

Every cycle it renders the full set of files it owns (everything under
Meerkat/, Riffado/, HUBSPOT_DIR, CALENDAR_DIR and EMAIL_DIR), compares git blob hashes against the
repo, and pushes only the differences as one commit through Forgejo's
change-files API. Files outside those folders are never touched.

Standard library only, so it runs on the stock python image.
"""

import base64
import email.header
import email.utils
import hashlib
import html
import io
import json
import logging
import os
import re
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "America/New_York"))
except Exception:  # no tzdata in the image: fall back to UTC
    LOCAL_TZ = timezone.utc

FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://forgejo.forgejo.svc.cluster.local").rstrip("/")
FORGEJO_TOKEN = os.environ["FORGEJO_TOKEN"]
REPO = os.environ.get("BRAIN_REPO", "WhiteHouse/brain")
BRANCH = os.environ.get("BRAIN_BRANCH", "main")
MEERKAT_URL = os.environ.get("MEERKAT_URL", "http://meerkat.meerkat.svc.cluster.local").rstrip("/")
MEERKAT_TOKEN = os.environ.get("MEERKAT_TOKEN", "")
RIFFADO_URL = os.environ.get("RIFFADO_URL", "http://riffado.riffado.svc.cluster.local:3000").rstrip("/")
RIFFADO_TOKEN = os.environ.get("RIFFADO_TOKEN", "")
RIFFADO_UI = os.environ.get("RIFFADO_UI", "https://riffado.internal.white.fm").rstrip("/")
MEERKAT_UI = os.environ.get("MEERKAT_UI", "https://meerkat.internal.white.fm").rstrip("/")
INTERVAL = int(os.environ.get("SYNC_INTERVAL_SECONDS", "120"))
# Riffado details are only re-fetched when a recording changes; this forces a
# full re-render now and then in case a summary lands without bumping updated_at.
FULL_RESYNC_SECONDS = int(os.environ.get("FULL_RESYNC_SECONDS", "21600"))
# Pages/<site>/ in the repo is the source of truth for <site>.pages.internal.white.fm.
PAGES_MCP_URL = os.environ.get("PAGES_MCP_URL", "http://pages.pages.svc.cluster.local:8080/mcp")
PAGES_MCP_HOST = os.environ.get("PAGES_MCP_HOST", "pages-mcp.internal.white.fm")
PAGES_MCP_TOKEN = os.environ.get("PAGES_MCP_TOKEN", "")
# Redeploy unchanged sites this often so edits made straight to the pages host
# don't stick: the repo always wins.
PAGES_REDEPLOY_SECONDS = int(os.environ.get("PAGES_REDEPLOY_SECONDS", "1800"))
# HubSpot, read with a service key. A full pull is a few dozen requests, so it
# simply re-renders everything every HUBSPOT_INTERVAL_SECONDS.
HUBSPOT_URL = os.environ.get("HUBSPOT_URL", "https://api.hubapi.com").rstrip("/")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_INTERVAL = int(os.environ.get("HUBSPOT_INTERVAL_SECONDS", "600"))
# Google Calendar and Gmail through one read-only OAuth client (calendar.readonly +
# gmail.readonly). Account-specific settings come from the SOPS secret; which mail is
# rendered is decided by the entity pages plus the repo-owned GOOGLE_CONFIG.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOOGLE_ACCOUNT = os.environ.get("GOOGLE_ACCOUNT", "").strip().casefold()  # mailbox address, for links and "me"
GOOGLE_INTERVAL = int(os.environ.get("GOOGLE_INTERVAL_SECONDS", "600"))
GOOGLE_CONFIG = ".brain/google.json"
CALENDAR_DIR = os.environ.get("CALENDAR_DIR", "Calendar").strip("/")
CALENDAR_PAST_DAYS = int(os.environ.get("CALENDAR_PAST_DAYS", "90"))
CALENDAR_FUTURE_DAYS = int(os.environ.get("CALENDAR_FUTURE_DAYS", "60"))
EMAIL_DIR = os.environ.get("EMAIL_DIR", "Email").strip("/")
EMAIL_INDEX = f"{EMAIL_DIR}/.index.json"
# Gmail threads.get calls per cycle (metadata checks plus full fetches): bounds the
# backfill so API quota and commit size stay sane; the rest follows next cycle.
GMAIL_MAX_FETCH = int(os.environ.get("GMAIL_MAX_FETCH", "150"))
EMAIL_MAX_CHARS = int(os.environ.get("EMAIL_MAX_CHARS", "4000"))  # per message
MAX_COMMIT_BYTES = int(os.environ.get("MAX_COMMIT_BYTES", str(4 * 1024 * 1024)))
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
RUN_ONCE = os.environ.get("RUN_ONCE", "") == "1"
PORT = int(os.environ.get("PORT", "8080"))
AUTHOR = {"name": "brain-sync", "email": "brain-sync@whitematter.tech"}

MEERKAT_DIR = "Meerkat"
RIFFADO_DIR = "Riffado"
RIFFADO_INDEX = f"{RIFFADO_DIR}/.index.json"
PAGES_DIR = "Pages"
# Where HubSpot notes go and which extra tags they get: set in the SOPS secret.
HUBSPOT_DIR = os.environ.get("HUBSPOT_DIR", "HubSpot").strip("/")
HUBSPOT_TAGS = [t.strip() for t in os.environ.get("HUBSPOT_TAGS", "").split(",") if t.strip()]
# Repo-owned routing rules: recordings matching a rule link to that rule's hub
# note (e.g. a business folder) and get its tags. Edit the file in the repo.
ROUTES_FILE = ".brain/routes.json"
# Entity pages: one note per person/company/business/property, built from the
# repo's own records, listing every note that mentions it. Tuned by ENTITY_CONFIG.
ENTITIES_DIR = os.environ.get("ENTITIES_DIR", "Entities").strip("/")
ENTITY_CONFIG = ".brain/entities.json"
ENTITY_INTERVAL = int(os.environ.get("ENTITY_INTERVAL_SECONDS", "600"))
ENTITY_MAX_MENTIONS = int(os.environ.get("ENTITY_MAX_MENTIONS", "300"))
# Repo-owned fixes for words transcription keeps getting wrong: {"wrong": "right"}.
CORRECTIONS_FILE = ".brain/corrections.json"
# Write-back into Meerkat: a summary note per contact and an activity per meeting/recording.
MEERKAT_WRITEBACK = os.environ.get("MEERKAT_WRITEBACK", "1") == "1"
MEERKAT_WRITEBACK_MAX = int(os.environ.get("MEERKAT_WRITEBACK_MAX", "60"))  # Meerkat writes per run
MEERKAT_ACTIVITY_DIRS = [d.strip().strip("/") for d in
                         os.environ.get("MEERKAT_ACTIVITY_DIRS", f"{CALENDAR_DIR},{RIFFADO_DIR}").split(",") if d.strip()]
MEERKAT_RECENT = int(os.environ.get("MEERKAT_RECENT", "12"))  # recent mentions listed in the summary note
BRAIN_UI = os.environ.get("BRAIN_UI", "").rstrip("/")  # Forgejo web URL, for links in Meerkat
# Everything brain-sync writes into Meerkat carries a marker, and render_contact skips
# marked notes/activities, so write-back never flows back into the repo.
WRITEBACK_MARK = "[brain] "  # first characters of the summary note
WRITEBACK_KEY_RE = re.compile(r"\[brain:([0-9a-f]{12})\]")  # last line of each activity description
SITE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

log = logging.getLogger("brain-sync")

STATE = {
    "last_success": {k: 0.0 for k in ("meerkat", "riffado", "pages", "hubspot", "entities", "google")},
    "last_error": {k: 0.0 for k in ("meerkat", "riffado", "pages", "hubspot", "entities", "google")},
    "files_written": 0,
    "commits": 0,
}
_wake = threading.Event()


# ---------------------------------------------------------------- HTTP helpers


def http_json(method, url, token, body=None, scheme="Bearer", retries=4):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method)
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
            detail = e.read()[:2000].decode(errors="replace")
            # Google reports per-minute quota as 403 rateLimitExceeded.
            limited = e.code in (429, 502, 503, 504) or (e.code == 403 and "ratelimitexceeded" in detail.lower())
            if limited and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 2)
                log.warning("%s %s -> %s, retrying in %ss", method, url.split("?")[0], e.code, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {detail[:500]}") from None
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 2))
                continue
            raise


def forgejo(method, path, body=None):
    return http_json(method, f"{FORGEJO_URL}/api/v1{path}", FORGEJO_TOKEN, body, scheme="token")


# -------------------------------------------------------------- formatting


def parse_ts(value):
    """Parse ISO timestamps from both APIs (Go emits 9 fractional digits)."""
    if not value or value.startswith("0001-01-01"):
        return None
    v = value.strip().replace("Z", "+00:00")
    v = re.sub(r"(\.\d{6})\d+", r"\1", v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def fmt_date(value):
    dt = parse_ts(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def fmt_dt(value):
    dt = parse_ts(value)
    return dt.isoformat(timespec="minutes") if dt else ""


def fmt_human(value):
    dt = parse_ts(value)
    return dt.strftime("%a %b %-d, %Y %-I:%M %p") if dt else ""


def fmt_duration(ms):
    if not ms:
        return ""
    minutes = round(ms / 60000)
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


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


def tagify(text):
    return re.sub(r"[^a-z0-9/_-]+", "-", (text or "").lower()).strip("-")


def clean_block(text):
    return (text or "").replace("\r\n", "\n").strip()


GENERATED = "<!-- Generated by brain-sync from {src}. Edits here are overwritten; change it in {src} instead. -->"


# ------------------------------------------------------------------ Meerkat


def meerkat_get(path):
    return http_json("GET", f"{MEERKAT_URL}/api/v1{path}", MEERKAT_TOKEN)


def contact_name(c):
    parts = [c.get("prefix"), c.get("firstname"), c.get("middle_name"), c.get("lastname"), c.get("suffix")]
    name = " ".join(p.strip() for p in parts if p and p.strip())
    return name or c.get("nickname") or f"Contact {c['ID']}"


MEERKAT_FULL_SECONDS = int(os.environ.get("MEERKAT_FULL_SECONDS", "1800"))
MEERKAT_REQUEST_DELAY = float(os.environ.get("MEERKAT_REQUEST_DELAY", "0.1"))
_meerkat_details = {}  # contact id -> (UpdatedAt, {child lists})
_meerkat_full_at = 0.0


def fetch_meerkat():
    contacts, page = [], 1
    while True:
        data = meerkat_get(f"/contacts?limit=100&page={page}") or {}
        batch = data.get("contacts") or []
        contacts.extend(batch)
        if not batch or len(contacts) >= int(data.get("total") or 0):
            break
        page += 1
    # Each contact's notes/activities/relationships/reminders cost five requests,
    # so only re-fetch them for contacts that are new or whose UpdatedAt moved,
    # with a full refresh every MEERKAT_FULL_SECONDS (child records don't always
    # bump the contact) and a small delay between requests to stay under
    # Meerkat's rate limit.
    global _meerkat_full_at
    full = time.time() - _meerkat_full_at > MEERKAT_FULL_SECONDS
    seen = set()
    for c in contacts:
        cid = c["ID"]
        seen.add(cid)
        stamp = c.get("UpdatedAt") or ""
        cached = _meerkat_details.get(cid)
        if not full and cached and cached[0] == stamp:
            c.update(cached[1])
            continue
        details = {}
        for key, path, field in (("_notes", "notes", "notes"), ("_activities", "activities", "activities"),
                                 ("_relationships", "relationships", "relationships"),
                                 ("_incoming", "incoming-relationships", "incoming_relationships"),
                                 ("_reminders", "reminders", "reminders")):
            details[key] = (meerkat_get(f"/contacts/{cid}/{path}") or {}).get(field) or []
            time.sleep(MEERKAT_REQUEST_DELAY)
        _meerkat_details[cid] = (stamp, details)
        c.update(details)
    for cid in list(_meerkat_details):
        if cid not in seen:
            del _meerkat_details[cid]
    if full:
        _meerkat_full_at = time.time()
    return contacts


def assign_paths(items, base_of, key_of, folder):
    """Stable, collision-free paths: the plain name wins for the oldest key."""
    # Grouped case-insensitively: "Jane DeSmith" and "Jane Desmith" would collide on macOS.
    by_base = {}
    for it in items:
        by_base.setdefault(base_of(it).casefold(), []).append(it)
    paths = {}
    for group in by_base.values():
        group.sort(key=lambda it: str(key_of(it)))
        for i, it in enumerate(group):
            base = base_of(it)
            name = base if i == 0 else f"{base} ({str(key_of(it))[:8]})"
            paths[key_of(it)] = f"{folder}/{name}.md"
    return paths


def is_writeback_note(n):
    return (n.get("content") or "").startswith(WRITEBACK_MARK)


def writeback_key(a):
    m = WRITEBACK_KEY_RE.search(a.get("description") or "")
    return m.group(1) if m else None


def render_contact(c, names):
    name = contact_name(c)
    emails = [e.get("value") for e in (c.get("emails") or []) if e.get("value")] or (
        [c["email"]] if c.get("email") else []
    )
    phones = [p.get("value") for p in (c.get("phones") or []) if p.get("value")] or (
        [c["phone"]] if c.get("phone") else []
    )
    circles = c.get("circles") or []
    fm = {
        "source": "meerkat",
        "meerkat_id": c["ID"],
        "name": name,
        "aliases": [c["nickname"]] if c.get("nickname") else [],
        "organization": c.get("organization"),
        "job_title": c.get("job_title"),
        "department": c.get("department"),
        "circles": circles,
        "emails": emails,
        "phones": phones,
        "birthday": c.get("birthday"),
        "archived": True if c.get("archived") else None,
        "url": f"{MEERKAT_UI}/contacts/{c['ID']}",
        "tags": ["person", "meerkat"] + [f"circle/{tagify(x)}" for x in circles if tagify(x)],
    }
    out = [frontmatter(fm), GENERATED.format(src="Meerkat"), "", f"# {name}"]
    role = " · ".join(x for x in [c.get("job_title"), c.get("department"), c.get("organization")] if x)
    if role:
        out += ["", role]

    details = []
    for label, val in [
        ("Nickname", c.get("nickname")),
        ("Email", ", ".join(emails)),
        ("Phone", ", ".join(phones)),
        ("Birthday", c.get("birthday")),
        ("Anniversary", c.get("anniversary")),
        ("Gender", c.get("gender")),
        ("Address", c.get("address")),
        ("How we met", c.get("how_we_met")),
        ("Work", c.get("work_information")),
        ("Food preference", c.get("food_preference")),
        ("Contact info", c.get("contact_information")),
        ("Role", c.get("role")),
    ]:
        if val and str(val).strip():
            details.append(f"- **{label}:** {clean_block(str(val))}")
    for a in c.get("addresses") or []:
        val = a.get("value") if isinstance(a, dict) else a
        if val:
            details.append(f"- **Address:** {val}")
    for u in c.get("urls") or []:
        val = u.get("value") if isinstance(u, dict) else u
        if val:
            details.append(f"- **URL:** {val}")
    for k, v in sorted((c.get("custom_fields") or {}).items()):
        if v not in (None, ""):
            details.append(f"- **{k}:** {v}")
    if details:
        out += ["", "## Details", *details]

    rels = []
    for r in c["_relationships"]:
        target = names.get(r.get("related_contact_id")) if r.get("related_contact_id") else None
        who = f"[[{target}]]" if target else (r.get("name") or "?")
        extra = f" (birthday {r['birthday']})" if r.get("birthday") else ""
        rels.append(f"- {r.get('type') or 'Related'}: {who}{extra}")
    for r in c["_incoming"]:
        src = names.get(r.get("contact_id"))
        if src:
            rels.append(f"- Listed by [[{src}]] as their {r.get('type') or 'relation'}")
    if rels:
        out += ["", "## Relationships", *rels]

    notes = sorted((n for n in c["_notes"] if not is_writeback_note(n)), key=lambda n: n.get("date") or n.get("CreatedAt") or "", reverse=True)
    if notes:
        out += ["", "## Notes"]
        for n in notes:
            out += ["", f"### {fmt_date(n.get('date') or n.get('CreatedAt')) or 'Undated'}", "", clean_block(n.get("content"))]

    acts = sorted((a for a in c["_activities"] if not writeback_key(a)), key=lambda a: a.get("date") or "", reverse=True)
    if acts:
        out += ["", "## Activities"]
        for a in acts:
            head = f"### {fmt_date(a.get('date'))} — {a.get('title') or 'Activity'}"
            if a.get("location"):
                head += f" ({a['location']})"
            out += ["", head]
            if clean_block(a.get("description")):
                out += ["", clean_block(a.get("description"))]

    rems = sorted(c["_reminders"], key=lambda r: r.get("remind_at") or "")
    if rems:
        out += ["", "## Reminders"]
        for r in rems:
            done = "x" if r.get("completed") else " "
            when = fmt_date(r.get("remind_at"))
            rec = f" (repeats {r['recurrence']})" if r.get("recurrence") and r.get("recurrence") != "once" else ""
            out.append(f"- [{done}] {when} — {clean_block(r.get('message'))}{rec}")

    return "\n".join(out).rstrip() + "\n"


def render_meerkat(contacts):
    paths = assign_paths(contacts, lambda c: safe_name(contact_name(c)), lambda c: c["ID"], f"{MEERKAT_DIR}/People")
    names = {cid: p.rsplit("/", 1)[1][:-3] for cid, p in paths.items()}
    files = {paths[c["ID"]]: render_contact(c, names) for c in contacts}
    files[f"{MEERKAT_DIR}/README.md"] = (
        "# Meerkat\n\n"
        "One note per contact in [Meerkat](" + MEERKAT_UI + "), with their notes, activities,\n"
        "relationships and reminders. Written by `brain-sync` (whitehouse-rke2\n"
        "`apps/ai/brain`) every couple of minutes; edit contacts in Meerkat, not here.\n"
    )
    return files


# ------------------------------------------------------------------ Riffado


def riffado_get(path):
    return http_json("GET", f"{RIFFADO_URL}/api/v1{path}", RIFFADO_TOKEN)


def list_recordings():
    out, cursor = [], None
    while True:
        q = "/recordings?limit=100" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        data = riffado_get(q) or {}
        out.extend(data.get("data") or [])
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            return out


def recording_sig(r):
    return f"{r.get('updated_at')}|{int(bool(r.get('has_transcription')))}|{int(bool(r.get('has_summary')))}"


def recording_base(r):
    day = fmt_date(r.get("recorded_at") or r.get("created_at")) or "undated"
    # Plaud titles often lead with "MM-DD "; the filename already has the date.
    title = re.sub(r"^\d{1,2}-\d{1,2}\s+", "", r.get("title") or "") or "Untitled recording"
    return f"{day[:4]}/{day} {safe_name(title)}"


def load_routes():
    """Rules from ROUTES_FILE: [{"note": path, "match": [regex...], "ids": [...], "tags": [...]}]."""
    raw = read_raw(ROUTES_FILE)
    if not raw:
        return [], ""
    try:
        rules = json.loads(raw)
        compiled = [dict(r, _re=[re.compile(m, re.I) for m in r.get("match") or []]) for r in rules if r.get("note")]
    except (ValueError, re.error, AttributeError) as e:
        log.warning("ignoring %s: %s", ROUTES_FILE, e)
        return [], ""
    return compiled, hashlib.sha1(raw.encode()).hexdigest()[:12]


def load_corrections():
    """[(compiled regex, replacement)] from CORRECTIONS_FILE, plus a digest for change detection."""
    raw = read_raw(CORRECTIONS_FILE)
    if not raw:
        return [], ""
    try:
        pairs = [(re.compile(r"\b" + re.escape(wrong) + r"\b", re.I), right)
                 for wrong, right in json.loads(raw).items() if wrong and right]
    except (ValueError, AttributeError) as e:
        log.warning("ignoring %s: %s", CORRECTIONS_FILE, e)
        return [], ""
    return pairs, hashlib.sha1(raw.encode()).hexdigest()[:12]


def correct(text, corrections):
    for rx, right in corrections:
        text = rx.sub(lambda _m: right, text)
    return text


def correct_recording(d, corrections):
    """Apply corrections to the title, summary and transcript before rendering."""
    if not corrections:
        return d
    d = dict(d, title=correct(d.get("title") or "", corrections) or d.get("title"))
    s = dict(d.get("summary") or {})
    if s.get("text"):
        s["text"] = correct(s["text"], corrections)
    for k in ("key_points", "action_items"):
        if s.get(k):
            s[k] = [correct(x, corrections) for x in s[k]]
    t = dict(d.get("transcript") or {})
    if t.get("text"):
        t["text"] = correct(t["text"], corrections)
    return dict(d, summary=s or d.get("summary"), transcript=t or d.get("transcript"))


def route_links(routes, rid, *texts):
    hay = "\n".join(t for t in texts if t)
    hits = [r for r in routes if rid in (r.get("ids") or []) or any(rx.search(hay) for rx in r["_re"])]
    links = [f"[[{r['note'].removesuffix('.md')}|{r['note'].removesuffix('.md').rsplit('/', 1)[-1]}]]" for r in hits]
    tags = [t for r in hits for t in r.get("tags") or []]
    return links, tags


def render_recording(d, routes=()):
    title = d.get("title") or "Untitled recording"
    t = d.get("transcript") or {}
    s = d.get("summary") or {}
    device = (d.get("device") or {}).get("name")
    related, extra_tags = route_links(routes, d["id"], title, s.get("text"), "\n".join(s.get("key_points") or []),
                                      t.get("text"))
    fm = {
        "source": "riffado",
        "riffado_id": d["id"],
        "title": title,
        "recorded_at": fmt_dt(d.get("recorded_at")),
        "duration": fmt_duration(d.get("duration_ms")),
        "device": device,
        "language": t.get("language"),
        "transcript_source": t.get("source"),
        "summary_model": s.get("model"),
        "updated_at": d.get("updated_at"),
        "url": f"{RIFFADO_UI}/recordings/{d['id']}",
        "related": related,
        "tags": ["recording", "riffado"] + extra_tags,
    }
    meta = " · ".join(x for x in [fmt_human(d.get("recorded_at")), fmt_duration(d.get("duration_ms")), device] if x)
    out = [frontmatter(fm), GENERATED.format(src="Riffado"), "", f"# {title}", "", f"*{meta}*"]
    if related:
        out += ["", "**Related:** " + ", ".join(related)]
    if clean_block(s.get("text")):
        out += ["", "## Summary", "", clean_block(s.get("text"))]
    if s.get("key_points"):
        out += ["", "## Key points", ""] + [f"- {clean_block(p)}" for p in s["key_points"] if clean_block(p)]
    if s.get("action_items"):
        out += ["", "## Action items", ""] + [f"- [ ] {clean_block(p)}" for p in s["action_items"] if clean_block(p)]
    if clean_block(t.get("text")):
        out += ["", "## Transcript", "", clean_block(t.get("text"))]
    elif not s:
        out += ["", "_Not transcribed yet._"]
    return "\n".join(out).rstrip() + "\n"


def render_riffado(current_files, full):
    """Returns (files, unchanged_paths). Unchanged recordings keep their current blob."""
    try:
        index = json.loads(current_files.get(RIFFADO_INDEX) or "{}")
    except ValueError:
        index = {}
    routes, routes_sig = load_routes()
    corrections, corrections_sig = load_corrections()
    recs = list_recordings()
    paths = assign_paths(recs, recording_base, lambda r: r["id"], RIFFADO_DIR)
    files, keep, new_index = {}, set(), {}
    fetched = 0
    for r in recs:
        # Editing the routes or corrections re-renders every recording once.
        path, sig = paths[r["id"]], f"{recording_sig(r)}|{routes_sig}|{corrections_sig}"
        prev = index.get(r["id"]) or {}
        if not full and prev.get("sig") == sig and prev.get("path") == path and path in current_files:
            keep.add(path)
        else:
            detail = riffado_get(f"/recordings/{r['id']}")
            if not detail:
                continue
            files[path] = render_recording(correct_recording(detail, corrections), routes)
            fetched += 1
        new_index[r["id"]] = {"path": path, "sig": sig}
    files[RIFFADO_INDEX] = json.dumps(new_index, indent=1, sort_keys=True) + "\n"
    files[f"{RIFFADO_DIR}/README.md"] = (
        "# Riffado\n\n"
        "Transcripts and AI summaries of Plaud recordings from\n"
        "[Riffado](" + RIFFADO_UI + "), one note per recording, filed by year.\n"
        "Written by `brain-sync` (whitehouse-rke2 `apps/ai/brain`); rename or\n"
        "re-summarize in Riffado and the note follows.\n"
    )
    log.info("riffado: %d recordings, %d fetched", len(recs), fetched)
    return files, keep


# ------------------------------------------------------------------ HubSpot


HS_PROPS = {
    "companies": ["name", "domain", "website", "industry", "phone", "address", "city", "state", "zip",
                  "description", "lifecyclestage", "numberofemployees", "annualrevenue", "createdate",
                  "hs_lastmodifieddate"],
    "contacts": ["firstname", "lastname", "email", "phone", "mobilephone", "jobtitle", "company",
                 "lifecyclestage", "hs_lead_status", "city", "state", "website", "createdate", "lastmodifieddate"],
    "deals": ["dealname", "amount", "dealstage", "pipeline", "closedate", "createdate", "dealtype",
              "description", "hs_lastmodifieddate"],
    "notes": ["hs_note_body", "hs_timestamp"],
    "calls": ["hs_call_title", "hs_call_body", "hs_call_direction", "hs_call_status", "hs_call_duration",
              "hs_timestamp"],
    "meetings": ["hs_meeting_title", "hs_meeting_body", "hs_internal_meeting_notes", "hs_meeting_outcome",
                 "hs_meeting_start_time", "hs_timestamp"],
    "tasks": ["hs_task_subject", "hs_task_body", "hs_task_status", "hs_task_priority", "hs_timestamp"],
}
HS_ASSOC = {
    "contacts": ["companies", "deals"],
    "deals": ["companies", "contacts"],
    "notes": ["contacts", "companies", "deals"],
    "calls": ["contacts", "companies", "deals"],
    "meetings": ["contacts", "companies", "deals"],
    "tasks": ["contacts", "companies", "deals"],
}
# Meeting passwords, dial-in PINs and similar codes, redacted wherever free text from a
# source is rendered (CRM bodies, event descriptions, mail).
SECRET_RE = re.compile(
    r"(?i)(?<![a-z])(password|passcode|passwort|pwd|pin|access code|meeting code|participant code|conference code"
    r"|host key|security code|verification code|confirmation code|one[- ]time (?:pass)?code|otp"
    r"|log[- ]?in code|sign[- ]in code|auth(?:entication)? code|meeting id|conference id|webinar id)"
    r"(\s*(?:[:=#]|\bis\b)\s*)([^\s&<]+(?:[ ]\d{2,}#?)*)")
# One-tap dial strings carry the meeting id and passcode: "+15551234567,,123456#,,,,*789#".
DIAL_RE = re.compile(r"(\+?\d[\d ().-]{6,}\d)\s*,,[\d#*,]+")
# One-time codes in mail: "123456 is your ... code", "G-123456", and a lone code line in a
# message that talks about a code.
OTP_BEFORE_RE = re.compile(r"(?i)\b\d{4,8}\b(?=[^\n]{0,40}\b(?:is your|is the|as your)\b[^\n]{0,40}"
                           r"\b(?:code|pin|password|otp)\b)")
OTP_PREFIX_RE = re.compile(r"\b([A-Z]{1,3}-)\d{4,8}\b")
OTP_CONTEXT_RE = re.compile(r"(?i)\b(verification|security|one[- ]time|sign[- ]in|log[- ]?in|confirmation|access|"
                            r"authentication|2fa|two[- ]factor)\b[^\n]{0,30}\bcode\b|\bcode\b[^\n]{0,40}\b(expires?|valid)\b")
OTP_LINE_RE = re.compile(r"(?m)^(\s*)(?:\d{4,8}|\d{3}[ -]\d{3})(\s*)$")


def redact(text, otp=False):
    text = SECRET_RE.sub(r"\1\2[redacted]", text or "")
    text = DIAL_RE.sub(r"\1,,[redacted]", text)
    if otp:
        text = OTP_BEFORE_RE.sub("[redacted]", text)
        text = OTP_PREFIX_RE.sub(r"\1[redacted]", text)
        if OTP_CONTEXT_RE.search(text):
            text = OTP_LINE_RE.sub(r"\1[redacted]\2", text)
    return text


def hubspot_get(path):
    return http_json("GET", f"{HUBSPOT_URL}{path}", HUBSPOT_TOKEN)


def hs_list(obj):
    out, after = [], None
    while True:
        q = f"/crm/v3/objects/{obj}?limit=100&properties={','.join(HS_PROPS[obj])}"
        if HS_ASSOC.get(obj):
            q += f"&associations={','.join(HS_ASSOC[obj])}"
        if after:
            q += f"&after={after}"
        data = hubspot_get(q) or {}
        out.extend(data.get("results") or [])
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return out


def hs_assoc(rec, kind):
    ids = [a["id"] for a in ((rec.get("associations") or {}).get(kind) or {}).get("results") or []]
    return list(dict.fromkeys(ids))


def _html_link(m):
    url, label = html.unescape(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2)).strip()
    if not label or html.unescape(label) == url:
        return url
    return f"{label} ({url})"


def html_to_text(text, secrets=True, links=False):
    if not text:
        return ""
    if "<" in text:
        text = re.sub(r"(?is)<(head|style|script|title)\b.*?</\1\s*>|<!--.*?-->", "", text)
        if links:  # keep link targets: "label (url)"
            text = re.sub(r"(?is)<a\s[^>]*?href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", _html_link, text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
        text = re.sub(r"(?i)</(p|div|h\d|li|tr|ul|ol|table)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = "\n".join(line.strip() for line in text.replace("\r\n", "\n").split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return (redact(text) if secrets else text).strip()


def fmt_money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def hs_engagement(kind, p):
    """(timestamp, heading, body) for one note/call/meeting/task."""
    ts = p.get("hs_timestamp") or p.get("hs_meeting_start_time") or ""
    if kind == "notes":
        return ts, "Note", html_to_text(p.get("hs_note_body"))
    if kind == "calls":
        bits = [x.title() for x in (p.get("hs_call_direction"), p.get("hs_call_status")) if x]
        if p.get("hs_call_duration"):
            bits.append(fmt_duration(int(p["hs_call_duration"])))
        head = f"Call: {p.get('hs_call_title') or 'Call'}" + (f" ({', '.join(bits)})" if bits else "")
        return ts, head, html_to_text(p.get("hs_call_body"))
    if kind == "meetings":
        head = f"Meeting: {p.get('hs_meeting_title') or 'Meeting'}"
        if p.get("hs_meeting_outcome"):
            head += f" ({p['hs_meeting_outcome'].title()})"
        body = html_to_text(p.get("hs_meeting_body"))
        notes = html_to_text(p.get("hs_internal_meeting_notes"))
        return ts, head, "\n\n".join(x for x in (body, f"**Internal notes:** {notes}" if notes else "") if x)
    status = " ✓" if p.get("hs_task_status") == "COMPLETED" else ""
    return ts, f"Task: {p.get('hs_task_subject') or 'Task'}{status}", html_to_text(p.get("hs_task_body"))


def render_timeline(entries):
    out = []
    for ts, head, body in sorted(entries, key=lambda e: e[0] or "", reverse=True):
        out += ["", f"### {fmt_date(ts) or 'Undated'} — {safe_name(head, 140)}"]
        if body:
            out += ["", body]
    return ["", "## Timeline", *out] if out else []


def link(path, name):
    return f"[[{path[:-3]}|{name}]]"


def render_hubspot():
    recs = {obj: hs_list(obj) for obj in HS_PROPS}
    stages = {}
    for pipe in (hubspot_get("/crm/v3/pipelines/deals") or {}).get("results") or []:
        for st in pipe.get("stages") or []:
            stages[(pipe["id"], st["id"])] = (pipe["label"], st["label"])

    def company_name(c):
        p = c["properties"]
        return p.get("name") or p.get("domain") or f"Company {c['id']}"

    def contact_name(c):
        p = c["properties"]
        full = " ".join(x.strip() for x in (p.get("firstname"), p.get("lastname")) if x and x.strip())
        return full or p.get("email") or f"Contact {c['id']}"

    def deal_name(d):
        return d["properties"].get("dealname") or f"Deal {d['id']}"

    by = {k: {r["id"]: r for r in recs[k]} for k in ("companies", "contacts", "deals")}
    paths = {
        "companies": assign_paths(recs["companies"], lambda c: safe_name(company_name(c)), lambda c: c["id"],
                                  f"{HUBSPOT_DIR}/Companies"),
        "contacts": assign_paths(recs["contacts"], lambda c: safe_name(contact_name(c)), lambda c: c["id"],
                                 f"{HUBSPOT_DIR}/Contacts"),
        "deals": assign_paths(recs["deals"], lambda d: safe_name(deal_name(d)), lambda d: d["id"],
                              f"{HUBSPOT_DIR}/Deals"),
    }
    names = {"companies": company_name, "contacts": contact_name, "deals": deal_name}

    # Associations are fetched from the contact/deal side; mirror them so both ends link.
    links = {k: {i: {"companies": set(), "contacts": set(), "deals": set()} for i in by[k]} for k in by}

    def connect(a, ai, b, bi):
        if ai in links[a] and bi in links[b]:
            links[a][ai][b].add(bi)
            links[b][bi][a].add(ai)

    for kind in ("contacts", "deals"):
        for r in recs[kind]:
            for other in HS_ASSOC[kind]:
                for oid in hs_assoc(r, other):
                    connect(kind, r["id"], other, oid)

    timeline = {k: {i: [] for i in by[k]} for k in by}
    for kind in ("notes", "calls", "meetings", "tasks"):
        for e in recs[kind]:
            entry = hs_engagement(kind, e["properties"])
            for other in ("contacts", "companies", "deals"):
                for oid in hs_assoc(e, other):
                    if oid in timeline[other]:
                        timeline[other][oid].append(entry)

    def link_list(kind, ids):
        items = sorted((names[kind](by[kind][i]), paths[kind][i]) for i in ids)
        return [f"- {link(path, name)}" for name, path in items]

    def section(title, kind, ids):
        return ["", f"## {title}", *link_list(kind, ids)] if ids else []

    def details(pairs):
        rows = [f"- **{label}:** {val}" for label, val in pairs if val not in (None, "")]
        return ["", "## Details", *rows] if rows else []

    files = {}
    for c in recs["companies"]:
        p, i = c["properties"], c["id"]
        name = company_name(c)
        fm = {"source": "hubspot", "hubspot_id": i, "name": name, "domain": p.get("domain"),
              "industry": p.get("industry"), "lifecycle_stage": p.get("lifecyclestage"),
              "city": p.get("city"), "state": p.get("state"), "updated_at": p.get("hs_lastmodifieddate"),
              "url": c.get("url"), "tags": ["company", "hubspot", *HUBSPOT_TAGS]}
        out = [frontmatter(fm), GENERATED.format(src="HubSpot"), "", f"# {name}"]
        if html_to_text(p.get("description")):
            out += ["", html_to_text(p.get("description"))]
        address = ", ".join(x for x in (p.get("address"), p.get("city"), p.get("state"), p.get("zip")) if x)
        out += details([("Website", p.get("website") or p.get("domain")), ("Phone", p.get("phone")),
                        ("Address", address), ("Industry", p.get("industry")),
                        ("Employees", p.get("numberofemployees")), ("Annual revenue", fmt_money(p.get("annualrevenue"))),
                        ("Lifecycle stage", p.get("lifecyclestage")), ("Created", fmt_date(p.get("createdate")))])
        out += section("Contacts", "contacts", links["companies"][i]["contacts"])
        out += section("Deals", "deals", links["companies"][i]["deals"])
        out += render_timeline(timeline["companies"][i])
        files[paths["companies"][i]] = "\n".join(out).rstrip() + "\n"

    for c in recs["contacts"]:
        p, i = c["properties"], c["id"]
        name = contact_name(c)
        companies = [names["companies"](by["companies"][x]) for x in links["contacts"][i]["companies"]]
        fm = {"source": "hubspot", "hubspot_id": i, "name": name, "email": p.get("email"),
              "phones": [x for x in (p.get("phone"), p.get("mobilephone")) if x], "job_title": p.get("jobtitle"),
              "organization": (companies[0] if companies else p.get("company")),
              "lifecycle_stage": p.get("lifecyclestage"), "lead_status": p.get("hs_lead_status"),
              "updated_at": p.get("lastmodifieddate"), "url": c.get("url"),
              "tags": ["person", "hubspot", *HUBSPOT_TAGS]}
        out = [frontmatter(fm), GENERATED.format(src="HubSpot"), "", f"# {name}"]
        role = " · ".join(x for x in (p.get("jobtitle"), p.get("company")) if x)
        if role:
            out += ["", role]
        out += details([("Email", p.get("email")), ("Phone", p.get("phone")), ("Mobile", p.get("mobilephone")),
                        ("Location", ", ".join(x for x in (p.get("city"), p.get("state")) if x)),
                        ("Website", p.get("website")), ("Lifecycle stage", p.get("lifecyclestage")),
                        ("Lead status", p.get("hs_lead_status")), ("Created", fmt_date(p.get("createdate")))])
        out += section("Companies", "companies", links["contacts"][i]["companies"])
        out += section("Deals", "deals", links["contacts"][i]["deals"])
        out += render_timeline(timeline["contacts"][i])
        files[paths["contacts"][i]] = "\n".join(out).rstrip() + "\n"

    for d in recs["deals"]:
        p, i = d["properties"], d["id"]
        name = deal_name(d)
        pipeline, stage = stages.get((p.get("pipeline"), p.get("dealstage")), (p.get("pipeline"), p.get("dealstage")))
        fm = {"source": "hubspot", "hubspot_id": i, "name": name, "amount": fmt_money(p.get("amount")),
              "pipeline": pipeline, "stage": stage, "close_date": fmt_date(p.get("closedate")),
              "updated_at": p.get("hs_lastmodifieddate"), "url": d.get("url"),
              "tags": ["deal", "hubspot", *HUBSPOT_TAGS, f"deal-stage/{tagify(stage)}"]}
        out = [frontmatter(fm), GENERATED.format(src="HubSpot"), "", f"# {name}"]
        if html_to_text(p.get("description")):
            out += ["", html_to_text(p.get("description"))]
        out += details([("Amount", fmt_money(p.get("amount"))), ("Pipeline", pipeline), ("Stage", stage),
                        ("Type", p.get("dealtype")), ("Close date", fmt_date(p.get("closedate"))),
                        ("Created", fmt_date(p.get("createdate")))])
        out += section("Companies", "companies", links["deals"][i]["companies"])
        out += section("Contacts", "contacts", links["deals"][i]["contacts"])
        out += render_timeline(timeline["deals"][i])
        files[paths["deals"][i]] = "\n".join(out).rstrip() + "\n"

    files[f"{HUBSPOT_DIR}/README.md"] = (
        "# HubSpot\n\n"
        "HubSpot CRM: one note per company, contact and deal, linked\n"
        "to each other, with the notes, calls, meetings and tasks logged against them.\n"
        "Written by `brain-sync` (whitehouse-rke2 `apps/ai/brain`) every few minutes;\n"
        "edit records in HubSpot, not here.\n"
    )
    log.info("hubspot: %s", ", ".join(f"{len(v)} {k}" for k, v in recs.items()))
    return files


# ----------------------------------------------------------------- Entities


ENTITY_KINDS = {"person": "People", "company": "Companies", "business": "Businesses", "property": "Properties"}
ORG_KINDS = {"company", "business"}
ORG_SUFFIX = re.compile(r"[,.]?\s+(llc|l\.l\.c\.|inc\.?|incorporated|co\.?|corp\.?|corporation|ltd\.?|company|pllc|pc)$", re.I)
GENERIC_TAGS = {"person", "company", "deal", "hubspot", "meerkat", "recording", "riffado", "business", "property"}


def repo_snapshot():
    """{path: text} for every Markdown file on BRANCH, from one archive download."""
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1/repos/{REPO}/archive/{BRANCH}.tar.gz")
    req.add_header("Authorization", f"token {FORGEJO_TOKEN}")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar:
            if not m.isfile():
                continue
            path = m.name.split("/", 1)[1] if "/" in m.name else m.name
            if path.endswith(".md") or path == ENTITY_CONFIG:
                files[path] = tar.extractfile(m).read().decode("utf-8", errors="replace")
    return files


def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        k, sep, v = line.partition(":")
        if not sep or line.startswith((" ", "-")):
            continue
        v = v.strip()
        try:
            fm[k.strip()] = json.loads(v)
        except ValueError:
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip("\"'")
    return fm


def strip_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            return text[end + 4:]
    return text


def norm_name(name, kind):
    n = re.sub(r"\s+", " ", (name or "").strip())
    if kind in ORG_KINDS:
        n = ORG_SUFFIX.sub("", n)
        n = re.sub(r"^the\s+", "", n, flags=re.I)
    return re.sub(r"[^\w& ]+", "", n).casefold().strip()


class Entities:
    def __init__(self):
        self.items = []  # dicts
        self.by_key = {}

    def find(self, kind, name=None, emails=(), domains=()):
        keys = [("email", e.casefold()) for e in emails if e] + [("domain", d.casefold()) for d in domains if d]
        if name:
            group = "org" if kind in ORG_KINDS else kind
            keys.append((group, norm_name(name, kind)))
        for k in keys:
            if k in self.by_key:
                return self.by_key[k]
        return None

    def add(self, kind, name, record=None, aliases=(), emails=(), domains=(), facts=None, tags=()):
        if not name or not norm_name(name, kind):
            return None
        e = self.find(kind, name, emails, domains)
        if e is None:
            e = {"kind": kind, "name": name, "aliases": set(), "emails": set(), "domains": set(),
                 "records": [], "facts": {}, "tags": set(), "orgs": set()}
            self.items.append(e)
        elif kind in ORG_KINDS and e["kind"] == "company" and kind == "business":
            e["kind"] = kind
        e["aliases"].update(a for a in aliases if a)
        e["aliases"].add(name)
        e["emails"].update(x.casefold() for x in emails if x)
        e["domains"].update(x.casefold() for x in domains if x)
        if record and record not in e["records"]:
            e["records"].append(record)
        for k, v in (facts or {}).items():
            if v and k not in e["facts"]:
                e["facts"][k] = v
        e["tags"].update(t for t in tags if t and t not in GENERIC_TAGS)
        self.index(e)
        return e

    def index(self, e):
        group = "org" if e["kind"] in ORG_KINDS else e["kind"]
        for a in e["aliases"]:
            self.by_key.setdefault((group, norm_name(a, e["kind"])), e)
        for x in e["emails"]:
            self.by_key.setdefault(("email", x), e)
        for x in e["domains"]:
            self.by_key.setdefault(("domain", x), e)

    def merge(self, a, b):
        if a is b:
            return
        for k in ("aliases", "emails", "domains", "tags", "orgs"):
            a[k] |= b[k]
        a["records"] += [r for r in b["records"] if r not in a["records"]]
        for k, v in b["facts"].items():
            a["facts"].setdefault(k, v)
        self.items.remove(b)
        self.by_key = {k: (a if v is b else v) for k, v in self.by_key.items()}
        self.index(a)


def as_list(v):
    return v if isinstance(v, list) else ([v] if v else [])


def collect_entities(files, cfg):
    ents = Entities()
    title = lambda path: path.rsplit("/", 1)[-1][:-3]
    for path, text in sorted(files.items()):
        if path.startswith(ENTITIES_DIR + "/"):
            continue
        fm = parse_frontmatter(text)
        src = fm.get("source")
        if src == "meerkat":
            ents.add("person", fm.get("name") or title(path), (path, "Meerkat"), as_list(fm.get("aliases")),
                     as_list(fm.get("emails")), facts={"organization": fm.get("organization"),
                                                       "title": fm.get("job_title")}, tags=as_list(fm.get("tags")))
        elif src == "hubspot" and path.startswith(HUBSPOT_DIR + "/Contacts/"):
            ents.add("person", fm.get("name") or title(path), (path, "HubSpot contact"),
                     emails=as_list(fm.get("email")), facts={"organization": fm.get("organization"),
                                                             "title": fm.get("job_title")}, tags=as_list(fm.get("tags")))
        elif src == "hubspot" and path.startswith(HUBSPOT_DIR + "/Companies/"):
            ents.add("company", fm.get("name") or title(path), (path, "HubSpot company"),
                     domains=as_list(fm.get("domain")), tags=as_list(fm.get("tags")))
        elif not src and fm.get("type") in ENTITY_KINDS:
            ents.add(fm["type"], fm.get("name") or title(path), (path, "Note"), as_list(fm.get("aliases")),
                     tags=as_list(fm.get("tags")))
    for src in cfg.get("folder_sources") or []:
        base, kind = src.get("path", "").strip("/"), src.get("type", "company")
        for path, text in files.items():
            rest = path[len(base) + 1:] if path.startswith(base + "/") else ""
            if rest.count("/") == 1 and rest.endswith("/README.md"):
                h1 = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), rest.split("/")[0])
                ents.add(kind, h1, (path, "Context"), tags=src.get("tags") or ())
    for item in cfg.get("entities") or []:
        ents.add(item.get("type", "company"), item.get("name"), aliases=item.get("aliases") or (),
                 emails=as_list(item.get("emails")), domains=as_list(item.get("domains")), tags=item.get("tags") or ())
    for name, aliases in (cfg.get("aliases") or {}).items():
        e = next((x for x in ents.items if name in x["aliases"]), None)
        if e:
            e["aliases"].update(aliases)
            ents.index(e)
    for group in cfg.get("merge") or []:
        found = [next((x for x in ents.items if n in x["aliases"]), None) for n in group]
        found = [f for f in found if f]
        for other in found[1:]:
            ents.merge(found[0], other)
    ignore = {x.casefold() for x in cfg.get("ignore") or []}
    ents.items = [e for e in ents.items if e["name"].casefold() not in ignore]
    for e in ents.items:
        e["aliases"] = {a for a in e["aliases"] if a.casefold() not in ignore}
    # A person's organization links them to that org's entity.
    for e in ents.items:
        org = e["facts"].get("organization")
        target = ents.find("company", org) if org else None
        if target and target is not e:
            e["orgs"].add(id(target))
            target.setdefault("people", set()).add(id(e))
    return ents.items, cfg


def match_aliases(e, cfg):
    """Aliases safe for free-text matching: multi-word names, domains, explicit aliases."""
    explicit = set((cfg.get("aliases") or {}).get(e["name"], []))
    for item in cfg.get("entities") or []:
        if item.get("name") in e["aliases"]:
            explicit |= {item["name"], *(item.get("aliases") or [])}
    out = set()
    for a in e["aliases"] | e["domains"]:
        words = a.split()
        if len(words) >= 2 or "." in a or a in explicit:
            out.add(a)
        if e["kind"] in ORG_KINDS:
            short = ORG_SUFFIX.sub("", a).strip(" ,")
            if short != a and (len(short.split()) >= 2 or short in explicit):
                out.add(short)
    no_match = {x.casefold() for x in cfg.get("no_match") or []}
    return {a for a in out if len(a) >= int(cfg.get("min_alias_length", 4)) and a.casefold() not in no_match}


def mention_group(path):
    """Source a mention came from: the folder path minus year folders, at most two levels."""
    parts = [p for p in path.split("/")[:-1] if not re.fullmatch(r"\d{4}", p)]
    return "/".join(parts[:2]) or "(root)"


def note_date(path, fm):
    for k in ("recorded_at", "date", "created", "updated_at"):
        if fm.get(k):
            return str(fm[k])[:10]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path)
    return m.group(1) if m else ""


_entity_mentions = []  # [(entity, sorted mentions)] from the last render_entities


def render_entities(files, cfg):
    items, cfg = collect_entities(files, cfg)
    by_id = {id(e): e for e in items}
    paths = assign_paths(items, lambda e: safe_name(e["name"]), lambda e: f"{e['kind']}:{e['name']}", "")
    for e in items:
        e["path"] = f"{ENTITIES_DIR}/{ENTITY_KINDS[e['kind']]}{paths[e['kind'] + ':' + e['name']]}"

    alias_owner = {}
    for e in items:
        for a in match_aliases(e, cfg):
            alias_owner.setdefault(a.casefold(), set()).add(id(e))
    alias_owner = {a: next(iter(o)) for a, o in alias_owner.items() if len(o) == 1}  # drop ambiguous
    by_first = {}
    for a in alias_owner:
        by_first.setdefault(re.split(r"[\s.]", a)[0], []).append(a)
    # Notes that list participants in frontmatter (calendar attendees, mail participants)
    # mention whoever owns those addresses, even if the name isn't in the text.
    email_owner = {}
    for e in items:
        for x in e["emails"]:
            email_owner.setdefault(x, set()).add(id(e))
    email_owner = {x: next(iter(o)) for x, o in email_owner.items() if len(o) == 1 and x not in SELF_EMAILS}
    skip = tuple(cfg.get("skip_paths") or ()) + (ENTITIES_DIR + "/", ".brain/")
    mentions = {id(e): [] for e in items}
    for path, text in files.items():
        if path.startswith(skip) or not path.endswith(".md"):
            continue
        fm = parse_frontmatter(text)
        body = strip_frontmatter(text)
        low = body.casefold()
        words = set(re.findall(r"[\w&]+", low))
        hits = {}
        for w in words & by_first.keys():
            for a in by_first[w]:
                m = re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", low)
                if m:
                    hits.setdefault(alias_owner[a], m.start())
        for k in PARTICIPANT_FIELDS:
            for v in as_list(fm.get(k)):
                for addr in EMAIL_RE.findall(str(v)):
                    if addr.casefold() in email_owner:
                        hits.setdefault(email_owner[addr.casefold()], -1)
        for eid, pos in hits.items():
            e = by_id[eid]
            if any(r[0] == path for r in e["records"]):
                continue
            title = fm.get("title") or fm.get("name") or path.rsplit("/", 1)[-1][:-3]
            if pos < 0:  # matched by address only
                mentions[eid].append((note_date(path, fm), path, str(title), "participant"))
                continue
            line_start = body.rfind("\n", 0, pos) + 1
            line_end = body.find("\n", pos)
            snippet = re.sub(r"\s+", " ", body[line_start:line_end if line_end >= 0 else None]).strip()
            if len(snippet) > 180:
                off = max(0, pos - line_start - 70)
                snippet = ("…" if off else "") + snippet[off:off + 170].strip() + "…"
            mentions[eid].append((note_date(path, fm), path, str(title), snippet))

    files_out = {}
    global _entity_mentions
    _entity_mentions = []
    for e in items:
        ms = sorted(mentions[id(e)], key=lambda m: (m[0], m[1]), reverse=True)
        _entity_mentions.append((e, ms))
        orgs = [by_id[i] for i in e["orgs"] if i in by_id]
        people = [by_id[i] for i in e.get("people", ()) if i in by_id]
        fm = {"type": e["kind"], "name": e["name"],
              "aliases": sorted(a for a in e["aliases"] if a != e["name"]),
              "emails": sorted(e["emails"]), "domains": sorted(e["domains"]),
              "organization": e["facts"].get("organization"), "title": e["facts"].get("title"),
              "mentions": len(ms), "tags": ["entity", f"entity/{e['kind']}", *sorted(e["tags"])]}
        out = [frontmatter(fm), "<!-- Generated by brain-sync from the records below and every note that mentions "
               "them. Edits here are overwritten; tune matching in .brain/entities.json. -->", "", f"# {e['name']}"]
        role = " · ".join(x for x in (e["facts"].get("title"), e["facts"].get("organization")) if x)
        if role:
            out += ["", f"*{role}*"]
        if orgs or people:
            out += ["", "## Related"]
            out += [f"- Works with/at {link(o['path'], o['name'])}" for o in orgs]
            out += [f"- {link(p['path'], p['name'])}" for p in sorted(people, key=lambda p: p["name"])]
        if e["records"]:
            out += ["", "## Records"] + [f"- {link(p, label)} — `{p.rsplit('/', 1)[0]}`" for p, label in e["records"]]
        if ms:
            out += ["", f"## Mentions ({len(ms)})"]
            groups = {}
            for m in ms[:ENTITY_MAX_MENTIONS]:
                groups.setdefault(mention_group(m[1]), []).append(m)
            for g, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                out += ["", f"### {g} ({len(rows)})"]
                for date, p, t, snip in rows:
                    out.append(f"- {link(p, safe_name(t, 120))}" + (f" · {date}" if date else "") +
                               (f" — {snip}" if snip else ""))
            if len(ms) > ENTITY_MAX_MENTIONS:
                out += ["", f"_…and {len(ms) - ENTITY_MAX_MENTIONS} older mentions._"]
        files_out[e["path"]] = "\n".join(out).rstrip() + "\n"
    files_out[f"{ENTITIES_DIR}/README.md"] = (
        "# Entities\n\n"
        "One page per person, company, business and property, built by `brain-sync` from\n"
        "the records in this repo, listing every note that mentions it. Regenerated every\n"
        "few minutes; don't edit pages here. Tune matching in `.brain/entities.json`\n"
        "(aliases, merges, extra entities, names to ignore).\n"
    )
    log.info("entities: %d entities, %d mentions", len(items), sum(len(v) for v in mentions.values()))
    return files_out


# ------------------------------------------------------------------ Google


# Addresses never used for domain matching: a company entity whose domain is a public mail
# provider would otherwise claim every mailbox there.
PUBLIC_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me", "pm.me",
    "gmx.com", "mail.com", "comcast.net", "att.net", "verizon.net", "fastmail.com", "hey.com",
}
# Automated senders: never enough on their own to pull a thread in or to ask for a reply.
NOREPLY_RE = re.compile(r"^(no[._-]?reply|do[._-]?not[._-]?reply|notifications?|notify|alerts?|mailer-daemon"
                        r"|postmaster|bounces?|newsletters?)(?=[._+@-])", re.I)
EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
# Frontmatter fields whose addresses count as entity mentions (see render_entities).
PARTICIPANT_FIELDS = ("attendees", "participants", "organizer", "from", "to", "cc")
DEFAULT_EXCLUDE_LABELS = ["CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
                          "SPAM", "TRASH"]
CATEGORY_QUERY = {"CATEGORY_PROMOTIONS": "promotions", "CATEGORY_SOCIAL": "social",
                  "CATEGORY_UPDATES": "updates", "CATEGORY_FORUMS": "forums"}
# Bumped when the note layout changes, so every thread re-renders once.
EMAIL_RENDER_VERSION = 1
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

# The mailbox owner's own addresses (account, send-as aliases, config). Kept module-wide so
# entity matching can ignore them too.
SELF_EMAILS = {GOOGLE_ACCOUNT} - {""}


class EntityDirectory:
    """Entity pages by email address and (for companies/businesses) by mail domain."""

    def __init__(self, pages):
        self.emails, self.domains = {}, {}
        for path, text in sorted(pages.items()):
            if path.endswith("/README.md"):
                continue
            fm = parse_frontmatter(text)
            kind = fm.get("type")
            hit = (path[:-3], str(fm.get("name") or path.rsplit("/", 1)[-1][:-3]), kind)
            for e in as_list(fm.get("emails")):
                self.emails.setdefault(str(e).casefold(), hit)
            if kind in ORG_KINDS:
                for d in as_list(fm.get("domains")):
                    d = str(d).casefold().removeprefix("www.")
                    if d and d not in PUBLIC_MAIL_DOMAINS:
                        self.domains.setdefault(d, hit)

    def by_email(self, addr):
        return self.emails.get((addr or "").casefold())

    def org(self, addr):
        dom = (addr or "").rpartition("@")[2].casefold()
        while "." in dom:
            if dom in self.domains:
                return self.domains[dom]
            dom = dom.partition(".")[2]
        return None

    def lookup(self, addr):
        """(path, name, kind) of the person with this address, else of its company, else None."""
        return self.by_email(addr) or self.org(addr)


_entity_pages = None  # {path: text} of the last rendered entity pages


def entity_directory():
    global _entity_pages
    if _entity_pages is None:
        _entity_pages = {p: t for p, t in repo_snapshot().items() if p.startswith(ENTITIES_DIR + "/")}
    return EntityDirectory(_entity_pages)


def load_google_config():
    raw = read_raw(GOOGLE_CONFIG)
    if not raw:
        return {}, ""
    try:
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            raise ValueError("not an object")
    except ValueError as e:
        log.warning("ignoring %s: %s", GOOGLE_CONFIG, e)
        return {}, ""
    return cfg, hashlib.sha1(raw.encode()).hexdigest()[:12]


_google_token = ("", 0.0)


def google_token():
    global _google_token
    if _google_token[0] and time.time() < _google_token[1] - 120:
        return _google_token[0]
    body = urllib.parse.urlencode({"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
                                   "refresh_token": GOOGLE_REFRESH_TOKEN, "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"google token refresh -> {e.code}: {e.read()[:200].decode(errors='replace')}") from None
    _google_token = (data["access_token"], time.time() + int(data.get("expires_in") or 3600))
    return _google_token[0]


def google_get(url, **params):
    q = urllib.parse.urlencode(params, doseq=True)
    return http_json("GET", url + (f"?{q}" if q else ""), google_token(), retries=6)


def google_pages(url, key, **params):
    out, token = [], None
    while True:
        data = google_get(url, **params, **({"pageToken": token} if token else {})) or {}
        out.extend(data.get(key) or [])
        token = data.get("nextPageToken")
        if not token:
            return out


def self_addresses(cfg):
    addrs = set(SELF_EMAILS) | {str(a).casefold() for a in cfg.get("self_addresses") or []}
    prof = google_get(f"{GMAIL_API}/profile") or {}
    if prof.get("emailAddress"):
        addrs.add(prof["emailAddress"].casefold())
    for s in (google_get(f"{GMAIL_API}/settings/sendAs") or {}).get("sendAs") or []:
        if s.get("sendAsEmail"):
            addrs.add(s["sendAsEmail"].casefold())
    SELF_EMAILS.update(addrs)
    return addrs


def addr_label(name, addr):
    name = (name or "").strip().strip('"')
    return f"{name} <{addr}>" if name and name.casefold() != addr.casefold() else addr


def who(name, addr, directory, selfs):
    """Markdown for one participant: "Me", a link to their entity page, or name <address>."""
    if (addr or "").casefold() in selfs:
        return "Me"
    hit = directory.by_email(addr)
    if hit:
        return f"[[{hit[0]}|{hit[1]}]] <{addr}>"
    out = addr_label(name, addr) if addr else (name or "?")
    org = directory.org(addr)
    return f"{out} · [[{org[0]}|{org[1]}]]" if org else out


def entity_paths(addrs, directory, selfs):
    paths = []
    for a in addrs:
        hit = None if a.casefold() in selfs else directory.lookup(a)
        if hit and hit[0] not in paths:
            paths.append(hit[0])
    return paths


def clip_text(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n_[…truncated]_"


# ---------------------------------------------------------------- Calendar


def event_times(ev):
    """(all_day, start, end) in local time; for all-day events dates with an inclusive end."""
    s, e = ev.get("start") or {}, ev.get("end") or {}
    if s.get("date"):
        start = date.fromisoformat(s["date"])
        end = date.fromisoformat(e["date"]) - timedelta(days=1) if e.get("date") else start
        return True, start, max(start, end)
    return False, parse_ts(s.get("dateTime")), parse_ts(e.get("dateTime"))


def event_when(all_day, start, end):
    if all_day:
        if start == end:
            return f"{start.strftime('%a %b %-d, %Y')} · all day"
        return f"{start.strftime('%a %b %-d')} – {end.strftime('%a %b %-d, %Y')} · all day"
    if not start:
        return ""
    head = start.strftime("%a %b %-d, %Y · %-I:%M %p")
    if not end:
        return f"{head} {start.strftime('%Z')}".strip()
    tail = end.strftime("%-I:%M %p") if end.date() == start.date() else end.strftime("%a %b %-d, %-I:%M %p")
    return f"{head} – {tail} {end.strftime('%Z')}".strip()


def keep_event(ev):
    if ev.get("status") == "cancelled" or ev.get("eventType") == "workingLocation":
        return False
    me = next((a for a in ev.get("attendees") or [] if a.get("self")), None)
    if me and me.get("responseStatus") == "declined":
        return False
    # Free/busy placeholders: nothing but a time slot.
    if not (ev.get("summary") or ev.get("description") or ev.get("location") or ev.get("attendees")):
        return False
    return True


def fetch_calendar(cfg):
    skip = {str(x).casefold() for x in cfg.get("exclude_calendars") or []}
    now = datetime.now(LOCAL_TZ)
    tmin = (now - timedelta(days=CALENDAR_PAST_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    tmax = now + timedelta(days=CALENDAR_FUTURE_DAYS)
    cals = google_pages(f"{CALENDAR_API}/users/me/calendarList", "items", maxResults=250)
    by_key = {}
    for c in cals:
        name = c.get("summaryOverride") or c.get("summary") or c["id"]
        if c.get("accessRole") == "freeBusyReader" or {c["id"].casefold(), name.casefold()} & skip:
            continue
        evs = google_pages(f"{CALENDAR_API}/calendars/{urllib.parse.quote(c['id'], safe='')}/events", "items",
                           timeMin=tmin.isoformat(), timeMax=tmax.isoformat(), singleEvents="true",
                           showDeleted="false", maxResults=2500)
        for ev in evs:
            if not keep_event(ev):
                continue
            # The same meeting shows up on every calendar it was invited to: keep one copy.
            key = (ev.get("iCalUID") or ev["id"], json.dumps(ev.get("originalStartTime") or ev.get("start"),
                                                            sort_keys=True))
            if key in by_key:
                if name not in by_key[key]["_calendars"]:
                    by_key[key]["_calendars"].append(name)
                continue
            ev["_calendars"] = [name]
            by_key[key] = ev
    return len(cals), list(by_key.values()), tmin.date()


def render_event(ev, directory, selfs):
    title = (ev.get("summary") or "").strip() or "Busy"
    all_day, start, end = event_times(ev)
    people = [a for a in ev.get("attendees") or [] if not a.get("resource")]
    org = ev.get("organizer") or {}
    me = next((a for a in people if a.get("self")), None)
    addrs = [a.get("email") for a in people if a.get("email")] + ([org["email"]] if org.get("email") else [])
    ents = entity_paths(addrs, directory, selfs)
    video = ev.get("hangoutLink") or next((p.get("uri") for p in (ev.get("conferenceData") or {}).get("entryPoints") or []
                                           if p.get("entryPointType") == "video"), "")
    cal = ev["_calendars"]
    fm = {
        "source": "google-calendar",
        "calendar": cal[0] if len(cal) == 1 else cal,
        "event_id": ev["id"],
        "title": title,
        "date": start.isoformat() if all_day else (start.date().isoformat() if start else ""),
        "start": start.isoformat() if all_day else (start.isoformat(timespec="minutes") if start else ""),
        "end": end.isoformat() if all_day else (end.isoformat(timespec="minutes") if end else ""),
        "all_day": all_day,
        "location": redact(ev.get("location")),
        "organizer": addr_label(org.get("displayName"), org["email"]) if org.get("email") and not org.get("self")
        else ("me" if org.get("self") else None),
        "attendees": [addr_label(a.get("displayName"), a["email"]) for a in people if a.get("email")],
        "status": ev.get("status"),
        "my_response": me.get("responseStatus") if me else None,
        "recurring": True if ev.get("recurringEventId") else None,
        "entities": ents,
        "url": ev.get("htmlLink"),
        "tags": ["calendar", "event"],
    }
    out = [frontmatter(fm), GENERATED.format(src="Google Calendar"), "", f"# {title}", "",
           f"*{event_when(all_day, start, end)}*", ""]
    rows = [("Calendar", ", ".join(cal)), ("Location", redact(ev.get("location"))),
            ("Organizer", who(org.get("displayName"), org.get("email"), directory, selfs) if org.get("email") else ""),
            ("Status", ev.get("status") if ev.get("status") not in (None, "confirmed") else ""),
            ("My response", {"needsAction": "not answered"}.get(me.get("responseStatus"), me.get("responseStatus"))
             if me and not org.get("self") else ""),
            ("Video", redact(video)), ("Recurring", "yes" if ev.get("recurringEventId") else "")]
    out += [f"- **{k}:** {v}" for k, v in rows if v]
    if people:
        out += ["", f"## Attendees ({len(people)})", ""]
        for a in sorted(people, key=lambda a: (not a.get("organizer"), (a.get("displayName") or a.get("email") or "").casefold())):
            status = {"needsAction": "", "accepted": "accepted", "tentative": "tentative",
                      "declined": "declined"}.get(a.get("responseStatus"), "")
            extra = ", ".join(x for x in ("organizer" if a.get("organizer") else "",
                                          "optional" if a.get("optional") else "", status) if x)
            out.append(f"- {who(a.get('displayName'), a.get('email'), directory, selfs)}" + (f" — {extra}" if extra else ""))
    desc = html_to_text(ev.get("description") or "", links=True)
    if desc:
        out += ["", "## Description", "", clip_text(desc, 3 * EMAIL_MAX_CHARS)]
    return "\n".join(out).rstrip() + "\n"


def render_calendar(directory, selfs, cfg):
    ncal, events, start = fetch_calendar(cfg)

    def base(ev):
        all_day, s, _ = event_times(ev)
        d = (s if all_day else (s.date() if s else date.today())).isoformat()
        return f"{d[:4]}/{d} {safe_name((ev.get('summary') or '').strip() or 'Busy')}"

    paths = assign_paths(events, base, lambda ev: ev["id"], CALENDAR_DIR)
    files = {paths[ev["id"]]: render_event(ev, directory, selfs) for ev in events}
    files[f"{CALENDAR_DIR}/README.md"] = (
        "# Calendar\n\n"
        f"One note per event from Google Calendar (every calendar in the account), from\n"
        f"{CALENDAR_PAST_DAYS} days back to {CALENDAR_FUTURE_DAYS} days ahead, filed by year. Declined and cancelled\n"
        "events are left out; notes older than the window are kept as they were.\n"
        "Attendees link to their entity pages. Written by `brain-sync` (whitehouse-rke2\n"
        "`apps/ai/brain`); change the event in Google Calendar, not here.\n"
    )
    log.info("calendar: %d calendars, %d events", ncal, len(events))
    return files, start


# ------------------------------------------------------------------- Gmail


def decode_header(value):
    try:
        return str(email.header.make_header(email.header.decode_header(value or "")))
    except Exception:
        return value or ""


def msg_headers(msg):
    return {h["name"].lower(): decode_header(h.get("value")) for h in (msg.get("payload") or {}).get("headers") or []}


def parse_addrs(*values):
    out = []
    for name, addr in email.utils.getaddresses([v for v in values if v]):
        if "@" in addr:
            out.append((name.strip(), addr.strip().casefold()))
    return out


def live_messages(thread):
    msgs = [m for m in thread.get("messages") or [] if "DRAFT" not in (m.get("labelIds") or [])]
    return sorted(msgs, key=lambda m: int(m.get("internalDate") or 0))


def thread_meta(thread):
    """Participants, per-message labels, subject and first date: all matching needs."""
    msgs = live_messages(thread)
    people, labels = {}, []
    for m in msgs:
        h = msg_headers(m)
        for name, addr in parse_addrs(h.get("from"), h.get("to"), h.get("cc")):
            if not people.get(addr):
                people[addr] = name
        labels.append(m.get("labelIds") or [])
    subject = msg_headers(msgs[0]).get("subject", "") if msgs else ""
    return {"hid": thread.get("historyId"), "people": people, "labels": labels, "subject": subject,
            "first": int(msgs[0].get("internalDate") or 0) if msgs else 0}


class MailRules:
    """Which threads get a note: another participant is a known entity (by address, or by
    company domain) or is listed in include_*; nothing in exclude_*; not only excluded labels."""

    def __init__(self, cfg, directory, selfs, label_ids):
        low = lambda k: {str(x).casefold().lstrip("@") for x in cfg.get(k) or []}
        self.inc_addr, self.inc_dom = low("include_addresses"), low("include_domains")
        self.exc_addr, self.exc_dom = low("exclude_addresses"), low("exclude_domains")
        names = cfg.get("exclude_labels", DEFAULT_EXCLUDE_LABELS)
        self.exc_labels = {label_ids.get(str(n).casefold(), str(n)) for n in names}
        self.directory, self.selfs = directory, selfs

    @staticmethod
    def in_domains(addr, domains):
        dom = addr.rpartition("@")[2]
        return any(dom == d or dom.endswith("." + d) for d in domains)

    def match(self, people, labels=()):
        others = [a for a in people if a not in self.selfs]
        if not others:
            return False
        if any(a in self.exc_addr or self.in_domains(a, self.exc_dom) for a in people):
            return False
        if labels and all(set(l) & self.exc_labels for l in labels):
            return False
        for a in others:
            if a in self.inc_addr or self.in_domains(a, self.inc_dom):
                return True
            if not NOREPLY_RE.match(a) and self.directory.lookup(a):
                return True
        return False

    def query_excludes(self):
        q = [f"-category:{CATEGORY_QUERY[l]}" for l in sorted(self.exc_labels) if l in CATEGORY_QUERY]
        return " ".join(["-in:chats", *q])


def part_text(part):
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    ctype = next((h.get("value") or "" for h in part.get("headers") or [] if h.get("name", "").lower() == "content-type"), "")
    m = re.search(r'charset="?([\w.:-]+)', ctype, re.I)
    try:
        return raw.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def message_body(payload):
    """(plain text, attachment names): text/plain preferred, else HTML converted."""
    plain, htmls, files = [], [], []

    def walk(p):
        mt = (p.get("mimeType") or "").lower()
        hdrs = {h.get("name", "").lower(): h.get("value", "") for h in p.get("headers") or []}
        if p.get("filename"):
            inline = mt.startswith("image/") and "content-id" in hdrs and "attachment" not in hdrs.get(
                "content-disposition", "").lower()
            if not inline:
                files.append(p["filename"])
            return
        if p.get("parts"):
            for c in p["parts"]:
                walk(c)
        elif mt == "text/plain":
            plain.append(part_text(p))
        elif mt == "text/html":
            htmls.append(part_text(p))

    walk(payload or {})
    if any(t.strip() for t in plain):
        text = "\n\n".join(plain)
    else:
        # Gmail wraps the quoted conversation in a gmail_quote block: drop it with the rest.
        text = "\n\n".join(re.sub(r"(?is)<div[^>]*class=\"?[^\">]*gmail_quote.*$|<blockquote.*?</blockquote>", "", h)
                           for h in htmls)
        text = html_to_text(text, secrets=False, links=False)
    return text, files


QUOTE_HEAD_RE = re.compile(r"^On\b.{4,300}\bwrote:\s*$", re.S)
OUTLOOK_SEP_RE = re.compile(r"^(-{2,}\s*Original Message\s*-{2,}|_{8,})\s*$", re.I)
SENT_FROM_RE = re.compile(r"^(Sent from my|Get Outlook for)\b", re.I)


def clean_mail(text):
    """Strip quoted replies, obvious signatures, then redact and cap."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">"):
            continue
        two = (s + " " + lines[i + 1].strip()) if i + 1 < len(lines) else s
        if s.startswith("On ") and (QUOTE_HEAD_RE.match(s) or QUOTE_HEAD_RE.match(two)):
            break
        if OUTLOOK_SEP_RE.match(s):
            break
        if s.startswith("From:") and any(l.strip().startswith(("Sent:", "Date:")) for l in lines[i + 1:i + 4]) \
                and not any("Forwarded message" in l for l in lines[max(0, i - 3):i]):
            break
        if line.rstrip() in ("--", "-- "):
            break
        if SENT_FROM_RE.match(s):
            continue
        out.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return clip_text(redact(text, otp=True), EMAIL_MAX_CHARS)


def clean_subject(subject):
    return re.sub(r"^(\s*(re|fwd?|aw|wg)\s*(\[\d+\])?\s*:\s*)+", "", subject or "", flags=re.I).strip()


def mail_url(tid):
    acct = f"?authuser={urllib.parse.quote(GOOGLE_ACCOUNT)}" if GOOGLE_ACCOUNT else ""
    return f"https://mail.google.com/mail/u/{acct}#all/{tid}"


def fmt_ms(ms, pattern="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(int(ms) / 1000, LOCAL_TZ).strftime(pattern) if ms else ""


def iso_ms(ms):
    return datetime.fromtimestamp(int(ms) / 1000, LOCAL_TZ).isoformat(timespec="minutes") if ms else ""


HIDDEN_LABELS = {"UNREAD", "CATEGORY_PERSONAL", "DRAFT", "CHAT"}


def render_thread(thread, directory, selfs, label_names):
    msgs = live_messages(thread)
    meta = thread_meta(thread)
    subject = meta["subject"].strip() or "(no subject)"
    first, last = msgs[0], msgs[-1]
    last_from = (parse_addrs(msg_headers(last).get("from")) or [("", "")])[0]
    last_self = last_from[1] in selfs
    last_hit = None if last_self or NOREPLY_RE.match(last_from[1]) else directory.lookup(last_from[1])
    labels = sorted({label_names.get(l, l) for m in msgs for l in m.get("labelIds") or []} - HIDDEN_LABELS
                    - {k for k, v in label_names.items() if v in HIDDEN_LABELS})
    fm = {
        "source": "gmail",
        "thread_id": thread["id"],
        "subject": subject,
        "title": subject,
        "date": iso_ms(last.get("internalDate"))[:10],
        "first_date": iso_ms(first.get("internalDate")),
        "last_date": iso_ms(last.get("internalDate")),
        "messages": len(msgs),
        "participants": [addr_label(n, a) for a, n in meta["people"].items()],
        "last_from": addr_label(*last_from) if last_from[1] else None,
        "last_from_self": last_self,
        "last_from_entity": last_hit[0] if last_hit else None,
        "entities": entity_paths(list(meta["people"]), directory, selfs),
        "labels": labels,
        "url": mail_url(thread["id"]),
        "tags": ["email", "gmail"],
    }
    span = fmt_ms(first.get("internalDate"), "%b %-d, %Y")
    end = fmt_ms(last.get("internalDate"), "%b %-d, %Y")
    out = [frontmatter(fm), GENERATED.format(src="Gmail"), "", f"# {subject}", "",
           f"*{len(msgs)} message{'s' if len(msgs) != 1 else ''} · {span}{' – ' + end if end != span else ''}*", "",
           "**Participants:** " + ", ".join(who(n, a, directory, selfs) for a, n in meta["people"].items()),
           "", "## Messages"]
    for m in msgs:
        h = msg_headers(m)
        sender = (parse_addrs(h.get("from")) or [("", h.get("from", "?"))])[0]
        name = "Me" if sender[1] in selfs else (sender[0] or sender[1])
        out += ["", f"### {fmt_ms(m.get('internalDate'))} — {safe_name(name, 80)}", ""]
        heads = [("From", [sender]), ("To", parse_addrs(h.get("to"))), ("Cc", parse_addrs(h.get("cc")))]
        out += [f"**{k}:** " + ", ".join(who(n, a, directory, selfs) for n, a in v) + "  " for k, v in heads if v]
        body, attachments = message_body(m.get("payload"))
        body = clean_mail(body)
        if body:
            out += ["", body]
        if attachments:
            out += ["", "**Attachments:** " + ", ".join(f"`{safe_name(f, 120)}`" for f in attachments)]
    return "\n".join(out).rstrip() + "\n"


def thread_base(meta):
    day = iso_ms(meta.get("first"))[:10] or "undated"
    return f"{day[:4]}/{day} {safe_name(clean_subject(meta.get('subject')) or '(no subject)')}"


def link_sig(people, directory, selfs):
    hits = [(a, (directory.lookup(a) or ("",))[0]) for a in sorted(people) if a not in selfs]
    return hashlib.sha1(json.dumps(hits).encode()).hexdigest()[:12]


_gmail_meta = {}  # thread id -> thread_meta(); memory only, so unmatched mail never lands in the repo


def render_gmail(index_text, existing, directory, selfs, cfg):
    """Returns (files, keep). Unchanged threads keep their current blob."""
    try:
        index = (json.loads(index_text or "{}") or {}).get("threads") or {}
    except ValueError:
        index = {}
    labels = google_get(f"{GMAIL_API}/labels") or {}
    label_names = {l["id"]: l.get("name") or l["id"] for l in labels.get("labels") or []}
    label_ids = {v.casefold(): k for k, v in label_names.items()}
    rules = MailRules(cfg, directory, selfs, label_ids)
    lookback = int(cfg.get("lookback_days") or 180)
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=lookback)
    query = f"after:{int(cutoff.timestamp())} {rules.query_excludes()}"
    listed = google_pages(f"{GMAIL_API}/threads", "threads", q=query, maxResults=500)  # newest first
    # Metadata checks get two thirds of the budget so a backfill still renders every cycle.
    budget, checked, fetched, deferred = GMAIL_MAX_FETCH * 2 // 3, 0, 0, 0

    # 1. Which threads match: index entries whose history didn't move keep their stored
    #    participants; anything new or changed gets a metadata look (cached in memory).
    matched = {}  # tid -> meta
    listed_ids = set()
    for t in listed:
        tid, hid = t["id"], t.get("historyId")
        listed_ids.add(tid)
        prev, meta = index.get(tid), _gmail_meta.get(tid)
        if prev and prev.get("hid") == hid and not (meta and meta["hid"] == hid):
            meta = {"hid": hid, "people": prev.get("people") or {}, "labels": [], "subject": prev.get("subject", ""),
                    "first": prev.get("first") or 0}
        if not meta or meta["hid"] != hid:
            if budget <= 0:
                deferred += 1
                if prev:  # keep the old note until there is budget to look again
                    matched[tid] = {"hid": prev.get("hid"), "people": prev.get("people") or {}, "labels": [],
                                    "subject": prev.get("subject", ""), "first": prev.get("first") or 0, "stale": True}
                continue
            data = google_get(f"{GMAIL_API}/threads/{tid}", format="metadata",
                              metadataHeaders=["From", "To", "Cc", "Subject", "Date"])
            budget -= 1
            checked += 1
            if not data:
                continue
            meta = _gmail_meta[tid] = thread_meta(data)
        if rules.match(meta["people"], meta["labels"]):
            matched[tid] = meta
    # Notes for threads that fell out of the lookback window stay as long as they still match.
    for tid, prev in index.items():
        if tid not in listed_ids and (prev.get("first") or 0) / 1000 < cutoff.timestamp() \
                and rules.match(prev.get("people") or {}):
            matched[tid] = {"hid": prev.get("hid"), "people": prev.get("people") or {}, "labels": [],
                            "subject": prev.get("subject", ""), "first": prev.get("first") or 0, "stale": True}

    # 2. Render what is new or changed, newest first, within what is left of the budget.
    paths = assign_paths(list(matched), lambda tid: thread_base(matched[tid]), lambda tid: tid, EMAIL_DIR)
    files, keep, new_index = {}, set(), {}
    budget = GMAIL_MAX_FETCH - checked
    order = sorted(matched, key=lambda tid: matched[tid].get("first") or 0, reverse=True)
    for tid in order:
        meta, prev, path = matched[tid], index.get(tid) or {}, paths[tid]
        sig = link_sig(meta["people"], directory, selfs)
        entry = {"path": path, "hid": meta["hid"], "people": meta["people"], "subject": meta["subject"],
                 "first": meta["first"], "links": sig, "v": EMAIL_RENDER_VERSION}
        same = (prev.get("hid") == meta["hid"] and prev.get("path") == path and prev.get("links") == sig
                and prev.get("v") == EMAIL_RENDER_VERSION and path in existing)
        if same or (meta.get("stale") and prev.get("path") == path and path in existing):
            keep.add(path)
            new_index[tid] = prev if meta.get("stale") else entry
            continue
        if budget <= 0:
            deferred += 1
            if prev.get("path") in existing:
                keep.add(prev["path"])
                new_index[tid] = prev
            continue
        data = google_get(f"{GMAIL_API}/threads/{tid}", format="full")
        budget -= 1
        if not data or not live_messages(data):
            continue
        full_meta = thread_meta(data)
        _gmail_meta[tid] = dict(full_meta)
        entry.update(hid=full_meta["hid"], people=full_meta["people"], subject=full_meta["subject"],
                     first=full_meta["first"], links=link_sig(full_meta["people"], directory, selfs))
        files[path] = render_thread(data, directory, selfs, label_names)
        new_index[tid] = entry
        fetched += 1
    # A deferred thread may keep an old path that a newer thread now owns: drop the old one.
    for tid, entry in list(new_index.items()):
        if entry.get("path") in files and paths.get(tid) != entry.get("path"):
            new_index.pop(tid)
    files[EMAIL_INDEX] = json.dumps({"threads": new_index}, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    files[f"{EMAIL_DIR}/README.md"] = (
        "# Email\n\n"
        "Gmail threads with people and companies that have an entity page (matched by\n"
        "address or company domain), plus whatever `.brain/google.json` includes; one note per\n"
        "thread, filed by the date of its first message. Quoted replies and signatures are\n"
        "stripped, passwords and one-time codes redacted, long messages cut, attachments listed\n"
        "by name only. Written by `brain-sync` (whitehouse-rke2 `apps/ai/brain`), read-only.\n\n"
        "`.brain/google.json` (all optional): `include_domains`, `include_addresses`,\n"
        "`exclude_domains`, `exclude_addresses` (a thread involving any of these is left out),\n"
        "`exclude_labels` (default: promotions, social, updates, forums, spam, trash; a thread\n"
        "is left out when every message carries one), `lookback_days` (180),\n"
        "`exclude_calendars`, `self_addresses`.\n"
    )
    log.info("gmail: %d threads listed, %d matched, %d checked, %d rendered, %d deferred",
             len(listed), len(matched), checked, fetched, deferred)
    return files, keep


_google_last = 0.0


def sync_google():
    global _google_last
    if time.time() - _google_last < GOOGLE_INTERVAL:
        return
    cfg, _ = load_google_config()
    directory = entity_directory()
    selfs = self_addresses(cfg)
    failed = []

    try:
        existing = list_tree(CALENDAR_DIR)
        files, start = render_calendar(directory, selfs, cfg)
        # Notes from before the window are history: keep them as they are.
        older = {p for p in existing if p not in files
                 and (m := re.search(r"/(\d{4}-\d{2}-\d{2}) ", p)) and m.group(1) < start.isoformat()}
        apply_changes(CALENDAR_DIR, files, existing, keep=older, label="calendar")
    except Exception:
        log.exception("calendar sync failed")
        failed.append("calendar")
    try:
        existing = list_tree(EMAIL_DIR)
        index_text = read_raw(EMAIL_INDEX) if EMAIL_INDEX in existing else None
        files, keep = render_gmail(index_text, existing, directory, selfs, cfg)
        apply_changes(EMAIL_DIR, files, existing, keep=keep, label="gmail")
    except Exception:
        log.exception("gmail sync failed")
        failed.append("gmail")
    _google_last = time.time()
    if failed:
        raise RuntimeError(f"google: {', '.join(failed)} failed")


# --------------------------------------------------------------- repo sync


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
    """{relative path: blob_sha} for every file below a git tree."""
    out, page = {}, 1
    while True:
        data = forgejo("GET", f"/repos/{REPO}/git/trees/{tree_sha}?recursive=true&per_page=1000&page={page}")
        for e in data.get("tree") or []:
            if e["type"] == "blob":
                out[e["path"]] = e["sha"]
        if not data.get("truncated") or not data.get("tree"):
            return out
        page += 1


def read_raw(path):
    data = read_bytes(path)
    return data.decode() if data is not None else None


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


def apply_changes(prefix, desired, existing, keep=frozenset(), label=""):
    ops, size = [], 0
    for path, text in sorted(desired.items()):
        content = text.encode()
        if existing.get(path) == blob_sha(content):
            continue
        op = {"operation": "update" if path in existing else "create", "path": path,
              "content": base64.b64encode(content).decode()}
        if path in existing:
            op["sha"] = existing[path]
        ops.append(op)
    for path, sha in sorted(existing.items()):
        if path not in desired and path not in keep:
            ops.append({"operation": "delete", "path": path, "sha": sha})
    if not ops:
        return 0
    counts = {k: sum(1 for o in ops if o["operation"] == k) for k in ("create", "update", "delete")}
    summary = ", ".join(f"{v} {k}d" for k, v in counts.items() if v)
    if DRY_RUN:
        for o in ops:
            log.info("[dry-run] %s %s", o["operation"], o["path"])
        return len(ops)
    batch, batches = [], []
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
            "branch": BRANCH,
            "message": f"{label}: {summary}{part}",
            "author": AUTHOR,
            "committer": AUTHOR,
            "files": b,
        })
        STATE["commits"] += 1
    STATE["files_written"] += len(ops)
    log.info("%s: committed %s", label, summary)
    return len(ops)


_meerkat_contacts = []  # last fetch, with child lists; write-back updates these in place


def sync_meerkat():
    global _meerkat_contacts
    _meerkat_contacts = fetch_meerkat()
    files = render_meerkat(_meerkat_contacts)
    apply_changes(MEERKAT_DIR, files, list_tree(MEERKAT_DIR), label="meerkat")


_last_full = 0.0


def sync_riffado():
    global _last_full
    existing = list_tree(RIFFADO_DIR)
    full = time.time() - _last_full > FULL_RESYNC_SECONDS
    current = {p: None for p in existing}
    if RIFFADO_INDEX in existing:
        current[RIFFADO_INDEX] = read_raw(RIFFADO_INDEX)
    files, keep = render_riffado(current, full)
    apply_changes(RIFFADO_DIR, files, existing, keep=keep, label="riffado")
    if full:
        _last_full = time.time()


# ------------------------------------------------------------------- Pages


def mcp_call(tool, arguments):
    """Call one tool on the pages MCP server (streamable HTTP, stdlib only)."""

    def post(body, session=None):
        req = urllib.request.Request(PAGES_MCP_URL, data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {PAGES_MCP_TOKEN}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        # The pages server only exposes /mcp on its control-plane host.
        req.add_header("Host", PAGES_MCP_HOST)
        if session:
            req.add_header("Mcp-Session-Id", session)
            req.add_header("MCP-Protocol-Version", "2025-06-18")
        with urllib.request.urlopen(req, timeout=300) as resp:
            sid = resp.headers.get("mcp-session-id") or session
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode()
        if not raw.strip():
            return sid, None
        if "text/event-stream" in ctype:
            msgs = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and line[5:].strip()]
            return sid, next((m for m in reversed(msgs) if "id" in m), None)
        return sid, json.loads(raw)

    sid, init = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "brain-sync", "version": "1"}}})
    if not init or "error" in init:
        raise RuntimeError(f"pages MCP initialize failed: {init}")
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}, sid)
    if not res or "error" in res:
        raise RuntimeError(f"pages MCP {tool} failed: {res and res.get('error')}")
    result = res.get("result") or {}
    text = " ".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(f"pages MCP {tool}: {text[:500]}")
    return result.get("structuredContent") or text


_pages_deployed = {}  # site -> (tree sha, deployed at)


def sync_pages():
    root = forgejo("GET", f"/repos/{REPO}/git/trees/{BRANCH}")
    entry = next((e for e in (root or {}).get("tree") or [] if e["path"] == PAGES_DIR and e["type"] == "tree"), None)
    if not entry:
        return
    sites = forgejo("GET", f"/repos/{REPO}/git/trees/{entry['sha']}") or {}
    for e in sites.get("tree") or []:
        if e["type"] != "tree":
            continue
        name = e["path"]
        if not SITE_NAME_RE.match(name):
            log.warning("pages: skipping %s/%s: not a valid site name", PAGES_DIR, name)
            continue
        prev = _pages_deployed.get(name)
        if prev and prev[0] == e["sha"] and time.time() - prev[1] < PAGES_REDEPLOY_SECONDS:
            continue
        files = []
        for rel in sorted(tree_blobs(e["sha"])):
            data = read_bytes(f"{PAGES_DIR}/{name}/{rel}")
            if data is not None:
                files.append({"path": rel, "content": base64.b64encode(data).decode(), "encoding": "base64"})
        if not files:
            continue
        reason = "changed" if prev and prev[0] != e["sha"] else ("startup" if not prev else "periodic")
        if DRY_RUN:
            log.info("[dry-run] pages: would deploy %s (%d files, %s)", name, len(files), reason)
        else:
            mcp_call("deploy_site", {"name": name, "files": files, "replace": True})
            log.info("pages: deployed %s (%d files, %s)", name, len(files), reason)
        _pages_deployed[name] = (e["sha"], time.time())


_hubspot_last = 0.0


def sync_hubspot():
    global _hubspot_last
    if time.time() - _hubspot_last < HUBSPOT_INTERVAL:
        return
    apply_changes(HUBSPOT_DIR, render_hubspot(), list_tree(HUBSPOT_DIR), label="hubspot")
    _hubspot_last = time.time()


_entities_last = (0.0, "")


# ------------------------------------------------------- Meerkat write-back


def brain_url(path):
    if not BRAIN_UI:
        return ""
    return f"{BRAIN_UI}/{REPO}/src/branch/{BRANCH}/" + urllib.parse.quote(path)


def section_text(text, heading, limit):
    """Plain text of a '## heading' section of a note, clipped."""
    body = strip_frontmatter(text)
    m = re.search(r"(?m)^## " + re.escape(heading) + r"\s*$", body)
    if not m:
        return ""
    rest = body[m.end():]
    end = re.search(r"(?m)^## ", rest)
    return clip_text(re.sub(r"\n{3,}", "\n\n", rest[:end.start() if end else None]).strip(), limit)


def writeback_plan(files, cfg):
    """(summary note per Meerkat contact id, activity per meeting/recording) from the entity mentions."""
    today = datetime.now(LOCAL_TZ).date().isoformat()
    # People who'd get an activity for nearly everything (e.g. the owner): summary note only.
    no_acts = {x.casefold() for x in cfg.get("meerkat_no_activities") or []}
    notes, acts = {}, {}
    for e, ms in _entity_mentions:
        if e["kind"] != "person":
            continue
        ids = [parse_frontmatter(files.get(p, "")).get("meerkat_id") for p, label in e["records"] if label == "Meerkat"]
        ids = [i for i in ids if isinstance(i, int)]
        if not ids:
            continue
        top = lambda p: p.split("/", 1)[0]
        counts, last = {}, {}
        for date, p, _, _ in ms:
            counts[top(p)] = counts.get(top(p), 0) + 1
            if date and date <= today:
                last[top(p)] = max(last.get(top(p), ""), date)
        out = [WRITEBACK_MARK + "Brain summary. Updated automatically by brain-sync; edits here are overwritten.", ""]
        if brain_url(e["path"]):
            out.append(f"Brain page: {brain_url(e['path'])}")
        for p, label in e["records"]:
            if label != "Meerkat":
                url = parse_frontmatter(files.get(p, "")).get("url") or brain_url(p)
                out.append(f"{label}: {url}" if url else f"{label}: {p}")
        if ms:
            out += ["", f"Mentioned in {len(ms)} notes: " +
                    ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))]
            if last:
                out.append("Last: " + ", ".join(f"{k} {v}" for k, v in sorted(last.items(), key=lambda kv: kv[1], reverse=True)))
            upcoming = sorted((m for m in ms if m[0] > today), key=lambda m: m[0])[:5]
            if upcoming:
                out += ["", "Upcoming:"] + [f"- {d} · {top(p)} · {t}" for d, p, t, _ in upcoming]
            recent = [m for m in ms if m[0] <= today][:MEERKAT_RECENT]
            if recent:
                out += ["", "Recent:"] + [f"- {d or 'undated'} · {top(p)} · {t}" for d, p, t, _ in recent]
        else:
            out += ["", "No notes mention them yet."]
        text = "\n".join(out)
        if len(text) > 4900:
            text = text[:4900].rsplit("\n", 1)[0] + "\n…"
        for i in ids:
            notes[i] = text
        if no_acts & {a.casefold() for a in e["aliases"]}:
            continue
        for date, p, title, _ in ms:
            if not date or date > today or top(p) not in MEERKAT_ACTIVITY_DIRS:
                continue
            fm = parse_frontmatter(files.get(p, ""))
            ident = str(fm.get("event_id") or fm.get("riffado_id") or p)
            key = hashlib.sha1(ident.encode()).hexdigest()[:12]
            if key not in acts:
                when = parse_ts(str(fm.get("start") or fm.get("recorded_at") or date))
                if when is None:
                    when = datetime.fromisoformat(date).replace(tzinfo=LOCAL_TZ)
                if when > datetime.now(LOCAL_TZ):  # later today: not an interaction yet
                    continue
                recording = bool(fm.get("riffado_id"))
                desc = [section_text(files.get(p, ""), "Summary", 1200)] if recording else []
                desc += [x for x in (fm.get("url"), brain_url(p)) if x]
                desc.append(f"[brain:{key}]")
                acts[key] = {"title": (("Recording: " if recording else "") + str(title))[:200],
                             "location": str(fm.get("location") or "")[:300],
                             "date": when.isoformat(timespec="seconds"),
                             "description": "\n\n".join(d for d in desc if d)[-2000:], "cids": set()}
            acts[key]["cids"].update(ids)
    return notes, acts


def meerkat_write(method, path, body):
    if DRY_RUN:
        log.info("[dry-run] meerkat %s %s", method, path)
        return {}
    r = http_json(method, f"{MEERKAT_URL}/api/v1{path}", MEERKAT_TOKEN, body)
    time.sleep(MEERKAT_REQUEST_DELAY)
    return r or {}


def writeback_meerkat(notes, acts):
    """Upsert summary notes and create/extend activities, at most MEERKAT_WRITEBACK_MAX writes."""
    contacts = {c["ID"]: c for c in _meerkat_contacts}
    budget, done = MEERKAT_WRITEBACK_MAX, {"notes": 0, "activities": 0}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cid, text in sorted(notes.items()):
        c = contacts.get(cid)
        if c is None:
            continue
        mine = [n for n in c.get("_notes") or [] if is_writeback_note(n)]
        if mine and mine[0].get("content") == text:
            continue
        if budget <= 0:
            break
        body = {"content": text, "date": now, "contact_id": cid}
        if mine:
            meerkat_write("PUT", f"/notes/{mine[0]['ID']}", body)
            mine[0]["content"] = text
        else:
            r = meerkat_write("POST", f"/contacts/{cid}/notes", body)
            c.setdefault("_notes", []).append((r.get("note") or r) if r else {"content": text})
        budget -= 1
        done["notes"] += 1
    existing = {}
    for c in contacts.values():
        for a in c.get("_activities") or []:
            k = writeback_key(a)
            if k:
                existing.setdefault(k, {"a": a, "cids": set()})["cids"].add(c["ID"])
    for key, act in sorted(acts.items(), key=lambda kv: kv[1]["date"], reverse=True):
        cids = {i for i in act["cids"] if i in contacts}
        ex = existing.get(key)
        if not cids or (ex and cids <= ex["cids"]):
            continue
        if budget <= 0:
            break
        body = {k: act[k] for k in ("title", "description", "location", "date")}
        body["contact_ids"] = sorted(cids | (ex["cids"] if ex else set()))
        if ex:
            meerkat_write("PUT", f"/activities/{ex['a']['ID']}", body)
            a = ex["a"]
        else:
            r = meerkat_write("POST", "/activities", body)
            a = (r.get("activity") or r) if r else dict(body)
            existing[key] = {"a": a, "cids": set()}
        for i in cids - existing[key]["cids"]:
            contacts[i].setdefault("_activities", []).append(a)
        existing[key]["cids"] |= cids
        budget -= 1
        done["activities"] += 1
    if done["notes"] or done["activities"]:
        log.info("meerkat write-back: %d summary notes, %d activities", done["notes"], done["activities"])


def sync_entities():
    global _entities_last, _entity_pages
    root = forgejo("GET", f"/repos/{REPO}/branches/{BRANCH}") or {}
    head = ((root.get("commit") or {}).get("id")) or ""
    if head == _entities_last[1] or time.time() - _entities_last[0] < ENTITY_INTERVAL:
        return
    files = repo_snapshot()
    try:
        cfg = json.loads(files.get(ENTITY_CONFIG) or "{}")
    except ValueError as e:
        log.warning("ignoring %s: %s", ENTITY_CONFIG, e)
        cfg = {}
    rendered = render_entities(files, cfg)
    apply_changes(ENTITIES_DIR, rendered, list_tree(ENTITIES_DIR), label="entities")
    _entity_pages = rendered
    if MEERKAT_WRITEBACK and MEERKAT_TOKEN and _meerkat_contacts:
        try:
            writeback_meerkat(*writeback_plan(files, cfg))
        except Exception:
            log.exception("meerkat write-back failed")
    # Our own commit moves the head; remember that one so it doesn't re-trigger.
    after = ((forgejo("GET", f"/repos/{REPO}/branches/{BRANCH}") or {}).get("commit") or {}).get("id") or head
    _entities_last = (time.time(), after)


def cycle():
    for name, fn, enabled in (("meerkat", sync_meerkat, MEERKAT_TOKEN), ("riffado", sync_riffado, RIFFADO_TOKEN),
                              ("hubspot", sync_hubspot, HUBSPOT_TOKEN), ("pages", sync_pages, PAGES_MCP_TOKEN),
                              ("entities", sync_entities, True),
                              ("google", sync_google, GOOGLE_REFRESH_TOKEN and GOOGLE_CLIENT_ID)):
        if not enabled:
            continue
        try:
            fn()
            STATE["last_success"][name] = time.time()
        except Exception:
            STATE["last_error"][name] = time.time()
            log.exception("%s sync failed", name)


# ------------------------------------------------------------- HTTP server


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/plain"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, "ok\n")
        elif self.path == "/metrics":
            lines = [
                "# HELP brain_sync_last_success_timestamp_seconds Last successful sync per source.",
                "# TYPE brain_sync_last_success_timestamp_seconds gauge",
            ]
            for k, v in STATE["last_success"].items():
                lines.append(f'brain_sync_last_success_timestamp_seconds{{source="{k}"}} {v}')
            lines += ["# TYPE brain_sync_last_error_timestamp_seconds gauge"]
            for k, v in STATE["last_error"].items():
                lines.append(f'brain_sync_last_error_timestamp_seconds{{source="{k}"}} {v}')
            lines += [
                "# TYPE brain_sync_files_written_total counter",
                f"brain_sync_files_written_total {STATE['files_written']}",
                "# TYPE brain_sync_commits_total counter",
                f"brain_sync_commits_total {STATE['commits']}",
            ]
            self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        else:
            self._send(404, "not found\n")

    def do_POST(self):
        # Any POST to /trigger (e.g. a Riffado or Forgejo webhook) runs a cycle now.
        # It only wakes the loop; the sync itself always re-reads both sources.
        if self.path.startswith("/trigger"):
            _wake.set()
            self._send(202, "queued\n")
        else:
            self._send(404, "not found\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if RUN_ONCE:
        cycle()
        return
    threading.Thread(target=ThreadingHTTPServer(("", PORT), Handler).serve_forever, daemon=True).start()
    log.info("brain-sync up: repo=%s branch=%s every %ss", REPO, BRANCH, INTERVAL)
    while True:
        cycle()
        _wake.wait(INTERVAL)
        _wake.clear()


if __name__ == "__main__":
    main()
