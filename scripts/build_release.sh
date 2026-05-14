#!/usr/bin/env bash
# JiuwenClaw 打包脚本 - 支持指定版本号和 vendor 嵌入
#
# 用法:
#   ./scripts/build_release.sh                    # 使用当前版本打包
#   ./scripts/build_release.sh --version 1.0.0    # 指定版本号打包
#   ./scripts/build_release.sh --version 1.0.0.dev1  # 开发版本
#   ./scripts/build_release.sh --version 1.0.0 --git-hash  # 添加 git hash
#   ./scripts/build_release.sh --vendor           # 嵌入 openjiuwen/openjiuwen_deepsearch
#   ./scripts/build_release.sh --clean            # 清理后重新打包
#   ./scripts/build_release.sh --skip-frontend    # 跳过前端构建
#   ./scripts/build_release.sh --outdir ./release # 指定输出目录
#   ./scripts/build_release.sh --bump             # 永久更新版本号
#
# 功能:
#   - 编译前端 (jiuwenclaw/web)
#   - 构建 wheel 包（包含前端 dist）
#   - 支持临时版本覆盖（构建后恢复）
#   - 支持 git hash 后缀
#   - 支持永久版本更新 (--bump)
#   - 支持嵌入依赖包 (--vendor)

set -e

PROJECT_ROOT="$(cd "$(dirname "$(dirname "$0")")" && pwd)"
PYPROJECT_TOML="$PROJECT_ROOT/pyproject.toml"
VENDOR_DIR="$PROJECT_ROOT/jiuwenclaw/vendor"

# Git 仓库配置
OPENJIUWEN_REPO="https://gitcode.com/openJiuwen/agent-core.git"
OPENJIUWEN_BRANCH="enterprise-dev"
OPENJIUWEN_DEEPSEARCH_REPO="https://gitcode.com/openJiuwen/deepsearch.git"
OPENJIUWEN_DEEPSEARCH_BRANCH="enterprise_dev"

# 默认参数
VERSION=""
GIT_HASH=false
CLEAN=false
VENDOR=false
VENDOR_UPDATE=false
SKIP_FRONTEND=false
BUILD_FRONTEND=false
OUTDIR="$PROJECT_ROOT/dist"
BUMP=false
VERBOSE=false

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --version)
            VERSION="$2"
            shift 2
            ;;
        --git-hash)
            GIT_HASH=true
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --vendor)
            VENDOR=true
            shift
            ;;
        --vendor-update)
            VENDOR=true
            VENDOR_UPDATE=true
            shift
            ;;
        --skip-frontend)
            SKIP_FRONTEND=true
            shift
            ;;
        --build-frontend)
            BUILD_FRONTEND=true
            shift
            ;;
        --outdir)
            OUTDIR="$2"
            shift 2
            ;;
        --bump)
            BUMP=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            head -35 "$0" | tail -33
            exit 0
            ;;
        *)
            echo "[build] 未知参数: $1"
            exit 1
            ;;
    esac
done

# 验证参数
if [[ "$BUMP" == "true" && -z "$VERSION" ]]; then
    echo "[build] 错误: --bump 需要配合 --version 使用"
    exit 1
fi

echo "[build] 项目根目录: $PROJECT_ROOT"

# 获取当前版本
get_current_version() {
    grep '^version\s*=' "$PYPROJECT_TOML" | sed 's/version = "//;s/"$//' || {
        echo "[build] 错误: 无法从 pyproject.toml 获取版本"
        exit 1
    }
}

# 获取 git short hash
get_git_hash() {
    git rev-parse --short HEAD 2>/dev/null || echo ""
}

# 计算最终版本号
CURRENT_VERSION=$(get_current_version)
if [[ -z "$VERSION" ]]; then
    VERSION="$CURRENT_VERSION"
fi

if [[ "$GIT_HASH" == "true" ]]; then
    HASH=$(get_git_hash)
    if [[ -n "$HASH" ]]; then
        if [[ "$VERSION" == *"+"* ]]; then
            VERSION="${VERSION}.git${HASH}"
        else
            VERSION="${VERSION}+git${HASH}"
        fi
    fi
fi

echo "[build] 目标版本: $VERSION"

# 清理构建产物
clean_build() {
    echo "[build] 清理构建产物..."
    rm -rf "$PROJECT_ROOT/build"
    rm -rf "$PROJECT_ROOT/dist"
    rm -rf "$PROJECT_ROOT/*.egg-info"
    rm -rf "$PROJECT_ROOT/jiuwenclaw.egg-info"
}

if [[ "$CLEAN" == "true" ]]; then
    clean_build
fi

