#!/bin/sh
# Mirror a private GitHub repo into the Forgejo brain repo
# under <mirror dir>. One commit per upstream change; no-op otherwise.
set -eu
umask 077

SRC="${SRC_REPO:-git@github.com:<org>/<repo>.git}"
SRC_BRANCH="${SRC_BRANCH:-main}"
DEST="${DEST_DIR:-<mirror dir>}"
BRAIN="${BRAIN_URL:-http://forgejo.forgejo.svc.cluster.local/WhiteHouse/brain.git}"
WORK="${WORK_DIR:-/tmp/work}"
# Subfolders other writers own (brain-sync renders HubSpot here); never replaced.
KEEP="${KEEP_DIRS:-HubSpot}"

export HOME="$WORK/home"
mkdir -p "$HOME"
cp "${DEPLOY_KEY_FILE:-/secrets/deploy-key}" "$HOME/id"
chmod 600 "$HOME/id"
export GIT_SSH_COMMAND="ssh -i $HOME/id -o IdentitiesOnly=yes -o UserKnownHostsFile=${KNOWN_HOSTS:-/config/known_hosts} -o StrictHostKeyChecking=yes"
git config --global user.name ww-mirror
git config --global user.email brain-sync@whitematter.tech
git config --global credential.helper '!f() { echo username=brain-sync; echo "password=$FORGEJO_TOKEN"; }; f'

head=$(git ls-remote "$SRC" "refs/heads/$SRC_BRANCH" | cut -f1)
[ -n "$head" ] || { echo "cannot read $SRC"; exit 1; }

# Only the mirrored folder is checked out; the rest of the repo stays untouched.
rm -rf "$WORK/brain" "$WORK/src"
git clone -q --depth 1 --filter=blob:none --no-checkout "$BRAIN" "$WORK/brain"
cd "$WORK/brain"
git sparse-checkout set --no-cone "/$DEST/"
git checkout -q

if [ "$(cat "$DEST/.source-commit" 2>/dev/null || true)" = "$head" ]; then
  echo "up to date at $head"
  exit 0
fi

git clone -q --depth 1 --branch "$SRC_BRANCH" "$SRC" "$WORK/src"
subject=$(git -C "$WORK/src" log -1 --format=%s)
mkdir -p "$DEST"
for entry in "$DEST"/* "$DEST"/.[!.]*; do
  [ -e "$entry" ] || continue
  case " $KEEP " in *" ${entry##*/} "*) continue ;; esac
  rm -rf "$entry"
done
excludes=""
for k in $KEEP; do excludes="$excludes --exclude=./$k"; done
# shellcheck disable=SC2086
(cd "$WORK/src" && tar --exclude=./.git --exclude=./.github --exclude='*.DS_Store' $excludes -cf - .) | tar -xf - -C "$DEST"
cat > "$DEST/.source-commit" <<EOT
$head
EOT

git add -A -- "$DEST"
if git diff --cached --quiet; then
  echo "no content change at $head"
  exit 0
fi
git commit -q -m "mirror: sync $(echo "$head" | cut -c1-8)" -m "$subject"
for attempt in 1 2 3; do
  if git push -q origin HEAD:main; then
    echo "mirrored $head ($subject)"
    exit 0
  fi
  git pull -q --rebase origin main
done
echo "push failed"
exit 1
