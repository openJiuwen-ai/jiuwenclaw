#!/bin/sh
# 鸿蒙 Preview：AgentServer 精简栈一键安装（自包含）
# 鸿蒙部署目录名通常为 jiuwenswarm（Windows 开发仓 jiuwenswarm_enterprise_dev 拷过去后可改名）
# 对齐本仓开发流程：
#   1. python3 -m venv .venv && source .venv/bin/activate
#   2. preload WHEEL_DIR 中 native wheel（cryptography/lupa 等，最先执行）
#   3. pip install -r requirements-minimal.txt   （phase-1 逐包 + import 验证，含传递依赖）
#   4. pip install openjiuwen-harmonyos --no-deps  （git 或本地 agent-core/harmonyos）
#   5. pip install agentcore-minimal 补依赖      （harmonyos/pyproject.toml − Phase 1，含传递依赖）
#   6. pip install --no-deps -e .                （jiuwenclaw 本体）
#
# 用法（鸿蒙 HiShell）:
#   cd /storage/Users/currentUser/officeClaw/jiuwenswarm
#   export OHOS_REAL_PYTHON=/storage/Users/currentUser/usr/local/bin/python3.12
#   export WHEEL_DIR=/storage/Users/currentUser/officeClaw/jiuwenswarm/wheels
#   sed -i 's/\r$//' scripts/*.sh scripts/ohos/*.sh
#   sh scripts/install-ohos-agentserver.sh
#
# 环境变量:
#   OHOS_REAL_PYTHON   cmd-pkgs Python（编 wheel / libpython 用）
#   WHEEL_DIR          wheels/（预编 native wheel，默认 $REPO_ROOT/wheels）
#   AGENT_CORE_PATH    本地 agent-core（USE_LOCAL_OPENJIUWEN=1 时）
#   GIT_EXECUTABLE     显式指定 git 路径（pip git+ 依赖 clone 时用）
#   OPENJIUWEN_USE_HARMONYOS_PYPROJECT=1  默认从 clone 的 harmonyos/pyproject.toml 做 -e 安装
#   USE_LOCAL_OPENJIUWEN=1  本地 pip install --no-deps -e $AGENT_CORE_PATH
#   OPENJIUWEN_GIT_REPO / OPENJIUWEN_GIT_REF  默认 openJiuwen/agent-core @ enterprise-dev
#   CREATE_VENV=1      默认创建 $REPO_ROOT/.venv
#   SKIP_PHASE0/1/2/3/4  跳过对应阶段（phase 0 = wheels 预装）
#   CONTINUE_ON_FAIL=1 单包失败继续（默认 1）

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
OHOS_DIR="$SCRIPT_DIR/ohos"
export OHOS_ENV_SCRIPTS_DIR="$OHOS_DIR"
# shellcheck disable=SC1091
. "$OHOS_DIR/ohos-env.sh"
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
export OHOS_REPO_ROOT="$REPO_ROOT"
OFFICE_CLAW=${OFFICE_CLAW:-$(CDPATH= cd -- "$REPO_ROOT/.." && pwd)}
WHEEL_BUILD_ROOT=${WHEEL_BUILD_ROOT:-$REPO_ROOT}
WHEEL_DIR=${WHEEL_DIR:-$WHEEL_BUILD_ROOT/wheels}
REPORT_DIR=${REPORT_DIR:-$REPO_ROOT/ohos-install-reports}
CREATE_VENV=${CREATE_VENV:-1}
VENV_DIR=${VENV_DIR:-$REPO_ROOT/.venv}
RECREATE_VENV=${RECREATE_VENV:-0}
CONTINUE_ON_FAIL=${CONTINUE_ON_FAIL:-1}
USE_LOCAL_OPENJIUWEN=${USE_LOCAL_OPENJIUWEN:-0}
# PRODUCTION=1 (默认): 用非 editable 安装(源码复制进 site-packages),用于打包场景
# PRODUCTION=0: 开发模式,保留 -e 以便改源码立即生效
PRODUCTION=${PRODUCTION:-1}
if [ "$PRODUCTION" = "1" ]; then
  EDITABLE_FLAG=""
else
  EDITABLE_FLAG="-e"
