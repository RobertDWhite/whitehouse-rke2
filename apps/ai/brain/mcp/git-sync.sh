#!/bin/sh
# Keeps a read-only clone of $REPO_URL ($BRANCH) in $REPO_DIR for brain-mcp,
# polling every $SYNC_INTERVAL_SECONDS. After each change it writes
#   $META_DIR/HEAD     "<sha> <commit unix time>"
#   $META_DIR/lastmod  `git log --format=@%ct --name-only` (newest first)
# which brain-mcp uses to reload and to date notes by their last commit.
# The token is passed per command as an HTTP header and never written to disk.
set -u
REPO_DIR=${REPO_DIR:-/work/repo}
META_DIR=${META_DIR:-/work/meta}
BRANCH=${BRANCH:-main}
INTERVAL=${SYNC_INTERVAL_SECONDS:-120}
export HOME=/tmp GIT_TERMINAL_PROMPT=0

g() { git -c core.quotePath=false -c "http.extraHeader=Authorization: token ${FORGEJO_TOKEN}" "$@"; }

mkdir -p "$META_DIR"
last=""
while :; do
  if [ ! -d "$REPO_DIR/.git" ]; then
    rm -rf "$REPO_DIR"
    if ! g clone -q --single-branch --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"; then
      echo "$(date -Iseconds) clone failed; retrying in 30s"
      sleep 30
      continue
    fi
  elif ! { g -C "$REPO_DIR" fetch -q origin "$BRANCH" && g -C "$REPO_DIR" reset -q --hard FETCH_HEAD; }; then
    echo "$(date -Iseconds) fetch failed; keeping $(git -C "$REPO_DIR" rev-parse --short HEAD)"
  fi
  head=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || true)
  if [ -n "$head" ] && [ "$head" != "$last" ]; then
    if g -C "$REPO_DIR" log --format=@%ct --name-only --no-renames > "$META_DIR/lastmod.tmp"; then
      mv "$META_DIR/lastmod.tmp" "$META_DIR/lastmod"
      echo "$head $(git -C "$REPO_DIR" log -1 --format=%ct)" > "$META_DIR/HEAD.tmp"
      mv "$META_DIR/HEAD.tmp" "$META_DIR/HEAD"
      echo "$(date -Iseconds) at $head"
      last=$head
    fi
  fi
  sleep "$INTERVAL"
done
