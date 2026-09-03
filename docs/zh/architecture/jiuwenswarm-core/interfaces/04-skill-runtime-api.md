# Skill 与 Skill Turbo Runtime Python API

覆盖普通 Skill 管理/执行能力、Skill Turbo 规划执行图及其内置 PPT 节点接口。

> 签名与行号取自当前源码 AST。这里同时列出公开和内部顶级接口；名称以下划线开头者是实现细节，不承诺稳定兼容。行为语义与调用约束请结合对应模块设计分册阅读。

## `jiuwenswarm/server/runtime/skill/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/__init__.py#L1)

**模块职责：** Skill 运行时模块 — 对外暴露公共工具函数.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill/__init__.py#L18) |

## `jiuwenswarm/server/runtime/skill/artifact_security.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L1)

**模块职责：** Common artifact integrity and SkillHub HMAC verification.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_SHA256_RE` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L21) |
| `_HMAC_ALGORITHM` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L22) |
| `_HMAC_ENCODING` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L23) |
| `_HMAC_SCOPE` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L24) |

### [`class ArtifactVerificationError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L27)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, code: str, message: str)` | 源码未提供方法级文档字符串。 | [L28](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L28) |

### [`class MappingSecretResolver(SecretResolver)`](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L33)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, secrets: Mapping[str, str])` | 源码未提供方法级文档字符串。 | [L34](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L34) |
| `def resolve(self, reference: str) -> str \| None` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L37) |

### [`class EnvironmentSecretResolver(SecretResolver)`](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L41)

Resolve only explicit ``env://NAME`` references.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve(self, reference: str) -> str \| None` | 源码未提供方法级文档字符串。 | [L44](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L44) |

### [`class VerificationResult`](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L56)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `verified` | `bool` | `—` | [L57](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L57) |
| `artifact_sha256` | `str` | `—` | [L58](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L58) |
| `algorithm` | `str \| None` | `None` | [L59](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L59) |
| `scope` | `str \| None` | `None` | [L60](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L60) |
| `key_id` | `str \| None` | `None` | [L61](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L61) |
| `verified_at` | `str \| None` | `None` | [L62](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L62) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_audit_dict(self) -> dict[str, str \| bool]` | 源码未提供方法级文档字符串。 | [L64](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L64) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def verify_skillhub_artifact(descriptor: ArtifactDescriptor, body: bytes, *, trust_policy: TrustPolicy, secret_resolver: SecretResolver) -> VerificationResult` | Verify raw ZIP bytes using the frozen ``skillhub-artifact-v1`` contract. | [L80](../../../../../jiuwenswarm/server/runtime/skill/artifact_security.py#L80) |

## `jiuwenswarm/server/runtime/skill/skill_manager.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1)

**模块职责：** SkillManager - 管理 skills 的加载、安装、卸载与 marketplace 操作.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L76](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L76) |
| `ENABLED_SKILLS_ENV` | `未显式标注` | [L79](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L79) |
| `_SKILLNET_DOWNLOAD_TIMEOUT` | `int` | [L99](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L99) |
| `_SKILLNET_MAX_RETRIES` | `int` | [L100](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L100) |
| `_SKILLNET_INSTALL_JOBS` | `dict[str, dict[str, Any]]` | [L104](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L104) |
| `_FREE_SEARCH_PROXY_URL_ENV` | `未显式标注` | [L105](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L105) |
| `_FREE_SEARCH_SSL_VERIFY_ENV` | `未显式标注` | [L106](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L106) |
| `_SKILLNET_PROXY_ENV_KEYS` | `未显式标注` | [L107](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L107) |
| `_SKILLNET_NO_PROXY_ENV_KEYS` | `未显式标注` | [L115](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L115) |
| `_FREE_SEARCH_DEFAULT_NO_PROXY` | `未显式标注` | [L116](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L116) |
| `_TEAM_SKILLS_HUB_MARKET_TIMEOUT` | `float` | [L119](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L119) |
| `_TEAM_SKILLS_HUB_BASE_URL_DEFAULT` | `未显式标注` | [L120](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L120) |
| `_TEAM_SKILLS_HUB_DEFAULT_ALLOWED_DOWNLOAD_HOSTS` | `tuple[str, ...]` | [L121](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L121) |
| `_IMPORT_LOCAL_REMOTE_TIMEOUT` | `float` | [L126](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L126) |
| `_IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS` | `tuple[str, ...]` | [L127](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L127) |
| `_ONLINE_SEARCH_RRF_K` | `未显式标注` | [L128](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L128) |
| `_ONLINE_SEARCH_SOURCE_ORDER` | `未显式标注` | [L129](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L129) |
| `_EVOLUTION_FILENAME` | `未显式标注` | [L161](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L161) |

### [`class _ImportLocalTLSAdapter(HTTPAdapter)`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L142)

仅在 ssl_verify=false 时挂载，跳过证书/主机名校验。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def init_poolmanager(self, *args, **kwargs)` | 源码未提供方法级文档字符串。 | [L149](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L149) |

### [`class SkillNetEmptyDownloadError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L188)

skillnet-ai ``download()`` returned None; 前端用 detail_key 做多语言。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, github_context: str = '') -> None` | 源码未提供方法级文档字符串。 | [L191](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L191) |

### [`class SkillNetInstallError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L199)

安装失败，携带 i18n detail_key 供前端本地化。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, detail_key: str, *, detail: str = '', detail_params: dict[str, Any] \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L202](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L202) |

### [`class SkillNameConflictError(ValueError)`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L308)

同名但不同来源的 Skill 安装冲突（映射为稳定错误码 skill_name_conflict）。

### [`class SkillManager`](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L473)

Skill 管理器，对应 skills.* 请求方法.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: str \| None = None, *, persist_skills_state: bool = True, service_id: str \| None = None, agent_id: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L476](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L476) |
| `@property def _skillnet_install_jobs(self) -> dict[str, dict[str, Any]]` | 进程级共享的 SkillNet 安装任务表（见模块常量 ``_SKILLNET_INSTALL_JOBS``）. | [L523](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L523) |
| `def set_skillnet_install_complete_hook(self, hook: Callable[[], Awaitable[None]] \| None) -> None` | 安装成功落盘后回调（通常为重载 Agent 实例）. | [L527](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L527) |
| `def _register_builtin_skill_sources(self) -> None` | Register built-in protocol adapters; clients are started lazily. | [L531](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L531) |
| `@staticmethod def _default_swarm_skill_hub_config() -> SourceConfig` | Build the personal/standalone default using the common SourceConfig. | [L543](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L543) |
| `@staticmethod def _source_config_custom_payload(record: dict[str, Any]) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L576](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L576) |
| `@staticmethod def _resolve_source_endpoint(reference: str \| None) -> str \| None` | 源码未提供方法级文档字符串。 | [L590](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L590) |
| `@staticmethod def _parse_source_config(raw: dict[str, Any]) -> SourceConfig` | 源码未提供方法级文档字符串。 | [L601](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L601) |
| `async def apply_skill_source_configs(self, extension_config: list[dict[str, Any]] \| None) -> dict[str, Any]` | Atomically apply ``custom_config.skill_sources`` from effective policy. | [L672](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L672) |
| `def register_skill_source_provider(self, config: SourceConfig, provider: SkillSourceProvider, *, display_name: str \| None = None) -> None` | Bind an extension-created Provider to this AgentServer runtime. | [L752](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L752) |
| `def bind_skill_source_extension(self, config: SourceConfig, extension: SkillSourceExtension, *, display_name: str \| None = None) -> SkillSourceProvider` | Instantiate one configured source through a registered SPI factory. | [L762](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L762) |
| `async def handle_skills_list(self, params: dict) -> dict` | 返回所有可用 skill（本地 + marketplace 中未安装的）. | [L780](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L780) |
| `async def handle_skills_installed(self, params: dict) -> dict` | 返回已安装的 marketplace 插件列表. | [L806](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L806) |
| `async def handle_skills_enterprise_list(self, params: dict) -> dict` | 企业兼容列表：复用 workspace 安装 DTO，并保留租户标识字段。 | [L838](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L838) |
| `async def handle_skills_get(self, params: dict) -> dict` | 获取单个 skill 详情（name 必填）. | [L863](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L863) |
| `async def handle_skills_toggle(self, params: dict) -> dict` | 切换已安装本地 skill 的 enabled 状态。 | [L985](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L985) |
| `async def handle_skills_retrieval_status(self, params: dict) -> dict` | 返回本地 skill retrieval 索引状态. | [L1029](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1029) |
| `async def handle_skills_retrieval_index_build(self, params: dict) -> dict` | 构建或复用本地 skill retrieval 索引. | [L1035](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1035) |
| `async def handle_skills_retrieval_index_cancel(self, params: dict) -> dict` | 请求取消本地 skill retrieval 索引构建. | [L1044](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1044) |
| `async def handle_skills_retrieval_search(self, params: dict) -> dict` | 基于本地索引检索已安装 skills. | [L1050](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1050) |
| `async def handle_skills_retrieval_tree(self, params: dict) -> dict` | 返回本地 skill retrieval 树索引概览. | [L1057](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1057) |
| `async def handle_skills_evolution_status(self, params: dict) -> dict` | 检查某个 skill 是否存在 evolutions.json. | [L1064](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1064) |
| `async def handle_skills_evolution_get(self, params: dict) -> dict` | 获取某个 skill 的 evolutions.json 内容（重点返回 entries）. | [L1080](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1080) |
| `async def handle_skills_evolution_save(self, params: dict) -> dict` | 保存某个 skill 的 evolutions.json 条目列表. | [L1125](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1125) |
| `async def handle_skills_marketplace_list(self, params: dict) -> dict` | 列出已配置的 marketplace 源. | [L1183](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1183) |
| `async def handle_skills_install(self, params: dict) -> dict` | 安装 marketplace 中的 skill. | [L1202](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1202) |
| `async def handle_skills_install_builtin(self, params: dict) -> dict` | 安装内置技能. | [L1302](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1302) |
| `@staticmethod def _normalize_online_search_identifier(source: str, identifier: str) -> str` | Build a conservative identity key for safe result merging. | [L1364](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1364) |
| `@staticmethod def _normalize_online_search_item(source: str, item: dict[str, Any], rank: int) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1378](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1378) |
| `@classmethod def _aggregate_online_search_results(cls, query: str, source_results: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L1431](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1431) |
| `async def handle_skills_online_search(self, params: dict) -> dict` | Search the fixed online sources used by the Skills Online Search surface. | [L1483](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1483) |
| `async def handle_skills_skillnet_search(self, params: dict) -> dict` | 在线搜索 SkillNet 技能. | [L1562](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1562) |
| `async def handle_skills_skillnet_install(self, params: dict) -> dict` | 从 SkillNet URL 异步安装：立即返回 install_id，不阻塞网关队列. | [L1643](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1643) |
| `async def handle_skills_skillnet_install_status(self, params: dict) -> dict` | 查询 SkillNet 异步安装状态. | [L1678](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1678) |
| `async def handle_skills_skillnet_evaluate(self, params: dict) -> dict` | 使用 skillnet-ai 的 evaluate，LLM 配置复用 react.model_client_config + model_name. | [L1712](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1712) |
| `async def handle_skills_clawhub_get_token(self, params: dict) -> dict` | 获取 ClawHub CLI token（已掩码）. | [L1738](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1738) |
| `async def handle_skills_clawhub_set_token(self, params: dict) -> dict` | 设置 ClawHub CLI token. | [L1747](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1747) |
| `async def handle_skills_clawhub_search(self, params: dict) -> dict` | 从 ClawHub 搜索技能. | [L1756](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1756) |
| `async def handle_skills_clawhub_download(self, params: dict) -> dict` | 从 ClawHub 下载技能. | [L1839](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L1839) |
| `async def handle_skills_team_skills_hub_init(self, params: dict) -> dict` | 初始化 TeamSkills 模板目录（最小可用脚手架）。 | [L2021](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2021) |
| `async def handle_skills_team_skills_hub_validate(self, params: dict) -> dict` | 校验 TeamSkills 目录结构与 SKILL.md 内容。 | [L2121](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2121) |
| `async def handle_skills_team_skills_hub_pack(self, params: dict) -> dict` | 将 TeamSkills 目录打包为 zip。 | [L2186](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2186) |
| `@staticmethod def _source_error(code: str, message: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L2220](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2220) |
| `@staticmethod def _source_context(params: dict[str, Any]) -> ProviderInvocationContext` | 源码未提供方法级文档字符串。 | [L2228](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2228) |
| `async def handle_skills_source_providers(self, params: dict) -> dict` | List configured sources without exposing endpoint or credentials. | [L2236](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2236) |
| `async def handle_skills_source_search(self, params: dict) -> dict` | Dispatch a normalized search request to one Skill Source Provider. | [L2244](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2244) |
| `def _find_installation_by_skill_ref(self, source_id: str, skill_id: str) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L2315](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2315) |
| `@staticmethod def _validate_source_artifact(descriptor: ArtifactDescriptor, *, source_id: str, skill_id: str, version_id: str) -> None` | 源码未提供方法级文档字符串。 | [L2331](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2331) |
| `async def _fetch_verified_source_artifact(self, *, source_id: str, skill_id: str, version_id: str, params: dict[str, Any]) -> tuple[ArtifactDescriptor, bytes, dict[str, Any]]` | Fetch and verify one exact artifact for both install and update. | [L2353](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2353) |
| `def _commit_source_skill_entity(self, skill_dir: Path, *, skill_name: str, source_id: str, skill_id: str, version_id: str, version: str, force: bool, fingerprint: str \| None = None, verification: dict[str, Any] \| None = None, market_display_name: str = '') -> dict[str, Any]` | Atomically replace the entity, then commit the JSON installation record. | [L2402](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2402) |
| `async def handle_skills_source_install(self, params: dict) -> dict` | Install one exact Provider artifact through the common transaction. | [L2480](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2480) |
| `async def handle_skills_update(self, params: dict) -> dict` | Update an installed SkillRef through the same verified install transaction. | [L2555](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2555) |
| `async def handle_skills_updates_check(self, params: dict) -> dict` | Check configured Providers and cache only non-sensitive update status. | [L2590](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2590) |
| `async def handle_skills_team_skills_hub_info(self, params: dict) -> dict` | 查询 Team Skills Hub 技能版本详情（/api/v1/artifacts/{asset_id}）。 | [L2700](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2700) |
| `async def handle_skills_team_skills_hub_search(self, params: dict) -> dict` | 从 Team Skills Hub 搜索技能（/api/v1/plugins）。 | [L2723](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2723) |
| `async def handle_skills_team_skills_hub_install(self, params: dict) -> dict` | 从 Team Skills Hub 安装技能（/api/v1/artifacts/{asset_id}）。 | [L2805](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2805) |
| `async def handle_skills_team_skills_hub_publish(self, params: dict) -> dict` | 发布 TeamSkills（对齐 jiuwen-teamskills 的 /api/v1/plugins 协议）。 | [L2951](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L2951) |
| `async def handle_skills_team_skills_hub_delete(self, params: dict) -> dict` | 删除 TeamSkills（对齐 jiuwen-teamskills 的 DELETE /api/v1/plugins/...）。 | [L3008](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3008) |
| `async def _skillnet_install_background(self, install_id: str, skill_url: str, force: bool, mirror_url: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L3037](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3037) |
| `def _skillnet_install_files_sync(self, skill_url: str, force: bool, mirror_url: str \| None = None, checksum_sha256: str = '') -> dict[str, Any]` | 在工作线程中下载并拷贝到 skills 目录；返回 ok / skill_name / meta / skill_url. | [L3126](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3126) |
| `def install_skill_sync(self, skill_url: str, force: bool = True, mirror_url: str \| None = None, checksum_sha256: str = '') -> dict[str, Any]` | 同步安装 skill（线程安全，可在 ``asyncio.to_thread`` 中调用）. | [L3265](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3265) |
| `async def handle_skills_uninstall(self, params: dict) -> dict` | 卸载已安装的 skill. | [L3281](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3281) |
| `async def handle_skills_import_local(self, params: dict) -> dict` | 从本地路径或远程归档 URL 导入 skill. | [L3386](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3386) |
| `def _import_local_from_path(self, src: Path, *, force: bool, origin: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L3414](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3414) |
| `def _download_web_skill_bytes(self, download_url: str) -> bytes` | 同步下载技能归档（仅 Web 验签安装使用；不改 import_local 远程路径）。 | [L3477](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3477) |
| `def _install_web_skill_dir(self, skill_dir: Path, *, skill_name: str) -> dict[str, Any]` | 企业 Web 安装专用：先完成实体落盘，再由调用方提交 workspace 状态。 | [L3509](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3509) |
| `def remove_skill_directory(self, skill_name: str) -> None` | 源码未提供方法级文档字符串。 | [L3531](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3531) |
| `async def handle_skills_web_install(self, params: dict) -> dict` | 企业 Web 安装兼容入口：下载、校验、落盘并提交 workspace JSON。 | [L3543](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3543) |
| `async def handle_skills_web_uninstall(self, params: dict) -> dict` | 企业 Web 卸载兼容入口：仅允许删除 workspace 中的 user 安装。 | [L3684](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3684) |
| `async def _import_skill_from_remote_archive(self, *, download_url: str, force: bool, checksum_sha256: str = '') -> dict[str, Any]` | Download archive by URL, extract by type, then reuse local import flow. | [L3739](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3739) |
| `async def handle_skills_marketplace_add(self, params: dict) -> dict` | 添加 marketplace 源. | [L3805](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3805) |
| `async def handle_skills_marketplace_remove(self, params: dict) -> dict` | 删除 marketplace 源. | [L3831](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3831) |
| `async def handle_skills_marketplace_toggle(self, params: dict) -> dict` | 启用或禁用 marketplace 源. | [L3868](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3868) |
| `@staticmethod def _coerce_str_list(val: Any) -> list[str]` | frontmatter 里 tags/allowed_tools 可能是逗号分隔字符串，统一为 list[str]. | [L3939](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3939) |
| `@staticmethod def _convert_yaml_date(obj: Any) -> Any` | 源码未提供方法级文档字符串。 | [L3955](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3955) |
| `@staticmethod def _parse_skill_md(path: Path) -> dict \| None` | 解析 SKILL.md，提取 YAML frontmatter 和正文. | [L3965](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L3965) |
| `@staticmethod def _try_find_skill_file(directory: Path) -> Path \| None` | 在目录中查找 skill 文件. | [L4030](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4030) |
| `def _is_builtin_skill(self, skill_name: str, installed_plugins: list[dict], skill_path: Path \| None = None) -> bool` | 判断技能是否为内置技能. | [L4050](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4050) |
| `@staticmethod def _slug_from_clawhub_origin(origin: str) -> str` | 从 ClawHub origin 中提取 slug（最后 / 后的部分）。 | [L4089](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4089) |
| `@staticmethod def _dir_origin_candidates(child: Path) -> list[str]` | 磁盘 skill 目录可能对应的 origin 候选（用于在 local_skills 中精确匹配）。 | [L4108](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4108) |
| `def _find_local_skill_for_dir(self, child: Path, meta: dict) -> dict \| None` | 定位磁盘目录对应的 local_skill 记录，优先按 origin 精确匹配，再按 name 回退。 | [L4121](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4121) |
| `def _find_installed_plugin_for_dir(self, child: Path, meta: dict, ls_record: dict \| None) -> dict \| None` | 定位磁盘目录对应的 installed_plugin 记录，origin 优先、name 回退. | [L4146](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4146) |
| `def _scan_local_skills(self) -> list[dict]` | 扫描 agent/skills/ 下的本地 skill（跳过 _marketplace）. | [L4175](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4175) |
| `def _scan_builtin_skills(self) -> list[dict]` | 扫描内置技能目录中尚未安装到用户目录的技能. | [L4236](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4236) |
| `def _resolve_skill_source(self, skill_name: str, origin: str \| None = None) -> str` | 解析 skill 来源（local / project / marketplace 名称）. | [L4278](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4278) |
| `def _resolve_skill_display_name(self, skill_name: str) -> str` | 解析 skill 展示名（优先 local_skills/installed_plugins 记录的 display_name）. | [L4321](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4321) |
| `def _resolve_local_skill_dir(self, skill_name: str) -> Path \| None` | 根据 skill name 定位本地技能目录（仅 agent/skills 下）. | [L4339](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4339) |
| `def _resolve_local_skill_dir_by_origin(self, origin: str) -> Path \| None` | 根据 origin 精确定位本地技能目录. | [L4362](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4362) |
| `def _get_skill_evolution_path(self, skill_name: str) -> Path \| None` | 源码未提供方法级文档字符串。 | [L4404](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4404) |
| `def _scan_marketplace_skills(self) -> list[dict]` | 扫描 _marketplace/ 下已 clone 的仓库中未安装的 skill. | [L4410](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4410) |
| `def _get_mirror_skills_dirs(self) -> list[Path]` | 返回需要镜像同步的 skills 目录（不包含当前运行目录）. | [L4472](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4472) |
| `@staticmethod def _normalize_lang_suffix(name: str) -> str` | 将 xxxx_zh.MD / xxxx_en.MD 规范为 xxxx.MD（去除 _zh/_en 后缀）。 | [L4497](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4497) |
| `@staticmethod def _generate_agent_data_for_workspace(workspace_root: Path) -> None` | Generate agent/workspace/agent-data.json from agent tree. | [L4510](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4510) |
| `def _refresh_agent_data_indexes(self) -> None` | Refresh agent-data.json for runtime and mirror workspaces. | [L4560](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4560) |
| `@staticmethod def _locate_skill_dir(path: Path) -> Path \| None` | 定位包含 SKILL.md 的目录（优先当前目录，再向下递归）；文件名大小写不敏感. | [L4576](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4576) |
| `@staticmethod def _get_team_skills_hub_base_url(override_url: str \| None = None) -> str` | 源码未提供方法级文档字符串。 | [L4594](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4594) |
| `@staticmethod def _resolve_teamskills_hub_auth(params: dict[str, Any]) -> dict[str, str]` | 源码未提供方法级文档字符串。 | [L4599](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4599) |
| `def _prepare_teamskills_publish_zip(self, *, path_raw: str, file_raw: str, plugin_version: str, tmpdir: Path) -> Path` | 对齐 jiuwen-teamskills：上传前规范化 zip，确保包含合法 plugin.yaml. | [L4610](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4610) |
| `def _build_teamskills_publish_zip_from_root(self, root: Path, plugin_version: str, tmpdir: Path) -> Path` | 源码未提供方法级文档字符串。 | [L4638](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4638) |
| `@staticmethod def _normalize_teamskills_hub_http_error(resp: httpx.Response) -> str` | 源码未提供方法级文档字符串。 | [L4698](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4698) |
| `async def _teamskills_hub_publish_request(self, *, base_url: str, zip_path: Path, checksum_sha256: str, plugin_id: str \| None, plugin_version: str, version_desc: str, force: bool, token: str \| None, system_token: str \| None) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L4704](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4704) |
| `async def _teamskills_hub_delete_request(self, *, base_url: str, skill_id: str, version: str, token: str \| None, system_token: str \| None) -> None` | 源码未提供方法级文档字符串。 | [L4751](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4751) |
| `@staticmethod def _get_team_skills_hub_allowed_download_hosts() -> list[str]` | 源码未提供方法级文档字符串。 | [L4772](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4772) |
| `@staticmethod def _get_import_local_allowed_download_hosts() -> list[str]` | 源码未提供方法级文档字符串。 | [L4785](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4785) |
| `@staticmethod def _assert_team_skills_hub_download_url_allowed(download_url: str) -> None` | 源码未提供方法级文档字符串。 | [L4798](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4798) |
| `@staticmethod def _assert_import_local_download_url_allowed(download_url: str) -> None` | 源码未提供方法级文档字符串。 | [L4816](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4816) |
| `@staticmethod def _team_skills_hub_host_matches_rule(host: str, rule: str) -> bool` | 源码未提供方法级文档字符串。 | [L4833](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4833) |
| `@staticmethod def _is_http_download_target(value: str) -> bool` | 源码未提供方法级文档字符串。 | [L4846](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4846) |
| `@staticmethod def _is_github_skill_folder_url(url: str) -> bool` | GitHub tree/blob 目录链接，走 skillnet-ai 按文件拉取。 | [L4851](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4851) |
| `def _assert_skill_download_url_allowed(self, download_url: str, *, allowed_hosts: tuple[str, ...] \| None = None) -> None` | SkillHub / 远程归档下载主机白名单（import_local 与 Team Skills Hub 并集）。 | [L4860](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4860) |
| `def _download_http_archive_bytes_sync(self, download_url: str) -> bytes` | 同步下载 HTTPS 直链 zip/tar 归档（SkillHub CDN 等）。 | [L4902](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4902) |
| `async def _team_skills_hub_http_get_data(self, path: str, *, params: dict[str, Any] \| None = None, timeout: float = _TEAM_SKILLS_HUB_MARKET_TIMEOUT, base_url: str \| None = None) -> Any` | 源码未提供方法级文档字符串。 | [L4932](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4932) |
| `@staticmethod def _safe_extract_zip_members_into(zf: zipfile.ZipFile, dest_root: Path) -> None` | 将已打开的 ZIP 成员解压到 dest_root（须为 resolve() 后的目录），拒绝 Zip Slip。 | [L4970](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4970) |
| `@staticmethod def _safe_extract_zip_bytes_to_dir(data: bytes, dest_dir: Path) -> None` | 将 ZIP 字节解压到 dest_dir（不落盘 staging zip，与 ClawHub extractall 语义一致）。 | [L4999](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L4999) |
| `@staticmethod def _safe_extract_zip_to_dir(zip_path: Path, dest_dir: Path) -> None` | 将 ZIP 文件解压到 dest_dir，拒绝 Zip Slip（..、绝对路径、写出目标目录外）。 | [L5007](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5007) |
| `@staticmethod def _safe_extract_tar_to_dir(tar_path: Path, dest_dir: Path) -> None` | Extract TAR/TAR.GZ/TGZ safely into dest_dir. | [L5015](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5015) |
| `@staticmethod def _detect_archive_format(body: bytes) -> str` | 源码未提供方法级文档字符串。 | [L5050](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5050) |
| `def _extract_archive_bytes_to_dir(self, body: bytes, dest_dir: Path) -> None` | 源码未提供方法级文档字符串。 | [L5060](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5060) |
| `async def _download_remote_archive_and_verify(self, download_url: str, *, checksum_sha256: str = '', timeout: float \| None = None) -> bytes` | 源码未提供方法级文档字符串。 | [L5080](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5080) |
| `async def _download_zip_and_verify(self, download_url: str, *, checksum_sha256: str = '', timeout: float \| None = None, max_bytes: int \| None = None) -> bytes` | 源码未提供方法级文档字符串。 | [L5117](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5117) |
| `@staticmethod def _get_github_token() -> str` | 源码未提供方法级文档字符串。 | [L5155](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5155) |
| `@staticmethod def _skillnet_eval_llm_params() -> dict[str, str \| None]` | 与主对话一致的 API Key / Base URL / 模型名（config.yaml react 段）. | [L5159](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5159) |
| `@staticmethod def _skillnet_evaluate_sync(skill_url: str) -> dict[str, Any]` | 同步 evaluate，供 asyncio.to_thread 调用. | [L5185](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5185) |
| `@staticmethod def _skillnet_search_sync(search_kwargs: dict[str, Any]) -> list[Any]` | 同步调用 skillnet-ai search，供 asyncio.to_thread 使用. | [L5225](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5225) |
| `@staticmethod def _github_skillnet_install_error_context(skill_url: str) -> str` | 下载失败时拉 GitHub Contents 与 rate_limit，把官方 message 等拼给前端. | [L5243](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5243) |
| `@staticmethod def _skillnet_download_sync(skill_url: str, target_dir: str, mirror_url: str \| None = None) -> str` | 同步调用 skillnet-ai download；失败时附带 GitHub API 返回说明（如前端的限流文案）。 | [L5303](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5303) |
| `async def _git_clone(self, url: str, dest: Path) -> str \| None` | 浅克隆 git 仓库，返回 commit hash 或 None. | [L5337](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5337) |
| `async def _git_pull(self, repo_path: Path) -> str \| None` | 拉取最新代码，返回 commit hash 或 None. | [L5360](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5360) |
| `async def _git_get_commit(self, repo_path: Path) -> str \| None` | 获取当前 HEAD commit hash. | [L5381](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5381) |
| `async def _sync_marketplace_repos(self) -> None` | 同步所有已配置 marketplace 到本地目录（存在则 pull，不存在则 clone）. | [L5400](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5400) |
| `async def handle_plugins_list(self, params: dict) -> dict` | 列出所有已安装插件（含启用/禁用状态）. | [L5435](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5435) |
| `async def handle_plugins_install(self, params: dict) -> dict` | 安装插件，支持 marketplace spec 或本地路径/URL. | [L5456](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5456) |
| `async def handle_plugins_uninstall(self, params: dict) -> dict` | 卸载插件，复用 skills.uninstall 逻辑. | [L5497](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5497) |
| `async def handle_plugins_enable(self, params: dict) -> dict` | 启用插件. | [L5510](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5510) |
| `async def handle_plugins_disable(self, params: dict) -> dict` | 禁用插件. | [L5520](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5520) |
| `async def handle_plugins_reload(self, params: dict) -> dict` | 重载插件：根据 enabled 状态物理移动技能目录，然后统计摘要. | [L5530](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5530) |
| `def _load_state(self) -> dict[str, Any]` | 加载 skills_state.json，失败时返回默认空状态. | [L5585](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5585) |
| `def _save_state(self) -> None` | 持久化状态到 skills_state.json（企业路径可关闭）。 | [L5598](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5598) |
| `def _get_marketplaces(self) -> list[dict]` | 源码未提供方法级文档字符串。 | [L5622](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5622) |
| `def _add_marketplace(self, marketplace: dict) -> None` | 源码未提供方法级文档字符串。 | [L5631](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5631) |
| `def _remove_marketplace(self, name: str) -> bool` | 源码未提供方法级文档字符串。 | [L5636](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5636) |
| `def _set_marketplace_enabled(self, name: str, enabled: bool) -> bool` | 源码未提供方法级文档字符串。 | [L5645](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5645) |
| `def _set_marketplace_last_updated(self, name: str) -> bool` | 源码未提供方法级文档字符串。 | [L5658](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5658) |
| `@staticmethod def normalize_marketplaces(raw_marketplaces: Any) -> list[dict]` | 源码未提供方法级文档字符串。 | [L5672](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5672) |
| `def _normalize_state(self, state: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L5693](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5693) |
| `def _get_installed_plugins(self) -> list[dict]` | 源码未提供方法级文档字符串。 | [L5706](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5706) |
| `def get_installed_plugins(self) -> list[dict]` | 返回已安装插件记录的拷贝。 | [L5713](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5713) |
| `def list_skill_installations(self) -> list[dict[str, Any]]` | Return workspace installation records without exposing mutable state. | [L5717](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5717) |
| `def find_skill_dir_by_identity(self, *, skill_id: str, version: str) -> str` | 盘→账本回填定位：返回 SKILL.md 声明同 ``skill_id+version`` 的目录名，未命中返回空串. | [L5725](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5725) |
| `def _skill_installation_entity_ready(self, record: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L5747](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5747) |
| `def _installation_enabled(self, record: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L5757](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5757) |
| `def _skill_installation_dto(self, record: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L5764](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5764) |
| `def _apply_skill_installation_meta(self, payload: dict[str, Any], record: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L5780](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5780) |
| `def _find_skill_installation(self, *, name: str, origin: str \| None = None) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L5809](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5809) |
| `def record_skill_installation(self, *, name: str, source_type: str, origin: str = '', source: str = '', version: str = '', skill_id: str \| None = None, source_id: str \| None = None, version_id: str \| None = None, installation_id: str \| None = None, fingerprint: str \| None = None, verification: dict[str, Any] \| None = None, entity_dir: str \| None = None, market_display_name: str \| None = None, replace_by_name: bool = False) -> dict[str, Any]` | Create or update one managed installation in workspace JSON. | [L5830](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5830) |
| `def remove_skill_installation(self, *, name: str, origin: str \| None = None, expected_source_type: str \| None = None) -> bool` | Remove a matching workspace record, optionally guarded by source type. | [L5939](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5939) |
| `def list_enabled_skill_names(self) -> list[str]` | Return enabled, disk-backed skill names for the current workspace. | [L5960](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5960) |
| `def get_local_skills(self) -> list[dict]` | 返回本地技能安装记录的拷贝。 | [L5972](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5972) |
| `@staticmethod def _resolve_skill_name(child: Path, md: Path, meta: dict) -> str` | Canonical skill name for a folder: parsed name, or folder name as fallback. | [L5977](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5977) |
| `def _collect_existing_local_skill_names(self) -> set[str]` | 源码未提供方法级文档字符串。 | [L5990](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L5990) |
| `def _collect_existing_clawhub_origins(self) -> set[str]` | 磁盘上可由目录名反推的 ClawHub origin 集合（``clawhub:{slug}``）。 | [L6009](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6009) |
| `def _register_unmanaged_local_skills(self) -> None` | Auto-register skills that exist on disk but were never recorded. | [L6028](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6028) |
| `def _register_builtin_skills(self) -> None` | 企业版：把仓库内置技能复制进 tenant workspace 并登记为 builtin（不可卸载）。 | [L6111](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6111) |
| `def _apply_enabled_config(self, payload: dict[str, Any], skill_name: str, *, default_enabled: bool \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L6188](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6188) |
| `def get_skill_enabled(self, skill_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L6203](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6203) |
| `def set_skill_enabled(self, skill_name: str, enabled: bool) -> None` | 源码未提供方法级文档字符串。 | [L6206](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6206) |
| `def remove_skill_config(self, skill_name: str) -> None` | 源码未提供方法级文档字符串。 | [L6210](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6210) |
| `def list_disabled_skills(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L6214](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6214) |
| `def list_execution_disabled_skills(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L6217](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6217) |
| `def get_skill_meta(self, skill_name: str) -> dict[str, Any] \| None` | 返回本地 skill 的解析元数据，附带目录与 skill 文件路径。 | [L6220](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6220) |
| `def is_builtin_skill(self, skill_name: str) -> bool` | 判断当前运行目录中的 skill 是否为真正的内置技能。 | [L6235](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6235) |
| `def _add_installed_plugin(self, plugin: dict) -> None` | 源码未提供方法级文档字符串。 | [L6254](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6254) |
| `def _remove_installed_plugin(self, name: str, origin: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L6266](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6266) |
| `def _set_plugin_enabled(self, name: str, enabled: bool, origin: str \| None = None) -> bool` | 设置插件的启用/禁用状态。 | [L6278](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6278) |
| `@staticmethod def _normalize_plugin(p: dict) -> dict` | 规范化插件记录，补全 enabled 字段. | [L6303](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6303) |
| `def _add_local_skill(self, skill: dict) -> None` | 源码未提供方法级文档字符串。 | [L6308](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6308) |
| `def _remove_local_skill(self, name: str, origin: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L6320](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6320) |
| `@staticmethod def _record_identity_key(record: dict) -> str` | 记录身份键：优先 origin（区分同名不同来源），origin 为空时回退 name. | [L6333](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6333) |
| `@classmethod def _record_matches(cls, record: dict, *, name: str, origin: str) -> bool` | 判断记录是否匹配给定 name+origin 身份。origin 优先，name 作兜底. | [L6347](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6347) |
| `@staticmethod def _apply_local_skill_meta(meta: dict, ls_rec: dict) -> str` | 把 local_skill 记录上的展示字段回填到 meta，并返回 origin. | [L6361](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6361) |
| `def _get_clawhub_token(self) -> str` | 获取 ClawHub CLI token. | [L6385](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6385) |
| `def _set_clawhub_token(self, token: str) -> None` | 设置 ClawHub CLI token（掩码处理）。 | [L6389](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6389) |
| `@staticmethod def _mask_clawhub_token(token: str) -> str` | 掩码处理 ClawHub token。 | [L6395](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L6395) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _get_ssl_verify() -> bool` | 延迟导入以规避循环依赖：ssl_config 所在的 tools 包 __init__ 会回引本模块。 | [L70](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L70) |
| `def enabled_skills_from_environ() -> str \| None` | Read ENABLED_SKILLS from tip/env (skills allowlist). | [L82](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L82) |
| `def _maybe_disable_insecure_warning() -> None` | 关闭证书校验时同步静默 urllib3 的 InsecureRequestWarning。 | [L132](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L132) |
| `def _get_agent_root_dir() -> 'Path'` | 源码未提供函数级文档字符串。 | [L164](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L164) |
| `def _get_marketplace_dir() -> 'Path'` | 源码未提供函数级文档字符串。 | [L168](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L168) |
| `def _get_state_file() -> 'Path'` | 源码未提供函数级文档字符串。 | [L172](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L172) |
| `def _map_github_api_error(exc: Exception) -> SkillNetInstallError` | 将 GitHubAPIError 按状态码映射为可本地化的 detail_key，原始报错留在 detail。 | [L215](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L215) |
| `def _is_valid_http_mirror_url(url: str) -> bool` | Return True if url is a plausible http(s) mirror base (for SkillDownloader). | [L235](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L235) |
| `def _env_bool(name: str, default: bool = True) -> bool` | 源码未提供函数级文档字符串。 | [L248](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L248) |
| `def _get_free_search_proxy_url() -> str` | 源码未提供函数级文档字符串。 | [L255](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L255) |
| `def _free_search_ssl_verify() -> bool` | 源码未提供函数级文档字符串。 | [L259](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L259) |
| `def _disable_insecure_request_warning() -> None` | 源码未提供函数级文档字符串。 | [L263](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L263) |
| `def _skillnet_proxy_mapping() -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L267](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L267) |
| `def _configure_skillnet_requests_session(session: Any) -> None` | 源码未提供函数级文档字符串。 | [L274](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L274) |
| `@contextmanager def _skillnet_network_context()` | Expose the configured proxy to third-party SkillNet clients during one call. | [L285](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L285) |
| `def _safe_path_name(value: Any, label: str) -> str` | 源码未提供函数级文档字符串。 | [L312](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L312) |
| `def _safe_child_path(base: Path, name: Any, label: str) -> Path` | 源码未提供函数级文档字符串。 | [L329](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L329) |
| `def _log_rejected_name(operation: str, label: str, value: Any, exc: ValueError) -> None` | 源码未提供函数级文档字符串。 | [L340](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L340) |
| `def _sha256_matches(body: bytes, checksum_sha256: str) -> bool` | 校验值非空时比对 SHA-256（大小写不敏感）；空校验值视为通过. | [L350](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L350) |
| `def _safe_rmtree(path: Path) -> bool` | 安全地删除目录树，处理 Windows 上的 git 文件锁定问题. | [L358](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L358) |
| `def _handle_copy_error(exc: OSError, dest: Path, logger_prefix: str, src: Path \| None = None) -> dict[str, Any]` | 处理文件/目录复制失败的统一错误处理函数. | [L420](../../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L420) |