fi
AGENT_CORE_PATH=${AGENT_CORE_PATH:-$OFFICE_CLAW/agent-core}
OPENJIUWEN_GIT_REPO=${OPENJIUWEN_GIT_REPO:-https://gitcode.com/openJiuwen/agent-core.git}
OPENJIUWEN_GIT_REF=${OPENJIUWEN_GIT_REF:-enterprise-dev}
OPENJIUWEN_SPEC=${OPENJIUWEN_SPEC:-openjiuwen-harmonyos @ git+${OPENJIUWEN_GIT_REPO}@${OPENJIUWEN_GIT_REF}#subdirectory=harmonyos}

DEPS_INSTALLER=${OHOS_DEPS_INSTALLER:-$OHOS_DIR/install-ohos-all-deps.sh}
WHEEL_PRELOADER=${OHOS_WHEEL_PRELOADER:-$OHOS_DIR/ohos-wheel-preload.sh}

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"
}

die() {
  log "ERROR: $*"
  exit 1
}

resolve_base_python() {
  if [ -n "${OHOS_REAL_PYTHON:-}" ]; then
    echo "$OHOS_REAL_PYTHON"
    return 0
  fi
  if [ -n "${PYTHON:-}" ]; then
    echo "$PYTHON"
    return 0
  fi
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

run_manifest_phase() {
  _phase=$1
  _profile=$2
  [ -f "$DEPS_INSTALLER" ] || die "deps installer not found: $DEPS_INSTALLER"

  log "======== phase $_phase: $_profile (generated manifest) ========"
  mkdir -p "$REPORT_DIR/phase-$_phase"
  _harmonyos=
  for _p in \
    "${OPENJIUWEN_SRC_DIR:-$REPO_ROOT/.cache/openjiuwen-src}/harmonyos/pyproject.toml" \
    "${AGENT_CORE_PATH:-}/harmonyos/pyproject.toml" \
    "$OFFICE_CLAW/agent-core/harmonyos/pyproject.toml"; do
    [ -f "$_p" ] || continue
    _harmonyos="$_p"
    break
  done
  MANIFEST="" \
    MANIFEST_PROFILE="$_profile" \
    HARMONYOS_PYPROJECT="${_harmonyos:-}" \
    REPORT_DIR="$REPORT_DIR/phase-$_phase" \
    PYTHON="$PYTHON" \
    OHOS_REAL_PYTHON="$OHOS_REAL_PYTHON" \
    USE_VENV=1 \
    VENV_DIR="$VENV_DIR" \
    CREATE_VENV="${CREATE_VENV:-1}" \
    RECREATE_VENV="${RECREATE_VENV:-0}" \
    OFFICE_CLAW="$OFFICE_CLAW" \
    AGENT_CORE_PATH="$AGENT_CORE_PATH" \
    OPENJIUWEN_SRC_DIR="${OPENJIUWEN_SRC_DIR:-$REPO_ROOT/.cache/openjiuwen-src}" \
    WHEEL_BUILD_ROOT="$WHEEL_BUILD_ROOT" \
    WHEEL_DIR="$WHEEL_DIR" \
    SKIP_WHEEL_PRELOAD=1 \
    AUTO=1 \
    CONTINUE_ON_FAIL="$CONTINUE_ON_FAIL" \
    sh "$DEPS_INSTALLER"
}

pip_in_venv() {
  ensure_ohos_tool_path
  # 不用 env 包一层：部分 OhOS 上 env 子进程 PATH 与父 shell 不一致，pip 调 git 会 ENOENT
  export RUSTC="${RUSTC:-}"
  export CARGO="${CARGO:-}"
  export MATURIN="${MATURIN:-}"
  export CC="${CC:-}"
  export CXX="${CXX:-}"
  export AR="${AR:-}"
  export RANLIB="${RANLIB:-}"
  export RUSTFLAGS="${RUSTFLAGS:-}"
  export OPENSSL_DIR="${OPENSSL_DIR:-}"
  export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}"
  export WHEEL_BUILD_ROOT="${WHEEL_BUILD_ROOT:-}"
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  export PATH
  # 默认走清华镜像，规避 pypi.org DNS 问题；可由调用方覆盖
  : "${PIP_INDEX_URL:=https://pypi.tuna.tsinghua.edu.cn/simple}"
  : "${PIP_TRUSTED_HOST:=pypi.tuna.tsinghua.edu.cn}"
  export PIP_INDEX_URL PIP_TRUSTED_HOST
  "$PYTHON" -m pip install --no-cache-dir --no-build-isolation \
    --index-url "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" "$@"
}

ensure_pep517_minimal() {
  if "$PYTHON" -c "import setuptools, wheel" 2>/dev/null; then
    log "PEP517: setuptools+wheel OK"
    return 0
  fi
  log "PEP517 bootstrap: pip install -U pip setuptools wheel (SKIP_PHASE1 或未跑 deps installer 时需要)"
  "$PYTHON" -m pip install --no-cache-dir -U pip setuptools wheel \
    || die "PEP517 bootstrap failed (pip setuptools wheel)"
}

resolve_openjiuwen_install_dir() {
  _src=$1
  if [ "${OPENJIUWEN_USE_HARMONYOS_PYPROJECT:-1}" = "1" ] \
    && [ -f "$_src/harmonyos/pyproject.toml" ]; then
    echo "$_src/harmonyos"
    return 0
  fi
  echo "$_src"
}

_prepend_path_dir() {
  _dir=$1
  [ -n "$_dir" ] || return 0
  [ -d "$_dir" ] || return 0
  case ":${PATH}:" in
    *":$_dir:"*) ;;
    *) PATH="$_dir:$PATH" ;;
  esac
}

