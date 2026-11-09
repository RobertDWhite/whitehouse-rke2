"""Index the family VHS conversions: frames, transcripts, captions, classification.

Runs in-cluster as the home-videos-indexer Job (see ../README.md). Every step writes
its own file under WORK/<slug>/ and is skipped when that file exists, so the Job can
be killed and re-run at any point.

    python3 indexer.py extract     # meta.json, thumbs/ (320px), cap/ (640px), audio.flac
    python3 indexer.py transcribe  # tx.json   via nemotron-transcribe (5090, one at a time)
    python3 indexer.py caption     # captions.json via qwen-flash vision (GB10)
    python3 indexer.py classify    # info.json via qwen-flash, uses WORK/people.md if present
    python3 indexer.py all         # all four, concurrently (each tape moves on as soon as it can)
    python3 indexer.py retime      # re-transcribe spans that came back without word timings
    python3 indexer.py deloop      # re-transcribe / collapse "yeah, yeah, yeah..." loops

One frame every STEP seconds (frame k is at k*STEP); the 640px copies feed the
captioner and are deleted once captions.json is written.
"""
import base64, concurrent.futures as cf, hashlib, json, os, re, subprocess, sys, threading, time
import httpx

SRC = os.environ.get("SRC", "/vhs/White")
WORK = os.environ.get("WORK", "/work/White")
STEP = int(os.environ.get("STEP", "30"))
NEMOTRON = os.environ.get("NEMOTRON_URL", "http://nemotron-transcribe.riffado.svc.cluster.local")
NEMOTRON_KEY = os.environ.get("NEMOTRON_API_KEY", "")
LLM = os.environ.get("LLM_URL", "http://qwen-flash.qwen-flash.svc.cluster.local:8000/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-35b-a3b")
CAPTION_WORKERS = int(os.environ.get("CAPTION_WORKERS", "4"))
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mpg", ".dv")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def slugify(name):
    s = re.sub(r"\.[^.]+$", "", name)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def head_hash(path, n=4 << 20):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read(n)).hexdigest()


def catalog():
    """All videos under SRC, with exact duplicates and ProRes masters folded into one entry."""
    path = os.path.join(WORK, "catalog.json")
    if os.path.exists(path):
        return json.load(open(path))
    files = sorted((f for f in os.listdir(SRC) if f.lower().endswith(VIDEO_EXT) and not f.startswith(".")),
                   key=lambda f: (bool(re.search(r" \(\d+\)\.", f)), f))  # "X (1).mov" after "X.mov"
    entries, by_key = [], {}
    for f in files:
        p = os.path.join(SRC, f)
        size = os.path.getsize(p)
        key = (size, head_hash(p))
        if key in by_key:  # byte-identical copy, e.g. "X (1).mov"
            by_key[key]["aliases"].append(f)
            continue
        info = probe(p)
        v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
        e = {"file": f, "slug": slugify(f), "size": size, "aliases": [],
             "duration": float(info["format"].get("duration", 0)),
             "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height")}
        by_key[key] = e
        entries.append(e)
    # A ProRes master next to its ".low" H.264 copy: index the H.264 one (browser-playable).
    names = {e["file"]: e for e in entries}
    for e in list(entries):
        if e["codec"] == "prores":
            stem = re.sub(r"(\.high)?\.[^.]+$", "", e["file"])
            low = next((n for n in names if n.startswith(stem) and ".low." in n), None)
            if low:
                names[low]["aliases"].append(e["file"])
                names[low]["master"] = e["file"]
                entries.remove(e)
    entries.sort(key=lambda e: e["duration"])  # short tapes first: results show up sooner
    os.makedirs(WORK, exist_ok=True)
    write_json(path, entries)
    log(f"catalog: {len(entries)} videos ({len(files)} files)")
    return entries


def d(e, *p):
    return os.path.join(WORK, e["slug"], *p)


