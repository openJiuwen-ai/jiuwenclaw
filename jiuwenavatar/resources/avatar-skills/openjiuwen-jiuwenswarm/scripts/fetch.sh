#!/usr/bin/env bash
# ---------------------------------------------------------------
# 按 reference 名拉取 openJiuwen jiuwenswarm 快照到 assets/<name>
#
# 用法:
#   bash scripts/fetch.sh 0.2.3
#   bash scripts/fetch.sh enterprise_kub
#   bash scripts/fetch.sh auto
#   bash scripts/fetch.sh          # 交互输入 name / auto
#
# 环境变量:
#   FORCE=1  单 name 模式：目标目录非空时仍尝试克隆
#
# Git 源解析:
#   - 优先读取 references/<name>.md 顶部的 <!-- git-ref: <branch-or-tag> -->
#   - 若无注释，则用 <name> 本身作为 git branch/tag
#
# auto 模式:
#   - 扫描 references/[0-9]*.md（语义化版本索引）
#   - 若存在 references/enterprise_kub.md，一并纳入
#   - 忽略 *-notes.md 等补充文档
#   - assets/<name> 已存在则跳过；不存在则拉取
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

# Asset / reference 文件名：允许字母数字、点、下划线、短横线（不可含路径分隔符）
name_safe() {
  local t="$1"
  [[ -n "$t" ]] && [[ "$t" =~ ^[A-Za-z0-9._-]+$ ]] && [[ "$t" != *".."* ]]
}

normalize_name() {
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

# 从 references/<name>.md 解析 <!-- git-ref: ... -->；缺省则返回 name 本身
resolve_git_ref() {
  local name="$1"
  local ref_file="$REFERENCES_ROOT/${name}.md"
  local git_ref=""
  if [[ -f "$ref_file" ]]; then
    git_ref="$(
      sed -nE 's/^<!--[[:space:]]*git-ref:[[:space:]]*([^[:space:]]+)[[:space:]]*-->[[:space:]]*$/\1/p' "$ref_file" \
        | head -n 1
    )"
  fi
  if [[ -z "$git_ref" ]]; then
    git_ref="$name"
  fi
  printf '%s' "$git_ref"
}

list_reference_names() {
  local ref="$REFERENCES_ROOT" f base
  [[ -d "$ref" ]] || { err "references 目录不存在: $ref"; exit 1; }
  shopt -s nullglob
  local files=("$ref"/[0-9]*.md)
  if [[ -f "$ref/enterprise_kub.md" ]]; then
    files+=("$ref/enterprise_kub.md")
  fi
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    err "references 中未找到可拉取的索引（[0-9]*.md / enterprise_kub.md）"
    exit 1
  fi
  for f in "${files[@]}"; do
    base="$(basename "$f" .md)"
    if name_safe "$base"; then
      printf '%s\n' "$base"
    fi
  done | awk 'NF' | awk '!seen[$0]++'
}

fetch_one() {
  local name="$1"
  local skip_if_exists="${2:-0}"
  local target_dir="$ASSETS_ROOT/$name"
  local git_ref head describe

  git_ref="$(resolve_git_ref "$name")"

  info "索引名: $name"
  info "Git 源: $git_ref"
  info "目标: $target_dir"

  if [[ -d "$target_dir" ]]; then
    if [[ "$skip_if_exists" == "1" ]]; then
      skip "$name — assets 已存在，跳过"
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

  info "正在浅克隆 $git_ref ..."
  if [[ -d "$target_dir" ]]; then
    git -C "$target_dir" clone --branch "$git_ref" --depth 1 --single-branch "$REPO_URL" .
  else
    git clone --branch "$git_ref" --depth 1 --single-branch "$REPO_URL" "$target_dir"
  fi

  head="$(git -C "$target_dir" rev-parse --short HEAD)"
  describe="$(git -C "$target_dir" describe --tags --exact-match 2>/dev/null || echo '(detached HEAD)')"
  ok "已拉取 $name（$git_ref）到 $target_dir"
  ok "HEAD: $head  $describe"
  return 0
}

run_auto() {
  local names=() n rc fetched=0 skipped=0 failed=0
  mapfile -t names < <(list_reference_names)

  info "auto 模式：自 references/ 发现 ${#names[@]} 个索引"
  info "仓库: $REPO_URL"
  info "names: $(IFS=,; echo "${names[*]}")"

  for n in "${names[@]}"; do
    echo ""
    info "---- $n ----"
    if fetch_one "$n" 1; then
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

NAME="${1:-}"
if [[ -z "$NAME" ]]; then
  read -rp "请输入索引名 (例如 0.2.3 / enterprise_kub) 或 auto: " NAME
fi
NAME="$(normalize_name "$NAME")"

command -v git >/dev/null 2>&1 || { err "未找到 git，请先安装"; exit 1; }
mkdir -p "$ASSETS_ROOT"

if [[ "$(echo "$NAME" | tr '[:upper:]' '[:lower:]')" == "auto" ]]; then
  run_auto
  exit $?
fi

if ! name_safe "$NAME"; then
  err "无效的索引名: ${NAME:-<empty>}（仅允许字母数字 . _ -，不可含路径分隔符）"
  exit 1
fi

info "仓库: $REPO_URL"
fetch_one "$NAME" 0