# HiShell 交互式 PATH 含 git；非交互 sh 需 ohos-env.sh + 本函数补 venv/bin
ensure_ohos_tool_path() {
  # shellcheck disable=SC1091
  . "$OHOS_DIR/ohos-env.sh"
  if [ -n "${VENV_DIR:-}" ] && [ -d "$VENV_DIR/bin" ]; then
    _prepend_path_dir "$VENV_DIR/bin"
    export PATH
  fi
}

find_cmd_git() {
  ensure_ohos_tool_path
  ohos_find_git
}

# patch_venv_activate_ohos 定义于 ohos-env.sh

link_git_into_venv() {
  _git=$1
  [ -n "$_git" ] || return 0
  [ -x "$_git" ] || return 0
  [ -d "${VENV_DIR:-}/bin" ] || return 0
  # 避免把上次生成的 shim 当成真 git 再 wrap 一次 → 无限递归
  _shim="$VENV_DIR/bin/git"
  [ "$_git" = "$_shim" ] && return 0
  case "$_git" in
    "$VENV_DIR"/*) return 0 ;;
  esac
  _git_dir=$(dirname "$_git")
  # 用 wrapper 而非 symlink：pip 子进程 PATH 常不含 hnp，shim 内 exec 绝对路径
  cat >"$_shim" <<EOF
#!/bin/sh
export PATH="$_git_dir:\${PATH:-}"
export LD_LIBRARY_PATH="${OHOS_HNP_LIB}:\${LD_LIBRARY_PATH:-}"
exec "$_git" "\$@"
EOF
  chmod +x "$_shim" 2>/dev/null || true
  _prepend_path_dir "$VENV_DIR/bin"
  export PATH
}

clone_openjiuwen_repo() {
  _git=$(find_cmd_git) || return 1
  _repo="$OPENJIUWEN_GIT_REPO"
  _ref="$OPENJIUWEN_GIT_REF"
  _dest="${OPENJIUWEN_SRC_DIR:-$REPO_ROOT/.cache/openjiuwen-src}"
  _log="${REPORT_DIR}/phase-2/git-clone.log"
  mkdir -p "$(dirname "$_dest")" "$REPORT_DIR/phase-2"
  if [ -d "$_dest/.git" ]; then
    log "refresh clone: $_dest ($_ref)"
    "$_git" -C "$_dest" fetch --depth 1 origin "$_ref" >>"$_log" 2>&1 \
      && "$_git" -C "$_dest" checkout "$_ref" >>"$_log" 2>&1 \
      && return 0
    log "WARN: git fetch failed, re-clone"
    rm -rf "$_dest"
  fi
  log "git clone -b $_ref --depth 1 $_repo -> $_dest"
  "$_git" clone --depth 1 --branch "$_ref" "$_repo" "$_dest" >>"$_log" 2>&1 || return 1
  return 0
}

resolve_agent_core_path() {
  if [ -n "${AGENT_CORE_PATH:-}" ] && [ -d "$AGENT_CORE_PATH" ]; then
    echo "$AGENT_CORE_PATH"
    return 0
  fi
  for _p in \
    "$OFFICE_CLAW/agent-core" \
    "$OFFICE_CLAW/agent-core_5969" \
    "$REPO_ROOT/../agent-core" \
    "$REPO_ROOT/vendor/openjiuwen"
  do
    if [ -f "$_p/pyproject.toml" ] || [ -f "$_p/setup.py" ] || [ -f "$_p/harmonyos/pyproject.toml" ]; then
      echo "$_p"
      return 0
    fi
  done
  return 1
}

install_openjiuwen() {
  ensure_pep517_minimal

  if [ "$USE_LOCAL_OPENJIUWEN" = "1" ]; then
    _local=$(resolve_agent_core_path) || die "USE_LOCAL_OPENJIUWEN=1 but AGENT_CORE_PATH not found (set AGENT_CORE_PATH=$OFFICE_CLAW/agent-core)"
    _install=$(resolve_openjiuwen_install_dir "$_local")
    log "pip install --no-deps $EDITABLE_FLAG $_install"
    pip_in_venv --no-deps $EDITABLE_FLAG "$_install" || die "openjiuwen local install failed"
    return 0
  fi

  if _git=$(find_cmd_git); then
    link_git_into_venv "$_git"
    export PATH="$(dirname "$_git")${PATH:+:$PATH}"
    log "git: $_git ($("$_git" --version 2>&1 | head -1))"
    log "PATH (git dir prepended): $(echo "$PATH" | tr ':' '\n' | head -5 | tr '\n' ':')..."
    _src="${OPENJIUWEN_SRC_DIR:-$REPO_ROOT/.cache/openjiuwen-src}"
    if clone_openjiuwen_repo; then
      _install=$(resolve_openjiuwen_install_dir "$_src")
      log "pip install --no-deps $EDITABLE_FLAG $_install  (manual git clone, bypass pip git+)"
      pip_in_venv --no-deps $EDITABLE_FLAG "$_install" || die "openjiuwen local install from clone failed"
    else
      log "WARN: git clone failed, fallback pip git+ spec"
      log "pip install --no-deps \"$OPENJIUWEN_SPEC\""
      pip_in_venv --no-deps "$OPENJIUWEN_SPEC" || die "openjiuwen git install failed"
    fi
    return 0
  fi

  if _local=$(resolve_agent_core_path); then
    log "WARN: git not in PATH; fallback to local agent-core: $_local"
    _install=$(resolve_openjiuwen_install_dir "$_local")
    log "pip install --no-deps $EDITABLE_FLAG $_install"
    pip_in_venv --no-deps $EDITABLE_FLAG "$_install" || die "openjiuwen local install failed"
    return 0
  fi

  die "git not found and no local agent-core. Options:
  1) export PATH=${OHOS_HNP_BIN}:\$PATH   (cmd-pkgs git 常见路径)
  2) export GIT_EXECUTABLE=/path/to/git
  3) copy agent-core to $OFFICE_CLAW/agent-core then USE_LOCAL_OPENJIUWEN=1
  4) Windows zip: https://gitcode.com/openJiuwen/agent-core/-/tree/enterprise-dev"
}

run_wheel_preload_phase() {
  [ -f "$WHEEL_PRELOADER" ] || die "wheel preloader not found: $WHEEL_PRELOADER"
  mkdir -p "$REPORT_DIR/phase-0"
  log "======== phase 0: preload native wheels (first) ========"
  REPORT_DIR="$REPORT_DIR/phase-0" \
    PYTHON="$PYTHON" \
    WHEEL_DIR="$WHEEL_DIR" \
    OHOS_REAL_PYTHON="$OHOS_REAL_PYTHON" \
    sh "$WHEEL_PRELOADER" || die "native wheel preload failed"
}

# ---------- 0. 解析 Python / venv ----------
BASE_PY=$(resolve_base_python) || die "set OHOS_REAL_PYTHON=/path/to/python3.12"
BASE_PY=$(readlink -f "$BASE_PY" 2>/dev/null || echo "$BASE_PY")
export OHOS_REAL_PYTHON="$BASE_PY"

if [ "$CREATE_VENV" = "1" ]; then
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    log "create venv: $VENV_DIR (base $BASE_PY)"
    "$BASE_PY" -m venv "$VENV_DIR"
  else
    log "reuse venv: $VENV_DIR"
  fi
  export PYTHON="$VENV_DIR/bin/python"
  # 勿 readlink -f venv/bin/python：会解析到 OHOS_REAL_PYTHON，pip 装到系统 Python
  patch_venv_activate_ohos "$VENV_DIR"
else
  export PYTHON="${PYTHON:-$BASE_PY}"
  PYTHON=$(readlink -f "$PYTHON" 2>/dev/null || echo "$PYTHON")
  export PYTHON
  log "CREATE_VENV=0 PYTHON=$PYTHON"
fi

log "install-ohos-agentserver (2026-05-30-wheels-first)"
log "REPO_ROOT=$REPO_ROOT"
log "OFFICE_CLAW=$OFFICE_CLAW"
log "WHEEL_DIR=$WHEEL_DIR"
log "PYTHON=$PYTHON ($("$PYTHON" --version 2>&1))"
log "OHOS_REAL_PYTHON=$OHOS_REAL_PYTHON"
ensure_ohos_tool_path
if _git_probe=$(find_cmd_git 2>/dev/null); then
  log "git probe: $_git_probe ($("$_git_probe" --version 2>&1 | head -1))"
else
  log "git probe: NOT FOUND (phase 2 将尝试本地 agent-core 或报错)"
fi

# ---------- 0. preload native wheels（最先，避免后续 pip 拉 PyPI 覆盖）----------
if [ "${SKIP_PHASE0:-0}" != "1" ]; then
  run_wheel_preload_phase
else
  log "SKIP phase 0 (native wheels preload)"
fi

# ---------- 1. requirements-minimal（逐包 + 传递依赖）----------
if [ "${SKIP_PHASE1:-0}" != "1" ]; then
  run_manifest_phase 1 agentserver-minimal
else
  log "SKIP phase 1 (requirements-minimal manifest)"
fi

# ---------- 2. openjiuwen --no-deps ----------
if [ "${SKIP_PHASE2:-0}" != "1" ]; then
  log "======== phase 2: openjiuwen --no-deps ========"
  install_openjiuwen
  if ! "$PYTHON" -c "import openjiuwen; print('openjiuwen OK', openjiuwen.__file__)" 2>/dev/null; then
    log "WARN: import openjiuwen failed (may need phase 3 deps)"
  fi
else
  log "SKIP phase 2 (openjiuwen)"
fi

# ---------- 3. agentcore-minimal（harmonyos/pyproject.toml − requirements-minimal，含传递依赖）----------
if [ "${SKIP_PHASE3:-0}" != "1" ]; then
  run_manifest_phase 3 agentcore-minimal
else
  log "SKIP phase 3 (agentcore-minimal manifest)"
fi

# ---------- 4. jiuwenclaw --no-deps -e . ----------
if [ "${SKIP_PHASE4:-0}" != "1" ]; then
  log "======== phase 4: jiuwenclaw --no-deps $EDITABLE_FLAG . ========"
  ensure_pep517_minimal
  pip_in_venv --no-deps $EDITABLE_FLAG "$REPO_ROOT" || die "jiuwenclaw install failed"
else
  log "SKIP phase 4 (jiuwenclaw -e .)"
fi

# ---------- 5. 启动前验证 ----------
log "======== verify ========"
_fail=0
_venv_sp=$("$PYTHON" -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)
log "  PYTHON=$PYTHON"
log "  site-packages=${_venv_sp:-unknown}"
case "${_venv_sp:-}" in
  "$VENV_DIR"/*) ;;
  *)
    log "  WARN: site-packages 不在 $VENV_DIR 下 — Phase 1/3 可能装到了系统 Python，请 RECREATE_VENV=1 重装"
    _fail=$((_fail + 1))
    ;;
esac
_verify_ld=$(ohos_native_ld_library_path 2>/dev/null || true)
for _mod in openjiuwen jiuwenclaw pydantic pydantic_core sqlalchemy greenlet openai lupa.luajit21 fastmcp cryptography; do
  if env LD_LIBRARY_PATH="${_verify_ld:-${LD_LIBRARY_PATH:-}}" \
    "$PYTHON" -c "import ${_mod}" 2>/dev/null; then
    log "  import ${_mod}: OK"
  else
    log "  import ${_mod}: FAIL"
    _fail=$((_fail + 1))
  fi
done

if command -v jiuwenclaw-agentserver >/dev/null 2>&1; then
  log "  jiuwenclaw-agentserver: $(command -v jiuwenclaw-agentserver)"
else
  log "  jiuwenclaw-agentserver: MISSING (check venv bin in PATH)"
  _fail=$((_fail + 1))
fi

_libdir=$("$OHOS_REAL_PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")' 2>/dev/null || true)
echo ""
echo "完成。报告目录: $REPORT_DIR"
echo ""
echo "启动 AgentServer:"
echo "  cd $REPO_ROOT"
echo "  source $VENV_DIR/bin/activate"
echo "  export LD_LIBRARY_PATH=\"${_libdir}\${OPENSSL_DIR:+:\$OPENSSL_DIR/lib}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\""
echo "  jiuwenclaw-agentserver --port 18092"
echo ""

[ "$_fail" -eq 0 ] || exit 1
exit 0
