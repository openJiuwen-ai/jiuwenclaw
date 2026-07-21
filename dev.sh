#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# dev.sh — JiuwenAvatar 一键开发环境启动脚本
#
# 用法:
#   ./dev.sh          # 完整流程: 安装依赖 + 构建前端 + 启动后端
#   ./dev.sh --skip-install   # 跳过依赖安装
#   ./dev.sh --skip-build     # 跳过前端构建
#   ./dev.sh --frontend-only  # 只启动前端 dev server
#   ./dev.sh --backend-only   # 只启动后端
# ---------------------------------------------------------------------------

set -euo pipefail

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
FRONTEND_DIR="$PROJECT_ROOT/jiuwenavatar/channels/web/frontend"

# 解析参数
SKIP_INSTALL=false
SKIP_BUILD=false
FRONTEND_ONLY=false
BACKEND_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --skip-install)  SKIP_INSTALL=true ;;
    --skip-build)    SKIP_BUILD=true ;;
    --frontend-only) FRONTEND_ONLY=true ;;
    --backend-only)  BACKEND_ONLY=true ;;
    --help|-h)
      echo "用法: ./dev.sh [--skip-install] [--skip-build] [--frontend-only] [--backend-only]"
      exit 0
      ;;
  esac
done

echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║    JiuwenAvatar 开发环境一键启动脚本      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: 安装后端依赖 (uv)
# ---------------------------------------------------------------------------
if [ "$SKIP_INSTALL" = false ]; then
  echo -e "${YELLOW}[1/4] 安装后端依赖 (uv sync)...${NC}"
  if command -v uv &>/dev/null; then
    uv sync
    echo -e "${GREEN}✓ 后端依赖安装完成${NC}"
  else
    echo -e "${RED}✗ 未找到 uv，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
    exit 1
  fi
else
  echo -e "${YELLOW}[1/4] 跳过后端依赖安装${NC}"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 2: 安装前端依赖 (npm)
# ---------------------------------------------------------------------------
if [ "$SKIP_INSTALL" = false ] && [ "$BACKEND_ONLY" = false ]; then
  echo -e "${YELLOW}[2/4] 安装前端依赖 (npm install)...${NC}"
  if command -v npm &>/dev/null; then
    cd "$FRONTEND_DIR"
    npm install
    cd "$PROJECT_ROOT"
    echo -e "${GREEN}✓ 前端依赖安装完成${NC}"
  else
    echo -e "${RED}✗ 未找到 npm，请先安装 Node.js${NC}"
    exit 1
  fi
else
  echo -e "${YELLOW}[2/4] 跳过前端依赖安装${NC}"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 3: 构建前端
# ---------------------------------------------------------------------------
if [ "$SKIP_BUILD" = false ] && [ "$BACKEND_ONLY" = false ]; then
  echo -e "${YELLOW}[3/4] 构建前端 (npm run build)...${NC}"
  cd "$FRONTEND_DIR"
  npm run build
  cd "$PROJECT_ROOT"
  echo -e "${GREEN}✓ 前端构建完成${NC}"
else
  echo -e "${YELLOW}[3/4] 跳过前端构建${NC}"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 4: 启动服务
# ---------------------------------------------------------------------------

# 释放开发端口：凡占用这些端口的进程一律尝试杀掉（先 SIGTERM，残留再 SIGKILL）。
# 端口含义: 28092=AgentServer, 29000=Web Gateway, 29001=ACP/TUI Gateway,
# 29002=Avatar HTTP/Webhook, 29173=Vite 前端。
DEV_PORTS=(28092 29000 29001 29002 29173)

stop_stale_dev_services() {
  local port pid cmd attempt
  for port in "${DEV_PORTS[@]}"; do
    # 第一轮 SIGTERM 优雅停止
    for pid in $(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true); do
      cmd="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
      echo -e "${YELLOW}停止占用端口 ${port} 的进程 (pid ${pid}): ${cmd:-未知}${NC}"
      kill "${pid}" 2>/dev/null || true
    done
  done
  sleep 1

  # 第二轮：仍未释放的端口强制 SIGKILL
  for port in "${DEV_PORTS[@]}"; do
    for attempt in 1 2 3; do
      pid="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
      [ -z "${pid}" ] && break
      echo -e "${RED}端口 ${port} 仍被占用 (pid ${pid})，强制结束 (kill -9)...${NC}"
      # shellcheck disable=SC2086
      kill -9 ${pid} 2>/dev/null || true
      sleep 0.5
    done
  done
}

