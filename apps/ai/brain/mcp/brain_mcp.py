#!/usr/bin/env python3
"""brain-mcp: read-only MCP server over a local clone of the brain repo.

The git-sync sidecar (git-sync.sh) keeps a clone in REPO_DIR and writes, after
every change, META_DIR/HEAD ("<sha> <commit time>") and META_DIR/lastmod (the
output of `git log --format=@%ct --name-only`). This server loads every Markdown
file into memory whenever HEAD changes and answers MCP tool calls from that
snapshot.

Transport: MCP streamable HTTP, stateless, JSON responses (no SSE), on
POST /mcp with `Authorization: Bearer $MCP_TOKEN`. GET /healthz is open (only
reachable in-cluster). Standard library only, so it runs on the stock python
image.
"""

import difflib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.environ.get("REPO_DIR", "/work/repo")
META_DIR = os.environ.get("META_DIR", "/work/meta")
MCP_TOKEN = os.environ["MCP_TOKEN"]
PORT = int(os.environ.get("PORT", "8080"))
ENTITIES_DIR = os.environ.get("ENTITIES_DIR", "Entities").strip("/")
ENTITY_KINDS = [k for k in os.environ.get("ENTITY_KINDS", "People,Companies,Businesses,Properties").split(",") if k]
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(2 * 1024 * 1024)))
RELOAD_CHECK_SECONDS = int(os.environ.get("RELOAD_CHECK_SECONDS", "15"))
SERVER_NAME = "brain"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

# Frontmatter keys that carry the date a note is "about", in order of preference.
DATE_KEYS = ["date", "created", "recorded_at", "started", "meeting_date", "published", "updated_at", "updated"]
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

INSTRUCTIONS = """\
Read-only access to a personal knowledge repository: Markdown notes kept in git
(CRM records, meeting transcripts, work notes, agent sessions, hand-written hub
notes), plus generated entity pages.

How to use it:
1. Read the repo's CLAUDE.md first (read_note("CLAUDE.md")): it describes the
   folder layout and the rules for interpreting notes.
2. For any question about a person, company, business or property, start with
   get_entity(name). An entity page lists its authoritative records, related
   entities and every note that mentions it (newest first); follow those links
   with read_note.
3. Use search for anything else (literal terms by default, regex=true for a
   regular expression), narrowed with path_prefix. Snippets are previews: read
   the note before relying on it. list_recent shows what changed lately;
   outline shows a long note's headings so you can read just the part you need
   (read_note offset).
4. Cite every note you used as [[path/without/.md]]. When sources disagree, say
   so and give both, with dates.

The repo is synced from git every couple of minutes; there are no write tools.
"""


def now_ts():
    return time.time()


def iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- parsing

def parse_scalar(raw):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "[{\"":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    if raw[0] == "[" and raw.endswith("]"):
        return [p.strip().strip("'\"") for p in raw[1:-1].split(",") if p.strip()]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def parse_frontmatter(text):
    """Small YAML subset: `key: value`, inline JSON-ish lists, `- item` block lists."""
    if not text.startswith("---"):
        return {}, 0
    end = text.find("\n---", 3)
    if end < 0:
        return {}, 0
    body_start = text.find("\n", end + 4)
    body_start = len(text) if body_start < 0 else body_start + 1
    meta, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            meta[key] = parse_scalar(m.group(2))
            continue
        m = re.match(r"^\s+-\s+(.*)$", line)
        if m and key is not None:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(parse_scalar(m.group(1)))
    return meta, body_start


def note_date(meta, path):
    for k in DATE_KEYS:
        v = meta.get(k)
        if isinstance(v, str):
            m = DATE_RE.search(v)
            if m:
                return m.group(1)
    m = DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def note_title(meta, text, body_start, path):
    for k in ("title", "name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    m = re.search(r"^#\s+(.+)$", text[body_start:body_start + 4000], re.M)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(path))[0]


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)]


# ---------------------------------------------------------------- snapshot

class Note:
    __slots__ = ("path", "text", "meta", "body_start", "title", "date", "mtime")

    def __init__(self, path, text, mtime):
        self.path = path
        self.text = text
        self.meta, self.body_start = parse_frontmatter(text)
        self.title = note_title(self.meta, text, self.body_start, path)
        self.date = note_date(self.meta, path)
        self.mtime = mtime


class Snapshot:
    def __init__(self):
        self.head = None
        self.commit_ts = None
        self.loaded_at = None
        self.notes = {}         # path -> Note
        self.lower = {}         # lower(path) -> [paths]
        self.entities = {}      # lower(name/alias) -> [paths]
        self.entity_names = []  # (lower key, path) for fuzzy matching