# ---------------------------------------------------------------- extract
def extract(e):
    os.makedirs(d(e), exist_ok=True)
    src = os.path.join(SRC, e["file"])
    if not os.path.exists(d(e, "frames.done")):
        for sub in ("thumbs", "cap"):
            os.makedirs(d(e, sub), exist_ok=True)
        log("frames", e["slug"])
        # fps=1/STEP with round=down emits the frame at 0, STEP, 2*STEP ... seconds.
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-filter_complex",
                        f"[0:v]fps=1/{STEP}:round=down,yadif=deint=interlaced,split=2[a][b];"
                        "[a]scale=320:-2[t];[b]scale=640:-2[c]",
                        "-map", "[t]", "-q:v", "5", "-start_number", "0", d(e, "thumbs", "%05d.jpg"),
                        "-map", "[c]", "-q:v", "3", "-start_number", "0", d(e, "cap", "%05d.jpg")],
                       check=True)
        open(d(e, "frames.done"), "w").close()
    if not os.path.exists(d(e, "audio.flac")):
        log("audio", e["slug"])
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "flac", d(e, "audio.tmp.flac")], check=True)
        os.replace(d(e, "audio.tmp.flac"), d(e, "audio.flac"))


# ---------------------------------------------------------------- transcribe
def transcribe(e):
    if os.path.exists(d(e, "tx.json")):
        return
    log("transcribe", e["slug"], f"{e['duration'] / 60:.0f} min")
    t0 = time.time()
    for attempt in range(5):
        try:
            with open(d(e, "audio.flac"), "rb") as f:
                r = httpx.post(f"{NEMOTRON}/v1/audio/transcriptions",
                               headers={"Authorization": f"Bearer {NEMOTRON_KEY}"},
                               files={"file": ("audio.flac", f)},
                               data={"model": "nemotron-transcribe", "response_format": "verbose_json"},
                               timeout=httpx.Timeout(None, connect=30))
            r.raise_for_status()
            break
        except Exception as ex:  # pod restart, vLLM start race: wait and retry
            log("transcribe failed", e["slug"], attempt, repr(ex)[:300])
            time.sleep(120)
    else:
        raise RuntimeError(f"transcription failed for {e['slug']}")
    write_json(d(e, "tx.json"), r.json())
    log("transcribed", e["slug"], f"in {time.time() - t0:.0f}s")


# ---------------------------------------------------------------- retime
# nemotron-transcribe sends ~28-minute chunks to the model. When CTC alignment fails
# on a chunk ("alignment_failed", common on noisy VHS audio) every segment of that
# chunk comes back spanning the whole chunk. retime finds those spans and
# re-transcribes them in short pieces cut at pauses, where alignment works. Each new
# segment carries "chunk": the piece it came from, because Nemotron numbers speakers
# per request. The original transcript is kept as tx.orig.json.
RETIME_MIN, RETIME_MAX, RETIME_SMALL = 150, 240, 60


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


def fallback_spans(tx):
    from collections import Counter
    c = Counter((s["start"], s["end"]) for s in tx.get("segments", []) if "chunk" not in s)
    return sorted(k for k, n in c.items() if k[1] - k[0] >= 120 and (n >= 2 or k[1] - k[0] >= 300))


def silences(e, a, b):
    """Midpoints of pauses (absolute seconds) in the tape's audio between a and b."""
    err = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", str(a), "-t", str(b - a), "-i", d(e, "audio.flac"),
                          "-af", "silencedetect=noise=-32dB:d=0.35", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    out, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", line)
        if m and start is not None:
            out.append((a + (start + float(m.group(1))) / 2, float(m.group(2))))
            start = None
    return out


def cut_points(e, a, b):
    sil, cuts, pos = silences(e, a, b), [], a
    while b - pos > RETIME_MAX:
        window = [x for x in sil if pos + RETIME_MIN <= x[0] <= pos + RETIME_MAX]
        pos = max(window, key=lambda x: x[1])[0] if window else pos + RETIME_MAX
        cuts.append(pos)
    return [a, *cuts, b]


