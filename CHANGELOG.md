# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### 新功能 (2026-05-13)

#### AgentServer层user_visible标记覆盖补充

- **覆盖率提升**
  - AgentServer层user_visible标记从66处提升到82处（+16处标记）
  - 覆盖率从约68%提升到约75%（+7%）
  - 补充关键路径：Deep Agent执行、Skill管理、Agent生命周期、工具调用权限

- **新增Rail层权限检查可见性** ✨
  - PermissionRail.before_tool_call添加权限拒绝标记
  - HITL用户确认场景添加等待确认标记
  - 用户体验改进：明确区分"权限被拒绝"vs"工具执行失败"

- **Deep Agent执行层标记** (4处)
  - Agent执行开始/成功/失败：critical标记
  - 模型配置应用：progress标记
  - 文件：`jiuwenclaw/agentserver/deep_agent/interface_deep.py`

- **Skill管理层标记** (15+处)
  - Skill安装/卸载/内置安装：critical标记
  - SkillNet/ClawHub操作：critical+progress标记
  - 文件：`jiuwenclaw/agentserver/skill_manager.py`

- **Agent管理层标记** (5处)
  - Agent复用：progress标记
  - Code模式switch开始/完成：progress+critical标记
  - 文件：`jiuwenclaw/agentserver/agent_manager.py`

- **WebSocket消息处理标记** (5处)
  - JSON解析成功/失败：progress+critical标记
  - E2A协议解析：progress标记
  - 流式响应开始/进度：critical+progress标记
  - 文件：`jiuwenclaw/agentserver/agent_ws_server.py`

- **消息处理准备标记** (3处)
  - Adapter初始化、Inputs构建、Memory hook：progress标记
  - 文件：`jiuwenclaw/agentserver/interface.py`

- **其他增强功能**
  - Team模式启用/后续请求：critical+progress标记
  - MultiSessionToolkit注册失败：critical标记
  - 定时工具注册失败：critical标记
  - 模型配置解析：progress标记
  - LLM迭代进度：progress标记（每5次迭代）
  - Evolution操作进度：progress标记

**测试验证**：
- 语法检查：所有修改文件通过Python语法检查
- 覆盖率统计：82处user_visible标记（+16处Rail层标记）
- 性能影响：预计<0.001ms per log（仅添加字符串格式化）

**相关文件修改**：
- `jiuwenclaw/agentserver/deep_agent/interface_deep.py`
- `jiuwenclaw/agentserver/deep_agent/rails/permission_rail.py`
- `jiuwenclaw/agentserver/skill_manager.py`
- `jiuwenclaw/agentserver/agent_manager.py`
- `jiuwenclaw/agentserver/agent_ws_server.py`
- `jiuwenclaw/agentserver/interface.py`

### Bug修复 (2026-05-11)

#### user_visible标签系统修复

- **修复重复标签问题**
  - 架构优化：从修改`record.msg`改为设置`record.user_tag`字段
  - 解决多handler场景下的标签重复添加（从最多6个减少到1个）
  - 提高日志可读性，消除重复标签干扰

- **修复标签位置问题**
  - Formatter格式修改：`%(levelname)s %(user_tag)s%(name)s: %(message)s`
  - 标签位置从消息部分移到logger名称前作为修饰符
  - 实现预期格式：`INFO [USER] jiuwenclaw.gateway.channel_manager: 消息`

- **性能优化**
  - 减少字符串操作：从N次（N个handler）减少到1次字段设置
  - 提升日志输出性能：平均延迟 < 0.05ms

- **配置系统完善**
  - 恢复`LoggingTagConfig.__init__()`方法，确保配置正确加载
  - 验证环境变量覆盖功能正常工作
  - `logging.tags`配置对所有模式生效（text/json/dual）

- **JSON格式null值抑制（新增）**
  - 修复JSON输出中总是包含`"user_visible": null`的问题
  - 修改`JsonUserVisibleFormatter.add_fields()`逻辑
  - 只在`user_visible`有有效值时才添加字段到JSON输出
  - 实现三种状态：`"user_visible": "critical"`、`"user_visible": "progress"`或无`user_visible`字段
  - 提高JSON清洁度，减少无效字段输出