SNAP = Snapshot()
LOAD_LOCK = threading.Lock()


def read_head():
    try:
        with open(os.path.join(META_DIR, "HEAD"), encoding="utf-8") as f:
            parts = f.read().split()
        return parts[0], (int(parts[1]) if len(parts) > 1 else None)
    except (OSError, IndexError, ValueError):
        return None, None


def read_lastmod():
    out, ts = {}, None
    try:
        with open(os.path.join(META_DIR, "lastmod"), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith("@"):
                    try:
                        ts = int(line[1:])
                    except ValueError:
                        ts = None
                elif ts is not None and line not in out:
                    out[line] = ts
    except OSError:
        pass
    return out


def load(force=False):
    head, commit_ts = read_head()
    if head is None or (head == SNAP.head and not force):
        return
    with LOAD_LOCK:
        if head == SNAP.head and not force:
            return
        t0 = time.time()
        lastmod = read_lastmod()
        notes = {}
        for root, dirs, files in os.walk(REPO_DIR):
            dirs[:] = [d for d in dirs if d != ".git"]
            for fn in files:
                if not fn.lower().endswith(".md"):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, REPO_DIR).replace(os.sep, "/")
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        continue
                    with open(full, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                notes[rel] = Note(rel, text, lastmod.get(rel))
        snap = Snapshot()
        snap.head, snap.commit_ts, snap.loaded_at = head, commit_ts, time.time()
        snap.notes = notes
        for p in notes:
            snap.lower.setdefault(p.lower(), []).append(p)
        prefixes = tuple(f"{ENTITIES_DIR}/{k}/" for k in ENTITY_KINDS)
        for p, n in notes.items():
            if not p.startswith(prefixes):
                continue
            keys = {os.path.splitext(os.path.basename(p))[0]}
            keys.update(as_list(n.meta.get("name")))
            keys.update(as_list(n.meta.get("aliases")))
            for k in keys:
                k = k.strip().lower()
                if k:
                    snap.entities.setdefault(k, []).append(p)
                    snap.entity_names.append((k, p))
        globals()["SNAP"] = snap
        logging.info("loaded %d notes (%d entity keys) at %s in %.1fs",
                     len(notes), len(snap.entities), head[:12], time.time() - t0)


def reload_loop():
    while True:
        try:
            load()
        except Exception:
            logging.exception("reload failed")
        time.sleep(RELOAD_CHECK_SECONDS)


# ---------------------------------------------------------------- helpers

class ToolError(Exception):
    pass


def norm_path(path):
    """Accept `a/b`, `a/b.md`, `[[a/b|label]]`, `a/b#heading`, leading slash."""
    p = (path or "").strip()
    if p.startswith("[[") and p.endswith("]]"):
        p = p[2:-2]
    p = p.split("|", 1)[0].split("#", 1)[0].strip().lstrip("/")
    if not p:
        raise ToolError("path is required")
    if ".." in p.split("/"):
        raise ToolError("invalid path")
    return p


def resolve(path):
    snap = SNAP
    p = norm_path(path)
    cands = [p] if p.lower().endswith(".md") else [p + ".md", p]
    for c in cands:
        if c in snap.notes:
            return snap.notes[c]
    for c in cands:
        hits = snap.lower.get(c.lower())
        if hits:
            return snap.notes[hits[0]]
    # Obsidian-style short link: a unique basename anywhere in the repo.
    if "/" not in p:
        base = (p if p.lower().endswith(".md") else p + ".md").lower()
        hits = [q for q in snap.notes if q.lower().rsplit("/", 1)[-1] == base]
        if len(hits) == 1:
            return snap.notes[hits[0]]
        if hits:
            raise ToolError("ambiguous note name; candidates: " + ", ".join(sorted(hits)[:20]))
    raise ToolError(f"note not found: {p}")


def link(path):
    return "[[" + (path[:-3] if path.lower().endswith(".md") else path) + "]]"


def prefix_filter(path_prefix):
    if not path_prefix:
        return lambda p: True
    pre = path_prefix.strip().strip("/").lower()
    if not pre:
        return lambda p: True
    return lambda p: p.lower() == pre or p.lower().startswith(pre + "/") or (
        pre.endswith(".md") and p.lower() == pre)


def snippet(text, start, end, width=160):
    a = max(0, start - width)
    b = min(len(text), end + width)
    s = text[a:b].replace("\r", "")
    s = re.sub(r"\s+", " ", s).strip()
    return ("..." if a > 0 else "") + s + ("..." if b < len(text) else "")


def split_terms(query):
    terms = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', query):
        t = (m.group(1) or m.group(2)).strip()
        if t:
            terms.append(t)
    return terms


def clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- tools

def tool_search(query, path_prefix=None, limit=20, regex=False, case_sensitive=False):
    if not query or not str(query).strip():
        raise ToolError("query is required")
    limit = clamp(limit, 1, 100, 20)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if regex:
            pats = [re.compile(query, flags | re.MULTILINE)]
        else:
            pats = [re.compile(re.escape(t), flags) for t in split_terms(query)]
    except re.error as e:
        raise ToolError(f"bad regex: {e}")
    keep = prefix_filter(path_prefix)
    snap = SNAP
    results = []
    for p, n in snap.notes.items():
        if not keep(p):
            continue
        first = None
        score = 0.0
        ok = True
        for rx in pats:
            m = rx.search(n.text)
            in_name = bool(rx.search(p) or rx.search(n.title))
            if not m and not in_name:
                ok = False
                break
            if m and (first is None or m.start() < first.start()):
                first = m
            if m:
                hits = sum(1 for _ in zip(range(50), rx.finditer(n.text)))
                score += hits ** 0.5
            if in_name:
                score += 5
        if not ok:
            continue
        if first is not None:
            snip = snippet(n.text, first.start(), first.end())
        else:
            snip = snippet(n.text, n.body_start, n.body_start)
        results.append((score, n.date or "", n.mtime or 0, {
            "path": p, "link": link(p), "title": n.title, "date": n.date,
            "modified": iso(n.mtime), "snippet": snip}))
    total = len(results)
    results.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    return {"total_matches": total, "returned": min(total, limit),
            "results": [r[3] for r in results[:limit]]}


def tool_read_note(path, max_chars=20000, offset=0):
    n = resolve(path)
    max_chars = clamp(max_chars, 200, 200000, 20000)
    offset = clamp(offset, 0, len(n.text), 0)
    chunk = n.text[offset:offset + max_chars]
    out = {"path": n.path, "link": link(n.path), "title": n.title, "date": n.date,
           "modified": iso(n.mtime), "length": len(n.text), "offset": offset,
           "content": chunk}
    if offset + max_chars < len(n.text):
        out["truncated"] = True
        out["next_offset"] = offset + max_chars
    return out


def tool_get_entity(name, kind=None, max_chars=20000):
    if not name or not str(name).strip():
        raise ToolError("name is required")
    snap = SNAP
    key = str(name).strip().lower()
    kinds = None
    if kind:
        kinds = [k for k in ENTITY_KINDS if k.lower() == str(kind).strip().lower()
                 or k.lower().rstrip("s") == str(kind).strip().lower().rstrip("s")]
        if not kinds:
            raise ToolError("kind must be one of: " + ", ".join(ENTITY_KINDS))

    def ok(p):
        return kinds is None or any(p.startswith(f"{ENTITIES_DIR}/{k}/") for k in kinds)

    hits = sorted({p for p in snap.entities.get(key, []) if ok(p)})
    if len(hits) == 1:
        n = snap.notes[hits[0]]
        out = tool_read_note(n.path, max_chars=max_chars)
        out["type"] = n.meta.get("type")
        out["aliases"] = as_list(n.meta.get("aliases"))
        return out
    if hits:
        return {"ambiguous": True, "message": "several entities match; call again with kind or use read_note",
                "candidates": [{"path": p, "link": link(p), "title": snap.notes[p].title} for p in hits]}
    # No exact match: substring and fuzzy candidates.
    scored = {}
    for k, p in snap.entity_names:
        if not ok(p):
            continue
        if key in k or (len(k) >= 4 and k in key):
            s = 0.9 + len(key) / max(len(k), 1) * 0.1
        else:
            # Compare against the whole name and against every run of words as
            # long as the query, so a misspelled first or last name still matches.
            words, n = k.split(), max(1, len(key.split()))
            spans = {k} | {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}
            s = max(difflib.SequenceMatcher(None, key, c).ratio() for c in spans)
            if s < 0.8:
                continue
            s *= 0.85
        if s > scored.get(p, 0):
            scored[p] = s
    cands = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:15]
    return {"found": False, "message": "no entity with that exact name or alias; candidates below (try search too)",
            "candidates": [{"path": p, "link": link(p), "title": snap.notes[p].title} for p, _ in cands]}


