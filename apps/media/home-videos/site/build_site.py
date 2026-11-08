"""Build and deploy the home-videos Pages site from the indexer's work volume.

    python3 apps/media/home-videos/site/build_site.py            # pull, build, deploy
    python3 apps/media/home-videos/site/build_site.py --no-pull  # rebuild from the local copy
    python3 apps/media/home-videos/site/build_site.py --no-deploy

Pulls /work/White (minus audio and caption-size frames) out of the indexer pod with
kubectl exec + tar into ~/.cache/home-videos, writes the site to
~/.cache/home-videos/site, and uploads it to pages-mcp in batches under its 64 MiB
per-call cap. The MCP token comes from apps/ai/pages/11-secret.sops.yaml.
The videos themselves are not uploaded: the site plays them from /media/ on its
own host (media-server in namespace home-videos).
"""
import base64, json, os, re, shutil, ssl, subprocess, sys
import urllib.request

try:  # python.org builds on macOS ship without a CA bundle
    import certifi
    SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL = ssl.create_default_context()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "../../../.."))
CACHE = os.path.expanduser("~/.cache/home-videos")
WORK = os.path.join(CACHE, "White")
OUT = os.path.join(CACHE, "site")
SITE = "home-videos"
FOLDER = "White"  # path under /media/ (= VHS_Conversions/<folder>)
STEP = 30
MCP = "https://pages-mcp.internal.white.fm/mcp"
BATCH_BYTES = int(2.5 * (1 << 20))  # the MCP endpoint rejects request bodies much over ~4 MB (413)


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def pull():
    pod = subprocess.run(["kubectl", "-n", "home-videos", "get", "pods", "-l", "app=home-videos-indexer",
                          "--field-selector=status.phase=Running", "-o", "jsonpath={.items[0].metadata.name}"],
                         capture_output=True, text=True).stdout.strip()
    temp = not pod
    if temp:  # no Job running: mount the work volume in a short-lived reader pod
        pod = "site-pull"
        sh("kubectl", "apply", "-f", "-", input=READER_POD.encode(), stdout=subprocess.DEVNULL)
        sh("kubectl", "-n", "home-videos", "wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=300s",
           stdout=subprocess.DEVNULL)
    os.makedirs(CACHE, exist_ok=True)
    try:
        _tar(pod)
    finally:
        if temp:
            sh("kubectl", "-n", "home-videos", "delete", "pod", pod, "--wait=false", stdout=subprocess.DEVNULL)


READER_POD = """
apiVersion: v1
kind: Pod
metadata: {name: site-pull, namespace: home-videos, labels: {app: home-videos-site-pull}}
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - {key: kubernetes.io/hostname, operator: NotIn, values: [rke2-node-50, rke2-node-10]}
  securityContext: {runAsNonRoot: true, runAsUser: 1026, runAsGroup: 100}
  containers:
    - name: indexer
      image: registry.internal.white.fm/clean-audio-mcp:0.5.1@sha256:f7ca9a91cb6464e57c4b8173e77b5a06860adcfb92ef89d4d1ca4ea3d9706982
      command: [sleep, "1800"]
      volumeMounts: [{name: work, mountPath: /work, readOnly: true}]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
  volumes:
    - {name: work, persistentVolumeClaim: {claimName: work, readOnly: true}}
"""


def _tar(pod):
    tar = subprocess.Popen(["kubectl", "-n", "home-videos", "exec", pod, "-c", "indexer", "--", "tar", "cf", "-", "--warning=no-file-changed",
                            "-C", "/work", "--exclude=*.flac", "--exclude=cap", "--exclude=*.tmp", "White"],
                           stdout=subprocess.PIPE)
    sh("tar", "xf", "-", "-C", CACHE, stdin=tar.stdout)
    if tar.wait() not in (0, 1):  # 1 = files changed while reading (the indexer is still running)
        sys.exit("tar from pod failed")


# ------------------------------------------------------------------ data
WINDOW = 600  # must match indexer/voices.py


def group_of(seg):
    return f"{seg.get('chunk') or 'w%d' % int(seg['start'] // WINDOW)}|{seg.get('speaker', 'Speaker 1')}"


