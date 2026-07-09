#!/usr/bin/env bash
# ============================================================================
# setup_coding_cli.sh — 检测并安装数字分身使用的「编码引擎 CLI」
#
# 用法:
#   ./scripts/setup_coding_cli.sh                 # 安装/检测 claude（默认）
#   ./scripts/setup_coding_cli.sh claude-code     # 安装 Claude Code CLI
#   ./scripts/setup_coding_cli.sh codex           # 安装 OpenAI Codex CLI
#   ./scripts/setup_coding_cli.sh all             # 两者都装
#
# 说明:
#   - 由 jiuwenavatar 运行时在「分身选择 claude-code / codex 后端且 CLI 缺失」时自动调用，
#     也可由用户手动运行。
#   - 自动识别国内网络环境，国内走 npm + 淘宝镜像，海外走官方安装器 > Homebrew > npm。
#   - 安装策略移植自 CodeReviewAvatar/scripts/setup_and_run.sh。
# ============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

TARGET="${1:-claude-code}"
NPM_MIRROR="https://registry.npmmirror.com"

# ── 国内网络检测 ─────────────────────────────────────────────────────────────
is_china_network() {
    local header
    header=$(curl -sI --connect-timeout 5 --max-time 8 https://claude.ai/install.sh 2>/dev/null | head -1)
    if echo "$header" | grep -qi "html\|unavailable\|region"; then return 0; fi
    local sample
    sample=$(curl -s --connect-timeout 5 --max-time 8 https://claude.ai/install.sh 2>/dev/null | head -c 100)
    if [ -n "$sample" ] && ! echo "$sample" | grep -q "^#!/"; then return 0; fi
    if echo "${LANG:-}${LC_ALL:-}" | grep -qi "zh_CN\|zh-Hans"; then return 0; fi
    return 1
}

refresh_path() { export PATH="$HOME/.local/bin:$HOME/.claude/bin:$(npm config get prefix 2>/dev/null)/bin:$PATH"; }

# ── 安装 Claude Code ─────────────────────────────────────────────────────────
install_claude() {
    if command -v claude &>/dev/null; then
        ok "Claude Code 已安装: $(claude --version 2>/dev/null || echo installed)"
        return 0
    fi
    warn "未检测到 Claude Code，开始安装..."

    if is_china_network; then
        warn "国内网络环境，使用 npm + 淘宝镜像"
        command -v npm &>/dev/null || fail "需要 npm (Node.js 18+)，请先安装 Node.js: https://nodejs.org"
        npm install -g @anthropic-ai/claude-code --registry="$NPM_MIRROR" 2>&1
    else
        info "尝试官方安装器 (curl)..."
        if curl -fsSL --connect-timeout 10 --max-time 60 https://claude.ai/install.sh | bash 2>&1; then
            ok "官方安装器完成"
        elif command -v brew &>/dev/null; then
            info "回退 Homebrew..."; brew install --cask claude-code
        elif command -v npm &>/dev/null; then
            info "回退 npm..."; npm install -g @anthropic-ai/claude-code 2>&1
        else
            fail "无可用安装方式，请手动安装: https://code.claude.com/docs/zh-CN/quickstart"
        fi
    fi

    refresh_path
    command -v claude &>/dev/null \
        && ok "Claude Code 安装成功: $(claude --version 2>/dev/null || echo installed)" \
        || fail "安装后仍找不到 claude；国内可手动: npm install -g @anthropic-ai/claude-code --registry=$NPM_MIRROR"
}

# ── 安装 OpenAI Codex ────────────────────────────────────────────────────────
install_codex() {
    if command -v codex &>/dev/null; then
        ok "Codex 已安装: $(codex --version 2>/dev/null || echo installed)"
        return 0
    fi
    warn "未检测到 Codex，开始安装..."
    command -v npm &>/dev/null || fail "安装 Codex 需要 npm (Node.js 18+)，请先安装 Node.js: https://nodejs.org"

    if is_china_network; then
        warn "国内网络环境，使用淘宝镜像"
        npm install -g @openai/codex --registry="$NPM_MIRROR" 2>&1
    else
        npm install -g @openai/codex 2>&1
    fi

    refresh_path
    command -v codex &>/dev/null \
        && ok "Codex 安装成功: $(codex --version 2>/dev/null || echo installed)" \
        || fail "安装后仍找不到 codex；可手动: npm install -g @openai/codex"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  setup_coding_cli.sh — 目标: $TARGET"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

case "$TARGET" in
    claude-code|claude) install_claude ;;
    codex)              install_codex ;;
    all)                install_claude; install_codex ;;
    jiuwen-coding)      ok "jiuwen-coding 为原生后端，无需安装外部 CLI" ;;
    *)                  fail "未知目标: $TARGET (支持: claude-code | codex | all)" ;;
esac

ok "完成"
