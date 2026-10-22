"""Feed the Markdown notes of a Forgejo repo into Graphiti as episodes.

Runs inside the Graphiti MCP image and reuses the MCP server's config file and
client factories, so the worker and the server always share the same LLM,
embedder, entity types and Neo4j database.

Each cycle:
  1. Looks up the branch head; downloads the repo archive when it moved or when
     there is still a backlog.
  2. Hashes every Markdown note (after stripping noise such as the generated
     mention lists on entity pages) and compares with the state file.
  3. Removes the episodes of notes that changed or were deleted.
  4. Adds at most BATCH_SIZE new or changed notes, newest first, one episode
     per chunk of at most MAX_EPISODE_CHARS characters.
State lives in STATE_PATH (a small PVC). Metrics on :METRICS_PORT/metrics.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import signal
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ.get('MCP_SRC', '/app/mcp/src'))

from config.schema import GraphitiConfig  # noqa: E402
from graphiti_core import Graphiti  # noqa: E402
from graphiti_core.driver.neo4j_driver import Neo4jDriver  # noqa: E402
from graphiti_core.nodes import EpisodeType  # noqa: E402
from services.factories import (  # noqa: E402
    CrossEncoderFactory,
    EmbedderFactory,
    LLMClientFactory,
)
from utils.type_config import (  # noqa: E402
    build_edge_type_map,
    build_edge_types,
    build_entity_types,
)


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


FORGEJO_URL = env('FORGEJO_URL', 'http://forgejo.forgejo.svc.cluster.local').rstrip('/')
FORGEJO_REPO = env('FORGEJO_REPO')
FORGEJO_TOKEN = env('FORGEJO_TOKEN')
BRANCH = env('BRANCH', 'main')
STATE_PATH = env('STATE_PATH', '/state/state.json')
BATCH_SIZE = int(env('BATCH_SIZE', '10'))
INTERVAL_SECONDS = int(env('INTERVAL_SECONDS', '1800'))
MAX_EPISODE_CHARS = int(env('MAX_EPISODE_CHARS', '6000'))
MAX_ATTEMPTS = int(env('MAX_ATTEMPTS', '3'))
GROUP_MODE = env('GROUP_MODE', 'single')  # single | folder
GROUP_ID = env('GROUP_ID', env('GRAPHITI_GROUP_ID', 'main'))
EXCLUDE_GLOBS = [g.strip() for g in env('EXCLUDE_GLOBS').split(',') if g.strip()]
ENTITIES_DIR = env('ENTITIES_DIR', 'Entities').strip('/')
STRIP_FROM_HEADING = env('ENTITY_STRIP_FROM_HEADING', r'^##\s+Mentions\b')
# Frontmatter keys, or "Key: value" lines near the top of the body (common in
# exported docs), that give a note's date. Matched case-insensitively, "_" == " ".
DATE_KEYS = [
    k.strip().lower().replace('_', ' ')
    for k in env('DATE_KEYS', 'date,created,created at,created date,updated at').split(',')
    if k.strip()
]
METRICS_PORT = int(env('METRICS_PORT', '9100'))
# Bump to re-ingest everything after changing how notes are rendered.
RENDER_VERSION = env('RENDER_VERSION', '1')

log = logging.getLogger('brain-ingest')
STOP = asyncio.Event()

METRICS: dict[str, float] = {
    'last_success': 0.0,
    'last_cycle_seconds': 0.0,
    'notes_total': 0,
    'notes_ingested': 0,
    'notes_pending': 0,
    'notes_failed': 0,
    'episodes_added_total': 0,
    'episodes_removed_total': 0,
    'errors_total': 0,
}


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ('/metrics', '/healthz'):
            self.send_response(404)
            self.end_headers()
            return
        m = METRICS
        lines = [
            '# TYPE graphiti_ingest_last_success_timestamp_seconds gauge',
            f'graphiti_ingest_last_success_timestamp_seconds {m["last_success"]}',
            '# TYPE graphiti_ingest_last_cycle_duration_seconds gauge',
            f'graphiti_ingest_last_cycle_duration_seconds {m["last_cycle_seconds"]}',
            '# TYPE graphiti_ingest_notes gauge',
            f'graphiti_ingest_notes{{state="total"}} {m["notes_total"]}',
            f'graphiti_ingest_notes{{state="ingested"}} {m["notes_ingested"]}',
            f'graphiti_ingest_notes{{state="pending"}} {m["notes_pending"]}',
            f'graphiti_ingest_notes{{state="failed"}} {m["notes_failed"]}',
            '# TYPE graphiti_ingest_episodes_added_total counter',
            f'graphiti_ingest_episodes_added_total {m["episodes_added_total"]}',
            '# TYPE graphiti_ingest_episodes_removed_total counter',
            f'graphiti_ingest_episodes_removed_total {m["episodes_removed_total"]}',
            '# TYPE graphiti_ingest_errors_total counter',
            f'graphiti_ingest_errors_total {m["errors_total"]}',
        ]
        body = ('\n'.join(lines) + '\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


# --------------------------------------------------------------------------- forgejo


def forgejo_get(path: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        f'{FORGEJO_URL}/api/v1/repos/{FORGEJO_REPO}{path}',
        headers={'Authorization': f'token {FORGEJO_TOKEN}'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def head_commit() -> str:
    data = json.loads(forgejo_get(f'/branches/{urllib.parse.quote(BRANCH, safe="")}'))
    return data['commit']['id']


def last_commit_date(path: str) -> datetime | None:
    q = urllib.parse.urlencode(
        {'sha': BRANCH, 'path': path, 'limit': 1, 'stat': 'false', 'verification': 'false',
         'files': 'false'}
    )
    try:
        commits = json.loads(forgejo_get(f'/commits?{q}'))
    except Exception as e:  # noqa: BLE001
        log.warning('commit date lookup failed for a note: %s', e)
        return None
    if not commits:
        return None
    return parse_date(commits[0]['commit']['committer']['date'])


def load_notes() -> dict[str, tuple[str, float]]:
    """Return {path: (text, mtime)} for every Markdown note in the branch."""
    raw = forgejo_get(f'/archive/{urllib.parse.quote(BRANCH, safe="")}.tar.gz', timeout=300)
    notes: dict[str, tuple[str, float]] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile() or not member.name.lower().endswith('.md'):
                continue
            parts = member.name.split('/')[1:]  # drop the "<repo>/" prefix
            if not parts or any(p.startswith('.') for p in parts):
                continue
            path = '/'.join(parts)
            if any(fnmatch.fnmatch(path, g) for g in EXCLUDE_GLOBS):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            notes[path] = (f.read().decode('utf-8', errors='replace'), float(member.mtime))
    return notes


# --------------------------------------------------------------------------- rendering

FRONTMATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n', re.S)
FILENAME_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')
FILENAME_LONG_DATE_RE = re.compile(r'([A-Z][a-z]+ \d{1,2}, \d{4})')  # "March 3, 2026"


def parse_date(value: str) -> datetime | None:
    value = value.strip().strip('"\'')
    if not value:
        return None
    dt = None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%B %d, %Y %I:%M %p', '%B %d, %Y', '%b %d, %Y'):
            try:
                dt = datetime.strptime(value, fmt)
                break
            except ValueError:
                pass
    if dt is None:
        m = FILENAME_DATE_RE.search(value)
        if not m:
            return None
        dt = datetime.fromisoformat(m.group(1))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


BODY_FIELD_RE = re.compile(r'^\**([A-Za-z][A-Za-z _]{1,30}?)\**:\**\s*(.+)$')


def note_date(path: str, text: str) -> datetime | None:
    fields: dict[str, str] = {}
    m = FRONTMATTER_RE.match(text)
    body = text[m.end():] if m else text
    if m:
        for line in m.group(1).splitlines():
            if ':' in line and not line.startswith((' ', '\t', '-')):
                k, v = line.split(':', 1)
                fields.setdefault(k.strip().lower().replace('_', ' '), v)
    for line in body.splitlines()[:20]:
        fm = BODY_FIELD_RE.match(line.strip())
        if fm:
            fields.setdefault(fm.group(1).strip().lower().replace('_', ' '), fm.group(2))
    for key in DATE_KEYS:
        if key in fields:
            dt = parse_date(fields[key])
            if dt:
                return dt
    name = path.rsplit('/', 1)[-1]
    m = FILENAME_DATE_RE.search(name) or FILENAME_LONG_DATE_RE.search(name)
    if m:
        return parse_date(m.group(1))
    return None


def render(path: str, text: str) -> str:
    """The text that is hashed and ingested for a note."""
    if ENTITIES_DIR and path.startswith(ENTITIES_DIR + '/'):
        m = re.search(STRIP_FROM_HEADING, text, re.M)
        if m:
            text = text[: m.start()]
    # Generated-file banners carry no facts.
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return text.strip()


def chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    cur = ''
    for para in re.split(r'\n\s*\n', text):
        while len(para) > limit:  # a single huge paragraph (e.g. a transcript)
            cut = para.rfind(' ', 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                out.append(cur)
                cur = ''
            out.append(para[:cut])
            para = para[cut:].lstrip()
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur)
            cur = para
        else:
            cur = f'{cur}\n\n{para}' if cur else para
    if cur:
        out.append(cur)
    return out


def group_for(path: str) -> str:
    if GROUP_MODE != 'folder':
        return GROUP_ID
    top = path.split('/', 1)[0] if '/' in path else 'root'
    return re.sub(r'[^A-Za-z0-9_-]+', '-', top).strip('-').lower() or 'root'


# --------------------------------------------------------------------------- state


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault('notes', {})
    state.setdefault('failures', {})
    state.setdefault('commit', '')
    return state


def save_state(state: dict) -> None:
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------- graphiti


class Ingester:
    def __init__(self) -> None:
        cfg = GraphitiConfig()
        db = cfg.database.providers.neo4j
        self.graphiti = Graphiti(
            graph_driver=Neo4jDriver(
                uri=db.uri, user=db.username, password=db.password, database=db.database
            ),
            llm_client=LLMClientFactory.create(cfg.llm),
            embedder=EmbedderFactory.create(cfg.embedder),
            cross_encoder=CrossEncoderFactory.create(cfg.llm, cfg.embedder),
            max_coroutines=int(env('SEMAPHORE_LIMIT', '2')),
        )
        self.entity_types = build_entity_types(cfg.graphiti.entity_types)
        self.edge_types = build_edge_types(cfg.graphiti.edge_types)
        self.edge_type_map = build_edge_type_map(cfg.graphiti.edge_type_map)
        log.info('LLM %s, embedder %s', cfg.llm.model, cfg.embedder.model)

    async def setup(self) -> None:
        await self.graphiti.build_indices_and_constraints()

    async def remove(self, uuids: list[str]) -> None:
        for uuid in uuids:
            try:
                await self.graphiti.remove_episode(uuid)
                METRICS['episodes_removed_total'] += 1
            except Exception as e:  # noqa: BLE001  (already gone is fine)
                log.warning('could not remove episode %s: %s', uuid, e)

    async def add(self, path: str, body: str, when: datetime) -> list[str]:
        parts = chunks(body, MAX_EPISODE_CHARS)
        folder = path.split('/', 1)[0] if '/' in path else '(root)'
        uuids: list[str] = []
        try:
            for i, part in enumerate(parts, 1):
                name = path if len(parts) == 1 else f'{path} (part {i}/{len(parts)})'
                header = f'Note: {path}' + (f' (part {i} of {len(parts)})' if len(parts) > 1 else '')
                result = await self.graphiti.add_episode(
                    name=name,
                    episode_body=f'{header}\n\n{part}',
                    source=EpisodeType.text,
                    source_description=f'brain note in {folder}',
                    reference_time=when,
                    group_id=group_for(path),
                    entity_types=self.entity_types,
                    edge_types=self.edge_types,
                    edge_type_map=self.edge_type_map,
                )
                uuids.append(result.episode.uuid)
                METRICS['episodes_added_total'] += 1
        except BaseException:
            await self.remove(uuids)  # no half-ingested notes
            raise
        return uuids


async def cycle(ing: Ingester, state: dict) -> None:
    started = time.time()
    commit = head_commit()
    backlog = state.get('backlog', True)
    if commit == state['commit'] and not backlog:
        log.info('branch head unchanged (%s), nothing pending', commit[:12])
        METRICS['last_success'] = time.time()
        return

    notes = load_notes()
    rendered = {p: render(p, t) for p, (t, _) in notes.items()}
    hashes = {
        p: hashlib.sha256(f'{RENDER_VERSION}\n{body}'.encode()).hexdigest()
        for p, body in rendered.items()
    }

    # Deleted notes: drop their episodes.
    for path in [p for p in state['notes'] if p not in notes]:
        log.info('removing deleted note %s', path)
        await ing.remove(state['notes'][path]['episodes'])
        del state['notes'][path]
        save_state(state)
    for path in [p for p in state['failures'] if p not in notes]:
        del state['failures'][path]

    pending = []
    for path, h in hashes.items():
        if not rendered[path]:
            continue
        if state['notes'].get(path, {}).get('hash') == h:
            continue
        fail = state['failures'].get(path)
        if fail and fail['hash'] == h and fail['count'] >= MAX_ATTEMPTS:
            continue
        explicit = note_date(path, notes[path][0])
        sort_key = (explicit or datetime.fromtimestamp(notes[path][1], timezone.utc)).timestamp()
        pending.append((sort_key, path, explicit))
    pending.sort(reverse=True)  # newest first

    METRICS['notes_total'] = len(notes)
    METRICS['notes_pending'] = len(pending)
    log.info('%d notes, %d new or changed, taking up to %d', len(notes), len(pending), BATCH_SIZE)

    done = 0
    for _, path, explicit in pending[:BATCH_SIZE]:
        if STOP.is_set():
            break
        when = explicit or last_commit_date(path) or datetime.now(timezone.utc)
        prev = state['notes'].get(path)
        t0 = time.time()
        try:
            if prev:
                await ing.remove(prev['episodes'])
                del state['notes'][path]
                save_state(state)
            uuids = await ing.add(path, rendered[path], when)
        except Exception as e:  # noqa: BLE001
            METRICS['errors_total'] += 1
            fail = state['failures'].get(path)
            count = fail['count'] + 1 if fail and fail['hash'] == hashes[path] else 1
            state['failures'][path] = {'hash': hashes[path], 'count': count, 'error': str(e)[:300]}
            save_state(state)
            log.exception('failed to ingest a note (attempt %d/%d)', count, MAX_ATTEMPTS)
            if is_connection_error(e):
                log.error('model or database unreachable; ending this cycle early')
                break
            continue
        state['notes'][path] = {
            'hash': hashes[path],
            'episodes': uuids,
            'reference_time': when.isoformat(),
            'ingested_at': datetime.now(timezone.utc).isoformat(),
        }
        state['failures'].pop(path, None)
        save_state(state)
        done += 1
        log.info(
            'ingested %d/%d: %s (%d episode(s), %.0fs)',
            done, min(BATCH_SIZE, len(pending)), path, len(uuids), time.time() - t0,
        )

    state['commit'] = commit
    state['backlog'] = len(pending) > done
    save_state(state)
    METRICS['notes_pending'] = len(pending) - done
    METRICS['notes_ingested'] = len(state['notes'])
    METRICS['notes_failed'] = sum(1 for f in state['failures'].values() if f['count'] >= MAX_ATTEMPTS)
    METRICS['last_success'] = time.time()
    METRICS['last_cycle_seconds'] = time.time() - started
    log.info('cycle done: %d ingested, %d still pending', done, METRICS['notes_pending'])


def is_connection_error(e: BaseException) -> bool:
    text = f'{type(e).__name__} {e}'.lower()
    return any(s in text for s in ('connect', 'unavailable', 'timed out', 'timeout'))


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    for noisy in ('httpx', 'neo4j', 'graphiti_core'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
    if not (FORGEJO_REPO and FORGEJO_TOKEN):
        sys.exit('FORGEJO_REPO and FORGEJO_TOKEN are required')

    server = ThreadingHTTPServer(('0.0.0.0', METRICS_PORT), MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, STOP.set)

    ing = Ingester()
    await ing.setup()
    state = load_state()
    METRICS['notes_ingested'] = len(state['notes'])
    once = env('RUN_ONCE') == '1'
    while not STOP.is_set():
        try:
            await cycle(ing, state)
        except Exception:  # noqa: BLE001
            METRICS['errors_total'] += 1
            log.exception('cycle failed')
        if once:
            break
        try:
            await asyncio.wait_for(STOP.wait(), timeout=INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    await ing.graphiti.close()


if __name__ == '__main__':
    asyncio.run(main())