# 清理函数: 退出时杀掉所有子进程
cleanup() {
  echo ""
  echo -e "${YELLOW}正在停止所有服务...${NC}"
  jobs -p | xargs -r kill 2>/dev/null
  wait 2>/dev/null
  echo -e "${GREEN}✓ 所有服务已停止${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

if [ "$FRONTEND_ONLY" = true ]; then
  # 只启动前端 dev server (Vite, 带热更新)
  echo -e "${CYAN}[4/4] 启动前端开发服务器 (Vite dev server)...${NC}"
  echo -e "  前端地址: ${GREEN}http://localhost:29173${NC}"
  echo -e "  需要 Gateway 运行在 localhost:29000 才能连接后端"
  echo ""
  cd "$FRONTEND_DIR"
  npm run dev
  exit 0
fi

# 启动后端 (AgentServer + Gateway)
echo -e "${CYAN}[4/4] 启动后端服务 (jiuwenavatar-app)...${NC}"
echo -e "  Web 地址: ${GREEN}http://0.0.0.0:29000${NC}"
echo -e "  Avatar HTTP: ${GREEN}http://0.0.0.0:29002/avatar/chat${NC}"
echo -e "  按 Ctrl+C 停止所有服务"
echo ""

stop_stale_dev_services

# 单机对外访问：Gateway / Avatar HTTP 绑定 0.0.0.0
export WEB_HOST="${WEB_HOST:-0.0.0.0}"
export AVATAR_HTTP_HOST="${AVATAR_HTTP_HOST:-0.0.0.0}"
export WEBHOOK_HOST="${WEBHOOK_HOST:-0.0.0.0}"

if [ "$BACKEND_ONLY" = true ]; then
  uv run python -m jiuwenavatar.app
  exit 0
fi

# 等待 Gateway 真正监听 Web 端口后再启动前端，避免 Vite 代理在
# 后端就绪前刷 "ECONNREFUSED 127.0.0.1:29000" 的瞬时报错。
wait_for_gateway() {
  local port="${1:-29000}" timeout="${2:-60}" waited=0
  echo -e "${CYAN}等待 Gateway 在端口 ${port} 就绪...${NC}"
  while [ "${waited}" -lt "${timeout}" ]; do
    # 后端进程提前挂掉则立即报错退出，避免空等
    if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
      echo -e "${RED}✗ 后端进程已退出，启动失败${NC}"
      return 1
    fi
    if lsof -ti "tcp:${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      echo -e "${GREEN}✓ Gateway 已就绪 (端口 ${port})${NC}"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo -e "${YELLOW}⚠ 等待 Gateway 超时 (${timeout}s)，仍尝试启动前端${NC}"
  return 0
}

# 完整模式: 后端 + 前端 dev server (并行)
echo -e "${CYAN}启动后端...${NC}"
uv run python -m jiuwenavatar.app &
BACKEND_PID=$!

# 等待后端 Gateway 就绪（而非固定 sleep），消除前端代理瞬时报错
if ! wait_for_gateway 29000 60; then
  cleanup
fi

echo -e "${CYAN}启动前端开发服务器...${NC}"
cd "$FRONTEND_DIR"
npx vite --host &
FRONTEND_PID=$!
cd "$PROJECT_ROOT"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  JiuwenAvatar 开发环境已启动!             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo -e "  后端 (Gateway):  ${CYAN}http://0.0.0.0:29000${NC}"
echo -e "  Avatar HTTP:     ${CYAN}http://0.0.0.0:29002/avatar/chat${NC}"
echo -e "  前端 (Vite HMR): ${CYAN}http://0.0.0.0:29173${NC}"
echo -e "  按 Ctrl+C 停止所有服务"
echo ""

# 等待任意子进程退出
wait
