# Skill、SkillDev 与来源管理模块设计说明书

> 本分册来自 149 个 `server/runtime/**/*.py` 文件的逐文件源码取证。全部类、函数、方法、字段与准确签名见[Runtime Core Python API](../interfaces/03-runtime-core-api.md)和[Skill Runtime Python API](../interfaces/04-skill-runtime-api.md)。

## 5. 普通 Skill 运行时

### 5.1 状态、扫描与元数据

- `SkillManager` 的事实源是 tenant workspace `skills/` 与 `skills/skills_state.json`；状态含 installed plugins、local skills、每 skill enabled、marketplace、ClawHub token 等。构造时创建目录、加载/规范化状态、注册默认 SwarmSkillHub source、补登记盘上孤儿技能；企业版再复制/登记 builtin（[`skill/skill_manager.py:473`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L473)）。
- 扫描分 local、builtin、marketplace；每个目录优先 `SKILL.md`，否则第一个 `*.md`。frontmatter 用 YAML，解析失败退回简单 `key:value`；统一 `name/description/version/author/tags/allowed_tools/body/path`。无 frontmatter 时目录名最终是权威 skill name（[`skill_manager.py:3939`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3939)）。
- 安装记录身份优先 `origin`，无 origin 才按 name；这是 ClawHub 同名不同 slug 能共存的基础。展示元数据会把 origin/display/market short description 从 ledger 回填（[`skill_manager.py:6333`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6333)）。
- [`state_utils.py`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py) 负责 enabled/disabled 的纯状态变换；执行禁用集只返回当前仍安装的技能，防止卸载后的旧 disabled 配置幽灵化。写状态由 `_save_state()` 统一执行。

### 5.2 CRUD、安装与依赖边界

- list/get/toggle 是“扫描盘 + ledger”组合视图；install/import/web/source/Hub 最终都将归档安全解压到临时目录，验证存在可解析 skill 文件、检查名称冲突，再移动到 `skills/` 并 upsert ledger。失败时尽量移除 staging/目标半成品；路径都经 `_safe_path_name/_safe_child_path` 防穿越（[`skill_manager.py:780`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L780)）。
- uninstall 先按 origin/name 定位安装实体；builtin/prebuilt 的删除策略与 user/marketplace 不同。磁盘删除与 ledger 删除发生在同一 handler，但不是数据库事务；代码对复制/删除/状态写失败分别返回结构化结果，并在关键“user→prebuilt 提升”失败时恢复旧 user 记录。
- 普通 Skill 的“依赖”并非自动 pip/npm 安装：runtime 管理的可执行边界主要是 frontmatter `allowed_tools`、SkillUse rail 的可见/加载集合、workspace 目录与 credential injection rail。SkillDev 的 `SkillDevDeps` 是开发流水线依赖注入容器，不是安装包解析器（[`skilldev/deps.py:22`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L22)）。
- facade 对 `skills.*` RPC 调 `SkillManager`；改变磁盘/启用状态后触发 `refresh_skill_rails` 或必要的 agent reload，`SkillUseRail.reload_skills()` 才让正在运行的 adapter 重扫（[`agent_adapter/interface.py:1962`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1962)、[`interface_deep.py:6304`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6304)）。运行时实际链为：可见技能筛选 → prompt rail 暴露元数据 → 模型调用 `skill_tool/list_skill/load_skill` → rail 按需读取 SKILL.md/资源 → 工具执行仍受 permission/credential rails。

### 5.3 白名单、Source/Hub、Bundle 与凭证