## `jiuwenswarm/server/runtime/skill/skill_whitelist.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L1)

**模块职责：** Skill 白名单：按租户将预置技能同步到 workspace 与 ``skills_state.json``。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L23) |
| `MANIFEST_FILENAME` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L25) |
| `_RESERVED_SKILL_DIR_NAMES` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L26) |
| `SOURCE_PREBUILT` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L27) |
| `SOURCE_USER` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L28) |
| `_SKILLS_DIR_SYNC_LOCKS` | `dict[str, asyncio.Lock]` | [L30](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L30) |
| `_SKILLS_DIR_SYNC_LOCKS_META` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L31) |
| `_SPI_DATA_KEYS` | `未显式标注` | [L95](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L95) |

### [`class SkillWhitelistItem`](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L45)

Gateway/DB 白名单项。增量判定看 ``skill_name`` 定位后比 version / sha256 / skill_id.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `id` | `str` | `—` | [L48](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L48) |
| `version` | `str` | `—` | [L49](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L49) |
| `source` | `str` | `—` | [L50](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L50) |
| `source_id` | `str` | `''` | [L51](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L51) |
| `version_id` | `str` | `''` | [L52](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L52) |
| `sha256` | `str` | `''` | [L53](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L53) |

### [`class AgentSkillWhitelistConfig`](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L57)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `agent_id` | `str` | `—` | [L58](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L58) |
| `service_id` | `str` | `—` | [L59](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L59) |
| `skills` | `list[SkillWhitelistItem]` | `field(default_factory=list)` | [L60](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L60) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def items_with_source(self) -> list[SkillWhitelistItem]` | 源码未提供方法级文档字符串。 | [L63](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L63) |

### [`class SkillWhitelistSyncResult`](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L72)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `enabled_skill_dirs` | `list[str]` | `field(default_factory=list)` | [L73](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L73) |
| `errors` | `list[str]` | `field(default_factory=list)` | [L74](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L74) |
| `succeeded` | `list[str]` | `field(default_factory=list)` | [L75](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L75) |
| `failed` | `list[dict[str, str]]` | `field(default_factory=list)` | [L76](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L76) |
| `ok` | `bool` | `True` | [L77](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L77) |

### [`class SkillWhitelistSynchronizer`](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L151)

将预制技能同步到租户 skills/，并写入 ``installed_skill``（按 skill 原子）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: str \| Path, service_id: str, agent_id: str, *, group_id: str \| None = None, bot_id: str \| None = None, skill_manager: SkillManager \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L154](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L154) |
| `def _remove_installed_dir(self, installed_dir: str) -> None` | 源码未提供方法级文档字符串。 | [L174](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L174) |
| `@staticmethod def _skill_dir_ready(skills_dir: Path, skill_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L182) |
| `def _should_download_prebuilt(self, item: SkillWhitelistItem, installed_skills_map: dict[str, dict[str, Any]]) -> tuple[bool, str]` | 是否需要下载预置包。返回 (need_download, installed_skill_name). | [L188](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L188) |
| `def _adopt_existing_dir_for(self, item: SkillWhitelistItem) -> str` | 盘→账本回填：返回 SKILL.md 声明同 ``skill_id+version`` 的就绪目录名，未命中返回空. | [L232](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L232) |
| `@staticmethod def _recorded_checksum(installed_row: dict[str, Any]) -> str` | 读取安装记录中已校验过的 ``verification.checksum_sha256``. | [L244](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L244) |
| `@staticmethod def _mark_failed(result: SkillWhitelistSyncResult, *, skill_name: str, error_code: str, error_message: str) -> None` | 源码未提供方法级文档字符串。 | [L252](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L252) |
| `async def sync(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult` | 按 skills 物理目录串行同步：同一落盘目录同时仅一个 sync 在飞. | [L269](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L269) |
| `async def _run_sync(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult` | 持锁同步：对齐模板预制 → 剔除多余 → 刷新启用集. | [L275](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L275) |
| `async def reconcile_disk_into_ledger(self) -> SkillWhitelistSyncResult` | 仅做盘→库对账并重算启用集（供热刷新路径复用，不跑预制模板 sync）. | [L306](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L306) |
| `async def _fetch_installed_skills_map(self, result: SkillWhitelistSyncResult) -> dict[str, dict[str, Any]] \| None` | 查询租户已装技能，返回 skill_name -> 行；失败时写 result 并返回 None. | [L314](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L314) |
| `def _apply_prebuilt_outcome(self, outcome: dict[str, Any], installed_skills_map: dict[str, dict[str, Any]], kept_prebuilt_names: set[str], result: SkillWhitelistSyncResult) -> None` | 根据单项 sync 结果更新 kept 集合与本地索引（不再回读 DB）. | [L336](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L336) |
| `async def _remove_prebuilt_not_in_template(self, installed_skills_map: dict[str, dict[str, Any]], kept_prebuilt_names: set[str], result: SkillWhitelistSyncResult) -> None` | 当前模板里没有的预制技能：硬删盘+库（不降回 user）. | [L369](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L369) |
| `async def _ensure_prebuilt_installed(self, item: SkillWhitelistItem, installed_skills_map: dict[str, dict[str, Any]]) -> dict[str, Any]` | 确保模板项对应的预制已就绪：按需下载落盘 → 校验目录 → upsert 账本. | [L405](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L405) |
| `def _restore_promoted_user(self, existing_user: dict[str, Any] \| None, installed_dir: str) -> None` | 恢复"提升 user→prebuilt"失败时被删除的 user 记录，避免技能进入孤儿状态。 | [L517](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L517) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def _skills_dir_sync_lock_for(skills_dir: Path) -> asyncio.Lock` | 源码未提供函数级文档字符串。 | [L34](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L34) |
| `def is_skill_whitelist_tenant(agent_id: str \| None, service_id: str \| None) -> bool` | ACP/default 或 ID 缺失的租户不启用白名单逻辑；仅企业版下生效. | [L80](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L80) |
| `def _extract_spi_fields(raw: dict[str, Any]) -> dict[str, str]` | SPI 元数据优先取 ``data`` 字段（JSON 对象或字符串），顶层同名字段兜底. | [L98](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L98) |
| `def parse_agent_skill_whitelist(agent_id: str, service_id: str, skills: list[dict[str, Any]] \| None) -> AgentSkillWhitelistConfig` | 解析 gateway 返回的 skills 列表. | [L116](../../../../../jiuwenswarm/server/runtime/skill/skill_whitelist.py#L116) |

## `jiuwenswarm/server/runtime/skill/skilldev/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/__init__.py#L1)

**模块职责：** SkillDev — Skill 开发模式模块.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/skill/skilldev/__init__.py#L35) |

## `jiuwenswarm/server/runtime/skill/skilldev/context.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L1)

**模块职责：** SkillDevContext — 每个阶段的执行上下文.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L30) |

### [`class SkillDevContext`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L33)

阶段执行上下文.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, task_id: str, deps: SkillDevDeps, state: SkillDevState, workspace: Path, event_queue: asyncio.Queue) -> None` | 源码未提供方法级文档字符串。 | [L39](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L39) |
| `async def emit(self, event_type: SkillDevEventType, payload: dict) -> None` | 向前端推送一个事件（放入 Pipeline 的事件队列）. | [L53](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L53) |
| `@staticmethod def create_stage_agent(stage_name: str, system_prompt: str, tools: list[str] \| None = None, max_iterations: int = 20)` | 为当前阶段创建隔离的 ReActAgent. | [L63](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L63) |
| `def _register_tools(self, agent, tool_names: list[str]) -> None` | 根据工具名白名单将工具注册到 Agent. | [L107](../../../../../jiuwenswarm/server/runtime/skill/skilldev/context.py#L107) |

## `jiuwenswarm/server/runtime/skill/skilldev/deps.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L1)

**模块职责：** SkillDevDeps — SkillDevService 的最小外部依赖定义.

### [`class SkillDevDeps`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L22)

SkillDevService 的全部外部依赖（由 JiuWenSwarm 构造并注入）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `model_name` | `str` | `—` | [L26](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L26) |
| `model_client_config` | `dict` | `—` | [L27](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L27) |
| `mcp_tools_factory` | `Callable[[], list]` | `—` | [L31](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L31) |
| `sysop_config` | `object \| None` | `—` | [L33](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L33) |
| `state_store` | `StateStore` | `—` | [L36](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L36) |
| `workspace_provider` | `WorkspaceProvider` | `—` | [L37](../../../../../jiuwenswarm/server/runtime/skill/skilldev/deps.py#L37) |

## `jiuwenswarm/server/runtime/skill/skilldev/pipeline.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L1)

**模块职责：** SkillDevPipeline — 确定性状态机编排器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L44) |

### [`class SkillDevPipeline`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L47)

SkillDev 确定性状态机.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `STAGE_HANDLERS` | `未显式标注` | `{SkillDevStage.INIT: InitStageHandler, SkillDevStage.PLAN: PlanStageHandler, SkillDevStage.GENERATE…` | [L55](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L55) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, task_id: str, state: SkillDevState, deps: SkillDevDeps) -> None` | 源码未提供方法级文档字符串。 | [L68](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L68) |
| `async def run(self) -> AsyncIterator[SkillDevEvent]` | 从当前阶段开始执行，直到遇到挂起点或终态. | [L74](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L74) |
| `async def resume(self, data: dict) -> AsyncIterator[SkillDevEvent]` | 从挂起点恢复执行. | [L150](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L150) |
| `async def _emit(self, event_type: SkillDevEventType, payload: dict) -> None` | 向事件队列写入一个事件. | [L177](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L177) |
| `async def _checkpoint(self) -> None` | 阶段边界：持久化状态 + 同步工作区文件. | [L186](../../../../../jiuwenswarm/server/runtime/skill/skilldev/pipeline.py#L186) |

## `jiuwenswarm/server/runtime/skill/skilldev/schema.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L1)

**模块职责：** SkillDev 模块的核心数据模型.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SUSPENSION_POINTS` | `dict[SkillDevStage, SuspensionConfig]` | [L295](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L295) |
| `_STAGE_GROUPS` | `list[_StageGroup]` | [L547](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L547) |
| `ALLOWED_FRONTMATTER_KEYS` | `未显式标注` | [L627](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L627) |
| `SKILL_NAME_MAX_LEN` | `未显式标注` | [L638](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L638) |
| `SKILL_DESC_MAX_LEN` | `未显式标注` | [L639](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L639) |

### [`class SkillDevStage(str, Enum)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L25)

SkillDev Pipeline 的所有阶段.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `INIT` | `未显式标注` | `'init'` | [L35](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L35) |
| `PLAN` | `未显式标注` | `'plan'` | [L36](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L36) |
| `PLAN_CONFIRM` | `未显式标注` | `'plan_confirm'` | [L37](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L37) |
| `GENERATE` | `未显式标注` | `'generate'` | [L38](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L38) |
| `VALIDATE` | `未显式标注` | `'validate'` | [L39](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L39) |
| `TEST_DESIGN` | `未显式标注` | `'test_design'` | [L40](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L40) |
| `TEST_RUN` | `未显式标注` | `'test_run'` | [L41](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L41) |
| `EVALUATE` | `未显式标注` | `'evaluate'` | [L42](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L42) |
| `REVIEW` | `未显式标注` | `'review'` | [L43](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L43) |
| `IMPROVE` | `未显式标注` | `'improve'` | [L44](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L44) |
| `PACKAGE` | `未显式标注` | `'package'` | [L47](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L47) |
| `DESC_OPTIMIZE_CONFIRM` | `未显式标注` | `'desc_optimize_confirm'` | [L48](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L48) |
| `DESC_OPTIMIZE` | `未显式标注` | `'desc_optimize'` | [L49](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L49) |
| `COMPLETED` | `未显式标注` | `'completed'` | [L52](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L52) |
| `ERROR` | `未显式标注` | `'error'` | [L55](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L55) |

### [`class SkillDevTaskMode(str, Enum)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L58)

任务入口模式（由请求参数自动判断）.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `CREATE` | `未显式标注` | `'create'` | [L61](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L61) |
| `CREATE_WITH_RESOURCES` | `未显式标注` | `'create_with_resources'` | [L62](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L62) |
| `MODIFY` | `未显式标注` | `'modify'` | [L63](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L63) |

### [`class SkillDevEventType(str, Enum)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L71)

Pipeline 向前端推送的事件类型.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `STAGE_CHANGED` | `未显式标注` | `'skilldev.stage_changed'` | [L79](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L79) |
| `PROGRESS` | `未显式标注` | `'skilldev.progress'` | [L80](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L80) |
| `ERROR` | `未显式标注` | `'skilldev.error'` | [L81](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L81) |
| `AGENT_THINKING` | `未显式标注` | `'skilldev.agent_thinking'` | [L84](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L84) |
| `TEST_PROGRESS` | `未显式标注` | `'skilldev.test_progress'` | [L85](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L85) |
| `CONFIRM_REQUEST` | `未显式标注` | `'skilldev.confirm_request'` | [L88](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L88) |
| `TODOS_UPDATE` | `未显式标注` | `'skilldev.todos_update'` | [L89](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L89) |
| `ARTIFACT_READY` | `未显式标注` | `'skilldev.artifact_ready'` | [L90](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L90) |
| `EVAL_READY` | `未显式标注` | `'skilldev.eval_ready'` | [L93](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L93) |
| `VALIDATE_RESULT` | `未显式标注` | `'skilldev.validate_result'` | [L94](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L94) |
| `DESC_OPT_READY` | `未显式标注` | `'skilldev.desc_opt_ready'` | [L95](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L95) |

### [`class SkillDevEvent`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L99)

Pipeline 内部事件，最终被序列化为 AgentResponseChunk 推送给前端.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `event_type` | `SkillDevEventType` | `—` | [L102](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L102) |
| `payload` | `dict[str, Any]` | `—` | [L103](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L103) |
| `task_id` | `str` | `''` | [L104](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L104) |

### [`class SkillDevState`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L113)

Pipeline 运行时状态，在请求执行期间驻内存，在阶段边界通过 StateStore checkpoint.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `task_id` | `str` | `—` | [L116](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L116) |
| `stage` | `SkillDevStage` | `SkillDevStage.INIT` | [L117](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L117) |
| `mode` | `SkillDevTaskMode` | `SkillDevTaskMode.CREATE` | [L118](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L118) |
| `iteration` | `int` | `0` | [L119](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L119) |
| `input` | `dict[str, Any]` | `field(default_factory=dict)` | [L122](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L122) |
| `reference_texts` | `list[str]` | `field(default_factory=list)` | [L125](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L125) |
| `existing_skill_md` | `str \| None` | `None` | [L126](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L126) |
| `plan` | `dict[str, Any] \| None` | `None` | [L127](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L127) |
| `plan_confirmed_at` | `str \| None` | `None` | [L128](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L128) |
| `evals` | `dict[str, Any] \| None` | `None` | [L129](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L129) |
| `eval_results` | `dict[str, Any] \| None` | `None` | [L130](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L130) |
| `feedback_history` | `list[dict]` | `field(default_factory=list)` | [L131](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L131) |
| `desc_optimize_result` | `dict[str, Any] \| None` | `None` | [L134](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L134) |
| `zip_path` | `str \| None` | `None` | [L139](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L139) |
| `zip_size` | `int` | `0` | [L140](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L140) |
| `created_at` | `str` | `field(default_factory=lambda: _now_iso())` | [L143](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L143) |
| `updated_at` | `str` | `field(default_factory=lambda: _now_iso())` | [L144](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L144) |
| `error` | `str \| None` | `None` | [L145](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L145) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def touch(self) -> None` | 更新 updated_at 时间戳. | [L147](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L147) |
| `def to_checkpoint_dict(self) -> dict` | 序列化为可持久化的字典（用于 StateStore）. | [L151](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L151) |
| `@classmethod def from_checkpoint_dict(cls, data: dict) -> 'SkillDevState'` | 从持久化字典恢复状态. | [L175](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L175) |
| `def to_status_dict(self) -> dict` | 序列化为前端可展示的状态摘要. | [L197](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L197) |

### [`class SuspensionConfig`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L218)

挂起点的声明式配置.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `confirm_type` | `str` | `—` | [L230](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L230) |
| `title` | `str` | `—` | [L231](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L231) |
| `message` | `str` | `—` | [L232](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L232) |
| `actions` | `list[dict[str, str]]` | `—` | [L233](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L233) |
| `extract_data` | `Callable` | `—` | [L236](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L236) |
| `on_resume` | `Callable` | `—` | [L237](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L237) |
| `next_stage` | `SkillDevStage \| Callable` | `—` | [L238](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L238) |

### [`class EvalCase`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L341)

单个测试用例.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `id` | `int` | `—` | [L344](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L344) |
| `prompt` | `str` | `—` | [L345](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L345) |
| `expected_output` | `str` | `''` | [L346](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L346) |
| `files` | `list[str]` | `field(default_factory=list)` | [L347](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L347) |
| `expectations` | `list[str]` | `field(default_factory=list)` | [L348](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L348) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L350](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L350) |

### [`class EvalSet`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L361)

完整的测试集.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `skill_name` | `str` | `—` | [L364](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L364) |
| `evals` | `list[EvalCase]` | `field(default_factory=list)` | [L365](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L365) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L367](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L367) |
| `@classmethod def from_dict(cls, data: dict) -> 'EvalSet'` | 源码未提供方法级文档字符串。 | [L374](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L374) |

### [`class GradingExpectation`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L382)

单条 assertion 的评分结果.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `text` | `str` | `—` | [L385](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L385) |
| `passed` | `bool` | `—` | [L386](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L386) |
| `evidence` | `str` | `''` | [L387](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L387) |

### [`class GradingResult`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L391)

单次运行的评分结果（grading.json）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `expectations` | `list[GradingExpectation]` | `field(default_factory=list)` | [L394](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L394) |
| `pass_rate` | `float` | `0.0` | [L395](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L395) |
| `passed_count` | `int` | `0` | [L396](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L396) |
| `failed_count` | `int` | `0` | [L397](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L397) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L399](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L399) |

### [`class RunTiming`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L415)

单次运行的耗时数据（timing.json）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `total_tokens` | `int` | `0` | [L418](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L418) |
| `duration_ms` | `int` | `0` | [L419](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L419) |
| `total_duration_seconds` | `float` | `0.0` | [L420](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L420) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L422](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L422) |

### [`class MetricStats`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L431)

某指标的统计摘要.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `mean` | `float` | `0.0` | [L434](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L434) |
| `stddev` | `float` | `0.0` | [L435](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L435) |
| `min` | `float` | `0.0` | [L436](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L436) |
| `max` | `float` | `0.0` | [L437](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L437) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L439](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L439) |

### [`class BenchmarkRun`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L449)

benchmark.json 中的一条 run 记录.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `eval_id` | `int` | `—` | [L452](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L452) |
| `eval_name` | `str` | `—` | [L453](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L453) |
| `configuration` | `str` | `—` | [L454](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L454) |
| `run_number` | `int` | `1` | [L455](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L455) |
| `pass_rate` | `float` | `0.0` | [L456](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L456) |
| `time_seconds` | `float` | `0.0` | [L457](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L457) |
| `tokens` | `int` | `0` | [L458](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L458) |
| `expectations` | `list[dict]` | `field(default_factory=list)` | [L459](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L459) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L461](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L461) |

### [`class Benchmark`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L477)

完整的 benchmark 结果.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `skill_name` | `str` | `—` | [L480](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L480) |
| `runs` | `list[BenchmarkRun]` | `field(default_factory=list)` | [L481](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L481) |
| `run_summary` | `dict[str, Any]` | `field(default_factory=dict)` | [L482](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L482) |
| `notes` | `list[str]` | `field(default_factory=list)` | [L483](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L483) |
| `timestamp` | `str` | `field(default_factory=lambda: _now_iso())` | [L484](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L484) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L486](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L486) |

### [`class TriggerEvalQuery`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L496)

描述优化阶段的单个触发测试查询.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `query` | `str` | `—` | [L499](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L499) |
| `should_trigger` | `bool` | `—` | [L500](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L500) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L502](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L502) |

### [`class DescOptimizeIteration`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L507)

描述优化的单轮迭代结果.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `iteration` | `int` | `—` | [L510](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L510) |
| `description` | `str` | `—` | [L511](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L511) |
| `train_passed` | `int` | `0` | [L512](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L512) |
| `train_total` | `int` | `0` | [L513](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L513) |
| `test_passed` | `int \| None` | `None` | [L514](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L514) |
| `test_total` | `int \| None` | `None` | [L515](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L515) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict` | 源码未提供方法级文档字符串。 | [L517](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L517) |

### [`class _StageGroup`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L536)

一组后端阶段的展示配置.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `id` | `str` | `—` | [L539](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L539) |
| `label` | `str` | `—` | [L540](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L540) |
| `stages` | `frozenset[SkillDevStage]` | `—` | [L541](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L541) |
| `modes` | `frozenset[SkillDevTaskMode] \| None` | `None` | [L542](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L542) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _plan_extract_data(state: SkillDevState) -> dict` | 源码未提供函数级文档字符串。 | [L246](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L246) |
| `def _plan_confirm_on_resume(state: SkillDevState, data: dict) -> None` | 源码未提供函数级文档字符串。 | [L250](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L250) |
| `def _review_extract_data(state: SkillDevState) -> dict` | 源码未提供函数级文档字符串。 | [L256](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L256) |
| `def _review_on_resume(state: SkillDevState, data: dict) -> None` | 源码未提供函数级文档字符串。 | [L264](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L264) |
| `def _review_next_stage(data: dict) -> SkillDevStage` | 源码未提供函数级文档字符串。 | [L274](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L274) |
| `def _desc_opt_extract_data(state: SkillDevState) -> dict` | 源码未提供函数级文档字符串。 | [L279](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L279) |
| `def _desc_optimize_confirm_on_resume(state: SkillDevState, data: dict) -> None` | 源码未提供函数级文档字符串。 | [L284](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L284) |
| `def _desc_optimize_confirm_next_stage(data: dict) -> SkillDevStage` | 源码未提供函数级文档字符串。 | [L288](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L288) |
| `def compute_todos(current_stage: SkillDevStage, mode: SkillDevTaskMode \| None = None) -> list[dict[str, str]]` | 根据当前阶段和任务模式，计算面向用户的 Todo 列表. | [L592](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L592) |
| `def _now_iso() -> str` | 返回当前 UTC 时间的 ISO 8601 字符串. | [L647](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L647) |
| `def generate_task_id() -> str` | 生成唯一 task_id，格式：sd_{timestamp}_{random}. | [L654](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L654) |
| `def determine_task_mode(params: dict) -> SkillDevTaskMode` | 根据请求参数自动判断任务模式. | [L663](../../../../../jiuwenswarm/server/runtime/skill/skilldev/schema.py#L663) |

## `jiuwenswarm/server/runtime/skill/skilldev/service.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L1)

**模块职责：** SkillDevService — 无状态请求处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L41) |
| `_METHOD_DISPATCH` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L44) |

### [`class SkillDevService`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L55)

SkillDev 模式的服务入口（无状态）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, deps: SkillDevDeps) -> None` | 源码未提供方法级文档字符串。 | [L58](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L58) |
| `async def handle(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]` | 根据 ReqMethod 分发到具体处理函数. | [L65](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L65) |
| `async def _handle_start(self, params: dict, request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | 源码未提供方法级文档字符串。 | [L89](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L89) |
| `async def _handle_respond(self, params: dict, request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | 源码未提供方法级文档字符串。 | [L130](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L130) |
| `def _handle_status(self, params: dict, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L173](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L173) |
| `def _handle_download(self, params: dict, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L201](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L201) |
| `@staticmethod async def _handle_cancel(params: dict, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L235](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L235) |
| `def _handle_file_list(self, params: dict, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L249](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L249) |
| `def _handle_file_read(self, params: dict, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L278](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L278) |
| `@staticmethod def _event_to_chunk(event: SkillDevEvent, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L317](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L317) |
| `@staticmethod def _error_chunk(request_id: str, channel_id: str, message: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L328](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L328) |
| `@staticmethod def _build_file_tree(directory: Path, root: Path) -> list[dict]` | 递归构建文件树. | [L339](../../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L339) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/__init__.py#L1)

**模块职责：** SkillDev Pipeline 各阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/__init__.py#L21) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/base.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L1)

**模块职责：** StageHandler 基类和 StageResult.

### [`class StageResult`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L18)

阶段执行结果，由 Pipeline 读取以驱动状态跳转.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `next_stage` | `SkillDevStage` | `—` | [L21](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L21) |

### [`class StageHandler(ABC)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L24)

SkillDev Pipeline 阶段处理器基类.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@abstractmethod async def execute(self, ctx) -> StageResult` | 执行阶段逻辑. | [L32](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L32) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L1)

**模块职责：** DESC_OPTIMIZE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L43) |
| `MAX_ITERATIONS` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L45) |
| `HOLDOUT_RATIO` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L46) |
| `TRIGGER_QUERY_GEN_PROMPT` | `未显式标注` | [L52](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L52) |
| `IMPROVE_DESC_PROMPT` | `未显式标注` | [L75](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L75) |

### [`class _OptimizationLoopInput`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L103)

描述优化循环的输入参数封装.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `skill_name` | `str` | `—` | [L106](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L106) |
| `skill_body` | `str` | `—` | [L107](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L107) |
| `current_desc` | `str` | `—` | [L108](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L108) |
| `train_set` | `list[TriggerEvalQuery]` | `—` | [L109](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L109) |
| `test_set` | `list[TriggerEvalQuery]` | `—` | [L110](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L110) |

### [`class _ImproveDescriptionInput`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L114)

描述改进步骤的输入参数封装.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `skill_name` | `str` | `—` | [L117](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L117) |
| `skill_body` | `str` | `—` | [L118](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L118) |
| `current_desc` | `str` | `—` | [L119](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L119) |
| `train_results` | `list[dict]` | `—` | [L120](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L120) |
| `history` | `list[DescOptimizeIteration]` | `—` | [L121](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L121) |

### [`class DescOptimizeStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L124)