def nemotron_piece(e, a, b):
    audio = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                            "-i", d(e, "audio.flac"), "-c:a", "flac", "-f", "flac", "-"],
                           capture_output=True, check=True).stdout
    for attempt in range(5):
        try:
            r = httpx.post(f"{NEMOTRON}/v1/audio/transcriptions", headers={"Authorization": f"Bearer {NEMOTRON_KEY}"},
                           files={"file": ("piece.flac", audio)},
                           data={"model": "nemotron-transcribe", "response_format": "verbose_json"},
                           timeout=httpx.Timeout(None, connect=30))
            r.raise_for_status()
            return r.json().get("segments", [])
        except Exception as ex:
            log("retime piece failed", e["slug"], f"{a:.0f}", attempt, repr(ex)[:200])
            time.sleep(60)
    raise RuntimeError(f"retime failed for {e['slug']} at {a:.0f}s")


def retime_span(e, a, b, tag):
    out = []
    pts = cut_points(e, a, b)
    for i in range(len(pts) - 1):
        pa, pb = pts[i], pts[i + 1]
        segs = nemotron_piece(e, pa, pb)
        whole = bool(segs) and all(s["end"] - s["start"] >= (pb - pa) * 0.9 for s in segs) \
            and (len(segs) > 1 or len(segs[0]["text"].split()) > 30)
        if whole and pb - pa > RETIME_SMALL * 1.5:  # alignment failed again: smaller pieces
            sub = list(range(int(pa), int(pb), RETIME_SMALL))[1:]
            bounds = [pa, *sub, pb]
            for j in range(len(bounds) - 1):
                for s in nemotron_piece(e, bounds[j], bounds[j + 1]):
                    out.append({**s, "start": round(bounds[j] + s["start"], 2), "end": round(bounds[j] + s["end"], 2),
                                "text": squash_loops(s["text"]), "chunk": f"{tag}.{i}.{j}"})
            continue
        for s in segs:
            out.append({**s, "start": round(pa + s["start"], 2), "end": round(pa + s["end"], 2),
                        "text": squash_loops(s["text"]), "chunk": f"{tag}.{i}"})
    return out


def retime(e):
    path = d(e, "tx.json")
    if not os.path.exists(path):
        return False
    tx = json.load(open(path))
    spans = fallback_spans(tx)
    if not spans:
        return False
    if not os.path.exists(d(e, "tx.orig.json")):
        write_json(d(e, "tx.orig.json"), tx)
    log("retime", e["slug"], len(spans), "spans", f"{sum(b - a for a, b in spans) / 60:.0f} min")
    for k, (a, b) in enumerate(spans):
        t0 = time.time()
        new = retime_span(e, a, b, f"r{int(a)}")
        segs = [s for s in tx["segments"] if (s["start"], s["end"]) != (a, b) or "chunk" in s]
        segs = sorted(segs + new, key=lambda s: s["start"])
        for i, s in enumerate(segs):
            s["id"] = str(i)
        tx["segments"] = segs
        write_json(path, tx)  # after every span, so a restart resumes at the next one
        log("retimed", e["slug"], f"{a:.0f}-{b:.0f}", len(new), "segments", f"in {time.time() - t0:.0f}s")
    return True


# ---------------------------------------------------------------- deloop
# Under music or tape noise Nemotron sometimes "hears" one word over and over: dozens
# of lines of "yeah, yeah, yeah..." at 20+ words a second. `deloop` re-sends those
# stretches in short pieces (often real speech comes back), then collapses whatever
# still loops to its first couple of words and drops the rest.
LOOP_WPS = 5.0  # words per second nobody actually speaks at
FILLER = {"yeah", "yes", "no", "oh", "uh", "um", "ah", "ha", "hey", "mm", "hmm", "ooh", "la", "na", "da", "it", "the", "a"}


def _words(text):
    return [x for x in (re.sub(r"[^\w']", "", w.lower()) for w in text.split()) if x]


def loopy(s):
    w = _words(s["text"])
    if len(w) < 6 or s["end"] - s["start"] > 60:  # > 60 s: an unaligned leftover, not a loop
        return False
    uniq = len(set(w)) / len(w)
    fast = len(w) / max(s["end"] - s["start"], 0.2) > LOOP_WPS
    return (len(w) >= 12 and uniq <= 0.25) or (fast and uniq <= 0.5)


