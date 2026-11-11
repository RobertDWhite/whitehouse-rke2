"""Carry the names given on the site across a re-transcription.

`indexer.py retranscribe` keeps a tape's old transcript and voices
(tx.pre-retx.json, voices.pre-retx.json) and voices.py then numbers the tape's
voices afresh, so a label like "<slug>/v3" would point at somebody else. For every
labelled old voice this finds the new voice that speaks over the same stretches of
the tape (seconds of overlapping speech) and moves the label there. A label whose
voice has no clear successor (under MIN_OVERLAP seconds, or under MIN_SHARE of
the old voice's timed speech) is removed rather than left on the wrong person;
everything is written to relabel.json beside the tape, and the labels API keeps
its own history.jsonl.

Runs in the home-videos-cleanup Job after voices.py:
    python3 relabel.py [slug ...]
"""
import bisect, json, os, sys, time, urllib.request

from voices import WORK, utterances, write_json

LABELS = os.environ.get("LABELS_URL", "http://labels.home-videos.svc.cluster.local:8080/api/labels")
MIN_OVERLAP, MIN_SHARE = 3.0, 0.1
PLACEHOLDERS = {"Unknown", "Not speech", "TV"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def spans(tx, voices):
    """voice -> sorted [(start, end)] of its timed speech."""
    out = {}
    groups = voices.get("groups") or {}
    for u in utterances(tx):
        v = groups.get(u["g"])
        if v:
            out.setdefault(v, []).append((u["start"], u["end"]))
    for v in out:
        out[v].sort()
    return out


def overlap(a, b):
    """Seconds where two sorted interval lists overlap."""
    starts = [x[0] for x in b]
    tot = 0.0
    for s, e in a:
        i = max(0, bisect.bisect_right(starts, s) - 1)
        while i < len(b) and b[i][0] < e:
            tot += max(0.0, min(e, b[i][1]) - max(s, b[i][0]))
            i += 1
    return tot


def api(body=None):
    req = urllib.request.Request(LABELS, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def run(slug, labels):
    dd = os.path.join(WORK, slug)
    files = {k: os.path.join(dd, f) for k, f in (("otx", "tx.pre-retx.json"), ("ov", "voices.pre-retx.json"),
                                                  ("ntx", "tx.json"), ("nv", "voices.json"))}
    if os.path.exists(os.path.join(dd, "relabel.json")) or not all(os.path.exists(p) for p in files.values()):
        return
    mine = {k.split("/", 1)[1]: x["name"] for k, x in labels.items() if k.split("/", 1)[0] == slug}
    old = spans(json.load(open(files["otx"])), json.load(open(files["ov"])))
    new = spans(json.load(open(files["ntx"])), json.load(open(files["nv"])))
    report = {"moved": {}, "dropped": {}, "conflicts": []}
    cands = []  # (overlap, old voice, new voice)
    for ov in mine:
        for nv, iv in new.items():
            ol = overlap(old.get(ov, []), iv)
            if ol >= MIN_OVERLAP:
                cands.append((ol, ov, nv))
    # Real names first: when the new clustering merges voices that were labelled
    # differently, a name beats "Unknown" / "Not speech" / "TV", then most overlap wins.
    cands.sort(key=lambda c: (mine[c[1]] not in PLACEHOLDERS, c[0]), reverse=True)
    best, placed = {}, set()  # new voice -> (overlap, name, old voice)
    # 1. each labelled old voice goes to the new voice sharing most of its speech
    for ol, ov, nv in cands:
        talk = sum(e - s for s, e in old.get(ov, []))
        if ov in placed or ol < MIN_SHARE * talk:
            continue
        if nv in best:
            if best[nv][1] != mine[ov]:
                report["conflicts"].append({"new": nv, "kept": best[nv][1], "lost": mine[ov], "from": ov})
            continue
        best[nv] = (ol, mine[ov], ov)
        placed.add(ov)
    # 2. the new clustering may split one person into several voices: a new voice
    #    that is mostly (>= 60 %) one labelled old voice gets that name too
    for ol, ov, nv in cands:
        ntalk = sum(e - s for s, e in new[nv])
        if nv not in best and ov in placed and ol >= 0.6 * ntalk:
            best[nv] = (ol, mine[ov], ov)
    for ov, name in mine.items():
        if ov not in placed:
            report["dropped"][ov] = {"name": name, "talk": round(sum(e - s for s, e in old.get(ov, [])), 1)}
    for nv, (ol, name, ov) in best.items():
        report["moved"].setdefault(ov, []).append({"to": nv, "name": name, "overlap": round(ol, 1)})
    change = {f"{slug}/{nv}": name for nv, (ol, name, ov) in best.items()}
    for ov in mine:
        key = f"{slug}/{ov}"
        if key not in change:
            change[key] = None
    change = {k: v for k, v in change.items() if (labels.get(k) or {}).get("name") != v}
    if change:
        api({"set": change})
    write_json(os.path.join(dd, "relabel.json"), report)
    log("relabel", slug, f"{len(report['moved'])} moved, {len(report['dropped'])} dropped,",
        f"{len(report['conflicts'])} conflicts")


def main():
    labels = api()["labels"]
    cat = json.load(open(os.path.join(WORK, "catalog.json")))
    only = set(sys.argv[1:])
    for e in cat:
        if not only or e["slug"] in only:
            try:
                run(e["slug"], labels)
            except Exception as ex:
                log("relabel failed", e["slug"], repr(ex)[:300])
    log("all done")


if __name__ == "__main__":
    main()