DESC_OPTIMIZE 阶段：优化 SKILL.md 的 description 以提高触发准确率.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L127](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L127) |
| `async def _generate_trigger_queries(self, ctx: SkillDevContext, skill_name: str, description: str) -> list[TriggerEvalQuery]` | 调用 Agent 生成 ~20 个触发测试查询. | [L197](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L197) |
| `@staticmethod def _split_eval_set(queries: list[TriggerEvalQuery], holdout: float, seed: int = 42) -> tuple[list[TriggerEvalQuery], list[TriggerEvalQuery]]` | 按 should_trigger 分层切分 train/test. | [L226](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L226) |
| `async def _optimization_loop(self, ctx: SkillDevContext, loop_input: _OptimizationLoopInput) -> tuple[str, list[DescOptimizeIteration]]` | 运行 eval → improve 循环，返回 (best_description, history). | [L250](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L250) |
| `async def _eval_description(self, ctx: SkillDevContext, description: str, queries: list[TriggerEvalQuery]) -> list[dict]` | 对每个 query，调用模型判断当前 description 是否会触发. | [L321](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L321) |
| `async def _improve_description(self, ctx: SkillDevContext, improve_input: _ImproveDescriptionInput) -> str` | 调用模型基于失败案例改进 description. | [L352](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L352) |
| `@staticmethod def _apply_description(skill_md: Path, old_desc: str, new_desc: str) -> None` | 替换 SKILL.md frontmatter 中的 description 字段. | [L377](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/desc_optimize_stage.py#L377) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L1)

**模块职责：** EVALUATE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L39) |
| `GRADER_SYSTEM_PROMPT` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L46) |
| `ANALYST_SYSTEM_PROMPT` | `未显式标注` | [L78](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L78) |

### [`class EvaluateStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L93)

EVALUATE 阶段：Grader 评分 → Benchmark 聚合 → Analyst 分析.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L96](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L96) |
| `async def _grade_all_evals(self, ctx: SkillDevContext, iter_dir: Path) -> None` | 为每个 eval 的 with_skill / baseline 结果执行评分. | [L141](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L141) |
| `def _aggregate_benchmark(self, ctx: SkillDevContext, iter_dir: Path) -> Benchmark` | 遍历所有 grading.json + timing.json，聚合为 Benchmark. | [L184](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L184) |
| `async def _analyze_patterns(self, ctx: SkillDevContext, benchmark: Benchmark) -> list[str]` | 分析 benchmark 结果，发现隐藏模式. | [L256](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L256) |
| `@staticmethod def _render_benchmark_md(benchmark: Benchmark) -> str` | 把 Benchmark 渲染为 Markdown 报告. | [L277](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L277) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _calc_stats(values: list[float]) -> MetricStats` | 源码未提供函数级文档字符串。 | [L316](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/evaluate_stage.py#L316) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L1)

**模块职责：** GENERATE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L23) |
| `GENERATE_SYSTEM_PROMPT` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L25) |

### [`class GenerateStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L85)

GENERATE 阶段：Agent 按 plan 生成完整 skill 文件集.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L88](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L88) |
| `def _resolve_generation_order(self, plan: dict) -> list[tuple[str, str]]` | 确定文件生成顺序：SKILL.md 优先，scripts/ 其次，其余最后. | [L124](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L124) |
| `async def _generate_all_files(self, ctx: SkillDevContext, skill_dir: Path, generation_order: list[tuple[str, str]]) -> list[str]` | 逐文件调用 Agent 生成内容. | [L149](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L149) |
| `async def _generate_single_file(self, agent, ctx: SkillDevContext, filepath: str, role: str) -> str` | 为单个文件生成内容. | [L188](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L188) |
| `async def _validate_scripts(self, skill_dir: Path) -> None` | 验证生成的 Python 脚本语法正确性. | [L197](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/generate_stage.py#L197) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L1)

**模块职责：** IMPROVE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L28) |
| `IMPROVE_SYSTEM_PROMPT` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L30) |

### [`class ImproveStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L79)

IMPROVE 阶段：Agent 根据用户反馈改进 Skill，随后进入下一轮测试.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L82](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L82) |
| `async def _run_improve_agent(self, ctx: SkillDevContext, feedback: dict, report: str) -> None` | 调用 Agent 分析反馈并修改 skill 文件. | [L107](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L107) |
| `def _read_skill_files(self, skill_dir) -> str` | 读取当前 skill 目录下所有文件内容. | [L130](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/improve_stage.py#L130) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L1)

**模块职责：** INIT 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L27) |

### [`class InitStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L30)

INIT 阶段：解析请求参数，准备工作区.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L33](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L33) |
| `async def _extract_resources(self, resources: list[dict], dest_dir: Path) -> list[str]` | 解析资源文件列表，提取纯文本内容. | [L70](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L70) |
| `def _parse_file_to_text(self, file_path: Path) -> str` | 将文件解析为纯文本. | [L97](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L97) |
| `async def _extract_existing_skill(self, existing_skill: dict, dest_dir: Path) -> str \| None` | 解压已有 skill.zip，提取 SKILL.md 内容. | [L113](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/init_stage.py#L113) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L1)

**模块职责：** PACKAGE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L20) |
| `_EXCLUDE_DIRS` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L23) |
| `_EXCLUDE_FILES` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L24) |
| `_EXCLUDE_GLOBS` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L25) |
| `_ROOT_EXCLUDE_DIRS` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L26) |

### [`class PackageStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L29)

PACKAGE 阶段：打包 skill/ 为 .skill (zip) 文件.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L32](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L32) |
| `def _zip_skill_dir(self, skill_dir: Path, zip_path: Path) -> None` | 将 skill_dir 打包为 zip，排除无关文件. | [L66](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L66) |
| `def _should_exclude(self, file_path: Path, skill_dir: Path) -> bool` | 判断文件是否应被排除出 zip 包. | [L80](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/package_stage.py#L80) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L1)

**模块职责：** PLAN 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L23) |
| `PLAN_SYSTEM_PROMPT` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L25) |

### [`class PlanStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L92)

PLAN 阶段：Agent 生成开发计划，随后进入 PLAN_CONFIRM 挂起点.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L95](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L95) |
| `async def _generate_plan(self, ctx: SkillDevContext) -> dict` | 调用 ReActAgent 生成 plan JSON. | [L108](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L108) |
| `def _build_messages(self, ctx: SkillDevContext) -> list[dict]` | 构造发送给 PLAN Agent 的消息列表. | [L143](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L143) |
| `def _parse_plan_json(self, text: str) -> dict` | 从 Agent 输出中提取 JSON plan. | [L157](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/plan_stage.py#L157) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L1)

**模块职责：** TEST_DESIGN 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L35) |
| `TEST_DESIGN_SYSTEM_PROMPT` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L37) |

### [`class TestDesignStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L73)

TEST_DESIGN 阶段：Agent 设计测试用例，输出 evals.json.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L76) |
| `def _read_skill_files(self, skill_dir) -> str` | 读取 skill 目录下所有文件，拼接为字符串供 Agent 分析. | [L94](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L94) |
| `async def _design_evals(self, ctx: SkillDevContext, skill_content: str) -> dict` | 调用 Agent 设计测试用例. | [L109](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_design_stage.py#L109) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L1)

**模块职责：** TEST_RUN 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L32) |

### [`class TestRunStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L35)

TEST_RUN 阶段：子 Agent 并行执行测试用例（with_skill vs baseline）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L38](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L38) |
| `async def _run_all_evals(self, ctx: SkillDevContext, eval_cases: list[dict], iter_dir) -> list[dict]` | 并行执行所有测试用例. | [L71](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L71) |
| `async def _run_single_eval(self, ctx: SkillDevContext, case: dict, case_dir) -> dict` | 为单个测试用例创建 with_skill + baseline 两组子 Agent 并行执行. | [L131](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/test_run_stage.py#L131) |

## `jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L1)

**模块职责：** VALIDATE 阶段处理器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L31) |

### [`class ValidateStageHandler(StageHandler)`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L34)

VALIDATE 阶段：校验 SKILL.md 格式合规性.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def execute(self, ctx: SkillDevContext) -> StageResult` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L37) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def validate_skill_md(skill_md_path: Path) -> tuple[bool, str]` | 校验 SKILL.md 的 YAML frontmatter 格式. | [L67](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L67) |
| `def parse_skill_frontmatter(skill_md_path: Path) -> tuple[str, str, str]` | 从 SKILL.md 解析出 (name, description, body_content). | [L111](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L111) |
| `def _parse_frontmatter(text: str) -> dict[str, str]` | 极简 YAML frontmatter 解析（key: value 单行 + block scalar）. | [L125](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L125) |
| `def _has_invalid_hyphen_usage(name: str) -> bool` | 校验 name 中连字符使用是否非法. | [L151](../../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/validate_stage.py#L151) |

## `jiuwenswarm/server/runtime/skill/skilldev/state_utils.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L1)

**模块职责：** Skill 状态工具函数 — 纯函数，无 SkillManager 依赖.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L18) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_state_file() -> Path` | 源码未提供函数级文档字符串。 | [L21](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L21) |
| `def normalize_skill_configs(raw_configs: Any) -> dict[str, dict[str, bool]]` | Normalize per-skill config records. | [L25](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L25) |
| `def get_registered_skill_names(state: dict[str, Any]) -> set[str]` | Return all skill names recorded in installed/local state lists. | [L42](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L42) |
| `def normalize_local_skills(raw_local_skills: Any, existing_local_skill_names: set[str], existing_clawhub_origins: set[str] \| None = None) -> list[dict[str, Any]]` | Keep only local skill records that still exist under the local skills dir. | [L58](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L58) |
| `def get_skill_enabled(state: dict[str, Any], skill_name: str) -> bool` | Read a skill enabled flag with backward-compatible default true. | [L98](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L98) |
| `def set_skill_enabled(state: dict[str, Any], skill_name: str, enabled: bool) -> None` | Persist a skill enabled flag into state. | [L113](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L113) |
| `def remove_skill_config(state: dict[str, Any], skill_name: str) -> bool` | Drop a skill's per-skill config record. Returns True if anything was removed. | [L126](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L126) |
| `def list_disabled_skills(state: dict[str, Any]) -> list[str]` | Return sorted disabled skill names from canonical config. | [L142](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L142) |
| `def list_execution_disabled_skills(state: dict[str, Any]) -> list[str]` | Return disabled skill names that are currently installed. | [L157](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L157) |
| `def load_execution_disabled_skills() -> list[str]` | Read skills_state.json and return disabled skill names that are installed. | [L168](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L168) |
| `def filter_visible_skill_names(names: list[str]) -> list[str]` | Return only the skill names that are not disabled. | [L180](../../../../../jiuwenswarm/server/runtime/skill/skilldev/state_utils.py#L180) |

## `jiuwenswarm/server/runtime/skill/skilldev/store.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L1)

**模块职责：** StateStore — SkillDev 任务状态的持久化层.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L19) |

### [`class StateStore`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L22)

SkillDev 任务状态存储（本地文件实现）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, base_dir: Path) -> None` | Args: base_dir: SkillDev 工作区根目录，约定为 get_workspace_dir() / "skilldev" 即 ~/.jiuwenswarm/agent/workspace/skilldev/ | [L29](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L29) |
| `def _state_file(self, task_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L37) |
| `async def save_state(self, task_id: str, state: SkillDevState) -> None` | 将状态序列化并写入 state.json（checkpoint）. | [L40](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L40) |
| `async def load_state(self, task_id: str) -> SkillDevState \| None` | 从 state.json 恢复状态，不存在则返回 None. | [L55](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L55) |
| `def load_state_sync(self, task_id: str) -> SkillDevState \| None` | 同步版 load_state，供非 async 上下文使用（如 status 查询）. | [L68](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L68) |
| `def list_tasks(self) -> list[str]` | 列出所有存在 checkpoint 的 task_id. | [L76](../../../../../jiuwenswarm/server/runtime/skill/skilldev/store.py#L76) |

## `jiuwenswarm/server/runtime/skill/skilldev/workspace.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L1)

**模块职责：** WorkspaceProvider — SkillDev 任务工作区管理.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L32) |

### [`class WorkspaceProvider`](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L35)

SkillDev 工作区管理（本地文件系统实现）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, base_dir: Path) -> None` | Args: base_dir: SkillDev 工作区根目录，约定为 get_workspace_dir() / "skilldev" 即 ~/.jiuwenswarm/agent/workspace/skilldev/ | [L38](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L38) |
| `def get_local_path(self, task_id: str) -> Path` | 返回指定任务的本地工作区路径（不保证已创建）. | [L46](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L46) |
| `async def ensure_local(self, task_id: str) -> Path` | 确保工作区目录及其标准子目录存在，返回工作区根路径. | [L50](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L50) |
| `async def sync_to_remote(self, task_id: str) -> None` | 将本地工作区同步到远程存储（本地实现为空操作）. | [L58](../../../../../jiuwenswarm/server/runtime/skill/skilldev/workspace.py#L58) |

## `jiuwenswarm/server/runtime/skill/source_registry.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L1)

**模块职责：** In-process registry for configured Skill Source providers.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_ID_RE` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L18) |

### [`class SourceRegistryError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L21)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, code: str, message: str)` | 源码未提供方法级文档字符串。 | [L22](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L22) |

### [`class _ProviderEntry`](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L28)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `config` | `SourceConfig` | `—` | [L29](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L29) |
| `provider` | `SkillSourceProvider` | `—` | [L30](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L30) |
| `display_name` | `str` | `—` | [L31](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L31) |
| `started` | `bool` | `False` | [L32](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L32) |
| `start_lock` | `asyncio.Lock \| None` | `None` | [L33](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L33) |

### [`class SourceRegistry`](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L36)

Keeps runtime provider instances only; it never stores workspace state.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L39](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L39) |
| `def register(self, config: SourceConfig, provider: SkillSourceProvider, *, display_name: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L42](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L42) |
| `def bind_extension(self, config: SourceConfig, extension: SkillSourceExtension, *, display_name: str \| None = None) -> SkillSourceProvider` | Create and bind a configured Provider through an extension factory. | [L72](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L72) |
| `def list(self) -> list[SourceDescriptor]` | 源码未提供方法级文档字符串。 | [L88](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L88) |
| `def get_config(self, source_id: str) -> SourceConfig` | Return the trusted runtime configuration for one source. | [L102](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L102) |
| `async def get(self, source_id: str, capability: str) -> SkillSourceProvider` | 源码未提供方法级文档字符串。 | [L109](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L109) |
| `async def close(self) -> None` | 源码未提供方法级文档字符串。 | [L135](../../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L135) |

## `jiuwenswarm/server/runtime/skill/sources/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/sources/__init__.py#L1)

**模块职责：** Built-in Skill Source provider adapters.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L8](../../../../../jiuwenswarm/server/runtime/skill/sources/__init__.py#L8) |

## `jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L1)

**模块职责：** SwarmSkillHub protocol adapter for the common Skill Source SPI.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SWARM_SKILL_HUB_SOURCE_ID` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L25) |

### [`class SwarmSkillHubProvider(SkillSourceProvider)`](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L68)

Maps `/api/v1/plugins` and `/api/v1/artifacts` to the v2 SPI.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `source_id` | `未显式标注` | `SWARM_SKILL_HUB_SOURCE_ID` | [L71](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L71) |
| `provider_type` | `未显式标注` | `'swarmskillhub'` | [L72](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L72) |
| `display_name` | `未显式标注` | `'SwarmSkillHub'` | [L73](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L73) |
| `capabilities` | `未显式标注` | `frozenset({'search', 'check_updates', 'get_artifact'})` | [L74](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L74) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, base_url: str, *, source_id: str = SWARM_SKILL_HUB_SOURCE_ID, display_name: str = 'SwarmSkillHub', timeout: float = 60.0) -> None` | 源码未提供方法级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L76) |
| `async def start(self) -> None` | 源码未提供方法级文档字符串。 | [L92](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L92) |
| `async def close(self) -> None` | 源码未提供方法级文档字符串。 | [L96](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L96) |
| `async def _get_data(self, path: str, params: Mapping[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L101](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L101) |
| `async def search(self, request: SkillSearchRequest, context: ProviderInvocationContext) -> SkillSearchResult` | 源码未提供方法级文档字符串。 | [L131](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L131) |
| `async def check_updates(self, installed: list[InstalledArtifact] \| tuple[InstalledArtifact, ...], context: ProviderInvocationContext) -> tuple[SkillUpdateStatus, ...]` | 源码未提供方法级文档字符串。 | [L208](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L208) |
| `async def get_artifact(self, skill_ref: SkillRef, version_id: str, context: ProviderInvocationContext) -> ArtifactDescriptor` | 源码未提供方法级文档字符串。 | [L269](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L269) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _text(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L28](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L28) |
| `def _int_or_none(value: Any) -> int \| None` | 源码未提供函数级文档字符串。 | [L32](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L32) |
| `def _float_or_none(value: Any) -> float \| None` | 源码未提供函数级文档字符串。 | [L39](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L39) |
| `def _bool_value(value: Any, default: bool = False) -> bool` | 源码未提供函数级文档字符串。 | [L46](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L46) |
| `def _timestamp(value: Any) -> datetime \| str \| int \| None` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/server/runtime/skill/sources/swarm_skill_hub.py#L54) |

## `jiuwenswarm/server/runtime/skill/workspace_provider.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L1)

**模块职责：** Resolve and prepare the workspace before constructing ``SkillManager``.

### [`class SkillWorkspaceUnavailable(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L17)

The requested workspace cannot safely host Skill state.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `code` | `未显式标注` | `'workspace_unavailable'` | [L20](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L20) |

### [`class SkillWorkspace`](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L24)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `workspace_dir` | `Path` | `—` | [L25](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L25) |
| `skills_dir` | `Path` | `—` | [L26](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L26) |
| `state_file` | `Path` | `—` | [L27](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L27) |
| `existed` | `bool` | `—` | [L28](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L28) |

### [`class SkillWorkspaceProvider`](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L31)

Idempotently prepare one explicit workspace without fallback routing.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_manager_lock` | `未显式标注` | `threading.RLock()` | [L34](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L34) |
| `_managers` | `'WeakValueDictionary[str, Any]'` | `WeakValueDictionary()` | [L35](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L35) |
| `_EMPTY_STATE` | `未显式标注` | `{'marketplaces': [], 'installed_plugins': [], 'local_skills': [], 'skill_configs': {}}` | [L37](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L37) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def ensure(self, workspace_dir: str \| Path, *, require_valid_state: bool) -> SkillWorkspace` | 源码未提供方法级文档字符串。 | [L44](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L44) |
| `def get_or_create_manager(self, workspace_dir: str \| Path, *, require_valid_state: bool, factory: Callable[[SkillWorkspace], Any]) -> tuple[Any, bool]` | Reuse a manager by verified workspace path or create it exactly once. | [L96](../../../../../jiuwenswarm/server/runtime/skill/workspace_provider.py#L96) |

## `jiuwenswarm/server/runtime/skill_turbo/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/__init__.py#L1)

**模块职责：** SkillTurbo 模块。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_EXPORTS` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/skill_turbo/__init__.py#L10) |
| `__all__` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/__init__.py#L26) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __getattr__(name: str) -> Any` | 源码未提供函数级文档字符串。 | [L29](../../../../../jiuwenswarm/server/runtime/skill_turbo/__init__.py#L29) |

## `jiuwenswarm/server/runtime/skill_turbo/agent.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L1)

**模块职责：** SkillTurbo 主入口 -- 串联规划、执行、降级。

### [`class SkillTurboNotHandled(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L19)

SkillTurbo 无法处理该任务，需要降级到 DeepAgent。

### [`class SkillTurbo`](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L23)

SkillTurbo 主入口。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: dict[str, Any])` | 源码未提供方法级文档字符串。 | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L26) |
| `@property def artifact_holder(self) -> dict[str, dict[str, Any]]` | 返回 executor 的节点产物 holder，供外部构建产物摘要。 | [L32](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L32) |
| `async def run(self, task: str, inputs: dict[str, Any]) -> Any` | 主入口：规划 -> 执行，失败降级到 DeepAgent（非流式）。 | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L36) |
| `async def run_stream(self, task: str, inputs: dict[str, Any], request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | 主入口：规划 -> 执行，失败降级到 DeepAgent（流式）。 | [L56](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L56) |
| `async def resume_stream(self, *, plan_code: str, inputs: dict[str, Any], request_id: str, channel_id: str, pending_tool_call_id: str, user_input: Any, task_states: list[dict[str, Any]] \| None = None) -> AsyncIterator[AgentResponseChunk]` | 从 HITL 中断点恢复执行：跳过 planner，直接重放 plan_code。 | [L99](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L99) |

## `jiuwenswarm/server/runtime/skill_turbo/environment.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L1)

**模块职责：** SkillTurboEnvironment -- 灵魂、工具、模型、技能配置环境。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L23) |
| `_DEFAULT_SKILL_CODES_DIR` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L26) |
| `_DEFAULT_SKILLS_DIR` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L27) |
| `_DEFAULT_SKILL_CODE_IMPORT_PACKAGE` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L31) |
| `_CHECKSUM_EXCLUDE_DIRS` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L36) |
| `_CHECKSUM_EXCLUDE_FILES` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L37) |

### [`class Skill`](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L92)

技能定义 -- 包含描述和预规划的 plan_code。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L95](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L95) |
| `description` | `str` | `—` | [L96](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L96) |
| `skill_md` | `str` | `—` | [L97](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L97) |
| `plan_code` | `str \| None` | `None` | [L98](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L98) |
| `match_keywords` | `list[str]` | `field(default_factory=list)` | [L99](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L99) |

### [`class SkillTurboEnvironment`](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L105)

Agent 环境 -- 灵魂、工具、模型、技能。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_scan_cache` | `ClassVar[dict[str, tuple[float, list['Skill']]]]` | `{}` | [L118](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L118) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: dict[str, Any])` | 源码未提供方法级文档字符串。 | [L120](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L120) |
| `def _resolve_skill_root(self) -> str` | 解析 skill_root 路径。 | [L172](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L172) |
| `def _resolve_sys_operation(self) -> Any` | 解析 sys_operation。 | [L218](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L218) |
| `@property def soul(self) -> str` | 源码未提供方法级文档字符串。 | [L267](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L267) |
| `@property def model_client(self) -> Model \| None` | 源码未提供方法级文档字符串。 | [L271](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L271) |
| `@property def config(self) -> dict[str, Any]` | 对外暴露原始配置 dict（含 ``permissions``），供 PermissionInterruptRail 使用。 | [L275](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L275) |
| `@property def tools(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L280](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L280) |
| `@property def fallback_handler(self) -> Any` | 节点级 fallback 委托 handler，由 DeepAdapter 注入。 | [L284](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L284) |
| `@property def card(self) -> Any` | agent card，executor 创建 session 时用于初始化 checkpointer。 | [L289](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L289) |
| `@property def skills(self) -> dict[str, Skill]` | 源码未提供方法级文档字符串。 | [L294](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L294) |
| `@property def skills_dir(self) -> str` | 源码未提供方法级文档字符串。 | [L298](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L298) |
| `@property def skill_root(self) -> str` | 技能根目录，用于 skill_code 定位外部资源（如 pptx-craft）。 | [L302](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L302) |
| `@property def skill_name(self) -> str` | [TEMP-EXTERNAL-SKILL] PPT skill 的外部目录名。 | [L307](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L307) |
| `@property def skill_checksum(self) -> str` | [TEMP-EXTERNAL-SKILL] 外部 skill 目录的 SHA256 校验值。 | [L312](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L312) |
| `@property def skill_checksum_ok(self) -> bool` | [TEMP-EXTERNAL-SKILL] SHA256 校验是否通过（空值时为 True）。 | [L317](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L317) |
| `@property def skill_codes_dir(self) -> str` | 源码未提供方法级文档字符串。 | [L322](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L322) |
| `@property def skill_code_import_package(self) -> str` | 源码未提供方法级文档字符串。 | [L326](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L326) |
| `@property def skill_code_import_prefixes(self) -> list[str]` | Validator 用：允许的 import 包前缀（基于包名，非文件系统路径）。 | [L330](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L330) |
| `@property def skill_codes_parent_dir(self) -> str` | skill_codes 包的父目录，用于 sys.path 注入。 | [L338](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L338) |
| `@property def tool_names(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L345](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L345) |
| `def get_tool_function(self, tool_name: str) -> Callable[..., Awaitable[Any]] \| None` | 获取工具的可调用函数。 | [L350](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L350) |
| `def get_tool_info_list(self) -> list[dict[str, Any]]` | 获取工具描述列表（供 Planner prompt 使用）。 | [L392](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L392) |
| `def register_skill(self, skill: Skill) -> None` | 注册或覆盖一个 skill。统一入口，便于扫描器、测试、上层注入。 | [L404](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L404) |
| `def has_skill(self, name: str) -> bool` | 源码未提供方法级文档字符串。 | [L415](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L415) |
| `def get_skill(self, name: str) -> Skill \| None` | 源码未提供方法级文档字符串。 | [L418](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L418) |
| `def reload(self) -> None` | 重新加载 skills：清空并重新执行加载流程（硬编码 + 目录扫描）。 | [L421](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L421) |
| `def register_tool(self, tool_card: Any) -> None` | 注册一个工具 ToolCard。重复注册以最新一次为准并写 info 日志。 | [L429](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L429) |
| `def has_tool(self, name: str) -> bool` | 源码未提供方法级文档字符串。 | [L444](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L444) |
| `async def register_tools(self, context: 'ToolLoaderContext \| None' = None) -> None` | 装载 jiuwenswarm + openjiuwen 工具到 environment。 | [L447](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L447) |
| `def build_tool_loader_context(self, *, request_id: str = '', session_id: str = '', channel_id: str = '', request_metadata: dict[str, Any] \| None = None) -> 'ToolLoaderContext'` | 源码未提供方法级文档字符串。 | [L478](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L478) |
| `def _refresh_send_file_tools(self, ctx: 'ToolLoaderContext', loader: Callable[[Any], list[Any]]) -> None` | 按请求刷新 send_file_to_user（移除旧实例后重新注册）。 | [L503](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L503) |
| `def _load(self, config: dict[str, Any]) -> None` | 从配置加载 soul / tools / model / skills / skill_codes。 | [L561](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L561) |
| `def _scan_skills_dir(self) -> None` | 扫描 ``skill_codes_dir`` 目录注册自定义 skill。 | [L572](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L572) |
| `@staticmethod def _compute_skill_codes_mtime(base: Path) -> float` | 计算 skill_codes 目录内所有 .py 文件的最大 mtime。 | [L687](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L687) |
| `def _validate_skill_code_dir(self, skill_name: str, skill_dir: Path) -> bool` | 加载 skill 时校验目录内所有 Python 代码。 | [L707](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L707) |
| `@staticmethod def _find_skill_root_file(skill_dir: Path) -> Path \| None` | 查找技能入口文件。 | [L754](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L754) |
| `def _build_plan_code(self, skill_name: str, root_file: Path) -> str` | 构建技能的 plan_code。 | [L782](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L782) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _compute_dir_checksum(dir_path: str) -> str` | [TEMP-EXTERNAL-SKILL] 计算目录的确定性 SHA256。 | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L40) |
| `def _verify_skill_checksum(pptx_root: str, expected_checksum: str) -> bool` | [TEMP-EXTERNAL-SKILL] 校验外部 skill 目录的 SHA256。 | [L67](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L67) |

## `jiuwenswarm/server/runtime/skill_turbo/evolver.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L1)

**模块职责：** SkillTurboEvolver -- 执行轨迹收集与规划优化（二期预留）。

### [`class ExecutionTrace`](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L16)

单次节点执行轨迹。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `plan_name` | `str` | `—` | [L19](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L19) |
| `instruction` | `str` | `—` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L20) |
| `inputs` | `dict[str, Any]` | `—` | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L21) |
| `output` | `Any` | `—` | [L22](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L22) |
| `success` | `bool` | `—` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L23) |
| `error` | `str \| None` | `None` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L24) |
| `fallback_used` | `bool` | `False` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L25) |
| `duration_ms` | `float` | `0.0` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L26) |
| `timestamp` | `datetime` | `field(default_factory=datetime.now)` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L27) |

### [`class PlanExecution`](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L31)

一次完整 plan 执行记录。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `task` | `str` | `—` | [L34](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L34) |
| `plan_code` | `str` | `—` | [L35](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L35) |
| `traces` | `list[ExecutionTrace]` | `—` | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L36) |
| `total_duration_ms` | `float` | `—` | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L37) |
| `final_success` | `bool` | `—` | [L38](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L38) |
| `fallback_count` | `int` | `—` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L39) |

### [`class SkillTurboEvolver`](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L42)

演进模块 -- 收集执行数据，反思优化规划。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, environment: SkillTurboEnvironment)` | 源码未提供方法级文档字符串。 | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L45) |
| `def record(self, execution: PlanExecution) -> None` | 源码未提供方法级文档字符串。 | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L49) |
| `def get_optimization_hints(self) -> dict[str, Any]` | 根据历史执行记录生成优化建议（二期实现）。 | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/evolver.py#L52) |

## `jiuwenswarm/server/runtime/skill_turbo/executor.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1)

**模块职责：** SkillTurboExecutor —— 规划代码校验、加载与异步执行。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `STREAM_SOURCE_ID_FIELD` | `未显式标注` | [L80](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L80) |
| `_retry_session` | `ContextVar[Any]` | [L83](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L83) |
| `logger` | `未显式标注` | [L125](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L125) |
| `_DEFAULT_LLM_MAX_TOKENS` | `未显式标注` | [L128](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L128) |
| `_session_var` | `ContextVar[Session \| None]` | [L186](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L186) |
| `_SKILL_TURBO_STREAM_FLUSH_INTERVAL_SECONDS` | `float` | [L191](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L191) |
| `_BUFFERABLE_EVENT_TYPES` | `frozenset[str]` | [L194](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L194) |
| `_request_id_var` | `ContextVar[str]` | [L197](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L197) |
| `_channel_id_var` | `ContextVar[str]` | [L198](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L198) |
| `_current_task_context_var` | `ContextVar[dict[str, Any] \| None]` | [L201](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L201) |
| `_current_task_holder_var` | `ContextVar[dict[str, str \| None] \| None]` | [L204](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L204) |
| `_task_events_queue_var` | `ContextVar[list[dict[str, Any]] \| None]` | [L209](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L209) |
| `_task_states_var` | `ContextVar[dict[str, dict[str, Any]] \| None]` | [L214](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L214) |
| `_SAFE_BUILTINS` | `dict[str, Any]` | [L231](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L231) |
| `_LLM_QUEUE_WAIT_LOG_THRESHOLD_MS` | `float` | [L308](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L308) |
| `_SKILL_TURBO_TC_PREFIX` | `未显式标注` | [L396](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L396) |

