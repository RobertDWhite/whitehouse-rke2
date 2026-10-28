#!/usr/bin/env python3
"""brain-imports: run the import scripts listed in the brain repo, commit what they write.

The brain repo's .brain/imports.json lists import scripts kept in the repo itself:

    {"imports": [{"script": "<dest>/<path>.py", "dest": "<dest>", "reads": ["<path>", ...]}]}

Each script owns its `dest` folder (the script must live inside it) and may read
the repo paths in `reads`. One pod runs three steps, so the code from the repo
never shares a container with a token:

  fetch   (FORGEJO_TOKEN)  copy dest + reads of every import from main into /work/repo
  run     (no secrets)     run each script: python3 -I <script>, cwd /work/repo/<n>
  commit  (FORGEJO_TOKEN)  diff each dest against the repo and commit the changes

A script may fetch from the network and call the LLM in LLM_URL / LLM_MODEL.
Everything left under dest after it runs is the new content of dest; files it
deletes are deleted. If dest changed in the repo while the script ran, that
import is skipped and the next run retries. A script that fails or times out
leaves its dest untouched.
"""
import base64
import hashlib
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request

FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://forgejo.forgejo.svc.cluster.local").rstrip("/")
REPO = os.environ.get("BRAIN_REPO", "WhiteHouse/brain")
BRANCH = os.environ.get("BRAIN_BRANCH", "main")
CONFIG = os.environ.get("IMPORTS_CONFIG", ".brain/imports.json")
WORK = pathlib.Path(os.environ.get("WORK_DIR", "/work"))
ENTITIES_DIR = os.environ.get("ENTITIES_DIR", "Entities").strip("/")
SCRIPT_TIMEOUT = int(os.environ.get("SCRIPT_TIMEOUT_SECONDS", "1500"))
MAX_COMMIT_BYTES = int(os.environ.get("MAX_COMMIT_BYTES", str(4 * 1024 * 1024)))
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
AUTHOR = {"name": "brain-sync", "email": "brain-sync@noreply.git.internal.white.fm"}
# Folders an import may never own: config, generated entity pages, published sites.
RESERVED = {".brain", ENTITIES_DIR, "Pages"}

log = logging.getLogger("brain-imports")


