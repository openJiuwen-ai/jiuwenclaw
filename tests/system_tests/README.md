# System Tests (ST) - JiuwenClaw

## 概述

系统测试（System Tests，简称ST）是JiuwenClaw项目中的高级测试阶段，用于验证完整的功能集成和端到端场景。

## 测试位置

- **测试目录**: `tests/system_tests/`
- **测试标记**: `@pytest.mark.st`
- **配置文件**: `conftest.py`

## 可用系统测试

### 1. 工作空间初始化测试
- **文件**: `test_init_workspace.py`
- **测试数量**: 19个
- **功能**: 测试用户工作空间初始化、语言选择、配置生成

### 2. ACP CLI测试
- **文件**: `test_acp_cli_stdio.py`
- **功能**: 测试Agent Communication Protocol (ACP) 的CLI标准输入输出

### 3. CLI通道WebSocket测试
- **文件**: `test_cli_channel_ws.py`
- **功能**: 测试CLI通道的WebSocket连接和通信

### 4. 编码记忆测试
- **文件**: `test_coding_memory.py`
- **功能**: 测试编码相关的记忆功能

### 5. Todo隔离计划测试
- **文件**: `test_todo_isolation_plan_vs_skill.py`
- **功能**: 测试Todo隔离计划与技能的交互

## 运行方式

### 方法1: 使用专用脚本（推荐）
```bash
# 运行所有ST测试
./run_st_tests.sh

# 查看脚本内容
cat run_st_tests.sh
```

### 方法2: 直接使用pytest
```bash
# 运行所有ST测试
python -m pytest tests/system_tests/ -m st -v

# 运行特定ST测试文件
python -m pytest tests/system_tests/test_init_workspace.py -v

# 运行单个ST测试
python -m pytest tests/system_tests/test_init_workspace.py::TestResolvePreferredLanguage::test_resolve_explicit_language -v

# 带覆盖率报告
python -m pytest tests/system_tests/ -m st --cov=jiuwenclaw --cov-report=html:htmlcov-st
```

### 方法3: 使用CI/CD
```bash
# GitLab CI自动运行
git push  # 自动触发CI pipeline

# 手动触发ST测试阶段
# 在GitLab UI中: CI/CD -> Pipelines -> 点击对应pipeline -> 手动触发system-test阶段
```

## CI/CD集成

### GitLab CI配置
项目包含 `.gitlab-ci.yml` 配置文件，定义了以下阶段：

1. **lint** - 代码质量检查
2. **unit-test** - 单元测试（UT）
3. **system-test** - 系统测试（ST）
4. **integration-test** - 集成测试

### ST测试阶段配置
```yaml
system-test:
  stage: system-test
  script:
    - python -m pytest tests/system_tests/ -m st -v --tb=short
  artifacts:
    paths:
      - htmlcov-st/           # 覆盖率HTML报告
      - coverage-st.xml       # 覆盖率XML报告
```

## 测试标记说明

| 标记 | 说明 | 路径 |
|------|------|------|
| `st` | 系统测试 | `tests/system_tests/` |
| `unit` | 单元测试 | `tests/unit_tests/`, `tests/unit/` |
| `integration` | 集成测试 | `tests/integration/` |

## 环境要求

- Python 3.11+
- 所有开发依赖: `pip install -e .[dev]`
- pytest配置: `pytest.ini`

## 故障排查

### 问题1: ST测试显示NOT_FOUND
**原因**: CI配置未正确设置ST测试路径
**解决**: 确保 `.gitlab-ci.yml` 包含 `tests/system_tests/` 路径

### 问题2: 导入错误
**原因**: 缺少依赖或环境配置问题
**解决**: 运行 `pip install -e .[dev]` 重装依赖

### 问题3: 测试超时
**原因**: ST测试可能需要较长时间
**解决**: 增加 `--timeout` 参数值或优化测试

## 相关文档

- [pytest.ini](../../pytest.ini) - pytest主配置文件
- [.gitlab-ci.yml](../../.gitlab-ci.yml) - GitLab CI配置
- [conftest.py](conftest.py) - ST测试fixtures和配置

## 维护指南

### 添加新的ST测试
1. 在 `tests/system_tests/` 创建新文件
2. 添加 `@pytest.mark.st` 装饰器
3. 使用 `conftest.py` 中的共享fixtures
4. 更新此README文档

### 测试命名规范
- 文件名: `test_*.py`
- 类名: `Test*`
- 方法名: `test_*`

### 使用Fixtures
```python
def test_my_feature(clean_environment, temp_home):
    # clean_environment: 提供干净的测试环境
    # temp_home: 临时HOME目录
    pass
```

## 联系方式

如有问题，请在项目Issue中标记 `system-tests` 标签。
