# 根包、公共基础与实例管理 Python API

覆盖包级启动/兼容补丁、`common` 横切能力和 `instance_manager` 多实例协调接口。

> 签名与行号取自当前源码 AST。这里同时列出公开和内部顶级接口；名称以下划线开头者是实现细节，不承诺稳定兼容。行为语义与调用约束请结合对应模块设计分册阅读。

## `jiuwenswarm/__init__.py`

[打开源码](../../../../../jiuwenswarm/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/app.py`

[打开源码](../../../../../jiuwenswarm/app.py#L1)

**模块职责：** Orchestrate AgentServer + Gateway in two processes (split layout, one command).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_parsed_dotenv_path` | `未显式标注` | [L30](../../../../../jiuwenswarm/app.py#L30) |
| `_workspace_dir` | `未显式标注` | [L33](../../../../../jiuwenswarm/app.py#L33) |
| `_config_file` | `未显式标注` | [L34](../../../../../jiuwenswarm/app.py#L34) |
| `_new_workspace` | `未显式标注` | [L35](../../../../../jiuwenswarm/app.py#L35) |
| `_old_workspace` | `未显式标注` | [L36](../../../../../jiuwenswarm/app.py#L36) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def main() -> None` | 源码未提供函数级文档字符串。 | [L49](../../../../../jiuwenswarm/app.py#L49) |

## `jiuwenswarm/common/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/common/chat_final.py`

[打开源码](../../../../../jiuwenswarm/common/chat_final.py#L1)

**模块职责：** chat.final 落地协议字段。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `FINAL_MODE_PATCH_SEGMENT` | `未显式标注` | [L14](../../../../../jiuwenswarm/common/chat_final.py#L14) |
| `FINAL_MODE_REPLACE_TURN` | `未显式标注` | [L15](../../../../../jiuwenswarm/common/chat_final.py#L15) |
| `FINAL_MODE_APPEND` | `未显式标注` | [L16](../../../../../jiuwenswarm/common/chat_final.py#L16) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def annotate_chat_final(payload: Mapping[str, Any] \| None, *, final_mode: str = FINAL_MODE_PATCH_SEGMENT) -> dict[str, Any]` | 确保 chat.final payload 带 final_mode；已有显式值时不覆盖。 | [L19](../../../../../jiuwenswarm/common/chat_final.py#L19) |
| `def ensure_final_mode_inplace(payload: MutableMapping[str, Any], *, final_mode: str = FINAL_MODE_PATCH_SEGMENT) -> None` | 就地写入 final_mode（仅当缺失时）。 | [L36](../../../../../jiuwenswarm/common/chat_final.py#L36) |
| `def reasoning_only_empty_reply_fallback_text(lang: str = 'zh') -> str` | Fixed short user-visible reply when the model only emitted reasoning. | [L50](../../../../../jiuwenswarm/common/chat_final.py#L50) |
| `def fill_reasoning_only_empty_final_content(*, content: str, has_visible_streamed_text: bool, has_reasoning: bool, lang: str = 'zh') -> str` | Fill empty chat.final content for reasoning-only completions. | [L61](../../../../../jiuwenswarm/common/chat_final.py#L61) |

## `jiuwenswarm/common/cleanup.py`

[打开源码](../../../../../jiuwenswarm/common/cleanup.py#L1)

**模块职责：** Periodic background cleanup for old session data and file_ops logs.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L24](../../../../../jiuwenswarm/common/cleanup.py#L24) |
| `DEFAULT_CLEANUP_PERIOD_DAYS` | `未显式标注` | [L26](../../../../../jiuwenswarm/common/cleanup.py#L26) |
| `RECURRING_CLEANUP_INTERVAL_S` | `未显式标注` | [L27](../../../../../jiuwenswarm/common/cleanup.py#L27) |
| `FIRST_CLEANUP_DELAY_S` | `未显式标注` | [L28](../../../../../jiuwenswarm/common/cleanup.py#L28) |
| `AGENT_ID` | `未显式标注` | [L29](../../../../../jiuwenswarm/common/cleanup.py#L29) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _get_cleanup_period_days() -> int` | 源码未提供函数级文档字符串。 | [L32](../../../../../jiuwenswarm/common/cleanup.py#L32) |
| `def _get_cutoff_timestamp() -> float` | 源码未提供函数级文档字符串。 | [L42](../../../../../jiuwenswarm/common/cleanup.py#L42) |
| `def _rmtree(path: Path) -> None` | 源码未提供函数级文档字符串。 | [L47](../../../../../jiuwenswarm/common/cleanup.py#L47) |
| `def cleanup_old_sessions() -> dict[str, int]` | 清理超过保留期的会话目录（按目录 mtime 判断，与 cc 一致）。 | [L52](../../../../../jiuwenswarm/common/cleanup.py#L52) |
| `def cleanup_orphan_file_ops() -> dict[str, int]` | 清理对应会话目录已不存在的 file_ops 日志。 | [L86](../../../../../jiuwenswarm/common/cleanup.py#L86) |
| `async def run_cleanup() -> None` | 执行一次完整的清理周期。 | [L135](../../../../../jiuwenswarm/common/cleanup.py#L135) |
| `async def cleanup_loop(stop_event: asyncio.Event, on_first_done: Callable[[], None] \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L149](../../../../../jiuwenswarm/common/cleanup.py#L149) |
| `def start_background_cleanup(on_first_done: Callable[[], None] \| None = None) -> asyncio.Task` | 源码未提供函数级文档字符串。 | [L173](../../../../../jiuwenswarm/common/cleanup.py#L173) |

## `jiuwenswarm/common/coding_memory_paths.py`

[打开源码](../../../../../jiuwenswarm/common/coding_memory_paths.py#L1)

**模块职责：** Helpers for resolving project-scoped coding memory paths.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_CODING_MEMORY_PROJECT` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/coding_memory_paths.py#L11) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve_coding_memory_project_name(project_dir: str \| PathLike[str] \| None) -> str` | Return the project-scoped directory name used under coding_memory/. | [L14](../../../../../jiuwenswarm/common/coding_memory_paths.py#L14) |
| `def resolve_project_coding_memory_dir(*, agent_workspace_dir: str \| PathLike[str], project_dir: str \| PathLike[str] \| None) -> str` | Resolve ``<agent_workspace>/coding_memory/<project_name>``. | [L30](../../../../../jiuwenswarm/common/coding_memory_paths.py#L30) |
| `def resolve_project_coding_memory_workspace_path(*, project_dir: str \| PathLike[str] \| None) -> str` | Resolve the workspace-relative ``coding_memory/<project_name>`` path. | [L43](../../../../../jiuwenswarm/common/coding_memory_paths.py#L43) |

## `jiuwenswarm/common/config.py`

[打开源码](../../../../../jiuwenswarm/common/config.py#L1)

**模块职责：** 定义 _current_config_yaml_path、get_merged_config_dict、resolve_env_vars、_normalize_config、_yaml_file_stamp、_read_with_retry 等符号。

**同名定义覆盖：** 下列较早定义已被后续同名定义覆盖，不属于当前可调用接口。

| 名称 | 被覆盖定义 | 当前生效定义 |
| --- | --- | --- |
| `normalize_permissions_tool_level` | [L1149](../../../../../jiuwenswarm/common/config.py#L1149) | [L1232](../../../../../jiuwenswarm/common/config.py#L1232) |
| `get_permissions_defaults_level` | [L1164](../../../../../jiuwenswarm/common/config.py#L1164) | [L1246](../../../../../jiuwenswarm/common/config.py#L1246) |
| `build_permissions_tools_list_view` | [L1170](../../../../../jiuwenswarm/common/config.py#L1170) | [L1252](../../../../../jiuwenswarm/common/config.py#L1252) |

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L40](../../../../../jiuwenswarm/common/config.py#L40) |
| `_CONFIG_MODULE_DIR` | `未显式标注` | [L42](../../../../../jiuwenswarm/common/config.py#L42) |
| `CONFIG_YAML_PATH` | `未显式标注` | [L43](../../../../../jiuwenswarm/common/config.py#L43) |
| `SWARMFLOW_ENABLED_CONFIG_PATH` | `未显式标注` | [L44](../../../../../jiuwenswarm/common/config.py#L44) |
| `DEFAULT_SWARMFLOW_ENABLED` | `未显式标注` | [L45](../../../../../jiuwenswarm/common/config.py#L45) |
| `_user_config` | `未显式标注` | [L47](../../../../../jiuwenswarm/common/config.py#L47) |
| `_YAML_PARSE_CACHE` | `dict[str, tuple[tuple[int, int], dict[str, Any]]]` | [L175](../../../../../jiuwenswarm/common/config.py#L175) |
| `_YAML_PARSE_CACHE_LOCK` | `未显式标注` | [L176](../../../../../jiuwenswarm/common/config.py#L176) |
| `_ConfigNsKey` | `未显式标注` | [L234](../../../../../jiuwenswarm/common/config.py#L234) |
| `_resolved_config_by_ns` | `dict[Any, tuple[float, dict[str, Any], int]]` | [L237](../../../../../jiuwenswarm/common/config.py#L237) |
| `_CONFIG_CACHE_TTL_SECONDS` | `float` | [L238](../../../../../jiuwenswarm/common/config.py#L238) |
| `_config_lock` | `未显式标注` | [L239](../../../../../jiuwenswarm/common/config.py#L239) |
| `_config_version` | `int` | [L240](../../../../../jiuwenswarm/common/config.py#L240) |
| `_CONFIG_WRITE_LOCK` | `未显式标注` | [L575](../../../../../jiuwenswarm/common/config.py#L575) |
| `_CONFIG_YAML_PATH` | `未显式标注` | [L610](../../../../../jiuwenswarm/common/config.py#L610) |
| `_load_yaml_round_trip` | `未显式标注` | [L611](../../../../../jiuwenswarm/common/config.py#L611) |
| `_dump_yaml_round_trip` | `未显式标注` | [L612](../../../../../jiuwenswarm/common/config.py#L612) |
| `_PERMISSIONS_WORKSPACE_ACCESS_AXES` | `tuple[str, ...]` | [L970](../../../../../jiuwenswarm/common/config.py#L970) |
| `_PERMISSIONS_WORKSPACE_ACCESS_LEVELS` | `frozenset[str]` | [L971](../../../../../jiuwenswarm/common/config.py#L971) |
| `_VALID_PERM_LEVEL` | `未显式标注` | [L1144](../../../../../jiuwenswarm/common/config.py#L1144) |
| `_VALID_RULE_SEVERITY` | `未显式标注` | [L1145](../../../../../jiuwenswarm/common/config.py#L1145) |
| `_RULE_MUTABLE_KEYS` | `未显式标注` | [L1146](../../../../../jiuwenswarm/common/config.py#L1146) |
| `_LEGACY_AGENT_SUBMODE_KEYS` | `tuple[str, ...]` | [L2358](../../../../../jiuwenswarm/common/config.py#L2358) |
| `_SANDBOX_RUNTIME_DEFAULTS` | `dict[str, Any]` | [L2610](../../../../../jiuwenswarm/common/config.py#L2610) |
| `_SANDBOX_RUNTIME_KEYS` | `tuple[str, ...]` | [L2620](../../../../../jiuwenswarm/common/config.py#L2620) |
| `_VALID_PRESERVE_FILE_SHARING_MODES` | `未显式标注` | [L2715](../../../../../jiuwenswarm/common/config.py#L2715) |
| `_DEFAULT_PRESERVE_FILE_SHARING_MODE` | `未显式标注` | [L2716](../../../../../jiuwenswarm/common/config.py#L2716) |
| `_VALID_SANDBOX_STARTUP_MODES` | `未显式标注` | [L2739](../../../../../jiuwenswarm/common/config.py#L2739) |
| `_DEFAULT_SANDBOX_STARTUP_MODE` | `未显式标注` | [L2740](../../../../../jiuwenswarm/common/config.py#L2740) |
| `_DEFAULT_SANDBOX_POLICY_FILE` | `未显式标注` | [L2741](../../../../../jiuwenswarm/common/config.py#L2741) |
| `_VALID_YUANRONG_EXECUTORS` | `未显式标注` | [L2744](../../../../../jiuwenswarm/common/config.py#L2744) |
| `_DEFAULT_YUANRONG_EXECUTOR` | `未显式标注` | [L2745](../../../../../jiuwenswarm/common/config.py#L2745) |
| `_DEFAULT_YUANRONG_URL` | `未显式标注` | [L2746](../../../../../jiuwenswarm/common/config.py#L2746) |
| `_YUANRONG_ENDPOINT_OPTIONAL_KEYS` | `tuple[str, ...]` | [L2747](../../../../../jiuwenswarm/common/config.py#L2747) |
| `DEFAULT_SANDBOX_STARTUP_MODE` | `未显式标注` | [L2761](../../../../../jiuwenswarm/common/config.py#L2761) |
| `DEFAULT_SANDBOX_POLICY_FILE` | `未显式标注` | [L2762](../../../../../jiuwenswarm/common/config.py#L2762) |
| `DEFAULT_YUANRONG_SANDBOX_URL` | `未显式标注` | [L2763](../../../../../jiuwenswarm/common/config.py#L2763) |
| `DEFAULT_YUANRONG_EXECUTOR` | `未显式标注` | [L2764](../../../../../jiuwenswarm/common/config.py#L2764) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _current_config_yaml_path() -> Path` | 返回用户 override config.yaml 路径（运行时可能被 JIUWENSWARM_CONFIG_DIR 重定位）。 | [L58](../../../../../jiuwenswarm/common/config.py#L58) |
| `def get_merged_config_dict() -> dict[str, Any]` | 模板与用户 override 合并后的字典（不解析环境变量）。 | [L63](../../../../../jiuwenswarm/common/config.py#L63) |
| `def resolve_env_vars(value: Any) -> Any` | 递归解析配置中的环境变量替换语法. | [L70](../../../../../jiuwenswarm/common/config.py#L70) |
| `def _normalize_config(config: dict[str, Any] \| None) -> None` | 后处理配置，将需要结构化的字符串字段解析为原生类型。 | [L124](../../../../../jiuwenswarm/common/config.py#L124) |
| `def _yaml_file_stamp(filepath: Path) -> tuple[int, int] \| None` | Return the identity a cached parse of this file is keyed on. | [L179](../../../../../jiuwenswarm/common/config.py#L179) |
| `def _read_with_retry(filepath: Path, max_attempts: int = 3) -> dict[str, Any]` | 读取 YAML，遇解析错误重试（应对跨进程写竞态）。 | [L196](../../../../../jiuwenswarm/common/config.py#L196) |
| `def _overlay_cache_key(ns: _ConfigNsKey, overlay: dict[str, Any] \| None) -> Any` | Build get_config cache key; overlay content hash avoids id() reuse. | [L243](../../../../../jiuwenswarm/common/config.py#L243) |
| `def _collect_expired_config_cache_keys(entries: dict[Any, tuple[float, dict[str, Any], int]], *, cleanup_now: float, config_version: int) -> list[Any]` | Return cache keys that are TTL-expired or tied to an old config version. | [L253](../../../../../jiuwenswarm/common/config.py#L253) |
| `def get_config()` | Return merged, env-var-resolved config with per-ns TTL cache. | [L270](../../../../../jiuwenswarm/common/config.py#L270) |
| `def clear_config_cache(service_id: str \| None = None, agent_id: str \| None = None) -> None` | Invalidate resolved-config cache (all slots or one ns). | [L330](../../../../../jiuwenswarm/common/config.py#L330) |
| `def get_config_raw()` | 合并包内模板与用户 override 后的快照（不解析环境变量）。 | [L371](../../../../../jiuwenswarm/common/config.py#L371) |
| `def get_default_model_provider(config: dict[str, Any] \| None) -> str` | 源码未提供函数级文档字符串。 | [L380](../../../../../jiuwenswarm/common/config.py#L380) |
| `def validate_persisted_kv_cache_affinity() -> tuple[bool, list[str]]` | Validate the persisted KVC invariant after a config write. | [L384](../../../../../jiuwenswarm/common/config.py#L384) |
| `def set_config(config)` | 源码未提供函数级文档字符串。 | [L390](../../../../../jiuwenswarm/common/config.py#L390) |
| `def _get_bool_env(value: str \| None) -> bool \| None` | 源码未提供函数级文档字符串。 | [L396](../../../../../jiuwenswarm/common/config.py#L396) |
| `def _get_evolution_config(config: dict[str, Any] \| None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L402](../../../../../jiuwenswarm/common/config.py#L402) |
| `def get_evolution_review_trigger_enabled(config: dict[str, Any] \| None, *, fallback: bool = False) -> bool` | Return whether review follow-ups are enabled. | [L414](../../../../../jiuwenswarm/common/config.py#L414) |
| `def get_evolution_signal_trigger_enabled(config: dict[str, Any] \| None, *, fallback: bool = True) -> bool` | Return whether passive signal-based evolution scans are enabled. | [L434](../../../../../jiuwenswarm/common/config.py#L434) |
| `def get_passive_skill_evolution_triggers(config: dict[str, Any] \| None) -> dict[str, bool]` | Return trigger flags for single-agent and teammate passive evolution. | [L461](../../../../../jiuwenswarm/common/config.py#L461) |
| `def get_skill_evolution_enabled(config: dict[str, Any] \| None) -> bool` | Return the canonical ``react.evolution.skill_evolution`` switch. | [L476](../../../../../jiuwenswarm/common/config.py#L476) |
| `def get_evolution_enabled(config: dict[str, Any] \| None) -> bool` | Return whether skill self-evolution is enabled. | [L488](../../../../../jiuwenswarm/common/config.py#L488) |
| `def get_skill_create_enabled(config: dict[str, Any] \| None) -> bool` | 源码未提供函数级文档字符串。 | [L496](../../../../../jiuwenswarm/common/config.py#L496) |
| `def get_evolution_auto_save_enabled(config: dict[str, Any] \| None = None) -> bool` | Return whether evolution approvals may auto-save without user action. | [L504](../../../../../jiuwenswarm/common/config.py#L504) |
| `def set_auto_memory_enabled(enabled: bool) -> None` | Set auto-memory enabled status in config. | [L516](../../../../../jiuwenswarm/common/config.py#L516) |
| `def load_yaml_round_trip(config_path: Path)` | ruamel 加载 config，保留注释与格式。 | [L527](../../../../../jiuwenswarm/common/config.py#L527) |
| `def dump_yaml_round_trip(config_path: Path, data: Any) -> None` | ruamel 写回 config，保留注释与格式（原子写入：临时文件 + os.replace）。 | [L535](../../../../../jiuwenswarm/common/config.py#L535) |
| `def _atomic_replace(src: Path, dst: Path, max_attempts: int = 10) -> None` | os.replace 重试：应对 Windows 下目标文件被并发占用导致的 PermissionError。 | [L556](../../../../../jiuwenswarm/common/config.py#L556) |
| `def _config_lock_path(config_path: Path) -> Path` | 锁文件路径跟随当前 CONFIG_YAML_PATH，避免模块加载时静态绑定。 | [L578](../../../../../jiuwenswarm/common/config.py#L578) |
| `def update_config(mutator, *, lock_timeout: float = 10.0) -> Any` | 跨进程互斥地读-改-写 config.yaml。 | [L583](../../../../../jiuwenswarm/common/config.py#L583) |
| `def update_heartbeat_in_config(payload: dict[str, Any]) -> None` | 只更新 heartbeat 段并写回。 | [L615](../../../../../jiuwenswarm/common/config.py#L615) |
| `def update_channel_in_config(channel_id: str, conf: dict[str, Any]) -> None` | 只更新 channels[channel_id] 并写回。 | [L630](../../../../../jiuwenswarm/common/config.py#L630) |
| `def _as_plain_yaml_str(value: Any) -> Any` | 字符串写成无引号 plain scalar，避免顶层/apps 因旧值风格（如 ``''``）不一致。 | [L644](../../../../../jiuwenswarm/common/config.py#L644) |
| `def update_xiaoyi_runtime_in_config(conf: dict[str, Any], *, api_id: str = '', agent_id: str = '') -> None` | 更新 ``channels.xiaoyi`` 运行时身份，并在存在 ``push_id`` 时同步写入 ``apps[]``。 | [L651](../../../../../jiuwenswarm/common/config.py#L651) |
| `def update_channel_subsection_in_config(channel_id: str, subsection_id: str, conf: dict[str, Any] \| list[Any] \| Any) -> None` | 更新 channels[channel_id][subsection_id] 并写回。 | [L705](../../../../../jiuwenswarm/common/config.py#L705) |
| `def replace_channel_subsection_with_cleanup(channel_id: str, subsection_id: str, conf: dict[str, Any] \| list[Any] \| Any, keep_keys: set[str]) -> None` | 整体替换 channels[channel_id][subsection_id] 并清理旧字段，一次 IO 完成。 | [L735](../../../../../jiuwenswarm/common/config.py#L735) |
| `def update_channel_app_field(channel_id: str, app_identifier: str, field_values: dict[str, Any], *, app_id_key: str = 'app_id') -> bool` | 更新 ``channels[channel_id].apps`` 列表中某个 app 条目的字段。 | [L763](../../../../../jiuwenswarm/common/config.py#L763) |
| `def update_preferred_language_in_config(lang: str) -> None` | 只更新顶层 preferred_language 并写回。非法值回退为 zh，与 set_preferred_language_in_config_file 一致。 | [L796](../../../../../jiuwenswarm/common/config.py#L796) |
| `def set_preferred_language_in_config_file(config_path: Path, lang: str) -> None` | 将 preferred_language 写入指定 config.yaml（用于 init 等尚未绑定全局路径的场景）。 | [L806](../../../../../jiuwenswarm/common/config.py#L806) |
| `def update_browser_in_config(updates: dict[str, Any]) -> None` | 只更新 browser 段（如 chrome_path）并写回。 | [L818](../../../../../jiuwenswarm/common/config.py#L818) |
| `def update_evolution_enabled_in_config(value: bool) -> None` | 更新 react.evolution.enabled（Skills 自演进总开关）并写回用户 override。 | [L829](../../../../../jiuwenswarm/common/config.py#L829) |
| `def update_context_engine_enabled_in_config(value: bool) -> None` | 更新 react.context_engine_config.enabled（上下文压缩开关）并写回。 | [L844](../../../../../jiuwenswarm/common/config.py#L844) |
| `def update_kv_cache_affinity_enabled_in_config(value: bool) -> None` | 更新 react.kv_cache_affinity_config.enable_kv_cache_affinity 并写回。 | [L856](../../../../../jiuwenswarm/common/config.py#L856) |
| `def update_kv_cache_release_enabled_in_config(value: bool) -> None` | 更新 react.kv_cache_affinity_config.enable_kv_cache_release 并写回。 | [L870](../../../../../jiuwenswarm/common/config.py#L870) |
| `def _merge_config_dict(target: dict[str, Any], patch: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L884](../../../../../jiuwenswarm/common/config.py#L884) |
| `def update_symphony_in_config(updates: dict[str, Any]) -> None` | 更新 symphony 配置段并写回。 | [L896](../../../../../jiuwenswarm/common/config.py#L896) |
| `def update_skill_retrieval_in_config(updates: dict[str, Any]) -> None` | 更新 symphony.skill_retrieval 配置段并写回。 | [L906](../../../../../jiuwenswarm/common/config.py#L906) |
| `def update_permissions_enabled_in_config(value: bool) -> None` | 更新 permissions.enabled（工具安全护栏开关）并写回。 | [L919](../../../../../jiuwenswarm/common/config.py#L919) |
| `def _effective_permissions() -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L927](../../../../../jiuwenswarm/common/config.py#L927) |
| `def _persist_permissions(mutate_fn) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L935](../../../../../jiuwenswarm/common/config.py#L935) |
| `def get_permissions_file_guard_workspace_rw_enabled() -> bool` | 读取 ``permissions.file_guard.workspace.rw_enabled``（缺省为 True）。 | [L943](../../../../../jiuwenswarm/common/config.py#L943) |
| `def update_permissions_file_guard_workspace_rw_enabled_in_config(value: bool) -> None` | 更新 ``permissions.file_guard.workspace.rw_enabled`` 并写回。 | [L954](../../../../../jiuwenswarm/common/config.py#L954) |
| `def get_permissions_file_guard_workspace_access() -> dict[str, str]` | 读取 ``permissions.file_guard.workspace`` 的 read/write/exec 三轴。 | [L974](../../../../../jiuwenswarm/common/config.py#L974) |
| `def update_permissions_file_guard_workspace_access_in_config(axis: dict[str, Any]) -> dict[str, str]` | 更新 ``permissions.file_guard.workspace`` 的 read/write/exec 三轴并写回。 | [L1004](../../../../../jiuwenswarm/common/config.py#L1004) |
| `def update_auto_recap_enabled_in_config(value: bool) -> None` | 更新 auto_recap.enabled（自动回顾开关）并写回。 | [L1038](../../../../../jiuwenswarm/common/config.py#L1038) |
| `def update_setup_guide_enabled_in_config(value: bool) -> None` | 原子更新 setup_guide.enabled（Web 首次配置引导开关）。 | [L1047](../../../../../jiuwenswarm/common/config.py#L1047) |
| `def update_proactive_recommendation_in_config(updates: dict[str, Any]) -> None` | 更新 proactive_recommendation 配置段并写回。 | [L1060](../../../../../jiuwenswarm/common/config.py#L1060) |
| `def update_updater_in_config(updates: dict[str, Any]) -> None` | 只更新 updater 段并写回。 | [L1070](../../../../../jiuwenswarm/common/config.py#L1070) |
| `def update_memory_enabled_in_config(mode: str, value: bool) -> None` | 更新 memory.enabled（记忆系统开关）并写回。 | [L1081](../../../../../jiuwenswarm/common/config.py#L1081) |
| `def update_proactive_memory_in_config(mode: str, value: bool) -> None` | 更新 memory.proactive_memory（主动记忆开关）并写回。 | [L1086](../../../../../jiuwenswarm/common/config.py#L1086) |
| `def _update_memory_in_modes_config(mode: str, item: str, value: bool) -> None` | 源码未提供函数级文档字符串。 | [L1091](../../../../../jiuwenswarm/common/config.py#L1091) |
| `def get_permissions_owner_scopes() -> dict[str, Any]` | 读取 permissions.owner_scopes 及 deny_guidance_message. | [L1107](../../../../../jiuwenswarm/common/config.py#L1107) |
| `def update_permissions_owner_scopes_in_config(owner_scopes: dict[str, Any], deny_guidance_message: str \| None = None) -> None` | 更新 permissions.owner_scopes（及可选 deny_guidance_message）并写回。 | [L1116](../../../../../jiuwenswarm/common/config.py#L1116) |
| `def get_permissions_deny_guidance() -> str` | 读取 permissions.deny_guidance_message. | [L1129](../../../../../jiuwenswarm/common/config.py#L1129) |
| `def update_permissions_deny_guidance_in_config(msg: str) -> None` | 更新 permissions.deny_guidance_message 并写回。 | [L1134](../../../../../jiuwenswarm/common/config.py#L1134) |
| `def get_permissions_tools() -> dict[str, Any]` | 返回 ``permissions.tools``（原始结构，可能含 legacy dict）。 | [L1224](../../../../../jiuwenswarm/common/config.py#L1224) |
| `def normalize_permissions_tool_level(raw: Any) -> str \| None` | Normalize a configured tool level for UI display. | [L1232](../../../../../jiuwenswarm/common/config.py#L1232) |
| `def get_permissions_defaults_level() -> str` | Return the effective default tool level as ``allow\|ask\|deny``. | [L1246](../../../../../jiuwenswarm/common/config.py#L1246) |
| `def build_permissions_tools_list_view(catalog_by_name: dict[str, dict[str, str]] \| None = None) -> dict[str, Any]` | Build the permissions list from runtime and explicitly configured tools. | [L1252](../../../../../jiuwenswarm/common/config.py#L1252) |
| `def replace_permissions_tools_in_config(tools: Any) -> None` | 整表替换 ``permissions.tools``；值仅允许 ``allow\|ask\|deny``（或 legacy ``{"*": level}``）。 | [L1313](../../../../../jiuwenswarm/common/config.py#L1313) |
| `def update_permissions_tool_in_config(tool_name: str, level: Any) -> dict[str, Any]` | 合并单条工具级别到 ``permissions.tools`` 并写回。 | [L1323](../../../../../jiuwenswarm/common/config.py#L1323) |
| `def delete_permissions_tool_in_config(tool_name: str) -> bool` | 从 ``permissions.tools`` 中删除一个键；不存在则返回 False。 | [L1352](../../../../../jiuwenswarm/common/config.py#L1352) |
| `def _validate_tools_map(tools: Any) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L1377](../../../../../jiuwenswarm/common/config.py#L1377) |
| `def get_permissions_rules() -> dict[str, Any]` | 返回 ``permissions.rules`` 列表（仅 dict 项）。 | [L1397](../../../../../jiuwenswarm/common/config.py#L1397) |
| `def get_permissions_approval_overrides() -> dict[str, Any]` | 返回 ``permissions.approval_overrides`` 列表（仅 dict 项）。 | [L1405](../../../../../jiuwenswarm/common/config.py#L1405) |
| `def create_permissions_rule_in_config(rule: dict[str, Any]) -> dict[str, Any]` | 追加一条 ``permissions.rules`` 项，返回落盘后的规则（含 ``id``）。 | [L1413](../../../../../jiuwenswarm/common/config.py#L1413) |
| `def update_permissions_rule_in_config(rule_id: str, patch: dict[str, Any]) -> dict[str, Any]` | 按 ``id`` 合并更新一条 rule。 | [L1445](../../../../../jiuwenswarm/common/config.py#L1445) |
| `def delete_permissions_rule_in_config(rule_id: str) -> bool` | 删除 ``permissions.rules`` 中指定 ``id``；若未找到返回 False。 | [L1495](../../../../../jiuwenswarm/common/config.py#L1495) |
| `def delete_permissions_approval_override_in_config(override_id: str) -> bool` | 按 ``id`` 删除 ``approval_overrides`` 中一项；若未找到返回 False。 | [L1519](../../../../../jiuwenswarm/common/config.py#L1519) |
| `def _normalize_rule_tools(raw: Any) -> list[str]` | 源码未提供函数级文档字符串。 | [L1543](../../../../../jiuwenswarm/common/config.py#L1543) |
| `def _normalize_rule_severity_action(rule: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L1552](../../../../../jiuwenswarm/common/config.py#L1552) |
| `def _parse_custom_headers(value: str \| dict \| None) -> dict[str, Any] \| None` | 解析 custom_headers 配置，支持 JSON 字符串格式或已解析的字典。 | [L1565](../../../../../jiuwenswarm/common/config.py#L1565) |
| `def _infer_is_default(entries: list[dict[str, Any]]) -> list[dict[str, Any]]` | 为模型条目列表推断 is_default 字段。 | [L1591](../../../../../jiuwenswarm/common/config.py#L1591) |
| `def _decrypt_model_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]` | 解密模型条目中的 api_key 字段，返回深拷贝不改变原始数据。同时推断 is_default。 | [L1637](../../../../../jiuwenswarm/common/config.py#L1637) |
| `def get_default_models(config: dict[str, Any] \| None = None) -> list[dict[str, Any]]` | 获取默认模型列表，兼容新旧格式。 | [L1673](../../../../../jiuwenswarm/common/config.py#L1673) |
| `def update_default_models_in_config(models_list: list[dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L1710](../../../../../jiuwenswarm/common/config.py#L1710) |
| `def update_default_model_provider_in_config(provider: str) -> bool` | Update only the default model provider in config.yaml. | [L1724](../../../../../jiuwenswarm/common/config.py#L1724) |
| `def ensure_defaults_list_in_config() -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L1766](../../../../../jiuwenswarm/common/config.py#L1766) |
| `def _require_dict(value: Any, field_name: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1801](../../../../../jiuwenswarm/common/config.py#L1801) |
| `def _require_non_empty_string(value: Any, field_name: str) -> str` | 源码未提供函数级文档字符串。 | [L1807](../../../../../jiuwenswarm/common/config.py#L1807) |
| `def resolve_legacy_team_model_ref(model_raw: dict[str, Any], config_data: dict[str, Any] \| None) -> str \| None` | 源码未提供函数级文档字符串。 | [L1814](../../../../../jiuwenswarm/common/config.py#L1814) |
| `def _transform_front_team_model_config(model_raw: dict[str, Any], config_data: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1858](../../../../../jiuwenswarm/common/config.py#L1858) |
| `def _transform_front_team_agent_spec(agent_key: str, agent_raw: Any, config_data: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1889](../../../../../jiuwenswarm/common/config.py#L1889) |
| `def _resolve_front_team_agent_spec(agents_raw: dict[str, Any], agent_key: Any, *, field_name: str, config_data: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1910](../../../../../jiuwenswarm/common/config.py#L1910) |
| `def _build_modes_team_mapping(front_payload: dict[str, Any], config_data: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1923](../../../../../jiuwenswarm/common/config.py#L1923) |
| `def _build_front_agent_registry(front_payload: dict[str, Any], config_data: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L2029](../../../../../jiuwenswarm/common/config.py#L2029) |
| `def replace_teams_in_config(front_payload: dict[str, Any]) -> None` | Replace ``modes.team`` using the frontend team-editor payload. | [L2041](../../../../../jiuwenswarm/common/config.py#L2041) |
| `def _ensure_config_object(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L2093](../../../../../jiuwenswarm/common/config.py#L2093) |
| `def update_swarmflow_enabled_in_config(enabled: bool) -> None` | Update ``modes.team.jiuwen_team.enable_swarmflow`` in config.yaml. | [L2103](../../../../../jiuwenswarm/common/config.py#L2103) |
| `def get_mcp_servers() -> list[dict[str, Any]]` | 读取 config.yaml 中的 mcp.servers（原始结构，不解析环境变量）。 | [L2115](../../../../../jiuwenswarm/common/config.py#L2115) |
| `def upsert_mcp_server_in_config(server: dict[str, Any]) -> tuple[dict[str, Any], bool]` | 新增或更新 mcp.servers 条目，返回（条目, 是否创建）。 | [L2127](../../../../../jiuwenswarm/common/config.py#L2127) |
| `def set_mcp_server_enabled_in_config(name: str, enabled: bool) -> dict[str, Any]` | 切换 mcp.servers 指定 name 的 enabled 状态并返回更新后的条目。 | [L2156](../../../../../jiuwenswarm/common/config.py#L2156) |
| `def get_mcp_server_config(name: str) -> dict[str, Any] \| None` | 按名称读取单个 mcp server 配置（原始结构）。 | [L2178](../../../../../jiuwenswarm/common/config.py#L2178) |
| `def remove_mcp_server_in_config(name: str) -> dict[str, Any]` | 删除指定 mcp server 配置并返回被删除的条目。 | [L2189](../../../../../jiuwenswarm/common/config.py#L2189) |
| `def upsert_subagent_in_config(name: str, enabled: bool = True) -> None` | 在 react.subagents.<name> 中添加或更新 agent 启用状态。 | [L2218](../../../../../jiuwenswarm/common/config.py#L2218) |
| `def remove_subagent_from_config(name: str) -> bool` | 从 react.subagents.<name> 中删除 agent 条目。 | [L2240](../../../../../jiuwenswarm/common/config.py#L2240) |
| `def update_memory_forbidden_enabled_in_config(value: bool) -> None` | 更新 memory.forbidden_memory_definition.enabled（记忆系统敏感信息过滤开关）并写回。 | [L2264](../../../../../jiuwenswarm/common/config.py#L2264) |
| `def update_memory_forbidden_description_in_config(description: dict[str, str]) -> None` | 更新 memory.forbidden_memory_definition.description（禁止记忆内容描述）并写回。 | [L2275](../../../../../jiuwenswarm/common/config.py#L2275) |
| `def update_memory_forbidden_in_config(updates: dict[str, Any]) -> None` | 更新 memory.forbidden_memory_definition 并写回。 | [L2293](../../../../../jiuwenswarm/common/config.py#L2293) |
| `def update_a2ui_in_config(updates: dict[str, Any]) -> None` | 更新 a2ui 配置段并写回 config.yaml。 | [L2309](../../../../../jiuwenswarm/common/config.py#L2309) |
| `def _deep_merge(template: dict[str, Any], user: dict[str, Any], depth: int = 0) -> dict[str, Any]` | Recursively merge template with user config, cleaning deprecated fields. | [L2320](../../../../../jiuwenswarm/common/config.py#L2320) |
| `def _migrate_legacy_agent_submode_memory(user_data: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L2361](../../../../../jiuwenswarm/common/config.py#L2361) |
| `def migrate_config_from_template(template_path: Path, user_config_path: Path) -> bool` | Sync user config with template structure, preserving user values. | [L2389](../../../../../jiuwenswarm/common/config.py#L2389) |
| `def _prune_override_keys(template: dict[str, Any], override: dict[str, Any], depth: int = 0) -> dict[str, Any]` | 递归清理 override 中模板不存在的字段（Remove 规则）。 | [L2446](../../../../../jiuwenswarm/common/config.py#L2446) |
| `def cleanup_override_against_template(template_path: Path, user_config_path: Path) -> bool` | 清理用户 override 中模板已删除的废弃字段。 | [L2466](../../../../../jiuwenswarm/common/config.py#L2466) |
| `def get_model_names() -> list[str]` | 获取可切换的模型名称列表。优先从 models.defaults 列表读取。 | [L2502](../../../../../jiuwenswarm/common/config.py#L2502) |
| `def add_or_update_model_in_config(name: str, model_config: dict[str, Any]) -> None` | 新增或更新一个模型配置，写入 config.yaml 的 models.<name> 节点。 | [L2530](../../../../../jiuwenswarm/common/config.py#L2530) |
| `def get_model_config(name: str, index: int \| None = None) -> dict[str, Any] \| None` | 获取指定模型的原始配置（不解析环境变量）。 | [L2547](../../../../../jiuwenswarm/common/config.py#L2547) |
| `def _coerce_optional_positive_int(value: Any, *, field: str, allow_zero: bool = False) -> Optional[int]` | 把 yaml/json 来的 idle 配置值归一化为 ``Optional[int]``. | [L2623](../../../../../jiuwenswarm/common/config.py#L2623) |
| `def _ensure_sandbox_runtime_shape(runtime: Any) -> dict[str, Any]` | 填充 sandbox runtime 缺省字段，返回归一化后的 dict（不写盘）。 | [L2663](../../../../../jiuwenswarm/common/config.py#L2663) |
| `def get_sandbox_runtime() -> dict[str, Any]` | 返回 sandbox runtime 当前内容 (含缺省字段填充)。 | [L2700](../../../../../jiuwenswarm/common/config.py#L2700) |
| `def _normalize_preserve_file_sharing_mode(value: Any) -> str \| None` | 归一化 ``sandbox.preserve_file_sharing_mode``. | [L2719](../../../../../jiuwenswarm/common/config.py#L2719) |
| `def _normalize_yuanrong_executor(value: Any) -> str` | 归一化 ``sandbox.executor``; 非法或空值回落到默认 ``docker``. | [L2767](../../../../../jiuwenswarm/common/config.py#L2767) |
| `def _normalize_sandbox_startup_mode(value: Any) -> str` | 归一化 ``sandbox.startup_mode``; 非法或空值回落到默认 ``internal``. | [L2775](../../../../../jiuwenswarm/common/config.py#L2775) |
| `def get_sandbox_startup_mode() -> str` | 返回 ``sandbox.startup_mode``: ``internal`` (agent-server 拉起 jiuwenbox) 或 ``external`` (用户自己启动 jiuwenbox)。 | [L2783](../../../../../jiuwenswarm/common/config.py#L2783) |
| `def get_sandbox_startup_mode_explicit() -> str \| None` | 同 :func:`get_sandbox_startup_mode`, 但仅返回 ``config.yaml`` 里**显式** 写出的合法值 (``internal`` / ``external``); 未配置 / 空串 / 非法值都返回 ``None``。 | [L2794](../../../../../jiuwenswarm/common/config.py#L2794) |
| `def update_sandbox_startup_mode(mode: str) -> str` | 写入 ``sandbox.startup_mode`` 到 config.yaml; 返回归一化后的值。 | [L2816](../../../../../jiuwenswarm/common/config.py#L2816) |
| `def _looks_like_bare_filename(value: str) -> bool` | ``True`` 表示参数应该被解释为 ``jiuwenbox/configs/`` 下的文件名。 | [L2831](../../../../../jiuwenswarm/common/config.py#L2831) |
| `def _jiuwenbox_configs_dir() -> Path \| None` | 探测仓库或安装位置上的 ``jiuwenbox/configs/`` 目录。 | [L2841](../../../../../jiuwenswarm/common/config.py#L2841) |
| `def resolve_sandbox_policy_path(value: str \| None) -> Path \| None` | 把 ``sandbox.policy_file`` 的取值解析为宿主机绝对路径。 | [L2879](../../../../../jiuwenswarm/common/config.py#L2879) |
| `def get_sandbox_policy_file() -> str` | 返回 ``sandbox.policy_file`` 原始字符串 (空表示未配置, 由调用方走默认)。 | [L2902](../../../../../jiuwenswarm/common/config.py#L2902) |
| `def get_sandbox_policy_path() -> Path \| None` | 返回 ``sandbox.policy_file`` 解析后的绝对路径。 | [L2909](../../../../../jiuwenswarm/common/config.py#L2909) |
| `def update_sandbox_policy_file(value: str) -> str` | 写入 ``sandbox.policy_file`` (仅文件名或绝对路径) 到 config.yaml; 返回归一化后的字符串。 | [L2919](../../../../../jiuwenswarm/common/config.py#L2919) |
| `def get_sandbox_endpoint() -> dict[str, Any]` | 返回 ``sandbox.url`` / ``sandbox.type`` / ``sandbox.preserve_file_sharing_mode`` / ``sandbox.startup_mode`` / ``sandbox.policy_file``, 以及 yuanrong 可选 knobs。 | [L2932](../../../../../jiuwenswarm/common/config.py#L2932) |
| `def update_sandbox_endpoint(url: str, sandbox_type: str, *, preserve_file_sharing_mode: str \| None = None, startup_mode: str \| None = None, policy_file: str \| None = None, executor: str \| None = None, image: str \| None = None, workdir: str \| None = None, mounts: list \| None = None, cpu: int \| None = None, cpu_limit: int \| None = None, memory: int \| None = None, mem_limit: int \| None = None, rootfs: dict \| None = None) -> dict[str, Any]` | 写入 ``sandbox.url`` / ``sandbox.type`` 以及可选的 ``preserve_file_sharing_mode`` / ``startup_mode`` / ``policy_file`` / yuanrong knobs 到 config.yaml; 返回实际写入的字段集合 (没有改动的字段不在返回里)。 | [L2972](../../../../../jiuwenswarm/common/config.py#L2972) |
| `def get_sandbox_preserve_file_sharing_mode() -> str \| None` | 返回 ``sandbox.preserve_file_sharing_mode`` (当前仅 ``"mount"``). | [L3076](../../../../../jiuwenswarm/common/config.py#L3076) |
| `def resolve_preserve_file_sharing_mode_default() -> str` | 返回当前可用的 ``preserve_file_sharing_mode``. | [L3087](../../../../../jiuwenswarm/common/config.py#L3087) |
| `def update_sandbox_preserve_file_sharing_mode(mode: str) -> str` | 写入 ``sandbox.preserve_file_sharing_mode``; 返回归一化后的值. | [L3097](../../../../../jiuwenswarm/common/config.py#L3097) |
| `def update_sandbox_runtime(patch: dict[str, Any]) -> dict[str, Any]` | 合并 patch 到 sandbox runtime 字段, 写回 YAML, 返回合并后的完整 runtime. | [L3116](../../../../../jiuwenswarm/common/config.py#L3116) |
| `def _parse_comma_separated_string(raw: str) -> list[str]` | Split a comma/semicolon-separated string into stripped non-empty items. | [L3176](../../../../../jiuwenswarm/common/config.py#L3176) |
| `def resolve_string_or_list_config(value: Any) -> list[str]` | Resolve a config field that can be either a YAML list or an env string. | [L3183](../../../../../jiuwenswarm/common/config.py#L3183) |

## `jiuwenswarm/common/context_keys.py`

[打开源码](../../../../../jiuwenswarm/common/context_keys.py#L1)

**模块职责：** Shared keys for runtime callback/context dictionaries.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `JIUWENSWARM_CHANNEL_CONTEXT_KEY` | `未显式标注` | [L10](../../../../../jiuwenswarm/common/context_keys.py#L10) |
| `__all__` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/context_keys.py#L13) |

## `jiuwenswarm/common/cron_team_completion.py`

[打开源码](../../../../../jiuwenswarm/common/cron_team_completion.py#L1)

**模块职责：** Shared cron team round completion signals for gateway and agent server.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `CRON_LEADER_PLACEHOLDER_MARKERS` | `未显式标注` | [L8](../../../../../jiuwenswarm/common/cron_team_completion.py#L8) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_cron_leader_placeholder_text(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L15](../../../../../jiuwenswarm/common/cron_team_completion.py#L15) |
| `def _is_cron_leader_final_event(event: dict[str, Any]) -> bool` | Only the team leader's chat.final counts toward cron completion. | [L22](../../../../../jiuwenswarm/common/cron_team_completion.py#L22) |
| `def new_cron_team_round_state() -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L33](../../../../../jiuwenswarm/common/cron_team_completion.py#L33) |
| `def cron_team_round_has_open_tasks(state: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L48](../../../../../jiuwenswarm/common/cron_team_completion.py#L48) |
| `def cron_team_round_has_active_members(state: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L53](../../../../../jiuwenswarm/common/cron_team_completion.py#L53) |
| `def cron_team_round_has_result_text(state: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L58](../../../../../jiuwenswarm/common/cron_team_completion.py#L58) |
| `def _harness_round_can_end(state: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L64](../../../../../jiuwenswarm/common/cron_team_completion.py#L64) |
| `def _cron_solo_harness_end_pending(state: dict[str, Any]) -> bool` | True when harness-style completion is imminent but tasks may still be delegated. | [L77](../../../../../jiuwenswarm/common/cron_team_completion.py#L77) |
| `async def _drain_cron_delegation_grace_events(*, request_queue: asyncio.Queue, round_state: dict[str, Any], grace_seconds: float = 2.0) -> list[dict[str, Any]]` | Wait briefly after a solo harness final in case task.created events follow. | [L93](../../../../../jiuwenswarm/common/cron_team_completion.py#L93) |
| `def cron_team_round_should_end(state: dict[str, Any], *, chunk_complete: bool = False) -> bool` | 源码未提供函数级文档字符串。 | [L117](../../../../../jiuwenswarm/common/cron_team_completion.py#L117) |
| `def _apply_team_task_event(state: dict[str, Any], nested: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L141](../../../../../jiuwenswarm/common/cron_team_completion.py#L141) |
| `def _apply_team_member_event(state: dict[str, Any], nested: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L160](../../../../../jiuwenswarm/common/cron_team_completion.py#L160) |
| `def _extract_workflow_summary(event: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L178](../../../../../jiuwenswarm/common/cron_team_completion.py#L178) |
| `def apply_cron_team_round_event(state: dict[str, Any], event: dict[str, Any]) -> None` | Update round completion state from a team stream event. | [L191](../../../../../jiuwenswarm/common/cron_team_completion.py#L191) |

## `jiuwenswarm/common/debug_dump.py`

[打开源码](../../../../../jiuwenswarm/common/debug_dump.py#L1)

**模块职责：** Live async-state dump for diagnosing coroutine stalls and deadlocks.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L34](../../../../../jiuwenswarm/common/debug_dump.py#L34) |
| `_SYNC_PRIMITIVE_TYPES` | `未显式标注` | [L36](../../../../../jiuwenswarm/common/debug_dump.py#L36) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _write_thread_stacks(out: TextIO) -> None` | 源码未提供函数级文档字符串。 | [L39](../../../../../jiuwenswarm/common/debug_dump.py#L39) |
| `def _collect_async_objects() -> tuple[list[asyncio.Task], list[object], list[asyncio.Queue]]` | Scan the gc heap once for tasks, sync primitives and queues. | [L47](../../../../../jiuwenswarm/common/debug_dump.py#L47) |
| `def _write_tasks(out: TextIO, tasks: list[asyncio.Task]) -> None` | 源码未提供函数级文档字符串。 | [L69](../../../../../jiuwenswarm/common/debug_dump.py#L69) |
| `def _write_waiting_primitives(out: TextIO, primitives: list[object], queues: list[asyncio.Queue]) -> None` | 源码未提供函数级文档字符串。 | [L85](../../../../../jiuwenswarm/common/debug_dump.py#L85) |
| `def dump_async_state(service_name: str) -> Path \| None` | Write a full thread/coroutine snapshot to the dump directory. | [L108](../../../../../jiuwenswarm/common/debug_dump.py#L108) |
| `def install_async_dump_handler(service_name: str) -> None` | Register a SIGUSR1 handler that snapshots live async state to a file. | [L143](../../../../../jiuwenswarm/common/debug_dump.py#L143) |

## `jiuwenswarm/common/e2a/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/__init__.py#L1)

**模块职责：** E2A（Everything-to-Agent）：统一信封；ACP / A2A 等经转换进入 E2A，并由 provenance 记录出处。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L79](../../../../../jiuwenswarm/common/e2a/__init__.py#L79) |

## `jiuwenswarm/common/e2a/acp/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/acp/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/e2a/acp/__init__.py#L12) |

## `jiuwenswarm/common/e2a/acp/acp_tool_updates.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L1)

**模块职责：** 定义 is_reasoning_event、normalize_tool_name、build_acp_tool_descriptor、build_acp_tool_call_update、build_acp_tool_result_update、build_acp_todo_update 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_TOOL_NAME_ALIASES` | `未显式标注` | [L10](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L10) |
| `_LIST_TOOL_ALIASES` | `未显式标注` | [L23](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L23) |
| `_SEARCH_TOOL_NAMES` | `未显式标注` | [L36](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L36) |
| `_READ_TOOL_NAMES` | `未显式标注` | [L50](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L50) |
| `_EDIT_TOOL_NAMES` | `未显式标注` | [L69](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L69) |
| `_DELETE_TOOL_NAMES` | `未显式标注` | [L82](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L82) |
| `_MOVE_TOOL_NAMES` | `未显式标注` | [L83](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L83) |
| `_EXECUTE_TOOL_NAMES` | `未显式标注` | [L84](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L84) |
| `_FETCH_TOOL_NAMES` | `未显式标注` | [L98](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L98) |
| `_TERMINAL_CREATE_TOOL_NAMES` | `未显式标注` | [L99](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L99) |
| `_TERMINAL_WAIT_EXIT_TOOL_NAMES` | `未显式标注` | [L100](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L100) |
| `_PATH_KEYS` | `未显式标注` | [L102](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L102) |
| `__all__` | `未显式标注` | [L554](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L554) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_reasoning_event(event_type: Any, payload: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L115](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L115) |
| `def normalize_tool_name(tool_name: str) -> str` | 源码未提供函数级文档字符串。 | [L124](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L124) |
| `def build_acp_tool_descriptor(tool_name: str, arguments: Any, *, tool_call_id: str, status: str \| None = None, raw_output: Any = None, title: str \| None = None, kind: str \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L129](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L129) |
| `def build_acp_tool_call_update(payload: dict[str, Any], cache: dict[str, dict[str, Any]] \| None = None) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L161](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L161) |
| `def build_acp_tool_result_update(payload: dict[str, Any], cache: dict[str, dict[str, Any]] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L203](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L203) |
| `def build_acp_todo_update(payload: dict[str, Any]) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L277](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L277) |
| `def _resolve_tool_call_id(payload: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L295](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L295) |
| `def _legacy_arguments(arguments: Any) -> Any` | 源码未提供函数级文档字符串。 | [L304](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L304) |
| `def _normalize_arguments(arguments: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L311](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L311) |
| `def _infer_tool_kind(tool_name: str) -> str` | 源码未提供函数级文档字符串。 | [L324](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L324) |
| `def _first_url_value(arguments: dict[str, Any]) -> str \| None` | Resolve the ``url`` argument to a displayable string. | [L343](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L343) |
| `def _url_list(arguments: dict[str, Any]) -> list[str]` | Return the ``url`` argument as a list of non-empty strings. | [L359](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L359) |
| `def _build_tool_title(tool_name: str, arguments: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L369](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L369) |
| `def _build_content_blocks(result: Any) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L423](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L423) |
| `def _build_terminal_content_blocks(terminal_id: str \| None) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L438](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L438) |
| `def _extract_locations(arguments: dict[str, Any]) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L444](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L444) |
| `def _location_from_value(value: Any, *, line: int \| None) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L472](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L472) |
| `def _resolve_line(arguments: dict[str, Any]) -> int \| None` | 源码未提供函数级文档字符串。 | [L481](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L481) |
| `def _first_string_value(arguments: dict[str, Any], *keys: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L489](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L489) |
| `def _summarize_path(path: str \| None) -> str \| None` | 源码未提供函数级文档字符串。 | [L497](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L497) |
| `def _humanize_tool_name(tool_name: str) -> str` | 源码未提供函数级文档字符串。 | [L510](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L510) |
| `def _extract_terminal_id(raw_output: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L517](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L517) |
| `def _resolve_tool_result_status(payload: dict[str, Any], result: Any, tool_name: str, raw_output: Any) -> str` | 源码未提供函数级文档字符串。 | [L523](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L523) |
| `def _is_list_like_tool_name(tool_name: str) -> bool` | 源码未提供函数级文档字符串。 | [L549](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py#L549) |

## `jiuwenswarm/common/e2a/acp/protocol.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L1)

**模块职责：** 定义 build_acp_initialize_result、build_acp_session_new_result、build_acp_session_list_result、build_acp_prompt_result。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L65](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L65) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def build_acp_initialize_result() -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L8](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L8) |
| `def build_acp_session_new_result(session_id: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L35](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L35) |
| `def build_acp_session_list_result(session_ids: list[str]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L42](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L42) |
| `def build_acp_prompt_result(*, stop_reason: str, user_message_id: str \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/common/e2a/acp/protocol.py#L54) |

## `jiuwenswarm/common/e2a/acp/session_updates.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L1)

**模块职责：** 定义 AcpSessionUpdateState、_ensure_tool_call_cache、_ensure_assistant_message_id、_reset_assistant_message_id、_ensure_thought_message_id、_reset_thought_message_id 等符号。

### [`class AcpSessionUpdateState(Protocol)`](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L15)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `assistant_message_id` | `str \| None` | `—` | [L16](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L16) |
| `assistant_text` | `str \| None` | `—` | [L17](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L17) |
| `thought_message_id` | `str \| None` | `—` | [L18](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L18) |
| `thought_text` | `str \| None` | `—` | [L19](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L19) |
| `user_message_id` | `str \| None` | `—` | [L20](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L20) |
| `tool_call_cache` | `dict[str, dict[str, Any]] \| None` | `—` | [L21](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L21) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _ensure_tool_call_cache(state: AcpSessionUpdateState) -> dict[str, dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L24](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L24) |
| `def _ensure_assistant_message_id(state: AcpSessionUpdateState) -> str` | 源码未提供函数级文档字符串。 | [L32](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L32) |
| `def _reset_assistant_message_id(state: AcpSessionUpdateState) -> str` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L38) |
| `def _ensure_thought_message_id(state: AcpSessionUpdateState) -> str` | 源码未提供函数级文档字符串。 | [L43](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L43) |
| `def _reset_thought_message_id(state: AcpSessionUpdateState) -> str` | 源码未提供函数级文档字符串。 | [L49](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L49) |
| `def _append_state_text(state: AcpSessionUpdateState, attr: str, text: str) -> None` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L54) |
| `def _build_incremental_text_update(*, text: str, state: AcpSessionUpdateState, text_attr: str, ensure_message_id, reset_message_id, update_kind: str) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L59](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L59) |
| `def build_acp_session_update(msg: Message, payload: dict[str, Any], state: AcpSessionUpdateState) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L101](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L101) |
| `def build_acp_final_text_update(payload: dict[str, Any], state: AcpSessionUpdateState) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L159](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L159) |
| `def build_acp_usage_update(payload: dict[str, Any]) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L187](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py#L187) |

## `jiuwenswarm/common/e2a/adapters.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/adapters.py#L1)

**模块职责：** 将 ACP JSON-RPC、A2A SendMessage 等外部形态转换为 E2A，并写入 provenance。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_CONVERTER_ACP` | `未显式标注` | [L38](../../../../../jiuwenswarm/common/e2a/adapters.py#L38) |
| `_CONVERTER_A2A` | `未显式标注` | [L39](../../../../../jiuwenswarm/common/e2a/adapters.py#L39) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def envelope_from_acp_jsonrpc(method: str, params: dict[str, Any] \| None = None, *, jsonrpc_id: str \| int \| None = None, session_id: str \| None = None, channel: str \| None = None, identity_origin: IdentityOrigin = IdentityOrigin.USER, converter: str \| None = None, extra_provenance_details: dict[str, Any] \| None = None) -> E2AEnvelope` | 由 ACP JSON-RPC 调用构造 E2A；provenance 标明来源为 acp。 | [L42](../../../../../jiuwenswarm/common/e2a/adapters.py#L42) |
| `def envelope_from_a2a_send_message(*, task_id: str \| None, context_id: str \| None, message_body: dict[str, Any], metadata: dict[str, Any] \| None = None, configuration: dict[str, Any] \| None = None, channel: str \| None = None, identity_origin: IdentityOrigin = IdentityOrigin.USER, converter: str \| None = None, extra_provenance_details: dict[str, Any] \| None = None) -> E2AEnvelope` | 将 A2A SendMessage 语义转为 E2A；provenance 标明来源为 a2a。 | [L80](../../../../../jiuwenswarm/common/e2a/adapters.py#L80) |
| `def envelope_to_acp_jsonrpc_call(envelope: E2AEnvelope) -> dict[str, Any]` | 将信封转为 JSON-RPC 风格单条调用描述（日志或下游 ACP 端点）。 | [L127](../../../../../jiuwenswarm/common/e2a/adapters.py#L127) |
| `def e2a_response_to_acp_jsonrpc_response(response: E2AResponse) -> dict[str, Any] \| None` | 将 ``E2AResponse`` 转为单条 JSON-RPC 2.0 **响应**对象（仅 ``result`` 或 ``error``，无 ``method``）。 | [L144](../../../../../jiuwenswarm/common/e2a/adapters.py#L144) |
| `def e2a_response_to_a2a_stream_payload(response: E2AResponse) -> dict[str, Any] \| None` | 将 ``response_kind == "a2a.stream_event"`` 的 ``E2AResponse`` 转为 A2A ``StreamResponse`` 形 JSON： | [L193](../../../../../jiuwenswarm/common/e2a/adapters.py#L193) |
| `def build_acp_tool_response_message(jsonrpc_id: str, response_data: dict[str, Any], session_id: str \| None, channel_id: str = 'acp') -> Any` | Build an internal Message for an ACP tool response (JSON-RPC response from client). | [L229](../../../../../jiuwenswarm/common/e2a/adapters.py#L229) |

## `jiuwenswarm/common/e2a/agent_compat.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/agent_compat.py#L1)

**模块职责：** AgentServer：E2AEnvelope → 现有 AgentRequest（第一阶段）；不得与 normalize_failed 兜底同时使用。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/e2a/agent_compat.py#L19) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _e2a_timestamp_to_float(ts: str \| None) -> float` | 源码未提供函数级文档字符串。 | [L22](../../../../../jiuwenswarm/common/e2a/agent_compat.py#L22) |
| `def e2a_to_agent_request(env: E2AEnvelope) -> AgentRequest` | 将规范化成功的 E2A 转为 AgentRequest。 | [L32](../../../../../jiuwenswarm/common/e2a/agent_compat.py#L32) |

## `jiuwenswarm/common/e2a/constants.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/constants.py#L1)

**模块职责：** E2A 出处常量与 ACP 方法名 / SessionUpdate 判别式（与 `ACP-reference.md` 一致）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `E2A_SOURCE_PROTOCOL_E2A` | `未显式标注` | [L6](../../../../../jiuwenswarm/common/e2a/constants.py#L6) |
| `E2A_SOURCE_PROTOCOL_ACP` | `未显式标注` | [L7](../../../../../jiuwenswarm/common/e2a/constants.py#L7) |
| `E2A_SOURCE_PROTOCOL_A2A` | `未显式标注` | [L8](../../../../../jiuwenswarm/common/e2a/constants.py#L8) |
| `E2A_RESPONSE_STATUS_SUCCEEDED` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/e2a/constants.py#L11) |
| `E2A_RESPONSE_STATUS_FAILED` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/e2a/constants.py#L12) |
| `E2A_RESPONSE_STATUS_IN_PROGRESS` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/e2a/constants.py#L13) |
| `E2A_RESPONSE_KIND_E2A_COMPLETE` | `未显式标注` | [L16](../../../../../jiuwenswarm/common/e2a/constants.py#L16) |
| `E2A_RESPONSE_KIND_E2A_CHUNK` | `未显式标注` | [L17](../../../../../jiuwenswarm/common/e2a/constants.py#L17) |
| `E2A_RESPONSE_KIND_E2A_ERROR` | `未显式标注` | [L18](../../../../../jiuwenswarm/common/e2a/constants.py#L18) |
| `E2A_RESPONSE_KIND_ACP_SESSION_UPDATE` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/e2a/constants.py#L19) |
| `E2A_RESPONSE_KIND_ACP_PROMPT_RESULT` | `未显式标注` | [L20](../../../../../jiuwenswarm/common/e2a/constants.py#L20) |
| `E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR` | `未显式标注` | [L21](../../../../../jiuwenswarm/common/e2a/constants.py#L21) |
| `E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST` | `未显式标注` | [L22](../../../../../jiuwenswarm/common/e2a/constants.py#L22) |
| `E2A_RESPONSE_KIND_A2A_TASK` | `未显式标注` | [L23](../../../../../jiuwenswarm/common/e2a/constants.py#L23) |
| `E2A_RESPONSE_KIND_A2A_MESSAGE` | `未显式标注` | [L24](../../../../../jiuwenswarm/common/e2a/constants.py#L24) |
| `E2A_RESPONSE_KIND_A2A_STREAM_EVENT` | `未显式标注` | [L25](../../../../../jiuwenswarm/common/e2a/constants.py#L25) |
| `E2A_RESPONSE_KIND_CRON` | `未显式标注` | [L26](../../../../../jiuwenswarm/common/e2a/constants.py#L26) |
| `E2A_RESPONSE_KIND_PLAN_APPROVAL_REQUIRED` | `未显式标注` | [L27](../../../../../jiuwenswarm/common/e2a/constants.py#L27) |
| `E2A_RESPONSE_KIND_EXT` | `未显式标注` | [L28](../../../../../jiuwenswarm/common/e2a/constants.py#L28) |
| `E2A_RESPONSE_KINDS` | `tuple[str, ...]` | [L31](../../../../../jiuwenswarm/common/e2a/constants.py#L31) |
| `E2A_A2A_STREAM_BRANCHES` | `tuple[str, ...]` | [L48](../../../../../jiuwenswarm/common/e2a/constants.py#L48) |
| `ACP_CLIENT_TO_AGENT_METHODS` | `tuple[str, ...]` | [L56](../../../../../jiuwenswarm/common/e2a/constants.py#L56) |
| `ACP_AGENT_TO_CLIENT_METHODS` | `tuple[str, ...]` | [L73](../../../../../jiuwenswarm/common/e2a/constants.py#L73) |
| `ACP_NOTIFICATION_NAMES` | `tuple[str, ...]` | [L86](../../../../../jiuwenswarm/common/e2a/constants.py#L86) |
| `E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY` | `未显式标注` | [L94](../../../../../jiuwenswarm/common/e2a/constants.py#L94) |
| `E2A_WIRE_LEGACY_AGENT_CHUNK_KEY` | `未显式标注` | [L95](../../../../../jiuwenswarm/common/e2a/constants.py#L95) |
| `E2A_WIRE_SERVER_PUSH_KEY` | `未显式标注` | [L97](../../../../../jiuwenswarm/common/e2a/constants.py#L97) |
| `E2A_INTERNAL_CANCEL_SOURCE_KEY` | `未显式标注` | [L99](../../../../../jiuwenswarm/common/e2a/constants.py#L99) |
| `E2A_CANCEL_SOURCE_CLIENT_DISCONNECT` | `未显式标注` | [L100](../../../../../jiuwenswarm/common/e2a/constants.py#L100) |
| `E2A_WIRE_INTERNAL_METADATA_KEYS` | `frozenset[str]` | [L103](../../../../../jiuwenswarm/common/e2a/constants.py#L103) |
| `ACP_SESSION_UPDATE_KINDS` | `tuple[str, ...]` | [L112](../../../../../jiuwenswarm/common/e2a/constants.py#L112) |

## `jiuwenswarm/common/e2a/gateway_normalize.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L1)

**模块职责：** Gateway：Channel Message / 类 Agent 请求字段 → E2AEnvelope；AgentResponse/Chunk → E2AResponse；规范化失败时构造兜底信封。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L36](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L36) |
| `E2A_INTERNAL_CONTEXT_KEY` | `未显式标注` | [L39](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L39) |
| `E2A_FALLBACK_FAILED_KEY` | `未显式标注` | [L40](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L40) |
| `E2A_LEGACY_AGENT_REQUEST_KEY` | `未显式标注` | [L41](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L41) |
| `MAX_LEGACY_AGENT_REQUEST_JSON_BYTES` | `未显式标注` | [L42](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L42) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def message_to_legacy_agent_dict(msg: 'Message') -> dict[str, Any]` | 从 Message 生成与历史 WebSocket 一致的 dict（用于兜底 legacy_agent_request）。 | [L45](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L45) |
| `def _legacy_payload_within_limit(legacy: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L70](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L70) |
| `def build_fallback_e2a(legacy: dict[str, Any]) -> E2AEnvelope` | 规范化失败时仍发 E2A 形状：在 channel_context 内携带 legacy 快照。 | [L93](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L93) |
| `def message_to_e2a(msg: 'Message') -> E2AEnvelope` | Message → E2AEnvelope（不经兜底）。 | [L115](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L115) |
| `def message_to_e2a_or_fallback(msg: 'Message') -> E2AEnvelope` | Message → E2A；失败或校验不通过则 build_fallback_e2a。 | [L180](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L180) |
| `def e2a_from_agent_fields(*, request_id: str, channel_id: str = '', session_id: str \| None = None, req_method: 'ReqMethod \| str \| None' = None, params: dict[str, Any] \| None = None, is_stream: bool = False, timestamp: float = 0.0, metadata: dict[str, Any] \| None = None, user_id: str \| None = None) -> E2AEnvelope` | 由与 AgentRequest 相同的字段构造 E2A（heartbeat / cron / app 管理请求等）。 | [L207](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L207) |
| `def channel_context_for_channel_reply(env: E2AEnvelope) -> dict[str, Any] \| None` | 供流式 chunk 回传到 Channel：去掉内部 _jiuwenswarm，保留 trace 与业务 metadata。 | [L239](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L239) |
| `def e2a_response_from_agent_response(resp: 'AgentResponse', *, response_id: str, sequence: int = 0, timestamp: str \| None = None) -> E2AResponse` | 将 ``AgentResponse``（与 ``E:\logs`` 中 ``AgentResponse: {...}`` 同形）规范为 ``E2AResponse``。 | [L246](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L246) |
| `def e2a_response_from_agent_chunk(chunk: 'AgentResponseChunk', *, response_id: str, sequence: int, is_stream: bool = True, timestamp: str \| None = None) -> E2AResponse` | 将 ``AgentResponseChunk``（与 ``E:\logs`` 中 ``AgentResponseChunk: {...}`` 同形）规范为 ``E2AResponse``。 | [L307](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L307) |
| `def e2a_response_to_agent_response(e2a: E2AResponse) -> 'AgentResponse'` | ``E2AResponse`` → 非流式 ``AgentResponse``（与 ``e2a_response_from_agent_response`` 对仗）。 | [L482](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L482) |
| `def _is_hitl_stream_terminal_body(body: dict[str, Any]) -> bool` | HITL 暂停帧：Agent 以 is_final=False 下发，但 Gateway 流式会话应在此结束。 | [L531](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L531) |
| `def e2a_response_to_agent_chunk(e2a: E2AResponse) -> 'AgentResponseChunk'` | ``E2AResponse`` → ``AgentResponseChunk``（与 ``e2a_response_from_agent_chunk`` 对仗）。 | [L538](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py#L538) |

## `jiuwenswarm/common/e2a/models.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/models.py#L1)

**模块职责：** E2A 数据模型：请求信封 ``E2AEnvelope``、响应 ``E2AResponse`` 与子结构。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `E2A_PROTOCOL_VERSION` | `未显式标注` | [L25](../../../../../jiuwenswarm/common/e2a/models.py#L25) |

### [`class IdentityOrigin(str, Enum)`](../../../../../jiuwenswarm/common/e2a/models.py#L34)

身份来源：谁触发了本次对 Agent 的请求。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `SYSTEM` | `未显式标注` | `'system'` | [L37](../../../../../jiuwenswarm/common/e2a/models.py#L37) |
| `USER` | `未显式标注` | `'user'` | [L38](../../../../../jiuwenswarm/common/e2a/models.py#L38) |
| `AGENT` | `未显式标注` | `'agent'` | [L39](../../../../../jiuwenswarm/common/e2a/models.py#L39) |
| `SERVICE` | `未显式标注` | `'service'` | [L40](../../../../../jiuwenswarm/common/e2a/models.py#L40) |

### [`class E2AProvenance`](../../../../../jiuwenswarm/common/e2a/models.py#L44)

记录 E2A 信封的出处。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `source_protocol` | `str` | `E2A_SOURCE_PROTOCOL_E2A` | [L53](../../../../../jiuwenswarm/common/e2a/models.py#L53) |
| `converter` | `str \| None` | `None` | [L54](../../../../../jiuwenswarm/common/e2a/models.py#L54) |
| `converted_at` | `str \| None` | `None` | [L55](../../../../../jiuwenswarm/common/e2a/models.py#L55) |
| `details` | `dict[str, Any]` | `field(default_factory=dict)` | [L56](../../../../../jiuwenswarm/common/e2a/models.py#L56) |

### [`class E2AFileRef`](../../../../../jiuwenswarm/common/e2a/models.py#L60)

文件引用（用于 ``params.files`` / ``params.attachments`` 等元素，对齐 MCP/A2A 常见形态）。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `uri` | `str` | `—` | [L63](../../../../../jiuwenswarm/common/e2a/models.py#L63) |
| `name` | `str \| None` | `None` | [L64](../../../../../jiuwenswarm/common/e2a/models.py#L64) |
| `mime_type` | `str \| None` | `None` | [L65](../../../../../jiuwenswarm/common/e2a/models.py#L65) |
| `size` | `int \| None` | `None` | [L66](../../../../../jiuwenswarm/common/e2a/models.py#L66) |
| `_meta` | `dict[str, Any]` | `field(default_factory=dict)` | [L67](../../../../../jiuwenswarm/common/e2a/models.py#L67) |

### [`class E2AAuth`](../../../../../jiuwenswarm/common/e2a/models.py#L71)

身份鉴权信息（按需填充）。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `method_id` | `str \| None` | `None` | [L78](../../../../../jiuwenswarm/common/e2a/models.py#L78) |
| `bearer_token` | `str \| None` | `None` | [L79](../../../../../jiuwenswarm/common/e2a/models.py#L79) |
| `api_key_ref` | `str \| None` | `None` | [L80](../../../../../jiuwenswarm/common/e2a/models.py#L80) |
| `credential_ref` | `str \| None` | `None` | [L81](../../../../../jiuwenswarm/common/e2a/models.py#L81) |
| `extra_headers` | `dict[str, str]` | `field(default_factory=dict)` | [L82](../../../../../jiuwenswarm/common/e2a/models.py#L82) |
| `_meta` | `dict[str, Any]` | `field(default_factory=dict)` | [L83](../../../../../jiuwenswarm/common/e2a/models.py#L83) |

### [`class E2AEnvelope`](../../../../../jiuwenswarm/common/e2a/models.py#L87)

E2A 统一信封：单结构兼容多协议入口，由网关或适配层解析后调用 Agent。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `protocol_version` | `str` | `E2A_PROTOCOL_VERSION` | [L109](../../../../../jiuwenswarm/common/e2a/models.py#L109) |
| `provenance` | `E2AProvenance` | `field(default_factory=E2AProvenance)` | [L110](../../../../../jiuwenswarm/common/e2a/models.py#L110) |
| `request_id` | `str \| None` | `None` | [L111](../../../../../jiuwenswarm/common/e2a/models.py#L111) |
| `jsonrpc_id` | `str \| int \| None` | `None` | [L112](../../../../../jiuwenswarm/common/e2a/models.py#L112) |
| `correlation_id` | `str \| None` | `None` | [L113](../../../../../jiuwenswarm/common/e2a/models.py#L113) |
| `task_id` | `str \| None` | `None` | [L114](../../../../../jiuwenswarm/common/e2a/models.py#L114) |
| `context_id` | `str \| None` | `None` | [L115](../../../../../jiuwenswarm/common/e2a/models.py#L115) |
| `session_id` | `str \| None` | `None` | [L116](../../../../../jiuwenswarm/common/e2a/models.py#L116) |
| `message_id` | `str \| None` | `None` | [L117](../../../../../jiuwenswarm/common/e2a/models.py#L117) |
| `timestamp` | `str \| None` | `None` | [L120](../../../../../jiuwenswarm/common/e2a/models.py#L120) |
| `identity_origin` | `IdentityOrigin` | `IdentityOrigin.USER` | [L123](../../../../../jiuwenswarm/common/e2a/models.py#L123) |
| `channel` | `str \| None` | `None` | [L124](../../../../../jiuwenswarm/common/e2a/models.py#L124) |
| `user_id` | `str \| None` | `None` | [L125](../../../../../jiuwenswarm/common/e2a/models.py#L125) |
| `agent_ref` | `dict \| None` | `None` | [L126](../../../../../jiuwenswarm/common/e2a/models.py#L126) |
| `chat_id` | `str \| None` | `None` | [L127](../../../../../jiuwenswarm/common/e2a/models.py#L127) |
| `source_agent_id` | `str \| None` | `None` | [L128](../../../../../jiuwenswarm/common/e2a/models.py#L128) |
| `agent_id` | `str \| None` | `None` | [L130](../../../../../jiuwenswarm/common/e2a/models.py#L130) |
| `service_id` | `str \| None` | `None` | [L131](../../../../../jiuwenswarm/common/e2a/models.py#L131) |
| `workspace_key` | `str \| None` | `None` | [L133](../../../../../jiuwenswarm/common/e2a/models.py#L133) |
| `method` | `str \| None` | `None` | [L136](../../../../../jiuwenswarm/common/e2a/models.py#L136) |
| `params` | `dict[str, Any]` | `field(default_factory=dict)` | [L137](../../../../../jiuwenswarm/common/e2a/models.py#L137) |
| `ext_method` | `str \| None` | `None` | [L138](../../../../../jiuwenswarm/common/e2a/models.py#L138) |
| `session_update_kind` | `str \| None` | `None` | [L139](../../../../../jiuwenswarm/common/e2a/models.py#L139) |
| `is_stream` | `bool` | `False` | [L140](../../../../../jiuwenswarm/common/e2a/models.py#L140) |
| `expected_output_modes` | `list[str]` | `field(default_factory=list)` | [L143](../../../../../jiuwenswarm/common/e2a/models.py#L143) |
| `auth` | `E2AAuth \| None` | `None` | [L146](../../../../../jiuwenswarm/common/e2a/models.py#L146) |
| `channel_context` | `dict[str, Any]` | `field(default_factory=dict)` | [L149](../../../../../jiuwenswarm/common/e2a/models.py#L149) |
| `a2a_metadata` | `dict[str, Any]` | `field(default_factory=dict)` | [L150](../../../../../jiuwenswarm/common/e2a/models.py#L150) |
| `acp_meta` | `dict[str, Any]` | `field(default_factory=dict)` | [L151](../../../../../jiuwenswarm/common/e2a/models.py#L151) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def ensure_timestamp(self) -> None` | 若未设置 timestamp，则填当前 UTC ISO8601。 | [L153](../../../../../jiuwenswarm/common/e2a/models.py#L153) |
| `def to_dict(self) -> dict[str, Any]` | 序列化为 JSON 友好 dict（枚举转为值）。 | [L158](../../../../../jiuwenswarm/common/e2a/models.py#L158) |
| `@classmethod def from_dict(cls, data: dict[str, Any]) -> E2AEnvelope` | 源码未提供方法级文档字符串。 | [L164](../../../../../jiuwenswarm/common/e2a/models.py#L164) |

### [`class E2AResponse`](../../../../../jiuwenswarm/common/e2a/models.py#L169)

E2A 统一响应：每条出站记录（含流式多帧）一条实例；与 ``E2AEnvelope`` 对称。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `protocol_version` | `str` | `E2A_PROTOCOL_VERSION` | [L180](../../../../../jiuwenswarm/common/e2a/models.py#L180) |
| `response_id` | `str \| None` | `None` | [L181](../../../../../jiuwenswarm/common/e2a/models.py#L181) |
| `request_id` | `str \| None` | `None` | [L182](../../../../../jiuwenswarm/common/e2a/models.py#L182) |
| `sequence` | `int` | `0` | [L183](../../../../../jiuwenswarm/common/e2a/models.py#L183) |
| `is_final` | `bool` | `False` | [L184](../../../../../jiuwenswarm/common/e2a/models.py#L184) |
| `status` | `str` | `E2A_RESPONSE_STATUS_IN_PROGRESS` | [L185](../../../../../jiuwenswarm/common/e2a/models.py#L185) |
| `response_kind` | `str` | `''` | [L186](../../../../../jiuwenswarm/common/e2a/models.py#L186) |
| `timestamp` | `str \| None` | `None` | [L187](../../../../../jiuwenswarm/common/e2a/models.py#L187) |
| `provenance` | `E2AProvenance` | `field(default_factory=E2AProvenance)` | [L188](../../../../../jiuwenswarm/common/e2a/models.py#L188) |
| `body` | `dict[str, Any]` | `field(default_factory=dict)` | [L189](../../../../../jiuwenswarm/common/e2a/models.py#L189) |
| `jsonrpc_id` | `str \| int \| None` | `None` | [L191](../../../../../jiuwenswarm/common/e2a/models.py#L191) |
| `correlation_id` | `str \| None` | `None` | [L192](../../../../../jiuwenswarm/common/e2a/models.py#L192) |
| `task_id` | `str \| None` | `None` | [L193](../../../../../jiuwenswarm/common/e2a/models.py#L193) |
| `context_id` | `str \| None` | `None` | [L194](../../../../../jiuwenswarm/common/e2a/models.py#L194) |
| `session_id` | `str \| None` | `None` | [L195](../../../../../jiuwenswarm/common/e2a/models.py#L195) |
| `message_id` | `str \| None` | `None` | [L196](../../../../../jiuwenswarm/common/e2a/models.py#L196) |
| `is_stream` | `bool` | `False` | [L197](../../../../../jiuwenswarm/common/e2a/models.py#L197) |
| `identity_origin` | `IdentityOrigin` | `IdentityOrigin.AGENT` | [L198](../../../../../jiuwenswarm/common/e2a/models.py#L198) |
| `channel` | `str \| None` | `None` | [L199](../../../../../jiuwenswarm/common/e2a/models.py#L199) |
| `user_id` | `str \| None` | `None` | [L200](../../../../../jiuwenswarm/common/e2a/models.py#L200) |
| `agent_ref` | `dict \| None` | `None` | [L201](../../../../../jiuwenswarm/common/e2a/models.py#L201) |
| `source_agent_id` | `str \| None` | `None` | [L202](../../../../../jiuwenswarm/common/e2a/models.py#L202) |
| `method` | `str \| None` | `None` | [L203](../../../../../jiuwenswarm/common/e2a/models.py#L203) |
| `projections` | `dict[str, Any]` | `field(default_factory=dict)` | [L205](../../../../../jiuwenswarm/common/e2a/models.py#L205) |
| `channel_context` | `dict[str, Any]` | `field(default_factory=dict)` | [L206](../../../../../jiuwenswarm/common/e2a/models.py#L206) |
| `metadata` | `dict[str, Any]` | `field(default_factory=dict)` | [L207](../../../../../jiuwenswarm/common/e2a/models.py#L207) |
| `a2a_metadata` | `dict[str, Any]` | `field(default_factory=dict)` | [L208](../../../../../jiuwenswarm/common/e2a/models.py#L208) |
| `acp_meta` | `dict[str, Any]` | `field(default_factory=dict)` | [L209](../../../../../jiuwenswarm/common/e2a/models.py#L209) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def ensure_timestamp(self) -> None` | 若未设置 timestamp，则填当前 UTC ISO8601。 | [L211](../../../../../jiuwenswarm/common/e2a/models.py#L211) |
| `def to_dict(self) -> dict[str, Any]` | 序列化为 JSON 友好 dict（枚举转为值）。 | [L216](../../../../../jiuwenswarm/common/e2a/models.py#L216) |
| `@classmethod def from_dict(cls, data: dict[str, Any]) -> E2AResponse` | 源码未提供方法级文档字符串。 | [L221](../../../../../jiuwenswarm/common/e2a/models.py#L221) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def utc_now_iso() -> str` | 当前 UTC 时刻的 RFC 3339 字符串（``provenance.converted_at``、响应 ``timestamp`` 缺省等）。 | [L28](../../../../../jiuwenswarm/common/e2a/models.py#L28) |
| `def _enum_value(obj: Any) -> Any` | 源码未提供函数级文档字符串。 | [L225](../../../../../jiuwenswarm/common/e2a/models.py#L225) |
| `def _dataclass_to_json_dict(obj: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L231](../../../../../jiuwenswarm/common/e2a/models.py#L231) |
| `def _provenance_from_dict(raw: Any) -> E2AProvenance` | 源码未提供函数级文档字符串。 | [L263](../../../../../jiuwenswarm/common/e2a/models.py#L263) |
| `def _normalize_timestamp_value(raw: Any) -> str \| None` | 规范为 RFC 3339 UTC 字符串；接受 str 或历史 float/int 纪元秒。 | [L278](../../../../../jiuwenswarm/common/e2a/models.py#L278) |
| `def _migrate_legacy_binding(data: dict[str, Any], prov: E2AProvenance) -> E2AProvenance` | 旧版 ``binding`` 字段迁入 provenance.details，避免丢失信息。 | [L289](../../../../../jiuwenswarm/common/e2a/models.py#L289) |
| `def _normalize_optional_wire_str(value: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L315](../../../../../jiuwenswarm/common/e2a/models.py#L315) |
| `def _resolve_wire_agent_id(data: dict[str, Any]) -> str \| None` | Resolve tenant ``agent_id`` from wire top-level fields only. | [L322](../../../../../jiuwenswarm/common/e2a/models.py#L322) |
| `def _resolve_wire_service_id(data: dict[str, Any]) -> str \| None` | 源码未提供函数级文档字符串。 | [L334](../../../../../jiuwenswarm/common/e2a/models.py#L334) |
| `def _resolve_wire_workspace_key(data: dict[str, Any]) -> str \| None` | Prefer ``workspace_key``; accept legacy wire key ``workspace_dir``. | [L338](../../../../../jiuwenswarm/common/e2a/models.py#L338) |
| `def _params_with_optional_legacy_payload(data: dict[str, Any]) -> dict[str, Any]` | 以 ``params`` 为真源；若存在顶层 ``payload`` 对象，将其键合并进 params（不覆盖已有键）。 | [L345](../../../../../jiuwenswarm/common/e2a/models.py#L345) |
| `def _envelope_from_dict(data: dict[str, Any]) -> E2AEnvelope` | 源码未提供函数级文档字符串。 | [L364](../../../../../jiuwenswarm/common/e2a/models.py#L364) |
| `def _e2a_response_from_dict(data: dict[str, Any]) -> E2AResponse` | 源码未提供函数级文档字符串。 | [L451](../../../../../jiuwenswarm/common/e2a/models.py#L451) |
| `def merge_params_to_acp_prompt(envelope: E2AEnvelope) -> dict[str, Any]` | 当 ``method == "session/prompt"`` 时，从 ``envelope.params`` 补全 ACP 所需 ``prompt``，返回新参数字典。 | [L500](../../../../../jiuwenswarm/common/e2a/models.py#L500) |

## `jiuwenswarm/common/e2a/wire_codec.py`

[打开源码](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L1)

**模块职责：** AgentServer ↔ Gateway WebSocket：E2AResponse 线编码 / 解码与 legacy 兜底。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L35](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L35) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _json_safe(value: Any) -> Any` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L38) |
| `def _raw_dict_to_agent_response(data: dict[str, Any]) -> AgentResponse` | 源码未提供函数级文档字符串。 | [L61](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L61) |
| `def _raw_dict_to_agent_chunk(data: dict[str, Any]) -> AgentResponseChunk` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L71) |
| `def is_e2a_response_wire_dict(data: dict[str, Any]) -> bool` | 判别 JSON 对象是否为 E2A 响应线格式（与 ``E2AEnvelope`` 区分：须含非空 ``response_kind``）。 | [L80](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L80) |
| `def _deprecated_unary_shape(data: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L90](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L90) |
| `def _deprecated_chunk_shape(data: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L100](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L100) |
| `def parse_agent_server_wire_unary(data: dict[str, Any]) -> AgentResponse` | 将一条非流式 WebSocket JSON 解析为 ``AgentResponse``。 | [L112](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L112) |
| `def parse_agent_server_wire_chunk(data: dict[str, Any]) -> AgentResponseChunk` | 将一条流式 WebSocket JSON 解析为 ``AgentResponseChunk``。 | [L171](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L171) |
| `def encode_agent_response_for_wire(resp: AgentResponse, *, response_id: str, sequence: int = 0) -> dict[str, Any]` | ``AgentResponse`` → E2A 线 dict；失败时 ``metadata`` 塞入整包 legacy 并记日志。 | [L232](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L232) |
| `def encode_agent_chunk_for_wire(chunk: AgentResponseChunk, *, response_id: str, sequence: int, is_stream: bool = True) -> dict[str, Any]` | ``AgentResponseChunk`` → E2A 线 dict；失败时 ``metadata`` 塞入整包 legacy。 | [L281](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L281) |
| `def _fallback_wire_unary_from_legacy(legacy: dict[str, Any], *, response_id: str, sequence: int, exc: BaseException) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L335](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L335) |
| `def _fallback_wire_chunk_from_legacy(legacy: dict[str, Any], *, response_id: str, sequence: int, exc: BaseException, is_stream: bool) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L372](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L372) |
| `def encode_json_parse_error_wire(*, request_id: str, channel_id: str, message: str, response_id: str = '') -> dict[str, Any]` | 入站 JSON 无法解析时发送的单帧 E2A 形错误（无 legacy blob）。 | [L410](../../../../../jiuwenswarm/common/e2a/wire_codec.py#L410) |

## `jiuwenswarm/common/git_safe_directory.py`

[打开源码](../../../../../jiuwenswarm/common/git_safe_directory.py#L1)

**模块职责：** Helpers for reporting Git dubious ownership checks.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_DUBIOUS_OWNERSHIP_MARKERS` | `未显式标注` | [L7](../../../../../jiuwenswarm/common/git_safe_directory.py#L7) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_dubious_ownership_error(result: subprocess.CompletedProcess[str]) -> bool` | Return True when a git command failed because of safe.directory checks. | [L13](../../../../../jiuwenswarm/common/git_safe_directory.py#L13) |
| `def safe_directory_value(path: str) -> str` | Normalize a project path for ``git config safe.directory``. | [L21](../../../../../jiuwenswarm/common/git_safe_directory.py#L21) |
| `def safe_directory_hint(path: str) -> str` | Return a user-facing command suggestion for Git safe.directory. | [L29](../../../../../jiuwenswarm/common/git_safe_directory.py#L29) |

## `jiuwenswarm/common/hooks_config.py`

[打开源码](../../../../../jiuwenswarm/common/hooks_config.py#L1)

**模块职责：** Hooks 配置模型 —— 定义 config.yaml 中 hooks 段的 schema 与匹配逻辑.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/hooks_config.py#L12) |
| `_AGENT_RAIL_EVENTS` | `未显式标注` | [L42](../../../../../jiuwenswarm/common/hooks_config.py#L42) |
| `_GATEWAY_EVENTS` | `未显式标注` | [L56](../../../../../jiuwenswarm/common/hooks_config.py#L56) |

### [`class HookType(str, Enum)`](../../../../../jiuwenswarm/common/hooks_config.py#L15)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `COMMAND` | `未显式标注` | `'command'` | [L16](../../../../../jiuwenswarm/common/hooks_config.py#L16) |
| `PROMPT` | `未显式标注` | `'prompt'` | [L17](../../../../../jiuwenswarm/common/hooks_config.py#L17) |

### [`class HookEvent(str, Enum)`](../../../../../jiuwenswarm/common/hooks_config.py#L20)

当前 17 个底层能力支持的event.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `PRE_TOOL_USE` | `未显式标注` | `'PreToolUse'` | [L22](../../../../../jiuwenswarm/common/hooks_config.py#L22) |
| `POST_TOOL_USE` | `未显式标注` | `'PostToolUse'` | [L23](../../../../../jiuwenswarm/common/hooks_config.py#L23) |
| `POST_TOOL_USE_FAILURE` | `未显式标注` | `'PostToolUseFailure'` | [L24](../../../../../jiuwenswarm/common/hooks_config.py#L24) |
| `STOP` | `未显式标注` | `'Stop'` | [L25](../../../../../jiuwenswarm/common/hooks_config.py#L25) |
| `USER_PROMPT_SUBMIT` | `未显式标注` | `'UserPromptSubmit'` | [L26](../../../../../jiuwenswarm/common/hooks_config.py#L26) |
| `SESSION_START` | `未显式标注` | `'SessionStart'` | [L27](../../../../../jiuwenswarm/common/hooks_config.py#L27) |
| `SESSION_END` | `未显式标注` | `'SessionEnd'` | [L28](../../../../../jiuwenswarm/common/hooks_config.py#L28) |
| `NOTIFICATION` | `未显式标注` | `'Notification'` | [L29](../../../../../jiuwenswarm/common/hooks_config.py#L29) |
| `PERMISSION_REQUEST` | `未显式标注` | `'PermissionRequest'` | [L30](../../../../../jiuwenswarm/common/hooks_config.py#L30) |
| `PERMISSION_DENIED` | `未显式标注` | `'PermissionDenied'` | [L31](../../../../../jiuwenswarm/common/hooks_config.py#L31) |
| `SUBAGENT_START` | `未显式标注` | `'SubagentStart'` | [L32](../../../../../jiuwenswarm/common/hooks_config.py#L32) |
| `SUBAGENT_STOP` | `未显式标注` | `'SubagentStop'` | [L33](../../../../../jiuwenswarm/common/hooks_config.py#L33) |
| `CONFIG_CHANGE` | `未显式标注` | `'ConfigChange'` | [L34](../../../../../jiuwenswarm/common/hooks_config.py#L34) |
| `INSTRUCTIONS_LOADED` | `未显式标注` | `'InstructionsLoaded'` | [L35](../../../../../jiuwenswarm/common/hooks_config.py#L35) |
| `SETUP` | `未显式标注` | `'Setup'` | [L36](../../../../../jiuwenswarm/common/hooks_config.py#L36) |
| `BEFORE_MODEL_CALL` | `未显式标注` | `'BeforeModelCall'` | [L37](../../../../../jiuwenswarm/common/hooks_config.py#L37) |
| `AFTER_MODEL_CALL` | `未显式标注` | `'AfterModelCall'` | [L38](../../../../../jiuwenswarm/common/hooks_config.py#L38) |

### [`class CommandHookConfig`](../../../../../jiuwenswarm/common/hooks_config.py#L76)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `type` | `str` | `'command'` | [L77](../../../../../jiuwenswarm/common/hooks_config.py#L77) |
| `command` | `str` | `''` | [L78](../../../../../jiuwenswarm/common/hooks_config.py#L78) |
| `timeout` | `int` | `30` | [L79](../../../../../jiuwenswarm/common/hooks_config.py#L79) |
| `shell` | `str` | `'bash'` | [L80](../../../../../jiuwenswarm/common/hooks_config.py#L80) |
| `status_message` | `str` | `''` | [L81](../../../../../jiuwenswarm/common/hooks_config.py#L81) |

### [`class PromptHookConfig`](../../../../../jiuwenswarm/common/hooks_config.py#L85)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `type` | `str` | `'prompt'` | [L86](../../../../../jiuwenswarm/common/hooks_config.py#L86) |
| `prompt` | `str` | `''` | [L87](../../../../../jiuwenswarm/common/hooks_config.py#L87) |
| `timeout` | `int` | `15` | [L88](../../../../../jiuwenswarm/common/hooks_config.py#L88) |
| `model` | `str` | `''` | [L89](../../../../../jiuwenswarm/common/hooks_config.py#L89) |
| `status_message` | `str` | `''` | [L90](../../../../../jiuwenswarm/common/hooks_config.py#L90) |

### [`class HookMatcher`](../../../../../jiuwenswarm/common/hooks_config.py#L94)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `matcher` | `str` | `'*'` | [L95](../../../../../jiuwenswarm/common/hooks_config.py#L95) |
| `hooks` | `list[dict]` | `field(default_factory=list)` | [L96](../../../../../jiuwenswarm/common/hooks_config.py#L96) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def matches(self, query: str) -> bool` | 检查 query 是否匹配此 matcher. | [L98](../../../../../jiuwenswarm/common/hooks_config.py#L98) |
| `@staticmethod def _match_single(pattern: str, query: str) -> bool` | 源码未提供方法级文档字符串。 | [L115](../../../../../jiuwenswarm/common/hooks_config.py#L115) |

### [`class HooksConfig`](../../../../../jiuwenswarm/common/hooks_config.py#L127)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `events` | `dict[str, list[HookMatcher]]` | `field(default_factory=dict)` | [L128](../../../../../jiuwenswarm/common/hooks_config.py#L128) |
| `disable_all_hooks` | `bool` | `False` | [L129](../../../../../jiuwenswarm/common/hooks_config.py#L129) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def match(self, event: str, query: str = '') -> list[dict]` | 获取匹配该事件 + query 的所有 hook 配置. | [L131](../../../../../jiuwenswarm/common/hooks_config.py#L131) |
| `def get_event_summary(self) -> list[dict]` | 返回各事件的 hook 数量摘要（供 /hooks 命令 UI 使用）. | [L143](../../../../../jiuwenswarm/common/hooks_config.py#L143) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_rail_event(event: HookEvent) -> bool` | 源码未提供函数级文档字符串。 | [L67](../../../../../jiuwenswarm/common/hooks_config.py#L67) |
| `def is_gateway_event(event: HookEvent) -> bool` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/common/hooks_config.py#L71) |
| `def load_hooks_config(config_base: dict \| None = None) -> HooksConfig` | 从 config.yaml 的 hooks 段加载配置. | [L165](../../../../../jiuwenswarm/common/hooks_config.py#L165) |

## `jiuwenswarm/common/http_proxy_config.py`

[打开源码](../../../../../jiuwenswarm/common/http_proxy_config.py#L1)

**模块职责：** Overlay-aware HTTP proxy resolution for outbound requests.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_PROXY_ENV_KEYS` | `tuple[str, ...]` | [L23](../../../../../jiuwenswarm/common/http_proxy_config.py#L23) |
| `_NO_PROXY_ENV_KEYS` | `tuple[str, ...]` | [L29](../../../../../jiuwenswarm/common/http_proxy_config.py#L29) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _read_env_then_environ(keys: tuple[str, ...]) -> str` | Tip/overlay first (no process fallthrough), then process env. | [L32](../../../../../jiuwenswarm/common/http_proxy_config.py#L32) |
| `def read_proxy_url() -> str` | Return the configured proxy URL for the current env context. | [L50](../../../../../jiuwenswarm/common/http_proxy_config.py#L50) |
| `def read_no_proxy_list() -> list[str]` | Parse NO_PROXY for the current env context (deduped, lowercased). | [L55](../../../../../jiuwenswarm/common/http_proxy_config.py#L55) |
| `def _hostname_matches_no_proxy(hostname: str, no_proxy_list: list[str]) -> bool` | 源码未提供函数级文档字符串。 | [L83](../../../../../jiuwenswarm/common/http_proxy_config.py#L83) |
| `def _is_ip_match(hostname: str, entry: str) -> bool` | 源码未提供函数级文档字符串。 | [L97](../../../../../jiuwenswarm/common/http_proxy_config.py#L97) |
| `def should_bypass_proxy(url: str) -> bool` | Return True when *url* should not use the configured HTTP proxy. | [L108](../../../../../jiuwenswarm/common/http_proxy_config.py#L108) |
| `def resolve_requests_proxies(url: str) -> dict[str, str] \| None` | Return a ``requests`` proxies mapping, or ``None`` for a direct connection. | [L120](../../../../../jiuwenswarm/common/http_proxy_config.py#L120) |
| `def resolve_httpx_proxy(url: str) -> str \| None` | Return an ``httpx`` proxy URL, or ``None`` for a direct connection. | [L130](../../../../../jiuwenswarm/common/http_proxy_config.py#L130) |
| `def prepare_requests_kwargs(url: str, kwargs: dict[str, Any] \| None = None) -> dict[str, Any]` | Merge overlay-aware proxy settings into ``requests`` keyword arguments. | [L138](../../../../../jiuwenswarm/common/http_proxy_config.py#L138) |
| `def _ssl_verify_raw() -> str` | 源码未提供函数级文档字符串。 | [L148](../../../../../jiuwenswarm/common/http_proxy_config.py#L148) |
| `def ssl_verify_enabled(default: bool = True) -> bool` | Whether TLS verification is enabled (tip, then process environ). | [L159](../../../../../jiuwenswarm/common/http_proxy_config.py#L159) |
| `def resolve_requests_verify() -> bool \| str` | Return requests ``verify`` kwarg: False \| CA path \| True. | [L169](../../../../../jiuwenswarm/common/http_proxy_config.py#L169) |
| `def _requests_verify() -> bool \| str` | 源码未提供函数级文档字符串。 | [L188](../../../../../jiuwenswarm/common/http_proxy_config.py#L188) |
| `def requests_request(method: str, url: str, **kwargs: Any) -> requests.Response` | Issue a ``requests`` call using overlay-aware proxy settings only. | [L192](../../../../../jiuwenswarm/common/http_proxy_config.py#L192) |
| `def requests_get(url: str, **kwargs: Any) -> requests.Response` | 源码未提供函数级文档字符串。 | [L209](../../../../../jiuwenswarm/common/http_proxy_config.py#L209) |
| `def requests_post(url: str, **kwargs: Any) -> requests.Response` | 源码未提供函数级文档字符串。 | [L213](../../../../../jiuwenswarm/common/http_proxy_config.py#L213) |

## `jiuwenswarm/common/kv_cache_affinity_config.py`

[打开源码](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L1)

**模块职责：** Pure configuration rules for Ascend KV cache affinity.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `ASCEND_AFFINITY_PROVIDER` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L11) |
| `KVC_CONFIG_KEYS` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L12) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def parse_bool(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L21](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L21) |
| `def select_default_model_entry(models: list[dict[str, Any]]) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L25](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L25) |
| `def default_model_provider_from_entries(models: list[dict[str, Any]]) -> str` | 源码未提供函数级文档字符串。 | [L34](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L34) |
| `def set_default_model_provider_in_entries(models: list[dict[str, Any]], provider: str) -> bool` | 源码未提供函数级文档字符串。 | [L42](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L42) |
| `def get_default_model_provider(config: dict[str, Any] \| None) -> str` | Return the effective default provider without constructing a Model. | [L59](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L59) |
| `def is_affinity_enabled(config: dict[str, Any] \| None) -> bool` | 源码未提供函数级文档字符串。 | [L86](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L86) |
| `def validate_affinity_invariant(config: dict[str, Any] \| None) -> tuple[bool, list[str]]` | 源码未提供函数级文档字符串。 | [L95](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L95) |
| `def normalize_affinity_request(params: dict[str, Any]) -> None` | Enforce switch/provider consistency on one mutable request payload. | [L118](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py#L118) |

## `jiuwenswarm/common/local_env_config.py`

[打开源码](../../../../../jiuwenswarm/common/local_env_config.py#L1)

**模块职责：** Process env tip bags for Track B; Track A stays in real ``os.environ``.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_HEADERS_ENV_KEY` | `未显式标注` | [L35](../../../../../jiuwenswarm/common/local_env_config.py#L35) |
| `_DEFAULT_HEADERS_ALIASES` | `未显式标注` | [L36](../../../../../jiuwenswarm/common/local_env_config.py#L36) |
| `_DEFAULT_HEADERS_FALLBACK_ALIASES` | `未显式标注` | [L43](../../../../../jiuwenswarm/common/local_env_config.py#L43) |
| `logger` | `未显式标注` | [L47](../../../../../jiuwenswarm/common/local_env_config.py#L47) |
| `SPAWN_ENV_KEYS` | `frozenset[str]` | [L53](../../../../../jiuwenswarm/common/local_env_config.py#L53) |
| `BUSINESS_MIRROR_KEYS` | `frozenset[str]` | [L89](../../../../../jiuwenswarm/common/local_env_config.py#L89) |
| `PROCESS_UNIQUE_ENV_KEYS` | `frozenset[str]` | [L179](../../../../../jiuwenswarm/common/local_env_config.py#L179) |
| `_LEGACY_PRODUCT_ENV_PREFIX` | `未显式标注` | [L186](../../../../../jiuwenswarm/common/local_env_config.py#L186) |
| `_CANONICAL_PRODUCT_ENV_PREFIX` | `未显式标注` | [L187](../../../../../jiuwenswarm/common/local_env_config.py#L187) |
| `_DEFAULT_SERVICE_ID` | `未显式标注` | [L258](../../../../../jiuwenswarm/common/local_env_config.py#L258) |
| `_DEFAULT_AGENT_ID` | `未显式标注` | [L259](../../../../../jiuwenswarm/common/local_env_config.py#L259) |
| `EnvNsKey` | `未显式标注` | [L260](../../../../../jiuwenswarm/common/local_env_config.py#L260) |
| `_active_bags` | `dict[EnvNsKey, dict[str, Any]]` | [L262](../../../../../jiuwenswarm/common/local_env_config.py#L262) |
| `_staged_bags` | `dict[EnvNsKey, dict[str, Any]]` | [L263](../../../../../jiuwenswarm/common/local_env_config.py#L263) |
| `_process_baseline` | `dict[str, str]` | [L265](../../../../../jiuwenswarm/common/local_env_config.py#L265) |
| `_UNBOUND` | `object` | [L268](../../../../../jiuwenswarm/common/local_env_config.py#L268) |
| `_task_env_overlay` | `ContextVar[Any]` | [L270](../../../../../jiuwenswarm/common/local_env_config.py#L270) |
| `_agent_env_ns` | `ContextVar[EnvNsKey \| None]` | [L273](../../../../../jiuwenswarm/common/local_env_config.py#L273) |
| `_mirrored_once` | `未显式标注` | [L277](../../../../../jiuwenswarm/common/local_env_config.py#L277) |
| `_EMPTY_OMIT_ENV_KEYS` | `frozenset[str]` | [L403](../../../../../jiuwenswarm/common/local_env_config.py#L403) |
| `ENV_CONFIG_DICT` | `MutableMapping[str, Any]` | [L770](../../../../../jiuwenswarm/common/local_env_config.py#L770) |
| `_LEGACY_OFFICE_CLAW_DISABLE_TOOL_CALLING` | `未显式标注` | [L943](../../../../../jiuwenswarm/common/local_env_config.py#L943) |
| `_LEGACY_OFFICE_CLAW_DISABLE_TRUTHY` | `未显式标注` | [L944](../../../../../jiuwenswarm/common/local_env_config.py#L944) |
| `_LEGACY_TOOL_CALLING_GUARD_STRIP_REASON` | `未显式标注` | [L945](../../../../../jiuwenswarm/common/local_env_config.py#L945) |

### [`class EnvNsIdError(ValueError)`](../../../../../jiuwenswarm/common/local_env_config.py#L280)

Raised when service_id / agent_id contains ``__`` or is otherwise invalid.

### [`class _ActiveEnvDict(MutableMapping[str, Any])`](../../../../../jiuwenswarm/common/local_env_config.py#L717)

MutableMapping proxy over the resolved active bag (default: default/default).

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _target(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L720](../../../../../jiuwenswarm/common/local_env_config.py#L720) |
| `def __getitem__(self, key: str) -> Any` | 源码未提供方法级文档字符串。 | [L723](../../../../../jiuwenswarm/common/local_env_config.py#L723) |
| `def __setitem__(self, key: str, value: Any) -> None` | 源码未提供方法级文档字符串。 | [L730](../../../../../jiuwenswarm/common/local_env_config.py#L730) |
| `def __delitem__(self, key: str) -> None` | 源码未提供方法级文档字符串。 | [L738](../../../../../jiuwenswarm/common/local_env_config.py#L738) |
| `def __iter__(self) -> Iterator[str]` | 源码未提供方法级文档字符串。 | [L742](../../../../../jiuwenswarm/common/local_env_config.py#L742) |
| `def __len__(self) -> int` | 源码未提供方法级文档字符串。 | [L745](../../../../../jiuwenswarm/common/local_env_config.py#L745) |
| `def clear(self) -> None` | 源码未提供方法级文档字符串。 | [L748](../../../../../jiuwenswarm/common/local_env_config.py#L748) |
| `def update(self, other: Mapping[Any, Any] \| Iterable[tuple[Any, Any]] = (), /, **kwargs: Any) -> None` | 源码未提供方法级文档字符串。 | [L753](../../../../../jiuwenswarm/common/local_env_config.py#L753) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def canonical_product_env_key(name: str) -> str` | Map ``JIUWENCLAW_*`` → ``JIUWENSWARM_*``; leave other keys unchanged. | [L190](../../../../../jiuwenswarm/common/local_env_config.py#L190) |
| `def legacy_product_env_key(name: str) -> str \| None` | Return the relay ``JIUWENCLAW_*`` alias for a ``JIUWENSWARM_*`` key. | [L198](../../../../../jiuwenswarm/common/local_env_config.py#L198) |
| `def normalize_product_env_aliases(env: Mapping[str, Any] \| None) -> dict[str, Any]` | Rewrite product legacy keys to canonical; canonical wins on clash. | [L206](../../../../../jiuwenswarm/common/local_env_config.py#L206) |
| `def env_keys_with_product_aliases(keys: Iterable[str]) -> frozenset[str]` | Expand a key set with both canonical and legacy product forms. | [L222](../../../../../jiuwenswarm/common/local_env_config.py#L222) |
| `def product_env_lookup_names(name: str) -> tuple[str, ...]` | Ordered names to probe for a product env key (canonical + relay alias). | [L239](../../../../../jiuwenswarm/common/local_env_config.py#L239) |
| `def normalize_env_ns_id(value: str \| None, *, default: str = _DEFAULT_AGENT_ID) -> str` | 源码未提供函数级文档字符串。 | [L284](../../../../../jiuwenswarm/common/local_env_config.py#L284) |
| `def get_bound_agent_env_ns() -> EnvNsKey \| None` | Return the currently bound (service_id, agent_id), or None if unbound. | [L296](../../../../../jiuwenswarm/common/local_env_config.py#L296) |
| `def resolve_env_ns(service_id: str \| None = None, agent_id: str \| None = None) -> EnvNsKey` | Resolve bag key: explicit args > ContextVar > default/default. | [L301](../../../../../jiuwenswarm/common/local_env_config.py#L301) |
| `def make_env_ns_key(service_id: str, agent_id: str, name: str) -> str` | 源码未提供函数级文档字符串。 | [L320](../../../../../jiuwenswarm/common/local_env_config.py#L320) |
| `def parse_env_ns_key(full_key: str) -> tuple[str, str, str] \| None` | 源码未提供函数级文档字符串。 | [L329](../../../../../jiuwenswarm/common/local_env_config.py#L329) |
| `def _bag(store: dict[EnvNsKey, dict[str, Any]], key: EnvNsKey) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L346](../../../../../jiuwenswarm/common/local_env_config.py#L346) |
| `def get_active_env(service_id: str \| None = None, agent_id: str \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L354](../../../../../jiuwenswarm/common/local_env_config.py#L354) |
| `def get_staged_env(service_id: str \| None = None, agent_id: str \| None = None) -> dict[str, Any]` | Return a copy of staged env overrides for the resolved ``(sid, aid)``. | [L361](../../../../../jiuwenswarm/common/local_env_config.py#L361) |
| `def clear_staged_env(service_id: str \| None = None, agent_id: str \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L369](../../../../../jiuwenswarm/common/local_env_config.py#L369) |
| `def effective_tip(service_id: str \| None = None, agent_id: str \| None = None) -> dict[str, Any]` | Formula B: ``active ∪ staged`` (staged wins). | [L377](../../../../../jiuwenswarm/common/local_env_config.py#L377) |
| `def _invalidate_resolved_config_cache(service_id: str \| None = None, agent_id: str \| None = None) -> None` | Drop get_config() resolved cache for this ns (lazy import avoids cycle). | [L388](../../../../../jiuwenswarm/common/local_env_config.py#L388) |
| `def stage_env_overrides(env_overrides: dict[str, Any] \| None, *, service_id: str \| None = None, agent_id: str \| None = None) -> None` | Merge env reload payload into staged bag without touching active. | [L414](../../../../../jiuwenswarm/common/local_env_config.py#L414) |
| `def promote_staged_env(*, service_id: str \| None = None, agent_id: str \| None = None) -> None` | Promote staged bag into active tip for this pair (tip-only). | [L442](../../../../../jiuwenswarm/common/local_env_config.py#L442) |
| `def _plaintext_tip_value(name: str, value: Any) -> str` | Store tip values as plaintext (decrypt ciphertext from .env / legacy). | [L464](../../../../../jiuwenswarm/common/local_env_config.py#L464) |
| `def _ensure_ciphertext(name: str, value: Any) -> str` | Store sensitive tip/baseline values as ciphertext; others unchanged. | [L472](../../../../../jiuwenswarm/common/local_env_config.py#L472) |
| `def seal_env_mapping(mapping: Mapping[str, Any] \| None) -> dict[str, Any]` | Copy a mapping with sensitive values sealed as ciphertext for long-lived stores. | [L490](../../../../../jiuwenswarm/common/local_env_config.py#L490) |
| `def materialize_env_mapping(mapping: Mapping[str, Any] \| None) -> dict[str, Any]` | Copy a mapping with sensitive values decrypted for short-lived use. | [L504](../../../../../jiuwenswarm/common/local_env_config.py#L504) |
| `def _pop_bare_if_default_default(service_id: str, agent_id: str, name: str) -> None` | Pop residual bare Track B key only for the default/default bag. | [L520](../../../../../jiuwenswarm/common/local_env_config.py#L520) |
| `def pop_track_b_bare_from_environ() -> list[str]` | Remove Track B bare keys from ``os.environ`` (H1 hygiene / after load_dotenv). | [L526](../../../../../jiuwenswarm/common/local_env_config.py#L526) |
| `def apply_env_overrides_to_active(env_overrides: dict[str, Any] \| None, *, service_id: str \| None = None, agent_id: str \| None = None) -> None` | Write env overrides directly to active tip (cold start / incremental). | [L544](../../../../../jiuwenswarm/common/local_env_config.py#L544) |
| `def replace_active_env(env_overrides: dict[str, Any] \| None, *, service_id: str \| None = None, agent_id: str \| None = None, clear_staged: bool = True) -> None` | Full-replace active tip for one ``(sid, aid)`` (sync path). | [L574](../../../../../jiuwenswarm/common/local_env_config.py#L574) |
| `def clear_agent_env_ns(service_id: str, agent_id: str) -> None` | Wipe staged + active tip for one ``(service_id, agent_id)`` pair. | [L606](../../../../../jiuwenswarm/common/local_env_config.py#L606) |
| `def apply_env_removals(removals: dict[str, None] \| None, *, service_id: str \| None = None, agent_id: str \| None = None) -> None` | Remove env keys from active and staged tip for one pair. | [L622](../../../../../jiuwenswarm/common/local_env_config.py#L622) |
| `def build_effective_env_overlay(*extra: dict[str, Any] \| None, service_id: str \| None = None, agent_id: str \| None = None) -> dict[str, Any]` | Formula B tip, then merge optional extras (extras win; ``None`` pops). | [L648](../../../../../jiuwenswarm/common/local_env_config.py#L648) |
| `def bind_agent_env_ns(service_id: str, agent_id: str) -> Token` | Bind tip env ns ``(service_id, agent_id)`` for this task. | [L673](../../../../../jiuwenswarm/common/local_env_config.py#L673) |
| `def reset_agent_env_ns(token: Token) -> None` | 源码未提供函数级文档字符串。 | [L679](../../../../../jiuwenswarm/common/local_env_config.py#L679) |
| `def bind_task_env_overlay(overlay: dict[str, Any] \| None) -> Token` | Bind task-scoped overlay. Always binds a dict (``None`` → ``{}``). | [L683](../../../../../jiuwenswarm/common/local_env_config.py#L683) |
| `def reset_task_env_overlay(token: Token) -> None` | 源码未提供函数级文档字符串。 | [L696](../../../../../jiuwenswarm/common/local_env_config.py#L696) |
| `def get_task_env_overlay() -> dict[str, Any] \| None` | Return current overlay if bound; ``None`` when unbound. | [L700](../../../../../jiuwenswarm/common/local_env_config.py#L700) |
| `def is_task_env_overlay_bound() -> bool` | 源码未提供函数级文档字符串。 | [L708](../../../../../jiuwenswarm/common/local_env_config.py#L708) |
| `def set_os_environ(name: str, value: Any, *, service_id: str \| None = None, agent_id: str \| None = None) -> None` | Write Track B active tip only (plaintext). Does not touch ``os.environ``. | [L778](../../../../../jiuwenswarm/common/local_env_config.py#L778) |
| `def get_os_environ(name: str, default: Any = None, *, service_id: str \| None = None, agent_id: str \| None = None) -> Any` | Read Track B from tip only (compat alias; prefer ``get_local_config``). | [L799](../../../../../jiuwenswarm/common/local_env_config.py#L799) |
| `def export_agent_environ(service_id: str, agent_id: str) -> dict[str, str]` | Tip (Track B, plaintext) ∪ Track A spawn keys ∪ Windows platform vars. | [L816](../../../../../jiuwenswarm/common/local_env_config.py#L816) |
| `def export_spawn_environ() -> dict[str, str]` | Return only process-shared keys that are safe for a child process. | [L841](../../../../../jiuwenswarm/common/local_env_config.py#L841) |
| `def _ensure_windows_platform_env(out: dict[str, str]) -> None` | Pass through OS-level vars a Windows child process needs to function. | [L857](../../../../../jiuwenswarm/common/local_env_config.py#L857) |
| `def get_process_baseline() -> dict[str, str]` | Return a copy of the process-shared Track B baseline (from ``.env``). | [L882](../../../../../jiuwenswarm/common/local_env_config.py#L882) |
| `def update_process_baseline(updates: Mapping[str, Any] \| None) -> None` | Merge plaintext Track B keys into process baseline (Web/CLI persist). | [L887](../../../../../jiuwenswarm/common/local_env_config.py#L887) |
| `def apply_process_baseline_gaps(service_id: str \| None, agent_id: str \| None, *, reserved_keys: Iterable[str] \| None = None) -> None` | Copy baseline keys not in ``reserved_keys`` into the agent tip. | [L904](../../../../../jiuwenswarm/common/local_env_config.py#L904) |
| `def hydrate_default_tip_from_baseline() -> None` | Local cold-start: copy entire baseline into ``default/default`` tip. | [L924](../../../../../jiuwenswarm/common/local_env_config.py#L924) |
| `def is_enterprise() -> bool` | True if JIUWENSWARM_EDITION is 'enterprise' (企业版). | [L933](../../../../../jiuwenswarm/common/local_env_config.py#L933) |
| `def should_hydrate_default_tip() -> bool` | True for local processes; False when enterprise edition. | [L938](../../../../../jiuwenswarm/common/local_env_config.py#L938) |
| `def _map_legacy_office_claw_disable_tool_calling() -> bool` | Map deprecated ``OFFICE_CLAW_DISABLE_TOOL_CALLING`` into Guard tip keys. | [L948](../../../../../jiuwenswarm/common/local_env_config.py#L948) |
| `def _ingest_legacy_guard_keys_into_baseline() -> None` | Copy mapped Guard keys from ``os.environ`` into baseline (secondary ingest). | [L973](../../../../../jiuwenswarm/common/local_env_config.py#L973) |
| `def ingest_bare_business_into_tip(*, force: bool = False) -> None` | After ``load_dotenv``: bare Track B → process_baseline, then pop bare. | [L987](../../../../../jiuwenswarm/common/local_env_config.py#L987) |
| `def ingest_bare_business_into_baseline(*, force: bool = False) -> None` | Alias for :func:`ingest_bare_business_into_tip` (baseline + optional hydrate). | [L1041](../../../../../jiuwenswarm/common/local_env_config.py#L1041) |
| `def mirror_bare_business_env_to_default_ns(*, force: bool = False) -> None` | Compat alias for :func:`ingest_bare_business_into_tip`. | [L1046](../../../../../jiuwenswarm/common/local_env_config.py#L1046) |
| `def _mapping_has_product_key(mapping: Mapping[str, Any], name: str) -> bool` | 源码未提供函数级文档字符串。 | [L1056](../../../../../jiuwenswarm/common/local_env_config.py#L1056) |
| `def _read_from_mapping(name: str, mapping: dict[str, Any], default: Any = None) -> Any` | 源码未提供函数级文档字符串。 | [L1060](../../../../../jiuwenswarm/common/local_env_config.py#L1060) |
| `def get_local_config(name: str, default = None)` | Track-B reader: bound overlay (seal) → formula B tip → process env. | [L1071](../../../../../jiuwenswarm/common/local_env_config.py#L1071) |
| `def read_env(name: str, default: str = '') -> str` | Overlay-aware tip reader for hot-reload paths. | [L1105](../../../../../jiuwenswarm/common/local_env_config.py#L1105) |
| `def read_env_if_set(name: str) -> str \| None` | Return env value when *name* is explicitly set. | [L1114](../../../../../jiuwenswarm/common/local_env_config.py#L1114) |
| `def read_default_headers_raw() -> str` | Overlay-aware raw JSON string for default HTTP headers. | [L1147](../../../../../jiuwenswarm/common/local_env_config.py#L1147) |
| `def parse_default_headers(raw: str) -> dict[str, str] \| None` | Parse and validate default_headers JSON; return None when empty. | [L1164](../../../../../jiuwenswarm/common/local_env_config.py#L1164) |
| `def read_default_headers() -> dict[str, str] \| None` | Read overlay-aware default_headers as a header map. | [L1178](../../../../../jiuwenswarm/common/local_env_config.py#L1178) |
| `def is_sensitive_env_name(name: str) -> bool` | 源码未提供函数级文档字符串。 | [L1183](../../../../../jiuwenswarm/common/local_env_config.py#L1183) |
| `def set_local_config(name: str, value) -> None` | Legacy tip write for current ns (prefer :func:`set_os_environ`). | [L1194](../../../../../jiuwenswarm/common/local_env_config.py#L1194) |
| `def decrypt(name, cipher)` | 源码未提供函数级文档字符串。 | [L1202](../../../../../jiuwenswarm/common/local_env_config.py#L1202) |
| `def encrypt(name, text)` | 源码未提供函数级文档字符串。 | [L1214](../../../../../jiuwenswarm/common/local_env_config.py#L1214) |
| `def reset_local_env_state_for_tests() -> None` | Clear bags + baseline + unbound overlay/ns ContextVars (unit tests only). | [L1226](../../../../../jiuwenswarm/common/local_env_config.py#L1226) |

## `jiuwenswarm/common/log_preview.py`

[打开源码](../../../../../jiuwenswarm/common/log_preview.py#L1)

**模块职责：** Bounded text previews for single-line log entries.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_PREVIEW_MAX_CHARS` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/log_preview.py#L19) |
| `_PREVIEW_ENV_VAR` | `未显式标注` | [L23](../../../../../jiuwenswarm/common/log_preview.py#L23) |
| `_FALSE_VALUES` | `未显式标注` | [L24](../../../../../jiuwenswarm/common/log_preview.py#L24) |
| `__all__` | `未显式标注` | [L71](../../../../../jiuwenswarm/common/log_preview.py#L71) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _preview_user_content_enabled() -> bool` | Return whether log previews may include user-authored text. | [L27](../../../../../jiuwenswarm/common/log_preview.py#L27) |
| `def preview_text(value: Any, limit: int = DEFAULT_PREVIEW_MAX_CHARS) -> str` | Render a value as a bounded single-line log fragment. | [L51](../../../../../jiuwenswarm/common/log_preview.py#L51) |

## `jiuwenswarm/common/mcp_call_timeout_patch.py`

[打开源码](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L1)

**模块职责：** Inject a per-call timeout into openjiuwen's MCP HTTP clients.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_PATCHED` | `未显式标注` | [L44](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L44) |
| `_wrapped_methods` | `set[tuple[type, str]]` | [L48](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L48) |
| `DEFAULT_CALL_TIMEOUT` | `未显式标注` | [L50](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L50) |
| `__all__` | `未显式标注` | [L52](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L52) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def apply_mcp_call_timeout_patch(default_timeout: float = DEFAULT_CALL_TIMEOUT) -> None` | Apply the per-call MCP timeout patch. Idempotent per process. | [L55](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py#L55) |

## `jiuwenswarm/common/mcp_config.py`

[打开源码](../../../../../jiuwenswarm/common/mcp_config.py#L1)

**模块职责：** Helpers for converting ``config.yaml`` MCP entries to runtime configs.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_HTTP_MCP_TRANSPORTS` | `未显式标注` | [L40](../../../../../jiuwenswarm/common/mcp_config.py#L40) |
| `_DANGEROUS_ARGS_PATTERN` | `未显式标注` | [L287](../../../../../jiuwenswarm/common/mcp_config.py#L287) |
| `_REQUEST_REMOTE_BLOCKED_HOSTS` | `未显式标注` | [L432](../../../../../jiuwenswarm/common/mcp_config.py#L432) |
| `_REQUEST_REMOTE_METADATA_HOSTS` | `未显式标注` | [L441](../../../../../jiuwenswarm/common/mcp_config.py#L441) |
| `_OFFICE_CLAW_MCP_ENV_KEYS` | `未显式标注` | [L681](../../../../../jiuwenswarm/common/mcp_config.py#L681) |
| `_OFFICE_CLAW_MCP_SCHEMA_CACHE_ENV` | `未显式标注` | [L694](../../../../../jiuwenswarm/common/mcp_config.py#L694) |
| `_OFFICE_CLAW_MCP_SCHEMA_CACHE_OFF` | `未显式标注` | [L695](../../../../../jiuwenswarm/common/mcp_config.py#L695) |
| `_office_claw_mcp_schema_cache` | `dict[str, list[dict[str, Any]]]` | [L696](../../../../../jiuwenswarm/common/mcp_config.py#L696) |
| `_office_claw_mcp_schema_inflight` | `dict[tuple[int, int, str], asyncio.Task[list[dict[str, Any]]]]` | [L697](../../../../../jiuwenswarm/common/mcp_config.py#L697) |
| `_office_claw_mcp_schema_cache_lock` | `未显式标注` | [L700](../../../../../jiuwenswarm/common/mcp_config.py#L700) |
| `_office_claw_mcp_schema_generation` | `未显式标注` | [L701](../../../../../jiuwenswarm/common/mcp_config.py#L701) |
| `_MCP_CALL_TOOL_TIMEOUT_S` | `未显式标注` | [L830](../../../../../jiuwenswarm/common/mcp_config.py#L830) |
| `_MCP_CONNECTOR_DISCOVERY_TIMEOUT_S` | `未显式标注` | [L831](../../../../../jiuwenswarm/common/mcp_config.py#L831) |
| `_request_scoped_mcp_sessions` | `dict[tuple[str, str], _PooledMcpWorker]` | [L874](../../../../../jiuwenswarm/common/mcp_config.py#L874) |
| `_request_scoped_mcp_build_lock` | `未显式标注` | [L876](../../../../../jiuwenswarm/common/mcp_config.py#L876) |
| `OFFICE_CLAW_REQUEST_TOOL_ID_PREFIX` | `未显式标注` | [L1199](../../../../../jiuwenswarm/common/mcp_config.py#L1199) |
| `OFFICE_CLAW_EXPECTED_TOOL_IDS_KWARG` | `未显式标注` | [L1200](../../../../../jiuwenswarm/common/mcp_config.py#L1200) |
| `_OFFICE_CLAW_TOOL_IDS_ATTR` | `未显式标注` | [L1209](../../../../../jiuwenswarm/common/mcp_config.py#L1209) |
| `_active_office_claw_tool_ids` | `ContextVar[frozenset[str] \| None]` | [L1214](../../../../../jiuwenswarm/common/mcp_config.py#L1214) |
| `_live_office_claw_allowlists_by_tool_id` | `dict[str, frozenset[str]]` | [L1222](../../../../../jiuwenswarm/common/mcp_config.py#L1222) |
| `_live_office_claw_registration_count_by_tool_name` | `dict[str, int]` | [L1223](../../../../../jiuwenswarm/common/mcp_config.py#L1223) |
| `_live_office_claw_tool_instances` | `weakref.WeakKeyDictionary[Any, frozenset[str]]` | [L1224](../../../../../jiuwenswarm/common/mcp_config.py#L1224) |
| `_live_office_claw_allowlist_lock` | `未显式标注` | [L1227](../../../../../jiuwenswarm/common/mcp_config.py#L1227) |
| `__all__` | `未显式标注` | [L2064](../../../../../jiuwenswarm/common/mcp_config.py#L2064) |

### [`class OfficeClawMcpRegistration`](../../../../../jiuwenswarm/common/mcp_config.py#L809)

Tools installed on one session agent for one Relay request.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L812](../../../../../jiuwenswarm/common/mcp_config.py#L812) |
| `tool_ids` | `tuple[str, ...]` | `—` | [L813](../../../../../jiuwenswarm/common/mcp_config.py#L813) |
| `tool_names` | `tuple[str, ...]` | `—` | [L814](../../../../../jiuwenswarm/common/mcp_config.py#L814) |
| `tool_instances` | `tuple[RequestScopedOfficeClawMcpTool, ...]` | `()` | [L815](../../../../../jiuwenswarm/common/mcp_config.py#L815) |

### [`class _McpCallRequest`](../../../../../jiuwenswarm/common/mcp_config.py#L834)

One call_tool request handed from an invoke task to the owner task.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('tool_name', 'arguments', 'future')` | [L837](../../../../../jiuwenswarm/common/mcp_config.py#L837) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L839](../../../../../jiuwenswarm/common/mcp_config.py#L839) |

### [`class _PooledMcpWorker`](../../../../../jiuwenswarm/common/mcp_config.py#L845)

Owns one stdio MCP process+session in a dedicated asyncio task.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('queue', 'task', 'server_name')` | [L851](../../../../../jiuwenswarm/common/mcp_config.py#L851) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, server_name: str) -> None` | 源码未提供方法级文档字符串。 | [L853](../../../../../jiuwenswarm/common/mcp_config.py#L853) |
| `@property def alive(self) -> bool` | 源码未提供方法级文档字符串。 | [L859](../../../../../jiuwenswarm/common/mcp_config.py#L859) |
| `async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any` | Submit a call_tool to the owner task and await its result. | [L862](../../../../../jiuwenswarm/common/mcp_config.py#L862) |

### [`class _RemoteMcpCallAdapter`](../../../../../jiuwenswarm/common/mcp_config.py#L1049)

让 remote MCP client 的 call_tool 返回形状对齐 stdio ClientSession。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, client: Any) -> None` | 源码未提供方法级文档字符串。 | [L1060](../../../../../jiuwenswarm/common/mcp_config.py#L1060) |
| `async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any` | 源码未提供方法级文档字符串。 | [L1063](../../../../../jiuwenswarm/common/mcp_config.py#L1063) |

### [`class RequestScopedOfficeClawMcpTool(Tool)`](../../../../../jiuwenswarm/common/mcp_config.py#L1953)

Invoke one OfficeClaw tool through a long-lived request-scoped process.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, card: ToolCard, params: Mapping[str, Any], request_id: str = '', server_name: str = '') -> None` | 源码未提供方法级文档字符串。 | [L1963](../../../../../jiuwenswarm/common/mcp_config.py#L1963) |
| `async def stream(self, inputs: Any, **kwargs: Any)` | 源码未提供方法级文档字符串。 | [L1975](../../../../../jiuwenswarm/common/mcp_config.py#L1975) |
| `async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L1978](../../../../../jiuwenswarm/common/mcp_config.py#L1978) |
| `def _resolve_unbound_office_claw_allowlist(self, tool_id: str, kwargs: Mapping[str, Any]) -> frozenset[str] \| None` | Re-bind only when the caller or live registry proves ownership. | [L1989](../../../../../jiuwenswarm/common/mcp_config.py#L1989) |
| `async def _invoke_with_active_binding(self, inputs: Any, **kwargs: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L2016](../../../../../jiuwenswarm/common/mcp_config.py#L2016) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def extract_enabled_mcp_server_entries(config_base: dict[str, Any]) -> list[dict[str, Any]]` | Return enabled ``mcp.servers`` entries from a resolved config mapping. | [L43](../../../../../jiuwenswarm/common/mcp_config.py#L43) |
| `def build_mcp_server_config(entry: dict[str, Any], *, server_id_scope: str \| None = None) -> McpServerConfig \| None` | Build a ``McpServerConfig`` from one ``mcp.servers`` entry. | [L66](../../../../../jiuwenswarm/common/mcp_config.py#L66) |
| `def build_enabled_mcp_server_configs(config_base: dict[str, Any], *, server_id_scope: str \| None = None) -> list[McpServerConfig]` | Build all enabled MCP server configs, skipping invalid entries. | [L133](../../../../../jiuwenswarm/common/mcp_config.py#L133) |
| `async def preflight_mcp_server_reachable(cfg: McpServerConfig, *, timeout: float = 3.0) -> tuple[bool, str]` | Cheap reachability probe for HTTP-based MCP servers. | [L147](../../../../../jiuwenswarm/common/mcp_config.py#L147) |
| `def is_asyncio_outer_cancellation() -> bool` | Return True when the current task has a pending outer cancel request. | [L201](../../../../../jiuwenswarm/common/mcp_config.py#L201) |
| `def _stable_mcp_server_id(scope: str, name: str, payload: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L213](../../../../../jiuwenswarm/common/mcp_config.py#L213) |
| `def _safe_id_part(value: str, *, default: str) -> str` | 源码未提供函数级文档字符串。 | [L229](../../../../../jiuwenswarm/common/mcp_config.py#L229) |
| `def _normalize_stdio_command_kind(command: str) -> str` | 将 command 归一化为 'node'、'python'、'npx' 或 'uvx'。 | [L234](../../../../../jiuwenswarm/common/mcp_config.py#L234) |
| `def _normalize_mcp_client_type(raw_type: object) -> str` | 源码未提供函数级文档字符串。 | [L258](../../../../../jiuwenswarm/common/mcp_config.py#L258) |
| `def _pick_mcp_url(tool_config: dict) -> str` | 源码未提供函数级文档字符串。 | [L271](../../../../../jiuwenswarm/common/mcp_config.py#L271) |
| `def _optional_auth_dict(tool_config: dict, key: str) -> dict \| None` | 源码未提供函数级文档字符串。 | [L278](../../../../../jiuwenswarm/common/mcp_config.py#L278) |
| `def _check_dangerous_args(tool_name: str, args: list) -> None` | 源码未提供函数级文档字符串。 | [L298](../../../../../jiuwenswarm/common/mcp_config.py#L298) |
| `def _trusted_cat_cafe_stdio_roots() -> list[Path]` | 源码未提供函数级文档字符串。 | [L316](../../../../../jiuwenswarm/common/mcp_config.py#L316) |
| `def _path_is_under_trusted_root(path: Path, roots: list[Path]) -> bool` | 源码未提供函数级文档字符串。 | [L360](../../../../../jiuwenswarm/common/mcp_config.py#L360) |
| `def _validate_cat_cafe_request_scoped_stdio(params: dict[str, Any]) -> None` | 限制请求级 stdio：禁止内联代码执行面，脚本路径须在受信根目录下。 | [L374](../../../../../jiuwenswarm/common/mcp_config.py#L374) |
| `def _loopback_mcp_allowed() -> bool` | 源码未提供函数级文档字符串。 | [L450](../../../../../jiuwenswarm/common/mcp_config.py#L450) |
| `def _is_blocked_host(host: str) -> bool` | 源码未提供函数级文档字符串。 | [L458](../../../../../jiuwenswarm/common/mcp_config.py#L458) |
| `def _validate_request_scoped_remote_mcp(tool_name: str, cfg: dict) -> None` | 源码未提供函数级文档字符串。 | [L485](../../../../../jiuwenswarm/common/mcp_config.py#L485) |
| `def create_mcp_tool(config_str: str) -> McpServerConfig` | 从 JSON 字符串解析并构造 ``McpServerConfig``。 | [L522](../../../../../jiuwenswarm/common/mcp_config.py#L522) |
| `def _office_claw_mcp_schema_cache_enabled() -> bool` | 源码未提供函数级文档字符串。 | [L704](../../../../../jiuwenswarm/common/mcp_config.py#L704) |
| `def _office_claw_mcp_build_fingerprint(params: Mapping[str, Any]) -> list[dict[str, Any]]` | Fingerprint the MCP bundle file(s) so a rebuild rotates the cache key. | [L711](../../../../../jiuwenswarm/common/mcp_config.py#L711) |
| `def _office_claw_mcp_schema_cache_key(params: Mapping[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L768](../../../../../jiuwenswarm/common/mcp_config.py#L768) |
| `def invalidate_office_claw_mcp_schema_cache() -> None` | Drop the entire OfficeClaw MCP schema cache and abort coalesced discovery. | [L788](../../../../../jiuwenswarm/common/mcp_config.py#L788) |
| `def _clear_office_claw_mcp_schema_cache_for_tests() -> None` | 源码未提供函数级文档字符串。 | [L804](../../../../../jiuwenswarm/common/mcp_config.py#L804) |
| `async def _run_mcp_worker(params: Mapping[str, Any], worker: _PooledMcpWorker) -> None` | Owner task：持有 MCP client 句柄，排干调用队列。 | [L879](../../../../../jiuwenswarm/common/mcp_config.py#L879) |
| `async def _enter_stdio_mcp_session(stack: AsyncExitStack, params: Mapping[str, Any]) -> Any` | stdio transport：起子进程 ClientSession，生命周期交由 stack 管理。 | [L977](../../../../../jiuwenswarm/common/mcp_config.py#L977) |
| `def _build_remote_mcp_config(server_name: str, params: Mapping[str, Any], client_type: str) -> Any` | 从 worker params（或 connect_params）重建 ``McpServerConfig``。 | [L992](../../../../../jiuwenswarm/common/mcp_config.py#L992) |
| `async def _enter_remote_mcp_session(stack: AsyncExitStack, params: Mapping[str, Any], client_type: str) -> Any` | sse/streamable-http transport：复用 openjiuwen 高层 client，长连接复用。 | [L1015](../../../../../jiuwenswarm/common/mcp_config.py#L1015) |
| `def _remote_mcp_result_to_text(raw: Any) -> str` | 把 extract_mcp_tool_result_content 的裸返回值规整成可读文本。 | [L1076](../../../../../jiuwenswarm/common/mcp_config.py#L1076) |
| `async def _drain_queue_with_error(worker: _PooledMcpWorker, exc: BaseException) -> None` | Fail any already-queued callers when the worker cannot start. | [L1094](../../../../../jiuwenswarm/common/mcp_config.py#L1094) |
| `async def acquire_request_scoped_mcp_session(request_id: str, server_name: str, params: Mapping[str, Any], *, force_rebuild: bool = False) -> Any` | Return a long-lived ``_PooledMcpWorker`` for this request+connector. | [L1105](../../../../../jiuwenswarm/common/mcp_config.py#L1105) |
| `async def _close_pooled_worker(worker: _PooledMcpWorker, key: tuple[str, str]) -> None` | Best-effort stop+remove one pooled worker (kills the stdio process). | [L1138](../../../../../jiuwenswarm/common/mcp_config.py#L1138) |
| `async def release_request_scoped_mcp_sessions(request_id: str) -> None` | Stop every long-lived MCP worker for one request (cleanup hook). | [L1187](../../../../../jiuwenswarm/common/mcp_config.py#L1187) |
| `def _office_claw_tool_name_from_tool_id(tool_id: str) -> str` | 源码未提供函数级文档字符串。 | [L1230](../../../../../jiuwenswarm/common/mcp_config.py#L1230) |
| `def publish_live_office_claw_allowlist(tool_ids: Iterable[str]) -> None` | Publish a request's OfficeClaw tool ids for interaction-round re-bind. | [L1238](../../../../../jiuwenswarm/common/mcp_config.py#L1238) |
| `def revoke_live_office_claw_allowlist(tool_ids: Iterable[str]) -> None` | Drop live allowlist entries after request-scoped MCP cleanup. | [L1255](../../../../../jiuwenswarm/common/mcp_config.py#L1255) |
| `def get_live_office_claw_allowlist_for_tool_id(tool_id: str) -> frozenset[str] \| None` | Return the live allowlist that currently owns ``tool_id``, if any. | [L1275](../../../../../jiuwenswarm/common/mcp_config.py#L1275) |
| `def is_office_claw_tool_name_live_concurrent(tool_name: str) -> bool` | True when more than one live request-scoped registration owns ``tool_name``. | [L1285](../../../../../jiuwenswarm/common/mcp_config.py#L1285) |
| `def register_live_office_claw_tool_instance(tool: RequestScopedOfficeClawMcpTool, tool_ids: Iterable[str]) -> None` | Track one request-scoped tool object for safe unbound re-bind. | [L1295](../../../../../jiuwenswarm/common/mcp_config.py#L1295) |
| `def unregister_live_office_claw_tool_instance(tool: RequestScopedOfficeClawMcpTool) -> None` | Drop a request-scoped tool object from the live instance registry. | [L1307](../../../../../jiuwenswarm/common/mcp_config.py#L1307) |
| `def get_live_office_claw_allowlist_for_tool_instance(tool: RequestScopedOfficeClawMcpTool) -> frozenset[str] \| None` | Return the allowlist registered for this tool object, if still live. | [L1314](../../../../../jiuwenswarm/common/mcp_config.py#L1314) |
| `def _clear_live_office_claw_allowlists_for_tests() -> None` | 源码未提供函数级文档字符串。 | [L1323](../../../../../jiuwenswarm/common/mcp_config.py#L1323) |
| `def _office_claw_tool_ids_carrier(agent: Any) -> Any` | Return the object that carries the request-scoped allowlist. | [L1330](../../../../../jiuwenswarm/common/mcp_config.py#L1330) |
| `@contextmanager def bind_active_office_claw_mcp_tools(tool_ids: Iterable[str] \| None) -> Iterator[None]` | Bind the request-scoped OfficeClaw tool id allowlist for this task. | [L1349](../../../../../jiuwenswarm/common/mcp_config.py#L1349) |
| `def set_agent_office_claw_tool_ids(agent: Any, tool_ids: Iterable[str] \| None) -> None` | Store the request-scoped OfficeClaw tool id allowlist on the shared ability_manager. | [L1362](../../../../../jiuwenswarm/common/mcp_config.py#L1362) |
| `def clear_agent_office_claw_tool_ids(agent: Any) -> None` | Remove the request-scoped allowlist from the shared ability_manager. | [L1386](../../../../../jiuwenswarm/common/mcp_config.py#L1386) |
| `@contextmanager def bind_office_claw_from_agent(agent: Any) -> Iterator[None]` | Re-bind the ContextVar from the shared ability_manager for the current task. | [L1399](../../../../../jiuwenswarm/common/mcp_config.py#L1399) |
| `def ensure_request_scoped_office_claw_tool_allowed(tool_id: str) -> None` | Refuse request-scoped OfficeClaw tools outside the active request allowlist. | [L1425](../../../../../jiuwenswarm/common/mcp_config.py#L1425) |
| `def get_active_office_claw_mcp_tool_ids() -> frozenset[str] \| None` | Return the request-local OfficeClaw tool id allowlist, if bound. | [L1443](../../../../../jiuwenswarm/common/mcp_config.py#L1443) |
| `def resolve_active_office_claw_tool_id(tool_name: str) -> str \| None` | Map a short tool name to this request's OfficeClaw tool id. | [L1449](../../../../../jiuwenswarm/common/mcp_config.py#L1449) |
| `def extract_office_claw_mcp(params: Any) -> dict[str, Any] \| None` | Return only the legacy ``office_claw_mcp`` request field. | [L1484](../../../../../jiuwenswarm/common/mcp_config.py#L1484) |
| `def extract_request_mcp_servers(params: Any) -> dict[str, dict[str, Any]] \| None` | 取 ``request_mcp_servers.mcpServers`` 的用户连接器 map。 | [L1495](../../../../../jiuwenswarm/common/mcp_config.py#L1495) |
| `def _normalized_path(value: str) -> str` | 源码未提供函数级文档字符串。 | [L1520](../../../../../jiuwenswarm/common/mcp_config.py#L1520) |
| `def validate_office_claw_mcp_config(config: Mapping[str, Any], *, environ: Mapping[str, str] \| None = None) -> dict[str, Any]` | Validate request config against Relay's startup-time MCP identity. | [L1524](../../../../../jiuwenswarm/common/mcp_config.py#L1524) |
| `def _stdio_server_parameters(params: Mapping[str, Any])` | 源码未提供函数级文档字符串。 | [L1596](../../../../../jiuwenswarm/common/mcp_config.py#L1596) |
| `async def _list_office_claw_mcp_tools_uncached(params: Mapping[str, Any]) -> list[dict[str, Any]]` | Start OfficeClaw MCP once, collect its tool schemas, then stop it. | [L1608](../../../../../jiuwenswarm/common/mcp_config.py#L1608) |
| `async def list_request_mcp_server_tools(server_name: str, config: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]` | 发现单个用户连接器的 tool schema。 | [L1638](../../../../../jiuwenswarm/common/mcp_config.py#L1638) |
| `async def _list_remote_mcp_connector_tools(server_name: str, server_cfg: Any, client_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]` | sse/streamable-http 连接器发现：复用 openjiuwen 的高层 MCP client。 | [L1726](../../../../../jiuwenswarm/common/mcp_config.py#L1726) |
| `def _remote_mcp_client_cls(client_type: str) -> Any \| None` | 返回 sse/streamable-http 对应的高层 MCP client 类（openjiuwen 提供）。 | [L1836](../../../../../jiuwenswarm/common/mcp_config.py#L1836) |
| `def _extract_mcp_tool_defs(response: Any) -> list[dict[str, Any]]` | 从 mcp ClientSession.list_tools() 的响应里抽 tool schema（stdio 发现复用）。 | [L1858](../../../../../jiuwenswarm/common/mcp_config.py#L1858) |
| `async def _discover_and_cache_office_claw_mcp_schema(params: Mapping[str, Any], cache_key: str, loop_key: tuple[int, int, str], generation: int) -> list[dict[str, Any]]` | Producer: run one discovery, write the cache, then clean up in-flight. | [L1871](../../../../../jiuwenswarm/common/mcp_config.py#L1871) |
| `async def list_office_claw_mcp_tools(params: Mapping[str, Any]) -> list[dict[str, Any]]` | Return immutable OfficeClaw tool schemas, coalescing identical discovery. | [L1906](../../../../../jiuwenswarm/common/mcp_config.py#L1906) |

## `jiuwenswarm/common/model_config_validation.py`

[打开源码](../../../../../jiuwenswarm/common/model_config_validation.py#L1)

**模块职责：** Shared validation helpers for model configuration values.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `PLACEHOLDER_API_BASES` | `未显式标注` | [L7](../../../../../jiuwenswarm/common/model_config_validation.py#L7) |
| `EXAMPLE_DOMAINS` | `未显式标注` | [L8](../../../../../jiuwenswarm/common/model_config_validation.py#L8) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_placeholder_api_base(api_base: str) -> bool` | Return True when api_base is a documentation placeholder URL. | [L11](../../../../../jiuwenswarm/common/model_config_validation.py#L11) |

## `jiuwenswarm/common/openjiuwen_logging.py`

[打开源码](../../../../../jiuwenswarm/common/openjiuwen_logging.py#L1)

**模块职责：** Bootstrap openjiuwen file logging under ``agent/.logs/openjiuwen``.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _pin_openjiuwen_log_path(log_root) -> None` | Pin openjiuwen log files under ``log_root`` (compat across openjiuwen versions). | [L14](../../../../../jiuwenswarm/common/openjiuwen_logging.py#L14) |
| `def bootstrap_openjiuwen_logging() -> bool` | Optionally load logging.yaml, pin log_path, and set default levels. | [L37](../../../../../jiuwenswarm/common/openjiuwen_logging.py#L37) |

## `jiuwenswarm/common/openjiuwen_rail_compat.py`

[打开源码](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L1)

**模块职责：** Drop newer evolution-rail kwargs when the installed openjiuwen SDK is older.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L11) |
| `_COMPAT_FLAG` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L13) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def filter_unsupported_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]` | Drop kwargs that ``func`` cannot accept unless it already takes **kwargs. | [L16](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L16) |
| `def _wrap_init_for_extra_kwargs(cls: type) -> None` | Allow newer trajectory/signal kwargs against older rail constructors. | [L31](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L31) |
| `def install_evolution_rail_kwargs_compat() -> None` | Older rails may lack signal_trigger / trajectory_span_processor parameters. | [L44](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py#L44) |

## `jiuwenswarm/common/openrouter_attribution.py`

[打开源码](../../../../../jiuwenswarm/common/openrouter_attribution.py#L1)

**模块职责：** 定义 is_openrouter_provider、inject_attribution_headers、inject_attribution_to_config。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `OPENROUTER_ATTRIBUTION_HEADERS` | `dict[str, str]` | [L7](../../../../../jiuwenswarm/common/openrouter_attribution.py#L7) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_openrouter_provider(provider: Optional[str]) -> bool` | 源码未提供函数级文档字符串。 | [L17](../../../../../jiuwenswarm/common/openrouter_attribution.py#L17) |
| `def inject_attribution_headers(mcc: dict[str, Any]) -> dict[str, Any]` | Inject OpenRouter attribution headers into model_client_config dict. | [L23](../../../../../jiuwenswarm/common/openrouter_attribution.py#L23) |
| `def inject_attribution_to_config(config: dict[str, Any]) -> None` | Inject OpenRouter attribution headers into all model_client_config entries in-place. | [L45](../../../../../jiuwenswarm/common/openrouter_attribution.py#L45) |

## `jiuwenswarm/common/reasoning_config.py`

[打开源码](../../../../../jiuwenswarm/common/reasoning_config.py#L1)

**模块职责：** 定义 _normalize_provider、_parse_api_base、resolve_reasoning_provider_kind、normalize_reasoning_level、resolve_reasoning_target。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `ReasoningProviderKind` | `未显式标注` | [L9](../../../../../jiuwenswarm/common/reasoning_config.py#L9) |
| `ReasoningLevel` | `未显式标注` | [L10](../../../../../jiuwenswarm/common/reasoning_config.py#L10) |
| `ReasoningEffort` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/reasoning_config.py#L11) |
| `OPENAI_SDK_REASONING_PROVIDERS` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/reasoning_config.py#L13) |
| `SUPPORTED_DEEPSEEK_V4_MODELS` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/reasoning_config.py#L19) |
| `LEVEL_MAPPING` | `dict[ReasoningLevel, ReasoningEffort]` | [L24](../../../../../jiuwenswarm/common/reasoning_config.py#L24) |
| `__all__` | `未显式标注` | [L99](../../../../../jiuwenswarm/common/reasoning_config.py#L99) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_provider(provider: Any) -> str` | 源码未提供函数级文档字符串。 | [L32](../../../../../jiuwenswarm/common/reasoning_config.py#L32) |
| `def _parse_api_base(api_base: str \| None)` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/common/reasoning_config.py#L38) |
| `def resolve_reasoning_provider_kind(api_base: str \| None) -> ReasoningProviderKind \| None` | 源码未提供函数级文档字符串。 | [L45](../../../../../jiuwenswarm/common/reasoning_config.py#L45) |
| `def normalize_reasoning_level(raw: Any) -> ReasoningLevel \| None` | 源码未提供函数级文档字符串。 | [L61](../../../../../jiuwenswarm/common/reasoning_config.py#L61) |
| `def resolve_reasoning_target(*, client_provider: Any, api_base: str \| None, model_name: str \| None) -> tuple[ReasoningProviderKind, str] \| None` | 源码未提供函数级文档字符串。 | [L79](../../../../../jiuwenswarm/common/reasoning_config.py#L79) |

## `jiuwenswarm/common/reasoning_injector.py`

[打开源码](../../../../../jiuwenswarm/common/reasoning_injector.py#L1)

**模块职责：** 定义 _model_config_to_dict、_resolve_model_name、_copy_extra_body、_runtime_config_copy、inject_deepseek_official_payload、inject_dashscope_bailian_payload 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L145](../../../../../jiuwenswarm/common/reasoning_injector.py#L145) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _model_config_to_dict(model_config_obj: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L15](../../../../../jiuwenswarm/common/reasoning_injector.py#L15) |
| `def _resolve_model_name(model_name: str, model_config_obj: Any) -> str` | 源码未提供函数级文档字符串。 | [L27](../../../../../jiuwenswarm/common/reasoning_injector.py#L27) |
| `def _copy_extra_body(value: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L36](../../../../../jiuwenswarm/common/reasoning_injector.py#L36) |
| `def _runtime_config_copy(model_config_dict: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L42](../../../../../jiuwenswarm/common/reasoning_injector.py#L42) |
| `def inject_deepseek_official_payload(model_config_obj: dict[str, Any], mapped_level: ReasoningEffort) -> None` | 源码未提供函数级文档字符串。 | [L49](../../../../../jiuwenswarm/common/reasoning_injector.py#L49) |
| `def inject_dashscope_bailian_payload(model_config_obj: dict[str, Any], mapped_level: ReasoningEffort) -> None` | 源码未提供函数级文档字符串。 | [L65](../../../../../jiuwenswarm/common/reasoning_injector.py#L65) |
| `def inject_reasoning_params(*, model_client_config: dict[str, Any], model_config_obj: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L79](../../../../../jiuwenswarm/common/reasoning_injector.py#L79) |
| `def _build_model_request_kwargs(*, model_name: str, model_config_obj: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L112](../../../../../jiuwenswarm/common/reasoning_injector.py#L112) |
| `def build_reasoning_model_request_kwargs(*, model_client_config: dict[str, Any], model_config_obj: Any, model_name: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L125](../../../../../jiuwenswarm/common/reasoning_injector.py#L125) |

## `jiuwenswarm/common/request_ext.py`

[打开源码](../../../../../jiuwenswarm/common/request_ext.py#L1)

**模块职责：** 请求级扩展字段透传（Web 握手 query / header → Message.metadata['ext']）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `ENV_FORWARD_HEADERS` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/request_ext.py#L19) |
| `ENV_FORWARD_HEADERS_LEGACY` | `未显式标注` | [L20](../../../../../jiuwenswarm/common/request_ext.py#L20) |
| `METADATA_KEY` | `未显式标注` | [L21](../../../../../jiuwenswarm/common/request_ext.py#L21) |
| `_request_ext` | `ContextVar['dict[str, Any] \| None']` | [L23](../../../../../jiuwenswarm/common/request_ext.py#L23) |
| `_runtime_override` | `'list[str] \| None'` | [L28](../../../../../jiuwenswarm/common/request_ext.py#L28) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def set_forward_headers(headers: 'list[str] \| None') -> None` | 整体覆盖 forward_headers（管控面 / DB 监听器使用）。 | [L31](../../../../../jiuwenswarm/common/request_ext.py#L31) |
| `def register_forward_header(name: str) -> None` | 加法注册单个字段名（扩展加载时使用）。 | [L43](../../../../../jiuwenswarm/common/request_ext.py#L43) |
| `def register_forward_headers(names: 'list[str]') -> None` | 加法注册多个字段名。 | [L55](../../../../../jiuwenswarm/common/request_ext.py#L55) |
| `def _env_forward_headers() -> 'list[str]'` | 源码未提供函数级文档字符串。 | [L61](../../../../../jiuwenswarm/common/request_ext.py#L61) |
| `def _read_forward_headers() -> 'list[str]'` | 源码未提供函数级文档字符串。 | [L68](../../../../../jiuwenswarm/common/request_ext.py#L68) |
| `def build_ext_from_source(source: 'Mapping[str, Any] \| None') -> 'dict[str, Any] \| None'` | 从入站 mapping（HTTP header 或 WS query 字典）按配置抽取扩展字段。 | [L74](../../../../../jiuwenswarm/common/request_ext.py#L74) |
| `def set_current(ext: 'dict[str, Any] \| None') -> 'Token'` | Channel 入口使用：将 ext 写入 ContextVar，返回还原 token。 | [L98](../../../../../jiuwenswarm/common/request_ext.py#L98) |
| `def attach_to_metadata(metadata: 'dict[str, Any] \| None', ext: 'dict[str, Any] \| None' = None) -> 'dict[str, Any] \| None'` | 构造 Message 时使用：将 ext 写入 metadata。 | [L103](../../../../../jiuwenswarm/common/request_ext.py#L103) |
| `def lift_from_metadata(metadata: 'Mapping[str, Any] \| None') -> 'Token \| None'` | AgentServer 入口使用：从 request.metadata 抬升 ext 到 ContextVar。 | [L120](../../../../../jiuwenswarm/common/request_ext.py#L120) |
| `def reset_ext(token: 'Token \| None') -> None` | 与 :func:`set_current` / :func:`lift_from_metadata` 配对，还原 ContextVar。 | [L130](../../../../../jiuwenswarm/common/request_ext.py#L130) |
| `def get_ext() -> 'dict[str, Any]'` | Agent rail 使用：读取当前请求的扩展字段。无字段时返回空 dict。 | [L136](../../../../../jiuwenswarm/common/request_ext.py#L136) |

## `jiuwenswarm/common/request_identity.py`

[打开源码](../../../../../jiuwenswarm/common/request_identity.py#L1)

**模块职责：** Transport-level Web request identity helpers.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `ROUTING_METADATA_KEY` | `未显式标注` | [L43](../../../../../jiuwenswarm/common/request_identity.py#L43) |
| `USER_ID_FIELD` | `未显式标注` | [L44](../../../../../jiuwenswarm/common/request_identity.py#L44) |
| `WEB_ROUTING_ID_FIELDS` | `未显式标注` | [L46](../../../../../jiuwenswarm/common/request_identity.py#L46) |
| `WEB_IDENTITY_FIELDS` | `未显式标注` | [L47](../../../../../jiuwenswarm/common/request_identity.py#L47) |
| `__all__` | `未显式标注` | [L152](../../../../../jiuwenswarm/common/request_identity.py#L152) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _identity_value(value: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L50](../../../../../jiuwenswarm/common/request_identity.py#L50) |
| `def _pick_field(mapping: Mapping[str, Any], field: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L59](../../../../../jiuwenswarm/common/request_identity.py#L59) |
| `def normalize_routing_identity(*sources: Mapping[str, Any] \| None) -> dict[str, str]` | 从若干 mapping 归一化完整身份（含 ``user_id``）；同一字段以先出现的非空值为准。 | [L63](../../../../../jiuwenswarm/common/request_identity.py#L63) |
| `def apply_routing_metadata(metadata: Mapping[str, Any] \| None, routing: Mapping[str, str] \| None) -> dict[str, Any]` | 写入身份：``user_id`` → 顶层；``group_id``/``bot_id``/``gateway_id`` → ``routing``。 | [L84](../../../../../jiuwenswarm/common/request_identity.py#L84) |
| `def web_routing_identity(metadata: Mapping[str, Any] \| None) -> dict[str, str]` | 读完整身份：顶层 ``user_id`` + ``metadata.routing`` 三字段（不读 ``routing.user_id``）。 | [L117](../../../../../jiuwenswarm/common/request_identity.py#L117) |
| `def merge_routing_into_params(params: Mapping[str, Any] \| None, metadata: Mapping[str, Any] \| None, *, override: bool = True) -> dict[str, Any]` | 仅用于 Gateway **本地** handler（如 cron.*）：handler 签名只有 params。 | [L134](../../../../../jiuwenswarm/common/request_identity.py#L134) |

## `jiuwenswarm/common/schema/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/schema/__init__.py#L1)

**模块职责：** 数据模型.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L8](../../../../../jiuwenswarm/common/schema/__init__.py#L8) |

## `jiuwenswarm/common/schema/agent.py`

[打开源码](../../../../../jiuwenswarm/common/schema/agent.py#L1)

**模块职责：** Agent 请求与响应模型.

### [`class PermissionContext`](../../../../../jiuwenswarm/common/schema/agent.py#L14)

权限上下文 - 统一承载权限判定所需的身份与场景信息.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `principal_user_id` | `str` | `''` | [L25](../../../../../jiuwenswarm/common/schema/agent.py#L25) |
| `triggering_user_id` | `str` | `''` | [L26](../../../../../jiuwenswarm/common/schema/agent.py#L26) |
| `channel_id` | `str` | `''` | [L27](../../../../../jiuwenswarm/common/schema/agent.py#L27) |
| `group_digital_avatar` | `bool` | `False` | [L28](../../../../../jiuwenswarm/common/schema/agent.py#L28) |
| `web_user_id` | `str` | `''` | [L29](../../../../../jiuwenswarm/common/schema/agent.py#L29) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def scene(self) -> str` | 从 channel_id + group_digital_avatar 派生，不要求外部显式赋值. | [L32](../../../../../jiuwenswarm/common/schema/agent.py#L32) |
| `@property def owner_scope_key(self) -> tuple[str, str]` | 用于 owner_scopes 配置查找的 key: (channel_id, principal_user_id). | [L41](../../../../../jiuwenswarm/common/schema/agent.py#L41) |
| `def to_dict(self) -> dict[str, Any]` | 序列化为 dict（供 Gateway→AgentServer WebSocket 传输）. | [L45](../../../../../jiuwenswarm/common/schema/agent.py#L45) |
| `@classmethod def from_dict(cls, data: dict[str, Any]) -> PermissionContext` | 从 dict 反序列化. | [L56](../../../../../jiuwenswarm/common/schema/agent.py#L56) |

### [`class AgentRequest`](../../../../../jiuwenswarm/common/schema/agent.py#L68)

Agent 请求（Gateway → AgentServer）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L71](../../../../../jiuwenswarm/common/schema/agent.py#L71) |
| `channel_id` | `str` | `''` | [L72](../../../../../jiuwenswarm/common/schema/agent.py#L72) |
| `session_id` | `str \| None` | `None` | [L73](../../../../../jiuwenswarm/common/schema/agent.py#L73) |
| `chat_id` | `str \| None` | `None` | [L74](../../../../../jiuwenswarm/common/schema/agent.py#L74) |
| `service_id` | `str \| None` | `None` | [L76](../../../../../jiuwenswarm/common/schema/agent.py#L76) |
| `agent_id` | `str \| None` | `None` | [L77](../../../../../jiuwenswarm/common/schema/agent.py#L77) |
| `workspace_key` | `str \| None` | `None` | [L79](../../../../../jiuwenswarm/common/schema/agent.py#L79) |
| `req_method` | `ReqMethod \| None` | `None` | [L80](../../../../../jiuwenswarm/common/schema/agent.py#L80) |
| `params` | `dict` | `field(default_factory=dict)` | [L81](../../../../../jiuwenswarm/common/schema/agent.py#L81) |
| `is_stream` | `bool` | `False` | [L82](../../../../../jiuwenswarm/common/schema/agent.py#L82) |
| `timestamp` | `float` | `0.0` | [L83](../../../../../jiuwenswarm/common/schema/agent.py#L83) |
| `metadata` | `dict[str, Any] \| None` | `None` | [L84](../../../../../jiuwenswarm/common/schema/agent.py#L84) |
| `enable_memory` | `bool \| None` | `None` | [L85](../../../../../jiuwenswarm/common/schema/agent.py#L85) |
| `permission_context` | `PermissionContext \| None` | `None` | [L86](../../../../../jiuwenswarm/common/schema/agent.py#L86) |
| `agent_ref` | `Any` | `None` | [L88](../../../../../jiuwenswarm/common/schema/agent.py#L88) |

### [`class AgentResponse`](../../../../../jiuwenswarm/common/schema/agent.py#L92)

Agent 响应（AgentServer → Gateway，非流式完整响应）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L95](../../../../../jiuwenswarm/common/schema/agent.py#L95) |
| `channel_id` | `str` | `—` | [L96](../../../../../jiuwenswarm/common/schema/agent.py#L96) |
| `ok` | `bool` | `True` | [L97](../../../../../jiuwenswarm/common/schema/agent.py#L97) |
| `payload` | `dict \| None` | `None` | [L98](../../../../../jiuwenswarm/common/schema/agent.py#L98) |
| `metadata` | `dict[str, Any] \| None` | `None` | [L99](../../../../../jiuwenswarm/common/schema/agent.py#L99) |
| `agent_ref` | `Any` | `None` | [L101](../../../../../jiuwenswarm/common/schema/agent.py#L101) |

### [`class AgentResponseChunk`](../../../../../jiuwenswarm/common/schema/agent.py#L105)

Agent 响应片段（AgentServer → Gateway，流式）.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L108](../../../../../jiuwenswarm/common/schema/agent.py#L108) |
| `channel_id` | `str` | `—` | [L109](../../../../../jiuwenswarm/common/schema/agent.py#L109) |
| `payload` | `dict \| None` | `None` | [L110](../../../../../jiuwenswarm/common/schema/agent.py#L110) |
| `is_complete` | `bool` | `False` | [L111](../../../../../jiuwenswarm/common/schema/agent.py#L111) |
| `agent_ref` | `Any` | `None` | [L112](../../../../../jiuwenswarm/common/schema/agent.py#L112) |
| `metadata` | `dict` | `field(default_factory=dict)` | [L113](../../../../../jiuwenswarm/common/schema/agent.py#L113) |

## `jiuwenswarm/common/schema/ask_user.py`

[打开源码](../../../../../jiuwenswarm/common/schema/ask_user.py#L1)

**模块职责：** Canonical AskUser response contract shared by adapters, rails, and tools.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_OTHER_OPTION_LABELS` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/schema/ask_user.py#L13) |

### [`class AskUserResponseError(ValueError)`](../../../../../jiuwenswarm/common/schema/ask_user.py#L16)

Raised when an AskUser response violates the canonical contract.

### [`class AskUserAnswer`](../../../../../jiuwenswarm/common/schema/ask_user.py#L21)

One normalized answer item.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `question` | `str` | `—` | [L24](../../../../../jiuwenswarm/common/schema/ask_user.py#L24) |
| `selected_options` | `tuple[str, ...]` | `—` | [L25](../../../../../jiuwenswarm/common/schema/ask_user.py#L25) |
| `custom_input` | `str \| None` | `—` | [L26](../../../../../jiuwenswarm/common/schema/ask_user.py#L26) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L28](../../../../../jiuwenswarm/common/schema/ask_user.py#L28) |
| `def readable_value(self) -> str` | 源码未提供方法级文档字符串。 | [L35](../../../../../jiuwenswarm/common/schema/ask_user.py#L35) |

### [`class AskUserResponse`](../../../../../jiuwenswarm/common/schema/ask_user.py#L43)

The only internal representation of an AskUser response.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `status` | `Literal['answered', 'skipped']` | `—` | [L46](../../../../../jiuwenswarm/common/schema/ask_user.py#L46) |
| `answers` | `tuple[AskUserAnswer, ...]` | `—` | [L47](../../../../../jiuwenswarm/common/schema/ask_user.py#L47) |
| `original_request` | `str \| None` | `None` | [L48](../../../../../jiuwenswarm/common/schema/ask_user.py#L48) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self, *, include_original_request: bool = True) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L50](../../../../../jiuwenswarm/common/schema/ask_user.py#L50) |
| `def to_readable_text(self) -> str` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/common/schema/ask_user.py#L59) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def ask_user_response_schema() -> dict[str, Any]` | Return the public JSON schema for the canonical resume response. | [L70](../../../../../jiuwenswarm/common/schema/ask_user.py#L70) |
| `def _normalize_answer(item: Any, index: int) -> AskUserAnswer \| None` | 源码未提供函数级文档字符串。 | [L108](../../../../../jiuwenswarm/common/schema/ask_user.py#L108) |
| `def normalize_ask_user_response(*, status: Any, answers: Any, original_request: Any = None) -> AskUserResponse` | Normalize the current array protocol into one semantic response. | [L155](../../../../../jiuwenswarm/common/schema/ask_user.py#L155) |
| `def parse_ask_user_response(value: Any) -> AskUserResponse` | Parse the canonical internal mapping without legacy fallbacks. | [L197](../../../../../jiuwenswarm/common/schema/ask_user.py#L197) |
| `def decode_user_input(value: Any) -> Any` | Decode user_input into a canonical form before strict parsing. | [L212](../../../../../jiuwenswarm/common/schema/ask_user.py#L212) |

## `jiuwenswarm/common/schema/chat_send.py`

[打开源码](../../../../../jiuwenswarm/common/schema/chat_send.py#L1)

**模块职责：** chat.send 参数契约。

### [`class ChatSendParams(TypedDict, total=False)`](../../../../../jiuwenswarm/common/schema/chat_send.py#L11)

chat.send 参数契约（TypedDict，供类型标注与文档）。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `content` | `str` | `—` | [L24](../../../../../jiuwenswarm/common/schema/chat_send.py#L24) |
| `query` | `str` | `—` | [L27](../../../../../jiuwenswarm/common/schema/chat_send.py#L27) |
| `skills` | `NotRequired[list[str]]` | `—` | [L33](../../../../../jiuwenswarm/common/schema/chat_send.py#L33) |
| `mode` | `NotRequired[str]` | `—` | [L42](../../../../../jiuwenswarm/common/schema/chat_send.py#L42) |
| `attachments` | `NotRequired[list[dict]]` | `—` | [L45](../../../../../jiuwenswarm/common/schema/chat_send.py#L45) |
| `files` | `NotRequired[dict]` | `—` | [L48](../../../../../jiuwenswarm/common/schema/chat_send.py#L48) |
| `trusted_dirs` | `NotRequired[list[str]]` | `—` | [L51](../../../../../jiuwenswarm/common/schema/chat_send.py#L51) |
| `project_dir` | `NotRequired[str]` | `—` | [L54](../../../../../jiuwenswarm/common/schema/chat_send.py#L54) |
| `cwd` | `NotRequired[str]` | `—` | [L57](../../../../../jiuwenswarm/common/schema/chat_send.py#L57) |
| `workspace_dir` | `NotRequired[str]` | `—` | [L60](../../../../../jiuwenswarm/common/schema/chat_send.py#L60) |
| `supports_user_interaction` | `NotRequired[bool]` | `—` | [L63](../../../../../jiuwenswarm/common/schema/chat_send.py#L63) |
| `plan_entry_source` | `NotRequired[str]` | `—` | [L66](../../../../../jiuwenswarm/common/schema/chat_send.py#L66) |
| `answers` | `NotRequired[list]` | `—` | [L69](../../../../../jiuwenswarm/common/schema/chat_send.py#L69) |
| `original_request` | `NotRequired[str]` | `—` | [L72](../../../../../jiuwenswarm/common/schema/chat_send.py#L72) |
| `session_id` | `NotRequired[str]` | `—` | [L75](../../../../../jiuwenswarm/common/schema/chat_send.py#L75) |
| `model_name` | `NotRequired[str]` | `—` | [L78](../../../../../jiuwenswarm/common/schema/chat_send.py#L78) |
| `request_id` | `NotRequired[str]` | `—` | [L81](../../../../../jiuwenswarm/common/schema/chat_send.py#L81) |
| `source` | `NotRequired[str]` | `—` | [L84](../../../../../jiuwenswarm/common/schema/chat_send.py#L84) |
| `is_supplement` | `NotRequired[bool]` | `—` | [L87](../../../../../jiuwenswarm/common/schema/chat_send.py#L87) |
| `supplement_input` | `NotRequired[str]` | `—` | [L90](../../../../../jiuwenswarm/common/schema/chat_send.py#L90) |
| `plan_approval_kind` | `NotRequired[str]` | `—` | [L93](../../../../../jiuwenswarm/common/schema/chat_send.py#L93) |
| `plan_content` | `NotRequired[str]` | `—` | [L96](../../../../../jiuwenswarm/common/schema/chat_send.py#L96) |
| `plan_language` | `NotRequired[str]` | `—` | [L99](../../../../../jiuwenswarm/common/schema/chat_send.py#L99) |
| `approval_schema` | `NotRequired[str]` | `—` | [L102](../../../../../jiuwenswarm/common/schema/chat_send.py#L102) |
| `evolution_meta` | `NotRequired[dict]` | `—` | [L105](../../../../../jiuwenswarm/common/schema/chat_send.py#L105) |
| `activate_response` | `NotRequired[dict]` | `—` | [L108](../../../../../jiuwenswarm/common/schema/chat_send.py#L108) |
| `team` | `NotRequired[bool]` | `—` | [L111](../../../../../jiuwenswarm/common/schema/chat_send.py#L111) |
| `run` | `NotRequired[dict]` | `—` | [L114](../../../../../jiuwenswarm/common/schema/chat_send.py#L114) |
| `cron` | `NotRequired[dict]` | `—` | [L117](../../../../../jiuwenswarm/common/schema/chat_send.py#L117) |

## `jiuwenswarm/common/schema/event_base.py`

[打开源码](../../../../../jiuwenswarm/common/schema/event_base.py#L1)

**模块职责：** 与 openjiuwen 0.1.9+ ``openjiuwen.core.runner.callback.events`` 中 EventBase 对齐的最小 HookEventBase。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_SCOPE` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/schema/event_base.py#L12) |

### [`class HookEventBase`](../../../../../jiuwenswarm/common/schema/event_base.py#L26)

带 scope 的钩子事件名基类（与 openjiuwen 0.1.9 EventBase 行为一致）。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `scope` | `str` | `DEFAULT_SCOPE` | [L29](../../../../../jiuwenswarm/common/schema/event_base.py#L29) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init_subclass__(cls, **kwargs)` | 源码未提供方法级文档字符串。 | [L31](../../../../../jiuwenswarm/common/schema/event_base.py#L31) |
| `@classmethod def get_event(cls, event_name: str) -> str` | 源码未提供方法级文档字符串。 | [L40](../../../../../jiuwenswarm/common/schema/event_base.py#L40) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def build_event_name(scope: str, event_name: str) -> str` | 源码未提供函数级文档字符串。 | [L15](../../../../../jiuwenswarm/common/schema/event_base.py#L15) |
| `def parse_event_name(scoped_event: str) -> tuple[str, str]` | 源码未提供函数级文档字符串。 | [L19](../../../../../jiuwenswarm/common/schema/event_base.py#L19) |

## `jiuwenswarm/common/schema/message.py`

[打开源码](../../../../../jiuwenswarm/common/schema/message.py#L1)

**模块职责：** 统一消息模型.

### [`class ReqMethod(Enum)`](../../../../../jiuwenswarm/common/schema/message.py#L10)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `INITIALIZE` | `未显式标注` | `'initialize'` | [L11](../../../../../jiuwenswarm/common/schema/message.py#L11) |
| `ACP_TOOL_RESPONSE` | `未显式标注` | `'acp.tool_response'` | [L12](../../../../../jiuwenswarm/common/schema/message.py#L12) |
| `CHAT_SEND` | `未显式标注` | `'chat.send'` | [L14](../../../../../jiuwenswarm/common/schema/message.py#L14) |
| `CHAT_RESUME` | `未显式标注` | `'chat.resume'` | [L15](../../../../../jiuwenswarm/common/schema/message.py#L15) |
| `CHAT_CANCEL` | `未显式标注` | `'chat.interrupt'` | [L16](../../../../../jiuwenswarm/common/schema/message.py#L16) |
| `CHAT_ANSWER` | `未显式标注` | `'chat.user_answer'` | [L17](../../../../../jiuwenswarm/common/schema/message.py#L17) |
| `CHAT_SWARMFLOW_REPLY` | `未显式标注` | `'chat.swarmflow_reply'` | [L18](../../../../../jiuwenswarm/common/schema/message.py#L18) |
| `SSH_RELAY` | `未显式标注` | `'ssh.relay'` | [L19](../../../../../jiuwenswarm/common/schema/message.py#L19) |
| `HISTORY_GET` | `未显式标注` | `'history.get'` | [L20](../../../../../jiuwenswarm/common/schema/message.py#L20) |
| `COMMAND_BTW` | `未显式标注` | `'command.btw'` | [L21](../../../../../jiuwenswarm/common/schema/message.py#L21) |
| `COMMAND_ADD_DIR` | `未显式标注` | `'command.add_dir'` | [L22](../../../../../jiuwenswarm/common/schema/message.py#L22) |
| `COMMAND_CHROME` | `未显式标注` | `'command.chrome'` | [L23](../../../../../jiuwenswarm/common/schema/message.py#L23) |
| `COMMAND_COMPACT` | `未显式标注` | `'command.compact'` | [L24](../../../../../jiuwenswarm/common/schema/message.py#L24) |
| `COMMAND_COMPACT_PARTIAL` | `未显式标注` | `'command.compact_partial'` | [L25](../../../../../jiuwenswarm/common/schema/message.py#L25) |
| `COMMAND_CONTEXT` | `未显式标注` | `'command.context'` | [L26](../../../../../jiuwenswarm/common/schema/message.py#L26) |
| `COMMAND_RECAP` | `未显式标注` | `'command.recap'` | [L27](../../../../../jiuwenswarm/common/schema/message.py#L27) |
| `COMMAND_DIFF` | `未显式标注` | `'command.diff'` | [L28](../../../../../jiuwenswarm/common/schema/message.py#L28) |
| `COMMAND_SIMPLIFY` | `未显式标注` | `'command.simplify'` | [L29](../../../../../jiuwenswarm/common/schema/message.py#L29) |
| `COMMAND_MCP` | `未显式标注` | `'command.mcp'` | [L30](../../../../../jiuwenswarm/common/schema/message.py#L30) |
| `COMMAND_MODEL` | `未显式标注` | `'command.model'` | [L31](../../../../../jiuwenswarm/common/schema/message.py#L31) |
| `COMMAND_RESUME` | `未显式标注` | `'command.resume'` | [L32](../../../../../jiuwenswarm/common/schema/message.py#L32) |
| `COMMAND_SANDBOX` | `未显式标注` | `'command.sandbox'` | [L33](../../../../../jiuwenswarm/common/schema/message.py#L33) |
| `COMMAND_SESSION` | `未显式标注` | `'command.session'` | [L34](../../../../../jiuwenswarm/common/schema/message.py#L34) |
| `COMMAND_WORKFLOWS` | `未显式标注` | `'command.workflows'` | [L35](../../../../../jiuwenswarm/common/schema/message.py#L35) |
| `COMMAND_STATUS` | `未显式标注` | `'command.status'` | [L36](../../../../../jiuwenswarm/common/schema/message.py#L36) |
| `CONFIG_GET` | `未显式标注` | `'config.get'` | [L38](../../../../../jiuwenswarm/common/schema/message.py#L38) |
| `CONFIG_SET` | `未显式标注` | `'config.set'` | [L39](../../../../../jiuwenswarm/common/schema/message.py#L39) |
| `CHANNEL_GET` | `未显式标注` | `'channel.get'` | [L40](../../../../../jiuwenswarm/common/schema/message.py#L40) |
| `SESSION_LIST` | `未显式标注` | `'session.list'` | [L42](../../../../../jiuwenswarm/common/schema/message.py#L42) |
| `SESSION_CREATE` | `未显式标注` | `'session.create'` | [L43](../../../../../jiuwenswarm/common/schema/message.py#L43) |
| `SESSION_SWITCH` | `未显式标注` | `'session.switch'` | [L44](../../../../../jiuwenswarm/common/schema/message.py#L44) |
| `SESSION_DELETE` | `未显式标注` | `'session.delete'` | [L45](../../../../../jiuwenswarm/common/schema/message.py#L45) |
| `SESSION_RENAME` | `未显式标注` | `'session.rename'` | [L46](../../../../../jiuwenswarm/common/schema/message.py#L46) |
| `SESSION_FORK` | `未显式标注` | `'session.fork'` | [L47](../../../../../jiuwenswarm/common/schema/message.py#L47) |
| `SESSION_REWIND` | `未显式标注` | `'session.rewind'` | [L48](../../../../../jiuwenswarm/common/schema/message.py#L48) |
| `SESSION_REWIND_AND_RESTORE` | `未显式标注` | `'session.rewind_and_restore'` | [L49](../../../../../jiuwenswarm/common/schema/message.py#L49) |
| `SESSION_REWIND_CONTEXT` | `未显式标注` | `'session.rewind_context'` | [L50](../../../../../jiuwenswarm/common/schema/message.py#L50) |
| `SESSION_REWIND_COMPACT` | `未显式标注` | `'session.rewind_compact'` | [L51](../../../../../jiuwenswarm/common/schema/message.py#L51) |
| `SESSION_RESTORE_FILES` | `未显式标注` | `'session.restore_files'` | [L52](../../../../../jiuwenswarm/common/schema/message.py#L52) |
| `HISTORY_LIST_TURNS` | `未显式标注` | `'history.list_turns'` | [L53](../../../../../jiuwenswarm/common/schema/message.py#L53) |
| `TEAM_TEMPLATES_LIST` | `未显式标注` | `'team.templates.list'` | [L54](../../../../../jiuwenswarm/common/schema/message.py#L54) |
| `TEAM_BINDINGS_LIST` | `未显式标注` | `'team.bindings.list'` | [L55](../../../../../jiuwenswarm/common/schema/message.py#L55) |
| `TEAM_BINDING_CREATE` | `未显式标注` | `'team.binding.create'` | [L56](../../../../../jiuwenswarm/common/schema/message.py#L56) |
| `TEAM_BINDING_GENERATE` | `未显式标注` | `'team.binding.generate'` | [L57](../../../../../jiuwenswarm/common/schema/message.py#L57) |
| `TEAM_SESSION_BIND` | `未显式标注` | `'team.session.bind'` | [L58](../../../../../jiuwenswarm/common/schema/message.py#L58) |
| `TEAM_DELETE` | `未显式标注` | `'team.delete'` | [L59](../../../../../jiuwenswarm/common/schema/message.py#L59) |
| `TEAM_SESSION_RESET` | `未显式标注` | `'team.session.reset'` | [L60](../../../../../jiuwenswarm/common/schema/message.py#L60) |
| `TEAM_RUNTIME_DISSOLVE` | `未显式标注` | `'team.runtime.dissolve'` | [L61](../../../../../jiuwenswarm/common/schema/message.py#L61) |
| `PATH_GET` | `未显式标注` | `'path.get'` | [L63](../../../../../jiuwenswarm/common/schema/message.py#L63) |
| `PATH_SET` | `未显式标注` | `'path.set'` | [L64](../../../../../jiuwenswarm/common/schema/message.py#L64) |
| `BROWSER_RUNTIME_RESTART` | `未显式标注` | `'browser.runtime_restart'` | [L66](../../../../../jiuwenswarm/common/schema/message.py#L66) |
| `CONFIG_CACHE_CLEAR` | `未显式标注` | `'config.cache_clear'` | [L68](../../../../../jiuwenswarm/common/schema/message.py#L68) |
| `AGENT_RELOAD_CONFIG` | `未显式标注` | `'agent.reload_config'` | [L69](../../../../../jiuwenswarm/common/schema/message.py#L69) |
| `SYNC_AGENTS_CONFIGS` | `未显式标注` | `'sync_agents_configs'` | [L70](../../../../../jiuwenswarm/common/schema/message.py#L70) |
| `AGENT_PREWARM_SYNC` | `未显式标注` | `'agent.prewarm.sync'` | [L71](../../../../../jiuwenswarm/common/schema/message.py#L71) |
| `LOGGING_SET` | `未显式标注` | `'logging.set'` | [L72](../../../../../jiuwenswarm/common/schema/message.py#L72) |
| `MEMORY_COMPUTE` | `未显式标注` | `'memory.compute'` | [L74](../../../../../jiuwenswarm/common/schema/message.py#L74) |
| `PROACTIVE_TICK` | `未显式标注` | `'proactive.tick'` | [L76](../../../../../jiuwenswarm/common/schema/message.py#L76) |
| `COMMAND_GOAL` | `未显式标注` | `'command.goal'` | [L77](../../../../../jiuwenswarm/common/schema/message.py#L77) |
| `FILES_LIST` | `未显式标注` | `'files.list'` | [L79](../../../../../jiuwenswarm/common/schema/message.py#L79) |
| `FILES_GET` | `未显式标注` | `'files.get'` | [L80](../../../../../jiuwenswarm/common/schema/message.py#L80) |
| `TTS_SYNTHESIZE` | `未显式标注` | `'tts.synthesize'` | [L81](../../../../../jiuwenswarm/common/schema/message.py#L81) |
| `AGENTS_LIST` | `未显式标注` | `'agents.list'` | [L83](../../../../../jiuwenswarm/common/schema/message.py#L83) |
| `AGENTS_GET` | `未显式标注` | `'agents.get'` | [L84](../../../../../jiuwenswarm/common/schema/message.py#L84) |
| `AGENTS_CREATE` | `未显式标注` | `'agents.create'` | [L85](../../../../../jiuwenswarm/common/schema/message.py#L85) |
| `AGENTS_UPDATE` | `未显式标注` | `'agents.update'` | [L86](../../../../../jiuwenswarm/common/schema/message.py#L86) |
| `AGENTS_DELETE` | `未显式标注` | `'agents.delete'` | [L87](../../../../../jiuwenswarm/common/schema/message.py#L87) |
| `AGENTS_ENABLE` | `未显式标注` | `'agents.enable'` | [L88](../../../../../jiuwenswarm/common/schema/message.py#L88) |
| `AGENTS_DISABLE` | `未显式标注` | `'agents.disable'` | [L89](../../../../../jiuwenswarm/common/schema/message.py#L89) |
| `AGENTS_TOOLS_LIST` | `未显式标注` | `'agents.tools_list'` | [L90](../../../../../jiuwenswarm/common/schema/message.py#L90) |
| `AGENT_SWITCH` | `未显式标注` | `'3rdagent.switch'` | [L91](../../../../../jiuwenswarm/common/schema/message.py#L91) |
| `AGENT_LIST` | `未显式标注` | `'3rdagent.list'` | [L92](../../../../../jiuwenswarm/common/schema/message.py#L92) |
| `SKILLS_MARKETPLACE_LIST` | `未显式标注` | `'skills.marketplace.list'` | [L94](../../../../../jiuwenswarm/common/schema/message.py#L94) |
| `SKILLS_LIST` | `未显式标注` | `'skills.list'` | [L95](../../../../../jiuwenswarm/common/schema/message.py#L95) |
| `SKILLS_INSTALLED` | `未显式标注` | `'skills.installed'` | [L96](../../../../../jiuwenswarm/common/schema/message.py#L96) |
| `SKILLS_GET` | `未显式标注` | `'skills.get'` | [L97](../../../../../jiuwenswarm/common/schema/message.py#L97) |
| `SKILLS_TOGGLE` | `未显式标注` | `'skills.toggle'` | [L98](../../../../../jiuwenswarm/common/schema/message.py#L98) |
| `SKILLS_INSTALL` | `未显式标注` | `'skills.install'` | [L99](../../../../../jiuwenswarm/common/schema/message.py#L99) |
| `SKILLS_IMPORT_LOCAL` | `未显式标注` | `'skills.import_local'` | [L100](../../../../../jiuwenswarm/common/schema/message.py#L100) |
| `SKILLS_MARKETPLACE_ADD` | `未显式标注` | `'skills.marketplace.add'` | [L101](../../../../../jiuwenswarm/common/schema/message.py#L101) |
| `SKILLS_MARKETPLACE_REMOVE` | `未显式标注` | `'skills.marketplace.remove'` | [L102](../../../../../jiuwenswarm/common/schema/message.py#L102) |
| `SKILLS_MARKETPLACE_TOGGLE` | `未显式标注` | `'skills.marketplace.toggle'` | [L103](../../../../../jiuwenswarm/common/schema/message.py#L103) |
| `SKILLS_UNINSTALL` | `未显式标注` | `'skills.uninstall'` | [L104](../../../../../jiuwenswarm/common/schema/message.py#L104) |
| `SKILLS_ONLINE_SEARCH` | `未显式标注` | `'skills.online_search.search'` | [L105](../../../../../jiuwenswarm/common/schema/message.py#L105) |
| `SKILLS_SKILLNET_SEARCH` | `未显式标注` | `'skills.skillnet.search'` | [L106](../../../../../jiuwenswarm/common/schema/message.py#L106) |
| `SKILLS_SKILLNET_INSTALL` | `未显式标注` | `'skills.skillnet.install'` | [L107](../../../../../jiuwenswarm/common/schema/message.py#L107) |
| `SKILLS_SKILLNET_INSTALL_STATUS` | `未显式标注` | `'skills.skillnet.install_status'` | [L108](../../../../../jiuwenswarm/common/schema/message.py#L108) |
| `SKILLS_SKILLNET_EVALUATE` | `未显式标注` | `'skills.skillnet.evaluate'` | [L109](../../../../../jiuwenswarm/common/schema/message.py#L109) |
| `SKILLS_CLAWHUB_GET_TOKEN` | `未显式标注` | `'skills.clawhub.get_token'` | [L110](../../../../../jiuwenswarm/common/schema/message.py#L110) |
| `SKILLS_CLAWHUB_SET_TOKEN` | `未显式标注` | `'skills.clawhub.set_token'` | [L111](../../../../../jiuwenswarm/common/schema/message.py#L111) |
| `SKILLS_CLAWHUB_SEARCH` | `未显式标注` | `'skills.clawhub.search'` | [L112](../../../../../jiuwenswarm/common/schema/message.py#L112) |
| `SKILLS_CLAWHUB_DOWNLOAD` | `未显式标注` | `'skills.clawhub.download'` | [L113](../../../../../jiuwenswarm/common/schema/message.py#L113) |
| `SKILLS_TEAMSKILLS_HUB_INFO` | `未显式标注` | `'skills.teamskillshub.info'` | [L114](../../../../../jiuwenswarm/common/schema/message.py#L114) |
| `SKILLS_TEAMSKILLS_HUB_INIT` | `未显式标注` | `'skills.teamskillshub.init'` | [L115](../../../../../jiuwenswarm/common/schema/message.py#L115) |
| `SKILLS_TEAMSKILLS_HUB_VALIDATE` | `未显式标注` | `'skills.teamskillshub.validate'` | [L116](../../../../../jiuwenswarm/common/schema/message.py#L116) |
| `SKILLS_TEAMSKILLS_HUB_PACK` | `未显式标注` | `'skills.teamskillshub.pack'` | [L117](../../../../../jiuwenswarm/common/schema/message.py#L117) |
| `SKILLS_TEAMSKILLS_HUB_SEARCH` | `未显式标注` | `'skills.teamskillshub.search'` | [L118](../../../../../jiuwenswarm/common/schema/message.py#L118) |
| `SKILLS_TEAMSKILLS_HUB_INSTALL` | `未显式标注` | `'skills.teamskillshub.install'` | [L119](../../../../../jiuwenswarm/common/schema/message.py#L119) |
| `SKILLS_TEAMSKILLS_HUB_PUBLISH` | `未显式标注` | `'skills.teamskillshub.publish'` | [L120](../../../../../jiuwenswarm/common/schema/message.py#L120) |
| `SKILLS_TEAMSKILLS_HUB_DELETE` | `未显式标注` | `'skills.teamskillshub.delete'` | [L121](../../../../../jiuwenswarm/common/schema/message.py#L121) |
| `SKILLS_SOURCE_PROVIDERS` | `未显式标注` | `'skills.source.providers'` | [L122](../../../../../jiuwenswarm/common/schema/message.py#L122) |
| `SKILLS_SOURCE_SEARCH` | `未显式标注` | `'skills.source.search'` | [L123](../../../../../jiuwenswarm/common/schema/message.py#L123) |
| `SKILLS_SOURCE_INSTALL` | `未显式标注` | `'skills.source.install'` | [L124](../../../../../jiuwenswarm/common/schema/message.py#L124) |
| `SKILLS_UPDATES_CHECK` | `未显式标注` | `'skills.updates.check'` | [L125](../../../../../jiuwenswarm/common/schema/message.py#L125) |
| `SKILLS_UPDATE` | `未显式标注` | `'skills.update'` | [L126](../../../../../jiuwenswarm/common/schema/message.py#L126) |
| `SKILLS_RETRIEVAL_STATUS` | `未显式标注` | `'skills.retrieval.status'` | [L127](../../../../../jiuwenswarm/common/schema/message.py#L127) |
| `SKILLS_RETRIEVAL_INDEX_BUILD` | `未显式标注` | `'skills.retrieval.index_build'` | [L128](../../../../../jiuwenswarm/common/schema/message.py#L128) |
| `SKILLS_RETRIEVAL_INDEX_CANCEL` | `未显式标注` | `'skills.retrieval.index_cancel'` | [L129](../../../../../jiuwenswarm/common/schema/message.py#L129) |
| `SKILLS_RETRIEVAL_SEARCH` | `未显式标注` | `'skills.retrieval.search'` | [L130](../../../../../jiuwenswarm/common/schema/message.py#L130) |
| `SKILLS_RETRIEVAL_TREE` | `未显式标注` | `'skills.retrieval.tree'` | [L131](../../../../../jiuwenswarm/common/schema/message.py#L131) |
| `SKILLS_EVOLUTION_STATUS` | `未显式标注` | `'skills.evolution.status'` | [L132](../../../../../jiuwenswarm/common/schema/message.py#L132) |
| `SKILLS_EVOLUTION_GET` | `未显式标注` | `'skills.evolution.get'` | [L133](../../../../../jiuwenswarm/common/schema/message.py#L133) |
| `SKILLS_EVOLUTION_SAVE` | `未显式标注` | `'skills.evolution.save'` | [L134](../../../../../jiuwenswarm/common/schema/message.py#L134) |
| `SKILLS_EVOLUTION_ARCHIVES` | `未显式标注` | `'skills.evolution.archives'` | [L135](../../../../../jiuwenswarm/common/schema/message.py#L135) |
| `SKILLS_EVOLUTION_ROLLBACK` | `未显式标注` | `'skills.evolution.rollback'` | [L136](../../../../../jiuwenswarm/common/schema/message.py#L136) |
| `SKILLS_EVOLUTION_REBUILD` | `未显式标注` | `'skills.evolution.rebuild'` | [L137](../../../../../jiuwenswarm/common/schema/message.py#L137) |
| `SKILLS_ENTERPRISE_LIST` | `未显式标注` | `'skills.enterprise.list'` | [L138](../../../../../jiuwenswarm/common/schema/message.py#L138) |
| `SKILLS_ENTERPRISE_INSTALL` | `未显式标注` | `'skills.enterprise.install'` | [L139](../../../../../jiuwenswarm/common/schema/message.py#L139) |
| `SKILLS_ENTERPRISE_UNINSTALL` | `未显式标注` | `'skills.enterprise.uninstall'` | [L140](../../../../../jiuwenswarm/common/schema/message.py#L140) |
| `SKILLS_ENTERPRISE_SOURCE_PROVIDERS` | `未显式标注` | `'skills.enterprise.source.providers'` | [L141](../../../../../jiuwenswarm/common/schema/message.py#L141) |
| `SKILLS_ENTERPRISE_SOURCE_SEARCH` | `未显式标注` | `'skills.enterprise.source.search'` | [L142](../../../../../jiuwenswarm/common/schema/message.py#L142) |
| `SYMPHONY_BUILD_SCORE` | `未显式标注` | `'symphony.build_score'` | [L144](../../../../../jiuwenswarm/common/schema/message.py#L144) |
| `SYMPHONY_PAUSE_BUILD` | `未显式标注` | `'symphony.pause_build'` | [L145](../../../../../jiuwenswarm/common/schema/message.py#L145) |
| `SYMPHONY_SCORE_STATUS` | `未显式标注` | `'symphony.score_status'` | [L146](../../../../../jiuwenswarm/common/schema/message.py#L146) |
| `SYMPHONY_GRAPH` | `未显式标注` | `'symphony.graph'` | [L147](../../../../../jiuwenswarm/common/schema/message.py#L147) |
| `SYMPHONY_PLAN` | `未显式标注` | `'symphony.plan'` | [L148](../../../../../jiuwenswarm/common/schema/message.py#L148) |
| `PLUGINS_LIST` | `未显式标注` | `'plugins.list'` | [L151](../../../../../jiuwenswarm/common/schema/message.py#L151) |
| `PLUGINS_INSTALL` | `未显式标注` | `'plugins.install'` | [L152](../../../../../jiuwenswarm/common/schema/message.py#L152) |
| `PLUGINS_UNINSTALL` | `未显式标注` | `'plugins.uninstall'` | [L153](../../../../../jiuwenswarm/common/schema/message.py#L153) |
| `PLUGINS_ENABLE` | `未显式标注` | `'plugins.enable'` | [L154](../../../../../jiuwenswarm/common/schema/message.py#L154) |
| `PLUGINS_DISABLE` | `未显式标注` | `'plugins.disable'` | [L155](../../../../../jiuwenswarm/common/schema/message.py#L155) |
| `PLUGINS_RELOAD` | `未显式标注` | `'plugins.reload'` | [L156](../../../../../jiuwenswarm/common/schema/message.py#L156) |
| `EXTENSIONS_LIST` | `未显式标注` | `'extensions.list'` | [L158](../../../../../jiuwenswarm/common/schema/message.py#L158) |
| `EXTENSIONS_IMPORT` | `未显式标注` | `'extensions.import'` | [L159](../../../../../jiuwenswarm/common/schema/message.py#L159) |
| `EXTENSIONS_DELETE` | `未显式标注` | `'extensions.delete'` | [L160](../../../../../jiuwenswarm/common/schema/message.py#L160) |
| `EXTENSIONS_TOGGLE` | `未显式标注` | `'extensions.toggle'` | [L161](../../../../../jiuwenswarm/common/schema/message.py#L161) |
| `HOOKS_LIST` | `未显式标注` | `'hooks.list'` | [L163](../../../../../jiuwenswarm/common/schema/message.py#L163) |
| `HEARTBEAT_GET_CONF` | `未显式标注` | `'heartbeat.get_conf'` | [L165](../../../../../jiuwenswarm/common/schema/message.py#L165) |
| `HEARTBEAT_SET_CONF` | `未显式标注` | `'heartbeat.set_conf'` | [L166](../../../../../jiuwenswarm/common/schema/message.py#L166) |
| `PERMISSIONS_ENABLED_GET` | `未显式标注` | `'permissions.enabled.get'` | [L169](../../../../../jiuwenswarm/common/schema/message.py#L169) |
| `PERMISSIONS_ENABLED_SET` | `未显式标注` | `'permissions.enabled.set'` | [L170](../../../../../jiuwenswarm/common/schema/message.py#L170) |
| `PERMISSIONS_TOOLS_GET` | `未显式标注` | `'permissions.tools.get'` | [L171](../../../../../jiuwenswarm/common/schema/message.py#L171) |
| `PERMISSIONS_TOOLS_LIST` | `未显式标注` | `'permissions.tools.list'` | [L172](../../../../../jiuwenswarm/common/schema/message.py#L172) |
| `PERMISSIONS_TOOLS_SET` | `未显式标注` | `'permissions.tools.set'` | [L173](../../../../../jiuwenswarm/common/schema/message.py#L173) |
| `PERMISSIONS_TOOLS_UPDATE` | `未显式标注` | `'permissions.tools.update'` | [L174](../../../../../jiuwenswarm/common/schema/message.py#L174) |
| `PERMISSIONS_TOOLS_DELETE` | `未显式标注` | `'permissions.tools.delete'` | [L175](../../../../../jiuwenswarm/common/schema/message.py#L175) |
| `PERMISSIONS_RULES_GET` | `未显式标注` | `'permissions.rules.get'` | [L176](../../../../../jiuwenswarm/common/schema/message.py#L176) |
| `PERMISSIONS_RULES_CREATE` | `未显式标注` | `'permissions.rules.create'` | [L177](../../../../../jiuwenswarm/common/schema/message.py#L177) |
| `PERMISSIONS_RULES_UPDATE` | `未显式标注` | `'permissions.rules.update'` | [L178](../../../../../jiuwenswarm/common/schema/message.py#L178) |
| `PERMISSIONS_RULES_DELETE` | `未显式标注` | `'permissions.rules.delete'` | [L179](../../../../../jiuwenswarm/common/schema/message.py#L179) |
| `PERMISSIONS_APPROVAL_OVERRIDES_GET` | `未显式标注` | `'permissions.approval_overrides.get'` | [L180](../../../../../jiuwenswarm/common/schema/message.py#L180) |
| `PERMISSIONS_APPROVAL_OVERRIDES_DELETE` | `未显式标注` | `'permissions.approval_overrides.delete'` | [L181](../../../../../jiuwenswarm/common/schema/message.py#L181) |
| `PERMISSIONS_WORKSPACE_ENABLE_GET` | `未显式标注` | `'permissions.file_guard.workspace.rw_enabled.get'` | [L182](../../../../../jiuwenswarm/common/schema/message.py#L182) |
| `PERMISSIONS_WORKSPACE_ENABLE_SET` | `未显式标注` | `'permissions.file_guard.workspace.rw_enabled.set'` | [L183](../../../../../jiuwenswarm/common/schema/message.py#L183) |
| `PERMISSIONS_WORKSPACE_ACCESS_GET` | `未显式标注` | `'permissions.file_guard.workspace.access.get'` | [L184](../../../../../jiuwenswarm/common/schema/message.py#L184) |
| `PERMISSIONS_WORKSPACE_ACCESS_SET` | `未显式标注` | `'permissions.file_guard.workspace.access.set'` | [L185](../../../../../jiuwenswarm/common/schema/message.py#L185) |
| `CHANNEL_FEISHU_GET_CONF` | `未显式标注` | `'channel.feishu.get_conf'` | [L187](../../../../../jiuwenswarm/common/schema/message.py#L187) |
| `CHANNEL_FEISHU_SET_CONF` | `未显式标注` | `'channel.feishu.set_conf'` | [L188](../../../../../jiuwenswarm/common/schema/message.py#L188) |
| `CHANNEL_XIAOYI_GET_CONF` | `未显式标注` | `'channel.xiaoyi.get_conf'` | [L190](../../../../../jiuwenswarm/common/schema/message.py#L190) |
| `CHANNEL_XIAOYI_SET_CONF` | `未显式标注` | `'channel.xiaoyi.set_conf'` | [L191](../../../../../jiuwenswarm/common/schema/message.py#L191) |
| `CHANNEL_TELEGRAM_GET_CONF` | `未显式标注` | `'channel.telegram.get_conf'` | [L193](../../../../../jiuwenswarm/common/schema/message.py#L193) |
| `CHANNEL_TELEGRAM_SET_CONF` | `未显式标注` | `'channel.telegram.set_conf'` | [L194](../../../../../jiuwenswarm/common/schema/message.py#L194) |
| `CHANNEL_SLACK_GET_CONF` | `未显式标注` | `'channel.slack.get_conf'` | [L195](../../../../../jiuwenswarm/common/schema/message.py#L195) |
| `CHANNEL_SLACK_SET_CONF` | `未显式标注` | `'channel.slack.set_conf'` | [L196](../../../../../jiuwenswarm/common/schema/message.py#L196) |
| `CHANNEL_DINGTALK_GET_CONF` | `未显式标注` | `'channel.dingtalk.get_conf'` | [L197](../../../../../jiuwenswarm/common/schema/message.py#L197) |
| `CHANNEL_DINGTALK_SET_CONF` | `未显式标注` | `'channel.dingtalk.set_conf'` | [L198](../../../../../jiuwenswarm/common/schema/message.py#L198) |
| `CHANNEL_WHATSAPP_GET_CONF` | `未显式标注` | `'channel.whatsapp.get_conf'` | [L200](../../../../../jiuwenswarm/common/schema/message.py#L200) |
| `CHANNEL_WHATSAPP_SET_CONF` | `未显式标注` | `'channel.whatsapp.set_conf'` | [L201](../../../../../jiuwenswarm/common/schema/message.py#L201) |
| `CHANNEL_WECHAT_GET_CONF` | `未显式标注` | `'channel.wechat.get_conf'` | [L202](../../../../../jiuwenswarm/common/schema/message.py#L202) |
| `CHANNEL_WECHAT_SET_CONF` | `未显式标注` | `'channel.wechat.set_conf'` | [L203](../../../../../jiuwenswarm/common/schema/message.py#L203) |
| `CHANNEL_WECHAT_GET_LOGIN_UI` | `未显式标注` | `'channel.wechat.get_login_ui'` | [L204](../../../../../jiuwenswarm/common/schema/message.py#L204) |
| `CHANNEL_WECHAT_UNBIND` | `未显式标注` | `'channel.wechat.unbind'` | [L205](../../../../../jiuwenswarm/common/schema/message.py#L205) |
| `UPDATER_GET_STATUS` | `未显式标注` | `'updater.get_status'` | [L207](../../../../../jiuwenswarm/common/schema/message.py#L207) |
| `UPDATER_CHECK` | `未显式标注` | `'updater.check'` | [L208](../../../../../jiuwenswarm/common/schema/message.py#L208) |
| `UPDATER_DOWNLOAD` | `未显式标注` | `'updater.download'` | [L209](../../../../../jiuwenswarm/common/schema/message.py#L209) |
| `UPDATER_GET_CONF` | `未显式标注` | `'updater.get_conf'` | [L210](../../../../../jiuwenswarm/common/schema/message.py#L210) |
| `UPDATER_SET_CONF` | `未显式标注` | `'updater.set_conf'` | [L211](../../../../../jiuwenswarm/common/schema/message.py#L211) |
| `TEAM_SNAPSHOT` | `未显式标注` | `'team.snapshot'` | [L213](../../../../../jiuwenswarm/common/schema/message.py#L213) |
| `TEAM_HISTORY_GET` | `未显式标注` | `'team.history.get'` | [L214](../../../../../jiuwenswarm/common/schema/message.py#L214) |
| `TEAM_MEMBERS_GET` | `未显式标注` | `'team.members.get'` | [L215](../../../../../jiuwenswarm/common/schema/message.py#L215) |
| `TEAM_MQ_PUBLISH` | `未显式标注` | `'team.mq.publish'` | [L216](../../../../../jiuwenswarm/common/schema/message.py#L216) |
| `HARNESS_PACKAGES_GET` | `未显式标注` | `'harness.packages.get'` | [L219](../../../../../jiuwenswarm/common/schema/message.py#L219) |
| `HARNESS_PACKAGES_SCAN` | `未显式标注` | `'harness.packages.scan'` | [L220](../../../../../jiuwenswarm/common/schema/message.py#L220) |
| `HARNESS_PACKAGES_ACTIVATE` | `未显式标注` | `'harness.packages.activate'` | [L221](../../../../../jiuwenswarm/common/schema/message.py#L221) |
| `HARNESS_PACKAGES_DEACTIVATE` | `未显式标注` | `'harness.packages.deactivate'` | [L222](../../../../../jiuwenswarm/common/schema/message.py#L222) |
| `HARNESS_PACKAGES_DELETE` | `未显式标注` | `'harness.packages.delete'` | [L223](../../../../../jiuwenswarm/common/schema/message.py#L223) |
| `HARNESS_PACKAGES_IMPORT` | `未显式标注` | `'harness.packages.import'` | [L224](../../../../../jiuwenswarm/common/schema/message.py#L224) |
| `HARNESS_PACKAGES_EXPORT` | `未显式标注` | `'harness.packages.export'` | [L225](../../../../../jiuwenswarm/common/schema/message.py#L225) |
| `SCHEDULE_CHECK_CONFIG` | `未显式标注` | `'schedule.check_config'` | [L228](../../../../../jiuwenswarm/common/schema/message.py#L228) |
| `SCHEDULE_UPDATE_CONFIG` | `未显式标注` | `'schedule.update_config'` | [L229](../../../../../jiuwenswarm/common/schema/message.py#L229) |
| `SCHEDULE_CREATE` | `未显式标注` | `'schedule.create'` | [L230](../../../../../jiuwenswarm/common/schema/message.py#L230) |
| `SCHEDULE_RUN` | `未显式标注` | `'schedule.run'` | [L231](../../../../../jiuwenswarm/common/schema/message.py#L231) |
| `SCHEDULE_LIST` | `未显式标注` | `'schedule.list'` | [L232](../../../../../jiuwenswarm/common/schema/message.py#L232) |
| `SCHEDULE_STATUS` | `未显式标注` | `'schedule.status'` | [L233](../../../../../jiuwenswarm/common/schema/message.py#L233) |
| `SCHEDULE_LOGS` | `未显式标注` | `'schedule.logs'` | [L234](../../../../../jiuwenswarm/common/schema/message.py#L234) |
| `SCHEDULE_CANCEL` | `未显式标注` | `'schedule.cancel'` | [L235](../../../../../jiuwenswarm/common/schema/message.py#L235) |
| `SCHEDULE_DELETE` | `未显式标注` | `'schedule.delete'` | [L236](../../../../../jiuwenswarm/common/schema/message.py#L236) |
| `ISSUE_WATCH_ONCE` | `未显式标注` | `'issue.watch_once'` | [L237](../../../../../jiuwenswarm/common/schema/message.py#L237) |
| `ISSUE_STATE_LIST` | `未显式标注` | `'issue.state.list'` | [L238](../../../../../jiuwenswarm/common/schema/message.py#L238) |
| `ISSUE_DELETE` | `未显式标注` | `'issue.delete'` | [L239](../../../../../jiuwenswarm/common/schema/message.py#L239) |
| `ISSUE_MATRIX` | `未显式标注` | `'issue.matrix'` | [L240](../../../../../jiuwenswarm/common/schema/message.py#L240) |

### [`class EventType(Enum)`](../../../../../jiuwenswarm/common/schema/message.py#L243)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `CONNECTION_ACK` | `未显式标注` | `'connection.ack'` | [L244](../../../../../jiuwenswarm/common/schema/message.py#L244) |
| `HELLO` | `未显式标注` | `'hello'` | [L245](../../../../../jiuwenswarm/common/schema/message.py#L245) |
| `CHAT_DELTA` | `未显式标注` | `'chat.delta'` | [L246](../../../../../jiuwenswarm/common/schema/message.py#L246) |
| `CHAT_REASONING` | `未显式标注` | `'chat.reasoning'` | [L247](../../../../../jiuwenswarm/common/schema/message.py#L247) |
| `CHAT_USAGE_METADATA` | `未显式标注` | `'chat.usage_metadata'` | [L248](../../../../../jiuwenswarm/common/schema/message.py#L248) |
| `CHAT_USAGE_SUMMARY` | `未显式标注` | `'chat.usage_summary'` | [L249](../../../../../jiuwenswarm/common/schema/message.py#L249) |
| `CHAT_FINAL` | `未显式标注` | `'chat.final'` | [L250](../../../../../jiuwenswarm/common/schema/message.py#L250) |
| `CHAT_RETRACT` | `未显式标注` | `'chat.retract'` | [L251](../../../../../jiuwenswarm/common/schema/message.py#L251) |
| `CHAT_MEDIA` | `未显式标注` | `'chat.media'` | [L252](../../../../../jiuwenswarm/common/schema/message.py#L252) |
| `CHAT_FILE` | `未显式标注` | `'chat.file'` | [L253](../../../../../jiuwenswarm/common/schema/message.py#L253) |
| `CHAT_TOOL_CALL` | `未显式标注` | `'chat.tool_call'` | [L254](../../../../../jiuwenswarm/common/schema/message.py#L254) |
| `CHAT_TOOL_UPDATE` | `未显式标注` | `'chat.tool_update'` | [L255](../../../../../jiuwenswarm/common/schema/message.py#L255) |
| `CHAT_TOOL_RESULT` | `未显式标注` | `'chat.tool_result'` | [L256](../../../../../jiuwenswarm/common/schema/message.py#L256) |
| `CHAT_SYMPHONY_STATUS` | `未显式标注` | `'chat.symphony_status'` | [L257](../../../../../jiuwenswarm/common/schema/message.py#L257) |
| `CONTEXT_USAGE` | `未显式标注` | `'context.usage'` | [L258](../../../../../jiuwenswarm/common/schema/message.py#L258) |
| `TODO_UPDATED` | `未显式标注` | `'todo.updated'` | [L259](../../../../../jiuwenswarm/common/schema/message.py#L259) |
| `TASK_START` | `未显式标注` | `'task.start'` | [L260](../../../../../jiuwenswarm/common/schema/message.py#L260) |
| `TASK_UPDATE` | `未显式标注` | `'task.update'` | [L261](../../../../../jiuwenswarm/common/schema/message.py#L261) |
| `TASK_COMPLETE` | `未显式标注` | `'task.complete'` | [L262](../../../../../jiuwenswarm/common/schema/message.py#L262) |
| `CHAT_PROCESSING_STATUS` | `未显式标注` | `'chat.processing_status'` | [L263](../../../../../jiuwenswarm/common/schema/message.py#L263) |
| `CHAT_ERROR` | `未显式标注` | `'chat.error'` | [L264](../../../../../jiuwenswarm/common/schema/message.py#L264) |
| `CHAT_INTERRUPT_RESULT` | `未显式标注` | `'chat.interrupt_result'` | [L265](../../../../../jiuwenswarm/common/schema/message.py#L265) |
| `CHAT_EVOLUTION_STATUS` | `未显式标注` | `'chat.evolution_status'` | [L266](../../../../../jiuwenswarm/common/schema/message.py#L266) |
| `CHAT_SUBTASK_UPDATE` | `未显式标注` | `'chat.subtask_update'` | [L267](../../../../../jiuwenswarm/common/schema/message.py#L267) |
| `CHAT_ASK_USER_QUESTION` | `未显式标注` | `'chat.ask_user_question'` | [L268](../../../../../jiuwenswarm/common/schema/message.py#L268) |
| `PLAN_APPROVAL_REQUIRED` | `未显式标注` | `'plan.approval_required'` | [L269](../../../../../jiuwenswarm/common/schema/message.py#L269) |
| `CHAT_SESSION_RESULT` | `未显式标注` | `'chat.session_result'` | [L270](../../../../../jiuwenswarm/common/schema/message.py#L270) |
| `GOAL_SNAPSHOT` | `未显式标注` | `'goal.snapshot'` | [L271](../../../../../jiuwenswarm/common/schema/message.py#L271) |
| `GOAL_UPDATED` | `未显式标注` | `'goal.updated'` | [L272](../../../../../jiuwenswarm/common/schema/message.py#L272) |
| `RUNTIME_ACCEPTED` | `未显式标注` | `'runtime.accepted'` | [L273](../../../../../jiuwenswarm/common/schema/message.py#L273) |
| `EXECUTION_ERROR` | `未显式标注` | `'execution.error'` | [L274](../../../../../jiuwenswarm/common/schema/message.py#L274) |
| `TEAM_MEMBER` | `未显式标注` | `'team.member'` | [L275](../../../../../jiuwenswarm/common/schema/message.py#L275) |
| `TEAM_TASK` | `未显式标注` | `'team.task'` | [L276](../../../../../jiuwenswarm/common/schema/message.py#L276) |
| `TEAM_MESSAGE` | `未显式标注` | `'team.message'` | [L277](../../../../../jiuwenswarm/common/schema/message.py#L277) |
| `WORKFLOW_UPDATED` | `未显式标注` | `'workflow.updated'` | [L278](../../../../../jiuwenswarm/common/schema/message.py#L278) |
| `HEARTBEAT_RELAY` | `未显式标注` | `'heartbeat.relay'` | [L279](../../../../../jiuwenswarm/common/schema/message.py#L279) |
| `HISTORY_GET` | `未显式标注` | `'history.message'` | [L280](../../../../../jiuwenswarm/common/schema/message.py#L280) |
| `PROACTIVE_RECOMMENDATION` | `未显式标注` | `'proactive_recommendation'` | [L281](../../../../../jiuwenswarm/common/schema/message.py#L281) |
| `SYNC_AGENTS_CONFIGS_RESULT` | `未显式标注` | `'sync_agents_configs.result'` | [L282](../../../../../jiuwenswarm/common/schema/message.py#L282) |

### [`class Mode(Enum)`](../../../../../jiuwenswarm/common/schema/message.py#L285)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `AGENT` | `未显式标注` | `'agent'` | [L286](../../../../../jiuwenswarm/common/schema/message.py#L286) |
| `AGENT_PLAN` | `未显式标注` | `'agent.plan'` | [L288](../../../../../jiuwenswarm/common/schema/message.py#L288) |
| `AGENT_FAST` | `未显式标注` | `'agent.fast'` | [L289](../../../../../jiuwenswarm/common/schema/message.py#L289) |
| `CODE_PLAN` | `未显式标注` | `'code.plan'` | [L290](../../../../../jiuwenswarm/common/schema/message.py#L290) |
| `CODE_NORMAL` | `未显式标注` | `'code.normal'` | [L291](../../../../../jiuwenswarm/common/schema/message.py#L291) |
| `CODE_TEAM` | `未显式标注` | `'code.team'` | [L292](../../../../../jiuwenswarm/common/schema/message.py#L292) |
| `TEAM` | `未显式标注` | `'team'` | [L293](../../../../../jiuwenswarm/common/schema/message.py#L293) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def from_raw(cls, raw_mode: Any, default: 'Mode \| None' = None) -> 'Mode'` | 解析 mode。plan / fast 已合并：agent.plan / agent.fast 归一为 agent。 | [L296](../../../../../jiuwenswarm/common/schema/message.py#L296) |
| `def to_runtime_mode(self) -> str` | 输出 runtime mode 值；历史 agent.plan / agent.fast 归一为 agent。 | [L321](../../../../../jiuwenswarm/common/schema/message.py#L321) |

### [`class Message`](../../../../../jiuwenswarm/common/schema/message.py#L329)

统一消息结构.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `id` | `str` | `—` | [L331](../../../../../jiuwenswarm/common/schema/message.py#L331) |
| `type` | `Literal['req', 'res', 'event']` | `—` | [L332](../../../../../jiuwenswarm/common/schema/message.py#L332) |
| `channel_id` | `str` | `—` | [L333](../../../../../jiuwenswarm/common/schema/message.py#L333) |
| `session_id` | `str \| None` | `—` | [L334](../../../../../jiuwenswarm/common/schema/message.py#L334) |
| `params` | `dict` | `—` | [L335](../../../../../jiuwenswarm/common/schema/message.py#L335) |
| `timestamp` | `float` | `—` | [L336](../../../../../jiuwenswarm/common/schema/message.py#L336) |
| `ok` | `bool` | `—` | [L337](../../../../../jiuwenswarm/common/schema/message.py#L337) |
| `provider` | `str \| None` | `None` | [L338](../../../../../jiuwenswarm/common/schema/message.py#L338) |
| `chat_id` | `str \| None` | `None` | [L339](../../../../../jiuwenswarm/common/schema/message.py#L339) |
| `user_id` | `str \| None` | `None` | [L340](../../../../../jiuwenswarm/common/schema/message.py#L340) |
| `bot_id` | `str \| None` | `None` | [L341](../../../../../jiuwenswarm/common/schema/message.py#L341) |
| `app_id` | `str \| None` | `None` | [L342](../../../../../jiuwenswarm/common/schema/message.py#L342) |
| `agent_ref` | `Any` | `None` | [L343](../../../../../jiuwenswarm/common/schema/message.py#L343) |
| `payload` | `dict \| None` | `None` | [L344](../../../../../jiuwenswarm/common/schema/message.py#L344) |
| `req_method` | `ReqMethod \| None` | `None` | [L345](../../../../../jiuwenswarm/common/schema/message.py#L345) |
| `event_type` | `EventType \| None` | `None` | [L346](../../../../../jiuwenswarm/common/schema/message.py#L346) |
| `mode` | `Mode` | `Mode.AGENT` | [L347](../../../../../jiuwenswarm/common/schema/message.py#L347) |
| `is_stream` | `bool` | `False` | [L348](../../../../../jiuwenswarm/common/schema/message.py#L348) |
| `stream_seq` | `int \| None` | `None` | [L349](../../../../../jiuwenswarm/common/schema/message.py#L349) |
| `stream_id` | `str \| None` | `None` | [L350](../../../../../jiuwenswarm/common/schema/message.py#L350) |
| `metadata` | `dict[str, Any] \| None` | `None` | [L351](../../../../../jiuwenswarm/common/schema/message.py#L351) |
| `group_digital_avatar` | `bool` | `False` | [L352](../../../../../jiuwenswarm/common/schema/message.py#L352) |
| `enable_memory` | `bool \| None` | `None` | [L353](../../../../../jiuwenswarm/common/schema/message.py#L353) |
| `enable_streaming` | `bool` | `True` | [L354](../../../../../jiuwenswarm/common/schema/message.py#L354) |

## `jiuwenswarm/common/schema/swarmflow_reply.py`

[打开源码](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L1)

**模块职责：** chat.swarmflow_reply 参数契约。

### [`class SwarmflowReplyParams(TypedDict, total=False)`](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L14)

chat.swarmflow_reply 参数契约（TypedDict，供类型标注与文档）。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `session_id` | `str` | `—` | [L17](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L17) |
| `team_name` | `str` | `—` | [L20](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L20) |
| `run_id` | `str` | `—` | [L24](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L24) |
| `correlation_id` | `str` | `—` | [L27](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L27) |
| `answer` | `str` | `—` | [L30](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py#L30) |

## `jiuwenswarm/common/secrets/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/__init__.py#L1)

**模块职责：** Unified secret storage entry point.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L5](../../../../../jiuwenswarm/common/secrets/__init__.py#L5) |

## `jiuwenswarm/common/secrets/envelope.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/envelope.py#L1)

**模块职责：** Built-in envelope: ENC:v1:<algorithm>:<wrap_b64>:<payload_b64>.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `PREFIX` | `未显式标注` | [L5](../../../../../jiuwenswarm/common/secrets/envelope.py#L5) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def parse_envelope(stored: str) -> tuple[str, str, str] \| None` | 源码未提供函数级文档字符串。 | [L8](../../../../../jiuwenswarm/common/secrets/envelope.py#L8) |
| `def build_envelope(algorithm: str, wrap_b64: str, payload_b64: str) -> str` | 源码未提供函数级文档字符串。 | [L21](../../../../../jiuwenswarm/common/secrets/envelope.py#L21) |

## `jiuwenswarm/common/secrets/legacy.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/legacy.py#L1)

**模块职责：** Legacy sensitive-key rules (wraps local_env_config).

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_legacy_sensitive_key(name: str) -> bool` | Whether *name* triggers custom-crypto auto encrypt/decrypt (legacy rules). | [L8](../../../../../jiuwenswarm/common/secrets/legacy.py#L8) |

## `jiuwenswarm/common/secrets/persistence/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/__init__.py#L1)

**模块职责：** L4 persistence adapters.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L8](../../../../../jiuwenswarm/common/secrets/persistence/__init__.py#L8) |

## `jiuwenswarm/common/secrets/persistence/_dotted.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/_dotted.py#L1)

**模块职责：** Dotted-path helpers for structured file mediums.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_dotted(data: Any, field: str) -> Any` | 源码未提供函数级文档字符串。 | [L8](../../../../../jiuwenswarm/common/secrets/persistence/_dotted.py#L8) |
| `def set_dotted(data: dict, field: str, value: Any) -> None` | 源码未提供函数级文档字符串。 | [L17](../../../../../jiuwenswarm/common/secrets/persistence/_dotted.py#L17) |
| `def delete_dotted(data: dict, field: str) -> None` | 源码未提供函数级文档字符串。 | [L29](../../../../../jiuwenswarm/common/secrets/persistence/_dotted.py#L29) |

## `jiuwenswarm/common/secrets/persistence/db.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/db.py#L1)

**模块职责：** DB medium adapter stub (enterprise integration pending).

### [`class DbMediumAdapter`](../../../../../jiuwenswarm/common/secrets/persistence/db.py#L8)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def read_raw(self, loc: StorageLocation) -> str` | 源码未提供方法级文档字符串。 | [L9](../../../../../jiuwenswarm/common/secrets/persistence/db.py#L9) |
| `def write_raw(self, loc: StorageLocation, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L14](../../../../../jiuwenswarm/common/secrets/persistence/db.py#L14) |
| `def delete_raw(self, loc: StorageLocation) -> None` | 源码未提供方法级文档字符串。 | [L19](../../../../../jiuwenswarm/common/secrets/persistence/db.py#L19) |

## `jiuwenswarm/common/secrets/persistence/default_file.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L1)

**模块职责：** Default logical-key storage backend (Phase 1: JSON file).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L10](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L10) |

### [`class DefaultFileStorageBackend`](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L13)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, path: Path) -> None` | 源码未提供方法级文档字符串。 | [L14](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L14) |
| `def read(self, logical_key: str) -> str` | 源码未提供方法级文档字符串。 | [L17](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L17) |
| `def write(self, logical_key: str, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L24](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L24) |
| `def delete(self, logical_key: str) -> None` | 源码未提供方法级文档字符串。 | [L32](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L32) |
| `def _load(self) -> dict` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L37) |
| `def _save(self, data: dict) -> None` | 源码未提供方法级文档字符串。 | [L47](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py#L47) |

## `jiuwenswarm/common/secrets/persistence/env.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L1)

**模块职责：** Env medium adapter (.env single variable).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L12](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L12) |

### [`class EnvMediumAdapter`](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L15)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, env_path: Path) -> None` | 源码未提供方法级文档字符串。 | [L16](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L16) |
| `def read_raw(self, loc: StorageLocation) -> str` | 源码未提供方法级文档字符串。 | [L19](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L19) |
| `def write_raw(self, loc: StorageLocation, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L24](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L24) |
| `def delete_raw(self, loc: StorageLocation) -> None` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L37) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _read_env_var(env_path: Path, name: str) -> str` | 源码未提供函数级文档字符串。 | [L41](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L41) |
| `def _unquote_env_value(value: str) -> str` | 源码未提供函数级文档字符串。 | [L58](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L58) |
| `def _persist_env_updates(env_path: Path, updates: dict[str, str]) -> None` | 源码未提供函数级文档字符串。 | [L65](../../../../../jiuwenswarm/common/secrets/persistence/env.py#L65) |

## `jiuwenswarm/common/secrets/persistence/file.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L1)

**模块职责：** File medium adapter (yaml/json/text).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L14) |

### [`class FileMediumAdapter`](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L17)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, config_dir: Path, workspace_dir: Path) -> None` | 源码未提供方法级文档字符串。 | [L18](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L18) |
| `def read_raw(self, loc: StorageLocation) -> str` | 源码未提供方法级文档字符串。 | [L22](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L22) |
| `def write_raw(self, loc: StorageLocation, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L41](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L41) |
| `def delete_raw(self, loc: StorageLocation) -> None` | 源码未提供方法级文档字符串。 | [L61](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L61) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _load_file(path: Path, fmt: str) -> object` | 源码未提供函数级文档字符串。 | [L65](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L65) |
| `def _save_file(path: Path, fmt: str, data: object) -> None` | 源码未提供函数级文档字符串。 | [L74](../../../../../jiuwenswarm/common/secrets/persistence/file.py#L74) |

## `jiuwenswarm/common/secrets/persistence/gateway.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L1)

**模块职责：** L4: persistence gateway.

### [`class PersistenceGateway`](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L12)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, env_adapter: EnvMediumAdapter, file_adapter: FileMediumAdapter, db_adapter: DbMediumAdapter \| None = None, default_backend: DefaultFileStorageBackend) -> None` | 源码未提供方法级文档字符串。 | [L13](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L13) |
| `def read(self, target: StorageTarget) -> str` | 源码未提供方法级文档字符串。 | [L26](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L26) |
| `def write(self, target: StorageTarget, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L31](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L31) |
| `def delete(self, target: StorageTarget) -> None` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L37) |
| `def _read_location(self, loc: StorageLocation) -> str` | 源码未提供方法级文档字符串。 | [L43](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L43) |
| `def _write_location(self, loc: StorageLocation, raw: str) -> None` | 源码未提供方法级文档字符串。 | [L52](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L52) |
| `def _delete_location(self, loc: StorageLocation) -> None` | 源码未提供方法级文档字符串。 | [L62](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py#L62) |

## `jiuwenswarm/common/secrets/providers/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L1)

**模块职责：** Built-in algorithm providers.

### [`class BuiltinAlgorithm(ABC)`](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L11)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L12](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L12) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@abstractmethod def encrypt(self, plaintext: str) -> tuple[str, str]` | Return (wrap_b64, payload_b64) for envelope. | [L15](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L15) |
| `@abstractmethod def decrypt(self, wrap_b64: str, payload_b64: str) -> str` | 源码未提供方法级文档字符串。 | [L19](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L19) |

### [`class Aes256GcmAlgorithm(BuiltinAlgorithm)`](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L22)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `未显式标注` | `'aes256gcm'` | [L23](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L23) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, master_key: bytes) -> None` | 源码未提供方法级文档字符串。 | [L25](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L25) |
| `@classmethod def from_sources(cls, *, master_key_env: str = 'JIUWEN_SECRET_MASTER_KEY', master_key_file: str = '~/.jiuwenswarm/config/.master_key') -> Aes256GcmAlgorithm` | 源码未提供方法级文档字符串。 | [L31](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L31) |
| `def encrypt(self, plaintext: str) -> tuple[str, str]` | 源码未提供方法级文档字符串。 | [L53](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L53) |
| `def decrypt(self, wrap_b64: str, payload_b64: str) -> str` | 源码未提供方法级文档字符串。 | [L61](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L61) |

### [`class DekAlgorithm(BuiltinAlgorithm)`](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L69)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `未显式标注` | `'dek'` | [L70](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L70) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, private_key_raw: bytes) -> None` | 源码未提供方法级文档字符串。 | [L72](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L72) |
| `@classmethod def from_private_key_b64(cls, private_key_b64: str) -> DekAlgorithm` | 源码未提供方法级文档字符串。 | [L76](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L76) |
| `def encrypt(self, plaintext: str) -> tuple[str, str]` | 源码未提供方法级文档字符串。 | [L80](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L80) |
| `def decrypt(self, wrap_b64: str, payload_b64: str) -> str` | 源码未提供方法级文档字符串。 | [L106](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L106) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _looks_like_b64(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L126](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L126) |
| `def _derive_32(raw: bytes) -> bytes` | 源码未提供函数级文档字符串。 | [L134](../../../../../jiuwenswarm/common/secrets/providers/__init__.py#L134) |

## `jiuwenswarm/common/secrets/registry.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/registry.py#L1)

**模块职责：** L2: secret_registry.yaml routing.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/common/secrets/registry.py#L14) |
| `Medium` | `未显式标注` | [L16](../../../../../jiuwenswarm/common/secrets/registry.py#L16) |
| `Format` | `未显式标注` | [L17](../../../../../jiuwenswarm/common/secrets/registry.py#L17) |
| `_VALID_MEDIA` | `frozenset[str]` | [L19](../../../../../jiuwenswarm/common/secrets/registry.py#L19) |
| `_VALID_FORMATS` | `frozenset[str]` | [L20](../../../../../jiuwenswarm/common/secrets/registry.py#L20) |
| `BUNDLED_REGISTRY_NAME` | `未显式标注` | [L22](../../../../../jiuwenswarm/common/secrets/registry.py#L22) |
| `StorageTarget` | `未显式标注` | [L45](../../../../../jiuwenswarm/common/secrets/registry.py#L45) |

### [`class StorageLocation`](../../../../../jiuwenswarm/common/secrets/registry.py#L33)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `medium` | `Medium` | `—` | [L34](../../../../../jiuwenswarm/common/secrets/registry.py#L34) |
| `path` | `str` | `—` | [L35](../../../../../jiuwenswarm/common/secrets/registry.py#L35) |
| `field` | `str \| None` | `None` | [L36](../../../../../jiuwenswarm/common/secrets/registry.py#L36) |
| `format` | `Format \| None` | `None` | [L37](../../../../../jiuwenswarm/common/secrets/registry.py#L37) |

### [`class DefaultLocation`](../../../../../jiuwenswarm/common/secrets/registry.py#L41)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `logical_key` | `str` | `—` | [L42](../../../../../jiuwenswarm/common/secrets/registry.py#L42) |

### [`class SecretRegistry`](../../../../../jiuwenswarm/common/secrets/registry.py#L68)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, config_dir: Path \| None = None, workspace_dir: Path \| None = None, bundled_path: Path \| None = None, user_path: Path \| None = None, entries: dict[str, StorageLocation] \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L69](../../../../../jiuwenswarm/common/secrets/registry.py#L69) |
| `@property def config_dir(self) -> Path` | 源码未提供方法级文档字符串。 | [L88](../../../../../jiuwenswarm/common/secrets/registry.py#L88) |
| `@property def workspace_dir(self) -> Path` | 源码未提供方法级文档字符串。 | [L92](../../../../../jiuwenswarm/common/secrets/registry.py#L92) |
| `def resolve(self, logical_key: str) -> StorageTarget` | 源码未提供方法级文档字符串。 | [L95](../../../../../jiuwenswarm/common/secrets/registry.py#L95) |
| `def reload(self) -> None` | 源码未提供方法级文档字符串。 | [L101](../../../../../jiuwenswarm/common/secrets/registry.py#L101) |
| `def resolve_file_absolute(self, loc: StorageLocation) -> Path` | 源码未提供方法级文档字符串。 | [L106](../../../../../jiuwenswarm/common/secrets/registry.py#L106) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def bundled_registry_path(*, resources_dir: Path \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L25](../../../../../jiuwenswarm/common/secrets/registry.py#L25) |
| `def derive_legacy_name(logical_key: str, target: StorageTarget) -> str` | 源码未提供函数级文档字符串。 | [L48](../../../../../jiuwenswarm/common/secrets/registry.py#L48) |
| `def resolve_file_path(path: str, *, config_dir: Path, workspace_dir: Path) -> Path` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/common/secrets/registry.py#L54) |
| `def _load_merged_entries(bundled: Path, user: Path) -> dict[str, StorageLocation]` | 源码未提供函数级文档字符串。 | [L114](../../../../../jiuwenswarm/common/secrets/registry.py#L114) |
| `def _read_yaml_mapping(path: Path) -> dict` | 源码未提供函数级文档字符串。 | [L123](../../../../../jiuwenswarm/common/secrets/registry.py#L123) |
| `def _parse_entry(logical_key: str, raw: object) -> StorageLocation` | 源码未提供函数级文档字符串。 | [L131](../../../../../jiuwenswarm/common/secrets/registry.py#L131) |
| `def infer_format_from_path(path: str) -> Format` | 源码未提供函数级文档字符串。 | [L166](../../../../../jiuwenswarm/common/secrets/registry.py#L166) |

## `jiuwenswarm/common/secrets/store.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/store.py#L1)

**模块职责：** L1: SecretStore facade.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/common/secrets/store.py#L20) |
| `_instance` | `SecretStore \| None` | [L22](../../../../../jiuwenswarm/common/secrets/store.py#L22) |

### [`class SecretStore`](../../../../../jiuwenswarm/common/secrets/store.py#L25)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, registry: SecretRegistry, transform: SecretTransform, gateway: PersistenceGateway) -> None` | 源码未提供方法级文档字符串。 | [L26](../../../../../jiuwenswarm/common/secrets/store.py#L26) |
| `@classmethod def get_instance(cls) -> SecretStore` | 源码未提供方法级文档字符串。 | [L38](../../../../../jiuwenswarm/common/secrets/store.py#L38) |
| `@classmethod def build_default(cls, *, config_dir: Path \| None = None, workspace_dir: Path \| None = None) -> SecretStore` | 源码未提供方法级文档字符串。 | [L45](../../../../../jiuwenswarm/common/secrets/store.py#L45) |
| `@classmethod def reset_for_tests(cls, store: SecretStore \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L63](../../../../../jiuwenswarm/common/secrets/store.py#L63) |
| `def get(self, key: str) -> str` | 源码未提供方法级文档字符串。 | [L67](../../../../../jiuwenswarm/common/secrets/store.py#L67) |
| `def set(self, key: str, value: str, *, algorithm: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L73](../../../../../jiuwenswarm/common/secrets/store.py#L73) |
| `def delete(self, key: str) -> None` | 源码未提供方法级文档字符串。 | [L81](../../../../../jiuwenswarm/common/secrets/store.py#L81) |
| `def configure_aes256gcm(self, *, master_key_env: str = 'JIUWEN_SECRET_MASTER_KEY', master_key_file: str = '~/.jiuwenswarm/config/.master_key') -> None` | 源码未提供方法级文档字符串。 | [L85](../../../../../jiuwenswarm/common/secrets/store.py#L85) |
| `def configure_dek(self, *, private_key_b64: str) -> None` | 源码未提供方法级文档字符串。 | [L96](../../../../../jiuwenswarm/common/secrets/store.py#L96) |
| `def register_custom_crypto(self, provider: CryptoProvider) -> None` | 源码未提供方法级文档字符串。 | [L99](../../../../../jiuwenswarm/common/secrets/store.py#L99) |
| `def bridge_legacy_extension_crypto(self) -> None` | 源码未提供方法级文档字符串。 | [L102](../../../../../jiuwenswarm/common/secrets/store.py#L102) |

## `jiuwenswarm/common/secrets/transform.py`

[打开源码](../../../../../jiuwenswarm/common/secrets/transform.py#L1)

**模块职责：** L3: plaintext <-> stored string transforms.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L16](../../../../../jiuwenswarm/common/secrets/transform.py#L16) |

### [`class SecretTransform`](../../../../../jiuwenswarm/common/secrets/transform.py#L19)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L20](../../../../../jiuwenswarm/common/secrets/transform.py#L20) |
| `def register_custom_crypto(self, provider: CryptoProvider \| None) -> None` | 源码未提供方法级文档字符串。 | [L24](../../../../../jiuwenswarm/common/secrets/transform.py#L24) |
| `def configure_aes256gcm(self, *, master_key_env: str = 'JIUWEN_SECRET_MASTER_KEY', master_key_file: str = '~/.jiuwenswarm/config/.master_key') -> None` | 源码未提供方法级文档字符串。 | [L27](../../../../../jiuwenswarm/common/secrets/transform.py#L27) |
| `def configure_dek(self, *, private_key_b64: str) -> None` | 源码未提供方法级文档字符串。 | [L39](../../../../../jiuwenswarm/common/secrets/transform.py#L39) |
| `def encode_for_store(self, logical_key: str, plaintext: str, *, algorithm: str \| None, legacy_name: str) -> str` | 源码未提供方法级文档字符串。 | [L43](../../../../../jiuwenswarm/common/secrets/transform.py#L43) |
| `def decode_from_store(self, logical_key: str, stored: str, *, legacy_name: str) -> str` | 源码未提供方法级文档字符串。 | [L67](../../../../../jiuwenswarm/common/secrets/transform.py#L67) |

## `jiuwenswarm/common/security/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/security/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/common/security/base_crypto.py`

[打开源码](../../../../../jiuwenswarm/common/security/base_crypto.py#L1)

**模块职责：** 定义 CryptoProvider、set_crypto_provider、get_crypto_provider。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_default_provider` | `CryptoProvider` | [L13](../../../../../jiuwenswarm/common/security/base_crypto.py#L13) |

### [`class CryptoProvider(Protocol)`](../../../../../jiuwenswarm/common/security/base_crypto.py#L5)

源码未提供类级文档字符串。

装饰器：`@runtime_checkable`。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def encrypt(self, plaintext: str, **kwargs) -> str` | 源码未提供方法级文档字符串。 | [L6](../../../../../jiuwenswarm/common/security/base_crypto.py#L6) |
| `def decrypt(self, ciphertext: str, **kwargs) -> str` | 源码未提供方法级文档字符串。 | [L9](../../../../../jiuwenswarm/common/security/base_crypto.py#L9) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def set_crypto_provider(provider: CryptoProvider) -> None` | 源码未提供函数级文档字符串。 | [L16](../../../../../jiuwenswarm/common/security/base_crypto.py#L16) |
| `def get_crypto_provider() -> Optional[CryptoProvider]` | 源码未提供函数级文档字符串。 | [L21](../../../../../jiuwenswarm/common/security/base_crypto.py#L21) |

## `jiuwenswarm/common/security/ws_origin.py`

[打开源码](../../../../../jiuwenswarm/common/security/ws_origin.py#L1)

**模块职责：** Shared WebSocket Origin validation helpers.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_ENABLE_ORIGIN_CHECK_ENV` | `未显式标注` | [L13](../../../../../jiuwenswarm/common/security/ws_origin.py#L13) |
| `_ALLOWED_ORIGIN_HOSTS_ENV` | `未显式标注` | [L14](../../../../../jiuwenswarm/common/security/ws_origin.py#L14) |
| `_FORBIDDEN_BODY` | `未显式标注` | [L15](../../../../../jiuwenswarm/common/security/ws_origin.py#L15) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_origin_check_enabled() -> bool` | Return whether WebSocket Origin validation is enabled. | [L18](../../../../../jiuwenswarm/common/security/ws_origin.py#L18) |
| `def get_allowed_origin_hosts() -> set[str]` | Return the global WebSocket Origin hostname allowlist from environment. | [L23](../../../../../jiuwenswarm/common/security/ws_origin.py#L23) |
| `def is_allowed_browser_origin(origin: str \| None) -> bool` | 校验浏览器 Origin 是否允许访问 WebSocket 服务。 | [L31](../../../../../jiuwenswarm/common/security/ws_origin.py#L31) |
| `def extract_handshake_request(args: tuple[Any, ...]) -> tuple[str, Any]` | Extract path and headers from legacy/new websockets process_request args. | [L49](../../../../../jiuwenswarm/common/security/ws_origin.py#L49) |
| `def get_header_value(headers: Any, key: str) -> str \| None` | Read a header from either legacy or modern websockets header containers. | [L66](../../../../../jiuwenswarm/common/security/ws_origin.py#L66) |
| `def forbidden_origin_response(process_request_args: tuple[Any, ...]) -> Any` | Build a 403 response for legacy/new websockets process_request APIs. | [L79](../../../../../jiuwenswarm/common/security/ws_origin.py#L79) |

## `jiuwenswarm/common/stage_timer.py`

[打开源码](../../../../../jiuwenswarm/common/stage_timer.py#L1)

**模块职责：** Per-stage timing for a linear sequence of steps on a hot path.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L72](../../../../../jiuwenswarm/common/stage_timer.py#L72) |

### [`class StageTimer`](../../../../../jiuwenswarm/common/stage_timer.py#L22)

Accumulate elapsed time per named stage of one pass through a code path.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | Start the clock for the first stage. | [L29](../../../../../jiuwenswarm/common/stage_timer.py#L29) |
| `def mark(self, stage: str) -> None` | Close the current stage and open the next one. | [L35](../../../../../jiuwenswarm/common/stage_timer.py#L35) |
| `def total_ms(self) -> float` | Return milliseconds elapsed since construction. | [L47](../../../../../jiuwenswarm/common/stage_timer.py#L47) |
| `def render(self, *, slowest_first: bool = False) -> str` | Render the recorded stages as a single-line ``name=ms`` breakdown. | [L55](../../../../../jiuwenswarm/common/stage_timer.py#L55) |

## `jiuwenswarm/common/thinking/__init__.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/__init__.py#L1)

**模块职责：** Subagent thinking control: semantic thinking → vendor kwargs.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L15](../../../../../jiuwenswarm/common/thinking/__init__.py#L15) |

## `jiuwenswarm/common/thinking/adapter.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/adapter.py#L1)

**模块职责：** Adapt semantic thinking (default|off|on) to frozen vendor kwargs.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_model_name(model: Any) -> str` | Best-effort model name from openjiuwen Model / config objects. | [L18](../../../../../jiuwenswarm/common/thinking/adapter.py#L18) |
| `def adapt_thinking(thinking: str \| None, model: Any = None, *, model_name: str = '') -> ThinkingProfile` | Build a frozen ThinkingProfile for one subagent lifetime. | [L38](../../../../../jiuwenswarm/common/thinking/adapter.py#L38) |

## `jiuwenswarm/common/thinking/rail.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/rail.py#L1)

**模块职责：** Rail that injects frozen thinking kwargs into each model call.

### [`class ThinkingInjectRail(DeepAgentRail)`](../../../../../jiuwenswarm/common/thinking/rail.py#L20)

Replay frozen thinking llm_call_kwargs before each model call.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `priority` | `未显式标注` | `15` | [L29](../../../../../jiuwenswarm/common/thinking/rail.py#L29) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, profile: ThinkingProfile \| None, *, role_id: str = '', agent_id: str = '') -> None` | 源码未提供方法级文档字符串。 | [L31](../../../../../jiuwenswarm/common/thinking/rail.py#L31) |
| `async def before_model_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L44](../../../../../jiuwenswarm/common/thinking/rail.py#L44) |

## `jiuwenswarm/common/thinking/register_hook.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/register_hook.py#L1)

**模块职责：** Register TaskTool subagent thinking hook with openjiuwen core.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_REGISTERED` | `未显式标注` | [L18](../../../../../jiuwenswarm/common/thinking/register_hook.py#L18) |
| `__all__` | `未显式标注` | [L78](../../../../../jiuwenswarm/common/thinking/register_hook.py#L78) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _on_subagent_thinking(subagent: Any, *, thinking: str, model: Any = None) -> None` | Attach ThinkingInjectRail when thinking is explicitly off/on. | [L21](../../../../../jiuwenswarm/common/thinking/register_hook.py#L21) |
| `def register_thinking_hook() -> None` | Idempotent registration of the core TaskTool thinking hook. | [L57](../../../../../jiuwenswarm/common/thinking/register_hook.py#L57) |

## `jiuwenswarm/common/thinking/types.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/types.py#L1)

**模块职责：** Types for subagent thinking control (semantic tiers).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `THINKING_VALUES` | `未显式标注` | [L15](../../../../../jiuwenswarm/common/thinking/types.py#L15) |

### [`class ThinkingProfile`](../../../../../jiuwenswarm/common/thinking/types.py#L48)

Frozen per-subagent thinking injection profile.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `thinking` | `str` | `—` | [L51](../../../../../jiuwenswarm/common/thinking/types.py#L51) |
| `llm_call_kwargs` | `Mapping[str, Any]` | `—` | [L52](../../../../../jiuwenswarm/common/thinking/types.py#L52) |
| `injected` | `bool` | `—` | [L53](../../../../../jiuwenswarm/common/thinking/types.py#L53) |
| `degraded` | `bool` | `—` | [L54](../../../../../jiuwenswarm/common/thinking/types.py#L54) |
| `reason` | `str \| None` | `None` | [L55](../../../../../jiuwenswarm/common/thinking/types.py#L55) |
| `vendor_style` | `str \| None` | `None` | [L56](../../../../../jiuwenswarm/common/thinking/types.py#L56) |
| `model_name` | `str` | `''` | [L57](../../../../../jiuwenswarm/common/thinking/types.py#L57) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@staticmethod def empty(*, thinking: str = 'default', degraded: bool = False, reason: str \| None = None, vendor_style: str \| None = None, model_name: str = '') -> ThinkingProfile` | 源码未提供方法级文档字符串。 | [L60](../../../../../jiuwenswarm/common/thinking/types.py#L60) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _deep_freeze(obj: Any) -> Any` | Recursively freeze mappings/lists so nested values are immutable. | [L18](../../../../../jiuwenswarm/common/thinking/types.py#L18) |
| `def freeze_llm_call_kwargs(kwargs: Mapping[str, Any] \| None) -> Mapping[str, Any]` | Deep-copy then recursively freeze so nested dicts cannot be mutated. | [L29](../../../../../jiuwenswarm/common/thinking/types.py#L29) |
| `def thaw_llm_call_kwargs(kwargs: Mapping[str, Any] \| None) -> dict[str, Any]` | Return a deep mutable copy of frozen kwargs (safe for per-call inject). | [L34](../../../../../jiuwenswarm/common/thinking/types.py#L34) |
| `def normalize_thinking(raw: Any) -> tuple[str, bool]` | Normalize tool input to default\|off\|on. | [L79](../../../../../jiuwenswarm/common/thinking/types.py#L79) |
| `def kwargs_digest(kwargs: Mapping[str, Any] \| None) -> str` | Compact, log-safe summary of injected kwargs (no secrets expected). | [L106](../../../../../jiuwenswarm/common/thinking/types.py#L106) |

## `jiuwenswarm/common/thinking/vendor_map.py`

[打开源码](../../../../../jiuwenswarm/common/thinking/vendor_map.py#L1)

**模块职责：** Vendor match table for thinking control (no skill/role knowledge).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_VENDOR_PATTERNS` | `tuple[tuple[re.Pattern[str], str], ...]` | [L15](../../../../../jiuwenswarm/common/thinking/vendor_map.py#L15) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def match_vendor_style(model_name: str) -> str \| None` | Return vendor style id for model_name, or None if unsupported. | [L27](../../../../../jiuwenswarm/common/thinking/vendor_map.py#L27) |
| `def style_to_kwargs(style: str, *, enabled: bool) -> dict` | Map vendor style + on/off to physical llm_call_kwargs. | [L38](../../../../../jiuwenswarm/common/thinking/vendor_map.py#L38) |

## `jiuwenswarm/common/tool_display.py`

[打开源码](../../../../../jiuwenswarm/common/tool_display.py#L1)

**模块职责：** 工具调用可读展示名。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_CALL_GOAL_KEYS` | `未显式标注` | [L17](../../../../../jiuwenswarm/common/tool_display.py#L17) |
| `_VERB_BY_TOOL` | `dict[str, str]` | [L19](../../../../../jiuwenswarm/common/tool_display.py#L19) |
| `_FILE_VERBS` | `未显式标注` | [L52](../../../../../jiuwenswarm/common/tool_display.py#L52) |
| `_QUERY_VERBS` | `未显式标注` | [L53](../../../../../jiuwenswarm/common/tool_display.py#L53) |
| `_COMMAND_VERBS` | `未显式标注` | [L54](../../../../../jiuwenswarm/common/tool_display.py#L54) |
| `_SKILL_VERBS` | `未显式标注` | [L55](../../../../../jiuwenswarm/common/tool_display.py#L55) |
| `_TODO_VERBS` | `未显式标注` | [L56](../../../../../jiuwenswarm/common/tool_display.py#L56) |
| `_FILE_ARG_KEYS` | `未显式标注` | [L58](../../../../../jiuwenswarm/common/tool_display.py#L58) |
| `_QUERY_ARG_KEYS` | `未显式标注` | [L63](../../../../../jiuwenswarm/common/tool_display.py#L63) |
| `_COMMAND_ARG_KEYS` | `未显式标注` | [L64](../../../../../jiuwenswarm/common/tool_display.py#L64) |
| `_URL_ARG_KEYS` | `未显式标注` | [L65](../../../../../jiuwenswarm/common/tool_display.py#L65) |
| `_SKILL_ARG_KEYS` | `未显式标注` | [L66](../../../../../jiuwenswarm/common/tool_display.py#L66) |
| `_CALL_GOAL_SCHEMA` | `dict[str, Any]` | [L68](../../../../../jiuwenswarm/common/tool_display.py#L68) |
| `_CALL_GOAL_MAX_LEN` | `未显式标注` | [L156](../../../../../jiuwenswarm/common/tool_display.py#L156) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize(name: str) -> str` | 源码未提供函数级文档字符串。 | [L80](../../../../../jiuwenswarm/common/tool_display.py#L80) |
| `def _as_mapping(arguments: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L89](../../../../../jiuwenswarm/common/tool_display.py#L89) |
| `def _first_string(args: Mapping[str, Any], keys: tuple[str, ...]) -> str` | 源码未提供函数级文档字符串。 | [L102](../../../../../jiuwenswarm/common/tool_display.py#L102) |
| `def _basename(path: str) -> str` | 源码未提供函数级文档字符串。 | [L124](../../../../../jiuwenswarm/common/tool_display.py#L124) |
| `def _truncate(value: str, max_len: int) -> str` | 源码未提供函数级文档字符串。 | [L132](../../../../../jiuwenswarm/common/tool_display.py#L132) |
| `def inject_call_goal_schema(parameters: Any) -> None` | 给工具 JSON Schema 注入可选 call_goal，供主模型随 tool_call 一并产出。 | [L137](../../../../../jiuwenswarm/common/tool_display.py#L137) |
| `def extract_call_goal(arguments: Any) -> tuple[str, Any]` | 从 arguments 取出 call_goal，并返回剥掉该字段后的 arguments（保持原类型风格）。 | [L159](../../../../../jiuwenswarm/common/tool_display.py#L159) |
| `def _format_message_to(to_value: Any) -> str` | 源码未提供函数级文档字符串。 | [L187](../../../../../jiuwenswarm/common/tool_display.py#L187) |
| `def build_tool_display_name(name: str, arguments: Any) -> str` | 规则兜底：根据工具名+参数组可读展示名；无法可靠组出时返回空串。 | [L203](../../../../../jiuwenswarm/common/tool_display.py#L203) |

## `jiuwenswarm/common/tool_ownership.py`

[打开源码](../../../../../jiuwenswarm/common/tool_ownership.py#L1)

**模块职责：** Single source of truth for how a tool instance is registered process-wide.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L32](../../../../../jiuwenswarm/common/tool_ownership.py#L32) |
| `_TOOL_REGISTER_LOCK` | `未显式标注` | [L34](../../../../../jiuwenswarm/common/tool_ownership.py#L34) |
| `_DEFAULT_STATELESS` | `未显式标注` | [L40](../../../../../jiuwenswarm/common/tool_ownership.py#L40) |
| `__all__` | `未显式标注` | [L147](../../../../../jiuwenswarm/common/tool_ownership.py#L147) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def mark_stateless(tools: list[Any]) -> list[Any]` | Declare tool instances as shared across agents. | [L43](../../../../../jiuwenswarm/common/tool_ownership.py#L43) |
| `def qualify_tool_id(card: ToolCard, owner_id: str) -> str` | Return the agent-qualified registration id for an owned tool. | [L65](../../../../../jiuwenswarm/common/tool_ownership.py#L65) |
| `def ensure_tool_registered(tool: Any) -> Any` | Register ``tool`` in ``Runner.resource_mgr`` if missing (thread-safe). | [L81](../../../../../jiuwenswarm/common/tool_ownership.py#L81) |
| `def register_tool(tool: Any, owner_id: str \| None) -> Any` | Register one tool instance under the ownership its card declares. | [L115](../../../../../jiuwenswarm/common/tool_ownership.py#L115) |
| `def unregister_tool(tool: Any) -> None` | Drop an owned tool's registration, leaving shared instances in place. | [L132](../../../../../jiuwenswarm/common/tool_ownership.py#L132) |

## `jiuwenswarm/common/updater.py`

[打开源码](../../../../../jiuwenswarm/common/updater.py#L1)

**模块职责：** 定义 UpdateStatus、UpdaterService、get_access_token、_is_newer_version、_detect_install_mode、_platform_asset_key。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_RELEASE_API_GITCODE` | `未显式标注` | [L23](../../../../../jiuwenswarm/common/updater.py#L23) |
| `DEFAULT_RELEASE_API_GITHUB` | `未显式标注` | [L24](../../../../../jiuwenswarm/common/updater.py#L24) |
| `DEFAULT_RELEASE_API_PYPI` | `未显式标注` | [L25](../../../../../jiuwenswarm/common/updater.py#L25) |
| `DEFAULT_ASSET_PATTERN_WINDOWS` | `未显式标注` | [L26](../../../../../jiuwenswarm/common/updater.py#L26) |
| `DEFAULT_ASSET_PATTERN_MACOS` | `未显式标注` | [L27](../../../../../jiuwenswarm/common/updater.py#L27) |
| `DEFAULT_ASSET_PATTERN_LINUX` | `未显式标注` | [L28](../../../../../jiuwenswarm/common/updater.py#L28) |
| `DEFAULT_TIMEOUT_SECONDS` | `未显式标注` | [L29](../../../../../jiuwenswarm/common/updater.py#L29) |
| `DEFAULT_TEXT` | `未显式标注` | [L30](../../../../../jiuwenswarm/common/updater.py#L30) |
| `DESKTOP_ENV_FLAG` | `未显式标注` | [L31](../../../../../jiuwenswarm/common/updater.py#L31) |
| `DEFAULT_SOURCE_CONFIG` | `dict[str, Any]` | [L33](../../../../../jiuwenswarm/common/updater.py#L33) |

### [`class UpdateStatus`](../../../../../jiuwenswarm/common/updater.py#L82)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `current_version` | `str` | `—` | [L83](../../../../../jiuwenswarm/common/updater.py#L83) |
| `latest_version` | `str` | `''` | [L84](../../../../../jiuwenswarm/common/updater.py#L84) |
| `state` | `str` | `'idle'` | [L85](../../../../../jiuwenswarm/common/updater.py#L85) |
| `has_update` | `bool` | `False` | [L86](../../../../../jiuwenswarm/common/updater.py#L86) |
| `install_mode` | `str` | `''` | [L87](../../../../../jiuwenswarm/common/updater.py#L87) |
| `release_notes` | `str` | `''` | [L88](../../../../../jiuwenswarm/common/updater.py#L88) |
| `published_at` | `str` | `''` | [L89](../../../../../jiuwenswarm/common/updater.py#L89) |
| `source_type` | `str` | `''` | [L90](../../../../../jiuwenswarm/common/updater.py#L90) |
| `asset_name` | `str` | `''` | [L91](../../../../../jiuwenswarm/common/updater.py#L91) |
| `matched_asset` | `str` | `''` | [L92](../../../../../jiuwenswarm/common/updater.py#L92) |
| `download_url` | `str` | `''` | [L93](../../../../../jiuwenswarm/common/updater.py#L93) |
| `downloaded_path` | `str` | `''` | [L94](../../../../../jiuwenswarm/common/updater.py#L94) |
| `downloaded_bytes` | `int` | `0` | [L95](../../../../../jiuwenswarm/common/updater.py#L95) |
| `total_bytes` | `int` | `0` | [L96](../../../../../jiuwenswarm/common/updater.py#L96) |
| `error` | `str` | `''` | [L97](../../../../../jiuwenswarm/common/updater.py#L97) |
| `checked_at` | `float` | `0.0` | [L98](../../../../../jiuwenswarm/common/updater.py#L98) |
| `installing` | `bool` | `False` | [L99](../../../../../jiuwenswarm/common/updater.py#L99) |
| `restart_command` | `str` | `''` | [L100](../../../../../jiuwenswarm/common/updater.py#L100) |

### [`class UpdaterService`](../../../../../jiuwenswarm/common/updater.py#L103)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L104](../../../../../jiuwenswarm/common/updater.py#L104) |
| `def get_status(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L112](../../../../../jiuwenswarm/common/updater.py#L112) |
| `def get_runtime_config(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L119](../../../../../jiuwenswarm/common/updater.py#L119) |
| `@staticmethod def _mask_token(token: str) -> str` | 源码未提供方法级文档字符串。 | [L139](../../../../../jiuwenswarm/common/updater.py#L139) |
| `def check(self, manual: bool = False) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L144](../../../../../jiuwenswarm/common/updater.py#L144) |
| `def start_download(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L169](../../../../../jiuwenswarm/common/updater.py#L169) |
| `def start_upgrade(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L215](../../../../../jiuwenswarm/common/updater.py#L215) |
| `def _executor_callback(self, updates: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L258](../../../../../jiuwenswarm/common/updater.py#L258) |
| `@staticmethod def _create_version_source(config: dict[str, Any]) -> Any` | 源码未提供方法级文档字符串。 | [L262](../../../../../jiuwenswarm/common/updater.py#L262) |
| `def _check(self, config: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L294](../../../../../jiuwenswarm/common/updater.py#L294) |
| `def _resolve_desktop_asset(self, config: dict[str, Any], release: ReleaseInfo) -> None` | 源码未提供方法级文档字符串。 | [L331](../../../../../jiuwenswarm/common/updater.py#L331) |
| `def _resolve_pip_asset(self, config: dict[str, Any], release: ReleaseInfo) -> None` | 源码未提供方法级文档字符串。 | [L362](../../../../../jiuwenswarm/common/updater.py#L362) |
| `@staticmethod def _load_config() -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L387](../../../../../jiuwenswarm/common/updater.py#L387) |
| `def _update_status(self, **updates: Any) -> None` | 源码未提供方法级文档字符串。 | [L445](../../../../../jiuwenswarm/common/updater.py#L445) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_access_token() -> str` | 源码未提供函数级文档字符串。 | [L46](../../../../../jiuwenswarm/common/updater.py#L46) |
| `def _is_newer_version(candidate: str, current: str) -> bool` | Return True when *candidate* is a newer release than *current*. | [L50](../../../../../jiuwenswarm/common/updater.py#L50) |
| `def _detect_install_mode() -> str` | 源码未提供函数级文档字符串。 | [L66](../../../../../jiuwenswarm/common/updater.py#L66) |
| `def _platform_asset_key() -> str` | 源码未提供函数级文档字符串。 | [L73](../../../../../jiuwenswarm/common/updater.py#L73) |

## `jiuwenswarm/common/updater_restart_helper.py`

[打开源码](../../../../../jiuwenswarm/common/updater_restart_helper.py#L1)

**模块职责：** 定义 _background_flags、_wait_for_port、_wait_for_port_release、main。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L15](../../../../../jiuwenswarm/common/updater_restart_helper.py#L15) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _background_flags() -> int` | 源码未提供函数级文档字符串。 | [L18](../../../../../jiuwenswarm/common/updater_restart_helper.py#L18) |
| `def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool` | 源码未提供函数级文档字符串。 | [L26](../../../../../jiuwenswarm/common/updater_restart_helper.py#L26) |
| `def _wait_for_port_release(host: str, port: int, timeout: float = 15.0) -> bool` | 源码未提供函数级文档字符串。 | [L30](../../../../../jiuwenswarm/common/updater_restart_helper.py#L30) |
| `def main() -> None` | 源码未提供函数级文档字符串。 | [L34](../../../../../jiuwenswarm/common/updater_restart_helper.py#L34) |

## `jiuwenswarm/common/upgrade_executor.py`

[打开源码](../../../../../jiuwenswarm/common/upgrade_executor.py#L1)

**模块职责：** 定义 UpgradeExecutor、DesktopExecutor、PipExecutor、_updates_dir、create_executor。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DOWNLOAD_CHUNK_SIZE` | `未显式标注` | [L20](../../../../../jiuwenswarm/common/upgrade_executor.py#L20) |

### [`class UpgradeExecutor(ABC)`](../../../../../jiuwenswarm/common/upgrade_executor.py#L29)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `upgrade_mode` | `str` | `''` | [L30](../../../../../jiuwenswarm/common/upgrade_executor.py#L30) |
| `is_platform_supported` | `bool` | `True` | [L31](../../../../../jiuwenswarm/common/upgrade_executor.py#L31) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: dict[str, Any], status_callback: Callable[[dict[str, Any]], None]) -> None` | 源码未提供方法级文档字符串。 | [L33](../../../../../jiuwenswarm/common/upgrade_executor.py#L33) |
| `@abstractmethod def install(self) -> None` | 源码未提供方法级文档字符串。 | [L42](../../../../../jiuwenswarm/common/upgrade_executor.py#L42) |
| `def upgrade(self) -> None` | Default upgrade raises; overridden by executors that install. | [L45](../../../../../jiuwenswarm/common/upgrade_executor.py#L45) |
| `@staticmethod def _fetch_text(url: str, headers: dict[str, str], timeout: int) -> str` | 源码未提供方法级文档字符串。 | [L52](../../../../../jiuwenswarm/common/upgrade_executor.py#L52) |
| `@staticmethod def _download_headers() -> dict[str, str]` | 源码未提供方法级文档字符串。 | [L69](../../../../../jiuwenswarm/common/upgrade_executor.py#L69) |

### [`class DesktopExecutor(UpgradeExecutor)`](../../../../../jiuwenswarm/common/upgrade_executor.py#L82)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `upgrade_mode` | `未显式标注` | `'desktop'` | [L83](../../../../../jiuwenswarm/common/upgrade_executor.py#L83) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: dict[str, Any], status_callback: Callable[[dict[str, Any]], None]) -> None` | 源码未提供方法级文档字符串。 | [L85](../../../../../jiuwenswarm/common/upgrade_executor.py#L85) |
| `def install(self) -> None` | 源码未提供方法级文档字符串。 | [L93](../../../../../jiuwenswarm/common/upgrade_executor.py#L93) |
| `def _download_file(self, url: str, destination: Path, headers: dict[str, str], timeout: int) -> None` | 源码未提供方法级文档字符串。 | [L123](../../../../../jiuwenswarm/common/upgrade_executor.py#L123) |

### [`class PipExecutor(UpgradeExecutor)`](../../../../../jiuwenswarm/common/upgrade_executor.py#L154)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `upgrade_mode` | `未显式标注` | `'pip'` | [L155](../../../../../jiuwenswarm/common/upgrade_executor.py#L155) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: dict[str, Any], status_callback: Callable[[dict[str, Any]], None]) -> None` | 源码未提供方法级文档字符串。 | [L157](../../../../../jiuwenswarm/common/upgrade_executor.py#L157) |
| `def install(self) -> None` | 源码未提供方法级文档字符串。 | [L165](../../../../../jiuwenswarm/common/upgrade_executor.py#L165) |
| `def _check_editable_install(self, package: str) -> str \| None` | 源码未提供方法级文档字符串。 | [L232](../../../../../jiuwenswarm/common/upgrade_executor.py#L232) |
| `@staticmethod def _find_uv_binary() -> str \| None` | 源码未提供方法级文档字符串。 | [L265](../../../../../jiuwenswarm/common/upgrade_executor.py#L265) |
| `@staticmethod def _is_uv_managed_venv() -> bool` | Return True only when running inside a uv-managed virtual environment. | [L280](../../../../../jiuwenswarm/common/upgrade_executor.py#L280) |
| `@staticmethod def _resolve_uv_command() -> str \| None` | 源码未提供方法级文档字符串。 | [L306](../../../../../jiuwenswarm/common/upgrade_executor.py#L306) |
| `def _build_install_args(self, package: str, timeout: int) -> list[str]` | 源码未提供方法级文档字符串。 | [L311](../../../../../jiuwenswarm/common/upgrade_executor.py#L311) |
| `def upgrade(self) -> None` | 源码未提供方法级文档字符串。 | [L330](../../../../../jiuwenswarm/common/upgrade_executor.py#L330) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _updates_dir() -> Path` | 源码未提供函数级文档字符串。 | [L23](../../../../../jiuwenswarm/common/upgrade_executor.py#L23) |
| `def create_executor(install_mode: str, config: dict[str, Any], status_callback: Callable[[dict[str, Any]], None]) -> UpgradeExecutor` | 源码未提供函数级文档字符串。 | [L418](../../../../../jiuwenswarm/common/upgrade_executor.py#L418) |

## `jiuwenswarm/common/utils.py`

[打开源码](../../../../../jiuwenswarm/common/utils.py#L1)

**模块职责：** 定义 CopyDiffResult、TrackCopyDiff、LoggingLevels、SafeRotatingFileHandler、_ComponentNameFilter、_CompositeFilter 等符号。

**同名定义覆盖：** 下列较早定义已被后续同名定义覆盖，不属于当前可调用接口。

| 名称 | 被覆盖定义 | 当前生效定义 |
| --- | --- | --- |
| `AsyncLRUCache` | [L3331](../../../../../jiuwenswarm/common/utils.py#L3331) | [L3541](../../../../../jiuwenswarm/common/utils.py#L3541) |

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL` | `bool` | [L64](../../../../../jiuwenswarm/common/utils.py#L64) |
| `_LOG_FILE_MAX_BYTES` | `未显式标注` | [L66](../../../../../jiuwenswarm/common/utils.py#L66) |
| `_LOG_FILE_BACKUP_COUNT` | `未显式标注` | [L67](../../../../../jiuwenswarm/common/utils.py#L67) |
| `_user_home` | `Path \| None` | [L535](../../../../../jiuwenswarm/common/utils.py#L535) |
| `_workspace_base_dir` | `Path \| None` | [L536](../../../../../jiuwenswarm/common/utils.py#L536) |
| `_config_dir` | `Path \| None` | [L607](../../../../../jiuwenswarm/common/utils.py#L607) |
| `_workspace_dir` | `Path \| None` | [L608](../../../../../jiuwenswarm/common/utils.py#L608) |
| `_root_dir` | `Path \| None` | [L609](../../../../../jiuwenswarm/common/utils.py#L609) |
| `_is_package` | `bool \| None` | [L610](../../../../../jiuwenswarm/common/utils.py#L610) |
| `_initialized` | `bool` | [L611](../../../../../jiuwenswarm/common/utils.py#L611) |
| `_AGENT_WORKSPACE_DIR_NAMES` | `未显式标注` | [L1765](../../../../../jiuwenswarm/common/utils.py#L1765) |
| `get_tenant_agent_jiuwenclaw_workspace_dir` | `未显式标注` | [L1850](../../../../../jiuwenswarm/common/utils.py#L1850) |
| `JIUWENSWARM_SHARED_SKILLS_DIRS_ENV` | `未显式标注` | [L1895](../../../../../jiuwenswarm/common/utils.py#L1895) |
| `JIUWENCLAW_SHARED_SKILLS_DIRS_ENV` | `未显式标注` | [L1897](../../../../../jiuwenswarm/common/utils.py#L1897) |
| `_GIT_BRANCH_CACHE` | `dict[str, tuple[float, str]]` | [L2196](../../../../../jiuwenswarm/common/utils.py#L2196) |
| `_GIT_BRANCH_TTL_SECONDS` | `未显式标注` | [L2197](../../../../../jiuwenswarm/common/utils.py#L2197) |
| `_SENSITIVE_MASK` | `未显式标注` | [L2301](../../../../../jiuwenswarm/common/utils.py#L2301) |
| `_DATA_IMAGE_PATTERN` | `未显式标注` | [L2302](../../../../../jiuwenswarm/common/utils.py#L2302) |
| `_KV_SENSITIVE_PATTERN` | `未显式标注` | [L2312](../../../../../jiuwenswarm/common/utils.py#L2312) |
| `_NAMED_SENSITIVE_KV_PATTERN` | `未显式标注` | [L2327](../../../../../jiuwenswarm/common/utils.py#L2327) |
| `_BEARER_SENSITIVE_PATTERN` | `未显式标注` | [L2335](../../../../../jiuwenswarm/common/utils.py#L2335) |
| `_SENSITIVE_PATTERNS` | `list[re.Pattern[str]]` | [L2336](../../../../../jiuwenswarm/common/utils.py#L2336) |
| `_SENSITIVE_PII_PATTERNS` | `tuple[re.Pattern[str], ...]` | [L2353](../../../../../jiuwenswarm/common/utils.py#L2353) |
| `_SENSITIVE_CREDENTIAL_PATTERNS` | `tuple[re.Pattern[str], ...]` | [L2355](../../../../../jiuwenswarm/common/utils.py#L2355) |
| `_ALREADY_MASKED_PATTERN` | `未显式标注` | [L2372](../../../../../jiuwenswarm/common/utils.py#L2372) |
| `_sanitize_engine_fallback_failures` | `未显式标注` | [L2375](../../../../../jiuwenswarm/common/utils.py#L2375) |
| `_identity_sanitize_failures` | `未显式标注` | [L2377](../../../../../jiuwenswarm/common/utils.py#L2377) |
| `_source_record_masking_installed` | `未显式标注` | [L2542](../../../../../jiuwenswarm/common/utils.py#L2542) |
| `_source_masking_failures` | `未显式标注` | [L2544](../../../../../jiuwenswarm/common/utils.py#L2544) |
| `_log_queue` | `_queue.SimpleQueue \| None` | [L2909](../../../../../jiuwenswarm/common/utils.py#L2909) |
| `_log_listener` | `QueueListener \| None` | [L2910](../../../../../jiuwenswarm/common/utils.py#L2910) |
| `_SUPPORTS_RESPECT_HANDLER_LEVEL` | `bool` | [L2912](../../../../../jiuwenswarm/common/utils.py#L2912) |
| `_FILE_HANDLER_LEVEL_MAP` | `dict[str, str]` | [L3195](../../../../../jiuwenswarm/common/utils.py#L3195) |
| `_LOGGING_CONFIG_TABLE` | `未显式标注` | [L3250](../../../../../jiuwenswarm/common/utils.py#L3250) |
| `logger` | `未显式标注` | [L3393](../../../../../jiuwenswarm/common/utils.py#L3393) |
| `_TOOL_ARGS_LOG_MAX_DEFAULT` | `未显式标注` | [L3397](../../../../../jiuwenswarm/common/utils.py#L3397) |

### [`class CopyDiffResult`](../../../../../jiuwenswarm/common/utils.py#L71)

Result of copy operation with diff tracking.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `added_dirs` | `list[str]` | `—` | [L73](../../../../../jiuwenswarm/common/utils.py#L73) |
| `added_files` | `list[str]` | `—` | [L74](../../../../../jiuwenswarm/common/utils.py#L74) |
| `overwritten_files` | `list[str]` | `—` | [L75](../../../../../jiuwenswarm/common/utils.py#L75) |

### [`class TrackCopyDiff`](../../../../../jiuwenswarm/common/utils.py#L78)

上下文管理器：自动追踪拷贝前后差异。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, dest: Path, cumulative: Optional[CopyDiffResult] = None, is_file: bool = False, overwrite: bool = False)` | 源码未提供方法级文档字符串。 | [L107](../../../../../jiuwenswarm/common/utils.py#L107) |
| `def __enter__(self) -> CopyDiffResult` | 源码未提供方法级文档字符串。 | [L120](../../../../../jiuwenswarm/common/utils.py#L120) |
| `def __exit__(self, exc_type, exc_val, exc_tb)` | 源码未提供方法级文档字符串。 | [L139](../../../../../jiuwenswarm/common/utils.py#L139) |

### [`class LoggingLevels`](../../../../../jiuwenswarm/common/utils.py#L185)

Container for logging level configuration.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `logger` | `int` | `—` | [L187](../../../../../jiuwenswarm/common/utils.py#L187) |
| `console` | `int` | `—` | [L188](../../../../../jiuwenswarm/common/utils.py#L188) |
| `gateway` | `int` | `—` | [L189](../../../../../jiuwenswarm/common/utils.py#L189) |
| `channel` | `int` | `—` | [L190](../../../../../jiuwenswarm/common/utils.py#L190) |
| `agent_server` | `int` | `—` | [L191](../../../../../jiuwenswarm/common/utils.py#L191) |
| `full` | `int` | `—` | [L192](../../../../../jiuwenswarm/common/utils.py#L192) |

### [`class SafeRotatingFileHandler(BaseRotatingHandler)`](../../../../../jiuwenswarm/common/utils.py#L195)

Safe rotating file handler

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, filename, maxBytes = 0, backupCount = 0, encoding = None, delay = False, errors = None)` | Initialize the handler. | [L198](../../../../../jiuwenswarm/common/utils.py#L198) |
| `def shouldRollover(self, record)` | Determine if rollover should occur. | [L209](../../../../../jiuwenswarm/common/utils.py#L209) |
| `def doRollover(self)` | Perform log rotation to keep app.log as the active log file. | [L224](../../../../../jiuwenswarm/common/utils.py#L224) |
| `def _cleanup_old_backups(self)` | Remove old backup files if they exceed backupCount. | [L249](../../../../../jiuwenswarm/common/utils.py#L249) |

### [`class _ComponentNameFilter(logging.Filter)`](../../../../../jiuwenswarm/common/utils.py#L302)

仅放行指定组件（由 logger 名判定）的日志记录。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, component: str) -> None` | 源码未提供方法级文档字符串。 | [L305](../../../../../jiuwenswarm/common/utils.py#L305) |
| `def filter(self, record: logging.LogRecord) -> bool` | 源码未提供方法级文档字符串。 | [L309](../../../../../jiuwenswarm/common/utils.py#L309) |

### [`class _CompositeFilter(logging.Filter)`](../../../../../jiuwenswarm/common/utils.py#L313)

组合多个过滤器，任一通过即放行

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, filters: list[logging.Filter]) -> None` | 源码未提供方法级文档字符串。 | [L316](../../../../../jiuwenswarm/common/utils.py#L316) |
| `def filter(self, record: logging.LogRecord) -> bool` | 源码未提供方法级文档字符串。 | [L320](../../../../../jiuwenswarm/common/utils.py#L320) |

### [`class SensitiveDataFilter(logging.Filter)`](../../../../../jiuwenswarm/common/utils.py#L2466)

Mask sensitive data in log messages, identity prefix, and tracebacks.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def filter(self, record: logging.LogRecord) -> bool` | 源码未提供方法级文档字符串。 | [L2469](../../../../../jiuwenswarm/common/utils.py#L2469) |

### [`class JsonOnlyFormatter(logging.Formatter)`](../../../../../jiuwenswarm/common/utils.py#L2534)

只输出message内容，不添加任何前缀（时间戳、级别、logger名）

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def format(self, record: logging.LogRecord) -> str` | 源码未提供方法级文档字符串。 | [L2537](../../../../../jiuwenswarm/common/utils.py#L2537) |

### [`class JsonUserVisibleFormatter(jsonlogger.JsonFormatter if jsonlogger else logging.Formatter)`](../../../../../jiuwenswarm/common/utils.py#L2706)

JSON 格式化日志输出。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_FIELD_RENAME_MAP` | `未显式标注` | `{'asctime': 'timestamp', 'levelname': 'level', 'name': 'logger'}` | [L2715](../../../../../jiuwenswarm/common/utils.py#L2715) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, timestamp_format: str = 'text', include_component: bool = True, sanitize_sensitive_data: bool = True, exc_info_style: str = 'simple', *args, **kwargs)` | 源码未提供方法级文档字符串。 | [L2717](../../../../../jiuwenswarm/common/utils.py#L2717) |
| `def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None` | 源码未提供方法级文档字符串。 | [L2733](../../../../../jiuwenswarm/common/utils.py#L2733) |

### [`class LoggingTagConfig`](../../../../../jiuwenswarm/common/utils.py#L2787)

用户可见性 Tag 配置。env > config.yaml > default(True)。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `user_visible` | `bool` | `True` | [L2790](../../../../../jiuwenswarm/common/utils.py#L2790) |
| `user_progress_visible` | `bool` | `True` | [L2791](../../../../../jiuwenswarm/common/utils.py#L2791) |
| `_env_prefix` | `str` | `'JIUWENSWARM_LOG_'` | [L2792](../../../../../jiuwenswarm/common/utils.py#L2792) |
| `_skip_env_load` | `bool` | `False` | [L2793](../../../../../jiuwenswarm/common/utils.py#L2793) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, skip_env_load: bool = False)` | 源码未提供方法级文档字符串。 | [L2795](../../../../../jiuwenswarm/common/utils.py#L2795) |
| `def _load_config(self) -> None` | 源码未提供方法级文档字符串。 | [L2799](../../../../../jiuwenswarm/common/utils.py#L2799) |
| `def _load_from_env(self, key: str, default: bool) -> bool` | 源码未提供方法级文档字符串。 | [L2813](../../../../../jiuwenswarm/common/utils.py#L2813) |
| `@staticmethod def _load_from_yaml(key: str, default: bool) -> bool` | 源码未提供方法级文档字符串。 | [L2826](../../../../../jiuwenswarm/common/utils.py#L2826) |
| `def is_user_visible_enabled(self) -> bool` | 源码未提供方法级文档字符串。 | [L2843](../../../../../jiuwenswarm/common/utils.py#L2843) |
| `def is_user_progress_visible_enabled(self) -> bool` | 源码未提供方法级文档字符串。 | [L2846](../../../../../jiuwenswarm/common/utils.py#L2846) |

### [`class UserVisibleTagFilter(logging.Filter)`](../../../../../jiuwenswarm/common/utils.py#L2850)

按 record.user_visible 设 record.user_tag（[USER]/[USER_PROGRESS]/""）。从不丢日志，幂等。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, tag_config: Optional[LoggingTagConfig] = None)` | 源码未提供方法级文档字符串。 | [L2853](../../../../../jiuwenswarm/common/utils.py#L2853) |
| `def filter(self, record: logging.LogRecord) -> bool` | 源码未提供方法级文档字符串。 | [L2857](../../../../../jiuwenswarm/common/utils.py#L2857) |

### [`class IdentityFieldFilter(logging.Filter)`](../../../../../jiuwenswarm/common/utils.py#L2868)

从 IdentityStore 读身份，写入字段并预先拼好 ``record.identity``。始终放行。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def filter(self, record: logging.LogRecord) -> bool` | 源码未提供方法级文档字符串。 | [L2878](../../../../../jiuwenswarm/common/utils.py#L2878) |

### [`class IdentityTextFormatter(logging.Formatter)`](../../../../../jiuwenswarm/common/utils.py#L2896)

文本 Formatter：使用 Filter 阶段已写好的 ``record.identity`` 排版。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def format(self, record: logging.LogRecord) -> str` | 源码未提供方法级文档字符串。 | [L2903](../../../../../jiuwenswarm/common/utils.py#L2903) |

### [`class AsyncLRUCache`](../../../../../jiuwenswarm/common/utils.py#L3541)

带可选过期时间与容量上限的 LRU 缓存（异步并发安全）.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, max_size: int \| None = None, ttl_seconds: int \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L3547](../../../../../jiuwenswarm/common/utils.py#L3547) |
| `def _is_expired(self, timestamp: float) -> bool` | 源码未提供方法级文档字符串。 | [L3557](../../../../../jiuwenswarm/common/utils.py#L3557) |
| `async def get(self, key: Hashable) -> Any \| None` | 获取缓存值，如果不存在或已过期则返回 None. | [L3562](../../../../../jiuwenswarm/common/utils.py#L3562) |
| `async def put(self, key: Hashable, value: Any) -> None` | 存入缓存值，如果超过容量则淘汰最久未使用的. | [L3581](../../../../../jiuwenswarm/common/utils.py#L3581) |
| `async def touch_if_same(self, key: Hashable, value: Any) -> bool` | 若 key 存在且缓存值与 value 为同一对象，则刷新访问时间. | [L3592](../../../../../jiuwenswarm/common/utils.py#L3592) |
| `async def remove(self, key: Hashable) -> None` | 删除缓存项. | [L3610](../../../../../jiuwenswarm/common/utils.py#L3610) |
| `async def clear(self) -> None` | 清空缓存. | [L3615](../../../../../jiuwenswarm/common/utils.py#L3615) |
| `def __len__(self) -> int` | 源码未提供方法级文档字符串。 | [L3620](../../../../../jiuwenswarm/common/utils.py#L3620) |
| `def snapshot_values_nowait(self) -> list[Any]` | Return cached values for sync callers (best-effort, no async lock). | [L3623](../../../../../jiuwenswarm/common/utils.py#L3623) |
| `async def keys(self) -> list[Hashable]` | 源码未提供方法级文档字符串。 | [L3635](../../../../../jiuwenswarm/common/utils.py#L3635) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _parse_log_level(name: str, default: int = logging.INFO) -> int` | Parse level name to logging module constant. | [L282](../../../../../jiuwenswarm/common/utils.py#L282) |
| `def _log_component_from_logger_name(name: str) -> str` | 按 ``logging.getLogger(__name__)`` 的 logger 名划分 gateway / channel / agent_server / permissions（含 security）。 | [L289](../../../../../jiuwenswarm/common/utils.py#L289) |
| `def merge_template_with_override(template: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]` | 模板默认值 + 用户 override；用户键覆盖模板。 | [L328](../../../../../jiuwenswarm/common/utils.py#L328) |
| `def _deep_merge(template: dict[str, Any], override: dict[str, Any], depth: int = 0) -> dict[str, Any]` | Recursively merge template with user override, cleaning deprecated fields. | [L341](../../../../../jiuwenswarm/common/utils.py#L341) |
| `def fill_template_defaults(target: dict[str, Any], template: dict[str, Any], depth: int = 0) -> dict[str, Any]` | 模板补缺型合并：以 target 为主体，模板仅补全 target 缺失的键。 | [L370](../../../../../jiuwenswarm/common/utils.py#L370) |
| `def load_yaml_dict(path: Path) -> dict[str, Any]` | 用 yaml.safe_load 读取 YAML 文件为 dict；不存在或无效时返回空 dict。 | [L395](../../../../../jiuwenswarm/common/utils.py#L395) |
| `def resolve_shipped_template_config_path() -> Path` | 包内 shipped 模板：jiuwenswarm/resources/config.yaml。 | [L404](../../../../../jiuwenswarm/common/utils.py#L404) |
| `def _read_template_version_value(template_path: Path) -> Any` | 读取模板 config.yaml 顶层的 ``version``（缺省 ``1.0``）。 | [L409](../../../../../jiuwenswarm/common/utils.py#L409) |
| `def _write_initial_user_override_config(template_src: Path, dest: Path) -> None` | 首次初始化用户目录时写入稀疏 override（仅 ``version``，取自模板）。 | [L425](../../../../../jiuwenswarm/common/utils.py#L425) |
| `def migrate_legacy_user_config_if_needed() -> None` | 无 ``version`` 的旧版完整 config 迁移为稀疏 override：仅保留 permissions + version。 | [L440](../../../../../jiuwenswarm/common/utils.py#L440) |
| `def _load_logging_config_from_yaml() -> dict[str, Any]` | 读取合并后的 logging 段（包内模板 + 用户 config.yaml override）。 | [L483](../../../../../jiuwenswarm/common/utils.py#L483) |
| `def _resolve_logging_levels(log_level_override: Optional[str]) -> LoggingLevels` | 返回日志级别配置。 | [L502](../../../../../jiuwenswarm/common/utils.py#L502) |
| `def get_user_home() -> Path` | Get the current user home directory. | [L539](../../../../../jiuwenswarm/common/utils.py#L539) |
| `def set_user_home(path: Path, initialized: bool = False) -> None` | Set a custom user home directory. | [L558](../../../../../jiuwenswarm/common/utils.py#L558) |
| `def get_user_workspace_dir() -> Path` | Get the user workspace directory path (~/.jiuwenswarm or custom path). | [L577](../../../../../jiuwenswarm/common/utils.py#L577) |
| `def _detect_installation_mode() -> bool` | Detect if running from a package installation (whl) or PyInstaller bundle. | [L614](../../../../../jiuwenswarm/common/utils.py#L614) |
| `def _find_source_root() -> Path` | Find the repository root in development mode (contains jiuwenswarm/ package). | [L639](../../../../../jiuwenswarm/common/utils.py#L639) |
| `def _find_package_root() -> Path \| None` | Best-effort detection of the jiuwenswarm package root. | [L652](../../../../../jiuwenswarm/common/utils.py#L652) |
| `def _resolve_preferred_language(config_yaml_dest: Path, explicit: Optional[str]) -> str` | 确定初始化使用的语言：显式参数优先，否则读 override + 模板，默认 zh。 | [L667](../../../../../jiuwenswarm/common/utils.py#L667) |
| `def _is_interactive() -> bool` | Check if stdin is connected to a terminal (interactive mode). | [L689](../../../../../jiuwenswarm/common/utils.py#L689) |
| `def prompt_preferred_language() -> Optional[Literal['zh', 'en']]` | 交互询问语言偏好。仅接受明确选项；空输入、不在列表或取消用语 → 返回 None（调用方应终止 init）。 非交互环境（stdin非TTY）默认返回 'zh'。 | [L697](../../../../../jiuwenswarm/common/utils.py#L697) |
| `def _get_builtin_skill_names() -> set[str]` | Get the set of built-in skill names from package resources. | [L730](../../../../../jiuwenswarm/common/utils.py#L730) |
| `def get_builtin_skill_names() -> set[str]` | Return official package builtin skill directory names. | [L735](../../../../../jiuwenswarm/common/utils.py#L735) |
| `def is_builtin_skill(skill_name: str) -> bool` | Whether *skill_name* is an official package builtin skill. | [L743](../../../../../jiuwenswarm/common/utils.py#L743) |
| `def _update_skills_state_for_builtin(user_skills_dir: Path, skill_names: list[str]) -> None` | 更新 skills_state.json，记录默认安装的内置技能. | [L751](../../../../../jiuwenswarm/common/utils.py#L751) |
| `def _install_default_builtin_skills(builtin_dir: Path, user_skills_dir: Path, overwrite: bool, cumulative_diff: CopyDiffResult) -> None` | 安装默认的内置技能到用户技能目录. | [L811](../../../../../jiuwenswarm/common/utils.py#L811) |
| `def _migrate_from_jiuwenclaw_root() -> bool` | Migrate from legacy ~/.jiuwenclaw/ to ~/.jiuwenswarm/. | [L875](../../../../../jiuwenswarm/common/utils.py#L875) |
| `def _migrate_jiuwenclaw_workspace_to_workspace(workspace_dir: Path) -> None` | Migrate from legacy jiuwenclaw_workspace directory name to workspace. | [L907](../../../../../jiuwenswarm/common/utils.py#L907) |
| `def _migrate_legacy_workspace(workspace_dir: Path, preferred_language: Optional[str] = None) -> None` | Migrate from legacy layout to new DeepAgent workspace layout. | [L945](../../../../../jiuwenswarm/common/utils.py#L945) |
| `def cleanup_team_files(workspace_dir: Path) -> None` | 清理 Team 旧版本遗留的文件和目录. | [L1117](../../../../../jiuwenswarm/common/utils.py#L1117) |
| `def update_config() -> None` | 稀疏 override 模式：迁移旧版全量 config（无 version 字段）并清理 override 中模板已删除的字段。 | [L1176](../../../../../jiuwenswarm/common/utils.py#L1176) |
| `def prepare_workspace(overwrite: bool = True, preferred_language: Optional[str] = None, workspace_dir: Optional[Path] = None) -> CopyDiffResult` | 源码未提供函数级文档字符串。 | [L1210](../../../../../jiuwenswarm/common/utils.py#L1210) |
| `def _close_log_handlers() -> None` | Close all jiuwenswarm log handlers to release file locks. | [L1462](../../../../../jiuwenswarm/common/utils.py#L1462) |
| `def _print_diff_summary(diff_result: CopyDiffResult, overwrite: bool) -> None` | 打印文件变更统计摘要。 | [L1477](../../../../../jiuwenswarm/common/utils.py#L1477) |
| `def init_user_workspace(overwrite: bool = True, workspace_dir: Optional[Path] = None) -> Path \| Literal['cancelled']` | Initialize ~/.jiuwenswarm from package or source resources. | [L1509](../../../../../jiuwenswarm/common/utils.py#L1509) |
| `def _resolve_paths(force = False) -> None` | Resolve and cache all paths. | [L1595](../../../../../jiuwenswarm/common/utils.py#L1595) |
| `def get_config_dir() -> Path` | Get the config directory path. | [L1638](../../../../../jiuwenswarm/common/utils.py#L1638) |
| `def get_runtime_state_path(session_id: str \| None = None) -> Path` | Per-session runtime_state.yaml path under config dir. | [L1644](../../../../../jiuwenswarm/common/utils.py#L1644) |
| `def get_workspace_dir() -> Path` | Get the workspace directory path. | [L1654](../../../../../jiuwenswarm/common/utils.py#L1654) |
| `def get_root_dir() -> Path` | Get the root directory path. | [L1660](../../../../../jiuwenswarm/common/utils.py#L1660) |
| `def get_agent_workspace_dir() -> Path` | Get the agent workspace directory path. | [L1666](../../../../../jiuwenswarm/common/utils.py#L1666) |
| `def get_default_project_workspace_dir() -> Path` | Get the fallback task workspace used when no project is bound. | [L1688](../../../../../jiuwenswarm/common/utils.py#L1688) |
| `def get_default_project_session_workspace_dir(session_id: str \| None = None) -> Path` | Get the no-project task workspace for a single conversation session. | [L1698](../../../../../jiuwenswarm/common/utils.py#L1698) |
| `def get_prompt_attachment_dir() -> Path` | Get the jiuwenswarm prompt attachment directory path. | [L1722](../../../../../jiuwenswarm/common/utils.py#L1722) |
| `def get_service_root_dir(service_id: str = 'default') -> Path` | Get the service-level directory path. | [L1728](../../../../../jiuwenswarm/common/utils.py#L1728) |
| `def get_agent_root_dir() -> Path` | Get the agent root directory path (multi-tenant default). | [L1738](../../../../../jiuwenswarm/common/utils.py#L1738) |
| `def get_agent_root_relative_dir() -> Path` | Get the agent root relative path under a tenant workspace root. | [L1755](../../../../../jiuwenswarm/common/utils.py#L1755) |
| `def get_agent_workspace_relative_dir() -> Path` | Get the agent workspace relative path under a tenant workspace root. | [L1760](../../../../../jiuwenswarm/common/utils.py#L1760) |
| `def collapse_nested_agent_workspace_dir(path: Path \| str) -> Path` | Collapse ``.../workspace/workspace`` back to the agent workspace. | [L1768](../../../../../jiuwenswarm/common/utils.py#L1768) |
| `def get_agent_sessions_relative_dir() -> Path` | Get the agent sessions relative path under a tenant workspace root. | [L1787](../../../../../jiuwenswarm/common/utils.py#L1787) |
| `def _normalize_tenant_id(value: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L1792](../../../../../jiuwenswarm/common/utils.py#L1792) |
| `def _require_workspace_key(workspace_key: str \| None) -> str` | Normalize and require non-empty ``workspace_key`` for ``workspace_{key}/`` paths. | [L1796](../../../../../jiuwenswarm/common/utils.py#L1796) |
| `def _require_tenant_ids(service_id: str \| None, agent_id: str \| None) -> tuple[str, str]` | Require non-empty ``service_id`` and ``agent_id`` (env/routing，非磁盘根). | [L1806](../../../../../jiuwenswarm/common/utils.py#L1806) |
| `def _effective_workspace_key(workspace_key: str \| None = None) -> str` | Explicit ``workspace_key`` > bound key > ``default``. | [L1817](../../../../../jiuwenswarm/common/utils.py#L1817) |
| `def get_multi_tenant_user_workspace_dir(workspace_key: str) -> Path` | Get multi-tenant user workspace directory path. | [L1832](../../../../../jiuwenswarm/common/utils.py#L1832) |
| `def get_tenant_agent_workspace_dir(workspace_key: str \| None = None) -> Path` | 多租户 DeepAgent 工作区：``workspace_{key}/agent/workspace``. | [L1843](../../../../../jiuwenswarm/common/utils.py#L1843) |
| `def get_tenant_agent_skills_dirs(workspace_key: str \| None = None) -> list[Path]` | 多租户 skills 目录（与 ``JiuWenSwarm`` / ``SkillManager`` 落盘路径一致）. | [L1853](../../../../../jiuwenswarm/common/utils.py#L1853) |
| `def get_multi_tenant_skill_dirs(workspace_key: str \| None = None) -> list[Path]` | Resolve the skills directory list for multi-tenant / single-tenant mode. | [L1858](../../../../../jiuwenswarm/common/utils.py#L1858) |
| `def get_agent_home_dir() -> Path` | 源码未提供函数级文档字符串。 | [L1869](../../../../../jiuwenswarm/common/utils.py#L1869) |
| `def get_agent_memory_dir() -> Path` | Get the agent memory directory path. | [L1873](../../../../../jiuwenswarm/common/utils.py#L1873) |
| `def get_agent_skills_dir() -> Path` | Get the agent skills directory path. | [L1884](../../../../../jiuwenswarm/common/utils.py#L1884) |
| `def parse_shared_skills_dirs_raw(raw: str) -> list[Path]` | Parse SHARED_SKILLS_DIRS value into deduplicated absolute paths. | [L1900](../../../../../jiuwenswarm/common/utils.py#L1900) |
| `def get_shared_agent_skills_dirs() -> list[Path]` | Read shared skill roots from tip/env (OfficeClaw ``office-claw-skills`` etc.). | [L1918](../../../../../jiuwenswarm/common/utils.py#L1918) |
| `def merge_shared_skills_trusted_dirs(trusted_dirs: list[str] \| None = None) -> list[str]` | Union CLI ``trusted_dirs`` with shared skill roots for file_guard allow. | [L1932](../../../../../jiuwenswarm/common/utils.py#L1932) |
| `def resolve_agent_registered_skill_dirs() -> list[Path]` | Resolve skill dirs: request-bound override, shared tip dirs, else workspace. | [L1972](../../../../../jiuwenswarm/common/utils.py#L1972) |
| `def get_interactions_dir() -> Path` | Get the interactions directory for pending interaction contexts. | [L1990](../../../../../jiuwenswarm/common/utils.py#L1990) |
| `def get_cron_jobs_path() -> Path` | Legacy global cron_jobs.json (pre-tenant Gateway). Prefer per-tenant helpers. | [L1999](../../../../../jiuwenswarm/common/utils.py#L1999) |
| `def resolve_gateway_cron_jobs_path_template() -> str` | Absolute path template for Gateway cron PersistentStore / CronJobStore. | [L2004](../../../../../jiuwenswarm/common/utils.py#L2004) |
| `def resolve_gateway_cron_jobs_path(service_id: str \| None = None, agent_id: str \| None = None) -> Path` | Gateway per-tenant cron store: ``gateway/cron/service_{sid}/agent_{aid}/cron_jobs.json``. | [L2018](../../../../../jiuwenswarm/common/utils.py#L2018) |
| `def resolve_tenant_agent_root_dir(workspace_key: str \| None = None) -> Path` | Resolve ``workspace_{key}/agent``. | [L2033](../../../../../jiuwenswarm/common/utils.py#L2033) |
| `def resolve_tenant_agent_workspace_dir(workspace_key: str \| None = None) -> Path` | Resolve ``workspace_{key}/agent/workspace``. | [L2051](../../../../../jiuwenswarm/common/utils.py#L2051) |
| `def resolve_tenant_sessions_dir(workspace_key: str \| None = None) -> Path` | Resolve ``workspace_{key}/agent/sessions`` for a tenant workspace key. | [L2056](../../../../../jiuwenswarm/common/utils.py#L2056) |
| `def resolve_cron_tenant_scope(*, service_id: str \| None = None, agent_id: str \| None = None, metadata: dict \| None = None, params: dict \| None = None, log_prefix: str = '[Cron]') -> tuple[str, str]` | Resolve cron tenant ids; missing values fall back to default/default. | [L2061](../../../../../jiuwenswarm/common/utils.py#L2061) |
| `def get_deepagent_todo_dir() -> Path` | Get the DeepAgent todo directory path. | [L2092](../../../../../jiuwenswarm/common/utils.py#L2092) |
| `def get_deepagent_messages_dir() -> Path` | Get the DeepAgent messages directory path. | [L2101](../../../../../jiuwenswarm/common/utils.py#L2101) |
| `def get_deepagent_agents_dir() -> Path` | Get the DeepAgent agents (sub-agent) directory path. | [L2110](../../../../../jiuwenswarm/common/utils.py#L2110) |
| `def get_deepagent_heartbeat_path() -> Path` | Get the DeepAgent HEARTBEAT.md file path. | [L2119](../../../../../jiuwenswarm/common/utils.py#L2119) |
| `def get_deepagent_agent_md_path() -> Path` | Get the DeepAgent AGENT.md file path. | [L2128](../../../../../jiuwenswarm/common/utils.py#L2128) |
| `def get_deepagent_soul_md_path() -> Path` | Get the DeepAgent SOUL.md file path. | [L2137](../../../../../jiuwenswarm/common/utils.py#L2137) |
| `def get_deepagent_identity_md_path() -> Path` | Get the DeepAgent IDENTITY.md file path. | [L2146](../../../../../jiuwenswarm/common/utils.py#L2146) |
| `def get_deepagent_user_md_path() -> Path` | Get the DeepAgent USER.md file path. | [L2155](../../../../../jiuwenswarm/common/utils.py#L2155) |
| `def get_builtin_skills_dir() -> Path` | Get the built-in skills directory from package resources. | [L2164](../../../../../jiuwenswarm/common/utils.py#L2164) |
| `def get_agent_sessions_dir() -> Path` | Get sessions directory (bound tenant or ``service_default/agent_default``). | [L2176](../../../../../jiuwenswarm/common/utils.py#L2176) |
| `def get_agent_evolution_trajectories_dir(workspace_key: str \| None = None) -> Path` | Get the evolution execution trajectories directory. | [L2184](../../../../../jiuwenswarm/common/utils.py#L2184) |
| `def resolve_git_branch(project_dir: str \| None) -> str` | 返回 ``project_dir`` 当前 git 分支，取不到时返回哨兵 ``"HEAD"``。 | [L2200](../../../../../jiuwenswarm/common/utils.py#L2200) |
| `def get_checkpoint_dir() -> Path` | Get the default checkpoint directory. | [L2233](../../../../../jiuwenswarm/common/utils.py#L2233) |
| `def _resolve_logs_service_id(service_id: str \| None = None) -> str` | Resolve service_id for logs: explicit > bound env_ns > default. | [L2243](../../../../../jiuwenswarm/common/utils.py#L2243) |
| `def get_logs_dir(service_id: str \| None = None) -> Path` | Get the logs directory path (service-level). | [L2258](../../../../../jiuwenswarm/common/utils.py#L2258) |
| `def get_xy_tmp_dir() -> Path` | 源码未提供函数级文档字符串。 | [L2273](../../../../../jiuwenswarm/common/utils.py#L2273) |
| `def get_env_file() -> Path` | 源码未提供函数级文档字符串。 | [L2280](../../../../../jiuwenswarm/common/utils.py#L2280) |
| `def reset_free_search_runtime_flags() -> None` | Start each process with free-search engines disabled unless reopened via config UI. | [L2284](../../../../../jiuwenswarm/common/utils.py#L2284) |
| `def get_config_file() -> Path` | Get the config.yaml file path. | [L2290](../../../../../jiuwenswarm/common/utils.py#L2290) |
| `def is_package_installation() -> bool` | Check if running from package installation. | [L2295](../../../../../jiuwenswarm/common/utils.py#L2295) |
| `def _fingerprint(value: str) -> str` | 返回 value 的 SHA256 前 4 字节（8 位 hex）指纹，用于脱敏后的关联。 | [L2358](../../../../../jiuwenswarm/common/utils.py#L2358) |
| `def _is_already_masked(value: Any) -> bool` | 判断 value 是否已是脱敏产物（纯掩码或带指纹），避免重复脱敏。 | [L2380](../../../../../jiuwenswarm/common/utils.py#L2380) |
| `def _masked_with_fp(value: Any) -> str` | 脱敏并附指纹：``******(fp:xxxxxxxx)``。value 为空或失败时退化为纯掩码。 | [L2389](../../../../../jiuwenswarm/common/utils.py#L2389) |
| `def _sanitize_log_text(text: str) -> str` | 源码未提供函数级文档字符串。 | [L2407](../../../../../jiuwenswarm/common/utils.py#L2407) |
| `def mask_sensitive(text: Any) -> str` | 对任意文本做敏感信息脱敏，返回脱敏后的字符串。 | [L2454](../../../../../jiuwenswarm/common/utils.py#L2454) |
| `def build_log_identity(record: logging.LogRecord) -> str` | 从 record.user_id/domain_id/app_id 拼文本 identity 片段（null 输出 ``null``）。 | [L2525](../../../../../jiuwenswarm/common/utils.py#L2525) |
| `def install_source_record_masking() -> None` | 在 LogRecord 创建层（``logging.setLogRecordFactory``）安装源头脱敏。 | [L2547](../../../../../jiuwenswarm/common/utils.py#L2547) |
| `def _resolve_logging_format() -> str` | 解析日志格式（text/json/dual）。优先级 env > config.yaml > default(text)。 | [L2610](../../../../../jiuwenswarm/common/utils.py#L2610) |
| `def _resolve_output_switches() -> dict[str, bool]` | 解析 console_enabled/file_enabled。优先级 env > config.yaml > default(True)。 | [L2634](../../../../../jiuwenswarm/common/utils.py#L2634) |
| `def _validate_json_config(config: dict) -> dict` | 验证 logging.json.* 字段。 | [L2671](../../../../../jiuwenswarm/common/utils.py#L2671) |
| `def _resolve_json_config() -> dict[str, Any]` | 解析 logging.json 子段（带默认值 + 验证）。 | [L2687](../../../../../jiuwenswarm/common/utils.py#L2687) |
| `def _iter_log_output_handlers() -> list[logging.Handler]` | Return handlers that actually write logs (listener targets when queued). | [L2915](../../../../../jiuwenswarm/common/utils.py#L2915) |
| `def flush_queued_logs() -> None` | Block until queued log records are written (tests / graceful drain). | [L2922](../../../../../jiuwenswarm/common/utils.py#L2922) |
| `def setup_logger(log_level: Optional[str] = None) -> logging.Logger` | 配置 ``jiuwenswarm`` 根日志：控制台 + 分组件文件 + 汇总 full.log。 | [L2953](../../../../../jiuwenswarm/common/utils.py#L2953) |
| `def wait_for_tcp_port(host: str, port: int, *, timeout: float = 15.0, max_attempts: int \| None = None, initial_delay: float = 0.1, max_delay: float = 2.0, connect_timeout: float = 1.0, target_state: str = 'connected') -> bool` | Wait for a TCP port to reach the desired state with exponential backoff. | [L3107](../../../../../jiuwenswarm/common/utils.py#L3107) |
| `def wait_for_pid_exit(pid: int, timeout: float = 60.0) -> None` | Wait for a process to exit, with a timeout and warning on failure. | [L3160](../../../../../jiuwenswarm/common/utils.py#L3160) |
| `def update_log_levels(log_level: Optional[str] = None, *, console_level: Optional[str] = None, gateway: Optional[str] = None, channel: Optional[str] = None, agent_server: Optional[str] = None, full: Optional[str] = None) -> logging.Logger` | 运行时动态更新 ``jiuwenswarm`` 根日志及各 handler 的级别，无需重建 handler。 | [L3208](../../../../../jiuwenswarm/common/utils.py#L3208) |
| `def _logging_config_row_to_dict(obj: dict[str, Any] \| Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L3253](../../../../../jiuwenswarm/common/utils.py#L3253) |
| `def apply_logging_config_payload(payload: dict[str, Any] \| None) -> None` | 将 DB 行 / WS payload 转为 :func:`update_log_levels` 调用。 | [L3273](../../../../../jiuwenswarm/common/utils.py#L3273) |
| `async def reload_logging_levels() -> None` | 从权威存储加载 logging 配置并刷新**本进程**日志级别。 | [L3288](../../../../../jiuwenswarm/common/utils.py#L3288) |
| `def _truncate_tool_args_log_fragment(text: str, *, full_detail: bool) -> str` | 源码未提供函数级文档字符串。 | [L3400](../../../../../jiuwenswarm/common/utils.py#L3400) |
| `def _log_tool_args_repair_stage(*, stage: str, before_raw: str, outcome: Literal['success', 'failed'], after_dict: Optional[dict] = None, error: Optional[str] = None) -> None` | 源码未提供函数级文档字符串。 | [L3406](../../../../../jiuwenswarm/common/utils.py#L3406) |
| `def _fix_missing_quotes(json_str: str) -> str` | 源码未提供函数级文档字符串。 | [L3439](../../../../../jiuwenswarm/common/utils.py#L3439) |
| `def fix_json_arguments(arguments: str \| dict) -> str \| dict` | 源码未提供函数级文档字符串。 | [L3463](../../../../../jiuwenswarm/common/utils.py#L3463) |
| `def normalize_tenant_scope_id(value: str \| None, *, default: str = 'default') -> str` | Normalize and validate a tenant scope ID (service_id / agent_id). | [L3648](../../../../../jiuwenswarm/common/utils.py#L3648) |

## `jiuwenswarm/common/version.py`

[打开源码](../../../../../jiuwenswarm/common/version.py#L1)

**模块职责：** 源码未提供模块级文档字符串。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__version__` | `未显式标注` | [L3](../../../../../jiuwenswarm/common/version.py#L3) |

## `jiuwenswarm/common/version_source.py`

[打开源码](../../../../../jiuwenswarm/common/version_source.py#L1)

**模块职责：** 定义 ReleaseAsset、ReleaseInfo、VersionSource、GitHubReleasesSource、GitCodeReleasesSource、PyPIVersionSource 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/common/version_source.py#L17) |
| `DEFAULT_TIMEOUT_SECONDS` | `未显式标注` | [L19](../../../../../jiuwenswarm/common/version_source.py#L19) |
| `GITHUB_API` | `未显式标注` | [L20](../../../../../jiuwenswarm/common/version_source.py#L20) |
| `GITCODE_API` | `未显式标注` | [L21](../../../../../jiuwenswarm/common/version_source.py#L21) |
| `PYPI_SIMPLE_API` | `未显式标注` | [L22](../../../../../jiuwenswarm/common/version_source.py#L22) |
| `_PRERELEASE_PATTERN` | `未显式标注` | [L25](../../../../../jiuwenswarm/common/version_source.py#L25) |
| `_PRERELEASE_TYPE_ORDER` | `dict[str, int]` | [L103](../../../../../jiuwenswarm/common/version_source.py#L103) |

### [`class ReleaseAsset`](../../../../../jiuwenswarm/common/version_source.py#L153)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L154](../../../../../jiuwenswarm/common/version_source.py#L154) |
| `download_url` | `str` | `—` | [L155](../../../../../jiuwenswarm/common/version_source.py#L155) |
| `size` | `int` | `0` | [L156](../../../../../jiuwenswarm/common/version_source.py#L156) |

### [`class ReleaseInfo`](../../../../../jiuwenswarm/common/version_source.py#L160)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `version` | `str` | `—` | [L161](../../../../../jiuwenswarm/common/version_source.py#L161) |
| `release_notes` | `str` | `''` | [L162](../../../../../jiuwenswarm/common/version_source.py#L162) |
| `published_at` | `str` | `''` | [L163](../../../../../jiuwenswarm/common/version_source.py#L163) |
| `assets` | `list[ReleaseAsset]` | `field(default_factory=list)` | [L164](../../../../../jiuwenswarm/common/version_source.py#L164) |
| `source_type` | `str` | `''` | [L165](../../../../../jiuwenswarm/common/version_source.py#L165) |
| `prerelease` | `bool` | `False` | [L166](../../../../../jiuwenswarm/common/version_source.py#L166) |

### [`class VersionSource(ABC)`](../../../../../jiuwenswarm/common/version_source.py#L169)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, name: str = '', timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None` | 源码未提供方法级文档字符串。 | [L170](../../../../../jiuwenswarm/common/version_source.py#L170) |
| `@abstractmethod def fetch_latest(self) -> ReleaseInfo` | 源码未提供方法级文档字符串。 | [L175](../../../../../jiuwenswarm/common/version_source.py#L175) |
| `def fetch_assets(self) -> list[ReleaseAsset]` | 源码未提供方法级文档字符串。 | [L178](../../../../../jiuwenswarm/common/version_source.py#L178) |
| `@staticmethod def _clean_version(raw: str) -> str` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/common/version_source.py#L182) |
| `@classmethod def _best_version_from_texts(cls, values: list[str]) -> str` | 源码未提供方法级文档字符串。 | [L195](../../../../../jiuwenswarm/common/version_source.py#L195) |
| `@classmethod def _best_version_from_release_data(cls, data: dict, assets_raw: list) -> str` | Resolve a release version from tags, names, and asset filenames. | [L203](../../../../../jiuwenswarm/common/version_source.py#L203) |
| `def _fetch_newest_from_list(self, list_url: str, headers: dict[str, str]) -> ReleaseInfo \| None` | Fetch the releases list and return the newest entry (incl. pre-releases). | [L223](../../../../../jiuwenswarm/common/version_source.py#L223) |
| `def _parse_release(self, data: dict) -> ReleaseInfo \| None` | Parse a single release dict. Override in subclasses. | [L257](../../../../../jiuwenswarm/common/version_source.py#L257) |
| `def _fetch_json(self, url: str, headers: dict[str, str] \| None = None) -> Any` | 源码未提供方法级文档字符串。 | [L261](../../../../../jiuwenswarm/common/version_source.py#L261) |
| `def _fetch_text(self, url: str, headers: dict[str, str] \| None = None) -> str` | 源码未提供方法级文档字符串。 | [L264](../../../../../jiuwenswarm/common/version_source.py#L264) |

### [`class GitHubReleasesSource(VersionSource)`](../../../../../jiuwenswarm/common/version_source.py#L281)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, owner: str, repo: str, token: str = '', api_url: str = '', timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None` | 源码未提供方法级文档字符串。 | [L282](../../../../../jiuwenswarm/common/version_source.py#L282) |
| `def fetch_latest(self) -> ReleaseInfo` | 源码未提供方法级文档字符串。 | [L300](../../../../../jiuwenswarm/common/version_source.py#L300) |
| `def _parse_release(self, data: dict) -> ReleaseInfo \| None` | 源码未提供方法级文档字符串。 | [L313](../../../../../jiuwenswarm/common/version_source.py#L313) |
| `def _build_headers(self) -> dict[str, str]` | 源码未提供方法级文档字符串。 | [L339](../../../../../jiuwenswarm/common/version_source.py#L339) |

### [`class GitCodeReleasesSource(VersionSource)`](../../../../../jiuwenswarm/common/version_source.py#L349)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, owner: str, repo: str, access_token: str = '', api_url: str = '', timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None` | 源码未提供方法级文档字符串。 | [L350](../../../../../jiuwenswarm/common/version_source.py#L350) |
| `def fetch_latest(self) -> ReleaseInfo` | 源码未提供方法级文档字符串。 | [L367](../../../../../jiuwenswarm/common/version_source.py#L367) |
| `def _parse_release(self, data: dict) -> ReleaseInfo \| None` | 源码未提供方法级文档字符串。 | [L382](../../../../../jiuwenswarm/common/version_source.py#L382) |
| `def _build_headers(self) -> dict[str, str]` | 源码未提供方法级文档字符串。 | [L414](../../../../../jiuwenswarm/common/version_source.py#L414) |

### [`class PyPIVersionSource(VersionSource)`](../../../../../jiuwenswarm/common/version_source.py#L424)

源码未提供类级文档字符串。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, package: str = 'jiuwenswarm', mirror: str = '', timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None` | 源码未提供方法级文档字符串。 | [L425](../../../../../jiuwenswarm/common/version_source.py#L425) |
| `def fetch_latest(self) -> ReleaseInfo` | 源码未提供方法级文档字符串。 | [L439](../../../../../jiuwenswarm/common/version_source.py#L439) |
| `def _fetch_simple_json(self) -> Any` | 源码未提供方法级文档字符串。 | [L485](../../../../../jiuwenswarm/common/version_source.py#L485) |
| `def _fetch_simple_html(self) -> Any` | 源码未提供方法级文档字符串。 | [L494](../../../../../jiuwenswarm/common/version_source.py#L494) |
| `def _resolve_url(self, url: str) -> str` | 源码未提供方法级文档字符串。 | [L511](../../../../../jiuwenswarm/common/version_source.py#L511) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_prerelease_version(version: str) -> bool` | Return True when *version* looks like a pre-release (alpha / beta / rc / dev). | [L31](../../../../../jiuwenswarm/common/version_source.py#L31) |
| `def strip_prerelease_suffix(version: str) -> str` | Remove the pre-release suffix so that ``0.2.0.beta1`` becomes ``0.2.0``. | [L36](../../../../../jiuwenswarm/common/version_source.py#L36) |
| `def release_sort_key(version: str) -> tuple[tuple[int, ...], int, tuple[int, ...]]` | Total-order key so that newer versions sort higher. | [L53](../../../../../jiuwenswarm/common/version_source.py#L53) |
| `def _detect_prerelease_type(version: str) -> str` | Return the lowercase pre-release type marker found in *version*. | [L85](../../../../../jiuwenswarm/common/version_source.py#L85) |
| `def _is_draft_entry(data: dict) -> bool` | Return True when a release dict is an unpublished draft. | [L113](../../../../../jiuwenswarm/common/version_source.py#L113) |
| `def _is_prerelease_entry(data: dict) -> bool` | Return True when a release dict is a pre-release or draft. | [L118](../../../../../jiuwenswarm/common/version_source.py#L118) |
| `def _unwrap_list(raw: Any) -> list \| None` | Normalise a list-API response that may be wrapped in a dict. | [L127](../../../../../jiuwenswarm/common/version_source.py#L127) |
| `def _with_query_params(url: str, **params: str \| int) -> str` | Return *url* with query parameters added without dropping existing ones. | [L144](../../../../../jiuwenswarm/common/version_source.py#L144) |

## `jiuwenswarm/common/work_mode.py`

[打开源码](../../../../../jiuwenswarm/common/work_mode.py#L1)

**模块职责：** 工作模式（work_mode）共享基础设施。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEFAULT_WEB_WORK_MODE` | `str` | [L14](../../../../../jiuwenswarm/common/work_mode.py#L14) |
| `DEFAULT_TUI_WORK_MODE` | `str` | [L16](../../../../../jiuwenswarm/common/work_mode.py#L16) |
| `SUPPORTED_WORK_MODES` | `frozenset[str]` | [L18](../../../../../jiuwenswarm/common/work_mode.py#L18) |
| `DEFAULT_PROJECT_ID_WORK` | `str` | [L20](../../../../../jiuwenswarm/common/work_mode.py#L20) |
| `DEFAULT_PROJECT_ID_CODE` | `str` | [L21](../../../../../jiuwenswarm/common/work_mode.py#L21) |
| `DEFAULT_PROJECT_IDS` | `frozenset[str]` | [L22](../../../../../jiuwenswarm/common/work_mode.py#L22) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def normalize_work_mode(raw: Any, *, default: str = DEFAULT_WEB_WORK_MODE) -> str` | 宽松规范化 ``work_mode``,非法值回落到 ``default``。 | [L25](../../../../../jiuwenswarm/common/work_mode.py#L25) |
| `def is_default_project_id(project_id: str \| None) -> bool` | 是否为虚拟默认项目 ID(含空串/``None``,归默认项目)。 | [L40](../../../../../jiuwenswarm/common/work_mode.py#L40) |
| `def resolve_default_project_id(work_mode: str) -> str` | 按 ``work_mode`` 返回默认项目 ID:``work``→``default``,``code``→``default_code``。 | [L47](../../../../../jiuwenswarm/common/work_mode.py#L47) |

## `jiuwenswarm/common/ws_diagnostics.py`

[打开源码](../../../../../jiuwenswarm/common/ws_diagnostics.py#L1)

**模块职责：** Helpers for WebSocket diagnostic logging.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_value(value: Any) -> Any` | 源码未提供函数级文档字符串。 | [L12](../../../../../jiuwenswarm/common/ws_diagnostics.py#L12) |
| `def _close_frame_info(frame: Any) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L18](../../../../../jiuwenswarm/common/ws_diagnostics.py#L18) |
| `def describe_ws_exception(exc: BaseException) -> dict[str, Any]` | Return stable, version-tolerant fields for WebSocket-related exceptions. | [L27](../../../../../jiuwenswarm/common/ws_diagnostics.py#L27) |
| `def describe_ws_peer(ws: Any) -> dict[str, Any]` | Return best-effort connection fields without depending on a concrete ws class. | [L55](../../../../../jiuwenswarm/common/ws_diagnostics.py#L55) |
| `def format_ws_diagnostics(*parts: Mapping[str, Any] \| None, **fields: Any) -> str` | Format diagnostic fields as stable ``key=value`` pairs for logs. | [L74](../../../../../jiuwenswarm/common/ws_diagnostics.py#L74) |

## `jiuwenswarm/common/ws_limits.py`

[打开源码](../../../../../jiuwenswarm/common/ws_limits.py#L1)

**模块职责：** Shared WebSocket payload limits for Gateway and AgentServer links.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `AGENT_WS_MAX_MESSAGE_BYTES` | `未显式标注` | [L6](../../../../../jiuwenswarm/common/ws_limits.py#L6) |
| `AGENT_WS_SEND_BUDGET_BYTES` | `未显式标注` | [L7](../../../../../jiuwenswarm/common/ws_limits.py#L7) |
| `WEB_WS_MAX_MESSAGE_BYTES` | `未显式标注` | [L11](../../../../../jiuwenswarm/common/ws_limits.py#L11) |

## `jiuwenswarm/deployment_mode.py`

[打开源码](../../../../../jiuwenswarm/deployment_mode.py#L1)

**模块职责：** Gateway 部署模式集中定义与判断 helper。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `MODE_STANDALONE` | `未显式标注` | [L18](../../../../../jiuwenswarm/deployment_mode.py#L18) |
| `MODE_ACTIVE_STANDBY` | `未显式标注` | [L19](../../../../../jiuwenswarm/deployment_mode.py#L19) |
| `MODE_DISTRIBUTED` | `未显式标注` | [L20](../../../../../jiuwenswarm/deployment_mode.py#L20) |
| `VALID_DEPLOYMENT_MODES` | `tuple[str, ...]` | [L22](../../../../../jiuwenswarm/deployment_mode.py#L22) |
| `SessionStorageBackend` | `未显式标注` | [L28](../../../../../jiuwenswarm/deployment_mode.py#L28) |
| `HistoryStorageBackend` | `未显式标注` | [L29](../../../../../jiuwenswarm/deployment_mode.py#L29) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def normalize_deployment_mode(raw: object) -> str` | 归一化 deployment_mode；非法/空值回退 ``standalone``。 | [L32](../../../../../jiuwenswarm/deployment_mode.py#L32) |
| `def uses_gateway_redis(mode: str) -> bool` | 该模式是否需要连接 Gateway Redis（active-standby / distributed）。 | [L40](../../../../../jiuwenswarm/deployment_mode.py#L40) |
| `def uses_leader_election(mode: str) -> bool` | 仅 active-standby 需要 LeaderElection；distributed 多副本同时处理，不选主。 | [L45](../../../../../jiuwenswarm/deployment_mode.py#L45) |
| `def session_storage_backend(mode: str) -> SessionStorageBackend` | SessionMap 存储后端：standalone 本地文件；其余为内存缓存 + Redis。 | [L50](../../../../../jiuwenswarm/deployment_mode.py#L50) |
| `def history_storage_backend(mode: str) -> HistoryStorageBackend` | Web 会话历史默认存储后端。 | [L57](../../../../../jiuwenswarm/deployment_mode.py#L57) |
| `def default_cron_enabled(mode: str) -> bool` | Cron 默认开关：distributed 默认关闭（多副本无选主，避免重复调度）。 | [L72](../../../../../jiuwenswarm/deployment_mode.py#L72) |
| `def channel_config_overlay_default(mode: str) -> bool` | channel_config DB overlay 是否启用：仅 active-standby（企业/K8s 主备）。 | [L77](../../../../../jiuwenswarm/deployment_mode.py#L77) |
| `def distributed_channel_whitelist() -> frozenset[str]` | distributed 模式允许启动的通道（tui 由 /tui 路由独立注册，不走 channels 段）。 | [L85](../../../../../jiuwenswarm/deployment_mode.py#L85) |

## `jiuwenswarm/dotenv_early.py`

[打开源码](../../../../../jiuwenswarm/dotenv_early.py#L1)

**模块职责：** Early --dotenv/--name parsing for multi-instance isolation.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_early_logger` | `未显式标注` | [L52](../../../../../jiuwenswarm/dotenv_early.py#L52) |
| `DESKTOP_PRESERVED_ENV_KEYS` | `未显式标注` | [L72](../../../../../jiuwenswarm/dotenv_early.py#L72) |
| `CLI_PORTS_ENV_FLAG` | `未显式标注` | [L84](../../../../../jiuwenswarm/dotenv_early.py#L84) |
| `_parsed_dotenv` | `Path \| None` | [L269](../../../../../jiuwenswarm/dotenv_early.py#L269) |
| `_component_name` | `str` | [L270](../../../../../jiuwenswarm/dotenv_early.py#L270) |
| `__all__` | `未显式标注` | [L313](../../../../../jiuwenswarm/dotenv_early.py#L313) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _early_warning(component_name: str, message: str) -> None` | Log early warning message to stderr. | [L58](../../../../../jiuwenswarm/dotenv_early.py#L58) |
| `def _early_error(component_name: str, message: str) -> None` | Log early error message to stderr. | [L63](../../../../../jiuwenswarm/dotenv_early.py#L63) |
| `def _should_preserve_session_ports() -> bool` | True when this process was launched with an explicit session port remap. | [L87](../../../../../jiuwenswarm/dotenv_early.py#L87) |
| `def load_dotenv_runtime(dotenv_path: str \| Path \| None, *, override: bool = True) -> bool` | load_dotenv wrapper that keeps session-injected port env vars. | [L95](../../../../../jiuwenswarm/dotenv_early.py#L95) |
| `def parse_dotenv_early(component_name: str = 'jiuwenswarm') -> Path \| None` | Parse --dotenv/--name arguments and load env before jiuwenswarm imports. | [L131](../../../../../jiuwenswarm/dotenv_early.py#L131) |
| `def _load_bootstrap_by_name_early(name: str, component_name: str) -> Path \| None` | Load bootstrap .env for named instance during early parsing. | [L190](../../../../../jiuwenswarm/dotenv_early.py#L190) |
| `def set_component_name(name: str) -> None` | Set the component name for warning messages. | [L273](../../../../../jiuwenswarm/dotenv_early.py#L273) |
| `def get_parsed_dotenv() -> Path \| None` | Get the path that was parsed, if any. | [L287](../../../../../jiuwenswarm/dotenv_early.py#L287) |
| `def load_instance_bootstrap_by_name(name: str) -> Path \| None` | Load bootstrap .env for a named instance after argparse parsing. | [L292](../../../../../jiuwenswarm/dotenv_early.py#L292) |

## `jiuwenswarm/init_workspace.py`

[打开源码](../../../../../jiuwenswarm/init_workspace.py#L1)

**模块职责：** CLI：将运行时数据初始化到用户数据根目录（与 ``get_user_workspace_dir()`` 一致）。

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def run_init(force: bool = False, name: Optional[str] = None) -> int` | Run workspace initialization. | [L44](../../../../../jiuwenswarm/init_workspace.py#L44) |
| `def main() -> int` | 源码未提供函数级文档字符串。 | [L154](../../../../../jiuwenswarm/init_workspace.py#L154) |

## `jiuwenswarm/instance_manager/__init__.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/__init__.py#L1)

**模块职责：** Instance manager for multi-instance isolation.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L101](../../../../../jiuwenswarm/instance_manager/__init__.py#L101) |

## `jiuwenswarm/instance_manager/bootstrap.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L1)

**模块职责：** Bootstrap .env file creation for instances.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L29](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L29) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def create_bootstrap_env(config: InstanceConfig) -> Path` | Create bootstrap .env file for an instance. | [L32](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L32) |
| `def create_bootstrap_env_for_name(name: str, workspace: Path) -> Path` | Create bootstrap .env file for a named instance (legacy interface). | [L75](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L75) |
| `def _create_basic_bootstrap_env(name: str, workspace: Path, component_name: str) -> None` | Create a basic bootstrap .env file during early parsing. | [L86](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L86) |
| `def load_instance_bootstrap_by_name(name: str) -> Path \| None` | Load bootstrap .env for a named instance after argparse parsing. | [L140](../../../../../jiuwenswarm/instance_manager/bootstrap.py#L140) |

## `jiuwenswarm/instance_manager/config.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/config.py#L1)

**模块职责：** Instance configuration, constants, name validation, and port management.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L23](../../../../../jiuwenswarm/instance_manager/config.py#L23) |
| `INSTANCE_NAME_PATTERN` | `未显式标注` | [L38](../../../../../jiuwenswarm/instance_manager/config.py#L38) |
| `RESERVED_NAMES` | `未显式标注` | [L39](../../../../../jiuwenswarm/instance_manager/config.py#L39) |
| `BASE_PORTS` | `未显式标注` | [L42](../../../../../jiuwenswarm/instance_manager/config.py#L42) |
| `PORT_TYPES` | `未显式标注` | [L51](../../../../../jiuwenswarm/instance_manager/config.py#L51) |
| `PID_FILENAME` | `未显式标注` | [L54](../../../../../jiuwenswarm/instance_manager/config.py#L54) |
| `PORT_ENV_OVERRIDES` | `未显式标注` | [L133](../../../../../jiuwenswarm/instance_manager/config.py#L133) |
| `PORT_ENV_NAMES` | `未显式标注` | [L376](../../../../../jiuwenswarm/instance_manager/config.py#L376) |

### [`class InstancesYamlError(Exception)`](../../../../../jiuwenswarm/instance_manager/config.py#L26)

Custom exception for instances.yaml parsing/validation errors.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, message: str)` | 源码未提供方法级文档字符串。 | [L32](../../../../../jiuwenswarm/instance_manager/config.py#L32) |

### [`class InstanceConfig`](../../../../../jiuwenswarm/instance_manager/config.py#L58)

Configuration for a named instance.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L66](../../../../../jiuwenswarm/instance_manager/config.py#L66) |
| `workspace` | `Path` | `—` | [L67](../../../../../jiuwenswarm/instance_manager/config.py#L67) |
| `ports` | `Dict[str, int]` | `field(default_factory=dict)` | [L68](../../../../../jiuwenswarm/instance_manager/config.py#L68) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __post_init__(self) -> None` | Expand and resolve workspace path. | [L70](../../../../../jiuwenswarm/instance_manager/config.py#L70) |
| `def get_pid_file_path(self) -> Path` | Get the PID file path for this instance. | [L74](../../../../../jiuwenswarm/instance_manager/config.py#L74) |
| `def get_bootstrap_env_path(self) -> Path` | Get the bootstrap .env file path for this instance. | [L78](../../../../../jiuwenswarm/instance_manager/config.py#L78) |

### [`class InstanceStatus`](../../../../../jiuwenswarm/instance_manager/config.py#L84)

Runtime status of an instance.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L95](../../../../../jiuwenswarm/instance_manager/config.py#L95) |
| `running` | `bool` | `—` | [L96](../../../../../jiuwenswarm/instance_manager/config.py#L96) |
| `pid` | `Optional[int]` | `—` | [L97](../../../../../jiuwenswarm/instance_manager/config.py#L97) |
| `workspace` | `Path` | `—` | [L98](../../../../../jiuwenswarm/instance_manager/config.py#L98) |
| `ports` | `Dict[str, int]` | `—` | [L99](../../../../../jiuwenswarm/instance_manager/config.py#L99) |
| `started_at` | `Optional[float]` | `None` | [L100](../../../../../jiuwenswarm/instance_manager/config.py#L100) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def validate_instance_name(name: str) -> Optional[str]` | Validate instance name, return error message or None if valid. | [L103](../../../../../jiuwenswarm/instance_manager/config.py#L103) |
| `def is_valid_instance_name(name: str) -> bool` | Check if instance name is valid (returns bool). | [L125](../../../../../jiuwenswarm/instance_manager/config.py#L125) |
| `def _resolved_base_port(port_type: str) -> int` | Return the effective base port for a port type. | [L142](../../../../../jiuwenswarm/instance_manager/config.py#L142) |
| `def compute_auto_port(port_type: str, index: int) -> int` | Compute auto-allocated port for an instance. | [L163](../../../../../jiuwenswarm/instance_manager/config.py#L163) |
| `def calculate_instance_ports(index: int) -> Dict[str, int]` | Calculate ports for an instance: base_port + index * 1000. | [L184](../../../../../jiuwenswarm/instance_manager/config.py#L184) |
| `def _bind_listen_probe(host: str, port: int, family: int) -> bool \| None` | Try bind()+listen() on ``host:port``. | [L192](../../../../../jiuwenswarm/instance_manager/config.py#L192) |
| `def is_port_available(host: str, port: int) -> bool` | Check if a port is available for binding on the given host. | [L230](../../../../../jiuwenswarm/instance_manager/config.py#L230) |
| `def check_port_conflicts(ports: Dict[str, int], host: str = '127.0.0.1', existing_ports: Optional[Sequence[int]] = None) -> List[int]` | Check for port conflicts. | [L276](../../../../../jiuwenswarm/instance_manager/config.py#L276) |
| `def collect_all_ports(exclude_name: Optional[str] = None) -> List[int]` | Collect all ports used by all instances for conflict detection. | [L304](../../../../../jiuwenswarm/instance_manager/config.py#L304) |
| `def _get_system_executable(name: str) -> str` | Get absolute path for system executable. | [L345](../../../../../jiuwenswarm/instance_manager/config.py#L345) |
| `def find_available_ports(base_index: int = 0, host: str = '127.0.0.1', scan_range: int = 10, exclude_ports: Optional[Sequence[int]] = None) -> Optional[tuple]` | Scan upward for the first fully-available port group. | [L388](../../../../../jiuwenswarm/instance_manager/config.py#L388) |
| `def _upsert_env_ports(env_path: Path, ports: Dict[str, int]) -> None` | Idempotently write port assignments into a .env file. | [L434](../../../../../jiuwenswarm/instance_manager/config.py#L434) |
| `def _format_url_hint(ports: Dict[str, int]) -> str` | Build the TUI/CLI connection hint shown after a port fallback. | [L479](../../../../../jiuwenswarm/instance_manager/config.py#L479) |

## `jiuwenswarm/instance_manager/lock.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/lock.py#L1)

**模块职责：** Instance lock and PID file management.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L27](../../../../../jiuwenswarm/instance_manager/lock.py#L27) |
| `LOCK_FILENAME` | `未显式标注` | [L30](../../../../../jiuwenswarm/instance_manager/lock.py#L30) |
| `STALE_LOCK_TIMEOUT` | `未显式标注` | [L32](../../../../../jiuwenswarm/instance_manager/lock.py#L32) |

### [`class InstanceLock`](../../../../../jiuwenswarm/instance_manager/lock.py#L35)

Cross-platform file lock for instance startup concurrency control.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, config: InstanceConfig)` | Initialize lock for given instance. | [L59](../../../../../jiuwenswarm/instance_manager/lock.py#L59) |
| `def acquire(self, timeout: float = 5.0) -> bool` | Acquire exclusive lock for instance startup. | [L69](../../../../../jiuwenswarm/instance_manager/lock.py#L69) |
| `def release(self) -> None` | Release the lock. | [L87](../../../../../jiuwenswarm/instance_manager/lock.py#L87) |
| `def _acquire_unix(self, timeout: float) -> bool` | Unix implementation using fcntl.flock. | [L110](../../../../../jiuwenswarm/instance_manager/lock.py#L110) |
| `def _acquire_windows(self, timeout: float) -> bool` | Windows implementation using exclusive file creation. | [L139](../../../../../jiuwenswarm/instance_manager/lock.py#L139) |
| `def _is_stale_lock(self) -> bool` | Check if existing lock file is stale (older than STALE_LOCK_TIMEOUT). | [L167](../../../../../jiuwenswarm/instance_manager/lock.py#L167) |
| `def _remove_stale_lock(self) -> None` | Remove stale lock file. | [L176](../../../../../jiuwenswarm/instance_manager/lock.py#L176) |
| `def __enter__(self) -> 'InstanceLock'` | Context manager entry. | [L184](../../../../../jiuwenswarm/instance_manager/lock.py#L184) |
| `def __exit__(self, exc_type, exc_val, exc_tb) -> None` | Context manager exit. | [L189](../../../../../jiuwenswarm/instance_manager/lock.py#L189) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def write_pid_file(config: InstanceConfig, pid: int, started_at: Optional[float] = None) -> None` | Write PID file for a running instance. | [L194](../../../../../jiuwenswarm/instance_manager/lock.py#L194) |
| `def read_pid_file(config: InstanceConfig) -> Optional[Dict[str, Any]]` | Read PID file for an instance. | [L241](../../../../../jiuwenswarm/instance_manager/lock.py#L241) |
| `def delete_pid_file(config: InstanceConfig) -> bool` | Delete PID file for an instance. | [L263](../../../../../jiuwenswarm/instance_manager/lock.py#L263) |
| `def is_process_alive(pid: int) -> bool` | Check if a process with given PID is alive. | [L282](../../../../../jiuwenswarm/instance_manager/lock.py#L282) |
| `def check_instance_running(workspace: Path) -> bool` | Check if instance is running via PID file (legacy interface). | [L320](../../../../../jiuwenswarm/instance_manager/lock.py#L320) |

## `jiuwenswarm/instance_manager/status.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/status.py#L1)

**模块职责：** Instance status query and process control.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L40](../../../../../jiuwenswarm/instance_manager/status.py#L40) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_instance_status(config: InstanceConfig) -> InstanceStatus` | Get runtime status of an instance. | [L43](../../../../../jiuwenswarm/instance_manager/status.py#L43) |
| `def get_default_instance_status() -> InstanceStatus` | Get status of the default instance (workspace at ~/.jiuwenswarm). | [L82](../../../../../jiuwenswarm/instance_manager/status.py#L82) |
| `def _find_pid_by_port(port: int) -> Optional[int]` | Find process PID that is listening on the given port. | [L135](../../../../../jiuwenswarm/instance_manager/status.py#L135) |
| `def list_all_instances(include_default: bool = True) -> List[InstanceStatus]` | List status of all instances. | [L199](../../../../../jiuwenswarm/instance_manager/status.py#L199) |
| `def format_status_line(status: InstanceStatus) -> str` | Format an instance status for display. | [L227](../../../../../jiuwenswarm/instance_manager/status.py#L227) |
| `def get_instance_config(name: str) -> Optional[InstanceConfig]` | Load instance configuration from instances.yaml. | [L254](../../../../../jiuwenswarm/instance_manager/status.py#L254) |
| `def load_all_instance_configs(path: Optional[Path] = None) -> Dict[str, InstanceConfig]` | Load all instance configurations from instances.yaml. | [L290](../../../../../jiuwenswarm/instance_manager/status.py#L290) |
| `def stop_process_by_pid(pid: int, timeout: float = 10.0) -> bool` | Stop a process by its PID directly. | [L338](../../../../../jiuwenswarm/instance_manager/status.py#L338) |
| `def stop_instance_process(config: InstanceConfig, timeout: float = 10.0) -> bool` | Stop a running instance process. | [L392](../../../../../jiuwenswarm/instance_manager/status.py#L392) |

## `jiuwenswarm/instance_manager/yaml.py`

[打开源码](../../../../../jiuwenswarm/instance_manager/yaml.py#L1)

**模块职责：** YAML configuration management for instances.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L27](../../../../../jiuwenswarm/instance_manager/yaml.py#L27) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_instances_yaml_path() -> Path` | Return path to instances.yaml: ~/.jiuwenswarm/instances.yaml | [L30](../../../../../jiuwenswarm/instance_manager/yaml.py#L30) |
| `def get_instances_dir() -> Path` | Return base directory for named instance workspaces: ~/.jiuwenswarm-instances/ | [L35](../../../../../jiuwenswarm/instance_manager/yaml.py#L35) |
| `def get_instance_workspace_path(name: str) -> Path` | Return workspace path for a named instance: ~/.jiuwenswarm-instances/<name>/ | [L40](../../../../../jiuwenswarm/instance_manager/yaml.py#L40) |
| `def _read_yaml_file(path: Path) -> dict` | Read and parse YAML file with error handling. | [L45](../../../../../jiuwenswarm/instance_manager/yaml.py#L45) |
| `def _validate_yaml_structure(data: Any, path: Path) -> dict` | Validate YAML top-level structure. | [L80](../../../../../jiuwenswarm/instance_manager/yaml.py#L80) |
| `def _validate_instance_entry(name: str, inst_data: Any, path: Path) -> None` | Validate a single instance entry in instances.yaml. | [L113](../../../../../jiuwenswarm/instance_manager/yaml.py#L113) |
| `def _validate_ports_config(name: str, ports: Any, path: Path) -> None` | Validate ports configuration for an instance. | [L157](../../../../../jiuwenswarm/instance_manager/yaml.py#L157) |
| `def load_instances_yaml() -> dict` | Load instances.yaml with comprehensive error handling. | [L193](../../../../../jiuwenswarm/instance_manager/yaml.py#L193) |
| `def save_instances_yaml(data: dict) -> None` | Save instances.yaml file atomically. | [L221](../../../../../jiuwenswarm/instance_manager/yaml.py#L221) |
| `def create_instances_yaml_template() -> Path` | Create a minimal instances.yaml template file if not exists. | [L257](../../../../../jiuwenswarm/instance_manager/yaml.py#L257) |
| `def update_instances_yaml(name: str, workspace: Path, ports: dict \| None = None) -> None` | Add or update instance entry in instances.yaml with full configuration. | [L294](../../../../../jiuwenswarm/instance_manager/yaml.py#L294) |
| `def get_instance_index(name: str) -> int` | Get instance declaration order index (starts from 1, 0 reserved for default). | [L329](../../../../../jiuwenswarm/instance_manager/yaml.py#L329) |

## `jiuwenswarm/llm_sse_patch.py`

[打开源码](../../../../../jiuwenswarm/llm_sse_patch.py#L1)

**模块职责：** Runtime patches for OpenAIModelClient.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L34](../../../../../jiuwenswarm/llm_sse_patch.py#L34) |
| `_HUAWEI_MAAS_API_MARKERS` | `tuple[str, ...]` | [L38](../../../../../jiuwenswarm/llm_sse_patch.py#L38) |
| `_GLM_TOOL_XML_CLOSED_RE` | `未显式标注` | [L57](../../../../../jiuwenswarm/llm_sse_patch.py#L57) |
| `_GLM_TOOL_XML_TRUNCATED_OPEN_RE` | `未显式标注` | [L61](../../../../../jiuwenswarm/llm_sse_patch.py#L61) |
| `_PATCH_APPLIED` | `未显式标注` | [L97](../../../../../jiuwenswarm/llm_sse_patch.py#L97) |
| `_AUTH_HEADER_PATCH_APPLIED` | `未显式标注` | [L98](../../../../../jiuwenswarm/llm_sse_patch.py#L98) |
| `_HUAWEI_MAAS_PLACEHOLDER_API_KEY` | `未显式标注` | [L99](../../../../../jiuwenswarm/llm_sse_patch.py#L99) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _is_huawei_maas_api_base(api_base: str) -> bool` | 检测 ``api_base`` 是否指向华为云 ModelArts MaaS 服务。 | [L46](../../../../../jiuwenswarm/llm_sse_patch.py#L46) |
| `def _sanitize_glm_tool_xml_tags(raw: str) -> str` | Strip GLM native XML tags and their inner content. | [L67](../../../../../jiuwenswarm/llm_sse_patch.py#L67) |
| `def _extract_authorization_header(headers: dict[str, Any] \| None) -> tuple[str, str] \| None` | Return ``(original_key, value)`` for Authorization when present. | [L102](../../../../../jiuwenswarm/llm_sse_patch.py#L102) |
| `def _restore_authorization_header(headers: dict[str, str], auth: tuple[str, str] \| None) -> dict[str, str]` | Re-apply Authorization after sanitize (case-insensitive replace). | [L121](../../../../../jiuwenswarm/llm_sse_patch.py#L121) |
| `def _resolve_model_client_authorization(model_client_config: Any) -> tuple[str, str] \| None` | Resolve explicit auth, or the scoped Huawei MaaS compatibility fallback. | [L136](../../../../../jiuwenswarm/llm_sse_patch.py#L136) |
| `def apply_openai_auth_header_patch() -> None` | Keep intentional ``Authorization`` on LLM client headers (Huawei MaaS Basic). | [L167](../../../../../jiuwenswarm/llm_sse_patch.py#L167) |
| `def _parse_chunk(chunk_str: str) -> dict \| None` | 解析单个数据块 JSON。 | [L279](../../../../../jiuwenswarm/llm_sse_patch.py#L279) |
| `def _extract_message_content(chunk: dict) -> tuple[str, str]` | 从 chunk 中提取思考内容和输出内容。 | [L290](../../../../../jiuwenswarm/llm_sse_patch.py#L290) |
| `def _build_tool_calls(msg: dict) -> list \| None` | 从消息中构建工具调用对象列表。 | [L298](../../../../../jiuwenswarm/llm_sse_patch.py#L298) |
| `def assemble_openai_response(response: str) -> Any` | 将分块 SSE 数据组装成标准的 OpenAI ``ChatCompletion``。 | [L318](../../../../../jiuwenswarm/llm_sse_patch.py#L318) |
| `def apply_openai_sse_invoke_patch() -> None` | 给 ``OpenAIModelClient`` 打补丁： | [L400](../../../../../jiuwenswarm/llm_sse_patch.py#L400) |

## `jiuwenswarm/openjiuwen_log_patch.py`

[打开源码](../../../../../jiuwenswarm/openjiuwen_log_patch.py#L1)

**模块职责：** Runtime patch: honor LOG_TO_FILE_ENABLED for openjiuwen structured logging.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L9](../../../../../jiuwenswarm/openjiuwen_log_patch.py#L9) |
| `_LOG_TO_FILE_PATCH_APPLIED` | `未显式标注` | [L11](../../../../../jiuwenswarm/openjiuwen_log_patch.py#L11) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def apply_openjiuwen_log_to_file_setting() -> None` | When ``LOG_TO_FILE_ENABLED=false``, keep openjiuwen console output only. | [L14](../../../../../jiuwenswarm/openjiuwen_log_patch.py#L14) |

## `jiuwenswarm/openjiuwen_skip_tool_patch.py`

[打开源码](../../../../../jiuwenswarm/openjiuwen_skip_tool_patch.py#L1)

**模块职责：** Runtime patch: ensure _skip_tool rails attach a ToolMessage before execution short-circuit.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_SKIP_TOOL_TOOL_MESSAGE_PATCHED` | `未显式标注` | [L7](../../../../../jiuwenswarm/openjiuwen_skip_tool_patch.py#L7) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def apply_skip_tool_tool_message_patch() -> None` | Ensure _skip_tool rails always attach a ToolMessage before execution short-circuit. | [L10](../../../../../jiuwenswarm/openjiuwen_skip_tool_patch.py#L10) |

## `jiuwenswarm/openjiuwen_streaming_tool_patch.py`

[打开源码](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L1)

**模块职责：** Runtime patch: prevent ReAct from hanging forever on stuck tool tasks.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L17) |
| `_STREAMING_TOOL_WAIT_TIMEOUT_PATCHED` | `未显式标注` | [L19](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L19) |
| `_current_streaming_tool_executor` | `ContextVar[Optional[Any]]` | [L23](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L23) |
| `_DEFAULT_STREAMING_TOOL_WAIT_TIMEOUT_S` | `未显式标注` | [L33](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L33) |
| `_executor_wait_clocks` | `WeakKeyDictionary[Any, _WaitTimeoutClock]` | [L74](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L74) |
| `_executor_wait_clocks_strong` | `dict[int, _WaitTimeoutClock]` | [L76](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L76) |

### [`class _WaitTimeoutClock`](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L37)

Pauseable deadline clock for wait_all (HITL time excluded).

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `pause_depth` | `int` | `0` | [L40](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L40) |
| `paused_total` | `float` | `0.0` | [L41](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L41) |
| `_pause_started` | `float \| None` | `None` | [L42](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L42) |
| `_unpaused` | `asyncio.Event` | `field(default_factory=asyncio.Event)` | [L43](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L43) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __post_init__(self) -> None` | 源码未提供方法级文档字符串。 | [L45](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L45) |
| `def pause(self) -> None` | 源码未提供方法级文档字符串。 | [L48](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L48) |
| `def resume(self) -> None` | 源码未提供方法级文档字符串。 | [L54](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L54) |
| `async def wait_unpaused(self, timeout: float) -> bool` | Wait until not paused. Returns False on timeout. | [L64](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L64) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_streaming_tool_wait_timeout_s(*, default: float = _DEFAULT_STREAMING_TOOL_WAIT_TIMEOUT_S) -> Optional[float]` | Return wait_all timeout seconds, or None to disable. | [L79](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L79) |
| `def _executor_wait_clock(executor: Any) -> _WaitTimeoutClock` | 源码未提供函数级文档字符串。 | [L94](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L94) |
| `def pause_streaming_tool_wait_timeout() -> None` | Pause the active StreamingToolExecutor wait_all budget (HITL / interrupt). | [L109](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L109) |
| `def resume_streaming_tool_wait_timeout() -> None` | Resume a previously paused wait_all budget. | [L117](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L117) |
| `@asynccontextmanager async def streaming_tool_wait_timeout_paused()` | Context manager: exclude enclosed await time from wait_all timeout. | [L126](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L126) |
| `def _remap_wait_all_timeout_results(results: list, timeout: float) -> list` | 源码未提供函数级文档字符串。 | [L135](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L135) |
| `async def _wait_all_with_pauseable_timeout(executor: Any, orig_wait_all, timeout: float) -> list` | Run orig wait_all; pause stretches the deadline (HITL excluded). | [L151](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L151) |
| `def apply_streaming_tool_wait_timeout_patch() -> None` | Prevent ReAct from hanging forever on stuck tool tasks. | [L209](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py#L209) |

## `jiuwenswarm/start_services.py`

[打开源码](../../../../../jiuwenswarm/start_services.py#L1)

**模块职责：** Launch JiuWenSwarm frontend/backend services with one command.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DATA_ROOT` | `未显式标注` | [L57](../../../../../jiuwenswarm/start_services.py#L57) |
| `PACKAGE_DIR` | `未显式标注` | [L62](../../../../../jiuwenswarm/start_services.py#L62) |
| `WEB_DEV_DIR` | `未显式标注` | [L65](../../../../../jiuwenswarm/start_services.py#L65) |

### [`class InstanceCommand`](../../../../../jiuwenswarm/start_services.py#L72)

Unified base class for instance management commands.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, name: str)` | 源码未提供方法级文档字符串。 | [L89](../../../../../jiuwenswarm/start_services.py#L89) |
| `def validate_and_load(self) -> int \| None` | Validate instance name and load config/status. | [L95](../../../../../jiuwenswarm/start_services.py#L95) |
| `def check_workspace_exists(self) -> int \| None` | Check if workspace directory exists. | [L129](../../../../../jiuwenswarm/start_services.py#L129) |
| `def check_running(self) -> bool \| None` | Check if instance is running. | [L144](../../../../../jiuwenswarm/start_services.py#L144) |
| `def check_ports_available(self) -> int \| None` | Check if all instance ports are available (for start operation). | [L154](../../../../../jiuwenswarm/start_services.py#L154) |
| `def check_ports_conflicts(self) -> list[tuple[str, int]]` | Check all instance ports and return the list of conflicts. | [L167](../../../../../jiuwenswarm/start_services.py#L167) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def print_instance_details(status: InstanceStatus) -> None` | Print detailed instance status in unified format. | [L194](../../../../../jiuwenswarm/start_services.py#L194) |
| `def _log_port_table(prefix: str, ports: dict[str, int]) -> None` | Log a port table with the given prefix line. | [L216](../../../../../jiuwenswarm/start_services.py#L216) |
| `def _resolve_ports_with_fallback(cmd: InstanceCommand, scan_range: int = 10) -> int \| None` | Resolve port conflicts by scanning for an available port group. | [L223](../../../../../jiuwenswarm/start_services.py#L223) |
| `def _sync_default_env_ports(ports: dict[str, int]) -> int \| None` | Sync the default instance's actual ports into ~/.jiuwenswarm/config/.env. | [L344](../../../../../jiuwenswarm/start_services.py#L344) |
| `def do_stop_instance(cmd: InstanceCommand) -> int` | Execute stop operation for an instance. | [L380](../../../../../jiuwenswarm/start_services.py#L380) |
| `def _run_instance_with_pid(commands: list[tuple[str, list[str], Path]], config: InstanceConfig) -> int` | Run processes for an instance with PID file management. | [L408](../../../../../jiuwenswarm/start_services.py#L408) |
| `def _build_commands(mode: str, dotenv_path: Path \| None = None) -> list[tuple[str, list[str], Path]]` | Build startup commands for instance. | [L449](../../../../../jiuwenswarm/start_services.py#L449) |
| `def _start_process(name: str, cmd: list[str], cwd: Path, *, ports: dict[str, int] \| None = None) -> subprocess.Popen[bytes]` | Start a single subprocess. | [L489](../../../../../jiuwenswarm/start_services.py#L489) |
| `def _terminate_processes(processes: dict[str, subprocess.Popen[bytes]]) -> None` | Terminate all running processes gracefully. | [L524](../../../../../jiuwenswarm/start_services.py#L524) |
| `def _resolve_runtime_ports() -> dict[str, int]` | Resolve default-instance ports from env overrides with BASE_PORTS fallback. | [L543](../../../../../jiuwenswarm/start_services.py#L543) |
| `def _print_port_banner(targets: list[tuple[str, int, str]], ready: dict[str, bool]) -> None` | Print a user-facing port / access-URL summary for issue #1059. | [L572](../../../../../jiuwenswarm/start_services.py#L572) |
| `def _wait_for_services_ready(ports: dict[str, int], processes: dict[str, subprocess.Popen[bytes]], *, overall_timeout: float \| None = None) -> None` | Wait for services to be ready and log a complete access / port summary. | [L594](../../../../../jiuwenswarm/start_services.py#L594) |
| `def _run_processes(commands: list[tuple[str, list[str], Path]], ports: dict[str, int]) -> int` | Run processes and wait for them. | [L739](../../../../../jiuwenswarm/start_services.py#L739) |
| `def _run(mode: str) -> int` | Run default instance. | [L772](../../../../../jiuwenswarm/start_services.py#L772) |
| `def _action_list() -> int` | List all instances with their status. | [L807](../../../../../jiuwenswarm/start_services.py#L807) |
| `def _action_status(name: str) -> int` | Show detailed status of a specific instance. | [L831](../../../../../jiuwenswarm/start_services.py#L831) |
| `def _action_stop(name: str) -> int` | Stop a running instance. | [L849](../../../../../jiuwenswarm/start_services.py#L849) |
| `def _action_restart(name: str, mode: str = 'all') -> int` | Restart an instance (stop then start). | [L874](../../../../../jiuwenswarm/start_services.py#L874) |
| `def _start_named_instance(name: str, mode: str) -> int` | Start a named instance. | [L915](../../../../../jiuwenswarm/start_services.py#L915) |
| `def _parse_args() -> argparse.Namespace` | Parse CLI arguments. | [L964](../../../../../jiuwenswarm/start_services.py#L964) |
| `def _validate_args(args: argparse.Namespace) -> int \| None` | Validate argument combinations, return error code or None if valid. | [L1012](../../../../../jiuwenswarm/start_services.py#L1012) |
| `def _dispatch_action(args: argparse.Namespace) -> int` | Dispatch action based on parsed arguments. | [L1025](../../../../../jiuwenswarm/start_services.py#L1025) |
| `def main() -> None` | CLI entry point. | [L1063](../../../../../jiuwenswarm/start_services.py#L1063) |