# Vendor: 拉取并嵌入依赖包
prepare_vendor() {
    local tmp_dir=$(mktemp -d)
    local vendor_openjiuwen="$VENDOR_DIR/openjiuwen"
    local vendor_deepsearch="$VENDOR_DIR/openjiuwen_deepsearch"

    echo "[build] 准备 vendor 依赖..."

    # 确保 vendor 目录存在
    mkdir -p "$VENDOR_DIR"

    # 检查是否需要拉取/更新 openjiuwen
    if [[ "$VENDOR_UPDATE" == "true" ]] || [[ ! -d "$vendor_openjiuwen" ]]; then
        echo "[build] 拉取 openjiuwen ($OPENJIUWEN_BRANCH)..."
        if [[ -d "$vendor_openjiuwen" ]]; then
            # 已存在，删除旧版本
            rm -rf "$vendor_openjiuwen"
        fi

        # 克隆到临时目录
        git clone --depth 1 --branch "$OPENJIUWEN_BRANCH" "$OPENJIUWEN_REPO" "$tmp_dir/openjiuwen_src" 2>/dev/null

        # 复制源码（排除 git 相关文件）
        mkdir -p "$vendor_openjiuwen"
        cp -r "$tmp_dir/openjiuwen_src/openjiuwen"/* "$vendor_openjiuwen/" 2>/dev/null || {
            # 如果仓库结构是直接包含源码而非 openjiuwen 子目录
            cp -r "$tmp_dir/openjiuwen_src"/* "$vendor_openjiuwen/"
            # 删除 git 文件
            rm -rf "$vendor_openjiuwen/.git"
        }
        rm -rf "$vendor_openjiuwen/.git"

        # 确保 __init__.py 存在
        touch "$vendor_openjiuwen/__init__.py"

        echo "[build] openjiuwen 已嵌入到 $vendor_openjiuwen"
    else
        echo "[build] 使用现有 vendor: openjiuwen (跳过拉取)"
    fi

    # 检查是否需要拉取/更新 openjiuwen_deepsearch
    if [[ "$VENDOR_UPDATE" == "true" ]] || [[ ! -d "$vendor_deepsearch" ]]; then
        echo "[build] 拉取 openjiuwen_deepsearch ($OPENJIUWEN_DEEPSEARCH_BRANCH)..."
        if [[ -d "$vendor_deepsearch" ]]; then
            rm -rf "$vendor_deepsearch"
        fi

        git clone --depth 1 --branch "$OPENJIUWEN_DEEPSEARCH_BRANCH" "$OPENJIUWEN_DEEPSEARCH_REPO" "$tmp_dir/deepsearch_src" 2>/dev/null

        mkdir -p "$vendor_deepsearch"
        cp -r "$tmp_dir/deepsearch_src/openjiuwen_deepsearch"/* "$vendor_deepsearch/" 2>/dev/null || {
            cp -r "$tmp_dir/deepsearch_src"/* "$vendor_deepsearch/"
            rm -rf "$vendor_deepsearch/.git"
        }
        rm -rf "$vendor_deepsearch/.git"

        touch "$vendor_deepsearch/__init__.py"

        echo "[build] openjiuwen_deepsearch 已嵌入到 $vendor_deepsearch"
    else
        echo "[build] 使用现有 vendor: openjiuwen_deepsearch (跳过拉取)"
    fi

    # 清理临时目录
    rm -rf "$tmp_dir"

    # 验证 vendor 目录
    if [[ -d "$vendor_openjiuwen" ]] && [[ -d "$vendor_deepsearch" ]]; then
        echo "[build] Vendor 依赖准备完成"
        return 0
    else
        echo "[build] 错误: Vendor 目录准备失败"
        return 1
    fi
}

# 处理 vendor
if [[ "$VENDOR" == "true" ]]; then
    prepare_vendor || exit 1
fi

# 前端构建
WEB_DIR="$PROJECT_ROOT/jiuwenclaw/web"
build_frontend() {
    if [[ ! -d "$WEB_DIR" ]]; then
        echo "[build] 错误: 前端目录不存在: $WEB_DIR"
        return 1
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
        echo "[build] 错误: 前端编译输出不存在: $DIST_DIR"
        return 1
    fi
    echo "[build] 前端编译完成"
    return 0
}

# 前端处理
if [[ "$BUILD_FRONTEND" == "true" ]]; then
    build_frontend || exit 1
elif [[ "$SKIP_FRONTEND" != "true" ]]; then
    DIST_DIR="$WEB_DIR/dist"
    if [[ ! -d "$DIST_DIR" ]] || [[ -z "$(ls -A "$DIST_DIR" 2>/dev/null)" ]]; then
        echo "[build] 前端 dist 不存在: $DIST_DIR"
        echo "[build] 请使用 --build-frontend 构建前端，或 --skip-frontend 跳过"
        exit 1
    fi
fi

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
    # 恢复 pyproject.toml（如果需要）
    if [[ -f "$PYPROJECT_TOML.bak" ]]; then
        mv "$PYPROJECT_TOML.bak" "$PYPROJECT_TOML"
        echo "[build] 已恢复 pyproject.toml"
    fi
    # 清理 vendor 子目录（打包完成后恢复环境）
    if [[ -d "$VENDOR_DIR/openjiuwen" ]]; then
        echo "[build] 清理 vendor/openjiuwen"
        rm -rf "$VENDOR_DIR/openjiuwen"
    fi
    if [[ -d "$VENDOR_DIR/openjiuwen_deepsearch" ]]; then
        echo "[build] 清理 vendor/openjiuwen_deepsearch"
        rm -rf "$VENDOR_DIR/openjiuwen_deepsearch"
    fi
}
trap cleanup EXIT

# 版本修改函数
update_version() {
    local new_version="$1"
    local permanent="$2"

    if [[ "$new_version" == "$CURRENT_VERSION" ]]; then
        return 0
    fi

    if [[ "$permanent" == "true" ]]; then
        sed -i.bak "s/^version = \"[^\"]*\"/version = \"$new_version\"/" "$PYPROJECT_TOML"
        rm -f "$PYPROJECT_TOML.bak"
        echo "[build] 永久更新版本: $CURRENT_VERSION -> $new_version"
    else
        # 临时修改，保存备份（如果还没有备份）
        if [[ ! -f "$PYPROJECT_TOML.bak" ]]; then
            cp "$PYPROJECT_TOML" "$PYPROJECT_TOML.bak"
        fi
        sed -i "s/^version = \"[^\"]*\"/version = \"$new_version\"/" "$PYPROJECT_TOML"
        echo "[build] 临时修改版本: $CURRENT_VERSION -> $new_version"
    fi
}

# Vendor 模式下修改依赖声明：移除 git 依赖
# 这样下游用户不需要再去下载 openjiuwen/openjiuwen_deepsearch
modify_dependencies_for_vendor() {
    if [[ ! -f "$PYPROJECT_TOML.bak" ]]; then
        cp "$PYPROJECT_TOML" "$PYPROJECT_TOML.bak"
    fi

    # 移除 openjiuwen git 依赖
    sed -i '/openjiuwen @ git+https:\/\/gitcode.com\/openJiuwen\/agent-core.git/d' "$PYPROJECT_TOML"
    # 移除 openjiuwen_deepsearch git 依赖
    sed -i '/openjiuwen_deepsearch @ git+https:\/\/gitcode.com\/openJiuwen\/deepsearch.git/d' "$PYPROJECT_TOML"

    echo "[build] 已移除 git 依赖声明（vendor 模式）"
}

# 处理版本
if [[ "$VERSION" != "$CURRENT_VERSION" ]]; then
    if [[ "$BUMP" == "true" ]]; then
        update_version "$VERSION" true
    else
        update_version "$VERSION" false
    fi
fi

# Vendor 模式下移除 git 依赖声明
if [[ "$VENDOR" == "true" ]]; then
    modify_dependencies_for_vendor
fi

# 确保 outdir 存在
mkdir -p "$OUTDIR"

# 构建 wheel
echo "[build] 正在构建 wheel 包..."

# 优先使用 uv build
if command -v uv &>/dev/null; then
    if [[ "$VERBOSE" == "true" ]]; then
        uv build --wheel --out-dir "$OUTDIR"
    else
        uv build --wheel --out-dir "$OUTDIR" 2>/dev/null
    fi
else
    pip install -q --upgrade build wheel
    if [[ "$VERBOSE" == "true" ]]; then
        python -m build --wheel --outdir "$OUTDIR"
    else
        python -m build --wheel --outdir "$OUTDIR" 2>/dev/null
    fi
fi

# 验证 wheel
WHEEL_FILE=$(ls -t "$OUTDIR"/*.whl 2>/dev/null | head -1)
if [[ -z "$WHEEL_FILE" ]]; then
    echo "[build] 错误: 未找到 wheel 包"
    exit 1
fi

WHEEL_SIZE=$(stat -c%s "$WHEEL_FILE" 2>/dev/null || stat -f%z "$WHEEL_FILE" 2>/dev/null || wc -c < "$WHEEL_FILE")
WHEEL_SIZE_KB=$((WHEEL_SIZE / 1024))

echo "[build] Wheel 包构建完成!"
echo "[build] 文件: $(basename "$WHEEL_FILE")"
echo "[build] 版本: $VERSION"
echo "[build] 大小: ${WHEEL_SIZE_KB} KB"
if [[ "$VENDOR" == "true" ]]; then
    echo "[build] Vendor: 包含 openjiuwen + openjiuwen_deepsearch"
fi
echo "[build] 路径: $WHEEL_FILE"