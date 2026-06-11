#!/bin/bash
set -e  # 遇到错误立即退出

# ===================== 1. 核心变量 =====================
TEST_DIR="./marketplace/dispatch/tests"
REPORT_HTML="./outputs/unit_test_report.html"
REPORT_XML="./outputs/unit_test_result.xml"
ut_result_value="PASSED"
UV_VENV_DIR="./.venv"

# ===================== 2. 基础配置（华为云源）=====================
echo "===== 配置pip源 & 安装基础工具 ====="
pip config set global.index-url https://repo.huaweicloud.com/repository/pypi/simple
pip config set global.trusted-host repo.huaweicloud.com

# 升级系统setuptools
pip uninstall -y setuptools
pip install setuptools==68.0.0 wheel==0.41.2

# 安装uv（指定低版本兼容版本）
pip install uv -i https://repo.huaweicloud.com/repository/pypi/simple
UV_PATH=$(pip show uv | grep "Location" | awk -F ': ' '{print $2}')
export PATH="$UV_PATH/bin:$PATH"
echo "当前uv版本：$(uv --version)"

# ===================== 3. 重建虚拟环境（低版本uv兼容）=====================
echo -e "\n===== 创建uv虚拟环境 ====="
rm -rf "${UV_VENV_DIR}"
uv venv "${UV_VENV_DIR}"  # 低版本uv仅支持创建，无额外参数

# 验证虚拟环境Python存在
if [ ! -f "${UV_VENV_DIR}/bin/python" ]; then
  echo "❌ 虚拟环境创建失败：${UV_VENV_DIR}/bin/python 不存在"
  exit 1
fi
echo "✅ 虚拟环境创建成功：${UV_VENV_DIR}"

# ===================== 4. 激活环境 + 安装依赖（低版本核心逻辑）=====================
echo -e "\n===== 激活环境并安装依赖 ====="
# 激活环境（低版本必须先激活，再装依赖）
source "${UV_VENV_DIR}/bin/activate"
echo "✅ 已激活虚拟环境，当前Python：$(which python)"

# 1. 同步pyproject.toml依赖（低版本uv sync无--venv参数）
uv cache clean
uv sync --all-extras --group dev --index-url https://repo.huaweicloud.com/repository/pypi/simple

# 2. 用虚拟环境的pip安装pysqlite3-binary（核心，低版本必须手动装）
pip install pysqlite3-binary==0.5.4.post2 --force-reinstall -i https://repo.huaweicloud.com/repository/pypi/simple

# 3. 用虚拟环境的pip安装pycryptodome（兜底）
pip install pycryptodome>=3.23.0 --force-reinstall -i https://repo.huaweicloud.com/repository/pypi/simple

# 4. 安装测试依赖（确保pytest等在虚拟环境中）
pip install pytest pytest-html pytest-cov pytest-timeout -i https://repo.huaweicloud.com/repository/pypi/simple

# ===================== 5. 前置验证（关键！）=====================
echo -e "\n===== 验证依赖安装 ====="
# 验证pysqlite3
python -c "
import pysqlite3
import sqlite3
print(f'✅ pysqlite3导入成功，sqlite3版本：{sqlite3.sqlite_version}')
" || {
  echo "❌ pysqlite3导入失败！"
  pip list | grep pysqlite3
  exit 1
}

# 验证Crypto
python -c "
import Crypto
from Crypto.Cipher import AES
print('✅ Crypto模块导入成功')
" || {
  echo "❌ Crypto导入失败！"
  pip list | grep pycryptodome
  exit 1
}

# ===================== 6. 执行测试（用激活的Python）=====================
echo -e "\n===== 执行单元测试 ====="
mkdir -p ./outputs
set +e

# 执行测试（全程用激活的Python，无路径问题）
python -c """
import pysqlite3
import sys
sys.modules['sqlite3'] = pysqlite3
import sqlite3
print(f'✅ 替换后sqlite3版本：{sqlite3.sqlite_version}')

from pytest import main
exit_code = main([
    '${TEST_DIR}',
    '--html=${REPORT_HTML}',
    '--junitxml=${REPORT_XML}',
    '--cov=${TEST_DIR}',
    '--timeout=60'
])
sys.exit(exit_code)
"""

TEST_EXIT_CODE=$?
set -e

# ===================== 7. 结果处理 =====================
echo -e "\n===== 测试结果处理 ====="
echo "测试退出码：${TEST_EXIT_CODE}"

if [ -f "${REPORT_XML}" ]; then
  head -n 10 "${REPORT_XML}"
else
  echo "❌ 报告文件不存在"
  TEST_EXIT_CODE=1
fi

if [ $TEST_EXIT_CODE -ne 0 ]; then
  ut_result_value="FAILED"
  echo "##vso[task.setvariable variable=ut_result;]${ut_result_value}"
  exit 1
else
  echo "✅ 所有单元测试通过！"
fi 解释代码