#!/usr/bin/env bash
#
# Publish the generated map to the `gh-pages` branch so GitHub Pages serves it
# for free at  https://<user>.github.io/<repo>/
#
# The running service (lafayette911org.py) writes traffic_map.html and
# traffic_data.js locally every cycle; this script copies those two files onto
# a dedicated `gh-pages` branch and force-pushes it. Force-pushing keeps that
# branch at a single commit, so the repository never bloats with the data
# file's churn.
#
# Run it on a timer (cron or a systemd timer) — see README "Publish to GitHub
# Pages". It is safe to run every few minutes: it skips the push entirely when
# neither output file has changed since the last publish, so it never triggers
# a needless Pages rebuild.
#
# Configuration (all optional — sensible defaults):
#   LAF911_MAP_PATH     path to traffic_map.html   (default: repo/traffic_map.html)
#   LAF911_DATAJS_PATH  path to traffic_data.js    (default: repo/traffic_data.js)
#   PAGES_BRANCH        branch to publish to        (default: gh-pages)
#   PAGES_PUSH_URL      git URL to push to          (default: origin's URL)
#   PAGES_STAMP         change-detection stamp file (default: repo/.pages_last_hash)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAP_HTML="${LAF911_MAP_PATH:-$REPO_DIR/traffic_map.html}"
DATA_JS="${LAF911_DATAJS_PATH:-$REPO_DIR/traffic_data.js}"
META_JSON="${LAF911_META_PATH:-$(dirname "$DATA_JS")/traffic_meta.json}"
BRANCH="${PAGES_BRANCH:-gh-pages}"
STAMP="${PAGES_STAMP:-$REPO_DIR/.pages_last_hash}"

# Reuse the main repo's existing git authentication (SSH key or credential
# helper) by pushing to the same URL as its `origin` remote unless overridden.
PUSH_URL="${PAGES_PUSH_URL:-$(git -C "$REPO_DIR" remote get-url origin)}"

for f in "$MAP_HTML" "$DATA_JS"; do
  if [ ! -f "$f" ]; then
    echo "publish_pages: missing $f (has the service rendered yet?)" >&2
    exit 1
  fi
done

# Skip when nothing changed, so running on a tight timer never spams Pages builds.
NEW_HASH="$(cat "$MAP_HTML" "$DATA_JS" | sha256sum | cut -d' ' -f1)"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$NEW_HASH" ]; then
  echo "publish_pages: no change since last publish, skipping"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# GitHub Pages serves index.html at the site root; keep the data file beside it
# (the map references it by relative name, so this Just Works under the project
# subpath). .nojekyll tells Pages to serve the files verbatim.
cp "$MAP_HTML" "$WORK/index.html"
cp "$DATA_JS"  "$WORK/traffic_data.js"
# traffic_meta.json lets the page check for new data cheaply before pulling
# the full data file. Optional: older renders won't have produced it yet.
if [ -f "$META_JSON" ]; then
  cp "$META_JSON" "$WORK/traffic_meta.json"
fi
touch "$WORK/.nojekyll"

cd "$WORK"
git init -q
git checkout -q -b "$BRANCH"
git add -A
git \
  -c user.name="lafayette911-bot" \
  -c user.email="lafayette911-bot@users.noreply.github.com" \
  commit -q -m "Publish map $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -q --force "$PUSH_URL" "$BRANCH"

echo "$NEW_HASH" > "$STAMP"
echo "publish_pages: published $BRANCH -> $PUSH_URL"
