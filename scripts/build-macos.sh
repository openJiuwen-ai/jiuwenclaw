#!/usr/bin/env bash
# JiuwenSwarm macOS .app + .dmg build script

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="JiuwenSwarm.app"
APP_PATH="$PROJECT_ROOT/dist/$APP_NAME"
DMG_ROOT="$PROJECT_ROOT/dist/dmg-root"
VERSION="0.2.3.beta1"
DMG_PATH="$PROJECT_ROOT/dist/JiuwenSwarm-$VERSION.dmg"

printf '=== JiuwenSwarm macOS package build ===\n'
printf 'Project root: %s\n\n' "$PROJECT_ROOT"

printf '[1/5] Install Python dependencies (uv sync --extra dev)...\n'
uv sync --extra dev

printf '\n[2/5] Build frontend (jiuwenswarm/channels/web/frontend)...\n'
rm -rf "$PROJECT_ROOT/jiuwenswarm/web/dist"
pushd "$PROJECT_ROOT/jiuwenswarm/channels/web/frontend" >/dev/null
npm install
npm run build
popd >/dev/null

TUI_BINARY=""
printf '\n[3/5] Build TUI native binary (Bun)...\n'
if command -v bun &>/dev/null; then
  pushd "$PROJECT_ROOT/jiuwenswarm/channels/tui/frontend" >/dev/null
  bun install
  popd >/dev/null
  TUI_BINARY="$(uv run python scripts/build_tui.py --target current | tail -n1)"
  if [[ -z "$TUI_BINARY" ]]; then
    printf 'Warning: TUI build produced no output, skipping TUI.\n'
    TUI_BINARY=""
  else
    TUI_BINARY="$PROJECT_ROOT/$TUI_BINARY"
    if [[ ! -f "$TUI_BINARY" ]]; then
      printf 'Warning: TUI binary not found at %s, skipping TUI.\n' "$TUI_BINARY"
      TUI_BINARY=""
    fi
  fi
else
  printf 'Warning: bun not found, skipping TUI build.\n'
  printf 'Install bun: curl -fsSL https://bun.sh/install | bash\n'
fi

printf '\n[4/5] Build macOS app bundle with PyInstaller...\n'
uv run pyinstaller scripts/jiuwenswarm.spec --noconfirm

if [[ ! -d "$APP_PATH" ]]; then
  printf 'Error: app bundle not found: %s\n' "$APP_PATH" >&2
  exit 1
fi

if [[ -n "$TUI_BINARY" && -f "$TUI_BINARY" ]]; then
  printf 'Copying TUI binary into app bundle...\n'
  cp "$TUI_BINARY" "$APP_PATH/Contents/MacOS/jiuwenswarm-tui"
  chmod +x "$APP_PATH/Contents/MacOS/jiuwenswarm-tui"
fi

printf '\n[5/5] Create DMG...\n'
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "$APP_PATH" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
rm -f "$DMG_PATH"
hdiutil create -volname "JiuwenSwarm" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_PATH"

printf '\n=== Build complete ===\n'
printf 'App bundle: %s\n' "$APP_PATH"
printf 'DMG file:   %s\n' "$DMG_PATH"