### [`class ToolCall`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L221)

简单的ToolCall对象，用于Rail回调。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L223) |
| `arguments` | `dict[str, Any]` | `—` | [L224](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L224) |
| `id` | `str` | `—` | [L225](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L225) |

### [`class PlanCodeLoadError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L283)

规划代码加载或根节点提取失败。

### [`class ExecutionTimeoutError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L287)

执行超时异常。

### [`class FallbackLimitExceededError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L291)

Fallback 次数超过限制。

### [`class ExecutorConfig`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L297)

Executor 配置项。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `execution_timeout` | `float` | `300.0` | [L300](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L300) |
| `max_fallback_count` | `int` | `3` | [L301](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L301) |
| `enable_fallback` | `bool` | `True` | [L302](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L302) |
| `enable_trace` | `bool` | `True` | [L303](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L303) |

### [`class ExecutionTrace`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L313)

单次执行追踪记录。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `plan_code_hash` | `str` | `''` | [L316](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L316) |
| `input_keys` | `list[str]` | `field(default_factory=list)` | [L317](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L317) |
| `start_time` | `float` | `0.0` | [L318](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L318) |
| `end_time` | `float` | `0.0` | [L319](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L319) |
| `duration_ms` | `float` | `0.0` | [L320](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L320) |
| `success` | `bool` | `False` | [L321](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L321) |
| `error` | `str \| None` | `None` | [L322](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L322) |
| `fallback_count` | `int` | `0` | [L323](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L323) |
| `node_execution_order` | `list[str]` | `field(default_factory=list)` | [L324](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L324) |

### [`class TaskCompleteEventData`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L328)

task.complete 事件构建所需的具名参数集合（G.FNM.03）。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `subplan` | `PlanNode` | `—` | [L331](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L331) |
| `task_id` | `str` | `—` | [L332](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L332) |
| `status` | `str` | `—` | [L333](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L333) |
| `timestamp` | `float` | `—` | [L334](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L334) |
| `duration_ms` | `int` | `—` | [L335](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L335) |
| `error` | `Any \| None` | `—` | [L336](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L336) |

### [`class _StreamBufferBucket`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L351)

单个 (stream_source_id, event_type) 桶的缓冲状态。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `parts` | `list[str]` | `field(default_factory=list)` | [L359](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L359) |
| `since` | `float` | `0.0` | [L360](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L360) |
| `first_chunk_sent` | `bool` | `False` | [L361](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L361) |
| `plan_name` | `str \| None` | `None` | [L362](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L362) |

### [`class _StreamBufferState`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L366)

流式缓冲层状态，按 (source_id, event_type) 分桶管理。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `buckets` | `dict[tuple[str \| None, str], _StreamBufferBucket]` | `field(default_factory=dict)` | [L374](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L374) |
| `last_emitted` | `dict[tuple[str \| None, str], str]` | `field(default_factory=dict)` | [L377](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L377) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_bucket(self, source_id: str \| None, event_type: str) -> _StreamBufferBucket` | 源码未提供方法级文档字符串。 | [L379](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L379) |
| `def all_buckets(self) -> list[tuple[tuple[str \| None, str], _StreamBufferBucket]]` | 源码未提供方法级文档字符串。 | [L389](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L389) |
| `def clear(self) -> None` | 源码未提供方法级文档字符串。 | [L392](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L392) |

### [`class SkillTurboExecutor`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L442)

规划代码运行时引擎。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_DENIED_FROMLIST_NAMES` | `frozenset[str]` | `frozenset({'__import__', '__builtins__', '__build_class__', 'exec', 'eval', 'compile', 'open', 'glo…` | [L3312](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3312) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, environment: SkillTurboEnvironment, trace_collector: SkillTurboEvolver \| None = None, config: ExecutorConfig \| None = None)` | 源码未提供方法级文档字符串。 | [L445](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L445) |
| `def _display_name(self, plan_name: str) -> str` | 将内部 plan_name 转为界面上展示的名称，未映射时原样返回。 | [L531](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L531) |
| `def _replace_plan_names_in_text(self, text: str) -> str` | 将文本中出现的已映射 plan_name 替换为对应的显示名。 | [L535](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L535) |
| `def validate(self, plan_code: str) -> list[str]` | 校验规划代码。 | [L545](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L545) |
| `@property def node_artifacts(self) -> dict[str, dict[str, Any]]` | 返回节点产物 holder，供外部构建产物摘要。 | [L550](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L550) |
| `def _merge_env_config_to_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]` | 将环境配置合并到 inputs，供 skill_code 使用。 | [L554](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L554) |
| `def _build_tool_loader_context(self, inputs: dict[str, Any], *, request_id: str = '', channel_id: str = '')` | 源码未提供方法级文档字符串。 | [L582](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L582) |
| `async def execute_plan(self, plan_code: str, inputs: dict[str, Any]) -> Any` | 执行规划代码的完整流程。 | [L601](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L601) |
| `async def execute_plan_stream(self, plan_code: str, inputs: dict[str, Any], request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | 流式执行规划代码。 | [L664](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L664) |
| `def _build_permission_rail(self) -> Any \| None` | 构建 PermissionInterruptRail；权限被禁用或构建失败时返回 None。 | [L902](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L902) |
| `def _build_ask_user_rail(self) -> SkillTurboAskUserRail \| None` | 构建结构化 ask_user rail；构建失败时返回 None（不阻塞执行）。 | [L941](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L941) |
| `async def _run_rail_hook(self, hook_name: str, ctx: AgentCallbackContext, *, skip_rails: set[Any] \| None = None) -> None` | 按 Rail 优先级执行 hook。 | [L956](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L956) |
| `def _setup_execution_context(self, plan_code: str, inputs: dict[str, Any], start: float, *, request_id: str = '', channel_id: str = '', enable_task_tracking: bool = False) -> dict[str, Any]` | 初始化请求级 ContextVar，并返回 token 供 finally 中按原上下文恢复。 | [L997](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L997) |
| `@staticmethod def _reset_execution_context(tokens: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L1121](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1121) |
| `async def _finish_trace(self, start: float, log_prefix: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L1146](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1146) |
| `async def _clear_stale_node_artifacts(self) -> None` | 新一轮 SkillTurbo 执行前，清除 session 中残留的上轮 node_artifacts。 | [L1167](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1167) |
| `async def _persist_node_artifacts(self, session: Any, *, skip_post_run: bool = False) -> None` | 将内存中的节点产物记录落盘到 session state（checkpointer 持久化）。 | [L1209](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1209) |
| `@staticmethod def _build_tool_call_context(tool_name: str, kwargs: dict[str, Any]) -> AgentCallbackContext` | 源码未提供方法级文档字符串。 | [L1239](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1239) |
| `@staticmethod def _build_model_call_context() -> AgentCallbackContext` | 源码未提供方法级文档字符串。 | [L1261](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1261) |
| `@staticmethod def _serialize_usage_metadata(usage_metadata: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1275](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1275) |
| `async def _emit_llm_usage(self, session: Session \| None, usage_metadata: Any, *, node_name: str \| None = None, stream_source_id: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L1297](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1297) |
| `def _interface_log_session_id(self) -> str` | 源码未提供方法级文档字符串。 | [L1329](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1329) |
| `@staticmethod def _session_id(session: Session \| None) -> str` | 源码未提供方法级文档字符串。 | [L1336](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1336) |
| `@staticmethod def _set_llm_interface_log_session() -> Any \| None` | 源码未提供方法级文档字符串。 | [L1343](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1343) |
| `@staticmethod def _reset_llm_interface_log_session(token: Any \| None) -> None` | 源码未提供方法级文档字符串。 | [L1351](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1351) |
| `def has_tool(self, tool_name: str) -> bool` | 检查工具是否存在。 | [L1359](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1359) |
| `def current_task_id(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L1371](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1371) |
| `def get_workspace_base_path(self) -> Path \| None` | 源码未提供方法级文档字符串。 | [L1374](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1374) |
| `def set_pending_resume(self, *, expected_tool_call_id: str, user_input: Any, task_states: list[dict[str, Any]] \| None = None) -> None` | adapter 在 resume 路径开始执行前调用，注入用户审批回复。 | [L1392](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1392) |
| `def _take_resume_task_states(self) -> list[dict[str, Any]] \| None` | 从 pending_resume 取出任务快照（只取一次，不影响 user_input 消费）。 | [L1421](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1421) |
| `def _snapshot_task_states(self) -> list[dict[str, Any]]` | 中断时导出当前二层任务快照，供 resume_ctx 持久化。 | [L1431](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1431) |
| `def _consume_pending_resume_input(self, current_tool_call_id: str, current_tool_name: str) -> tuple[Any \| None, str]` | 取出对当前 tool_call 的用户回复。 | [L1440](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1440) |
| `def _next_tool_call_id(self, tool_name: str, kwargs: dict[str, Any]) -> str` | 生成确定性 tool_call_id：基于 (tool_name, request_id, canonical_args, call_index) 哈希。 | [L1501](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1501) |
| `async def use_tool(self, tool_name: str, **kwargs: Any) -> Any` | 调用工具（带 PermissionInterruptRail 护栏）。 | [L1535](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1535) |
| `def _tool_trace_session_id(self) -> str` | 工具 trace 用的 session_id，优先复用 LLM trace 的 ContextVar，保持口径一致。 | [L1714](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1714) |
| `def _read_llm_concurrency_limit(self) -> int` | 从 environment.config 读取 LLM 并发上限。 | [L1727](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1727) |
| `def _llm_concurrency_guard(self) -> AbstractAsyncContextManager[None]` | 返回 LLM 并发限制的异步上下文管理器。 | [L1747](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1747) |
| `@contextlib.asynccontextmanager async def _acquire_llm_slot(self, sem: asyncio.Semaphore) -> AsyncIterator[None]` | 带排队拥堵日志的 Semaphore 包装。 | [L1767](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1767) |
| `@staticmethod def _gen_stream_source_id(node_name: str) -> str` | 生成并发场景下的 stream_source_id。 | [L1809](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1809) |
| `async def call_llm(self, prompt: str, system_prompt: str = '', *, node_name: str = 'unknown', concurrent: bool = False, thinking: str \| None = None) -> str` | 调用 LLM（使用Rail机制）。 | [L1817](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1817) |
| `async def stream_llm(self, prompt: str, system_prompt: str = '', node_name: str = 'unknown', concurrent: bool = False, thinking: str \| None = None) -> AsyncIterator[str]` | 流式调用 LLM（使用Rail机制）。 | [L1964](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1964) |
| `async def fallback(self, node: PlanNode, inputs: dict[str, Any], error: Exception) -> Any` | 节点执行失败时委托 fallback_handler 兜底。 | [L2135](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2135) |
| `async def fallback_stream(self, node: PlanNode, inputs: dict[str, Any], error: Exception) -> AsyncIterator[dict[str, Any]]` | 节点执行失败时委托 fallback_handler 兜底（流式版本）。 | [L2182](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2182) |
| `async def _execute_node_stream(self, node: PlanNode, inputs: dict[str, Any], request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | 流式执行单个节点，并实时转发工具等框架事件。 | [L2230](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2230) |
| `async def _flush_bucket_chunks(self, buffer_state: _StreamBufferState, bucket_key: tuple[str \| None, str], request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | flush 单个缓冲桶，yield 合并后的 chunk。 | [L2356](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2356) |
| `async def _flush_all_buffer_chunks(self, buffer_state: _StreamBufferState, request_id: str, channel_id: str) -> AsyncIterator[AgentResponseChunk]` | flush 所有缓冲桶，按 FIFO 顺序 yield 合并后的 chunk。 | [L2396](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2396) |
| `def _check_fallback_limit(self, error: Exception) -> None` | 检查 fallback 前置条件：是否启用、是否超限。不修改计数。 | [L2413](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2413) |
| `def _record_fallback_call(self, node: PlanNode, trace_prefix: str) -> None` | 源码未提供方法级文档字符串。 | [L2432](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2432) |
| `@staticmethod def _make_chunk(request_id: str, channel_id: str, payload: dict[str, Any] \| None, is_complete: bool = False) -> AgentResponseChunk` | 集中构造流式响应，避免各事件分支的基础字段出现漂移。 | [L2438](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2438) |
| `def _make_complete_chunk(self, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2454](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2454) |
| `def _make_error_chunk(self, request_id: str, channel_id: str, error: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2457](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2457) |
| `def _make_plan_started_chunk(self, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2469](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2469) |
| `def _make_plan_finished_chunk(self, request_id: str, channel_id: str, *, status: str \| None = None, error: str \| None = None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2484](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2484) |
| `def _make_event_chunk(self, request_id: str, channel_id: str, payload: dict[str, Any], task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2503](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2503) |
| `def _make_session_event_chunk(self, request_id: str, channel_id: str, payload: dict[str, Any], task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2514](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2514) |
| `def _make_task_event_chunk(self, task_event: dict[str, Any]) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2523](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2523) |
| `def _make_node_started_chunk(self, request_id: str, channel_id: str, node: PlanNode, task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2531](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2531) |
| `def _make_node_finished_chunk(self, request_id: str, channel_id: str, node: PlanNode, task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2548](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2548) |
| `def _make_node_delta_chunk(self, request_id: str, channel_id: str, node: PlanNode, content: Any, task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2563](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2563) |
| `def _make_node_error_chunk(self, request_id: str, channel_id: str, node: PlanNode, error: str, task_id: str \| None) -> AgentResponseChunk` | 源码未提供方法级文档字符串。 | [L2612](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2612) |
| `def _current_task_id(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L2629](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2629) |
| `async def _drain_task_event_chunks(self) -> AsyncIterator[AgentResponseChunk]` | 将 PlanNode 回调期间暂存的 task 事件按 FIFO 顺序转成前端 chunk。 | [L2653](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2653) |
| `@staticmethod def _normalize_task_event_type(task_event: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L2664](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2664) |
| `async def _initialize_pending_tasks(self, root: PlanNode) -> None` | 预置二层任务列表，让前端在第一个 task.start 前就能展示完整待办。 | [L2670](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2670) |
| `def _restore_task_states_from_snapshot(self, root: PlanNode, saved: list[dict[str, Any]]) -> dict[str, dict[str, Any]]` | 按 display name / index 对齐 resume 快照，复用原 ``task_id``。 | [L2717](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2717) |
| `def _live_task_states(self) -> dict[str, dict[str, Any]] \| None` | 运行中任务表：优先实例 holder（跨 create_task 共享），再回退 ContextVar。 | [L2767](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2767) |
| `async def _emit_task_update_event(self) -> None` | 发送 task.update 事件（全量任务状态快照）。 | [L2773](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2773) |
| `async def _should_skip_subplan_execute(self, subplan: PlanNode, inputs: dict[str, Any]) -> bool` | HITL resume 重放时，跳过已 completed 的二层 Stage 真实执行。 | [L2841](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2841) |
| `async def _should_suppress_subplan_start_banner(self, subplan: PlanNode, inputs: dict[str, Any]) -> bool` | HITL resume 重放时，抑制二层 stage 的「开始执行」进度横幅。 | [L2871](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2871) |
| `async def _before_subplan_execute(self, subplan: PlanNode, inputs: dict[str, Any]) -> None` | 子节点执行前回调 - 收集 task.start 事件数据。 | [L2903](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2903) |
| `async def _after_subplan_execute(self, subplan: PlanNode, inputs: dict[str, Any], result_or_error: Any) -> None` | 子节点执行后回调 - 收集 task.complete 事件数据。 | [L2984](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2984) |
| `def _collect_node_artifact(self, subplan: PlanNode, result_or_error: Any, task_id: str, timestamp: float, is_error: bool) -> None` | 节点产物收集。 | [L3091](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3091) |
| `def _get_or_create_task_state(self, subplan: PlanNode, task_states: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]` | 优先复用预置任务；兜底支持运行时出现的动态子节点。 | [L3138](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3138) |
| `def _find_task_state_by_plan_name(self, plan_name: str, task_states: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] \| None` | 源码未提供方法级文档字符串。 | [L3166](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3166) |
| `def _set_current_task_context(self, subplan: PlanNode, task_id: str, timestamp: float) -> None` | 源码未提供方法级文档字符串。 | [L3177](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3177) |
| `@staticmethod def _park_later_in_progress_tasks(current: dict[str, Any], task_states: dict[str, dict[str, Any]]) -> None` | 把 index 更大且仍 in_progress 的 stage 收成 pending。 | [L3198](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3198) |
| `@staticmethod def _update_task_state_on_start(task_state: dict[str, Any], timestamp: float) -> None` | 源码未提供方法级文档字符串。 | [L3217](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3217) |
| `@staticmethod def _update_task_state_on_complete(task_state: dict[str, Any], status: str, timestamp: float, duration_ms: int, error: Any \| None) -> None` | 源码未提供方法级文档字符串。 | [L3225](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3225) |
| `def _build_task_start_event(self, subplan: PlanNode, task_id: str, task_state: dict[str, Any], task_states: dict[str, dict[str, Any]], timestamp: float) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L3238](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3238) |
| `def _build_task_complete_event(self, data: TaskCompleteEventData) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L3262](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3262) |
| `def _prepare_root_node(self, plan_code: str) -> PlanNode` | 统一非流式/流式入口的 plan_code 加载流程，确保安全校验只维护一处。 | [L3282](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3282) |
| `def _load_plan_namespace(self, plan_code: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L3301](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3301) |
| `def _safe_import(self, name: str, globals_: dict[str, Any] \| None = None, locals_: dict[str, Any] \| None = None, fromlist: tuple[str, ...] = (), level: int = 0) -> Any` | 源码未提供方法级文档字符串。 | [L3318](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3318) |
| `def _build_namespace(self) -> dict[str, Any]` | 构建受限执行命名空间。 | [L3354](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3354) |
| `@staticmethod def _extract_root_node(namespace: dict[str, Any]) -> PlanNode` | 从命名空间提取根 PlanNode 实例。 | [L3375](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3375) |
| `def _bind_node_callbacks(self, root: PlanNode) -> None` | 源码未提供方法级文档字符串。 | [L3401](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3401) |
| `@staticmethod def _log_from_node(node: PlanNode, level: str, message: str, args: tuple[Any, ...]) -> None` | 输出节点受控日志。 | [L3418](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3418) |
| `def _ensure_skill_code_import_path(self) -> None` | 确保 skill_code 导入路径在 sys.path 中。 | [L3443](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3443) |
| `@staticmethod def _hash_code(code: str) -> str` | 计算代码哈希（用于追踪）。 | [L3455](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3455) |
| `async def _send_trace(self) -> None` | 发送执行追踪数据到 Evolver。 | [L3459](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3459) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve_skill_turbo_thinking_kwargs(thinking: str \| None, model_client: Any) -> dict[str, Any]` | Optional thinking inject for SkillTurbo LLM calls. | [L131](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L131) |
| `def is_skill_turbo_thinking_param_error(exc: BaseException) -> bool` | Heuristic: API/client rejected thinking-related call kwargs. | [L165](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L165) |
| `def _is_subplan_business_failure(result: Any) -> bool` | 子节点返回业务失败状态时，也应标记 task.complete 为 failed。 | [L339](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L339) |
| `def _parse_tool_name_from_call_id(tool_call_id: str) -> str \| None` | 从 ``skill_turbo-tc-{tool_name}-{args_hash}-{idx}`` 解析 tool_name。 | [L399](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L399) |
| `def _parse_call_idx_from_call_id(tool_call_id: Any) -> int \| None` | 从 ``skill_turbo-tc-{tool_name}-{args_hash}-{idx}`` 解析末段 idx。 | [L425](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L425) |

## `jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L1)

**模块职责：** SkillTurboFallbackHandler -- 节点级 fallback 的委托接口与 DeepAgent 实现。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L21) |

### [`class FallbackContractError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L24)

fallback subagent 未达成节点契约，需降级到 DeepAgent。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, node_name: str, reason: str, original_error: Exception \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L27) |

### [`class SkillTurboFallbackHandler(ABC)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L43)

节点级 fallback 委托接口。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@abstractmethod async def fallback(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None = None) -> dict[str, Any]` | 非流式 fallback：使用外部 agent 兜底失败节点。 | [L51](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L51) |
| `@abstractmethod def fallback_stream(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None = None) -> AsyncIterator[dict[str, Any]]` | 流式 fallback：使用外部 agent 兜底失败节点。 | [L62](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L62) |

### [`class DeepAgentFallbackHandler(SkillTurboFallbackHandler)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L73)

基于 DeepAgent spawn subagent 的 fallback 实现。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, adapter: Any, *, request_id: str = '', channel_id: str = '', session_id: str = '')` | 源码未提供方法级文档字符串。 | [L86](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L86) |
| `@staticmethod def _build_fallback_query(node_name: str, instruction: str, inputs: dict[str, Any], error: Exception) -> str` | 构造 fallback subagent 的任务 prompt。 | [L100](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L100) |
| `@staticmethod def _scan_balanced_json_objects(text: str) -> list[str]` | 对裸 JSON（无围栏）做括号平衡扫描，返回所有完整 {...} 子串。 | [L132](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L132) |
| `@staticmethod def _parse_fallback_output(fallback_output: Any) -> tuple[bool, dict[str, Any]]` | 解析 subagent 末尾的 JSON 契约声明。 | [L176](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L176) |
| `@staticmethod def _build_success_result(node_name: str, inputs: dict[str, Any], contract_result: dict[str, Any], error: Exception) -> dict[str, Any]` | 构建 fallback 成功后的结果 dict。 | [L243](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L243) |
| `@staticmethod def _log_degraded_result(node_name: str, fallback_output: Any, error: Exception) -> None` | 源码未提供方法级文档字符串。 | [L270](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L270) |
| `async def _execute_spawn_fallback(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None) -> str` | 通过 adapter 的 ``spawn_fallback`` 执行 SkillTurbo fallback，返回子代理输出文本。 | [L284](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L284) |
| `async def fallback(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None = None) -> dict[str, Any]` | 非流式 fallback 实现。 | [L296](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L296) |
| `def fallback_stream(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None = None) -> AsyncIterator[dict[str, Any]]` | 流式 fallback 实现。 | [L354](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L354) |
| `async def _fallback_stream_impl(self, node_name: str, instruction: str, inputs: dict[str, Any], error: Exception, parent_session: Session \| None) -> AsyncIterator[dict[str, Any]]` | 流式 fallback 的实际实现。 | [L365](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L365) |

## `jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L1)

**模块职责：** Helpers for the SkillTurbo guided-mode (interactive_ask) flag.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _raw_interactive_ask(source: Mapping[str, Any] \| None) -> Any` | 源码未提供函数级文档字符串。 | [L10](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L10) |
| `def extract_interactive_ask(*sources: Mapping[str, Any] \| None) -> bool` | Guided mode is explicit opt-in only. | [L16](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L16) |
| `def resolve_interactive_ask_from_inputs(inputs: dict[str, Any] \| None) -> bool` | Read guided-mode flag from SkillTurbo inputs / metadata. | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L28) |
| `def apply_interactive_ask_to_inputs(inputs: dict[str, Any] \| None, raw_interactive: Any) -> dict[str, Any]` | Copy inputs and stamp ``metadata.interactive_ask``. | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L39) |
| `def resolve_resume_interactive_ask(raw_interactive: Any, saved_inputs: Mapping[str, Any] \| None) -> bool` | 解析 resume 时的 interactive_ask 值，供 ``apply_interactive_ask_to_inputs`` 使用。 | [L56](../../../../../jiuwenswarm/server/runtime/skill_turbo/interactive_ask.py#L56) |

## `jiuwenswarm/server/runtime/skill_turbo/json_utils.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/json_utils.py#L1)

**模块职责：** JSON提取工具 -- 从LLM返回值中健壮地提取JSON。

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def extract_llm_json(raw: Union[str, dict, list], expected_type: type = dict) -> Any` | 从LLM返回值中健壮地提取JSON。 | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/json_utils.py#L12) |
| `def _extract_outermost_json(text: str, open_char: str, close_char: str) -> str \| None` | 使用括号计数法提取最外层完整的JSON结构。 | [L99](../../../../../jiuwenswarm/server/runtime/skill_turbo/json_utils.py#L99) |

## `jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L1)

**模块职责：** Protect user-visible SkillTurbo markdown from glued/unclosed fences.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_FENCE_INFO_CONTINUATION` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L17) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _looks_like_fence_line(line: str) -> bool` | 源码未提供函数级文档字符串。 | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L20) |
| `def _fence_info(line: str) -> str \| None` | Return the info string after a fence marker, or None if not a fence line. | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L25) |
| `def _is_fence_info_continuation(last_line: str, incoming: str) -> bool` | True when last_line is a marker-only fence and incoming is a language tag. | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L41) |
| `def terminate_dangling_markdown_fence(content: str) -> str` | Append a newline when content ends on a completed fence line. | [L54](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L54) |
| `def markdown_stream_incoming(previous: str, incoming: str) -> str` | Prefix incoming with a newline when concatenating would glue a fence. | [L71](../../../../../jiuwenswarm/server/runtime/skill_turbo/markdown_stream.py#L71) |

## `jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L1)

**模块职责：** SkillTurbo 节点产物持久化（跨请求复用）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L25) |
| `SKILL_TURBO_NODE_ARTIFACTS_KEY` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L28) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_session_id(session: Any) -> str` | 统一获取 session ID,与 executor._session_id 逻辑一致。 | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L31) |
| `async def save_node_artifacts(session: Any, *, skill: str, nodes: dict[str, dict[str, Any]], skip_post_run: bool = False) -> None` | 持久化节点产物记录到 session state。 | [L48](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L48) |
| `async def load_node_artifacts(session: Any) -> dict[str, Any] \| None` | 读取节点产物记录。返回 None 表示无可复用记录。 | [L121](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L121) |
| `async def clear_node_artifacts(session: Any) -> None` | 清除节点产物记录（任务最终成功后调用）。 | [L160](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L160) |

## `jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L1)

**模块职责：** SkillTurbo ↔ PermissionInterruptRail 胶水层（纯函数，无业务状态）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SKILL_TURBO_RESUME_CTX_KEY` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L33) |
| `SKILL_TURBO_ID_SUFFIX` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L38) |
| `logger` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L40) |

### [`class SkillTurboToolCall`](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L68)

SkillTurbo 内部用的轻量 tool_call 对象。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `id` | `str` | `—` | [L76](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L76) |
| `name` | `str` | `—` | [L77](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L77) |
| `arguments` | `dict[str, Any]` | `—` | [L78](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L78) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def set_skill_turbo_id(session: Any, card: Any) -> None` | 将 session 的 agent_id 设为 '{card.id}__skill_turbo'，使 SkillTurbo 的 checkpointer key 与 DeepAgent 隔离，避免 post_run 互相覆盖。 | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L43) |
| `def build_tool_ctx(*, session: Any, tool_name: str, tool_args: dict[str, Any], tool_call_id: str, resume_user_input: Any \| None = None) -> AgentCallbackContext` | 构造一个用于 ``before_tool_call`` / ``after_tool_call`` 的 ctx。 | [L81](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L81) |
| `def extract_tool_interrupt(exc: BaseException) -> ToolInterruptException \| None` | 沿 ``__cause__`` / ``cause`` 链抽出 ``ToolInterruptException``。 | [L118](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L118) |
| `def is_blocking_abort(exc: BaseException) -> bool` | 判断异常是否承载工具中断（HITL）语义。 | [L140](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L140) |
| `def build_interaction_output_from_abort(exc: BaseException) -> Any \| None` | 从 AbortError 构造 ``OutputSchema(type="__interaction__")``。 | [L147](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L147) |
| `def _get_sid(session: Any) -> str` | 获取 session ID，兼容 session_id 属性和 get_session_id() 方法。 | [L204](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L204) |
| `async def save_resume_ctx(session: Any, *, plan_code: str, inputs: dict[str, Any], pending_tool_call_id: str, task_states: list[dict[str, Any]] \| None = None) -> None` | 中断时保存断点上下文到 session state（checkpointer 持久化）。 | [L231](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L231) |
| `async def load_resume_ctx(session: Any) -> dict[str, Any] \| None` | 从 checkpointer 读取断点上下文。返回 None 表示无可恢复的 SkillTurbo 中断。 | [L287](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L287) |
| `async def clear_resume_ctx(session: Any) -> None` | 清除断点上下文（resume 跑通后调用）。 | [L317](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L317) |

## `jiuwenswarm/server/runtime/skill_turbo/plan_node.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L1)

**模块职责：** PlanNode 基类 -- 规划代码动态生成的递归执行节点。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L37) |
| `__all__` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L39) |

### [`class PlanNode(ABC)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L42)

规划节点 -- 递归结构，子类实现 async _execute，run 自带 fallback。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, plan_name: str, instruction: str, sub_plans: list[PlanNode] \| None = None, depth: int = 0)` | 源码未提供方法级文档字符串。 | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L45) |
| `def _update_subplans_depth(self) -> None` | 递归更新所有子节点的深度。 | [L90](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L90) |
| `def set_runtime_callbacks(self, *, has_tool: Callable[[str], bool] \| None = None, use_tool: Callable[..., Awaitable[Any]] \| None = None, call_llm: Callable[..., Awaitable[str]] \| None = None, stream_llm: Callable[..., AsyncIterator[str]] \| None = None, fallback: Callable[[PlanNode, dict[str, Any], Exception], Awaitable[Any]] \| None = None, fallback_stream: Callable[[PlanNode, dict[str, Any], Exception], AsyncIterator[Any]] \| None = None, extract_json: Callable[..., Any] \| None = None, log: Call…` | 源码未提供方法级文档字符串。 | [L102](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L102) |
| `def log(self, level: str, message: str, *args: Any) -> None` | 输出受控节点日志，供 plan_code 调试使用。 | [L150](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L150) |
| `def log_debug(self, message: str, *args: Any) -> None` | 源码未提供方法级文档字符串。 | [L156](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L156) |
| `def log_info(self, message: str, *args: Any) -> None` | 源码未提供方法级文档字符串。 | [L159](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L159) |
| `def log_warning(self, message: str, *args: Any) -> None` | 源码未提供方法级文档字符串。 | [L162](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L162) |
| `def log_error(self, message: str, *args: Any) -> None` | 源码未提供方法级文档字符串。 | [L165](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L165) |
| `def has_tool(self, tool_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L168](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L168) |
| `async def call_tool(self, tool_name: str, **kwargs: Any) -> Any` | 源码未提供方法级文档字符串。 | [L173](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L173) |
| `async def call_llm(self, prompt: str, system_prompt: str = '', node_name: str \| None = None, concurrent: bool = False, thinking: str \| None = None) -> str` | 调用 LLM（子类无需覆盖，由 Executor 注入回调）。 | [L178](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L178) |
| `async def stream_llm(self, prompt: str, system_prompt: str = '', node_name: str \| None = None, concurrent: bool = False, thinking: str \| None = None) -> AsyncIterator[str]` | 流式调用 LLM（子类无需覆盖，由 Executor 注入回调）。 | [L208](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L208) |
| `async def stream_llm_collect(self, prompt: str, system_prompt: str = '', node_name: str \| None = None, concurrent: bool = False, thinking: str \| None = None) -> str` | 流式调用 LLM 并收集完整文本（协程，可用于 asyncio.gather）。 | [L243](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L243) |
| `def extract_json(self, raw: Union[str, dict, list], expected_type: type = dict) -> Any` | 从LLM返回值中健壮地提取JSON。 | [L265](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L265) |
| `@abstractmethod async def _execute(self, inputs: dict[str, Any]) -> Any` | 非流式执行核心逻辑（子类必须实现）。 | [L297](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L297) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[Any]` | 流式执行核心逻辑（子类可选覆盖）。 | [L313](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L313) |
| `async def run(self, inputs: dict[str, Any]) -> Any` | 非流式执行入口 -- 固定模板方法，不可覆盖，自带 fallback。 | [L338](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L338) |
| `async def run_stream(self, inputs: dict[str, Any]) -> AsyncIterator[Any]` | 流式执行入口 -- 固定模板方法，自带流式 fallback。 | [L359](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L359) |
| `@staticmethod def _resume_skip_result(subplan: 'PlanNode') -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L379](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L379) |
| `async def should_skip_subplan(self, subplan: 'PlanNode', inputs: dict[str, Any]) -> bool` | HITL resume 重放时，编排层可查询是否应静默跳过该二层 stage。 | [L388](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L388) |
| `async def should_suppress_subplan_start_banner(self, subplan: 'PlanNode', inputs: dict[str, Any]) -> bool` | HITL resume 重放时，编排层可查询是否抑制「开始执行」进度横幅。 | [L398](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L398) |
| `async def _maybe_skip_subplan_execute(self, subplan: 'PlanNode', inputs: dict[str, Any]) -> dict[str, Any] \| None` | 若回调判定应跳过真实执行，触发 after 并返回 skip result；否则返回 None。 | [L408](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L408) |
| `async def execute_subplan(self, subplan: PlanNode, inputs: dict[str, Any]) -> Any` | 执行子节点，带有回调钩子。 | [L421](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L421) |
| `async def skip_subplan(self, subplan: PlanNode, inputs: dict[str, Any], *, message: str = '已跳过', extra: dict[str, Any] \| None = None) -> dict[str, Any]` | 跳过子节点执行，仍触发 task 回调并将其标记为 completed。 | [L458](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L458) |
| `async def skip_subplan_stream(self, subplan: PlanNode, inputs: dict[str, Any], *, message: str = '已跳过', extra: dict[str, Any] \| None = None) -> AsyncIterator[Any]` | 流式跳过子节点：yield 一条跳过结果并完成任务追踪。 | [L484](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L484) |
| `async def execute_subplan_stream(self, subplan: PlanNode, inputs: dict[str, Any]) -> AsyncIterator[Any]` | 流式执行子节点，带有回调钩子。 | [L510](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L510) |
| `def __repr__(self) -> str` | 源码未提供方法级文档字符串。 | [L564](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L564) |

