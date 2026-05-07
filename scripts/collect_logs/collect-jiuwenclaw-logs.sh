#!/usr/bin/env bash
# jiuwenclaw 日志采集：将运行日志 (.logs) 与可选 session 目录打成 tar.gz。
# 仓库路径：scripts/collect_logs/（Windows 原生见同目录 collect-jiuwenclaw-logs.ps1）。
# 设计见 docs/design/jiuwenclaw日志采集脚本设计文档.md
#
# 依赖: bash, tar, mkdir, cp, rm, mktemp, sort, date
# Git Bash / WSL / Linux / macOS 可用；macOS 使用 BSD stat。

set -euo pipefail

readonly SCRIPT_VERSION="1.0.0"
readonly SCRIPT_NAME="$(basename "$0")"

# 默认
OPT_BASE=""
OPT_SERVICE="service_default"
OPT_AGENT="agent_default"
OPT_SESSIONS="1"
OPT_OUTPUT="."
OPT_PREFIX="jiuwenclaw-logs"
OPT_DRY_RUN=0
OPT_QUIET=0

usage() {
  sed -n '1,120p' <<'EOF'
用法:
  collect-jiuwenclaw-logs.sh [选项]

将 {base}/.logs 下符合白名单的文件复制到包内 runtime_logs/，并按规则复制 session 子目录到 sessions/。

选项:
  --base DIR           基目录（含 .logs 与 agent 目录的一层）。未指定时:
                       $HOME/.office-claw/.jiuwenclaw/<--service>
  --service NAME       与默认根拼接的服务名，默认 service_default
  --agent NAME         agent 目录名，默认 agent_default
  --sessions SPEC      会话选择，默认 1（仅最新一个）
                       - 正整数 N: 第 N 新（1=最新）
                       - N-M: 闭区间，须 N<=M（禁止 3-1）
                       - all: 全部 officeclaw_* 目录（按新→旧）
  --output DIR         压缩包输出目录，默认当前目录
  --prefix STR         文件名前缀，默认 jiuwenclaw-logs
  --dry-run            只打印将纳入的路径，不生成压缩包
  -q, --quiet          少打印过程信息
  -h, --help           显示本帮助

示例（可先 cd 至本脚本所在目录 scripts/collect_logs）:
  ./collect-jiuwenclaw-logs.sh
  ./collect-jiuwenclaw-logs.sh --base "$HOME/.office-claw/.jiuwenclaw/service_default"
  ./collect-jiuwenclaw-logs.sh --sessions 1-3 --output /tmp

说明:
  - 序号按目录创建时间新→旧编号（不支持时用修改时间），1 为最新。
  - 隐私白名单：.logs 仅打包后缀为 .log 的文件（保持相对路径）；每个 session 目录仅复制根目录下
    history.json、metadata.json（存在则拷）。
  - sessions 为空或无可拷贝的 session 文件时仍成功（只要 .logs 目录存在），包内含 SESSIONS_NOTE.txt。
  - .logs 不存在则失败退出。
EOF
}

log_info() {
  if [[ "$OPT_QUIET" -eq 0 ]]; then
    printf '%s\n' "$*" >&2
  fi
}