DELOOP_VERSION = 2
RUN_MIN = 6  # a word (or 2-3 word phrase) said this many times in a row is a loop


def _tokens(segs):
    """Every word of the transcript in order, as (segment index, word index, normalised)."""
    out = []
    for i, s in enumerate(segs):
        if s["end"] - s["start"] > 60:
            continue
        for j, w in enumerate(s["text"].split()):
            n = re.sub(r"[^\w']", "", w.lower())
            if n:
                out.append((i, j, n))
    return out


def word_runs(segs):
    """Loops across lines too: the model often returns a loop as hundreds of one-word
    segments. Yields (first token, unit length, repeats) over _tokens(segs)."""
    tok = _tokens(segs)
    norms = [t[2] for t in tok]
    runs, i = [], 0
    while i < len(tok):
        for n in (1, 2, 3):
            unit, reps = norms[i:i + n], 1
            while norms[i + reps * n:i + (reps + 1) * n] == unit and len(unit) == n:
                reps += 1
            last = i + n * reps - 1
            dense = reps >= RUN_MIN and segs[tok[last][0]]["end"] - segs[tok[i][0]]["start"] <= max(10, 2 * n * reps)
            if dense and len(set(unit)) == n:  # said back to back, not a stray word spread over minutes
                runs.append((i, n, reps))
                i += n * reps
                break
        else:
            i += 1
    return tok, runs


def collapse_runs(segs):
    """Keep the first two repeats of each run (plus "…"), or none when the run is only
    filler ("yeah", "oh"); drop segments left empty."""
    tok, runs = word_runs(segs)
    drop, ell, dropped = set(), set(), 0
    for i, n, reps in runs:
        unit = {tok[i + k][2] for k in range(n)}
        keep = 0 if unit <= FILLER else 2 * n
        for k in range(keep, n * reps):
            drop.add(tok[i + k][:2])
        if keep:
            ell.add(tok[i + keep - 1][:2])
        dropped += n * reps - keep
    out = []
    for si, sg in enumerate(segs):
        ws = sg["text"].split()
        kept = [w + (" …" if (si, j) in ell else "") for j, w in enumerate(ws) if (si, j) not in drop]
        if not kept:
            continue
        out.append(sg if len(kept) == len(ws) and not any((si, j) in ell for j in range(len(ws)))
                   else {**sg, "text": " ".join(kept)})
    return out, dropped


def loop_spans(tx, pad=2.0, join=15.0):
    segs = tx.get("segments", [])
    marks = [(s["start"], s["end"]) for s in segs if loopy(s)]
    tok, runs = word_runs(segs)
    for i, n, reps in runs:
        a, b = segs[tok[i][0]], segs[tok[i + n * reps - 1][0]]
        marks.append((a["start"], b["end"]))
    spans = []
    for s0, s1 in sorted(marks):
        a, b = s0 - pad, s1 + pad
        if spans and a - spans[-1][1] < join:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    # Grow each span to whole segments, so a piece never cuts one in half.
    for sp in spans:
        for s in segs:
            if s["end"] - s["start"] <= 60 and s["start"] < sp[1] and s["end"] > sp[0]:
                sp[0], sp[1] = min(sp[0], s["start"]), max(sp[1], s["end"])
        sp[0] = max(0.0, sp[0])
    return [(round(a, 2), round(b, 2)) for a, b in spans]


def collapse_loops(segs):
    """Keep the first two words of a loop and drop the lines that only continue it."""
    out, run, run_end, dropped = [], None, -1e9, 0
    for s in sorted(segs, key=lambda s: s["start"]):
        w = _words(s["text"])
        if run and w and set(w) <= run and s["start"] - run_end < 10:
            run_end = max(run_end, s["end"])
            dropped += len(w)
            continue
        if loopy(s):
            run, run_end = set(w), s["end"]
            if run <= FILLER:  # "yeah, yeah, yeah..." carries nothing: drop the line
                dropped += len(w)
                continue
            text = squash_loops(s["text"], keep=2, min_repeats=3)
            if loopy({**s, "text": text}):
                text = " ".join(s["text"].split()[:2]) + " …"
            dropped += len(w) - len(_words(text))
            s = {**s, "text": text}
        else:
            run = None
        out.append(s)
    return out, dropped


