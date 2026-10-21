#!/usr/bin/env python3
"""brain-sync: mirror Meerkat, Riffado and HubSpot into the Forgejo brain repo as Markdown.

Every cycle it renders the full set of files it owns (everything under
Meerkat/, Riffado/ and Wallace and White/HubSpot/), compares git blob hashes
against the repo, and pushes only the differences as one commit through
Forgejo's change-files API. Files outside those folders are never touched.

Standard library only, so it runs on the stock python image.
"""

import base64
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
# HubSpot (Wallace & White portal), read with a service key. A full pull is ~25
# requests, so it simply re-renders everything every HUBSPOT_INTERVAL_SECONDS.
HUBSPOT_URL = os.environ.get("HUBSPOT_URL", "https://api.hubapi.com").rstrip("/")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_INTERVAL = int(os.environ.get("HUBSPOT_INTERVAL_SECONDS", "600"))
MAX_COMMIT_BYTES = int(os.environ.get("MAX_COMMIT_BYTES", str(4 * 1024 * 1024)))
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
RUN_ONCE = os.environ.get("RUN_ONCE", "") == "1"
PORT = int(os.environ.get("PORT", "8080"))
AUTHOR = {"name": "brain-sync", "email": "brain-sync@whitematter.tech"}

MEERKAT_DIR = "Meerkat"
RIFFADO_DIR = "Riffado"
RIFFADO_INDEX = f"{RIFFADO_DIR}/.index.json"
PAGES_DIR = "Pages"
HUBSPOT_DIR = "Wallace and White/HubSpot"
# Repo-owned routing rules: recordings matching a rule link to that rule's hub
# note (e.g. a business folder) and get its tags. Edit the file in the repo.
ROUTES_FILE = ".brain/routes.json"
# Repo-owned fixes for words transcription keeps getting wrong: {"wrong": "right"}.
CORRECTIONS_FILE = ".brain/corrections.json"
SITE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

log = logging.getLogger("brain-sync")

STATE = {
    "last_success": {"meerkat": 0.0, "riffado": 0.0, "pages": 0.0, "hubspot": 0.0},
    "last_error": {"meerkat": 0.0, "riffado": 0.0, "pages": 0.0, "hubspot": 0.0},
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
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 2)
                log.warning("%s %s -> %s, retrying in %ss", method, url, e.code, wait)
                time.sleep(wait)
                continue
            detail = e.read()[:500].decode(errors="replace")
            raise RuntimeError(f"{method} {url} -> {e.code}: {detail}") from None
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


def fetch_meerkat():
    contacts, page = [], 1
    while True:
        data = meerkat_get(f"/contacts?limit=100&page={page}") or {}
        batch = data.get("contacts") or []
        contacts.extend(batch)
        if not batch or len(contacts) >= int(data.get("total") or 0):
            break
        page += 1
    for c in contacts:
        cid = c["ID"]
        c["_notes"] = (meerkat_get(f"/contacts/{cid}/notes") or {}).get("notes") or []
        c["_activities"] = (meerkat_get(f"/contacts/{cid}/activities") or {}).get("activities") or []
        c["_relationships"] = (meerkat_get(f"/contacts/{cid}/relationships") or {}).get("relationships") or []
        c["_incoming"] = (meerkat_get(f"/contacts/{cid}/incoming-relationships") or {}).get(
            "incoming_relationships"
        ) or []
        c["_reminders"] = (meerkat_get(f"/contacts/{cid}/reminders") or {}).get("reminders") or []
    return contacts


def assign_paths(items, base_of, key_of, folder):
    """Stable, collision-free paths: the plain name wins for the oldest key."""
    by_base = {}
    for it in items:
        by_base.setdefault(base_of(it), []).append(it)
    paths = {}
    for base, group in by_base.items():
        group.sort(key=lambda it: str(key_of(it)))
        for i, it in enumerate(group):
            name = base if i == 0 else f"{base} ({str(key_of(it))[:8]})"
            paths[key_of(it)] = f"{folder}/{name}.md"
    return paths


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

    notes = sorted(c["_notes"], key=lambda n: n.get("date") or n.get("CreatedAt") or "", reverse=True)
    if notes:
        out += ["", "## Notes"]
        for n in notes:
            out += ["", f"### {fmt_date(n.get('date') or n.get('CreatedAt')) or 'Undated'}", "", clean_block(n.get("content"))]

    acts = sorted(c["_activities"], key=lambda a: a.get("date") or "", reverse=True)
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
SECRET_RE = re.compile(r"(?i)\b(password|passcode|pwd|pin|access code)(\s*[:=]\s*)[^\s&<]+")


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


def html_to_text(text):
    if not text:
        return ""
    if "<" in text:
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
        text = re.sub(r"(?i)</(p|div|h\d|li|tr|ul|ol|table)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = "\n".join(line.strip() for line in text.replace("\r\n", "\n").split("\n"))
    return SECRET_RE.sub(r"\1\2[redacted]", re.sub(r"\n{3,}", "\n\n", text)).strip()


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
              "url": c.get("url"), "tags": ["company", "hubspot", "wallace-white"]}
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
              "tags": ["person", "hubspot", "wallace-white"]}
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
              "tags": ["deal", "hubspot", "wallace-white", f"deal-stage/{tagify(stage)}"]}
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
        "Wallace & White's HubSpot CRM: one note per company, contact and deal, linked\n"
        "to each other, with the notes, calls, meetings and tasks logged against them.\n"
        "Written by `brain-sync` (whitehouse-rke2 `apps/ai/brain`) every few minutes;\n"
        "edit records in HubSpot, not here.\n"
    )
    log.info("hubspot: %s", ", ".join(f"{len(v)} {k}" for k, v in recs.items()))
    return files


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


def sync_meerkat():
    files = render_meerkat(fetch_meerkat())
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


def cycle():
    for name, fn, enabled in (("meerkat", sync_meerkat, MEERKAT_TOKEN), ("riffado", sync_riffado, RIFFADO_TOKEN),
                              ("hubspot", sync_hubspot, HUBSPOT_TOKEN), ("pages", sync_pages, PAGES_MCP_TOKEN)):
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