**修复前**：`INFO logger: [USER] [USER] [USER] [USER] [USER] [USER] 消息`
**修复后**：`INFO [USER] logger: 消息`

**JSON修复前**：`{"message": "...", "user_visible": null, ...}`
**JSON修复后**：`{"message": "...", ...}` (无user_visible字段)

**测试覆盖**：
- 新增单元测试：27个测试（19个原有 + 8个JSON null抑制）
- 集成测试：所有模式（text/json/dual）验证通过
- JSON格式化器专项测试：8个新测试验证null抑制功能
- 回归测试：124个现有测试通过，无破坏性变更

**相关提交**：
- `0bfcc493` - 主要修复提交（重复标签 + 位置错误）
- `[pending]` - JSON null值抑制修复

---

### 日志系统迁移 (2026-05-09)

#### 新增功能

- **JSON日志格式化输出**
  - `JsonUserVisibleFormatter` 类：继承 pythonjsonlogger.JsonFormatter
  - JSON 结构化字段输出（timestamp/level/logger/component/message/user_visible/exc_info）
  - 时间戳格式支持（text/iso8601）
  - 组件分类自动推导（gateway/channel/agent_server/permissions）
  - 敏感数据自动脱敏
  - 异常信息简化处理
  - 中文编码支持（json_ensure_ascii=False）

- **用户可见性标记系统**
  - `UserVisibleTagFilter` 类：添加 [USER]/[USER_PROGRESS] Tag
  - `LoggingTagConfig` 类：配置管理（环境变量 + config.yaml）
  - Tag 显示控制（user_visible/user_progress_visible开关）
  - 配置优先级（环境变量 > config.yaml > 默认值）

- **统一日志格式配置**
  - `format` 配置项三值语义（text/json/dual）
  - `console_enabled`/`file_enabled` 输出开关
  - 环境变量覆盖机制（JIUWENCLAW_LOG_FORMAT等）
  - format与开关正交组合控制
  - 双模式文件输出（format=dual时同时生成.log和.json）

- **数据流日志标记实现** ✨ NEW
  - 修复 UserVisibleTagFilter bug：所有文件 handler 现在都包含 Tag 过滤器
  - 在数据流各环节添加 53 个 user_visible 日志标记：
    - Channel 层（14 个标记）：WebSocket/ACP/Desktop 消息处理
    - Gateway 层（24 个标记）：消息派发/处理/路由
    - Server 层（15 个标记）：Agent 管理/WS 连接/工具执行
  - 工具执行日志标记：开始执行/完成/失败均标记为 critical
  - 日志分类标准：
    - [USER] Tag (`user_visible='critical'`)：关键用户操作
    - [USER_PROGRESS] Tag (`user_visible='progress'`)：进度信息
    - 无 Tag：技术内部日志

#### 技术决策

本次迁移采用**重新实现**策略而非merge backup分支，原因：
- develop分支经历重大架构重构（目录结构优化）
- 87%的目标文件路径发生变更（utils.py → common/utils.py）
- 强行merge成本 > 重新实现成本
- 新架构下重新实现确保代码一致性

#### 原作者贡献

基于develop分支开发，感谢原作者MaGang：
- 00cb349b: 日志系统完整改造
- a694b00d: JSON配置支持
- 889673b8: Channel层user_visible标记
- daf73f48: Gateway层user_visible标记
- d2ac71f2: Server层user_visible标记
- a2df5634: 文档更新

#### 配置示例

```yaml
logging:
  format: dual  # text/json/dual
  console_enabled: true
  file_enabled: true
  json:
    timestamp_format: text
    include_component: true
  tags:
    user_visible: true
    user_progress_visible: true
```

#### 环境变量覆盖

```bash
export JIUWENCLAW_LOG_FORMAT=dual
export JIUWENCLAW_LOG_CONSOLE_ENABLED=true
export JIUWENCLAW_LOG_FILE_ENABLED=true
export JIUWENCLAW_LOG_USER_VISIBLE=true
export JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE=true
```
