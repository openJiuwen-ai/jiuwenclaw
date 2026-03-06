#!/usr/bin/env bash
# JiuwenClaw 打包脚本
# 1. 编译前端 (jiuwenclaw/web)
# 2. 构建 wheel 包（包含前端 dist）

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "[build] 项目根目录: $PROJECT_ROOT"

# 1. 编译前端
WEB_DIR="$PROJECT_ROOT/jiuwenclaw/web"
if [[ ! -d "$WEB_DIR" ]]; then
    echo "[build] 错误: 前端目录不存在: $WEB_DIR" >&2
    exit 1
fi

echo "[build] 正在编译前端..."
cd "$WEB_DIR"
if [[ ! -d node_modules ]]; then
    echo "[build] 安装 npm 依赖..."
    npm install
fi
npm run build
cd "$PROJECT_ROOT"

DIST_DIR="$WEB_DIR/dist"
if [[ ! -d "$DIST_DIR" ]]; then
    echo "[build] 错误: 前端编译输出不存在: $DIST_DIR" >&2
    exit 1
fi
echo "[build] 前端编译完成: $DIST_DIR"

# 临时移走 node_modules，避免被打包进 wheel
NODE_MODULES="$WEB_DIR/node_modules"
NODE_MODULES_BAK="$WEB_DIR/node_modules.bak"
NODE_MODULES_MOVED=false
if [[ -d "$NODE_MODULES" ]]; then
    echo "[build] 临时移走 node_modules 以减小 wheel 体积..."
    mv "$NODE_MODULES" "$NODE_MODULES_BAK"
    NODE_MODULES_MOVED=true
fi

# 创建符号链接，让 workspace 和入口脚本在 jiuwenclaw 包内
SYMLINKS_REMOVED=()
JIUWENCLAW_DIR="$PROJECT_ROOT/jiuwenclaw"

WORKSPACE_LINK="$JIUWENCLAW_DIR/workspace"
if [[ ! -e "$WORKSPACE_LINK" ]]; then
    echo "[build] 创建 workspace 符号链接..."
    ln -s "$PROJECT_ROOT/workspace" "$WORKSPACE_LINK"
    SYMLINKS_REMOVED+=("$WORKSPACE_LINK")
fi

# 创建入口脚本的符号链接
APP_LINK="$JIUWENCLAW_DIR/app.py"
if [[ ! -e "$APP_LINK" ]]; then
    echo "[build] 创建 app.py 符号链接..."
    ln -s "$PROJECT_ROOT/app.py" "$APP_LINK"
    SYMLINKS_REMOVED+=("$APP_LINK")
fi

APP_WEB_LINK="$JIUWENCLAW_DIR/app_web.py"
if [[ ! -e "$APP_WEB_LINK" ]]; then
    echo "[build] 创建 app_web.py 符号链接..."
    ln -s "$PROJECT_ROOT/app_web.py" "$APP_WEB_LINK"
    SYMLINKS_REMOVED+=("$APP_WEB_LINK")
fi

START_SERVICES_LINK="$JIUWENCLAW_DIR/start_services.py"
if [[ ! -e "$START_SERVICES_LINK" ]]; then
    echo "[build] 创建 start_services.py 符号链接..."
    ln -s "$PROJECT_ROOT/start_services.py" "$START_SERVICES_LINK"
    SYMLINKS_REMOVED+=("$START_SERVICES_LINK")
fi

cleanup() {
    # 清理符号链接
    for link in "${SYMLINKS_REMOVED[@]}"; do
        if [[ -e "$link" ]]; then
            rm -rf "$link"
            echo "[build] 已删除符号链接: $link"
        fi
    done

    # 恢复 node_modules
    if [[ "$NODE_MODULES_MOVED" == "true" && -d "$NODE_MODULES_BAK" ]]; then
        mv "$NODE_MODULES_BAK" "$NODE_MODULES"
        echo "[build] 已恢复 node_modules"
    fi
}
trap cleanup EXIT

# 2. 构建 wheel
echo "[build] 正在构建 wheel 包..."
pip install -q --upgrade build wheel
python -m build --wheel

# 确保 dist 目录存在
DIST_OUTPUT="$PROJECT_ROOT/dist"
if [[ ! -d "$DIST_OUTPUT" ]]; then
    mkdir -p "$DIST_OUTPUT"
    echo "[build] 创建 dist 目录: $DIST_OUTPUT"
fi
echo "[build] 完成! wheel 包位于: $DIST_OUTPUT"
ls -la dist/*.whl 2>/dev/null || true