def deloop(e):
    path = d(e, "tx.json")
    if not os.path.exists(path):
        return False
    prev = json.load(open(d(e, "deloop.done"))) if os.path.exists(d(e, "deloop.done")) else {}
    if prev.get("version", 1 if prev else 0) >= DELOOP_VERSION:
        return False
    tx = json.load(open(path))
    before = sum(len(_words(s["text"])) for s in tx["segments"])
    done = [tuple(x) for x in prev.get("spans", [])]  # re-transcribed by an earlier version: don't again
    spans = [(a, b) for a, b in loop_spans(tx) if not any(a < y and b > x for x, y in done)]
    if spans:
        if not os.path.exists(d(e, "tx.pre-deloop.json")):
            write_json(d(e, "tx.pre-deloop.json"), tx)
        log("deloop", e["slug"], len(spans), "spans", f"{sum(b - a for a, b in spans) / 60:.1f} min")
    for a, b in spans:
        t0 = time.time()
        new = retime_span(e, a, b, f"d{int(a)}")
        keep = [s for s in tx["segments"] if not (s["start"] >= a - 0.01 and s["end"] <= b + 0.01)]
        tx["segments"] = sorted(keep + new, key=lambda s: s["start"])
        log("redone", e["slug"], f"{a:.0f}-{b:.0f}", len(new), "segments", f"in {time.time() - t0:.0f}s")
    segs, dropped = collapse_runs(tx["segments"])
    segs, dropped2 = collapse_loops(segs)
    dropped += dropped2
    changed = bool(spans) or len(segs) != len(tx["segments"]) or dropped
    if changed:
        for i, s in enumerate(segs):
            s["id"] = str(i)
        tx["segments"] = segs
        write_json(path, tx)
    after = sum(len(_words(s["text"])) for s in segs)
    write_json(d(e, "deloop.done"), {"version": DELOOP_VERSION, "spans": done + list(spans),
                                     "words_before": prev.get("words_before", before), "words_after": after})
    log("deloop done", e["slug"], f"{before} -> {after} words")
    return changed


# ---------------------------------------------------------------- caption
CAPTION_PROMPT = (
    "This is one frame from a family home video (VHS, 1989-2001). Reply with JSON only:\n"
    '{"scene": one or two plain sentences: where, who (count, approximate ages, what they wear), '
    "what is happening,\n"
    ' "setting": "indoor" | "outdoor" | "unclear",\n'
    ' "tags": up to 6 short lowercase tags (e.g. christmas, birthday, cake, presents, snow, pool, '
    "beach, soccer, t-ball, baseball, school, stage, church, baby, dog, car, kitchen, tv-broadcast, "
    "blank, static, title-card),\n"
    ' "stamp": the camcorder date/time overlay text exactly as shown, or null,\n'
    ' "text": any other readable on-screen text, or null}\n'
    "If the frame is blue/black/static/snow noise, say so in scene and tag it blank or static."
)


