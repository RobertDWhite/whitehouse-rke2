"""Songs sung or playing on the tapes, recognised from their lyrics in the transcript.

Runs after voices.py in the home-videos-cleanup Job (45-job-cleanup.yaml). Local only:
the transcript goes to qwen-flash, which knows the lyrics of well-known songs (pop,
hymns, carols, nursery rhymes, "Happy Birthday", TV themes and jingles). Nothing is
sent outside the cluster, so purely instrumental music is not identified.
For every tape with a tx.json it writes WORK/<slug>/songs.json:

    {"version": 1, "songs": [{"title": ..., "artist": ..., "start": s, "end": s,
                              "kind": "sung" | "playing" | "tv", "quote": ...,
                              "confidence": "high" | "medium"}]}

Every answer is checked against the transcript: the quoted lyric has to be there,
near the time the model gave, or the song is dropped.

    python3 songs.py [slug ...]   # skips tapes whose songs.json is newer than tx.json
    FORCE=1 python3 songs.py
"""
import json, os, re, sys, time, urllib.request

WORK = os.environ.get("WORK", "/work/White")
LLM = os.environ.get("LLM_URL", "http://qwen-flash.qwen-flash.svc.cluster.local:8000/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-35b-a3b")
FORCE = os.environ.get("FORCE") == "1"
WINDOW_CHARS = 60_000

PROMPT = """Below is a stretch of the transcript of a family's VHS home video (late 1980s to
early 2000s), one line per utterance with its time. Speech recognition is imperfect,
and music or TV in the background is transcribed too.

Find every moment where a real, recognisable existing song is sung or is playing: pop
and rock songs, oldies, hymns, Christmas carols, nursery rhymes and children's songs,
"Happy Birthday", TV show theme songs and well-known jingles. Only answer when the
transcribed words actually match that song's lyrics (allowing for recognition errors);
do not guess from mood or setting. Ordinary speech that merely contains a common phrase
is not a song.

Answer with JSON only:
{{"songs": [{{"title": "...", "artist": "performer or composer, or traditional",
             "start": "h:mm:ss of the first line with its lyrics",
             "end": "h:mm:ss of the last line with its lyrics",
             "kind": "sung" (people on the tape sing it) | "playing" (radio, record, toy, music box) | "tv" (in a TV program or commercial),
             "quote": "a few consecutive words copied exactly from the transcript lines",
             "confidence": "high" | "medium" | "low"}}]}}
Return {{"songs": []}} if there are none.

Tape: {title}

Transcript:
{transcript}
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def secs(s):
    try:
        p = [float(x) for x in str(s).strip().split(":")]
    except ValueError:
        return None
    v = 0.0
    for x in p:
        v = v * 60 + x
    return v


def words(text):
    return [x for x in (re.sub(r"[^\w']", "", w.lower()) for w in text.split()) if x]


def lines(tx):
    """Transcript segments merged into lines of up to ~20 s."""
    out = []
    for s in tx.get("segments", []):
        t = s["text"].strip()
        if not t or s["end"] - s["start"] > 60:
            continue
        if out and s["start"] - out[-1][1] < 1.5 and out[-1][1] - out[-1][0] < 20:
            out[-1][1] = s["end"]
            out[-1][2] += " " + t
        else:
            out.append([s["start"], s["end"], t])
    return out


def ask(title, chunk):
    body = {"model": LLM_MODEL, "temperature": 0, "max_tokens": 3000,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": PROMPT.format(title=title, transcript=chunk)}]}
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{LLM}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                content = json.loads(r.read())["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
            got = json.loads(content).get("songs") or []
            return [g for g in got if isinstance(g, dict)]
        except Exception as ex:
            log("songs llm failed", title[:40], attempt, repr(ex)[:200])
            time.sleep(20 * (attempt + 1))
    return []


def verify(song, ls):
    """The quote must appear in the transcript near the given time; returns (start, end)
    from the transcript itself, or None."""
    q = words(song.get("quote") or "")
    if len(q) < 3:
        return None
    a, b = secs(song.get("start")), secs(song.get("end"))
    if a is None:
        return None
    b = b if b is not None and b >= a else a
    near = [l for l in ls if l[1] >= a - 90 and l[0] <= b + 90]
    hay = [w for l in near for w in words(l[2])]
    qs = " ".join(q)
    if qs in " ".join(hay):
        hit = True
    else:  # allow a little slop: most of the quote's word pairs must be there in order
        pairs = list(zip(q, q[1:]))
        have = set(zip(hay, hay[1:]))
        hit = sum(p in have for p in pairs) >= max(2, 0.7 * len(pairs))
    if not hit:
        return None
    inside = [l for l in near if l[1] >= a - 5 and l[0] <= b + 5] or near
    return round(min(l[0] for l in inside), 1), round(max(l[1] for l in inside), 1)


def run(slug):
    dd = os.path.join(WORK, slug)
    tx_p, out_p = os.path.join(dd, "tx.json"), os.path.join(dd, "songs.json")
    if not os.path.exists(tx_p):
        return
    if not FORCE and os.path.exists(out_p) and os.path.getmtime(out_p) > os.path.getmtime(tx_p):
        return
    t0 = time.time()
    info_p = os.path.join(dd, "info.json")
    title = (json.load(open(info_p)).get("title") if os.path.exists(info_p) else None) or slug
    ls = lines(json.load(open(tx_p)))
    text = [f"[{hms(l[0])}] {l[2]}" for l in ls]
    chunks, cur, size = [], [], 0
    for i, t in enumerate(text):
        if size + len(t) > WINDOW_CHARS and cur:
            chunks.append(cur)
            cur, size = cur[-15:], sum(len(x) for x in cur[-15:])  # overlap, so a song isn't cut in two
        cur.append(t)
        size += len(t) + 1
    if cur:
        chunks.append(cur)
    found = []
    for c in chunks:
        for s in ask(title, "\n".join(c)):
            if s.get("confidence") not in ("high", "medium") or not s.get("title"):
                continue
            span = verify(s, ls)
            if not span:
                log("dropped unverified", slug, s.get("title"), s.get("quote"))
                continue
            found.append({"title": str(s["title"])[:120], "artist": str(s.get("artist") or "")[:120],
                          "start": span[0], "end": span[1],
                          "kind": s.get("kind") if s.get("kind") in ("sung", "playing", "tv") else "playing",
                          "quote": str(s.get("quote"))[:200], "confidence": s["confidence"]})
    # The same song found twice (window overlap, or verse after verse): one entry.
    found.sort(key=lambda s: s["start"])
    merged = []
    for s in found:
        m = next((x for x in merged if x["title"].casefold() == s["title"].casefold() and s["start"] - x["end"] < 120), None)
        if m:
            m["end"] = max(m["end"], s["end"])
            if s["confidence"] == "high":
                m["confidence"] = "high"
        else:
            merged.append(s)
    write_json(out_p, {"version": 1, "model": LLM_MODEL, "songs": merged})
    log("songs", slug, len(merged), ", ".join(s["title"] for s in merged)[:200], f"in {time.time() - t0:.0f}s")


def main():
    cat = json.load(open(os.path.join(WORK, "catalog.json")))
    only = set(sys.argv[1:])
    for e in cat:
        if not only or e["slug"] in only:
            try:
                run(e["slug"])
            except Exception as ex:
                log("songs failed", e["slug"], repr(ex)[:300])
    log("all done")


if __name__ == "__main__":
    main()