### [`class DisableThinkingMixin`](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L568)

窄 seam：挂载节点的 call_llm / stream_llm 强制 thinking="off"。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def call_llm(self, prompt: str, system_prompt: str = '', node_name: str \| None = None, concurrent: bool = False, thinking: str \| None = None) -> str` | 源码未提供方法级文档字符串。 | [L576](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L576) |
| `async def stream_llm(self, prompt: str, system_prompt: str = '', node_name: str \| None = None, concurrent: bool = False, thinking: str \| None = None) -> AsyncIterator[str]` | 源码未提供方法级文档字符串。 | [L593](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L593) |

## `jiuwenswarm/server/runtime/skill_turbo/planner.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L1)

**模块职责：** SkillTurboPlanner -- 任务匹配 skill 并返回 plan_code。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L17) |
| `_SKILL_CODES_PACKAGE` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L19) |
| `_MIN_ROUTE_CONFIDENCE` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L20) |

### [`class PlanGenerationError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L23)

规划生成失败。

### [`class SkillTurboPlanner`](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L27)

根据任务匹配 skill，返回对应的预规划 plan_code。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, environment: SkillTurboEnvironment)` | 源码未提供方法级文档字符串。 | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L30) |
| `async def plan(self, task: str, context: dict[str, Any] \| None = None) -> str \| None` | 根据任务匹配 skill，返回 plan_code。 无匹配或 skill 无预规划代码时返回 None，由 SkillTurbo 降级处理。 | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L33) |
| `async def match_skill(self, task: str, context: dict[str, Any] \| None = None) -> Skill \| None` | 调用 LLM 将任务路由到已注册 skill。失败或无合适 skill 时返回 None。 | [L57](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L57) |
| `def build_plan_code(self, skill: Skill \| str) -> str \| None` | 根据 skill 对象或 skill 名查找入口并组装 plan_code。 | [L65](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L65) |
| `async def _match_skill(self, task: str, context: dict[str, Any] \| None = None) -> Skill \| None` | 调用 LLM 将任务路由到已注册 skill。失败或无合适 skill 时返回 None。 | [L80](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L80) |
| `@staticmethod def _build_route_messages(task: str, context: dict[str, Any] \| None, skills_payload: list[dict[str, Any]]) -> list[dict[str, str]]` | 源码未提供方法级文档字符串。 | [L149](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L149) |
| `@staticmethod def _parse_confidence(value: Any) -> float` | 源码未提供方法级文档字符串。 | [L210](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L210) |
| `def _build_skill_plan_code(self, skill: Skill) -> str \| None` | 查找 skill_code 入口并组装 executor 可执行的 plan_code。 | [L217](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L217) |
| `def _find_skill_root_file(self, skill_name: str) -> Path \| None` | 源码未提供方法级文档字符串。 | [L246](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L246) |
| `def _skill_codes_dir(self) -> Path` | 源码未提供方法级文档字符串。 | [L268](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L268) |
| `def _to_skill_root_module(self, skill_name: str, root_file: Path) -> str` | 源码未提供方法级文档字符串。 | [L277](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L277) |

## `jiuwenswarm/server/runtime/skill_turbo/rails/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/__init__.py#L1)

**模块职责：** Rails for SkillTurbo.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L9](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/__init__.py#L9) |

## `jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L1)

**模块职责：** Artifact detection rail for SkillTurbo tool calls.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L35) |
| `_LOG_PREFIX` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L37) |

### [`class SkillTurboArtifactRail`](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L40)

在 SkillTurbo 工具调用后检测产物文件并发射 artifact.generated 事件。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `priority` | `未显式标注` | `90` | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L50) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, executor: Any) -> None` | 源码未提供方法级文档字符串。 | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L52) |
| `async def before_tool_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L59) |
| `async def after_tool_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L69](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L69) |
| `@staticmethod def _get_session_id(session: Any) -> str` | 源码未提供方法级文档字符串。 | [L131](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L131) |
| `async def _emit_artifact_generated(self, session: Any, paths: list[str], session_id: str, tool_name: str, task_id: str \| None) -> bool` | 构建 artifact.generated payload 并写入 session stream。 | [L140](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/artifact_rail.py#L140) |

## `jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L1)

**模块职责：** SkillTurbo 定制的结构化 AskUserRail。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L30) |

### [`class SkillTurboAskUserRail(StructuredAskUserRail)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L33)

把用户作答按前端 answers 结构回填给 skill_code。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, language: Optional[str] = None)` | 源码未提供方法级文档字符串。 | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L36) |
| `async def resolve_interrupt(self, ctx: AgentCallbackContext, tool_call: Optional[Any], user_input: Optional[Any], auto_confirm_config: Optional[dict] = None) -> Any` | 源码未提供方法级文档字符串。 | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L39) |
| `@staticmethod def _first_outline_preview(questions: list[Any]) -> dict[str, Any] \| None` | 返回第一个带非空 text 的 preview。 | [L78](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L78) |
| `@staticmethod def _should_skip_for_non_interactive(tool_call: Optional[Any]) -> bool` | 非引导模式下，带 preview 的内容确认类 ask_user 跳过。 | [L93](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L93) |
| `@classmethod def _parse_user_answers(cls, user_input: Any) -> list[dict[str, Any]] \| None` | 把 resume 的 user_input 解析为 skill_code 期望的 answers 列表。 | [L123](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L123) |
| `@staticmethod def _normalize_items(items: list[Any]) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L145](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L145) |
| `@staticmethod def _dict_answers_to_items(mapping: dict[str, Any]) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L158](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/ask_user_rail.py#L158) |

## `jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L1)

**模块职责：** SkillTurbo 权限审批消息定制。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SKILL_TURBO_APPROVAL_DESCRIPTION` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L18) |
| `SKILL_TURBO_APPROVAL_TOOLS` | `list[tuple[str, str]]` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L23) |
| `__all__` | `未显式标注` | [L73](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L73) |

### [`class SkillTurboPermissionRail(PermissionInterruptRail)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L36)

PermissionInterruptRail 子类，定制 skill_acceleration_exec 审批消息。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_message(self, tool_call: Optional[Any], result: Any) -> str` | 源码未提供方法级文档字符串。 | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L43) |
| `def _build_skill_turbo_message(self, tool_call: Optional[Any], result: Any) -> str` | skill_turbo 外层统一审批消息：通用兜底描述 + 工具清单。 | [L53](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/permission_rail.py#L53) |

## `jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L1)

**模块职责：** SkillTurboPromptRail — 注入 skill_acceleration_exec 使用指南。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L22) |
| `_SECTION_NAME` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L24) |
| `__all__` | `未显式标注` | [L108](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L108) |

### [`class SkillTurboPromptRail(DeepAgentRail)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L51)

Inject skill_acceleration_exec usage guide before each model call.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `priority` | `未显式标注` | `8` | [L57](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L57) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L59) |
| `def init(self, agent: Any) -> None` | 源码未提供方法级文档字符串。 | [L63](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L63) |
| `def uninit(self, agent: Any) -> None` | 源码未提供方法级文档字符串。 | [L66](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L66) |
| `def _resolve_priority(self, name: str, default_priority: int) -> int` | 源码未提供方法级文档字符串。 | [L74](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L74) |
| `@staticmethod def _resolve_language() -> str` | 源码未提供方法级文档字符串。 | [L81](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L81) |
| `async def before_model_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L84](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L84) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_skill_turbo_guide_text(language: str) -> str` | 源码未提供函数级文档字符串。 | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L27) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1)

**模块职责：** 定义 ContentPlanError、P41NormalizeNode、P42QuickResearchNode、P43OutlineGenNode、P44ValidateNode、ContentPlanNode 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L14) |
| `_MATERIAL_RICHNESS` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L16) |
| `_VALID_SEARCH_MODES` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L17) |
| `_VALID_SOURCE_TYPES` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L18) |
| `_SOURCE_MATERIAL_MAX_CHARS` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L19) |
| `_SEARCH_RESULT_MAX_CHARS` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L20) |
| `_OUTLINE_NAME` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L21) |
| `_SEARCH_RESULTS_FOR_P43_MAX_CHARS` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L22) |
| `_PAGE_HEADING_PATTERN` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L23) |
| `_OUTLINE_FIELD_PATTERN` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L24) |
| `_P4_MAX_ATTEMPTS` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L28) |
| `_INSUFFICIENT_INFO_MARKER` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L29) |
| `_QUERY_BOUNDS_NO_MATERIAL` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L30) |
| `_QUERY_BOUNDS_WITH_MATERIAL` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L31) |
| `_RESEARCH_DIMENSIONS` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L32) |
| `_P41_SYSTEM_PROMPT` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L40) |
| `_P42A_SYSTEM_PROMPT` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L55) |
| `_P42A_RESPONSE_MAX_CHARS` | `未显式标注` | [L67](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L67) |
| `_P42_MAX_RETRIES` | `未显式标注` | [L68](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L68) |
| `_P42_RELEVANCE_SYSTEM_PROMPT` | `未显式标注` | [L72](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L72) |
| `_P43_COMMON_RULES` | `未显式标注` | [L88](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L88) |
| `_P43_TOPIC_SYSTEM_PROMPT` | `未显式标注` | [L109](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L109) |
| `_P43_OUTLINE_SYSTEM_PROMPT` | `未显式标注` | [L126](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L126) |
| `_P43_DESCRIPTION_SYSTEM_PROMPT` | `未显式标注` | [L138](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L138) |
| `_STRUCTURAL_PAGE_TYPES` | `未显式标注` | [L919](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L919) |
| `_URL_IN_TEXT_PATTERN` | `未显式标注` | [L1075](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1075) |
| `_OUTLINE_TITLE_LINE_PATTERN` | `未显式标注` | [L1076](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1076) |

### [`class ContentPlanError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L149)

P4 内容策划失败。

### [`class P41NormalizeNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1313)

P4.1 — 需求标准化与素材充裕度评估。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1316](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1316) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1354](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1354) |

### [`class P42QuickResearchNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1359)

P4.2 — 条件化快速调研：生成 query → 并行 web_search，搜索结果直接传递给 P4.3。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1362](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1362) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1406](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1406) |

### [`class P43OutlineGenNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1415)

P4.3 — 按 source_type 生成 outline.md。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1418](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1418) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1460](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1460) |

### [`class P44ValidateNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1465)

P4.4 — 产物校验：outline.md 结构完整；搜索模式下校验已搜索来源章节。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1468](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1468) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1508](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1508) |

### [`class ContentPlanNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1513)

P4 — 内容策划（P4.1 → P4.2 → P4.3 → P4.4）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1538](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1538) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1602](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1602) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _require_p4_prerequisites(inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L153](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L153) |
| `def _decide_p4_should_search(search_mode: str, material_richness: str) -> bool` | 按 outline-planner 素材充裕度 × search_mode 决策表计算是否执行 P4.2。 | [L179](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L179) |
| `def _p4_search_reason(search_mode: str, material_richness: str, should_search: bool) -> str` | 源码未提供函数级文档字符串。 | [L192](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L192) |
| `def _parse_p41_response(raw: str) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L204](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L204) |
| `def _build_p41_prompt(inputs: dict[str, Any], source_material: str) -> str` | 源码未提供函数级文档字符串。 | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L223) |
| `def _apply_p41_result(inputs: dict[str, Any], parsed: dict[str, str], source_material: str) -> None` | 源码未提供函数级文档字符串。 | [L246](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L246) |
| `async def _run_p41_normalize(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L260](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L260) |
| `def _query_count_bounds(has_source_material: bool) -> tuple[int, int]` | 源码未提供函数级文档字符串。 | [L280](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L280) |
| `def _normalize_tool_text(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L284](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L284) |
| `def _is_search_result_usable(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L301](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L301) |
| `def _truncate_text(text: str, max_chars: int) -> str` | 源码未提供函数级文档字符串。 | [L310](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L310) |
| `def _parse_p42a_queries(raw: str, *, has_source_material: bool) -> list[dict[str, str]]` | 源码未提供函数级文档字符串。 | [L316](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L316) |
| `def _build_p42a_prompt(inputs: dict[str, Any], source_material: str) -> str` | 源码未提供函数级文档字符串。 | [L353](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L353) |
| `async def _stream_llm_collect_bounded(node: PlanNode, prompt: str, *, system_prompt: str, max_chars: int, error_prefix: str = 'P4.2a') -> str` | 流式收集 LLM 可见 content；超限立即失败以中止非 JSON 长正文空转。 | [L378](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L378) |
| `def _format_search_results_for_p43(search_results: list[dict[str, str]]) -> str` | 将搜索结果格式化为 P4.3 prompt 中的文本。 | [L402](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L402) |
| `async def _run_parallel_web_searches(node: PlanNode, queries: list[dict[str, str]]) -> list[dict[str, str]]` | 源码未提供函数级文档字符串。 | [L419](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L419) |
| `def _extract_entity(raw: str) -> str` | 从 P4.2a 的 LLM 输出中提取主题核心实体名（无明确实体时返回空串）。 | [L438](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L438) |
| `def _entity_in_result_body(entity: str, result: str) -> bool` | 检查实体名是否出现在搜索结果正文（排除 Query 回显行）中。 | [L452](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L452) |
| `def _entity_in_results(entity: str, batches: list[dict[str, str]]) -> bool` | 规则预检：搜索结果中是否直接提及实体名（不区分大小写）。 | [L470](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L470) |
| `async def _assess_and_suggest_retry(node: PlanNode, topic: str, entity: str, usable_batches: list[dict[str, str]], failure_mode: str) -> tuple[str, str, list[dict[str, str]]]` | 判定搜索结果与主题的相关性，相关性不足时生成重搜 query。 | [L500](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L500) |
| `async def _run_p42_quick_research(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L582](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L582) |
| `def _p43_system_prompt(source_type: str) -> str` | 源码未提供函数级文档字符串。 | [L654](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L654) |
| `def _should_include_searched_sources(inputs: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L662](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L662) |
| `def _is_no_search_degraded(inputs: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L666](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L666) |
| `def _has_no_image_source(inputs: dict[str, Any]) -> bool` | 本流程不会有 image_map：无用户本地图，且未启用 AI 生图。 | [L674](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L674) |
| `def _build_structural_page_directive(inputs: dict[str, Any]) -> str` | 根据 structural_page_request 构建中间结构页指令，注入 P4.3 prompt。 | [L686](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L686) |
| `def _build_p43_prompt(inputs: dict[str, Any], source_material: str, search_results_text: str) -> str` | 源码未提供函数级文档字符串。 | [L741](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L741) |
| `def _strip_markdown_fence(text: str) -> str` | 源码未提供函数级文档字符串。 | [L824](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L824) |
| `def _validate_outline_markdown_basic(text: str, *, topic: str, page_count: Any, structural_page_request: str = 'none', structural_page_count: Any = None) -> None` | 源码未提供函数级文档字符串。 | [L832](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L832) |
| `def _validate_structural_pages(pages: list[tuple[int, str]], *, structural_page_request: str = 'none', structural_page_count: Any = None) -> None` | 校验中间结构页合法性，与 pptx-craft outline-planner Stage 3 对齐。 | [L922](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L922) |
| `def _split_outline_pages(text: str) -> list[tuple[int, str]]` | 源码未提供函数级文档字符串。 | [L985](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L985) |
| `def _extract_outline_field(block: str, field: str) -> str` | 源码未提供函数级文档字符串。 | [L997](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L997) |
| `def _is_research_required_page(block: str) -> bool` | 源码未提供函数级文档字符串。 | [L1004](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1004) |
| `def _is_placeholder_field_value(value: str) -> bool` | 源码未提供函数级文档字符串。 | [L1008](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1008) |
| `def _validate_outline_markdown_full(text: str, *, topic: str, page_count: Any, include_searched_sources: bool, structural_page_request: str = 'none', structural_page_count: Any = None) -> None` | 源码未提供函数级文档字符串。 | [L1019](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1019) |
| `def _outline_validate_kwargs(inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1057](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1057) |
| `def _outline_full_error(text: str, inputs: dict[str, Any]) -> str \| None` | 源码未提供函数级文档字符串。 | [L1067](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1067) |
| `def _fix_outline_title_line(text: str, topic: str) -> str` | 源码未提供函数级文档字符串。 | [L1079](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1079) |
| `def _build_searched_sources_section(inputs: dict[str, Any]) -> str \| None` | 源码未提供函数级文档字符串。 | [L1086](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1086) |
| `def _replace_outline_field_value(block: str, field: str, new_value: str) -> str` | 源码未提供函数级文档字符串。 | [L1118](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1118) |
| `def _fallback_queries_text(inputs: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L1126](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1126) |
| `def _fix_placeholder_research_fields(text: str, inputs: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L1140](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1140) |
| `def _normalize_outline_contract(text: str, inputs: dict[str, Any]) -> str` | 写盘/校验前确定性规范化：标题对齐 topic、补已搜索来源、回填占位字段。不编造事实。 | [L1168](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1168) |
| `def _resolve_outline_path(inputs: dict[str, Any]) -> Path` | 源码未提供函数级文档字符串。 | [L1181](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1181) |
| `async def _run_p44_validate(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L1192](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1192) |
| `async def _write_outline(node: PlanNode, output_dir: str \| Path, content: str) -> Path` | 源码未提供函数级文档字符串。 | [L1215](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1215) |
| `def _check_insufficient_info(outline_text: str, inputs: dict[str, Any]) -> None` | 检查 LLM 是否标记了信息不足，若是则 raise ContentPlanError 触发重试/fallback。 | [L1232](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1232) |
| `async def _run_p43_outline_gen(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L1255](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/content_plan.py#L1255) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1)

**模块职责：** 定义 _ResearchConfig、PrepareNode、PageWorkerNode、DeepResearchNode、_extract_fetch_result_items、count_named_citations 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L39) |
| `_MAX_SEARCH_ROUNDS` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L41) |
| `_MAX_BACKFILL_ROUNDS` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L42) |
| `_MIN_SOURCES_PER_PAGE` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L43) |
| `_MIN_KEY_FINDINGS` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L44) |
| `_MIN_DATA_POINTS` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L45) |
| `_MIN_DATA_TYPES` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L46) |
| `_MIN_TIMEPOINTS` | `未显式标注` | [L47](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L47) |
| `_MIN_COMPARE_OBJECTS` | `未显式标注` | [L48](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L48) |
| `_MIN_COMPARE_DIMS` | `未显式标注` | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L49) |
| `_MIN_NAMED_CITATIONS` | `未显式标注` | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L50) |
| `_WORD_COUNT_MAP` | `未显式标注` | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L52) |
| `_WORD_COUNT_NO_SEARCH_MAP` | `未显式标注` | [L53](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L53) |
| `_PAGE_HEADER_RE` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L55) |
| `_TITLE_FIELD_RE` | `未显式标注` | [L56](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L56) |
| `_DATA_NEED_RE` | `未显式标注` | [L57](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L57) |
| `_PAGE_TYPE_RE` | `未显式标注` | [L58](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L58) |
| `_RESEARCH_QUERY_HEADER_RE` | `未显式标注` | [L59](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L59) |
| `_LIST_ITEM_RE` | `未显式标注` | [L60](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L60) |
| `_NEXT_FIELD_RE` | `未显式标注` | [L61](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L61) |
| `_SEARCHED_SOURCES_RE` | `未显式标注` | [L62](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L62) |
| `_URL_RE` | `未显式标注` | [L63](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L63) |
| `_NAMED_CITATION_RE` | `未显式标注` | [L65](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L65) |
| `_HOST_FROM_URL_RE` | `未显式标注` | [L66](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L66) |

### [`class _ResearchConfig`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L127)

封装撰写所需配置参数，避免函数签名过长。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `search_mode` | `str` | `—` | [L129](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L129) |
| `research_depth` | `str` | `—` | [L130](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L130) |
| `topic` | `str` | `—` | [L131](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L131) |
| `no_data_fallback` | `bool` | `False` | [L132](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L132) |

### [`class PrepareNode(DisableThinkingMixin, PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L135)

P6.0 — 全局预处理：解析 outline、判定搜索策略、素材覆盖度评估、计算每页最低字数。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L138](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L138) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L180](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L180) |
| `async def _read_file(self, path: str) -> str` | 源码未提供方法级文档字符串。 | [L240](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L240) |
| `async def _parse_outline_pages(self, outline_text: str) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L255](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L255) |
| `def _parse_outline_pages_fallback(self, outline_text: str) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L295](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L295) |
| `@staticmethod def _extract_multi_line_list(section: str, field_name: str) -> list[str]` | 提取 **字段名**： 后的多行 - 列表项或单行值。 | [L326](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L326) |
| `def _extract_searched_urls(self, outline_text: str) -> list[str]` | 源码未提供方法级文档字符串。 | [L346](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L346) |
| `async def _should_search(self, search_mode: str, source_material: str, pages: list[dict[str, Any]]) -> bool` | 源码未提供方法级文档字符串。 | [L353](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L353) |
| `async def _evaluate_material_sufficiency(self, source_material: str, pages: list[dict[str, Any]]) -> bool` | 源码未提供方法级文档字符串。 | [L369](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L369) |
| `async def _evaluate_page_coverage(self, pages: list[dict[str, Any]], source_material: str) -> dict[str, dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L412](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L412) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L467](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L467) |

### [`class PageWorkerNode(DisableThinkingMixin, PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L478)

P6.1 — per-page 并发闭环：搜索→评分→补搜→抓取→ghost→校验→回溯→撰写→按页校验→失败重写，N 页并发。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L481](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L481) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L653](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L653) |
| `async def _run_validate_research(self, output_dir: str, pptx_root: str, outline_path: str, research_depth: str) -> bool` | 调 cli validate-research 全量门禁，校验所有页面研究质量。 | [L732](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L732) |
| `async def _run_page_pipeline(self, *, page: dict[str, Any], coverage_info: dict[str, Any], searched_urls: list[str], source_material: str, config: _ResearchConfig, min_words_per_page: int, need_search: bool) -> dict[str, Any]` | 单页闭环：搜索→评分→补搜→抓取→ghost→校验→回溯→撰写→按页校验→失败重写。 | [L770](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L770) |
| `async def _search_for_page(self, page: dict[str, Any], coverage_info: dict[str, Any], searched_urls: list[str]) -> list[dict[str, Any]]` | 单页搜索：生成查询→并行搜索→评分筛选→缺口补搜。 | [L827](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L827) |
| `def _build_page_queries(self, page: dict[str, Any], coverage_info: dict[str, Any]) -> list[str]` | 按覆盖度生成搜索查询。 | [L877](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L877) |
| `def _parse_search_results(self, raw: str) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L912](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L912) |
| `async def _score_sources_for_page(self, page: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]` | 单页来源评分筛选：A+/A/A-/B+/B 保留，C 级丢弃。 | [L922](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L922) |
| `async def _backfill_search_for_page(self, page: dict[str, Any], existing_sources: list[dict[str, Any]]) -> list[dict[str, Any]]` | 单页缺口补搜（最多1轮）。 | [L1012](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1012) |
| `async def _fetch_for_page(self, page: dict[str, Any], page_sources: list[dict[str, Any]], research_depth: str) -> list[dict[str, Any]]` | 单页抓取校验：批量抓取→ghost识别→数据校验→定向回溯。 | [L1050](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1050) |
| `async def _batch_fetch_single(self, page: dict[str, Any], page_sources: list[dict[str, Any]], research_depth: str, extra_urls: list[str] \| None = None) -> list[dict[str, Any]]` | 单页批量抓取。 | [L1080](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1080) |
| `async def _identify_ghost_single(self, page: dict[str, Any], extractions: list[dict[str, Any]]) -> list[dict[str, Any]]` | 单页幽灵来源识别。 | [L1135](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1135) |
| `async def _validate_page_sufficiency(self, page: dict[str, Any], extractions: list[dict[str, Any]], research_depth: str, strict: bool = False) -> tuple[bool, list[str]]` | 单页数据充分性校验，返回 (is_gap, missing_items)。 | [L1190](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1190) |
| `def _compose_validation_content(self, extractions: list[dict[str, Any]], max_chars: int = 2500) -> str` | 合并抓取内容，优先保留结构化表格段落，按字符估算截断到约 2500 字（≈3000 token）。 | [L1278](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1278) |
| `async def _backfill_fetch_single(self, page: dict[str, Any], page_sources: list[dict[str, Any]], extractions: list[dict[str, Any]], missing: list[str], research_depth: str) -> list[dict[str, Any]]` | 单页定向回溯：补抓候选池剩余URL + 定向搜索。 | [L1341](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1341) |
| `def _build_targeted_queries(self, page: dict[str, Any], missing: list[str]) -> list[str]` | 源码未提供方法级文档字符串。 | [L1405](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1405) |
| `async def _write_single_page(self, page: dict[str, Any], extractions: list[dict[str, Any]], source_material: str, config: _ResearchConfig, min_words_per_page: int) -> str` | 撰写单页研究报告，返回以 `### P{N}:` 开头的该页 Markdown 片段。 | [L1433](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1433) |
| `def _build_fallback_page_section(self, page: dict[str, Any]) -> str` | 单页撰写失败时的兜底骨架。 | [L1513](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1513) |
| `def _build_no_data_page_section(self, page: dict[str, Any], topic: str, search_mode: str, research_depth: str) -> str` | 无研究数据降级模式：生成单页 stub（不含全局 header）。 | [L1528](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1528) |
| `def _finalize_page_section(self, page: dict[str, Any], section: str, extractions: list[dict[str, Any]] \| None, min_words_per_page: int) -> tuple[str, bool]` | 结构校验 + 规则补引用。返回 (section, structural_ok)；仅结构崩触发重写。 | [L1568](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1568) |
| `async def _write_file(self, path: str, content: str) -> bool` | 源码未提供方法级文档字符串。 | [L1603](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1603) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L1618](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1618) |

### [`class DeepResearchNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1630)

P6 — 深度研究根节点：编排预处理 + per-page 并发闭环。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1633](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1633) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1692](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1692) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L1718](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L1718) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _extract_fetch_result_items(result: Any) -> list[dict[str, Any]]` | Normalize a fetch_webpage tool result into a list of per-URL item dicts. | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L12) |
| `def count_named_citations(section: str) -> int` | Count unique non-numeric ``[source]`` labels in a research section. | [L69](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L69) |
| `def source_label_from_url(url: str) -> str` | Derive a short label from an existing URL host (no invention). | [L80](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L80) |
| `def enrich_section_citations(section: str, extractions: list[dict[str, Any]] \| None, *, min_citations: int = _MIN_NAMED_CITATIONS) -> str` | Append real extraction hosts as ``[label]`` until min_citations if needed. | [L91](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/deep_research.py#L91) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L1)

**模块职责：** 定义 DeliveryNode、_looks_like_path。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L14) |
| `_DEFAULT_STRUCTURAL_PAGES` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L16) |
| `_SEND_FAIL_MARKERS` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L18) |

### [`class DeliveryNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L31)

P10 — 交付与验收（对应 SKILL Stage 9）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L34](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L34) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L72](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L72) |
| `async def _send_file(self, pptx_path: str) -> str` | 源码未提供方法级文档字符串。 | [L157](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L157) |
| `@staticmethod def _is_send_failure(text: str) -> bool` | 源码未提供方法级文档字符串。 | [L184](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L184) |
| `async def _check_pages(self, pages_dir: str, page_count: int) -> bool` | 源码未提供方法级文档字符串。 | [L194](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L194) |
| `def _parse_listing(self, result: Any) -> list[str]` | 源码未提供方法级文档字符串。 | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L223) |
| `@staticmethod def _extract_path_from_item(item: Any) -> str` | 源码未提供方法级文档字符串。 | [L274](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L274) |
| `@staticmethod def _basename(path: str) -> str` | 源码未提供方法级文档字符串。 | [L288](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L288) |
| `@staticmethod def _build_summary(status: str, pptx_filename: str, page_count: int, pages_dir: str, send_file_status: str) -> str` | 源码未提供方法级文档字符串。 | [L293](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L293) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L313](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L313) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _looks_like_path(value: str) -> bool` | 源码未提供函数级文档字符串。 | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/delivery.py#L27) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L1)

**模块职责：** 定义 DocumentParseError、DocumentParseNode、_normalize_tool_text、_is_tool_error_text、_extract_vqa_ocr_section、_is_image_path 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_collect_user_text` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L10) |
| `_DOC_RAW_NAME` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L12) |
| `_MAX_PARSE_ATTEMPTS` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L13) |
| `_DOC_EXCERPT_MAX_CHARS` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L14) |
| `_PDF_BATCH_SIZE` | `未显式标注` | [L15](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L15) |
| `_PDF_MAX_AUTO_PARSE_PAGES` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L16) |
| `_PDF_TRUNCATION_MARKER` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L17) |
| `_IMAGE_EXTENSIONS` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L23) |
| `_PDF_EXTENSIONS` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L24) |
| `_IMAGE_OCR_QUESTION` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L25) |
| `_TOPIC_LLM_SYSTEM_PROMPT` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L30) |

### [`class DocumentParseError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L42)

P3 文档解析失败。

### [`class DocumentParseNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L143)

