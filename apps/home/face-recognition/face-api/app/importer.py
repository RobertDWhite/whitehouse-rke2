"""Import example faces through the running API.

stdin: one "<subject>\\t<base64 image>" per line (e.g. exported from CompreFace's
Postgres). Usage inside the pod:  python -m app.importer < faces.tsv
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("IMPORT_URL", "http://127.0.0.1:8080")
KEY = os.environ.get("API_KEY", "")
THRESHOLD = os.environ.get("IMPORT_DET_THRESHOLD", "0.5")

ok = failed = 0
for n, line in enumerate(sys.stdin, 1):
    line = line.rstrip("\n")
    if not line:
        continue
    subject, b64 = line.split("\t", 1)
    body = json.dumps({"file": b64}).encode()
    q = urllib.parse.urlencode({"subject": subject, "det_prob_threshold": THRESHOLD})
    req = urllib.request.Request(
        f"{URL}/api/v1/recognition/faces?{q}",
        data=body,
        headers={"content-type": "application/json", "x-api-key": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            json.load(r)
            ok += 1
    except urllib.error.HTTPError as e:
        failed += 1
        print(f"line {n} subject={subject}: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
print(json.dumps({"imported": ok, "failed": failed}))