def llm(messages, max_tokens=400, json_mode=True, timeout=180):
    body = {"model": LLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(6):
        try:
            r = httpx.post(f"{LLM}/chat/completions", json=body, timeout=timeout)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as ex:
            log("llm error", attempt, repr(ex)[:200])
            time.sleep(20 * (attempt + 1))
    raise RuntimeError("LLM unavailable")


def parse_json(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {"scene": s}


def caption_one(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    out = llm([{"role": "user", "content": [
        {"type": "text", "text": CAPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}}]}], max_tokens=300)
    try:
        return parse_json(out)
    except Exception:
        return {"scene": out}


def caption(e, pool):
    if os.path.exists(d(e, "captions.json")):
        return
    frames = sorted(os.listdir(d(e, "cap")))
    log("caption", e["slug"], len(frames), "frames")
    partial_path = d(e, "captions.partial.json")
    done = json.load(open(partial_path)) if os.path.exists(partial_path) else {}
    todo = [f for f in frames if f not in done]
    futs = {pool.submit(caption_one, d(e, "cap", f)): f for f in todo}
    for i, fut in enumerate(cf.as_completed(futs)):
        done[futs[fut]] = fut.result()
        if i % 40 == 39:
            write_json(partial_path, done)
    caps = [dict(t=int(f.split(".")[0]) * STEP, **done[f]) for f in frames]
    write_json(d(e, "captions.json"), caps)
    if os.path.exists(partial_path):
        os.remove(partial_path)
    for f in frames:
        os.remove(d(e, "cap", f))


# ---------------------------------------------------------------- classify
CATEGORIES = ["Christmas", "Easter", "Thanksgiving", "Halloween", "Birthdays", "Anniversaries",
              "Sports", "School & Performances", "Vacations & Trips", "Babies & Toddlers",
              "Family Gatherings", "Everyday Life", "TV & News", "Church", "Weddings",
              "Old Film Reels", "Other"]


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def compact_transcript(tx, limit_chars=180_000):
    lines, cur = [], None
    for s in tx.get("segments", []):
        txt = s["text"].strip()
        if not txt:
            continue
        if cur and (s["start"] - cur["end"] < 2.0) and len(cur["text"]) < 400:
            cur["text"] += " " + txt
            cur["end"] = s["end"]
        else:
            cur = {"start": s["start"], "end": s["end"], "text": txt}
            lines.append(cur)
    out = "\n".join(f"[{hms(l['start'])}] {l['text']}" for l in lines)
    return out[:limit_chars]


def compact_captions(caps):
    out = []
    for c in caps:
        stamp = f" (stamp: {c['stamp']})" if c.get("stamp") else ""
        tags = ",".join(c.get("tags") or [])
        out.append(f"[{hms(c['t'])}] {c.get('scene', '')}{stamp} [{tags}]")
    return "\n".join(out)


CLASSIFY_PROMPT = """You are cataloguing a family's digitised VHS home videos for a private family archive website.

File name: {file}  (dot-separated parts at the start are usually family surnames, e.g. Smith, Joneserger, Miller: whose tape it is, not a place)
Other copies/versions: {aliases}
Length: {length}
{people}
Below are (1) a frame description every {step} seconds, including any camcorder date stamp, and (2) a speech transcript with timestamps. The transcript comes from automatic speech recognition on old tape audio: expect errors, especially in names.

Return JSON only, with this shape:
{{
 "title": short human title for the whole tape (e.g. "Christmas 1997 and Sam's 3rd birthday"),
 "summary": 3-6 sentence overview of what the tape contains, warm but factual,
 "date_start": "YYYY-MM-DD" or "YYYY-MM" or "YYYY" (earliest date you can support), or null,
 "date_end": same format, or null,
 "date_basis": one sentence: what the dates rest on (stamps, filename, spoken, guess),
 "categories": 1-4 values from {categories},
 "people": names (as written in the people list when it matches) of people who appear or are addressed,
 "places": places named or clearly shown (e.g. "Myrtle Beach", "grandparents' house", "soccer field"),
 "chapters": [ {{"start": seconds, "title": short title, "summary": 1-2 sentences, "category": one value from the list, "date": "YYYY-MM-DD" or null, "people": [names], "tags": [up to 6 lowercase tags]}} ],
 "highlights": [ {{"t": seconds, "note": a moment worth jumping to (a funny line, a first, a milestone), described in your own words; quote only short phrases that clearly make sense}} ] (3-8 items),
 "quality_notes": one sentence on tape condition (blank stretches, static, missing audio), or null
}}
Chapters: split wherever the event, place or day changes (date-stamp jumps are a strong signal). Usually 3-25 chapters per tape. Chapters must be in time order; the first starts at 0. Mark long blank/static stretches as a chapter titled "Blank tape" with category "Other".
Camcorder date stamps are usually right but the clock can be off; if stamps contradict the filename and content, say so in date_basis.

=== FRAMES ===
{captions}

=== TRANSCRIPT ===
{transcript}
"""


def classify(e):
    if os.path.exists(d(e, "info.json")):
        return
    tx = json.load(open(d(e, "tx.json")))
    caps = json.load(open(d(e, "captions.json")))
    ppl_path = os.path.join(WORK, "people.md")
    people = ""
    if os.path.exists(ppl_path):
        people = "Who is who in this family (use these names):\n" + open(ppl_path).read().strip() + "\n"
    prompt = CLASSIFY_PROMPT.format(
        file=e["file"], aliases=", ".join(e["aliases"]) or "none", length=hms(e["duration"]),
        people=people, step=STEP, categories=json.dumps(CATEGORIES),
        captions=compact_captions(caps), transcript=compact_transcript(tx) or "(no speech detected)")
    log("classify", e["slug"], f"{len(prompt)} chars")
    # The model occasionally returns broken or truncated JSON on long tapes; retry, then skip.
    for attempt in range(3):
        out = llm([{"role": "user", "content": prompt}], max_tokens=16000, timeout=1200)
        try:
            info = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip())
            if isinstance(info, dict) and info.get("title"):
                write_json(d(e, "info.json"), info)
                return
        except json.JSONDecodeError as ex:
            log("classify: bad JSON", e["slug"], attempt, str(ex)[:120])
    with open(d(e, "classify.failed.txt"), "w") as f:
        f.write(out)
    log("classify: giving up on", e["slug"])


# ---------------------------------------------------------------- driver
def wait_for(path, stop):
    while not os.path.exists(path):
        if stop.is_set():
            return False
        time.sleep(20)
    return True


def run_all(entries):
    stop = threading.Event()
    errors = []

    def guard(fn):
        def inner():
            try:
                fn()
            except Exception as ex:
                errors.append(ex)
                log("worker died:", repr(ex))
                stop.set()
        return inner

    def extractor():
        for e in entries:
            if stop.is_set():
                return
            extract(e)

    def transcriber():
        for e in entries:
            if not wait_for(d(e, "audio.flac"), stop):
                return
            transcribe(e)

    def captioner():
        with cf.ThreadPoolExecutor(CAPTION_WORKERS) as pool:
            for e in entries:
                if not wait_for(d(e, "frames.done"), stop):
                    return
                caption(e, pool)

    def classifier():
        for e in entries:
            if not (wait_for(d(e, "tx.json"), stop) and wait_for(d(e, "captions.json"), stop)):
                return
            try:  # one bad tape must not stop transcription of the rest
                classify(e)
            except Exception as ex:
                log("classify failed", e["slug"], repr(ex)[:200])

    ts = [threading.Thread(target=guard(f), name=f.__name__)
          for f in (extractor, transcriber, captioner, classifier)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errors:
        sys.exit(1)
    log("all done")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    entries = catalog()
    only = set(sys.argv[2:])
    if only:
        entries = [e for e in entries if e["slug"] in only]
    if cmd == "all":
        run_all(entries)
    elif cmd == "extract":
        for e in entries:
            extract(e)
    elif cmd == "transcribe":
        for e in entries:
            transcribe(e)
    elif cmd == "caption":
        with cf.ThreadPoolExecutor(CAPTION_WORKERS) as pool:
            for e in entries:
                caption(e, pool)
    elif cmd == "classify":
        for e in entries:
            if os.path.exists(d(e, "tx.json")) and os.path.exists(d(e, "captions.json")):
                classify(e)
    elif cmd == "retime":  # then re-classify the tapes it changed
        for e in entries:
            if retime(e) and os.path.exists(d(e, "info.json")):
                os.replace(d(e, "info.json"), d(e, "info.pre-retime.json"))
                if os.path.exists(d(e, "captions.json")):
                    classify(e)
    elif cmd == "deloop":
        for e in entries:
            deloop(e)
    elif cmd == "catalog":
        print(json.dumps(entries, indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