P3 — 条件文档解析：读附件原文写入 doc_raw.md（下游按路径读取，不传 doc_content）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L162](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L162) |
| `async def _read_text_file(self, path: Path) -> str` | 源码未提供方法级文档字符串。 | [L210](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L210) |
| `async def _read_pdf_page_batch(self, path: Path, start: int, end: int) -> str \| None` | Read one PDF page batch. Returns None when the range is past the document end. | [L221](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L221) |
| `async def _read_large_pdf_file(self, path: Path) -> str` | 源码未提供方法级文档字符串。 | [L250](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L250) |
| `async def _read_image_file(self, path: Path) -> str` | 源码未提供方法级文档字符串。 | [L278](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L278) |
| `async def _read_single_document(self, path: Path) -> tuple[str, str \| None]` | 源码未提供方法级文档字符串。 | [L298](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L298) |
| `async def _build_doc_raw(self, doc_paths: list[str]) -> tuple[str, int, list[str]]` | 源码未提供方法级文档字符串。 | [L317](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L317) |
| `async def _parse_with_retry(self, inputs: dict[str, Any], doc_paths: list[str], doc_raw_path: Path) -> tuple[bool, str \| None]` | 源码未提供方法级文档字符串。 | [L338](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L338) |
| `async def _infer_topic_from_doc(self, inputs: dict[str, Any], doc_raw_path: Path) -> str` | 源码未提供方法级文档字符串。 | [L384](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L384) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L405](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L405) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L454](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L454) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_tool_text(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L46](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L46) |
| `def _is_tool_error_text(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L83](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L83) |
| `def _extract_vqa_ocr_section(text: str) -> str` | 源码未提供函数级文档字符串。 | [L87](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L87) |
| `def _is_image_path(path: str \| Path) -> bool` | 源码未提供函数级文档字符串。 | [L100](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L100) |
| `def _is_pdf_path(path: str \| Path) -> bool` | 源码未提供函数级文档字符串。 | [L104](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L104) |
| `def _merge_doc_raw_sections(parts: list[tuple[str, str]]) -> str` | 源码未提供函数级文档字符串。 | [L108](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L108) |
| `def _user_has_topic(inputs: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L117](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L117) |
| `def _build_topic_inference_prompt(user_text: str, doc_excerpt: str) -> str` | 源码未提供函数级文档字符串。 | [L122](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L122) |
| `def _parse_topic_from_llm_response(raw: str) -> str` | 源码未提供函数级文档字符串。 | [L132](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/document_parse.py#L132) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L1)

**模块职责：** 定义 ImagePrepareNode、_build_step0_prompt、_build_a2_prompt、_build_a3_prompt、_build_ai_prompt、_extract_tool_text 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L17) |
| `_MAX_RETRIES` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L19) |
| `_INTERMEDIATE_FILES` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L20) |
| `_VQA_QUESTION` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L27) |
| `_STEP0_SYSTEM` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L31) |
| `_A2_SYSTEM` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L33) |
| `_A3_SYSTEM` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L35) |

### [`class ImagePrepareNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L154)

P6.5 图片准备节点（Diana）：按 image_sources 级联分配图片，产出 image_map.json。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L157](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L157) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L194](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L194) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L255](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L255) |
| `async def _step0_page_needs(self, output_dir: str, outline_path: str, research_paths: Any) -> bool` | 源码未提供方法级文档字符串。 | [L268](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L268) |
| `async def _collect_research(self, research_paths: Any) -> str` | 源码未提供方法级文档字符串。 | [L299](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L299) |
| `async def _step_a_local(self, output_dir: str, image_paths: list[str], topic: str = '') -> None` | 源码未提供方法级文档字符串。 | [L318](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L318) |
| `async def _describe_images(self, image_paths: list[str]) -> list[dict]` | 源码未提供方法级文档字符串。 | [L342](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L342) |
| `async def _extract_entities(self, images: list[dict], topic: str = '') -> list[dict]` | 源码未提供方法级文档字符串。 | [L374](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L374) |
| `async def _match_images(self, output_dir: str, page_info: str, local_info: dict) -> None` | 源码未提供方法级文档字符串。 | [L395](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L395) |
| `async def _ai_source(self, output_dir: str, pptx_root: str, image_sources: list, topic: str, style_id: str) -> None` | 源码未提供方法级文档字符串。 | [L418](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L418) |
| `async def _read_temp_map(self, output_dir: str) -> dict` | 源码未提供方法级文档字符串。 | [L531](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L531) |
| `async def _step_d_finalize(self, output_dir: str, pptx_root: str, total_pages: int) -> bool` | 源码未提供方法级文档字符串。 | [L544](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L544) |
| `def _validate(self, output_dir: str) -> bool` | 源码未提供方法级文档字符串。 | [L571](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L571) |
| `@staticmethod async def _read_imagegen_status(node: 'ImagePrepareNode', output_dir: str) -> bool` | 读 P2 写的 imagegen_status.json，返回 supported 字段。 | [L576](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L576) |
| `async def _cleanup(self, output_dir: str) -> None` | 源码未提供方法级文档字符串。 | [L591](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L591) |
| `async def _write_json(self, file_path: str, data: Any) -> None` | 源码未提供方法级文档字符串。 | [L603](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L603) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_step0_prompt(outline: str, research: str) -> str` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L38) |
| `def _build_a2_prompt(images: list[dict], topic: str = '') -> str` | 源码未提供函数级文档字符串。 | [L58](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L58) |
| `def _build_a3_prompt(page_info: str, local_info: str) -> str` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L76) |
| `def _build_ai_prompt(keywords: list, topic: str, style_id: str, usage: str) -> str` | 源码未提供函数级文档字符串。 | [L94](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L94) |
| `def _extract_tool_text(result: Any, keys: tuple[str, ...]) -> str` | 从 VQA/OCR 工具返回值中提取文本，兼容 dict/str/object 格式。 | [L102](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L102) |
| `def _extract_vqa_answer(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L132](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L132) |
| `def _extract_ocr_text(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L136](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L136) |
| `def _parse_image_paths(text: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L143](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/image_prepare.py#L143) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L1)

**模块职责：** 定义 IntentClassifyError、IntentClassifyNode、_normalize_doc_path、_dedupe_paths、_looks_like_document_path、_is_image_path 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_collect_user_text` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L12) |
| `_DOC_EXTENSIONS` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L14) |
| `_IMAGE_EXTENSIONS` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L22) |
| `_LLM_PATH_ONLY_SYSTEM_PROMPT` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L30) |
| `_LLM_PATH_AND_SLOTS_SYSTEM_PROMPT` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L41) |
| `_FILE_PATH_KEYS` | `未显式标注` | [L133](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L133) |
| `_SLOT_NAMES` | `未显式标注` | [L184](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L184) |
| `_SPEAKER_NOTES_KEYWORDS` | `未显式标注` | [L208](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L208) |
| `_EDIT_EXISTING_KEYWORDS` | `未显式标注` | [L214](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L214) |

### [`class IntentClassifyError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L203)

P1 意图识别失败。

### [`class IntentClassifyNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L328)

P1 — workflow 内文档门控（结构化附件 + LLM 文本解析 + 无附件时槽位预提取）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L348](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L348) |
| `async def _extract_paths_only_with_llm(self, text: str) -> list[str]` | 场景 A：有附件，LLM 仅从 query 提取额外文件路径。 | [L390](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L390) |
| `async def _extract_paths_and_slots_with_llm(self, text: str) -> tuple[list[str], dict[str, Any]]` | 场景 B/C：无附件，LLM 提取路径 +（没路径时）提取槽位。 | [L400](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L400) |
| `async def _collect_doc_paths(self, inputs: dict[str, Any]) -> tuple[list[str], dict[str, Any]]` | 返回 (doc_paths, slots)。slots 仅在无附件且无路径时非空。 | [L418](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L418) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L447](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L447) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L478](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L478) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_doc_path(raw: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L86](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L86) |
| `def _dedupe_paths(paths: list[str]) -> list[str]` | 源码未提供函数级文档字符串。 | [L97](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L97) |
| `def _looks_like_document_path(path: str) -> bool` | 源码未提供函数级文档字符串。 | [L112](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L112) |
| `def _is_image_path(path: str) -> bool` | 源码未提供函数级文档字符串。 | [L117](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L117) |
| `def _split_image_paths(paths: list[str]) -> tuple[list[str], list[str]]` | 将路径列表分流为 (doc_paths, image_paths)。图片不进 doc_paths，避免触发 P3 Eve 解析。 | [L121](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L121) |
| `def _flatten_file_entries(raw: Any) -> list[Any]` | 将 attachments / files 各类容器展平为路径字符串或文件对象列表。 | [L136](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L136) |
| `def _collect_paths_from_file_entries(raw: Any) -> list[str]` | 源码未提供函数级文档字符串。 | [L161](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L161) |
| `def _collect_attachment_paths(inputs: dict[str, Any]) -> list[str]` | 源码未提供函数级文档字符串。 | [L175](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L175) |
| `def _collect_files_paths(inputs: dict[str, Any]) -> list[str]` | 读取 interface 传入的 files（含 OfficeClaw files.uploaded 格式）。 | [L179](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L179) |
| `def _build_llm_path_prompt(text: str) -> str` | 源码未提供函数级文档字符串。 | [L187](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L187) |
| `def _build_llm_path_and_slots_prompt(text: str) -> str` | 源码未提供函数级文档字符串。 | [L194](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L194) |
| `def _detect_speaker_notes_request(text: str) -> bool` | 检测用户是否要求生成演讲备注。 | [L221](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L221) |
| `def _detect_edit_existing_request(text: str, doc_paths: list[str]) -> bool` | 检测用户是否要编辑已有 PPT（而非从零生成）。 | [L229](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L229) |
| `def _parse_slots_from_llm_response(raw: str) -> dict[str, Any]` | 从 LLM 响应中提取 slots 字段，返回 {slot_name: value_or_empty}。 | [L244](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L244) |
| `def _parse_paths_from_llm_response(raw: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L285](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/intent_classify.py#L285) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L1)

**模块职责：** 定义 OutlineReviewNode。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L10) |
| `_OUTLINE_CONFIRM_ID` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L12) |
| `_OUTLINE_USE_EDITED_ID` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L13) |

### [`class OutlineReviewNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L16)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L17) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L47](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L47) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L97](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L97) |
| `async def _read_outline(self, outline_path: str) -> str` | 源码未提供方法级文档字符串。 | [L121](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L121) |
| `async def _write_outline(self, outline_path: str, content: str) -> None` | 源码未提供方法级文档字符串。 | [L136](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L136) |
| `async def _ask_user_review(self, outline_text: str, outline_path: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L147](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L147) |
| `def _parse_user_action(self, ask_result: dict[str, Any]) -> str` | 源码未提供方法级文档字符串。 | [L174](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L174) |
| `def _extract_edited_text(self, ask_result: dict[str, Any], original: str) -> str` | 源码未提供方法级文档字符串。 | [L186](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L186) |
| `def _extract_user_nl_input(self, ask_result: dict[str, Any]) -> str` | 源码未提供方法级文档字符串。 | [L198](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L198) |
| `async def _llm_revise_outline(self, original_outline: str, user_instruction: str) -> str` | 源码未提供方法级文档字符串。 | [L208](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/outline_review.py#L208) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L1)

**模块职责：** 定义 PipelineInitError、P01EnvDepsNode、P02WorkspaceInitNode、PipelineInitNode、_resolve_pptx_root、_ensure_package_json 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L12) |
| `_PLAYWRIGHT_INSTALL_TIMEOUT` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L24) |
| `_NPM_INSTALL_MARKERS` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L26) |
| `_PLAYWRIGHT_INSTALL_MARKERS` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L33) |
| `_DEFAULT_SKILL_NAME` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L41) |
| `_NPM_DEPS` | `未显式标注` | [L89](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L89) |
| `_PACKAGE_JSON_CONTENT` | `未显式标注` | [L97](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L97) |

### [`class PipelineInitError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L44)

P0 流水线初始化失败。

### [`class P01EnvDepsNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L258)

P0.1 — check-env、npm install、playwright install。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L270](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L270) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L305](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L305) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L348](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L348) |

### [`class P02WorkspaceInitNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L363)

P0.2 — 解析 pptx_root、创建 output_dir / pages_dir、初始化会话变量。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L379](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L379) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L417](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L417) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L453](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L453) |

### [`class PipelineInitNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L468)

P0 — 流水线启动 / 环境预置（P0.1 → P0.2）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L481](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L481) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L526](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L526) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L532](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L532) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_pptx_root(inputs: dict[str, Any]) -> str` | 解析 pptx_root，只使用已注册的新版外部 skill 目录。 | [L48](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L48) |
| `async def _ensure_package_json(node: PlanNode, pptx_root: str) -> None` | 源码未提供函数级文档字符串。 | [L104](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L104) |
| `def _node_modules_ready(pptx_root: str) -> bool` | 源码未提供函数级文档字符串。 | [L118](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L118) |
| `async def _bash(node: PlanNode, command: str, *, timeout_seconds: int = 300, required: bool = True, workdir: str \| None = None) -> _BashResult` | 源码未提供函数级文档字符串。 | [L128](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L128) |
| `def _needs_npm_install(check_output: str) -> bool` | 源码未提供函数级文档字符串。 | [L148](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L148) |
| `def _needs_playwright_install(check_output: str) -> bool` | 源码未提供函数级文档字符串。 | [L154](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L154) |
| `def _parse_cli_path(output: str) -> str` | 源码未提供函数级文档字符串。 | [L162](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L162) |
| `def _normalize_ppt_session_parent(path: Path) -> Path` | PPT 时间戳目录的父路径：智能体工作空间本身，不再套一层 workspace。 | [L176](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L176) |
| `def _default_ppt_session_parent() -> Path` | 最后兜底：相对 cwd 的 workspace，经 normalize 避免套一层 workspace。 | [L196](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L196) |
| `def _resolve_explicit_output_dir(inputs: dict[str, Any]) -> str \| None` | 上游显式指定的最终产物目录（完整路径，不再追加时间戳）。 | [L206](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L206) |
| `def _resolve_timestamp_parent_dir(inputs: dict[str, Any]) -> str` | generate-timestamp-dir 的父目录。 | [L217](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L217) |
| `def _resolve_workspace_base(inputs: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L248](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/pipeline_init.py#L248) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L1)

**模块职责：** 定义 PptCommon、_strip_line_numbers。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_JSON_FENCE_PATTERN` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L10) |
| `_CAT_N_PREFIX_RE` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L11) |
| `_OUTLINE_PAGE_HEADING_RE` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L12) |
| `NODE_DISPLAY_NAMES` | `dict[str, str]` | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L18) |

### [`class PptCommon`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L41)

PPT skill_codes 公共工具：流水线 inputs 解析与 LLM JSON 提取。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `TEXT_SOURCE_KEYS` | `未显式标注` | `('task', 'user_request', 'user_message', 'query')` | [L44](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L44) |
| `QUERY_PREFIXES` | `未显式标注` | `('你收到一条消息：\n', 'You receive a new message:\n')` | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L45) |
| `JSON_FENCE_PATTERN` | `未显式标注` | `_JSON_FENCE_PATTERN` | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L49) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def extract_plain_user_text(cls, raw: str) -> str` | 从 build_user_prompt 包装或裸文本中提取用户原文 content。 | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L52) |
| `@classmethod def collect_user_text(cls, inputs: dict[str, Any]) -> str` | 合并 task / user_request / user_message / query 中的用户可见原文。 | [L86](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L86) |
| `@classmethod def parse_json_payload(cls, raw: str) -> Any` | 解析 LLM 返回的 JSON（支持 markdown fence 与正文中的 JSON 对象）。 | [L105](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L105) |
| `@classmethod def parse_tool_file_content(cls, result: Any) -> str` | 从 read_file / write_file 工具返回值中提取文本内容，并去掉 cat -n 行号前缀。 | [L127](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L127) |
| `@classmethod async def read_file(cls, node: Any, file_path: str \| Path \| None, *, max_chars: int \| None = None, required: bool = False, label: str = 'file', error_type: type[Exception] = RuntimeError) -> str` | 源码未提供方法级文档字符串。 | [L156](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L156) |
| `@classmethod async def write_file(cls, node: Any, file_path: str \| Path, content: str, *, label: str = 'file', error_type: type[Exception] = RuntimeError) -> Path` | 源码未提供方法级文档字符串。 | [L193](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L193) |
| `@staticmethod def resolve_total_pages(*, page_count: int = 0, total_pages: int \| None = None, outline_text: str = '', outline_pages: dict[int, str] \| None = None, default_structural_pages: int = 2) -> int` | 从 outline 页码、上下文 total_pages 与 page_count 兜底推算总页数。 | [L219](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L219) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _strip_line_numbers(text: str) -> str` | 源码未提供函数级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_common.py#L37) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L1)

**模块职责：** 定义 ExportPaths、PPTExportNode、_sanitize_filename。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L17) |
| `_ILLEGAL_FILENAME_RE` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L20) |
| `_WINDOWS_RESERVED` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L21) |
| `_CONVERT_MAX_ATTEMPTS` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L55) |

### [`class ExportPaths`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L45)

导出路径相关参数封装。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `output_dir` | `str` | `—` | [L48](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L48) |
| `pages_dir` | `str` | `—` | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L49) |
| `pptx_path` | `str` | `—` | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L50) |
| `pptx_filename` | `str` | `—` | [L51](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L51) |
| `pptx_root` | `str` | `—` | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L52) |

### [`class PPTExportNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L58)

P9 — PPTX 导出（对应 SKILL Stage 8）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L61](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L61) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L110](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L110) |
| `async def _execute_template_finalizer(self, inputs: dict[str, Any], paths: ExportPaths) -> dict[str, Any]` | 模板包分支：执行 template-finalizer 终检导出流程。 | [L169](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L169) |
| `async def _attempt_convert(self, pages_dir: str, pptx_path: str, pptx_root: str) -> tuple[str, str \| None]` | 执行一次 convert，返回 (export_status, error_detail)。 | [L330](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L330) |
| `async def _run_convert(self, pages_dir: str, pptx_path: str, pptx_root: str) -> str` | 源码未提供方法级文档字符串。 | [L354](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L354) |
| `async def _validate_pptx(self, pptx_path: str, pptx_root: str) -> str` | 源码未提供方法级文档字符串。 | [L389](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L389) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L421](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L421) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _sanitize_filename(topic: str, *, max_length: int = 50) -> str` | 源码未提供函数级文档字符串。 | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_export.py#L30) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L1)

**模块职责：** 定义 PPTGenRootNode、_document_parse_failed、_document_parse_failure_result、_merge_subplan_result、_result_status、_append_subplan_step。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L25) |
| `_P3_SKIP_MESSAGE` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L27) |
| `_P3_SKIP_FIELDS` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L28) |
| `_MERGE_SKIP_KEYS` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L55) |
| `root` | `未显式标注` | [L406](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L406) |

### [`class PPTGenRootNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L96)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `display_names` | `dict[str, str]` | `NODE_DISPLAY_NAMES` | [L98](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L98) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L100](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L100) |
| `async def _run_subplan(self, subplan: PlanNode, inputs: dict[str, Any], results: list[dict[str, Any]]) -> None` | 源码未提供方法级文档字符串。 | [L134](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L134) |
| `async def _skip_p3_subplan(self, inputs: dict[str, Any], results: list[dict[str, Any]]) -> None` | 源码未提供方法级文档字符串。 | [L144](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L144) |
| `async def _run_p3_and_p2(self, inputs: dict[str, Any], results: list[dict[str, Any]]) -> None` | 源码未提供方法级文档字符串。 | [L164](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L164) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 非流式执行 PPT 生成全流程，并把共享上下文透传给所有子节点。 | [L173](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L173) |
| `async def _silent_resume_skip_subplan_stream(self, subplan: PlanNode, inputs: dict[str, Any], results: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]` | Resume 重放时已 completed 的 stage：不广播进度，仍走 subplan 回调链。 | [L198](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L198) |
| `async def _run_subplan_stream(self, subplan: PlanNode, inputs: dict[str, Any], results: list[dict[str, Any]], *, index: int, total_steps: int) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L236](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L236) |
| `async def _skip_p3_subplan_stream(self, inputs: dict[str, Any], results: list[dict[str, Any]], *, index: int, total_steps: int) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L278](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L278) |
| `async def _run_p3_and_p2_stream(self, inputs: dict[str, Any], results: list[dict[str, Any]], *, total_steps: int) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L324](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L324) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 流式执行 PPT 生成全流程，逐个透传子节点进度与结果。 | [L350](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L350) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _document_parse_failed(inputs: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L36) |
| `def _document_parse_failure_result(node: str, inputs: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L40) |
| `def _merge_subplan_result(inputs: dict[str, Any], result: Any) -> None` | 源码未提供函数级文档字符串。 | [L67](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L67) |
| `def _result_status(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L76) |
| `def _append_subplan_step(results: list[dict[str, Any]], subplan: PlanNode, result: Any) -> None` | 源码未提供函数级文档字符串。 | [L82](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L82) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1)

**模块职责：** 定义 _PageNumberPolicy、_ConstrainedCardState、_ChartLabelPlacement、PageGenContext、PrepareNode、PageWorkerNode 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L25) |
| `_CHART_CANDIDATE_TYPES` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L28) |
| `_CHART_CANDIDATE_SEMANTIC_SIGNALS` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L30) |
| `_P82_READ_TIMEOUT_SECONDS` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L39) |
| `_P82_FIX_ONE_TIMEOUT_SECONDS` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L40) |
| `_P8_1_POSTPROCESS_CONCURRENCY` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L45) |
| `_postprocess_sem` | `asyncio.Semaphore \| None` | [L47](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L47) |
| `_CONTENT_FILL_DENSITY_CHECKLIST` | `未显式标注` | [L173](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L173) |
| `_PRESET_STYLE_IDS` | `未显式标注` | [L182](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L182) |
| `_AGENDA_TEMPLATE_FILL_STYLE_IDS` | `未显式标注` | [L184](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L184) |
| `_STRUCTURAL_TEMPLATE_PAGE_TYPES` | `dict[str, str]` | [L185](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L185) |
| `_DEFAULT_GEN_RETRY_ROUND` | `未显式标注` | [L195](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L195) |
| `_MAX_PAGE_GENERATION_ATTEMPTS` | `未显式标注` | [L196](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L196) |
| `_UNFILLED_PLACEHOLDER_RE` | `未显式标注` | [L197](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L197) |
| `_HTML_COMMENT_RE` | `未显式标注` | [L198](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L198) |
| `_CSS_COMMENT_RE` | `未显式标注` | [L199](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L199) |
| `_CONTENT_PAGE_TYPES` | `未显式标注` | [L200](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L200) |
| `_PLACEHOLDER_SLOP_VALUES` | `未显式标注` | [L208](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L208) |
| `_MAIN_OPEN_TAG_RE` | `未显式标注` | [L220](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L220) |
| `_MAIN_CLOSE_TAG_RE` | `未显式标注` | [L221](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L221) |
| `_HEAD_BLOCK_RE` | `未显式标注` | [L222](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L222) |
| `_TITLE_TAG_RE` | `未显式标注` | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L223) |
| `_H1_INNER_TEXT_RE` | `未显式标注` | [L224](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L224) |
| `_CONTENT_SAFE_OPEN_RE` | `未显式标注` | [L228](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L228) |
| `_FOOTER_BLOCK_RE` | `未显式标注` | [L231](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L231) |
| `_P_INNER_TEXT_RE` | `未显式标注` | [L235](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L235) |
| `_PLACEHOLDER_STYLE_BLOCK_RE` | `未显式标注` | [L488](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L488) |
| `_PLACEHOLDER_ATTR_RE` | `未显式标注` | [L492](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L492) |
| `_PLACEHOLDER_TAG_RE` | `未显式标注` | [L497](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L497) |
| `_HEAD_HTML_COMMENT_RE` | `未显式标注` | [L502](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L502) |
| `_LOCAL_ASSET_LINK_RE` | `未显式标注` | [L504](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L504) |
| `_HEAD_URL_ATTR_RE` | `未显式标注` | [L557](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L557) |
| `_LOCAL_ASSET_URL_PREFIX` | `未显式标注` | [L561](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L561) |
| `_AGENDA_ITEM_NUM_RE` | `未显式标注` | [L577](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L577) |
| `_OUTLINE_RESEARCH_REQ_RE` | `未显式标注` | [L579](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L579) |
| `_REPAIRABLE_CONTENT_TEMPLATE_REASONS` | `未显式标注` | [L757](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L757) |
| `_VISIBLE_PAGE_NUMBER_RULE` | `未显式标注` | [L1369](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1369) |
| `_EDITABLE_LAYERING_RULES` | `未显式标注` | [L1377](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1377) |
| `_PAGE_NUMBER_NEGATIVE_RE` | `未显式标注` | [L1407](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1407) |
| `_PAGE_NUMBER_POSITIVE_RE` | `未显式标注` | [L1414](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1414) |
| `_PAGE_TYPE_TO_TEMPLATE` | `dict[str, str]` | [L1511](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1511) |
| `_TEMPLATE_STRUCTURAL_TYPES` | `未显式标注` | [L1527](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1527) |
| `_DEFAULT_STRUCTURAL_PAGES` | `未显式标注` | [L1536](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1536) |
| `_CDN_HEAD_SNIPPET` | `未显式标注` | [L1538](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1538) |
| `_DESIGN_RULES_DIGEST` | `未显式标注` | [L1559](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1559) |
| `_HTML_SKELETON` | `未显式标注` | [L1692](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1692) |
| `_AUDIENCE_VISIBLE_TEXT_RULES` | `未显式标注` | [L1715](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1715) |
| `_STRUCTURAL_DESIGN_RULES` | `未显式标注` | [L1729](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1729) |
| `_STRUCTURAL_HTML_SKELETON` | `未显式标注` | [L1758](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1758) |
| `_PAGE_TYPE_RE` | `未显式标注` | [L1783](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1783) |
| `_PAGE_LAYOUT_TEMPLATES` | `未显式标注` | [L1785](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1785) |
| `_STRUCTURAL_DENSITY_CHECKLIST` | `未显式标注` | [L1888](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1888) |
| `_DENSITY_CHECKLIST_DIGEST` | `未显式标注` | [L1897](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1897) |
| `_PPT_SLIDE_DIV_RE` | `未显式标注` | [L1958](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1958) |
| `_MALFORMED_HTML_RE` | `未显式标注` | [L1983](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1983) |
| `_PPT_SLIDE_OPEN_RE` | `未显式标注` | [L1987](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1987) |
| `_CHART_DIV_RE` | `未显式标注` | [L2085](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2085) |
| `_FLEX_COL_DIV_RE` | `未显式标注` | [L2089](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2089) |
| `_CHART_WRAPPER_HEIGHT_RE` | `未显式标注` | [L2093](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2093) |
| `_FLEX_GROW_RE` | `未显式标注` | [L2097](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2097) |
| `_CHART_SCAFFOLD_GET_ELEMENT_RE` | `未显式标注` | [L2204](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2204) |
| `_CHART_SCAFFOLD_NULL_OPTION_RE` | `未显式标注` | [L2207](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2207) |
| `_CHART_SCAFFOLD_OPTION_ASSIGN_RE` | `未显式标注` | [L2208](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2208) |
| `_JS_BLOCK_COMMENT_RE` | `未显式标注` | [L2209](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2209) |
| `_JS_LINE_COMMENT_RE` | `未显式标注` | [L2210](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2210) |
| `_SCRIPT_BODY_RE` | `未显式标注` | [L2238](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2238) |
| `_VISIBLE_PAGE_MARKER_RE` | `未显式标注` | [L2307](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2307) |
| `_VISIBLE_TEXT_LEAF_RE` | `未显式标注` | [L2316](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2316) |
| `_MAIN_BLOCK_RE` | `未显式标注` | [L2321](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2321) |
| `_PAGE_MARKER_SPACE_ENTITY_RE` | `未显式标注` | [L2322](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2322) |
| `_PAGE_MARKER_SLASH_ENTITY_RE` | `未显式标注` | [L2326](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2326) |
| `_DIV_TAG_RE` | `未显式标注` | [L2366](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2366) |
| `_PAGE_NUMBER_POSITION_CSS` | `未显式标注` | [L2367](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2367) |
| `_PAGE_NUMBER_STYLE` | `未显式标注` | [L2373](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2373) |
| `_PLACEHOLDER_HEADING_RE` | `未显式标注` | [L2454](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2454) |
| `_ECHARTS_INIT_NO_SVG_RE` | `未显式标注` | [L2509](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2509) |
| `_STATIC_SVG_BLOCK_RE` | `未显式标注` | [L2535](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2535) |
| `_SVG_CONTENT_TAGS` | `未显式标注` | [L2540](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2540) |
| `_ECHARTS_LIB_RE` | `未显式标注` | [L2560](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2560) |
| `_ECHARTS_INIT_RE` | `未显式标注` | [L2561](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2561) |
| `_CHART_HEADER_SPAN_RE` | `未显式标注` | [L2580](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2580) |
| `_CHART_UNIT_LABEL_RE` | `未显式标注` | [L2584](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2584) |
| `_CHART_UNIT_TOKEN_RE` | `未显式标注` | [L2589](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2589) |
| `_CHART_HEADER_WINDOW` | `未显式标注` | [L2594](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2594) |
| `_GRID_USAGE_RE` | `未显式标注` | [L2636](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2636) |
| `_OVERFLOW_HIDDEN_RE` | `未显式标注` | [L2645](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2645) |
| `_FONT_SIZE_RE` | `未显式标注` | [L2665](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2665) |
| `_LIST_BLOCK_RE` | `未显式标注` | [L2684](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2684) |
| `_CLASS_ATTR_RE` | `未显式标注` | [L2688](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2688) |
| `_LIST_ITEM_RE` | `未显式标注` | [L2692](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2692) |
| `_NON_TEXT_VISUAL_RE` | `未显式标注` | [L2693](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2693) |
| `_HTML_TAG_RE` | `未显式标注` | [L2697](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2697) |
| `_HTML_TAG_TOKEN_RE` | `未显式标注` | [L2739](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2739) |
| `_HTML_CLASS_RE` | `未显式标注` | [L2743](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2743) |
| `_HTML_VOID_TAGS` | `未显式标注` | [L2747](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2747) |
| `_HTML_STYLE_RE` | `未显式标注` | [L2824](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2824) |
| `_DECORATION_ROLE_RE` | `未显式标注` | [L2828](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2828) |
| `_DECORATION_CLASS_RE` | `未显式标注` | [L2832](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2832) |
| `_NEGATIVE_EDGE_STYLE_RE` | `未显式标注` | [L2836](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2836) |
| `_NEGATIVE_EDGE_CLASS_RE` | `未显式标注` | [L2841](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2841) |
| `_STYLE_BLOCK_RE` | `未显式标注` | [L2845](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2845) |
| `_CSS_RULE_RE` | `未显式标注` | [L2846](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2846) |
| `_UNSUPPORTED_RASTER_BG_RE` | `未显式标注` | [L2906](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2906) |
| `_EMPTY_DIV_RE` | `未显式标注` | [L2910](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2910) |
| `_OPEN_DIV_TAG_RE` | `未显式标注` | [L2911](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2911) |
| `_JS_DELIMITER_PAIRS` | `未显式标注` | [L2984](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2984) |
| `_SET_OPTION_RE` | `未显式标注` | [L2985](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2985) |
| `_SERIES_ARRAY_RE` | `未显式标注` | [L2986](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2986) |
| `_LABEL_OBJECT_RE` | `未显式标注` | [L2987](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2987) |
| `_SERIES_TYPE_RE` | `未显式标注` | [L2988](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2988) |
| `_LABEL_SHOW_RE` | `未显式标注` | [L2989](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2989) |
| `_LABEL_POSITION_RE` | `未显式标注` | [L2990](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2990) |
| `_LABEL_OFFSET_RE` | `未显式标注` | [L2994](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2994) |
| `_LABEL_DISTANCE_RE` | `未显式标注` | [L2995](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2995) |
| `_LABEL_FONT_SIZE_RE` | `未显式标注` | [L2999](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2999) |
| `_LABEL_LINE_HEIGHT_RE` | `未显式标注` | [L3003](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3003) |
| `_NON_PRIMARY_Y_AXIS_RE` | `未显式标注` | [L3007](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3007) |
| `_NUMBER_LITERAL` | `未显式标注` | [L3008](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3008) |
| `_Y_AXIS_INDEX_RE` | `未显式标注` | [L3009](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3009) |
| `_CHART_LABEL_REFERENCE_PLOT_HEIGHT_PX` | `未显式标注` | [L3010](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3010) |
| `_CHART_LABEL_MIN_GAP_PX` | `未显式标注` | [L3011](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3011) |
| `_CHART_TOP_LANE_MIN_GAP_PX` | `未显式标注` | [L3012](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3012) |
| `_SEARCH_NEEDED_ITEMS` | `未显式标注` | [L3428](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3428) |
| `_SEARCH_QUERY_TEMPLATES` | `dict[str, list[str]]` | [L3430](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3430) |
| `_REWRITE_ACTIONS` | `未显式标注` | [L3447](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3447) |
| `_CHART_CANDIDATE_CHROME_REWRITE_REASONS` | `未显式标注` | [L3553](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3553) |
| `_CHART_CANDIDATE_REWRITE_ACTIONS` | `dict[str, str]` | [L3559](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3559) |
| `_CHECK_LAYOUT_DENSITY` | `未显式标注` | [L3645](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3645) |
| `_ACTIVATE_TEMPLATE_CHART_TIMEOUT_SECONDS` | `未显式标注` | [L3646](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3646) |
| `_CHECK_LAYOUT_HARD_TAGS` | `未显式标注` | [L3647](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3647) |
| `_CHECK_LAYOUT_SOFT_WARNING_RE` | `未显式标注` | [L3655](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3655) |
| `_CHECK_LAYOUT_PAGE_REF_RE` | `未显式标注` | [L3656](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3656) |
| `_COMMENTED_CHART_SCAFFOLD_BLOCK_RE` | `未显式标注` | [L3678](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3678) |
| `_CANONICAL_ACTIVE_CHART_SCAFFOLD_RE` | `未显式标注` | [L3682](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3682) |
| `_PAGE_HEADING_RE` | `未显式标注` | [L4079](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4079) |
| `_IMAGE_LAYOUT_TEMPLATES` | `dict[int, str]` | [L4114](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4114) |
| `_IMAGE_LAYOUT_TEMPLATE_MANY` | `未显式标注` | [L4160](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4160) |

### [`class _PageNumberPolicy`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1398)

用户显式可见页码要求；默认关闭。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `enabled` | `bool` | `—` | [L1401](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1401) |
| `position` | `str` | `'bottom-right'` | [L1402](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1402) |
| `format_kind` | `str` | `'fraction'` | [L1403](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1403) |
| `zero_pad` | `bool` | `False` | [L1404](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1404) |

### [`class _ConstrainedCardState`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2731)

高度受限 Flex 卡片的解析状态。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `tag` | `str` | `—` | [L2734](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2734) |
| `depth` | `int` | `—` | [L2735](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2735) |
| `has_fixed_table` | `bool` | `False` | [L2736](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2736) |

### [`class _ChartLabelPlacement`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3016)

ECharts 数据标签的垂直几何参数。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `position` | `str` | `—` | [L3019](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3019) |
| `offset_x` | `float` | `—` | [L3020](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3020) |
| `offset_y` | `float` | `—` | [L3021](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3021) |
| `distance` | `float` | `—` | [L3022](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3022) |
| `font_size` | `float` | `—` | [L3023](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3023) |
| `line_height` | `float` | `—` | [L3024](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3024) |

### [`class PageGenContext`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4421)

单页生成使用的只读上下文。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `page_num` | `int` | `—` | [L4424](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4424) |
| `style_id` | `str` | `—` | [L4425](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4425) |
| `style_text` | `str` | `—` | [L4426](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4426) |
| `outline_page` | `str` | `—` | [L4427](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4427) |
| `research_page` | `str` | `—` | [L4428](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4428) |
| `outline_is_full` | `bool` | `—` | [L4429](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4429) |
| `image_map_page` | `str` | `—` | [L4430](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4430) |
| `designer_md_text` | `str` | `—` | [L4431](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4431) |
| `user_query` | `str` | `''` | [L4432](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4432) |
| `total_pages` | `int` | `0` | [L4433](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4433) |
| `pptx_root` | `str` | `''` | [L4434](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4434) |
| `outline_full` | `str` | `''` | [L4435](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4435) |

### [`class PrepareNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4619)

