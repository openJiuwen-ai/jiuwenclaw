#!/usr/bin/env bash
# JiuwenSwarm macOS .app + .dmg build script

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="JiuwenSwarm.app"
APP_PATH="$PROJECT_ROOT/dist/$APP_NAME"
DMG_ROOT="$PROJECT_ROOT/dist/dmg-root"
VERSION="0.2.3"
DMG_PATH="$PROJECT_ROOT/dist/JiuwenSwarm-$VERSION.dmg"

# ── 内置 Node 运行时（单架构，M 系列优先 arm64）─────────────────
# 任选 ≥ v18 的 LTS；v20/v22 为推荐版本。可用环境变量覆盖。
NODE_VERSION="${NODE_VERSION:-v22.11.0}"
BUNDLE_NODE="${BUNDLE_NODE:-1}"   # 0=跳过内置 node

# 返回构建机的 Node 架构名。M 系列(arm64) 优先；Intel 回退 x64。
resolve_node_arch() {
  case "$(uname -m)" in
    arm64) echo "arm64" ;;
    *)     echo "x64"   ;;
  esac
}

# 校验某个目录是一份「能真正跑起来」的 Node：既有 bin/node，又能执行 --version。
# 比 [[ -x ... ]] 强：能挡住下载损坏 / 架构不匹配 / 只解压了一半的情况。
node_runs() {
  local n="$1/bin/node"
  [[ -x "$n" ]] && "$n" --version >/dev/null 2>&1
}

# 解析 Node 运行时来源，优先级：$NODE_DIR > vendor/node > nodejs.org 官方下载
resolve_node_dir() {
  if [[ -n "${NODE_DIR:-}" ]] && node_runs "$NODE_DIR"; then
    echo "$NODE_DIR"; return
  fi
  local vendored="$PROJECT_ROOT/vendor/node"
  if node_runs "$vendored"; then
    echo "$vendored"; return
  fi
  local arch dir url
  arch="$(resolve_node_arch)"
  dir="$vendored"
  printf 'Downloading Node %s (%s) from nodejs.org...\n' "$NODE_VERSION" "$arch" >&2
  url="https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-darwin-${arch}.tar.gz"
  mkdir -p "$PROJECT_ROOT/vendor"

  # 先把 tarball 下载、解压到临时目录，校验 bin/node 可执行后，
  # 再 rm -rf 旧缓存并原子替换。任一步失败都不会破坏已有 vendor/node。
  local tmpdir staged tarball
  tmpdir="$(mktemp -d)"
  tarball="$tmpdir/node.tar.gz"
  staged="$tmpdir/node-${NODE_VERSION}-darwin-${arch}"

  if ! curl -fL "$url" -o "$tarball"; then
    rm -rf "$tmpdir"
    printf 'Error: failed to download %s\n' "$url" >&2
    return 1
  fi

  tar -xzf "$tarball" -C "$tmpdir"
  if ! node_runs "$staged"; then
    rm -rf "$tmpdir"
    printf 'Error: extracted tarball has no working bin/node (%s)\n' "$staged" >&2
    return 1
  fi

  rm -rf "$dir"          # 仅在替换物校验通过后才删旧缓存
  mv "$staged" "$dir"
  if ! node_runs "$dir"; then   # 跨卷 mv 可能只搬了一半，落地后再校验一次
    rm -rf "$dir" "$tmpdir"
    printf 'Error: node moved into %s but bin/node does not run\n' "$dir" >&2
    return 1
  fi
  rm -rf "$tmpdir"
  echo "$dir"
}

# 把 Node 运行时拷进 .app 包的 Contents/Resources/node-runtime
copy_node_into_app() {
  local src="$1"
  local dest="$APP_PATH/Contents/Resources/node-runtime"
  # 先确认来源 node 真能跑，否则 cp 会吐出晦涩的 'No such file or directory'。
  if ! node_runs "$src"; then
    printf 'Error: node source not usable — %s/bin/node missing or not runnable\n' "$src" >&2
    printf '       if this is the cached vendor/node, remove it and re-run to re-download.\n' >&2
    return 1
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  # cp -R 保留 npx/npm 符号链接（指向 ../lib/node_modules/...）与可执行位。
  cp -R "$src/." "$dest/"
  printf 'Bundled Node %s (%s) into app: %s\n' \
    "$( "$dest/bin/node" --version )" "$(resolve_node_arch)" "$dest"
}

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

# 内置 Node 运行时：在打 DMG 之前暂存进 .app，随后 cp -R app 到 dmg-root
# 会把它一并卷进 DMG。
if [[ "$BUNDLE_NODE" == "1" ]]; then
  printf '\n[4.5/5] Bundle Node.js runtime (single arch, M-series first)...\n'
  NODE_SRC="$(resolve_node_dir)" || exit 1
  copy_node_into_app "$NODE_SRC"
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
