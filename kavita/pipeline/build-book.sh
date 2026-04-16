#!/usr/bin/env bash
# Convert one book (a folder of .TIF pages) into a single OCR'd PDF.
#
# Args: <src_dir> <out_pdf>
#
# Safe to re-run: if <out_pdf> already exists and is non-empty it skips
# unless FORCE=1 is set.
set -euo pipefail

SRC="${1:?src_dir required}"
OUT="${2:?out_pdf required}"

if [[ ! -d "$SRC" ]]; then
  echo "skip (missing source): $SRC" >&2
  exit 0
fi

if [[ -s "$OUT" && "${FORCE:-0}" != "1" ]]; then
  echo "skip (exists): $OUT"
  exit 0
fi

mkdir -p "$(dirname "$OUT")"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# Collect and sort TIFFs deterministically by filename.
pages_list="$tmpdir/pages.list"
find "$SRC" -maxdepth 1 -type f \( -iname '*.tif' -o -iname '*.tiff' \) -print0 \
  | LC_ALL=C sort -z > "$pages_list"

page_count=$(tr -cd '\0' < "$pages_list" | wc -c | tr -d ' ')
if [[ "$page_count" -eq 0 ]]; then
  echo "skip (no TIFFs): $SRC" >&2
  exit 0
fi

merged="$tmpdir/merged.pdf"
echo "[$(date +%H:%M:%S)] merging ${page_count} pages -> $(basename "$OUT")"
xargs -0 img2pdf --output "$merged" < "$pages_list"

# OCR: deskew + cleanup, PDF/A output, skip pages that already have text
# (none will, but this makes the pass resumable/idempotent).
echo "[$(date +%H:%M:%S)] OCR -> $(basename "$OUT")"
ocrmypdf \
  --quiet \
  --skip-text \
  --deskew \
  --clean-final \
  --optimize 1 \
  --output-type pdfa \
  --jobs "${OCR_JOBS:-2}" \
  "$merged" "$OUT.partial"

mv "$OUT.partial" "$OUT"
echo "[$(date +%H:%M:%S)] done: $OUT"