P8.0 — 读取素材并按页拆分，产出共享只读数据供 per-page worker 复用。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L4622](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4622) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L4656](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4656) |
| `async def _read_file(self, path: str) -> str` | 源码未提供方法级文档字符串。 | [L4748](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4748) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L4764](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4764) |

### [`class PageWorkerNode(DisableThinkingMixin, PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4775)

P8.1 — 按新版 pptx-craft 规则并发生成并校验每页 HTML。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L4778](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4778) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L4824](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4824) |
| `async def _apply_check_layout_pass(self, *, pages_dir: str, pptx_root: str, successful_pages: list[int], outline_pages: dict[int, str], research_pages: dict[int, str], outline_full: str, style_id: str, style_text: str, image_map: dict[str, Any], designer_md_text: str, user_query: str, total_pages: int) -> dict[str, Any]` | P8.1 末尾：内容页+agenda 做 check-layout，至多一轮再填槽+复检。 | [L4954](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4954) |
| `async def _run_page_pipeline(self, *, page_num: int, pages_dir: str, style_id: str, style_text: str, outline_page: str, research_page: str, outline_is_full: bool, gen_retry_round: int, image_map: dict[str, Any], designer_md_text: str = '', user_query: str = '', total_pages: int = 0, pptx_root: str = '', outline_full: str = '') -> dict[str, Any]` | 生成并校验单页；仅生成失败时按预算重试。 | [L5125](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5125) |
| `async def _read_file(self, path: str) -> str` | 源码未提供方法级文档字符串。 | [L5249](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5249) |
| `async def _generate_structural_template_fill(self, ctx: PageGenContext, page_type: str) -> str` | 预设/custom 结构页：官方模板预铺 + 仅填 {{}}。 | [L5264](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5264) |
| `async def _generate_content_template_fill(self, ctx: PageGenContext, *, rewrite_hint: str = '') -> tuple[str, str, str]` | 四预设 ∪ custom 内容页：官方 content-template 预铺填槽。 | [L5336](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5336) |
| `async def _generate_one(self, ctx: PageGenContext, *, rewrite_hint: str = '', original_html: str = '') -> tuple[str, str, str]` | 生成单页 HTML，返回 (校验通过的 html 或空串, 最后一次产物 html 或空串, 失败 reason 或空串)。 | [L5400](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5400) |
| `async def _write_file(self, path: str, content: str) -> bool` | 源码未提供方法级文档字符串。 | [L5446](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5446) |
| `async def _delete_page_file(self, path: str) -> None` | 删除落盘 HTML，防止 convert 整目录扫入坏页（skill_code 禁 direct unlink）。 | [L5461](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5461) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L5477](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5477) |

### [`class QAFixNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5492)

P8.2 — 按新版 pptx-craft Stage 6 做完整性检查与官方 fix。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L5495](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5495) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L5526](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5526) |
| `async def _read_page_file(self, path: str) -> str` | 源码未提供方法级文档字符串。 | [L5597](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5597) |
| `async def _write_page_file(self, path: str, content: str) -> bool` | 源码未提供方法级文档字符串。 | [L5619](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5619) |
| `async def _find_latest_backup_path(self, pages_dir: str, page_num: int) -> str` | 源码未提供方法级文档字符串。 | [L5631](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5631) |
| `async def _fix_pages(self, page_nums: list[int], *, pages_dir: str, pptx_root: str, style_file_path: str) -> list[tuple[int, bool, str] \| BaseException]` | 仅对指定页面并发执行新版 pptx-craft fix。 | [L5659](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5659) |
| `async def _check_completeness(self, pages_dir: str, page_count: int) -> tuple[bool, list[str]]` | 源码未提供方法级文档字符串。 | [L5743](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5743) |
| `def _parse_listing(self, result: Any) -> list[str]` | 源码未提供方法级文档字符串。 | [L5798](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5798) |
| `@staticmethod def _extract_path_from_item(item: Any) -> str` | 源码未提供方法级文档字符串。 | [L5859](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5859) |
| `def _parse_listing_text(self, text: str) -> list[str]` | 源码未提供方法级文档字符串。 | [L5872](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5872) |
| `@staticmethod def _basename(path: str) -> str` | 源码未提供方法级文档字符串。 | [L5876](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5876) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L5880](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5880) |

### [`class PPTPageGenNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5901)

P8 — 幻灯片生成根节点。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L5904](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5904) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L5959](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5959) |
| `async def _execute_template_pack(self, inputs: dict[str, Any], page_count: int, total_pages: int) -> dict[str, Any]` | 模板包分支：调用 template-filler 脚本 + LLM 填充生成页面。 | [L6098](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6098) |
| `async def _read_file(self, path: str) -> str` | 读取文件内容（PPTPageGenNode 自身用，模板分支）。 | [L6328](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6328) |
| `async def _write_file(self, path: str, content: str) -> bool` | 写入文件内容（PPTPageGenNode 自身用，模板分支）。 | [L6345](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6345) |
| `async def _load_template_manifest(self, pack_dir: str, pptx_root: str) -> dict[str, Any]` | 读取模板包的 template-manifest.json。 | [L6361](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6361) |
| `@staticmethod def _select_template_id(page_type: str, manifest: dict[str, Any], outline_page: str = '', research_page: str = '') -> str` | 根据页面类型 + 内容形状选择模板 ID（容量感知）。 | [L6380](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6380) |
| `async def _template_fill_one(self, *, page_num: int, pack_dir: str, pages_dir: str, pptx_root: str, outline_page: str, output_dir: str, manifest: dict[str, Any], force_template_id: str = '') -> bool` | 单页模板填充：seed → LLM 填充 → write_file。返回是否成功。 | [L6516](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6516) |
| `async def _llm_fill_template(self, *, page_num: int, seed_html: str, outline_page: str, research_page: str, is_structural: bool) -> str` | 调用 LLM 填充模板 HTML 中的 data-slot 占位文字。 | [L6594](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6594) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L6664](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L6664) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _get_postprocess_sem() -> asyncio.Semaphore` | 懒初始化后处理 Semaphore，避免 import 时绑定错误 event loop。 | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L50) |
| `async def _run_postprocess(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any` | 在线程池执行同步后处理，并用 Semaphore 限制同时 in-flight 的后处理路数。 | [L58](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L58) |
| `def _extract_designer_section(text: str, *, include_charts: bool = False, for_content_template_fill: bool = False) -> str` | 从新版 references/designer.md 提取当前生成链路需要的关键章节。 | [L65](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L65) |
| `def _normalize_template_whitespace(text: str) -> str` | 源码未提供函数级文档字符串。 | [L238](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L238) |
| `def _outline_needs_research(outline_page: str) -> bool` | 源码未提供函数级文档字符串。 | [L242](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L242) |
| `def _uses_content_template_fill(style_id: str, page_type: str, outline_page: str) -> bool` | 四预设 ∪ custom 内容页：官方 content-template 预铺后仅填槽。 | [L250](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L250) |
| `def _uses_structural_template_fill(style_id: str, page_type: str) -> bool` | 普通分支下结构页是否走官方模板预铺填槽。 | [L259](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L259) |
| `def _resolve_style_page_template_path(pptx_root: str, style_id: str, *, page_type: str = 'agenda') -> str` | 解析 references/styles/{style_id}/{page_type}-template.html 绝对路径字符串。 | [L267](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L267) |
| `def _has_unfilled_placeholders(html: str) -> bool` | 检测是否残留 Stage 6 软门禁关心的 {{PLACEHOLDER}}。 | [L278](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L278) |
| `def _build_structural_template_fill_prompt(*, page_number: int, page_type: str, template_page_type: str, style_id: str, style_text: str, outline_page: str, outline_full: str, seed_html: str, user_query: str = '') -> str` | 构造结构页官方模板填槽 prompt（仅替换 {{}}，不重写骨架）。 | [L290](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L290) |
| `def _build_agenda_template_fill_prompt(*, page_number: int, style_id: str, style_text: str, outline_page: str, outline_full: str, seed_html: str, user_query: str = '') -> str` | 构造 agenda 官方模板填槽 prompt（仅替换 {{}}，不重写骨架）。 | [L435](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L435) |
| `def _extract_main_open_tag(html: str) -> str` | 源码未提供函数级文档字符串。 | [L459](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L459) |
| `def _extract_main_inner_html(html: str) -> str` | 源码未提供函数级文档字符串。 | [L464](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L464) |
| `def _normalize_h1_text_only(html: str) -> str` | 源码未提供函数级文档字符串。 | [L474](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L474) |
| `def _normalize_title_tag_text_only(html: str) -> str` | 源码未提供函数级文档字符串。 | [L478](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L478) |
| `def _extract_head_block(html: str) -> str` | 源码未提供函数级文档字符串。 | [L482](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L482) |
| `def _head_chrome_signature(html: str) -> str` | head chrome 签名：归一化空白 + title 内文占位化后的 head 全文。 | [L510](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L510) |
| `def _structural_chrome_matches_seed(seed_html: str, filled_html: str) -> bool` | 结构页填槽后 head chrome 必须与 seed 模板逐字一致（title 文字除外）。 | [L542](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L542) |
| `def _extract_head_url_fingerprint(html: str) -> frozenset[str]` | head 内共享 CDN URL 集合（剔除 __LOCAL_ASSET__ 本地替换与 title）。 | [L564](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L564) |
| `def _count_outline_content_chapters(outline_text: str) -> int` | 从大纲中统计内容页数（研究需求为 ✅ 的页面）。 | [L582](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L582) |
| `def _find_agenda_page_num(outline_text: str) -> int` | 从大纲中找到 agenda 页的页码，未找到返回 0。 | [L594](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L594) |
| `def _count_agenda_items(html: str) -> int` | 从 agenda 页 HTML 中统计条目数（按编号 01-09 去重计数）。 | [L604](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L604) |
| `def _validate_agenda_item_count(outline_text: str, page_htmls: list[dict[str, Any]]) -> list[int]` | 校验 agenda 页条目数与大纲内容章节数是否一致。 | [L609](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L609) |
| `def _vote_head_fingerprints(pages: list[dict[str, Any]]) -> list[int]` | 跨页 head 指纹投票：返回偏离多数派的页码列表。 | [L638](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L638) |
| `def _extract_header_block(html: str) -> str` | 源码未提供函数级文档字符串。 | [L699](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L699) |
| `def _extract_footer_block(html: str) -> str` | 源码未提供函数级文档字符串。 | [L707](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L707) |
| `def _normalize_footer_text_only(html: str) -> str` | 源码未提供函数级文档字符串。 | [L715](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L715) |
| `def _has_placeholder_slop(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L722](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L722) |
| `def _plain_text_fragment(html_fragment: str) -> str` | 源码未提供函数级文档字符串。 | [L727](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L727) |
| `def _extract_filled_title_inner(filled_html: str) -> str` | 源码未提供函数级文档字符串。 | [L731](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L731) |
| `def _extract_filled_footer_inner(filled_html: str) -> str` | 源码未提供函数级文档字符串。 | [L739](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L739) |
| `def _replace_main_inner_html(html: str, new_inner: str) -> str` | 源码未提供函数级文档字符串。 | [L747](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L747) |
| `def _is_chart_candidate_page(page_type: str, *, outline_page: str = '', research_page: str = '') -> bool` | pptx-craft designer L869：默认四类 + outline/research 语义扩展；结构页永不升格。 | [L766](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L766) |
| `def _html_chart_scaffold_script_region(html_no_comments: str) -> str` | 路径 B 扫描区：与 content-template 物理布局一致（</main> 后、</body> 前）。 | [L782](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L782) |
| `def _filled_chart_scaffold_is_progressed(filled_html: str) -> bool` | filled 中 scaffold 已填 option 或已暴露为活跃 script（非纯 dormant）。 | [L792](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L792) |
| `def _extract_chart_scaffold_region(filled_html: str) -> str \| None` | 从 filled 提取可合并的 scaffold 区域（注释内已填 option 或已激活 script）。 | [L809](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L809) |
| `def _merge_chart_scaffold_from_filled(seed_html: str, filled_html: str) -> str` | chrome repair 时保留 filled 对 scaffold 的激活/填 option 进度，避免回滚 seed dormant。 | [L824](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L824) |
| `def _repair_content_template_chrome(seed_html: str, filled_html: str) -> str \| None` | Restore Page Chrome from seed; keep filled title/content/footer slot values. | [L849](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L849) |
| `def _repair_structural_page_chrome(seed_html: str, filled_html: str) -> str \| None` | 结构页 chrome 修复：从 seed 恢复 head，保留 filled 的 body 内容。 | [L911](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L911) |
| `def _validate_content_template_fill_output(seed_html: str, filled_html: str) -> tuple[bool, str]` | Stage 6 软门禁：内容页须基于 seed 填槽；head/header/footer 骨架不可改。 | [L975](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L975) |
| `def _validate_custom_content_template_fill_output(seed_html: str, filled_html: str) -> tuple[bool, str]` | custom 内容页填槽轻量校验（对齐结构页；不做 head 全等 / chrome repair）。 | [L1033](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1033) |
| `def _build_content_layout_template(page_type: str) -> str` | 源码未提供函数级文档字符串。 | [L1059](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1059) |
| `def _build_content_template_fill_prompt(*, page_number: int, style_id: str, style_text: str, outline_page: str, research_page: str, outline_full: str, seed_html: str, image_map_page: str = '', designer_md_text: str = '', user_query: str = '', total_pages: int = 0, rewrite_hint: str = '') -> str` | 内容页 content-template 预铺填槽 prompt（四预设三槽；custom 含 THEME_*）。 | [L1075](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1075) |
| `def _build_content_template_fill_system_prompt(*, style_id: str, page_type: str, outline_page: str = '', research_page: str = '') -> str` | Stage 6 填槽 system prompt；图表候选页显式豁免 CHART_SCAFFOLD 出 Chrome 锁。 | [L1320](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1320) |
| `def _resolve_page_number_policy(user_query: str) -> _PageNumberPolicy` | 仅把明确的可见页码请求识别为开启；“生成 N 页”不触发。 | [L1424](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1424) |
| `def _format_visible_page_number(policy: _PageNumberPolicy, page_number: int, total_pages: int) -> str` | 源码未提供函数级文档字符串。 | [L1464](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1464) |
| `def _build_visible_page_number_rule(user_query: str, page_number: int, total_pages: int) -> str` | 源码未提供函数级文档字符串。 | [L1486](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1486) |
| `def _looks_like_path(value: str) -> bool` | 源码未提供函数级文档字符串。 | [L1532](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1532) |
| `def _detect_page_type(outline_page: str) -> str` | 源码未提供函数级文档字符串。 | [L1879](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1879) |
| `def _strip_html_fence(text: str) -> str` | 剥掉 LLM 偶尔加的 ```html ... ``` 包裹。 | [L1942](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1942) |
| `def _is_valid_html(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L1964](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1964) |
| `def _ppt_slide_bounds(html: str) -> tuple[int, int] \| None` | 返回 .ppt-slide 内容区 [start, end) 偏移；解析失败时返回 None。 | [L1993](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L1993) |
| `def _main_inside_ppt_slide(html: str) -> bool` | 内容页的 <main> 必须落在 .ppt-slide 容器内；无 <main> 时视为通过。 | [L2021](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2021) |
| `def _validate_no_escaped_content(html: str) -> bool` | 快速静态检测：内容块是否逃逸到 .ppt-slide 容器之外。 | [L2033](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2033) |
| `def _validate_slide_dom(html: str) -> bool` | P8.1 写盘前校验：拦截 LLM 畸形片段、main 滑出 slide 及内容逃逸到 slide 之外。 | [L2069](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2069) |
| `def _is_slide_exportable(html: str) -> bool` | P8.2 fix 后校验：仅确认导出边界内的结构未被破坏。 | [L2080](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2080) |
| `def _chart_wrapper_has_height_chain(wrapper_tag: str) -> bool` | designer.md 图表高度链：包装器须参与纵向高度分配（min-h-0 或 flex-1/flex-[N]）。 | [L2103](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2103) |
| `def _validate_chart_height_chain(html: str) -> bool` | P8.1 写盘前校验：ECharts 图表外层 flex-col 卡片须具备高度分配类。 | [L2108](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2108) |
| `def _inject_class_into_chart_wrappers(html: str, class_name: str, *, needs_inject: Callable[[str], bool]) -> tuple[str, int]` | 向满足条件的图表 flex-col 包装器注入 class；已含该类则跳过。 | [L2126](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2126) |
| `def _fix_chart_height_chain(html: str) -> str` | 写盘前修复图表高度链：缺高度链类时注入 min-h-0，再对缺 flex-1/flex-[N] 的包装器注入 flex-1。 | [L2177](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2177) |
| `def _strip_js_comments(js: str) -> str` | 去掉 JS 块注释与行注释，供 option 填充检测（忽略说明文字中的 const option = null）。 | [L2213](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2213) |
| `def _chart_scaffold_target_id(script_body: str) -> str` | 源码未提供函数级文档字符串。 | [L2219](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2219) |
| `def _html_has_element_id(html: str, element_id: str) -> bool` | 与 pptx-craft hasChartContainer 同口径：页内存在 id="…"。 | [L2224](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2224) |
| `def _validate_chart_mount_references(html: str) -> bool` | P8.1 写盘前校验：含 echarts.init 的活跃脚本中 getElementById 须在页内存在对应 id。 | [L2244](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2244) |
| `def _chart_scaffold_option_populated(script_body: str) -> bool` | 与 pptx-craft hasOptionAssignment ∧ ¬hasNullOption 同口径（仅看可执行代码）。 | [L2264](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2264) |
| `def _fix_chart_scaffold_activation(html: str) -> str` | 写盘前修复：LLM 填了 option 但忘删 CHART_SCAFFOLD 注释定界符时自动激活。 | [L2272](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2272) |
| `def _extract_backup_timestamp(path: str) -> str` | 源码未提供函数级文档字符串。 | [L2302](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2302) |
| `def _normalize_page_marker_text(text: str) -> str` | 仅还原页码识别所需的空白与斜杠实体，不依赖白名单外模块。 | [L2332](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2332) |
| `def _strip_visible_page_markers(html_text: str) -> str` | 移除 Page Chrome 中的可见运行页码，保留 main 内的导航/业务内容。 | [L2339](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2339) |
| `def _insert_visible_page_marker(html_text: str, marker_text: str, policy: _PageNumberPolicy, style_id: str) -> str` | 在 ppt-slide 根容器末尾插入一个普通、可编辑的页码文本元素。 | [L2381](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2381) |
| `def _apply_visible_page_number_policy(html_text: str, *, user_query: str, page_number: int, total_pages: int, style_id: str) -> str` | 默认移除运行页码；用户明确要求时确定性统一为一个可编辑页码。 | [L2422](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2422) |
| `def _extract_title_from_outline(outline_page: str) -> str` | 从 outline 片段中提取页面标题，用于替换「第X页」占位符。 | [L2460](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2460) |
| `def _replace_placeholder_headings(html: str, outline_page: str) -> str` | 后置校验：将 <h1>/<h2> 中的「第X页」占位符替换为 outline 中的实际标题。 | [L2494](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2494) |
| `def _fix_echarts_svg_renderer(html: str) -> str` | 后置校验：确保所有 echarts.init 调用使用 SVG 渲染器。 | [L2518](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2518) |
| `def _has_empty_chart_svg(html: str) -> bool` | 检测是否存在空的 echarts-static-svg（有容器但 SVG 内无图形元素）。 | [L2546](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2546) |
| `def _has_chart_without_init(html: str) -> bool` | 检测 ECharts 图表容器缺少 echarts.init 初始化脚本。 | [L2564](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2564) |
| `def _strip_chart_header_unit(html: str) -> str` | 剥离图表卡片头部 HTML 中的单位 span，避免与 ECharts yAxis.name 形成双单位。 | [L2597](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2597) |
| `def _has_grid_layout(html: str) -> bool` | 检测是否使用了 CSS Grid 布局（html-to-pptx 转换器不支持 Grid）。 | [L2639](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2639) |
| `def _has_overflow_hidden_on_content(html: str) -> bool` | 检测核心内容容器（div/section/main 等）上是否使用了 overflow-hidden。 | [L2651](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2651) |
| `def _check_font_size_consistency(html: str) -> bool` | 检测同页字号是否一致。返回 True 表示不一致。 | [L2668](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2668) |
| `def _has_sparse_flex_text_list(html: str) -> bool` | 检测高置信度的稀疏 flex-1 文字列表，避免把正常长列表误判为空白风险。 | [L2700](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2700) |
| `def _classes_from_tag_attrs(attrs: str) -> set[str]` | 源码未提供函数级文档字符串。 | [L2765](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2765) |
| `def _is_constrained_flex_card(classes: set[str]) -> bool` | 源码未提供函数级文档字符串。 | [L2770](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2770) |
| `def _has_risky_trailing_content_in_constrained_card(html: str) -> bool` | 检测本次坏例对应的高置信度卡片越界结构。 | [L2779](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2779) |
| `def _is_explicit_decoration(attrs: str, classes: set[str]) -> bool` | 仅识别明确标注的背景装饰，避免误判普通绝对定位内容。 | [L2849](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2849) |
| `def _has_negative_edge_in_css(html: str, classes: set[str]) -> bool` | 检查装饰类对应 CSS 规则是否将图形部分移出画布。 | [L2857](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2857) |
| `def _has_off_canvas_decoration(html: str) -> bool` | 检测内容页中依赖负坐标和画布裁切的明确背景装饰。 | [L2878](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2878) |
| `def _is_fullpage_overlay_style(style_text: str) -> bool` | 判断一段 CSS 文本是否表示全页覆盖定位。 | [L2914](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2914) |
| `def _strip_unsupported_fullpage_overlays(html: str) -> str` | 移除使用 css-whitelist 不支持栅格化背景的全页空装饰遮罩。 | [L2928](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L2928) |
| `def _find_matching_js_delimiter(text: str, start: int) -> int` | 返回 JS 对象/数组/调用的闭合位置；忽略字符串和注释中的括号。 | [L3027](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3027) |
| `def _extract_set_option_blocks(html: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L3085](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3085) |
| `def _extract_named_js_array(text: str, pattern: re.Pattern[str]) -> str` | 源码未提供函数级文档字符串。 | [L3095](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3095) |
| `def _extract_named_js_object(text: str, name: str) -> str` | 源码未提供函数级文档字符串。 | [L3106](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3106) |
| `def _extract_string_property(text: str, name: str) -> str` | 源码未提供函数级文档字符串。 | [L3118](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3118) |
| `def _extract_top_level_js_objects(array_text: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L3127](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3127) |
| `def _extract_series_objects(option_block: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L3145](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3145) |
| `def _extract_numeric_property(text: str, name: str) -> float \| None` | 源码未提供函数级文档字符串。 | [L3151](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3151) |
| `def _extract_series_data(series: str) -> list[float \| None]` | 源码未提供函数级文档字符串。 | [L3160](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3160) |
| `def _extract_y_axis_bounds(option_block: str) -> list[tuple[float, float]]` | 源码未提供函数级文档字符串。 | [L3195](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3195) |
| `def _label_placement_signature(series: str) -> _ChartLabelPlacement \| None` | 提取启用标签的定位参数，用于估算跨系列文字框。 | [L3213](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3213) |
| `def _label_vertical_interval(anchor_height: float, placement: _ChartLabelPlacement) -> tuple[float, float] \| None` | 按300px参考绘图区估算标签文字框的归一化垂直区间。 | [L3252](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3252) |
| `def _vertical_interval_gap(first: tuple[float, float], second: tuple[float, float]) -> float` | 源码未提供函数级文档字符串。 | [L3271](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3271) |
| `def _has_dual_axis_combo_label_collision_risk(html: str) -> bool` | 检测同一双轴柱线图中安全距离不足的数据标签。 | [L3282](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3282) |
| `def _has_chart_top_lane_collision_risk(html: str) -> bool` | 检测顶部横向图例与双Y轴名称之间的垂直安全距离。 | [L3337](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3337) |
| `def _post_check_data_viz(html: str, failed_items: list[str], search_mode: str) -> list[str]` | 程序化后置校验：对 LLM 判定的'缺数据可视化'做二次确认，移除误判。 | [L3374](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3374) |
| `def _post_check_layout_issues(html: str, failed_items: list[str]) -> list[str]` | 程序化后置校验：检测 Grid、裁切、字号、边界和碰撞风险等布局问题。 | [L3387](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3387) |
| `def _rewrite_action_for(reason: str, *, page_type: str = '', outline_page: str = '', research_page: str = '') -> str` | 源码未提供函数级文档字符串。 | [L3582](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3582) |
| `def _build_rewrite_hint(failed_items: list[str], *, page_type: str = '', outline_page: str = '', research_page: str = '') -> str` | 源码未提供函数级文档字符串。 | [L3601](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3601) |
| `def _build_page_gen_rewrite_hint(reason: str, *, page_type: str = '', outline_page: str = '', research_page: str = '') -> str` | 定向重试指引：按真实校验 reason 生成，避免一律导向图表高度链。 | [L3622](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3622) |
| `def _page_qualifies_for_check_layout(page_type: str) -> bool` | 内容页 + agenda；cover/section/ending 等其余结构页排除。 | [L3662](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3662) |
| `def _page_qualifies_for_chart_gate(page_type: str) -> bool` | 纯内容页（可能有 CHART_SCAFFOLD）；排除 cover/agenda/section/ending。 | [L3672](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3672) |
| `def _html_requires_activate_template_chart(html: str) -> bool` | 是否应调用 pptx-craft activate-template-chart（与 skill 调用时机对齐）。 | [L3688](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3688) |
| `def _collect_check_layout_page_nums(successful_pages: list[int], outline_pages: dict[int, str]) -> list[int]` | 收集已通过静态校验并成功落盘、需做 check-layout 的页码。 | [L3713](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3713) |
| `def _coerce_check_layout_page_num(value: Any) -> int \| None` | 源码未提供函数级文档字符串。 | [L3727](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3727) |
| `def _extract_hard_tags_from_check_layout_line(line: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L3740](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3740) |
| `def _extract_hard_issues_from_check_layout_value(value: Any) -> list[str]` | 源码未提供函数级文档字符串。 | [L3753](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3753) |
| `def _parse_check_layout_json_payload(payload: dict[str, Any]) -> dict[int, list[str]]` | 源码未提供函数级文档字符串。 | [L3787](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3787) |
| `def _parse_check_layout_hard_failures(output: str, page_nums: list[int] \| None = None) -> dict[int, list[str]]` | 解析 check-layout CLI 输出中的硬项失败页（忽略 *-warning 软警告）。 | [L3817](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3817) |
| `def _check_layout_timeout_seconds(page_count: int) -> int` | 源码未提供函数级文档字符串。 | [L3863](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3863) |
| `def _build_check_layout_rewrite_hint(page_num: int, issues: list[str], *, page_type: str = '', outline_page: str = '', research_page: str = '') -> str` | 源码未提供函数级文档字符串。 | [L3868](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3868) |
| `async def _run_check_layout(node: PlanNode, *, pages_dir: str, pptx_root: str, page_nums: list[int], density: str = _CHECK_LAYOUT_DENSITY) -> tuple[dict[int, list[str]], bool]` | 运行 pptx-craft check-layout；返回 (硬项失败页, 是否跳过)。 | [L3893](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3893) |
| `async def _run_activate_template_chart_page(node: PlanNode, *, pages_dir: str, pptx_root: str, page_num: int) -> tuple[bool, str, bool]` | 运行 pptx-craft activate-template-chart；返回 (通过, 失败详情, 是否跳过)。 | [L3958](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L3958) |
| `def _extract_page_keywords(research_page: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L4006](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4006) |
| `def _build_search_queries(templates: list[str], *, topic: str, page_keywords: list[str]) -> list[str]` | 源码未提供函数级文档字符串。 | [L4032](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4032) |
| `def _extract_search_text(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L4051](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4051) |
| `def _strip_leading_non_section_lines(text: str) -> str` | 剥离首部非 ``### P`` 章节开头的行。 | [L4082](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4082) |
| `def _split_md_pages(text: str) -> dict[int, str]` | 按 `### P{N}:` 章节拆分 Markdown，返回 {页码: 该页片段}。 | [L4099](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4099) |
| `def _build_image_section(image_map_page: str) -> str` | 根据本页图片素材描述和图片数量，构造图片素材 section。 | [L4171](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4171) |
| `def _build_page_prompt(page_number: int, style_id: str, style_text: str, outline_page: str, research_page: str, *, designer_md_text: str = '', outline_is_full: bool = False, research_is_full: bool = False, rewrite_hint: str = '', original_html: str = '', image_map_page: str = '', user_query: str = '', total_pages: int = 0) -> str` | 源码未提供函数级文档字符串。 | [L4207](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4207) |
| `def _postprocess_generated_html(raw_html: str, ctx: PageGenContext) -> tuple[str, str, str]` | LLM 全文生成后的同步 HTML 校验/修复（移出事件循环执行）。 | [L4438](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4438) |
| `def _postprocess_content_template_fill(raw_html: str, *, seed_html: str, ctx: PageGenContext) -> tuple[str, str, str]` | 内容页填槽后的同步校验/修复（移出事件循环执行）。 | [L4472](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4472) |
| `def _postprocess_structural_template_fill(raw_html: str, *, seed_html: str, page_type: str, ctx: PageGenContext) -> str` | 结构页填槽后的同步校验/修复（移出事件循环执行）。 | [L4546](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L4546) |
| `def _extract_page_number(filename: str) -> int` | 源码未提供函数级文档字符串。 | [L5891](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_page_gen.py#L5891) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1)

**模块职责：** 定义 RequirementCollectError、P21SlotExtractNode、P22AskBatchNode、P23AskStyleNode、P24DeriveParamsNode、RequirementCollectNode 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L12) |
| `_TEXT_SOURCE_KEYS` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L14) |
| `_collect_user_text` | `未显式标注` | [L15](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L15) |
| `_parse_json_payload` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L16) |
| `_DOC_EXCERPT_MAX_CHARS` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L17) |
| `_DEFAULT_AUDIENCE` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L18) |
| `_DEFAULT_PRESENTATION_PURPOSE` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L19) |
| `_DEFAULT_PAGE_COUNT` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L20) |
| `_MAX_PAGE_COUNT` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L21) |
| `_VALID_STYLE_IDS` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L23) |
| `_VALID_SEARCH_MODES` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L26) |
| `_VALID_SOURCE_TYPES` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L27) |
| `_VALID_RESEARCH_DEPTHS` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L28) |
| `_VALID_STRUCTURAL_REQUESTS` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L29) |
| `_STYLE_LABEL_TO_ID` | `dict[str, str]` | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L33) |
| `_PAGE_LABEL_TO_COUNT` | `dict[str, int]` | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L43) |
| `_PURPOSE_LABEL_TO_VALUE` | `dict[str, str]` | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L49) |
| `_SLOT_FIELDS` | `未显式标注` | [L56](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L56) |
| `_ASK_BATCH_FIELDS` | `未显式标注` | [L57](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L57) |
| `_P21_GAP_FIELDS` | `未显式标注` | [L58](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L58) |
| `_P21_SLOT_SYSTEM_PROMPT` | `未显式标注` | [L60](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L60) |
| `_TOPIC_SUGGEST_COUNT` | `未显式标注` | [L107](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L107) |
| `_P21_TOPIC_SUGGEST_SYSTEM_PROMPT` | `未显式标注` | [L109](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L109) |
| `_P24_SYSTEM_PROMPT` | `未显式标注` | [L120](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L120) |
| `_BATCH_FALLBACK_SYSTEM_PROMPT` | `未显式标注` | [L828](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L828) |
| `_TOPIC_FALLBACK_SYSTEM_PROMPT` | `未显式标注` | [L844](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L844) |
| `_STYLE_FALLBACK_SYSTEM_PROMPT` | `未显式标注` | [L851](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L851) |

