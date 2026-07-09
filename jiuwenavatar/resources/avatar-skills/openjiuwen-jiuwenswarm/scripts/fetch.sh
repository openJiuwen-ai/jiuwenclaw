#!/usr/bin/env bash
# ---------------------------------------------------------------
# 按 tag 拉取 openJiuwen jiuwenswarm 快照到 assets/<tag>
#
# 用法:
#   bash scripts/fetch.sh 0.2.0
#   bash scripts/fetch.sh auto
#   bash scripts/fetch.sh          # 交互输入 tag / auto
#
# 环境变量:
#   FORCE=1  单 tag 模式：目标目录非空时仍尝试克隆
#
# auto 模式:
#   - 从 references/ 读取 [0-9]*.md（版本索引），文件名（去 .md）即 tag
#   - 忽略 references/ 下非 [0-9]*.md 的补充文档（如 jiuwenswarm-sdk-notes.md）
#   - assets/<tag> 已存在则跳过；不存在则拉取
# ---------------------------------------------------------------

set -euo pipefail

REPO_URL="https://gitcode.com/openJiuwen/jiuwenswarm.git"
FORCE="${FORCE:-0}"

info()  { printf '\033[36m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[32m[OK]\033[0m    %s\n' "$*"; }
err()   { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; }
skip()  { printf '\033[33m[SKIP]\033[0m  %s\n' "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ASSETS_ROOT="$PACKAGE_ROOT/assets"
REFERENCES_ROOT="$PACKAGE_ROOT/references"

tag_safe() {
  local t="$1"
  [[ -n "$t" ]] && [[ "$t" != *".."* ]] && [[ "$t" != *"/"* ]] && [[ "$t" != *"\\"* ]]
}

normalize_tag() {
  local t="$1"
  t="$(echo "$t" | xargs)"
  if [[ "$t" =~ ^[vV]([0-9].*)$ ]]; then
    t="${BASH_REMATCH[1]}"
  fi
  printf '%s' "$t"
}

dir_empty() {
  local path="$1"
  shopt -s dotglob nullglob
  local entries=("$path"/*)
  shopt -u dotglob nullglob
  [[ ! -d "$path" ]] || [[ ${#entries[@]} -eq 0 ]]
}

list_reference_tags() {
  local ref="$REFERENCES_ROOT" f base
  [[ -d "$ref" ]] || { err "references 目录不存在: $ref"; exit 1; }
  shopt -s nullglob
  local files=("$ref"/[0-9]*.md)
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    err "references 中未找到 [0-9]*.md 版本索引，无法执行 auto"
    exit 1
  fi
  for f in "${files[@]}"; do
    base="$(basename "$f" .md)"
    if tag_safe "$base"; then
      printf '%s\n' "$base"
    fi
  done | sort -V
}

fetch_one_tag() {
  local tag="$1"
  local skip_if_exists="${2:-0}"
  local target_dir="$ASSETS_ROOT/$tag"
  local head describe

  info "标签: $tag"
  info "目标: $target_dir"

  if [[ -d "$target_dir" ]]; then
    if [[ "$skip_if_exists" == "1" ]]; then
      skip "$tag — assets 已存在，跳过"
      return 2
    fi
    if ! dir_empty "$target_dir"; then
      if [[ "$FORCE" != "1" ]]; then
        err "目标目录已存在且非空: $target_dir。请删除后重试，或设置 FORCE=1。"
        return 1
      fi
      info "目标目录非空，FORCE=1，继续尝试克隆..."
    fi
  fi

  info "正在浅克隆 tag $tag ..."
  if [[ -d "$target_dir" ]]; then
    git -C "$target_dir" clone --branch "$tag" --depth 1 --single-branch "$REPO_URL" .
  else
    git clone --branch "$tag" --depth 1 --single-branch "$REPO_URL" "$target_dir"
  fi

  head="$(git -C "$target_dir" rev-parse --short HEAD)"
  describe="$(git -C "$target_dir" describe --tags --exact-match 2>/dev/null || echo '(detached HEAD)')"
  ok "已拉取 $tag 到 $target_dir"
  ok "HEAD: $head  $describe"
  return 0
}

run_auto() {
  local tags=() t rc fetched=0 skipped=0 failed=0
  mapfile -t tags < <(list_reference_tags)

  info "auto 模式：自 references/ 发现 ${#tags[@]} 个 tag"
  info "仓库: $REPO_URL"
  info "tags: $(IFS=,; echo "${tags[*]}")"

  for t in "${tags[@]}"; do
    echo ""
    info "---- $t ----"
    if fetch_one_tag "$t" 1; then
      ((fetched++)) || true
    else
      rc=$?
      if [[ $rc -eq 2 ]]; then
        ((skipped++)) || true
      else
        ((failed++)) || true
      fi
    fi
  done

  echo ""
  info "auto 完成：拉取 $fetched，跳过 $skipped，失败 $failed"
  [[ "$failed" -eq 0 ]]
}

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  read -rp "请输入 tag (例如 0.2.0) 或 auto: " TAG
fi
TAG="$(normalize_tag "$TAG")"

command -v git >/dev/null 2>&1 || { err "未找到 git，请先安装"; exit 1; }
mkdir -p "$ASSETS_ROOT"

if [[ "$(echo "$TAG" | tr '[:upper:]' '[:lower:]')" == "auto" ]]; then
  run_auto
  exit $?
fi

if ! tag_safe "$TAG"; then
  err "无效的 tag: ${TAG:-<empty>}（不可包含路径分隔符或 ..）"
  exit 1
fi

info "仓库: $REPO_URL"
fetch_one_tag "$TAG" 0
