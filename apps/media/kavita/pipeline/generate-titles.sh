#!/usr/bin/env bash
# Walk the Robinson Curriculum source tree and emit a first-pass titles.tsv.
# Columns: disc<TAB>folder<TAB>series<TAB>title
# Every row starts with series=Uncategorized and title=folder-as-title;
# edit the file by hand, then run-pipeline.sh uses it as input.
#
# Usage:
#   ./generate-titles.sh [SRC_ROOT] > titles.tsv
#   ./generate-titles.sh > titles.tsv     # uses default mount path
set -euo pipefail

SRC_ROOT="${1:-/Volumes/Homeschool/Robinson Self-Teaching Home School Curriculum}"

if [[ ! -d "$SRC_ROOT" ]]; then
  echo "Source root not found: $SRC_ROOT" >&2
  exit 1
fi

printf "disc\tfolder\tseries\ttitle\n"

shopt -s nullglob
for disc_path in "$SRC_ROOT"/Disc\ *; do
  disc=$(basename "$disc_path")
  for sub in "$disc_path"/*/; do
    folder=$(basename "$sub")
    # A "book folder" is any directory that directly contains .TIF pages.
    # Disc 13 and 14 nest one level deeper (1CWRSTR / 2CWRSTR) -- handle both.
    if compgen -G "$sub/*.TIF" > /dev/null; then
      pretty=$(echo "$folder" | tr '_' ' ')
      printf "%s\t%s\tUncategorized\t%s\n" "$disc" "$folder" "$pretty"
    else
      for inner in "$sub"*/; do
        [[ -d "$inner" ]] || continue
        if compgen -G "$inner/*.TIF" > /dev/null; then
          inner_name=$(basename "$inner")
          rel="$folder/$inner_name"
          pretty=$(echo "$inner_name" | tr '_' ' ')
          printf "%s\t%s\tUncategorized\t%s\n" "$disc" "$rel" "$pretty"
        fi
      done
    fi
  done
done