def tool_list_recent(path_prefix=None, days=7, limit=20, sort="modified"):
    days = clamp(days, 1, 3650, 7)
    limit = clamp(limit, 1, 200, 20)
    keep = prefix_filter(path_prefix)
    snap = SNAP
    rows = []
    if sort == "date":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        for p, n in snap.notes.items():
            if keep(p) and n.date and n.date >= cutoff:
                rows.append((n.date, n.mtime or 0, n))
    elif sort == "modified":
        cutoff = now_ts() - days * 86400
        for p, n in snap.notes.items():
            if keep(p) and n.mtime and n.mtime >= cutoff:
                rows.append((n.mtime, n.date or "", n))
    else:
        raise ToolError("sort must be 'modified' or 'date'")
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return {"total": len(rows), "returned": min(len(rows), limit),
            "notes": [{"path": n.path, "link": link(n.path), "title": n.title, "date": n.date,
                       "modified": iso(n.mtime)} for _, _, n in rows[:limit]]}


def tool_outline(path):
    n = resolve(path)
    heads, in_code, pos = [], False, 0
    for line in n.text.splitlines(keepends=True):
        s = line.rstrip("\n")
        if s.startswith("```"):
            in_code = not in_code
        elif not in_code and pos >= n.body_start:
            m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", s)
            if m:
                heads.append({"level": len(m.group(1)), "heading": m.group(2), "offset": pos})
        pos += len(line)
    return {"path": n.path, "link": link(n.path), "title": n.title, "date": n.date,
            "modified": iso(n.mtime), "length": len(n.text), "frontmatter": n.meta,
            "headings": heads}


