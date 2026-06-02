#!/usr/bin/env python3
"""
Drive the full TIFF -> OCR'd PDF conversion.

Reads titles.tsv (disc<TAB>folder<TAB>series<TAB>title), runs build-book.sh
for each row, with N workers in parallel. Idempotent: books whose output
already exists are skipped unless FORCE=1.

Env:
  SRC_ROOT   default /Volumes/Homeschool/Robinson Self-Teaching Home School Curriculum
  OUT_ROOT   default /Volumes/Homeschool/robinson-pdf-out
  TITLES     default ./titles.tsv (next to this script)
  PARALLEL   default 2
  OCR_JOBS   default 2 (passed through to each worker)
  FORCE      default 0 (1 to rebuild existing PDFs)
"""

from __future__ import annotations

import concurrent.futures
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_ROOT = Path(os.environ.get(
    "SRC_ROOT",
    "/Volumes/Homeschool/Robinson Self-Teaching Home School Curriculum",
))
OUT_ROOT = Path(os.environ.get("OUT_ROOT", "/Volumes/Homeschool/robinson-pdf-out"))
TITLES = Path(os.environ.get("TITLES", HERE / "titles.tsv"))
PARALLEL = int(os.environ.get("PARALLEL", "2"))
OCR_JOBS = os.environ.get("OCR_JOBS", "2")
FORCE = os.environ.get("FORCE", "0")
WORKER = HERE / "build-book.sh"


def sanitize(s: str) -> str:
    return s.replace("/", "_").strip()


def load_jobs() -> list[tuple[Path, Path, str]]:
    jobs: list[tuple[Path, Path, str]] = []
    with TITLES.open(newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if not row or row[0].strip() == "" or row[0].startswith("#"):
                continue
            if row[0] == "disc":
                continue
            if len(row) < 4:
                continue
            disc, folder, series, title = row[0], row[1], row[2], row[3]
            series = sanitize(series)
            title = sanitize(title)
            src = SRC_ROOT / disc / folder
            out = OUT_ROOT / series / f"{title}.pdf"
            label = f"{disc}/{folder}"
            jobs.append((src, out, label))
    return jobs


def run_one(job: tuple[Path, Path, str]) -> tuple[str, bool, str]:
    src, out, label = job
    env = os.environ.copy()
    env["OCR_JOBS"] = OCR_JOBS
    env["FORCE"] = FORCE
    try:
        proc = subprocess.run(
            [str(WORKER), str(src), str(out)],
            env=env,
            capture_output=True,
            text=True,
        )
    except Exception as e:  # noqa: BLE001
        return label, False, f"launch error: {e}"

    # Prefer stdout's last line as the summary, fall back to stderr.
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    summary = tail[-1] if tail else ""
    ok = proc.returncode == 0
    if not ok:
        summary = f"exit {proc.returncode}: {summary or (proc.stderr or '').strip()}"
    return label, ok, summary


def main() -> int:
    if not WORKER.exists():
        print(f"missing worker: {WORKER}", file=sys.stderr)
        return 1
    if not TITLES.exists():
        print(f"missing titles file: {TITLES}", file=sys.stderr)
        return 1
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    jobs = load_jobs()
    total = len(jobs)
    print(f"queueing {total} books, parallel={PARALLEL} ocr_jobs={OCR_JOBS}")
    print(f"src: {SRC_ROOT}")
    print(f"out: {OUT_ROOT}")
    print()

    start = time.time()
    completed = 0
    failures: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        for label, ok, summary in pool.map(run_one, jobs):
            completed += 1
            status = "ok " if ok else "FAIL"
            elapsed = time.time() - start
            pct = 100 * completed / total
            print(
                f"[{completed:3d}/{total}  {pct:5.1f}%  t+{elapsed/60:6.1f}m] "
                f"{status} {label}: {summary}",
                flush=True,
            )
            if not ok:
                failures.append((label, summary))

    dur = time.time() - start
    print()
    print(f"done in {dur/60:.1f} minutes. {len(failures)} failure(s).")
    for label, summary in failures:
        print(f"  FAIL {label}: {summary}")
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