def forgejo(method, path, body=None):
    token = os.environ["FORGEJO_TOKEN"]
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def read_raw(path, ref):
    """Text of a repo file at ref, or None."""
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1/repos/{REPO}/raw/{urllib.parse.quote(path)}?ref={ref}")
    req.add_header("Authorization", f"token {os.environ['FORGEJO_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def clean_path(p):
    p = str(p or "").strip().strip("/")
    parts = p.split("/")
    if not p or any(x in ("", ".", "..") for x in parts):
        return ""
    return p


def load_imports(raw):
    try:
        items = (json.loads(raw) if raw else {}).get("imports") or []
    except (ValueError, AttributeError) as e:
        log.warning("ignoring %s: %s", CONFIG, e)
        return []
    out = []
    for i, m in enumerate(items):
        dest, script = clean_path(m.get("dest")), clean_path(m.get("script"))
        reads = [clean_path(r) for r in m.get("reads") or []]
        if not dest or dest.split("/")[0] in RESERVED or not script.startswith(dest + "/") or "" in reads:
            log.warning("ignoring import %r", m)
            continue
        out.append({"n": str(i), "dest": dest, "script": script, "reads": reads})
    return out


def list_tree(prefix):
    """{path: blob sha} under prefix on BRANCH."""
    q = urllib.parse.quote(f"{BRANCH}:{prefix}", safe="")
    root = forgejo("GET", f"/repos/{REPO}/git/trees/{q}?recursive=true&per_page=10000")
    if not root:
        return {}
    out, page = {}, 1
    while True:
        for e in root.get("tree") or []:
            if e.get("type") == "blob":
                out[f"{prefix}/{e['path']}"] = e["sha"]
        if not root.get("truncated"):
            return out
        page += 1
        root = forgejo("GET", f"/repos/{REPO}/git/trees/{q}?recursive=true&per_page=10000&page={page}") or {}


def blob_sha(content):
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


# ------------------------------------------------------------------ steps


def fetch():
    head = (((forgejo("GET", f"/repos/{REPO}/branches/{BRANCH}") or {}).get("commit") or {}).get("id")) or ""
    imports = load_imports(read_raw(CONFIG, head) or "")
    state = {"head": head, "imports": imports, "trees": {}}
    if not imports:
        (WORK / "state.json").write_text(json.dumps(state))
        log.info("no imports configured")
        return
    wanted = {m["n"]: [m["dest"], *m["reads"]] for m in imports}
    req = urllib.request.Request(f"{FORGEJO_URL}/api/v1/repos/{REPO}/archive/{head}.tar.gz")
    req.add_header("Authorization", f"token {os.environ['FORGEJO_TOKEN']}")
    with urllib.request.urlopen(req, timeout=600) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
        for member in tar:
            path = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not member.isfile() or not path or ".." in path.split("/"):
                continue
            for n, prefixes in wanted.items():
                if any(path == p or path.startswith(p + "/") for p in prefixes):
                    target = WORK / "repo" / n / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    for m in imports:
        state["trees"][m["n"]] = list_tree(m["dest"])
        (WORK / "repo" / m["n"] / m["dest"]).mkdir(parents=True, exist_ok=True)
    (WORK / "state.json").write_text(json.dumps(state))
    log.info("fetched %d import(s) at %s", len(imports), head[:8])


def run():
    state = json.loads((WORK / "state.json").read_text())
    results = {}
    (WORK / "home").mkdir(parents=True, exist_ok=True)
    for m in state["imports"]:
        root = WORK / "repo" / m["n"]
        if not (root / m["script"]).is_file():
            log.warning("import %s: %s not found", m["dest"], m["script"])
            continue
        env = {k: os.environ[k] for k in ("PATH", "TZ", "LLM_URL", "LLM_MODEL", "LLM_API_KEY") if k in os.environ}
        env.update(HOME=str(WORK / "home"), PYTHONDONTWRITEBYTECODE="1", BRAIN_IMPORT="1")
        log.info("import %s: running %s", m["dest"], m["script"])
        try:
            p = subprocess.run([sys.executable, "-I", m["script"]], cwd=root, env=env,
                               timeout=SCRIPT_TIMEOUT, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            log.error("import %s: timed out after %ss", m["dest"], SCRIPT_TIMEOUT)
            continue
        for line in (p.stdout + p.stderr).strip().splitlines()[-40:]:
            log.info("import %s | %s", m["dest"], line)
        if p.returncode == 0:
            results[m["n"]] = True
        else:
            log.error("import %s: exit %s, not committing", m["dest"], p.returncode)
    (WORK / "results.json").write_text(json.dumps(results))


def commit_files(label, ops):
    counts = {k: sum(1 for o in ops if o["operation"] == k) for k in ("create", "update", "delete")}
    summary = ", ".join(f"{v} {k}d" for k, v in counts.items() if v)
    if DRY_RUN:
        for o in ops:
            log.info("[dry-run] %s %s", o["operation"], o["path"])
        return summary
    batches, batch, size = [], [], 0
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
        forgejo("POST", f"/repos/{REPO}/contents", {"branch": BRANCH, "message": f"{label}: {summary}{part}",
                                                    "author": AUTHOR, "committer": AUTHOR, "files": b})
    return summary


def commit():
    state = json.loads((WORK / "state.json").read_text())
    ok = json.loads((WORK / "results.json").read_text()) if (WORK / "results.json").exists() else {}
    for m in state["imports"]:
        if not ok.get(m["n"]):
            continue
        dest = m["dest"]
        existing = list_tree(dest)
        if existing != state["trees"].get(m["n"]):
            log.warning("import %s: %s changed in the repo during the run, skipping", dest, dest)
            continue
        root = WORK / "repo" / m["n"]
        desired = {}
        for f in (root / dest).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.name != ".DS_Store":
                desired[f.relative_to(root).as_posix()] = f.read_bytes()
        ops = []
        for path, content in sorted(desired.items()):
            if existing.get(path) == blob_sha(content):
                continue
            op = {"operation": "update" if path in existing else "create", "path": path,
                  "content": base64.b64encode(content).decode()}
            if path in existing:
                op["sha"] = existing[path]
            ops.append(op)
        ops += [{"operation": "delete", "path": p, "sha": s} for p, s in sorted(existing.items()) if p not in desired]
        if not ops:
            log.info("import %s: no changes", dest)
            continue
        log.info("import %s: committed %s", dest, commit_files(f"import {dest}", ops))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    {"fetch": fetch, "run": run, "commit": commit}.get(step, lambda: sys.exit(f"usage: {sys.argv[0]} fetch|run|commit"))()


if __name__ == "__main__":
    main()
