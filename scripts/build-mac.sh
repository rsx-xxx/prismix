#!/usr/bin/env bash
# Build Prismix.app on this Mac, the same way the release workflow does, and put it in /Applications.
#   scripts/build-mac.sh            build, self-test, ad-hoc sign -> dist/Prismix.app
#   scripts/build-mac.sh --install  also quit the running app, replace /Applications/Prismix.app and start it again
set -euo pipefail
cd "$(dirname "$0")/.."

command -v deno >/dev/null || echo "note: deno not found, the build may skip its YouTube helper (brew install deno)" >&2
uv sync -q --group desktop
uv run --group desktop python packaging/gen_icon.py
uv run --group desktop python packaging/build.py
dist/Prismix.app/Contents/MacOS/Prismix --selftest
codesign --force --deep --timestamp=none -s - dist/Prismix.app

if [ "${1:-}" = "--install" ]; then
  osascript -e 'tell application "Prismix" to quit' 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -x Prismix >/dev/null || break; sleep 1; done
  pkill -x Prismix 2>/dev/null || true
  rm -rf /Applications/Prismix.app
  ditto dist/Prismix.app /Applications/Prismix.app
  xattr -dr com.apple.quarantine /Applications/Prismix.app 2>/dev/null || true
  open /Applications/Prismix.app
  echo "installed $(uv run python -c 'import prismix;print(prismix.__version__)' 2>/dev/null || true) to /Applications/Prismix.app"
fi
