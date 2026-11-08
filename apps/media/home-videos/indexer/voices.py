"""Who is speaking: voice fingerprints per tape, grouped into voices, with name guesses.

Runs as the home-videos-voices Job (44-job-voices.yaml). For every tape with a tx.json
it writes WORK/<slug>/voices.json:

    {"groups": {"<group>": "v1", ...},          # transcript segment group -> voice
     "voices": {"v1": {"talk": seconds, "n": utterances, "emb": [192 floats],
                       "clips": [[start, end, text], ...],
                       "guess": {"name": ..., "confidence": ..., "why": ...}}}}

A group is one Nemotron speaker number inside one request: Nemotron numbers speakers
per request, so "Speaker 1" in two pieces of a tape can be different people. Groups
are keyed by the segment's "chunk" (pieces made by `indexer.py retime`) or else by a
10-minute window. Each group gets an ECAPA speaker embedding (SpeechBrain, CPU), the
mean over its longest utterances. Groups whose embeddings are close are merged into
one voice, except two groups from the same request, which Nemotron already said
are different people. The voices then go to qwen-flash with the transcript and
people.md for a first guess at names (people.md and the name list come from the
home-videos-people Secret). The site's labeling page (label.html) compares
voice embeddings across tapes, so naming a voice once suggests it everywhere else.

    python3 voices.py [slug ...]     # skips tapes whose voices.json is newer than tx.json
    FORCE=1 python3 voices.py        # redo everything
"""
import json, os, re, sys, time, urllib.request

import numpy as np
import soundfile as sf
import torch

WORK = os.environ.get("WORK", "/work/White")
CACHE = os.environ.get("MODEL_CACHE", "/work/.cache/models")
LLM = os.environ.get("LLM_URL", "http://qwen-flash.qwen-flash.svc.cluster.local:8000/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-35b-a3b")
FORCE = os.environ.get("FORCE") == "1"
WINDOW = 600          # seconds per group when a segment has no "chunk"
CLIPS = 12            # utterances embedded per group
MIN_CLIP, MAX_CLIP = 1.2, 8.0
MERGE = float(os.environ.get("MERGE_THRESHOLD", "0.5"))  # cosine similarity to merge two groups
SR = 16000


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def group_of(s):
    key = s.get("chunk") or f"w{int(s['start'] // WINDOW)}"
    return f"{key}|{s.get('speaker', 'Speaker 1')}"


def request_of(group):
    return group.split("|")[0]


def utterances(tx):
    """Consecutive segments of one group, merged while the gap is short."""
    out = []
    for s in tx.get("segments", []):
        text = s["text"].strip()
        if not text or s["end"] - s["start"] > 60:  # unaligned leftovers carry no timing
            continue
        g = group_of(s)
        if out and out[-1]["g"] == g and s["start"] - out[-1]["end"] < 0.6 and out[-1]["end"] - out[-1]["start"] < 15:
            out[-1]["end"] = s["end"]
            out[-1]["text"] += " " + text
        else:
            out.append({"start": s["start"], "end": s["end"], "g": g, "text": text})
    return out


_model = None


def model():
    global _model
    if _model is None:
        from speechbrain.inference.speaker import EncoderClassifier
        _model = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                savedir=os.path.join(CACHE, "spkrec-ecapa-voxceleb"),
                                                run_opts={"device": "cpu"})
    return _model


def embed(audio_path, start, end):
    mid = (start + end) / 2
    half = min(end - start, MAX_CLIP) / 2
    a, b = int((mid - half) * SR), int((mid + half) * SR)
    wav, sr = sf.read(audio_path, start=max(0, a), stop=b, dtype="float32", always_2d=False)
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    if len(wav) < SR:
        return None
    with torch.no_grad():
        e = model().encode_batch(torch.from_numpy(wav)[None]).squeeze().numpy()
    return e / (np.linalg.norm(e) + 1e-9)


def unit(v):
    return v / (np.linalg.norm(v) + 1e-9)


def cluster(groups):
    """Merge groups into voices: average-linkage agglomeration on cosine similarity, with
    cannot-link between groups from the same request."""
    clusters = [{"members": [g], "emb": info["emb"] * info["talk"], "w": info["talk"], "req": {request_of(g)}}
                for g, info in groups.items() if info["emb"] is not None]
    while True:
        best, pair = MERGE, None
        for i in range(len(clusters)):
            ci = unit(clusters[i]["emb"])
            for j in range(i + 1, len(clusters)):
                if clusters[i]["req"] & clusters[j]["req"]:
                    continue
                sim = float(ci @ unit(clusters[j]["emb"]))
                if sim > best:
                    best, pair = sim, (i, j)
        if not pair:
            return clusters
        i, j = pair
        a, b = clusters[i], clusters.pop(j)
        a["members"] += b["members"]
        a["emb"] = a["emb"] + b["emb"]
        a["w"] += b["w"]
        a["req"] |= b["req"]