### [`class RequirementCollectError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L145)

P2 需求收集失败。

### [`class P21SlotExtractNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1158)

P2.1 — LLM 槽位分析；topic 缺失时 LLM 生成 4 个主题候选并 ask 用户选择。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1161](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1161) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1197](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1197) |

### [`class P22AskBatchNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1221)

P2.2 — 收集 page_count / audience / presentation_purpose，缺一不可。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1224](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1224) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1258](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1258) |

### [`class P23AskStyleNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1264)

P2.3 — 收集 style_id。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1271](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1271) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1304](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1304) |

### [`class P24DeriveParamsNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1316)

P2.4 — LLM 推断 search_mode、source_type、research_depth。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1323](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1323) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1358](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1358) |

### [`class RequirementCollectNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1401)

P2 — 需求收集（P2.1 → P2.2 → P2.3 → P2.4）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L1433](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1433) |
| `def _ensure_image_vars(self, ctx: dict[str, Any]) -> None` | 图片变量兜底：image_paths 空数组 + image_sources 默认 local。 | [L1488](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1488) |
| `def _set_style_mode(self, ctx: dict[str, Any]) -> None` | 根据 style_id / pack_dir 设置 style_mode（供下游 P3.5/P7/P8/P9 分支判断）。 | [L1496](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1496) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1528](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1528) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_page_count(value: Any) -> int \| None` | 源码未提供函数级文档字符串。 | [L149](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L149) |
| `def _normalize_style_id(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L179](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L179) |
| `def _resolve_style_id(style_id: Any, style_description: Any = None) -> str` | 归一化 style_id，支持从 style_description 回退。 | [L211](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L211) |
| `def _style_id_from_label(label: str) -> tuple[str, str]` | 源码未提供函数级文档字符串。 | [L227](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L227) |
| `def _page_count_from_label(label: str, other_text: str = '') -> int \| None` | 源码未提供函数级文档字符串。 | [L239](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L239) |
| `def _purpose_from_label(label: str, other_text: str = '') -> str` | 源码未提供函数级文档字符串。 | [L246](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L246) |
| `def _audience_from_label(label: str, other_text: str = '') -> str` | 源码未提供函数级文档字符串。 | [L254](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L254) |
| `def _has_nonempty_topic(inputs: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L264](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L264) |
| `def _set_requirement_artifact(ctx: dict[str, Any]) -> None` | 把 P2 需求收集的关键槽位写入 __artifact__，供跨请求复用。 | [L269](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L269) |
| `def _apply_slot_defaults(inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L282](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L282) |
| `def _batch_field_is_satisfied(inputs: dict[str, Any], field: str) -> bool` | 源码未提供函数级文档字符串。 | [L291](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L291) |
| `def _unsatisfied_batch_fields(inputs: dict[str, Any]) -> list[str]` | 源码未提供函数级文档字符串。 | [L303](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L303) |
| `def _require_batch_fields_collected(inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L311](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L311) |
| `def _prune_satisfied_batch_missing_fields(inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L324](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L324) |
| `def _merge_slot_payload(inputs: dict[str, Any], payload: dict[str, Any], *, preserve_topic: bool = False) -> None` | 源码未提供函数级文档字符串。 | [L332](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L332) |
| `def _build_p21_slot_prompt(user_text: str, doc_excerpt: str, inputs: dict[str, Any], *, preserve_topic: bool) -> str` | 源码未提供函数级文档字符串。 | [L406](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L406) |
| `def _parse_slot_analysis_response(raw: str, *, preserve_topic: bool) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L429](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L429) |
| `def _build_p24_prompt(inputs: dict[str, Any], user_text: str, doc_excerpt: str) -> str` | 源码未提供函数级文档字符串。 | [L449](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L449) |
| `async def _ask_missing_batch_fields(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L470](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L470) |
| `def _parse_derive_params_response(raw: str) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L516](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L516) |
| `async def _derive_params_via_llm(node: PlanNode, inputs: dict[str, Any]) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L550](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L550) |
| `def _field_from_header(header: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L569](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L569) |
| `def _field_for_answer_item(item: dict[str, Any], sent_questions: list[dict[str, Any]] \| None) -> str \| None` | 按答案中的 question 文本与发出题目精确匹配，映射到槽位字段。 | [L580](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L580) |
| `def _apply_answer_item(inputs: dict[str, Any], item: dict[str, Any], *, sent_questions: list[dict[str, Any]] \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L594](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L594) |
| `def _apply_ask_answers(inputs: dict[str, Any], answers: list[Any], *, sent_questions: list[dict[str, Any]] \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L640](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L640) |
| `def _build_batch_questions(missing_fields: list[str]) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L651](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L651) |
| `def _build_style_question() -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L701](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L701) |
| `def _style_id_resolved(inputs: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L716](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L716) |
| `def _style_needs_user_ask(inputs: dict[str, Any]) -> bool` | style_id 仍缺失时，判断是否需 ask（调用方应已确认 style 未 resolved）。 | [L720](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L720) |
| `def _finalize_style_slot(inputs: dict[str, Any], *, fallback: str \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L727](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L727) |
| `async def _ask_missing_style(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L739](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L739) |
| `def _normalize_ask_result(result: Any) -> tuple[str, list[Any]]` | 源码未提供函数级文档字符串。 | [L776](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L776) |
| `def _answer_item_is_empty(item: Any) -> bool` | An answer item is 'empty' when both selected_options and free text are blank. | [L786](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L786) |
| `def _is_auto_skip(status: str, answers: list[Any]) -> bool` | Detect when the user did not provide a usable answer. | [L806](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L806) |
| `def _build_batch_fallback_prompt(missing_fields: list[str], user_text: str, doc_excerpt: str) -> str` | 源码未提供函数级文档字符串。 | [L866](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L866) |
| `def _build_topic_fallback_prompt(topic_options: list[str], user_text: str, doc_excerpt: str) -> str` | 源码未提供函数级文档字符串。 | [L881](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L881) |
| `def _build_style_fallback_prompt(inputs: dict[str, Any], user_text: str) -> str` | 源码未提供函数级文档字符串。 | [L896](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L896) |
| `async def _llm_default_batch_fields(node: PlanNode, inputs: dict[str, Any], missing_fields: list[str]) -> None` | 超时兜底：LLM 推断缺失 batch 字段；最终仍为空时落到模块级 default。 | [L913](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L913) |
| `async def _llm_default_topic(node: PlanNode, inputs: dict[str, Any], topic_options: list[str]) -> str` | 超时兜底：LLM 从候选中挑选最契合的主题；失败时取第一项。 | [L957](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L957) |
| `async def _llm_default_style(node: PlanNode, inputs: dict[str, Any]) -> str` | 超时兜底：LLM 从五个有效 style_id 中挑选；失败时返回 'business-classic'。 | [L994](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L994) |
| `def _build_topic_suggest_prompt(inputs: dict[str, Any], doc_excerpt: str) -> str` | 源码未提供函数级文档字符串。 | [L1014](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1014) |
| `def _parse_topic_suggestions(raw: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L1027](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1027) |
| `def _build_topic_ask_question(topics: list[str]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1049](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1049) |
| `def _topic_text_from_ask_answers(answers: list[Any]) -> str` | 源码未提供函数级文档字符串。 | [L1061](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1061) |
| `def _append_topic_supplement(inputs: dict[str, Any], reply_text: str) -> None` | 源码未提供函数级文档字符串。 | [L1082](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1082) |
| `async def _generate_topic_suggestions(node: PlanNode, inputs: dict[str, Any], doc_excerpt: str) -> list[str]` | 源码未提供函数级文档字符串。 | [L1096](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1096) |
| `async def _resolve_topic_via_ask(node: PlanNode, inputs: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L1113](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/requirement_collect.py#L1113) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L1)

**模块职责：** Stage 8 — 演讲备注生成与注入（仅 need_speaker_notes=True 时执行）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L29) |
| `_DEFAULT_TONE` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L31) |

### [`class _SpeakerNotesContext`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L35)

生成和校验逐页演讲备注所需的共享上下文。

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `pages_dir` | `str` | `—` | [L38](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L38) |
| `page_texts` | `dict[int, str]` | `—` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L39) |
| `total_pages` | `int` | `—` | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L40) |
| `topic` | `str` | `—` | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L41) |
| `audience` | `str` | `—` | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L42) |
| `presentation_purpose` | `str` | `—` | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L43) |
| `tone_rules` | `str` | `—` | [L44](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L44) |

### [`class SpeakerNotesNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L47)

Stage 8 — 演讲备注生成与注入。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L50) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L60](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L60) |
| `async def _get_tone_rules(self, inputs: dict[str, Any]) -> str` | 取语调规则：优先 tone-style skill，降级为默认。 | [L116](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L116) |
| `async def _extract_page_texts(self, pptx_path: str, pptx_root: str) -> dict[int, str]` | cli notes extract-text 抽取每页可见纯文本。 | [L145](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L145) |
| `async def _generate_notes_per_page(self, context: _SpeakerNotesContext) -> None` | 按页并发生成备注分片。 | [L170](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L170) |
| `async def _validate_and_retry(self, context: _SpeakerNotesContext) -> None` | 分片校验：缺失/空页重跑一次。 | [L212](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L212) |
| `async def _inject_notes(self, pptx_path: str, pages_dir: str, pptx_root: str) -> bool` | 单进程 cli notes inject 写回 .pptx。 | [L253](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L253) |
| `async def _execute_stream(self, inputs: dict[str, Any])` | 源码未提供方法级文档字符串。 | [L274](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/speaker_notes.py#L274) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L1)

**模块职责：** 定义 StylePrepareNode。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L11) |
| `_PRESET_STYLES` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L14) |

### [`class StylePrepareNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L17)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L18](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L18) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L77](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L77) |
| `async def _read_template_md(self, pack_dir: str) -> str` | 读取模板包目录下的模板 md 文件内容（降级时使用）。 | [L155](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L155) |
| `async def _load_preset_style(self, style_id: str, pptx_root: str = '') -> str` | 源码未提供方法级文档字符串。 | [L179](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L179) |
| `async def _generate_custom_style(self, topic: str, audience: str, style_id: str, style_description: str, user_query: str = '') -> str` | 源码未提供方法级文档字符串。 | [L204](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L204) |
| `async def _write_style_file(self, path: str, content: str) -> None` | 源码未提供方法级文档字符串。 | [L306](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L306) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L318](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/style_prepare.py#L318) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L1)

**模块职责：** 定义 TemplateContextNode。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L11) |

### [`class TemplateContextNode(PlanNode)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L14)

P3.5 — 模板叙事上下文预处理（条件执行，仅 style_mode == template_canvas 时运行）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L21](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L21) |
| `async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L59) |
| `async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L130](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/template_context.py#L130) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L1)

**模块职责：** 定义 BashExecError、BashResult、_parse_exit_code_from_text、quote_path、cli_path、_exit_code_from_text 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_EXIT_CODE_RE` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L13) |
| `_EXIT_CODE_PREFIX_RE` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L28) |

### [`class BashExecError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L16)

bash 命令执行失败（required=True 时抛出）。

### [`class BashResult`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L21)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `exit_code` | `int` | `—` | [L22](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L22) |
| `stdout` | `str` | `—` | [L23](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L23) |
| `stderr` | `str` | `—` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L24) |
| `raw` | `str` | `—` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L25) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _parse_exit_code_from_text(text: str) -> int \| None` | 从 ``Exit code N`` 文本前缀解析退出码。 | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L31) |
| `def quote_path(path: str) -> str` | 源码未提供函数级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L37) |
| `def cli_path(subcommand: str, pptx_root: str) -> str` | 构建 cli.js 子命令路径。 | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L42) |
| `def _exit_code_from_text(*parts: str) -> int \| None` | 源码未提供函数级文档字符串。 | [L56](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L56) |
| `def _coerce_exit_code(exit_code: int, *, stdout: str = '', stderr: str = '', success: bool \| None = None, error: str = '') -> int` | 源码未提供函数级文档字符串。 | [L67](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L67) |
| `def normalize_tool_text(result: Any) -> str` | 源码未提供函数级文档字符串。 | [L89](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L89) |
| `def parse_bash_payload(text: str) -> BashResult` | 源码未提供函数级文档字符串。 | [L122](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L122) |
| `def _extract_bash_result(raw: Any) -> BashResult \| None` | 从 raw tool 返回值直接提取 exit_code/stdout/stderr。 | [L157](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L157) |
| `async def run_bash(node: PlanNode, command: str, *, timeout_seconds: int = 300, required: bool = True, workdir: str \| None = None) -> BashResult` | 源码未提供函数级文档字符串。 | [L217](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L217) |
| `def _build_bash_kwargs(command: str, timeout_seconds: int, workdir: str \| None, *, with_timeout: bool) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L265](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L265) |
| `def combined_output(result: BashResult) -> str` | 源码未提供函数级文档字符串。 | [L280](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/utils/bash_utils.py#L280) |

## `jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L1)

**模块职责：** SkillTurbo 工具化 -- 将 SkillTurbo 封装为 DeepAgent 的 @tool。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L25) |
| `_SKILL_TURBO_STOP_HINT` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L28) |
| `_SKILL_TURBO_HITL_PLACEHOLDER` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L42) |
| `_SKILL_TURBO_EVENT_TYPE_TO_OUTPUT_TYPE` | `dict[str, str]` | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L50) |
| `_SKILL_TURBO_SKIP_EVENT_TYPES` | `frozenset[str]` | [L67](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L67) |
| `_SKILL_TURBO_TASK_EVENT_TYPES` | `frozenset[str]` | [L78](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L78) |
| `_current_skill_turbo_adapter` | `ContextVar[Any]` | [L96](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L96) |
| `_skill_turbo_outer_todo_active` | `ContextVar[bool \| None]` | [L99](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L99) |
| `_skill_turbo_hitl_tic` | `ContextVar[Any]` | [L120](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L120) |
| `_skill_turbo_resume_answers` | `ContextVar[Any]` | [L155](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L155) |
| `_current_request_metadata` | `ContextVar[Any]` | [L218](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L218) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _without_inner_task_routing(payload: dict[str, Any]) -> dict[str, Any]` | Copy a parent-bound event without its SkillTurbo-only task id. | [L85](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L85) |
| `def set_skill_turbo_outer_todo_active(active: bool) -> Token` | Bind whether the parent task list owns this tool call's display. | [L104](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L104) |
| `def get_skill_turbo_outer_todo_active() -> bool \| None` | 源码未提供函数级文档字符串。 | [L109](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L109) |
| `def reset_skill_turbo_outer_todo_active(token: Token) -> None` | 源码未提供函数级文档字符串。 | [L113](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L113) |
| `def set_skill_turbo_hitl_tic(tic: Any) -> Token` | 源码未提供函数级文档字符串。 | [L123](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L123) |
| `def get_skill_turbo_hitl_tic() -> Any` | 源码未提供函数级文档字符串。 | [L127](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L127) |
| `def set_current_skill_turbo_adapter(adapter: Any) -> Token` | 绑定当前 async 上下文的 DeepAdapter 实例，返回 Token 用于 reset。 | [L131](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L131) |
| `def get_current_skill_turbo_adapter() -> Any` | 获取当前上下文的 DeepAdapter 实例。 | [L136](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L136) |
| `def reset_current_skill_turbo_adapter(token: Token) -> None` | 恢复之前的 adapter 绑定。 | [L141](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L141) |
| `def clear_current_skill_turbo_adapter() -> None` | 强制清空当前上下文的 adapter 绑定（用 None 覆盖，不依赖 Token）。 | [L146](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L146) |
| `def set_skill_turbo_resume_answers(answers: Any) -> Token` | 源码未提供函数级文档字符串。 | [L160](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L160) |
| `def get_skill_turbo_resume_answers() -> Any` | 源码未提供函数级文档字符串。 | [L164](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L164) |
| `def reset_skill_turbo_resume_answers(token: Token) -> None` | 源码未提供函数级文档字符串。 | [L168](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L168) |
| `def _resume_user_input_from_raw(raw: Any, resume_ctx: dict[str, Any], adapter: Any) -> Any` | 把 handle_resume 的 InteractiveInput / 原始 answers 转成内层 rail 的 user_input。 | [L172](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L172) |
| `def _resolve_skill_turbo_resume_session_id(external_session_id: Any, parent_session: Any) -> str` | Align resume checkpointer key with executor: metadata sid, else parent session. | [L193](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L193) |
| `def set_current_request_metadata(metadata: Any) -> Token` | 绑定当前 async 上下文的请求 metadata，返回 Token 用于 reset。 | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L223) |
| `def get_current_request_metadata() -> Any` | 获取当前上下文的请求 metadata。 | [L228](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L228) |
| `def reset_current_request_metadata(token: Token) -> None` | 恢复之前的 metadata 绑定。 | [L233](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L233) |
| `def _build_artifact_summary(holder: dict[str, Any]) -> str` | 从 executor 的 _node_artifacts_holder 构建产物摘要文本。 | [L238](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L238) |
| `def _wrap_skill_turbo_result(result_dict: dict[str, Any], artifact_holder: dict[str, Any] \| None = None) -> dict[str, Any]` | 在结果末尾追加产物摘要；成功时追加停止提示引导 LLM 结束当前轮次。 | [L268](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L268) |
| `@tool(name='skill_acceleration_exec', description='技能加速模块。当用户意图涉及技能类任务（如生成 PPT、文档转换等结构化产出）时，可优先尝试调用此工具以获得更快的生成流程。工具内部会二次判断是否真正匹配已支持的技能，不匹配时自动降级为普通对话。当前内部支持 ppt-craft 技能（PPT 演示文稿制作）。【重要】每次调用仅处理一个独立任务。若用户要求生成多个同类产物（如多份不同主题的 PPT），必须为每个产物分别发起独立调用，且严格串行：等待前一次调用完全结束并收到返回结果后，才能发起下一次调用。严禁在同一轮对话中并行发起多次调用。【临时排除】当任务需要使用自定义/指定模板时，不要调用此工具--无论该模板路径是出现在当前用户消息里、引用历史会话中生成或上传的模板（如“使用刚才生成的风格模板”“用之前那个模板”等自然语言引用）、还是消息中含 模板目录路径、模板主文件路径、pack_dir 等字段名。判断“是否涉及自定义模板”应基于语义。当前 skill 加速器暂未适配模板包功能，此类请求应改用 skill_tool 走 pptx-craft 标准流程。此排除规则为临时措施，待 skill 加速器适配模板功能后移除。') async def skill_turbo(query: str) -> dict[str, Any] \| str` | 执行 SkillAccelerationExec 任务。 | [L310](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L310) |
| `def get_skill_turbo_tools() -> list` | 返回 SkillTurbo 工具列表，供 interface_deep.py 注册。 | [L703](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L703) |

## `jiuwenswarm/server/runtime/skill_turbo/tools_loader.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L1)

**模块职责：** SkillTurbo 工具装载器。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L28) |
| `LoaderFn` | `未显式标注` | [L60](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L60) |
| `__all__` | `未显式标注` | [L327](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L327) |

### [`class ToolLoaderContext`](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L35)

工具装载上下文。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `agent_id` | `str` | `'skill_turbo'` | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L42) |
| `language` | `str` | `'zh'` | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L43) |
| `sys_operation` | `Any \| None` | `None` | [L44](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L44) |
| `vision_model_config` | `Any \| None` | `None` | [L45](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L45) |
| `audio_model_config` | `Any \| None` | `None` | [L46](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L46) |
| `video_model_enabled` | `bool` | `False` | [L47](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L47) |
| `image_gen_enabled` | `bool` | `False` | [L48](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L48) |
| `skill_manager` | `Any \| None` | `None` | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L49) |
| `request_id` | `str` | `''` | [L50](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L50) |
| `session_id` | `str` | `''` | [L51](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L51) |
| `channel_id` | `str` | `''` | [L52](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L52) |
| `request_metadata` | `dict[str, Any] \| None` | `None` | [L53](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L53) |
| `extra` | `dict[str, Any]` | `field(default_factory=dict)` | [L54](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L54) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _register_group(out_tools: list[Any], ctx: ToolLoaderContext, loader: LoaderFn, group: str) -> None` | 统一执行单个分组 loader：try/except + 去重 + 日志。 | [L63](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L63) |
| `async def load_all(ctx: ToolLoaderContext) -> list[Any]` | 加载所有可用工具。 | [L115](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L115) |
| `def load_send_file_tools(ctx: ToolLoaderContext) -> list[Any]` | 加载 send_file_to_user 工具（每次请求单独刷新，不走 load_all 一次性缓存）。 | [L140](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L140) |
| `def _load_jw_named_web(ctx: ToolLoaderContext) -> list[Any]` | jiuwenclaw 命名 Web 工具：web_search / fetch_webpage。 | [L154](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L154) |
| `def _load_jw_vision(ctx: ToolLoaderContext) -> list[Any]` | 视觉工具：vision_model_config 为空时跳过（沿用 _get_tool_cards 同款写法）。 | [L168](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L168) |
| `def _load_jw_audio(ctx: ToolLoaderContext) -> list[Any]` | 音频工具：create_audio_tools 内部已按 audio_model_config 过滤，本处不再判断。 | [L183](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L183) |
| `def _load_jw_video(ctx: ToolLoaderContext) -> list[Any]` | 视频理解：video_model_enabled 开关（沿用 _get_tool_cards 同款写法）。 | [L196](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L196) |
| `def _load_jw_image_gen(ctx: ToolLoaderContext) -> list[Any]` | 图像生成：image_gen_enabled 开关（沿用 _get_tool_cards 同款写法）。 | [L205](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L205) |
| `def _load_jw_skill_toolkit(ctx: ToolLoaderContext) -> list[Any]` | SkillToolkit：需要 skill_manager（沿用 _get_tool_cards 同款写法）。 | [L214](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L214) |
| `def _load_jw_ask_user(ctx: ToolLoaderContext) -> list[Any]` | AskUserQuestion：始终注册（沿用 _get_tool_cards 同款写法）。 | [L223](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L223) |
| `def _load_jw_deepresearch(ctx: ToolLoaderContext) -> list[Any]` | DeepResearch 工具集：始终注册（沿用 _get_tool_cards 同款写法）。 | [L236](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L236) |
| `def _load_jw_send_file(ctx: ToolLoaderContext) -> list[Any]` | send_file_to_user：沿用 interface_deep 同款可用性判断。 | [L249](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L249) |
| `def _load_oj_filesystem(ctx: ToolLoaderContext) -> list[Any]` | 文件系统工具：构造前提是 sys_operation 存在（jiuwenclaw 既有惯例）。 | [L284](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L284) |
| `def _load_oj_bash(ctx: ToolLoaderContext) -> list[Any]` | Bash 工具：构造前提是 sys_operation 存在。 | [L312](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L312) |

## `jiuwenswarm/server/runtime/skill_turbo/validator.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L1)

**模块职责：** PlanCodeValidator -- 规划代码 AST 静态安全校验。

### [`class PlanCodeValidationError(Exception)`](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L12)

规划代码静态校验失败。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, errors: list[str])` | 源码未提供方法级文档字符串。 | [L15](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L15) |

### [`class CodeValidationPolicy`](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L21)

代码静态校验策略。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `'plan_code'` | [L24](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L24) |
| `allow_import` | `bool` | `False` | [L25](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L25) |
| `allow_import_from` | `bool` | `True` | [L26](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L26) |
| `allowed_import_exact` | `tuple[str, ...]` | `()` | [L27](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L27) |
| `allowed_import_prefixes` | `tuple[str, ...]` | `()` | [L28](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L28) |
| `denied_import_exact` | `tuple[str, ...]` | `()` | [L29](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L29) |
| `denied_import_prefixes` | `tuple[str, ...]` | `()` | [L30](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L30) |
| `import_error_hint` | `str` | `'仅允许从 skill_codes 导入'` | [L31](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L31) |
| `deny_relative_import` | `bool` | `True` | [L32](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L32) |
| `deny_dunder_attribute` | `bool` | `True` | [L33](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L33) |
| `allowed_dunder_attributes` | `tuple[str, ...]` | `()` | [L34](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L34) |
| `denied_call_names` | `tuple[str, ...]` | `()` | [L35](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L35) |
| `denied_attribute_call_names` | `tuple[str, ...]` | `()` | [L36](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L36) |
| `deny_global` | `bool` | `False` | [L37](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L37) |
| `deny_nonlocal` | `bool` | `False` | [L38](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L38) |
| `deny_with` | `bool` | `False` | [L39](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L39) |
| `deny_try` | `bool` | `False` | [L40](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L40) |
| `deny_del_attribute` | `bool` | `False` | [L41](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L41) |
| `deny_del_subscript` | `bool` | `False` | [L42](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L42) |
| `deny_type_three_args` | `bool` | `False` | [L43](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L43) |
| `require_abort_reraise` | `bool` | `False` | [L46](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L46) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def plan_code(cls, allowed_import_prefixes: list[str] \| tuple[str, ...] \| None = None) -> 'CodeValidationPolicy'` | 源码未提供方法级文档字符串。 | [L49](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L49) |
| `@classmethod def generated_skill_code(cls) -> 'CodeValidationPolicy'` | 源码未提供方法级文档字符串。 | [L62](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L62) |
| `@classmethod def builtin_skill_code(cls, allowed_import_prefixes: list[str] \| tuple[str, ...] \| None = None) -> 'CodeValidationPolicy'` | 源码未提供方法级文档字符串。 | [L119](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L119) |

### [`class PlanCodeValidator`](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L203)

代码安全校验器：按 profile 拦截 import、dunder 与危险语法。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, allowed_import_prefixes: list[str] \| None = None, policy: CodeValidationPolicy \| None = None)` | 源码未提供方法级文档字符串。 | [L206](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L206) |
| `@classmethod def for_generated_skill_code(cls) -> 'PlanCodeValidator'` | 源码未提供方法级文档字符串。 | [L216](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L216) |
| `@classmethod def for_builtin_skill_code(cls, allowed_import_prefixes: list[str] \| None = None) -> 'PlanCodeValidator'` | 源码未提供方法级文档字符串。 | [L220](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L220) |
| `def validate(self, code: str) -> list[str]` | 校验代码，返回错误列表。空列表表示通过。 | [L227](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L227) |
| `def validate_or_raise(self, code: str) -> None` | 校验代码，失败时抛出 PlanCodeValidationError。 | [L239](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L239) |
| `def _check_node(self, node: ast.AST, errors: list[str]) -> None` | 源码未提供方法级文档字符串。 | [L245](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L245) |
| `def _check_import(self, node: ast.Import, errors: list[str]) -> None` | 源码未提供方法级文档字符串。 | [L292](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L292) |
| `def _check_import_from(self, node: ast.ImportFrom, errors: list[str]) -> None` | 源码未提供方法级文档字符串。 | [L302](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L302) |
| `def _check_call(self, node: ast.Call, errors: list[str]) -> None` | 源码未提供方法级文档字符串。 | [L315](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L315) |
| `def _check_delete(self, node: ast.Delete, errors: list[str]) -> None` | 源码未提供方法级文档字符串。 | [L338](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L338) |
| `def _check_try_reraise(self, node: ast.Try, errors: list[str]) -> None` | 要求 except Exception/BaseException 子句中显式重抛 AbortError。 | [L352](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L352) |
| `@staticmethod def _is_broad_handler(handler: ast.ExceptHandler) -> bool` | 源码未提供方法级文档字符串。 | [L373](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L373) |
| `@staticmethod def _handler_reraises_abort(handler: ast.ExceptHandler) -> bool` | 检查 except 子句体内是否包含 AbortError 重抛守护。 | [L385](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L385) |
| `def _is_import_allowed(self, module: str) -> bool` | 源码未提供方法级文档字符串。 | [L413](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L413) |
| `@staticmethod def _matches(module: str, exact_modules: tuple[str, ...], prefixes: tuple[str, ...]) -> bool` | 源码未提供方法级文档字符串。 | [L425](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L425) |
| `def _format_import_error(self, module: str, lineno: int) -> str` | 源码未提供方法级文档字符串。 | [L432](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L432) |
