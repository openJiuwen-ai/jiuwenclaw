# 永续会话迁移至 workswarm_0.2.6.beta2

日期：2026-09-03。基线：`upstream/workswarm_0.2.6.beta2`，`dd4a13d76ed606a018cb03e46e58bbb25078e1ed`。

## 使用入口

Web/桌面端在单 Agent 的新会话页面直接输入 `/persist <第一条任务>`。前端剥离控制前缀，在 `session.create` 中发送 `persist_session=true`，标题和首条消息只保留任务正文。单独输入 `/persist` 会提示补充任务，不创建会话。后续消息无需再带前缀；开关来自服务端持久化元数据，不能通过后续请求更改。普通新会话默认关闭，不修改用户的 `config.yaml`。

TUI 与受控 IM（包括飞书）保留 `/persist <第一条任务>`。IM 通过已有控制命令分发链路新建映射会话，再将任务正文送入 Agent；数字分身链路保留发送者身份。飞书可获取姓名时使用姓名，否则回退为 Open ID 尾号标签。

本次不迁入 Web `/btw`、`/compact`、`/plan` 的统一 slash picker，也不新增 `commands.list` 菜单元数据。beta2 原有 Plan 入口不变。

## 提交来源

| 来源提交 / PR | 纳入内容 |
| --- | --- |
| `8ff610e1b` / !5057 | 可插拔 Rail、前台注入、后台 Extractor/Builder、完整证据、动态 memory-cli、会话创建/预热协议及验收脚本 |
| `a1981719a` / !5181 | Web 文本 `/persist` 与 TUI 命令入口 |
| `02957a531` / !5558 | 连续性、并发证据、上下文替换、交互恢复、模型跟随、预取容错、历史 payload 修复、IM 建会话和身份注入；排除依赖 Web picker 的代码和专属测试 |
| `cf9c5560f` / !5748 | 飞书姓名与 Open ID 尾号兜底 |

补齐前置依赖：从 `280a267fd` 的 E2A 适配中仅迁入 `user_id=str(env.user_id or "")` 透传，保证 Gateway 认证的用户身份进入 AgentRequest；不迁该提交的 Heartbeat 功能。没有这处适配，正文身份标签正确，但请求身份字段为空，身份集成用例会失败。

beta2 中 `de7529b72` / !5387 曾加入功能，随后 `cc575568d` / !5393 完整撤销；因此本次从实际无功能的分支树迁入，而非按提交标题判断已经包含。未纳入此前明确排除的 `128eeba1e` 滚动行为修改，亦不迁文章素材、实验数据、用户配置或其他未提交改动。

## beta2 适配

- `App.tsx` 保留 beta2 原有创建链路，不引入来源分支独有的 `view_id` 或平台导航接口。
- `interface_deep.py` 同时保留 beta2 的模型上下文窗口字段与永续会话的活动请求模型；清理逻辑仅加入 Rail 收尾，不调用 beta2 不存在的演化后台清理方法。
- Gateway 使用 beta2 既有模式枚举。来源测试中的永续创建和异常隐藏用例单独放入 `test_message_handler_persist.py`，不迁入无关的新模式矩阵。
- 移除仅供旧永续开关标签使用的 CSS，前端不引入菜单入口。
- Rail、后台提示词、动态 Skill/CLI、自然任务验收脚本保持来源内容，不改语义策略。

## 验证与限制

本次只进行迁移回归，不启动此前已停止的真实模型实验，不触碰现有飞书会话。200 轮 Web/TUI × Work/Code 真实模型验收尚未在本次 beta2 迁移上完成，不能沿用其他分支实验作为通过证据。

环境：Windows，Python 3.12.12。beta2 的 `pyproject.toml` 声明包名 `workswarm`，但 `uv.lock` 仍记录 `jiuwenswarm`，原样 `uv sync --frozen --group test` 失败。本次不修改发布依赖，复用已有测试依赖，并将 beta2 锁定的 Core `ce21a9b7cfcce28923fba6c47758d60c624b69be` 安装至隔离目录，通过 `PYTHONPATH` 优先加载。用例数据路径独立。

默认 pytest 警告策略还会将该 Core 的 docstring 非法转义警告提升为收集错误。诊断回归仅添加 `-W "default:invalid escape sequence:SyntaxWarning"`，不修改依赖源码、pytest 配置或测试断言。此结果不等同于原始默认环境全部通过。

Web 会话创建与入口设置共 12 项通过（含新补的文本重试、Work/Code 开关隔离测试）；Web TypeScript/Vite 构建通过，存在原有重复 i18n 键、动态导入和包体积警告。TUI 构建和官方完整 `npm test` 通过。

后端首轮诊断回归为 332 通过、3 失败：两项是上述 E2A 用户身份前置依赖缺失，另有 `TestIdentityPreservation.test_write_is_atomic_no_empty_window` 在 Windows 并发读取元数据时 `os.replace` 返回 `PermissionError [WinError 5]`。后者在此次迁移前的其他上游版本也已复现，位于本次未改变的公用原子写入路径；本次不扩大迁移为会话存储重构，保留失败记录，不删除或跳过用例。

补齐 E2A 依赖后的最终回归：上述 9 个后端模块 **334 通过、1 失败**，唯一失败仍为 Windows 元数据原子替换；独立 E2A 全目录 **65 通过**。其中 Rail 专项 48 项全部通过，IM 身份注入 5 项全部通过，IM 永续创建/安全错误提示 3 项全部通过。合计后端 399 通过、1 个已知失败，不是全仓 UT 或整体验收通过。

本地 JUnit 证据保留于迁移工作区 `.venv/beta2-persist-tests.xml`（首次收集错误）、`.venv/beta2-persist-tests-retry.xml`（前置依赖缺失）、`.venv/beta2-persist-final.xml`（最终 334/1）及 `.venv/beta2-e2a-tests.xml`（65/0），不将环境文件打入发布提交。后端命令使用 `-o addopts= --tb=short -q -o log_cli=false -W "default:invalid escape sequence:SyntaxWarning"`；模块清单为 Rail、IM identity、IM persist、slash parser、Feishu adapter、Session metadata、warm pool、AgentServer ACP、Plan orchestration，以及单独运行的 `tests/unit_tests/e2a`。

项目维护记录：使用 expanded 范围维护现有 `.doc_project_maintainer/`；本次影响的会话、身份链路及已有符号说明已同步，全局覆盖仍为 partial。未创建签名密钥、未修改审计签名或提升符号审计状态。当前结果仅支持“代码迁移及上述局部回归完成”，不支持“真实模型整体验收通过”。