GUESS_PROMPT = """You are helping label who is speaking in a family's digitised VHS home video.

Who's who in this family:
{people}

Tape: {title}
People the cataloguer saw on this tape: {seen}

Below is the transcript. Each line starts with a voice id (v1, v2, ...) that a voice
matcher assigned; one id should be one person, but the matcher can be wrong. Names
people use for each other are the best clue: someone calling "Mom!" is not Mom, and
the person answering probably is; "Sam, look at the camera" means Sam is being
filmed and the speaker is likely a parent behind the camera. TV programs, news
anchors and commercials recorded off the air are "TV".

For each voice, answer with JSON only:
{{"voices": {{"v1": {{"name": "<one exact name from the list below, or TV, or Unknown adult, Unknown child>",
                    "confidence": "high" | "medium" | "low",
                    "why": "<one short sentence with the clue, quoting the transcript>"}}, ...}}}}
Exact names: {names}

Transcript:
{transcript}
"""

# The exact names to choose from (family data, from the home-videos-people Secret).
NAMES = json.load(open(os.environ["NAMES_FILE"])) if os.path.exists(os.environ.get("NAMES_FILE", "")) else []


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def guess(slug, utts, voice_of, voices):
    people = open(os.path.join(WORK, "people.md")).read() if os.path.exists(os.path.join(WORK, "people.md")) else ""
    info_p = os.path.join(WORK, slug, "info.json")
    info = json.load(open(info_p)) if os.path.exists(info_p) else {}
    lines = [f"[{hms(u['start'])}] {voice_of[u['g']]}: {u['text']}" for u in utts if u["g"] in voice_of]
    text = "\n".join(lines)
    if len(text) > 90_000:  # keep whole stretches: evenly spaced blocks of 60 lines
        blocks = [lines[i:i + 60] for i in range(0, len(lines), 60)]
        keep = max(1, int(len(blocks) * 90_000 / len(text)))
        step = len(blocks) / keep
        text = "\n...\n".join("\n".join(blocks[int(k * step)]) for k in range(keep))
    body = {"model": LLM_MODEL, "temperature": 0, "max_tokens": 4000,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": GUESS_PROMPT.format(
                people=people, title=info.get("title") or slug, seen=", ".join(info.get("people") or []) or "unknown",
                names=", ".join(NAMES), transcript=text)}]}
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{LLM}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                content = json.loads(r.read())["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
            got = json.loads(content).get("voices") or {}
            return {v: g for v, g in got.items() if v in voices and isinstance(g, dict)}
        except Exception as ex:
            log("guess failed", slug, attempt, repr(ex)[:200])
    return {}


def run(slug):
    dd = os.path.join(WORK, slug)
    tx_p, out_p, audio = os.path.join(dd, "tx.json"), os.path.join(dd, "voices.json"), os.path.join(dd, "audio.flac")
    if not os.path.exists(tx_p) or not os.path.exists(audio):
        return
    if not FORCE and os.path.exists(out_p) and os.path.getmtime(out_p) > os.path.getmtime(tx_p):
        return
    t0 = time.time()
    utts = utterances(json.load(open(tx_p)))
    by = {}
    for u in utts:
        by.setdefault(u["g"], []).append(u)
    groups = {}
    for g, us in by.items():
        talk = sum(u["end"] - u["start"] for u in us)
        picks = sorted((u for u in us if u["end"] - u["start"] >= MIN_CLIP), key=lambda u: u["start"] - u["end"])[:CLIPS]
        embs = [e for e in (embed(audio, u["start"], u["end"]) for u in picks) if e is not None]
        groups[g] = {"talk": talk, "n": len(us), "emb": unit(np.mean(embs, axis=0)) if embs else None, "picks": picks}
    clusters = sorted(cluster(groups), key=lambda c: -c["w"])
    voice_of, voices = {}, {}
    for k, c in enumerate(clusters):
        v = f"v{k + 1}"
        for g in c["members"]:
            voice_of[g] = v
        picks = sorted((u for g in c["members"] for u in groups[g]["picks"]), key=lambda u: u["start"] - u["end"])
        voices[v] = {"talk": round(sum(groups[g]["talk"] for g in c["members"]), 1),
                     "n": sum(groups[g]["n"] for g in c["members"]),
                     "emb": [round(float(x), 4) for x in unit(c["emb"])],
                     "clips": [[round(u["start"], 1), round(u["end"], 1), u["text"][:200]] for u in picks[:8]]}
    for v, g in guess(slug, utts, voice_of, voices).items():
        voices[v]["guess"] = {"name": str(g.get("name") or ""), "confidence": str(g.get("confidence") or ""),
                              "why": str(g.get("why") or "")[:300]}
    write_json(out_p, {"version": 1, "model": "speechbrain/spkrec-ecapa-voxceleb", "merge": MERGE,
                       "groups": voice_of, "voices": voices})
    log("voices", slug, f"{len(groups)} groups -> {len(voices)} voices", f"in {time.time() - t0:.0f}s")


def main():
    torch.set_num_threads(int(os.environ.get("THREADS", "6")))
    cat = json.load(open(os.path.join(WORK, "catalog.json")))
    only = set(sys.argv[1:])
    for e in sorted(cat, key=lambda e: e["duration"]):
        if not only or e["slug"] in only:
            try:
                run(e["slug"])
            except Exception as ex:
                log("voices failed", e["slug"], repr(ex)[:300])
    log("all done")


if __name__ == "__main__":
    main()
