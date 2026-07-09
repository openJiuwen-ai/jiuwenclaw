#!/usr/bin/env bash
# JiuwenAvatar macOS .app + .dmg build script

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="JiuwenAvatar.app"
APP_PATH="$PROJECT_ROOT/dist/$APP_NAME"
DMG_ROOT="$PROJECT_ROOT/dist/dmg-root"
# Read version from pyproject.toml (single source of truth)
VERSION=$(uv run python -c "
import tomllib
print(tomllib.load(open('$PROJECT_ROOT/pyproject.toml','rb'))['project']['version'])
")
DMG_PATH="$PROJECT_ROOT/dist/JiuwenAvatar-$VERSION.dmg"

printf '=== JiuwenAvatar macOS package build ===\n'
printf 'Project root: %s\n\n' "$PROJECT_ROOT"

printf '[1/4] Install Python dependencies (uv sync --extra dev)...\n'
uv sync --extra dev

printf '\n[2/4] Build frontend (jiuwenavatar/channels/web/frontend)...\n'
rm -rf "$PROJECT_ROOT/jiuwenavatar/web/dist"
pushd "$PROJECT_ROOT/jiuwenavatar/channels/web/frontend" >/dev/null
npm install
npm run build
popd >/dev/null

printf '\n[3/4] Build macOS app bundle with PyInstaller...\n'
uv run pyinstaller scripts/jiuwenavatar.spec --noconfirm

if [[ ! -d "$APP_PATH" ]]; then
  printf 'Error: app bundle not found: %s\n' "$APP_PATH" >&2
  exit 1
fi

printf '\n[4/4] Create DMG...\n'
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "$APP_PATH" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
rm -f "$DMG_PATH"
hdiutil create -volname "JiuwenAvatar" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_PATH"

printf '\n=== Build complete ===\n'
printf 'App bundle: %s\n' "$APP_PATH"
printf 'DMG file:   %s\n' "$DMG_PATH"