def squash_loops(text, keep=2, min_repeats=4):
    """Collapse a phrase repeated over and over (the model looping on music or tape
    noise) to its first `keep` occurrences."""
    w = text.split()
    if len(w) < 12:
        return text
    out, i = [], 0
    while i < len(w):
        for n in range(1, 9):
            reps = 1
            while w[i + reps * n:i + (reps + 1) * n] == w[i:i + n] and i + (reps + 1) * n <= len(w):
                reps += 1
            if reps >= min_repeats:
                out += w[i:i + n] * keep + ["…"]
                i += reps * n
                break
        else:
            out.append(w[i])
            i += 1
    return " ".join(out)


def utterances(tx, voices=None):
    """Nemotron returns many word-level fragments; merge them into readable lines.
    Each line is [start, end, voice, text]: voice is the number of the tape's voice
    from voices.json (v3 -> 3), 0 when unknown, or Nemotron's speaker number when the
    tape has no voices.json yet (negative, so the page can tell them apart)."""
    groups = (voices or {}).get("groups") or {}
    out = []
    for s in tx.get("segments", []):
        text = squash_loops(s["text"].strip())
        if not text:
            continue
        if groups:
            v = groups.get(group_of(s))
            spk = int(v[1:]) if v else 0
        else:
            spk = -int(re.sub(r"\D", "", s.get("speaker", "1")) or 1)
        if out:
            last = out[-1]
            gap = s["start"] - last[1]
            if gap < 1.5 and (last[2] == spk or len(text.split()) <= 2) and len(last[3]) < 350:
                last[1] = max(last[1], s["end"])
                last[3] += " " + text
                continue
        out.append([round(s["start"], 1), round(s["end"], 1), spk, text])
    return out


def year_of(s):
    m = re.match(r"(\d{4})", str(s or ""))
    return int(m.group(1)) if m else None


