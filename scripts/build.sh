#!/usr/bin/env bash
# JiuwenClaw 打包脚本
# 1. 编译前端 (jiuwenclaw/web)
# 2. 构建 wheel 包（包含前端 dist）
# 3. 构建 TUI wheel 包
#
# 用法:
#   ./scripts/build.sh                           # 开发态打包（保留 git+ 分支依赖）
#   ./scripts/build.sh --release \
#     --version=0.2.0 \
#     --openjiuwen=0.1.10 \
#     --deepsearch=0.1.5                         # 发布态打包（指定版本号）
#
# 注意: 此脚本不嵌入 vendor 依赖。
#       如果之前用 build_release.sh --vendor 打包过，会自动清理 vendor 子目录。

set -e
PROJECT_ROOT="$(cd "$(dirname "$(dirname "$0")")" && pwd)"

# ============================================================
# 参数解析
# ============================================================
RELEASE_MODE=false
JIUWENCLAW_VERSION=""
OPENJIUWEN_VERSION=""
DEEPSEARCH_VERSION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --release)
            RELEASE_MODE=true
            shift
            ;;
        --version=*)
            JIUWENCLAW_VERSION="${1#*=}"
            shift
            ;;
        --openjiuwen=*)
            OPENJIUWEN_VERSION="${1#*=}"
            shift
            ;;
        --deepsearch=*)
            DEEPSEARCH_VERSION="${1#*=}"
            shift
            ;;
        *)
            # 忽略其他参数
            shift
            ;;
    esac
done

# 发布模式下必须指定版本号
if [[ "$RELEASE_MODE" == "true" ]]; then
    if [[ -z "$JIUWENCLAW_VERSION" ]]; then
        echo "[build] 错误: --release 模式需要指定 --version=<version>" >&2
        exit 1
    fi
    if [[ -z "$OPENJIUWEN_VERSION" ]]; then
        echo "[build] 错误: --release 模式需要指定 --openjiuwen=<version>" >&2
        exit 1
    fi
    if [[ -z "$DEEPSEARCH_VERSION" ]]; then
        echo "[build] 错误: --release 模式需要指定 --deepsearch=<version>" >&2
        exit 1
    fi
    echo "[build] 发布模式: jiuwenclaw==$JIUWENCLAW_VERSION, openjiuwen==$OPENJIUWEN_VERSION, openjiuwen_deepsearch==$DEEPSEARCH_VERSION"
fi

echo "[build] 项目根目录: $PROJECT_ROOT"

# ============================================================
# 检测可用的 Python 命令
# ============================================================
detect_python() {
    for cmd in python3 python py; do
        if command -v "$cmd" &>/dev/null; then
            echo "$cmd"
            return 0
        fi
    done
    echo "[build] 错误: 未找到 python/python3/py 命令" >&2
    exit 1
}
PYTHON_CMD=$(detect_python)
echo "[build] 使用 Python: $PYTHON_CMD"

# ============================================================
# pyproject.toml 依赖替换（仅发布模式）
# ============================================================
PYPROJECT="$PROJECT_ROOT/pyproject.toml"

replace_dependencies_for_release() {
    echo "[build] 临时替换 pyproject.toml 为发布版本..."

    # 使用 Python 完成整个操作（避免 shell/Python 路径格式不兼容）
    "$PYTHON_CMD" -c "
import re
import sys
import shutil

pyproject = sys.argv[1]
pyproject_bak = sys.argv[2] + '.bak'
jiuwenclaw_ver = sys.argv[3]
openjiuwen_ver = sys.argv[4]
deepsearch_ver = sys.argv[5]

# 备份原始文件
shutil.copy2(pyproject, pyproject_bak)
print(f'[build]   备份: {pyproject} -> {pyproject_bak}')

# 读取内容
content = open(pyproject, 'r', encoding='utf-8').read()

# 替换 jiuwenclaw 版本号
new_content = re.sub(
    r'^version = \"[^\"]+\"',
    f'version = \"{jiuwenclaw_ver}\"',
    content,
    count=1,
    flags=re.MULTILINE
)

# 替换 openjiuwen git+ 依赖
new_content = re.sub(
    r'\"openjiuwen @ git\+https://gitcode\.com/openJiuwen/agent-core\.git@[^\"]+\"',
    f'\"openjiuwen=={openjiuwen_ver}\"',
    new_content
)

# 替换 openjiuwen_deepsearch git+ 依赖
new_content = re.sub(
    r'\"openjiuwen_deepsearch @ git\+https://gitcode\.com/openJiuwen/deepsearch\.git@[^\"]+\"',
    f'\"openjiuwen_deepsearch=={deepsearch_ver}\"',
    new_content
)

# 写入修改后的内容
open(pyproject, 'w', encoding='utf-8').write(new_content)
print(f'[build]   version -> {jiuwenclaw_ver}')
print(f'[build]   openjiuwen @ git+... -> openjiuwen=={openjiuwen_ver}')
print(f'[build]   openjiuwen_deepsearch @ git+... -> openjiuwen_deepsearch=={deepsearch_ver}')
" "$PYPROJECT" "$PYPROJECT" "$JIUWENCLAW_VERSION" "$OPENJIUWEN_VERSION" "$DEEPSEARCH_VERSION"
}

restore_pyproject() {
    if [[ -f "$PYPROJECT.bak" ]]; then
        # 使用 Python 恢复（避免路径格式问题）
        "$PYTHON_CMD" -c "
import shutil
import sys
pyproject = sys.argv[1]
pyproject_bak = pyproject + '.bak'
shutil.copy2(pyproject_bak, pyproject)
print(f'[build] 已恢复原始 pyproject.toml')
" "$PYPROJECT"
        rm -f "$PYPROJECT.bak"
    fi
}

# 发布模式下执行依赖替换
if [[ "$RELEASE_MODE" == "true" ]]; then
    replace_dependencies_for_release
fi


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

cleanup() {
    # 恢复 node_modules
    if [[ "$NODE_MODULES_MOVED" == "true" && -d "$NODE_MODULES_BAK" ]]; then
        mv "$NODE_MODULES_BAK" "$NODE_MODULES"
        echo "[build] 已恢复 node_modules"
    fi
    # 恢复 pyproject.toml（仅发布模式）
    if [[ "$RELEASE_MODE" == "true" ]]; then
        restore_pyproject
    fi
}
trap cleanup EXIT

# 2. 构建 wheel
echo "[build] 正在构建 wheel 包..."
pip install -q --upgrade build wheel
python -m build --wheel --no-isolation

# 确保 dist 目录存在
DIST_OUTPUT="$PROJECT_ROOT/dist"
if [[ ! -d "$DIST_OUTPUT" ]]; then
    mkdir -p "$DIST_OUTPUT"
    echo "[build] 创建 dist 目录: $DIST_OUTPUT"
fi
echo "[build] 完成! wheel 包位于: $DIST_OUTPUT"
ls -la dist/*.whl 2>/dev/null || true

# 3. 构建 TUI wheel
if ! command -v bun &>/dev/null; then
    echo "[build] 跳过 TUI 构建: 未找到 bun 命令" >&2
    echo "完成bun安装: curl -fsSL https://bun.sh/install | bash  # 针对 macOS、Linux 和 WSL" >&2
else
    echo "[build] 正在构建TUI的 wheel包..."
    cd "$PROJECT_ROOT"
    python scripts/build_python_packages.py --target all --clean --install-js-deps
    echo "[build] TUI的 wheel 包构建完成"
fi