log_warn() {
  printf '[WARN] %s\n' "$*" >&2
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

# 返回目录 mtime（秒），兼容 GNU stat 与 BSD stat
dir_mtime() {
  local p="$1"
  if stat -c '%Y' "$p" >/dev/null 2>&1; then
    stat -c '%Y' "$p"
  else
    stat -f '%m' "$p"
  fi
}

# 返回目录用于排序的时间戳（秒）：优先创建时间（birth），不支持或未知时退回 mtime
dir_sort_time() {
  local p="$1"
  local bt
  if stat -c '%W' "$p" >/dev/null 2>&1; then
    bt="$(stat -c '%W' "$p")"
    if [[ -n "$bt" && "$bt" != 0 ]]; then
      printf '%s' "$bt"
      return
    fi
  fi
  if stat -f '%B' "$p" >/dev/null 2>&1; then
    bt="$(stat -f '%B' "$p")"
    if [[ -n "$bt" && "$bt" != 0 ]]; then
      printf '%s' "$bt"
      return
    fi
  fi
  dir_mtime "$p"
}

resolve_base() {
  if [[ -n "$OPT_BASE" ]]; then
    printf '%s' "$(cd "$OPT_BASE" && pwd)"
    return
  fi
  local home="${HOME:-}"
  [[ -n "$home" ]] || die "未设置 HOME，请使用 --base 指定基目录"
  printf '%s' "$(cd "$home/.office-claw/.jiuwenclaw/$OPT_SERVICE" && pwd)"
}

# 将 sessions 目录下 officeclaw_* 按创建时间（birth；不支持则 mtime）降序写入文件，每行一个绝对路径
list_sessions_sorted() {
  local sessions_root="$1"
  local out_file="$2"
  : >"$out_file"
  if [[ ! -d "$sessions_root" ]]; then
    return 0
  fi
  shopt -s nullglob
  local d
  for d in "$sessions_root"/officeclaw_*; do
    [[ -d "$d" ]] || continue
    local st
    st="$(dir_sort_time "$d")"
    printf '%s\t%s\n' "$st" "$d"
  done | sort -t $'\t' -k1,1nr | cut -f2- >>"$out_file"
  shopt -u nullglob
}

# 解析 --sessions，输出选中的行号列表（1-based 对应 sorted 文件行号）到 stdout，每行一个行号
# 或输出单词 ALL
parse_sessions_spec() {
  local spec="$1"
  local s_lc
  s_lc="$(printf '%s' "$spec" | tr '[:upper:]' '[:lower:]')"
  if [[ "$s_lc" == "all" ]]; then
    echo "ALL"
    return
  fi
  if [[ "$spec" =~ ^([0-9]+)-([0-9]+)$ ]]; then
    local a="${BASH_REMATCH[1]}"
    local b="${BASH_REMATCH[2]}"
    if (( a > b )); then
      die "非法区间 \"$spec\"：序号须从小到大（新→旧编号），禁止逆序如 3-1"
    fi
    local i
    for (( i = a; i <= b; i++ )); do
      echo "$i"
    done
    return
  fi
  if [[ "$spec" =~ ^[0-9]+$ ]]; then
    echo "$spec"
    return
  fi
  die "无法解析 --sessions \"$spec\"（支持: 正整数、N-M、all）"
}

cleanup() {
  local st="${1:-}"
  if [[ -n "${STAGE_DIR:-}" && -d "$STAGE_DIR" ]]; then
    rm -rf "$STAGE_DIR"
  fi
  if [[ -n "${SORTED_FILE:-}" && -f "$SORTED_FILE" ]]; then
    rm -f "$SORTED_FILE"
  fi
}
trap 'cleanup' EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      [[ $# -ge 2 ]] || die "--base 需要参数"
      OPT_BASE="$2"
      shift 2
      ;;
    --service)
      [[ $# -ge 2 ]] || die "--service 需要参数"
      OPT_SERVICE="$2"
      shift 2
      ;;
    --agent)
      [[ $# -ge 2 ]] || die "--agent 需要参数"
      OPT_AGENT="$2"
      shift 2
      ;;
    --sessions)
      [[ $# -ge 2 ]] || die "--sessions 需要参数"
      OPT_SESSIONS="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || die "--output 需要参数"
      OPT_OUTPUT="$2"
      shift 2
      ;;
    --prefix)
      [[ $# -ge 2 ]] || die "--prefix 需要参数"
      OPT_PREFIX="$2"
      shift 2
      ;;
    --dry-run)
      OPT_DRY_RUN=1
      shift
      ;;
    -q|--quiet)
      OPT_QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1（使用 --help）"
      ;;
  esac
done

BASE="$(resolve_base)" || exit 1
LOGS_DIR="$BASE/.logs"
SESSIONS_DIR="$BASE/$OPT_AGENT/agent/sessions"

[[ -d "$LOGS_DIR" ]] || die "运行日志目录不存在: $LOGS_DIR（请检查 --base / --service）"

SORTED_FILE="$(mktemp)"
list_sessions_sorted "$SESSIONS_DIR" "$SORTED_FILE" || true

SESSION_LIST=()
while IFS= read -r __sl || [[ -n "${__sl:-}" ]]; do
  [[ -z "${__sl:-}" ]] && continue
  SESSION_LIST+=("$__sl")
done <"$SORTED_FILE"
TOTAL_SESSIONS="${#SESSION_LIST[@]}"
if [[ ! -d "$SESSIONS_DIR" ]]; then
  SESSION_ABSENT_REASON="sessions 目录不存在: $SESSIONS_DIR"
elif [[ "$TOTAL_SESSIONS" -eq 0 ]]; then
  SESSION_ABSENT_REASON="sessions 目录下无 officeclaw_* 子目录: $SESSIONS_DIR"
else
  SESSION_ABSENT_REASON=""
fi

# 解析要采集的 session 路径（可能为空）
SELECTED_SESSION_PATHS=()
parse_result="$(parse_sessions_spec "$OPT_SESSIONS")" || exit 1

parse_first="$(printf '%s' "$parse_result" | head -n1 | tr -d '\r')"
if [[ "$parse_first" == "ALL" ]]; then
  for (( i = 0; i < TOTAL_SESSIONS; i++ )); do
    SELECTED_SESSION_PATHS+=("${SESSION_LIST[$i]}")
  done
else
  wanted_sorted="$(printf '%s\n' "$parse_result" | sort -n | uniq)"
  max_wanted=0
  while IFS= read -r line || [[ -n "${line:-}" ]]; do
    [[ -z "${line:-}" ]] && continue
    [[ "$line" =~ ^[0-9]+$ ]] || continue
    if (( line > max_wanted )); then
      max_wanted="$line"
    fi
  done <<<"$wanted_sorted"

  if (( max_wanted > 0 && TOTAL_SESSIONS > 0 && max_wanted > TOTAL_SESSIONS )); then
    log_warn "请求的 session 序号最大为 $max_wanted，当前仅有 $TOTAL_SESSIONS 个；将只打包存在的序号。"
  fi

  while IFS= read -r idx || [[ -n "${idx:-}" ]]; do
    [[ -z "${idx:-}" ]] && continue
    [[ "$idx" =~ ^[0-9]+$ ]] || continue
    if (( idx >= 1 )); then
      arr_idx=$((idx - 1))
      if (( arr_idx < TOTAL_SESSIONS )); then
        SELECTED_SESSION_PATHS+=("${SESSION_LIST[$arr_idx]}")
      fi
    fi
  done <<<"$wanted_sorted"
fi

# dry-run
if [[ "$OPT_DRY_RUN" -eq 1 ]]; then
  log_info "BASE=$BASE"
  log_info "LOGS_DIR=$LOGS_DIR"
  log_info "--- runtime_logs（仅 *.log，最多 200 条路径）---"
  find "$LOGS_DIR" -type f -iname '*.log' -print 2>/dev/null | head -n 200
  _dry_c="$(find "$LOGS_DIR" -type f -iname '*.log' 2>/dev/null | wc -l | tr -d ' ')"
  log_info "(符合白名单的 .log 文件数: $_dry_c)"
  log_info "--- sessions (selected) ---"
  if [[ ${#SELECTED_SESSION_PATHS[@]} -eq 0 ]]; then
    log_info "(无)"
  else
    _dry_i=1
    for _dry_p in "${SELECTED_SESSION_PATHS[@]}"; do
      log_info "$_dry_i $_dry_p"
      _dry_i=$((_dry_i + 1))
    done
  fi
  exit 0
fi

STAGE_DIR="$(mktemp -d)"

TS="$(date +%Y%m%d_%H%M%S)"
BUNDLE_NAME="${OPT_PREFIX}_${TS}"
BUNDLE_ROOT="$STAGE_DIR/$BUNDLE_NAME"
mkdir -p "$BUNDLE_ROOT"

mkdir -p "$BUNDLE_ROOT/runtime_logs"
# 复制 .logs 下仅 *.log（保持相对路径）
LOGS_ABS="$(cd "$LOGS_DIR" && pwd)" || die "无法进入日志目录: $LOGS_DIR"
while IFS= read -r -d '' _lf; do
  _rel="${_lf#"${LOGS_ABS}/"}"
  _dest="$BUNDLE_ROOT/runtime_logs/$_rel"
  mkdir -p "$(dirname "$_dest")"
  cp -a "$_lf" "$_dest"
done < <(find "$LOGS_ABS" -type f -iname '*.log' -print0)

INCLUDE_SESSION_COUNT=0
SESSION_LINES_FOR_MANIFEST=""
if [[ ${#SELECTED_SESSION_PATHS[@]} -gt 0 ]]; then
  for _sess_path in "${SELECTED_SESSION_PATHS[@]}"; do
    [[ -d "$_sess_path" ]] || continue
    _sess_bn="$(basename "$_sess_path")"
    _sess_out="$BUNDLE_ROOT/sessions/$_sess_bn"
    _sess_copied=0
    for _jf in history.json metadata.json; do
      if [[ -f "$_sess_path/$_jf" ]]; then
        mkdir -p "$_sess_out"
        cp -a "$_sess_path/$_jf" "$_sess_out/$_jf"
        _sess_copied=1
      fi
    done
    if [[ "$_sess_copied" -eq 1 ]]; then
      INCLUDE_SESSION_COUNT=$((INCLUDE_SESSION_COUNT + 1))
      SESSION_LINES_FOR_MANIFEST+="$(printf '  - %s\n' "$_sess_path")"
    fi
  done
fi

NOTE_PATH="$BUNDLE_ROOT/SESSIONS_NOTE.txt"
if [[ "$INCLUDE_SESSION_COUNT" -eq 0 ]]; then
  {
    echo "本次归档未包含任何 session 目录数据。"
    echo
    if [[ -n "$SESSION_ABSENT_REASON" ]]; then
      echo "原因: $SESSION_ABSENT_REASON"
    else
      echo "原因: 按 --sessions=$OPT_SESSIONS 筛选后没有可用的 session（可能序号超出当前数量）。"
      echo "当前按创建时间新→旧共检测到 ${TOTAL_SESSIONS} 个 officeclaw_* 目录。"
    fi
    echo
    echo "运行日志仍已按设计打包在 runtime_logs/ 下。"
  } >"$NOTE_PATH"
fi

ARCHIVE_NAME="${BUNDLE_NAME}.tar.gz"
OUT_DIR="$(cd "$OPT_OUTPUT" && pwd)"
ARCHIVE_PATH="$OUT_DIR/$ARCHIVE_NAME"

{
  echo "jiuwenclaw 日志采集 MANIFEST"
  echo "script_version: $SCRIPT_VERSION"
  echo "privacy_logs_whitelist: '*.log' files only (case-insensitive), relative paths preserved"
  echo "privacy_session_files: history.json, metadata.json at session dir root only"
  echo "session_sort: creation_time (fallback: mtime)"
  echo "created_local: $(date -Iseconds 2>/dev/null || date)"
  echo "base: $BASE"
  echo "logs_dir: $LOGS_DIR"
  echo "sessions_dir: $SESSIONS_DIR"
  echo "sessions_spec: $OPT_SESSIONS"
  echo "sessions_detected: $TOTAL_SESSIONS"
  echo "sessions_included: $INCLUDE_SESSION_COUNT"
  echo
  echo "session_dirs_sorted_newest_first:"
  _mf_si=1
  for _mf_sp in "${SESSION_LIST[@]}"; do
    printf '  %d %s\n' "$_mf_si" "$_mf_sp"
    _mf_si=$((_mf_si + 1))
  done
  echo
  echo "session_dirs_included:"
  if [[ "$INCLUDE_SESSION_COUNT" -eq 0 ]]; then
    echo "  (none)"
  else
    printf '%s' "$SESSION_LINES_FOR_MANIFEST"
  fi
} >"$BUNDLE_ROOT/MANIFEST.txt"

if [[ "$INCLUDE_SESSION_COUNT" -eq 0 ]]; then
  # NOTE 已在 BUNDLE_ROOT 根
  :
else
  rm -f "$NOTE_PATH" 2>/dev/null || true
fi

log_info "正在打包: $ARCHIVE_PATH"
# 顶层仅一个目录 BUNDLE_NAME，其内含 runtime_logs、MANIFEST.txt、可选 sessions 与 SESSIONS_NOTE.txt
tar -czf "$ARCHIVE_PATH" -C "$STAGE_DIR" "$BUNDLE_NAME" || die "tar 打包失败"

log_info "完成: $ARCHIVE_PATH"
log_info "包含 session 目录数: $INCLUDE_SESSION_COUNT"

# 成功退出前清理暂存（trap 也会执行）
rm -rf "$STAGE_DIR"
STAGE_DIR=""
rm -f "$SORTED_FILE"
SORTED_FILE=""
trap - EXIT
exit 0