- 企业白名单只对非 default/global ACP 的 enterprise tenant 生效。同步按真实 `skills_dir` 取得进程内 asyncio 锁，逐条比较 `skill_id/version/sha256` 与 ledger/目录；需要时在线程下载，成功后登记 source_type=`prebuilt`，最后硬删模板不再包含的旧 prebuilt（[`skill_whitelist.py:34`](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L34)）。
- 同名 user 可被 prebuilt 提升；登记失败会恢复 user ledger，并在本轮下载过时删除新目录。单 skill 失败记入 `SkillWhitelistSyncResult`，不阻止其他项；无法读取 ledger 时整轮停止，避免盲删。
- `SourceRegistry` 注册带 capability/priority/trust/download policy 的 provider；应用 enterprise extension config 时先构造 candidate registry，全部验证/绑定成功后原子替换并关闭旧 registry。endpoint/auth 只接受显式引用，列表接口不泄露 endpoint/凭证（[`source_registry.py:36`](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L36)、[`skill_manager.py:531`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L531)）。
- `SwarmSkillHubProvider` 提供 search/check_updates/get_artifact；HTTP/JSON/API code/字段不合法均转明确 `RuntimeError`。下载 descriptor 经 size/host policy、SHA-256 与可选/强制 HMAC 校验；secret 只由 `env://UPPER_CASE_NAME` 解析，审计结果不含 secret（[`sources/swarm_skill_hub.py:68`](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L68)、[`artifact_security.py:27`](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L27)）。
- Team Skills Hub 的 bundle 流程包括 init/validate/pack/search/install/publish/delete；pack/publish zip 明确 staging root、版本元数据与排除项，下载再复用安全解压。SkillNet 安装是进程共享 job 表 + 后台线程/任务，立即返回 install_id，成功 hook 刷新 agent；状态可轮询（[`skill_manager.py:1643`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1643)）。
- ClawHub token 存在 `skills_state.json`，读取 API 只返回掩码；真正 token 用于 CLI/HTTP 调用。DeepAdapter 的 `SkillCredentialInjectionRail` 在执行前按 skill 需要注入凭证，manager 状态与 trace/history 的脱敏逻辑避免把 secret 回显（[`skill_manager.py:1738`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1738)、[`jiuwenswarm/server/runtime/agent_manager.py:50`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L50)、[`interface_deep.py:6382`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6382)）。

### 5.4 搜索、快照、进化与版本

- 搜索入口覆盖 marketplace、online 聚合、SkillNet、ClawHub、Team Skills Hub 和通用 Source SPI；各后端失败被归一为候选级错误，聚合搜索可返回部分结果。retrieval status/index build/cancel/search/tree 转交运行时检索服务（[`skill_manager.py:1029`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1029)）。
- “快照”有三类：skills_state ledger 是安装/启用快照；Team skill bundle 是可搬运目录快照；evolution 使用 body archive/version store 保存可 rollback 的历史版本。[`evolution_version.py`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py) 验证 skill path 在可信 roots，rollback 恢复 archive，rebuild 则准备 follow-up、执行 merge rewrite、最终落版本（[`agent_adapter/evolution_version.py:36`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L36)）。
- evolution slash (`/evolve`, list/simplify/rebuild/rollback) 与 RPC 共用 validation/status helpers；审批记录可 group、approve/reject，进度通过 push/broadcast。builtin evolution 有显式 guard，disk-only RPC 在未缓存 agent 时可创建临时 facade，但必须绑定本 session 的 registered skill roots（[`evolution_helpers.py:138`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L138)、[`evolution_slash.py:40`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L40)、[`session_skill_dirs.py:17`](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L17)）。
- `skilldev` 是独立持久状态机：INIT→PLAN→GENERATE→VALIDATE→TEST_DESIGN→TEST_RUN→EVALUATE→IMPROVE/DESC_OPTIMIZE→PACKAGE；每阶段返回 `StageResult`，pipeline checkpoint 到任务目录，resume 根据 suspension config 恢复。cancel handler 当前只确认“收到”，实际取消待完善（[`skilldev/pipeline.py:47`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L47)、[`skilldev/service.py:55`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L55)）。


## 运行时一致性与已知边界

Skill 的一致性、失败回退和并发约束还受全局运行时策略影响；详见[Runtime 分册的失败与一致性总览](03-runtime-session-agent.md#8-失败并发与一致性总览)。

- SkillDev 的 cancel handler 当前只确认收到请求，尚未实际取消 pipeline task。
- 普通 Skill 安装没有通用 requirements 自动安装器；依赖边界是 allowed tools、rails 和显式开发流水线注入。
- 企业白名单同步按真实 skill 根目录串行化，并以 artifact 校验及可信 source policy 作为下载边界。