def guess_title(file):
    t = re.sub(r"\.(mov|mp4|m4v)$", "", file, flags=re.I)
    t = re.sub(r"[._]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


VOICES = {}


def load():
    catalog = json.load(open(os.path.join(WORK, "catalog.json")))
    videos, details = [], {}
    for e in catalog:
        dd = os.path.join(WORK, e["slug"])
        thumbs = sorted(os.listdir(os.path.join(dd, "thumbs"))) if os.path.isdir(os.path.join(dd, "thumbs")) else []
        if not thumbs:
            continue
        info = json.load(open(os.path.join(dd, "info.json"))) if os.path.exists(os.path.join(dd, "info.json")) else {}
        tx = json.load(open(os.path.join(dd, "tx.json"))) if os.path.exists(os.path.join(dd, "tx.json")) else None
        caps = json.load(open(os.path.join(dd, "captions.json"))) if os.path.exists(os.path.join(dd, "captions.json")) else []
        voices = json.load(open(os.path.join(dd, "voices.json"))) if os.path.exists(os.path.join(dd, "voices.json")) else None
        dur = e["duration"]
        chapters = []
        raw = sorted((c for c in info.get("chapters") or [] if isinstance(c, dict)),
                     key=lambda c: float(c.get("start") or 0))
        for i, c in enumerate(raw):
            start = max(0.0, min(float(c.get("start") or 0), dur))
            end = float(raw[i + 1].get("start") or dur) if i + 1 < len(raw) else dur
            if end - start < 3:
                continue
            chapters.append({"start": round(start), "end": round(end), "title": c.get("title") or "Untitled",
                             "summary": c.get("summary") or "", "category": c.get("category") or "Other",
                             "date": c.get("date"), "people": c.get("people") or [], "tags": c.get("tags") or []})
        v = {
            "slug": e["slug"], "file": e["file"], "aliases": e.get("aliases", []), "dur": round(dur),
            "n": len(thumbs), "title": info.get("title") or guess_title(e["file"]),
            "summary": info.get("summary") or "", "date_start": info.get("date_start"),
            "date_end": info.get("date_end"), "date_basis": info.get("date_basis") or "",
            "categories": info.get("categories") or [], "people": info.get("people") or [],
            "places": info.get("places") or [], "chapters": chapters,
            "highlights": [h for h in info.get("highlights") or [] if isinstance(h, dict)],
            "quality": info.get("quality_notes") or "",
            "status": "done" if info else ("transcribed" if tx else "processing"),
        }
        v["year"] = year_of(v["date_start"])
        videos.append(v)
        if voices:
            VOICES[e["slug"]] = {k: {"talk": x["talk"], "n": x["n"], "emb": x["emb"], "clips": x["clips"],
                                     "guess": x.get("guess")} for k, x in voices["voices"].items()}
        details[e["slug"]] = {
            "tx": utterances(tx, voices) if tx else None,
            "caps": [[c["t"], c.get("scene") or "", c.get("stamp") or "", c.get("tags") or []] for c in caps],
        }
    videos.sort(key=lambda v: (v["date_start"] or "9999", v["title"]))
    return videos, details


# ------------------------------------------------------------------ build
def build():
    videos, details = load()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "data"))
    for v in videos:
        src = os.path.join(WORK, v["slug"], "thumbs")
        dst = os.path.join(OUT, "t", v["slug"])
        os.makedirs(dst)
        for f in os.listdir(src):
            shutil.copyfile(os.path.join(src, f), os.path.join(dst, f"{int(f.split('.')[0])}.jpg"))
        with open(os.path.join(OUT, "data", v["slug"] + ".json"), "w") as f:
            json.dump(details[v["slug"]], f, ensure_ascii=False, separators=(",", ":"))
    cfg = {"folder": FOLDER, "step": STEP}
    with open(os.path.join(OUT, "data", "voices.json"), "w") as f:
        json.dump(VOICES, f, ensure_ascii=False, separators=(",", ":"))
    lite = [{k: v[k] for k in ("slug", "file", "dur", "n", "title", "date_start", "date_end", "year", "people")}
            for v in videos]
    for name, data in (("index.html", videos), ("label.html", lite)):
        html = open(os.path.join(HERE, name.replace("index", "template"))).read()
        html = html.replace("/*VIDEOS*/[]", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        html = html.replace("/*CONFIG*/{}", json.dumps(cfg))
        with open(os.path.join(OUT, name), "w") as f:
            f.write(html)
    done = sum(v["status"] == "done" for v in videos)
    print(f"built {len(videos)} tapes ({done} classified), "
          f"{sum(len(v['chapters']) for v in videos)} chapters, {sum(len(x) for x in VOICES.values())} voices -> {OUT}")


# ------------------------------------------------------------------ deploy
def mcp_token():
    out = subprocess.run(["sops", "-d", os.path.join(REPO, "apps/ai/pages/11-secret.sops.yaml")],
                         capture_output=True, text=True, check=True).stdout
    return re.search(r"MCP_TOKEN:\s*['\"]?([^'\"\s]+)", out).group(1)


class Mcp:
    def __init__(self, token):
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                  "Accept": "application/json, text/event-stream"}
        self.id = 0
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "home-videos-build", "version": "1"}})
        self.rpc("notifications/initialized", None, notify=True)

    def rpc(self, method, params, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self.id += 1
            body["id"] = self.id
        req = urllib.request.Request(MCP, data=json.dumps(body).encode(), headers=self.h, method="POST")
        with urllib.request.urlopen(req, timeout=600, context=SSL) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.h["Mcp-Session-Id"] = sid
            raw = r.read().decode()
        if notify:
            return None
        if raw.lstrip().startswith("{"):
            msg = json.loads(raw)
        else:  # SSE
            msg = [json.loads(l[5:]) for l in raw.splitlines() if l.startswith("data:")][-1]
        if "error" in msg:
            raise RuntimeError(msg["error"])
        res = msg["result"]
        if res.get("isError"):
            raise RuntimeError(res["content"][0]["text"])
        return res


def deploy():
    files = []
    for root, _, names in os.walk(OUT):
        for n in names:
            p = os.path.join(root, n)
            files.append((os.path.relpath(p, OUT), p))
    # The pages go last, so they never point at thumbs that aren't there yet.
    files.sort(key=lambda f: (f[0].endswith(".html"), f[0]))
    mcp = Mcp(mcp_token())
    batch, size, first = [], 0, True

    def flush():
        nonlocal batch, size, first
        if not batch:
            return
        res = mcp.rpc("tools/call", {"name": "deploy_site",
                                     "arguments": {"name": SITE, "files": batch, "replace": first}})
        first = False
        print("uploaded", len(batch), "files", res["content"][0]["text"][:120])
        batch, size = [], 0

    for rel, p in files:
        data = open(p, "rb").read()
        if rel.endswith((".html", ".json")):
            item = {"path": rel, "content": data.decode(), "encoding": "text"}
        else:
            item = {"path": rel, "content": base64.b64encode(data).decode(), "encoding": "base64"}
        if size + len(data) > BATCH_BYTES:
            flush()
        batch.append(item)
        size += len(data)
    flush()
    print(f"https://{SITE}.pages.internal.white.fm/")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--no-pull" not in args:
        pull()
    build()
    if "--no-deploy" not in args:
        deploy()
