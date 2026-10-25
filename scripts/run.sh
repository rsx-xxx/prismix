#!/usr/bin/env bash
# Start Prismix from this checkout, after bringing it up to date.
#   scripts/run.sh            pull master, sync dependencies, serve on http://stems.localhost:8000
#   scripts/run.sh --port 9000   extra arguments go to `prismix`
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -n "${GITHUB_TOKEN:-}" ]; then
  auth=(-c "http.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64)")
else
  auth=()
fi
if [ -z "$(git status --porcelain --untracked-files=no)" ]; then
  git ${auth[@]+"${auth[@]}"} pull --ff-only -q || echo "pull skipped (offline or diverged), running the local copy" >&2
else
  echo "local changes present, not pulling" >&2
fi
uv sync -q
exec uv run prismix --open "$@"