def tool_list_dir(path=""):
    snap = SNAP
    pre = (path or "").strip().strip("/")
    pl = pre.lower() + "/" if pre else ""
    dirs, files = {}, []
    for p in snap.notes:
        if not p.lower().startswith(pl):
            continue
        rest = p[len(pl):]
        if "/" in rest:
            d = rest.split("/", 1)[0]
            dirs[d] = dirs.get(d, 0) + 1
        else:
            files.append(p)
    if pre and not dirs and not files:
        raise ToolError(f"no notes under: {pre}")
    files.sort()
    return {"path": pre, "folders": [{"name": d, "notes": c} for d, c in sorted(dirs.items())],
            "notes": files[:500], "notes_total": len(files)}


def tool_repo_status():
    snap = SNAP
    return {"head": snap.head, "commit_time": iso(snap.commit_ts), "loaded_at": iso(snap.loaded_at),
            "notes": len(snap.notes), "entity_keys": len(snap.entities)}


RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
PREFIX = {"type": "string", "description": "Only notes under this folder (repo-relative, e.g. 'Entities/People')."}

TOOLS = [
    {"name": "search", "fn": tool_search, "title": "Search notes",
     "description": "Full-text search over every Markdown note. By default each whitespace-separated term (or \"quoted phrase\") must appear, case-insensitive; set regex=true to use a Python regular expression instead. Returns path, [[link]], title, date, last-modified and a snippet around the first match, best matches first.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Terms, \"quoted phrase\", or a regex when regex=true."},
         "path_prefix": PREFIX,
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         "regex": {"type": "boolean", "default": False},
         "case_sensitive": {"type": "boolean", "default": False}},
         "required": ["query"]}},
    {"name": "read_note", "fn": tool_read_note, "title": "Read a note",
     "description": "Return a note's Markdown (frontmatter included). path is repo-relative, with or without .md; [[wikilinks]] are accepted. Long notes are paged: pass next_offset back as offset.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "max_chars": {"type": "integer", "default": 20000, "minimum": 200, "maximum": 200000},
         "offset": {"type": "integer", "default": 0, "minimum": 0}},
         "required": ["path"]}},
    {"name": "get_entity", "fn": tool_get_entity, "title": "Get an entity page",
     "description": "Find the entity page for a person, company, business or property by name or alias (case-insensitive) and return it. Entity pages list the authoritative records, related entities and every note that mentions the entity. Without an exact match it returns close candidates.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "kind": {"type": "string", "enum": ENTITY_KINDS, "description": "Optional: restrict to one entity folder."},
         "max_chars": {"type": "integer", "default": 20000, "minimum": 200, "maximum": 200000}},
         "required": ["name"]}},
    {"name": "list_recent", "fn": tool_list_recent, "title": "List recent notes",
     "description": "Newest notes. sort='modified' (default) uses the last git commit that touched each file; sort='date' uses the note's own date (frontmatter date/created/recorded_at/started/updated_at, or a YYYY-MM-DD in the file name).",
     "inputSchema": {"type": "object", "properties": {
         "path_prefix": PREFIX,
         "days": {"type": "integer", "default": 7, "minimum": 1},
         "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
         "sort": {"type": "string", "enum": ["modified", "date"], "default": "modified"}}}},
    {"name": "outline", "fn": tool_outline, "title": "Outline a note",
     "description": "A note's parsed frontmatter and its headings with character offsets (use an offset with read_note to read one section).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "list_dir", "fn": tool_list_dir, "title": "List a folder",
     "description": "Subfolders (with note counts) and notes directly inside a folder. Empty path = repo root.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": ""}}}},
    {"name": "repo_status", "fn": tool_repo_status, "title": "Repo status",
     "description": "Commit the snapshot was loaded from, its commit time and note count.",
     "inputSchema": {"type": "object", "properties": {}}},
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------- JSON-RPC

def rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_rpc(msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notification
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return rpc_result(mid, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Brain (read-only)", "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS})
    if method == "ping":
        return rpc_result(mid, {})
    if method == "tools/list":
        return rpc_result(mid, {"tools": [
            {"name": t["name"], "title": t["title"], "description": t["description"],
             "inputSchema": t["inputSchema"], "annotations": dict(RO, title=t["title"])} for t in TOOLS]})
    if method == "tools/call":
        t = TOOL_BY_NAME.get(params.get("name"))
        if not t:
            return rpc_error(mid, -32602, f"unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        if SNAP.head is None:
            return rpc_result(mid, {"isError": True, "content": [
                {"type": "text", "text": "repository not loaded yet; retry in a minute"}]})
        try:
            allowed = set(t["inputSchema"].get("properties", {}))
            out = t["fn"](**{k: v for k, v in args.items() if k in allowed})
            return rpc_result(mid, {"content": [
                {"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                "structuredContent": out})
        except ToolError as e:
            return rpc_result(mid, {"isError": True, "content": [{"type": "text", "text": str(e)}]})
        except TypeError as e:
            return rpc_result(mid, {"isError": True, "content": [{"type": "text", "text": f"bad arguments: {e}"}]})
        except Exception as e:  # noqa: BLE001
            logging.exception("tool %s failed", t["name"])
            return rpc_result(mid, {"isError": True, "content": [{"type": "text", "text": f"internal error: {e}"}]})
    if method in ("resources/list", "prompts/list"):
        return rpc_result(mid, {method.split("/")[0]: []})
    return rpc_error(mid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "brain-mcp"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body=None, ctype="application/json", extra=None):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def authorized(self):
        h = self.headers.get("Authorization", "")
        if h[:7].lower() != "bearer ":
            return False
        return hmac.compare_digest(h[7:].strip().encode(), MCP_TOKEN.encode())

    def do_GET(self):
        if self.path == "/healthz":
            ready = SNAP.head is not None
            return self.send(200 if ready else 503, {"ok": ready, "head": SNAP.head, "notes": len(SNAP.notes)})
        if self.path.split("?")[0].rstrip("/") == "/mcp":
            if not self.authorized():
                return self.send(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
            # No server-initiated messages: no SSE stream.
            return self.send(405, {"error": "method not allowed"}, extra={"Allow": "POST"})
        return self.send(404, {"error": "not found"})

    def do_DELETE(self):
        return self.send(405, {"error": "method not allowed"}, extra={"Allow": "POST"})

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/mcp":
            return self.send(404, {"error": "not found"})
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1024 * 1024:
                return self.send(413, {"error": "too large"})
            payload = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self.send(400, rpc_error(None, -32700, "parse error"))
        t0 = time.time()
        if isinstance(payload, list):
            out = [r for r in (handle_rpc(m) for m in payload) if r is not None]
            names = ",".join(str(m.get("method")) for m in payload if isinstance(m, dict))
        else:
            out = handle_rpc(payload)
            names = payload.get("method") if isinstance(payload, dict) else "?"
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                names += ":" + str((payload.get("params") or {}).get("name"))
        logging.info("POST /mcp %s %.0fms", names, (time.time() - t0) * 1000)
        if out is None or out == []:
            return self.send(202)
        return self.send(200, out)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    threading.Thread(target=reload_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("", PORT), Handler)
    srv.daemon_threads = True
    logging.info("listening on :%d", PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
