# JiuwenClaw测试清单 (Test Manifest)

## 测试分类统计

### 单元测试 (Unit Tests - UT)
**路径**: `tests/unit_tests/`, `tests/unit/`
**状态**: ✅ 可用
**测试文件数**: 50+ 个测试文件
**总测试数**: 78+ 个测试用例

**主要测试模块**:
- `tests/unit_tests/test_utils.py` - 工具函数测试
- `tests/unit_tests/logging/` - 日志系统测试 (19个测试)
- `tests/unit_tests/agentserver/` - Agent服务器测试
- `tests/unit/storage/` - 存储模块测试 (37个测试)
- `tests/unit/` - 各模块单元测试

### 系统测试 (System Tests - ST)
**路径**: `tests/system_tests/`
**状态**: ✅ 可用
**测试文件数**: 7 个测试文件
**总测试数**: 39 个测试用例

**测试文件清单**:
1. ✅ `test_init_workspace.py` - 工作空间初始化测试 (19个测试)
2. ✅ `test_acp_cli_stdio.py` - ACP CLI标准输入输出测试
3. ✅ `test_cli_channel_ws.py` - CLI通道WebSocket测试
4. ✅ `test_coding_memory.py` - 编码记忆功能测试
5. ✅ `test_todo_isolation_plan_vs_skill.py` - Todo隔离计划测试
6. ✅ `verify_session_channel_telemetry.py` - 会话通道遥测验证
7. ✅ `verify_telemetry_e2e.py` - 遥测端到端验证

**ST测试标记**: `@pytest.mark.st`
**运行命令**: `python -m pytest tests/system_tests/ -m st -v`

### 集成测试 (Integration Tests)
**路径**: `tests/integration/`
**状态**: ✅ 可用
**测试文件数**: 1+ 个测试文件

**主要测试**:
- `test_acp_cli_stdio_integration.py` - ACP CLI集成测试

## CI/CD配置

### GitCode CI配置
**配置文件**: `.gitcode/workflows/ci.yml`
**配置文件**: `.gitcode/workflows/test.yml`

**测试流水线**:
1. **lint** - 代码质量检查
2. **unit-test** - 单元测试（UT）
3. **system-test** - 系统测试（ST）✅ 已配置
4. **integration-test** - 集成测试

### ST测试CI配置详情
```yaml
system-test:
  name: System Tests (ST)
  steps:
    - name: Verify ST test files exist
      run: |
        if [ -d "tests/system_tests" ]; then
          echo "✓ System tests directory found"
          find tests/system_tests/ -name "test_*.py" -type f
        fi

    - name: Run System Tests
      run: |
        python -m pytest tests/system_tests/ -v --tb=short
```

## 本地测试运行

### 运行所有UT测试
```bash
python -m pytest tests/unit_tests/ tests/unit/ -v
```

### 运行ST测试
```bash
# 方法1：直接运行
python -m pytest tests/system_tests/ -v

# 方法2：使用ST标记
python -m pytest tests/system_tests/ -m st -v

# 方法3：使用专用脚本
./run_st_tests.sh
```

### 运行特定测试
```bash
# 运行特定ST测试文件
python -m pytest tests/system_tests/test_init_workspace.py -v

# 运行单个测试
python -m pytest tests/system_tests/test_init_workspace.py::TestResolvePreferredLanguage::test_resolve_explicit_language -v
```

## 测试结果预期

### UT测试预期结果
- **状态**: ✅ PASSED
- **测试数**: 78+
- **覆盖率**: 70%+
- **报告**: htmlcov-ut/, coverage-ut.xml

### ST测试预期结果
- **状态**: ✅ PASSED (大部分通过)
- **测试数**: 39个 (33通过, 6跳过)
- **覆盖率**: 包含端到端场景覆盖
- **报告**: htmlcov-st/, coverage-st.xml

## 故障排查

### 如果CI显示 "ST测试 NOT_FOUND"
**原因**: CI配置未正确识别ST测试路径
**解决**:
1. 确认 `.gitcode/workflows/ci.yml` 文件存在
2. 确认 `tests/system_tests/` 目录存在
3. 确认pytest.ini包含system_tests路径
4. 检查CI配置中的ST测试运行命令

### 验证ST测试配置
```bash
# 检查ST测试目录
ls -la tests/system_tests/

# 验证pytest能收集ST测试
python -m pytest tests/system_tests/ --collect-only

# 运行ST测试验证
python -m pytest tests/system_tests/ -v
```

## 更新日期
- **创建**: 2026-05-14
- **最后更新**: 2026-05-14
- **维护者**: JiuwenClaw Team

## 相关文档
- [pytest.ini](../pytest.ini) - pytest主配置
- [.gitcode/workflows/ci.yml](../.gitcode/workflows/ci.yml) - CI配置
- [tests/system_tests/README.md](system_tests/README.md) - ST测试详细文档
