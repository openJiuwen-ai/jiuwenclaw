# Server Runtime Core Python API

覆盖 `server/runtime` 根模块、会话、Agent Adapter、A2UI、调试与企业配置接口（不含 Skill 子系统）。

> 签名与行号取自当前源码 AST。这里同时列出公开和内部顶级接口；名称以下划线开头者是实现细节，不承诺稳定兼容。行为语义与调用约束请结合对应模块设计分册阅读。

## `jiuwenswarm/server/runtime/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/a2ui/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/__init__.py#L1)

**模块职责：** A2UI feature package.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/a2ui/__init__.py#L12) |

## `jiuwenswarm/server/runtime/a2ui/config.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L1)

**模块职责：** Configuration helpers for the A2UI feature.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SUPPORTED_A2UI_PROTOCOL_VERSIONS` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L12) |

### [`class A2UIConfig`](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L16)

Runtime switches controlling the optional A2UI feature.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `enabled` | `bool` | `False` | [L19](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L19) |
| `protocol_version` | `str` | `'0.8'` | [L20](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L20) |
| `stream_validation_enabled` | `bool` | `True` | [L21](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L21) |
| `non_web_fallback_enabled` | `bool` | `False` | [L22](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L22) |
| `dev_smoke_tools_enabled` | `bool` | `False` | [L23](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L23) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _to_bool(value: Any, default: bool) -> bool` | Parse config and environment boolean values with a stable default. | [L26](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L26) |
| `def get_a2ui_config(config: dict[str, Any] \| None = None) -> A2UIConfig` | Build A2UI config from a config dictionary plus environment overrides. | [L40](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L40) |
| `def get_current_a2ui_config() -> A2UIConfig` | Read A2UI config from the active jiuwenswarm runtime config. | [L73](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L73) |
| `def is_a2ui_enabled(config: dict[str, Any] \| None = None) -> bool` | Return whether A2UI is enabled for the supplied config. | [L80](../../../../../jiuwenswarm/server/runtime/a2ui/config.py#L80) |

## `jiuwenswarm/server/runtime/a2ui/integration.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L1)

**模块职责：** Integration helpers for wiring A2UI into jiuwenswarm host modules.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L17) |
| `_WEB_CONFIG_KEY_MAP` | `dict[str, str]` | [L19](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L19) |
| `_A2UI_CONFIG_DEFAULT_PAYLOAD` | `dict[str, str]` | [L23](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L23) |
| `_A2UI_CHANNEL_ID` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L27) |
| `__all__` | `未显式标注` | [L157](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L157) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_a2ui_channel(channel: str \| None) -> bool` | Return whether the channel can natively run A2UI. | [L30](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L30) |
| `def _to_bool(value: Any) -> bool` | Normalize Web config boolean values to Python bools. | [L35](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L35) |
| `def _bool_text(value: bool) -> str` | Render boolean config values in the string format used by Web config. | [L42](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L42) |
| `def _get_runtime_a2ui_config()` | Read runtime A2UI config while preserving env-only fallback for tests. | [L47](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L47) |
| `def _build_a2ui_client_event_prompt(content: dict[str, Any], channel: str, language: str) -> str` | Delegate client-event prompt construction to the A2UI runtime package. | [L57](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L57) |
| `def build_user_prompt_if_a2ui_event(content: object, *, channel: str, language: str) -> str \| None` | Build a model prompt for A2UI client events when the feature is enabled. | [L64](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L64) |
| `async def finalize_assistant_response_if_a2ui(content: str, *, channel: str \| None = _A2UI_CHANNEL_ID, user_query: Any, request_id: str, repair_call: Any, retry_without_a2ui_call: Any = None) -> str` | Validate/repair assistant A2UI content while keeping host modules generic. | [L88](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L88) |
| `def apply_non_web_text_fallback_to_payload(payload: dict[str, object], *, channel_id: str) -> dict[str, object]` | Retain the legacy gateway hook while keeping A2UI Web-only. | [L119](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L119) |
| `def get_a2ui_config_payload(raw_config: dict[str, object]) -> dict[str, str]` | Return user-facing Web config payload fields for the A2UI section. | [L132](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L132) |
| `def get_default_a2ui_config_payload() -> dict[str, str]` | Return fallback Web config fields when config loading fails. | [L140](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L140) |
| `def validate_a2ui_config_update(param_key: str, value: object) -> tuple[bool, dict[str, object], str]` | Validate and map one Web A2UI config update to config.yaml keys. | [L145](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L145) |

## `jiuwenswarm/server/runtime/a2ui/parser.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L1)

**模块职责：** A2UI response parsing helpers.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_A2UI_MESSAGE_KEYS` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L17) |
| `__all__` | `未显式标注` | [L121](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L121) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_a2ui_message(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L22](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L22) |
| `def coerce_message_list(value: Any) -> list[dict[str, Any]] \| None` | 源码未提供函数级文档字符串。 | [L26](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L26) |
| `def iter_tagged_block_bodies(text: str) -> list[tuple[int, str]]` | 源码未提供函数级文档字符串。 | [L34](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L34) |
| `def strip_tagged_a2ui_blocks(text: str) -> str` | 源码未提供函数级文档字符串。 | [L50](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L50) |
| `def parse_raw_json(text: str) -> list[dict[str, Any]] \| None` | 源码未提供函数级文档字符串。 | [L66](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L66) |
| `def parse_jsonl(text: str) -> list[dict[str, Any]] \| None` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L76) |
| `def parse_a2ui_response(content: str) -> list[A2UIResponsePart]` | 源码未提供函数级文档字符串。 | [L92](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L92) |
| `def may_contain_a2ui_content(content: str) -> bool` | 源码未提供函数级文档字符串。 | [L116](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L116) |

## `jiuwenswarm/server/runtime/a2ui/prompt_instructions.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L1)

**模块职责：** Request-scoped A2UI prompt rail instructions.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_BROWSER_WORKFLOW_ACTION_MARKERS` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L12) |
| `_BROWSER_WORKFLOW_INTENT_RE` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L34) |
| `__all__` | `未显式标注` | [L322](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L322) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_a2ui_browser_workflow_request(value: Any) -> bool` | Return whether the current request needs browser-specific A2UI rules. | [L64](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L64) |
| `def build_a2ui_browser_workflow_instruction() -> str` | Return browser-workflow rules for requests that actually need them. | [L82](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L82) |
| `def build_a2ui_autonomy_instruction(language: str = 'en') -> str` | 源码未提供函数级文档字符串。 | [L187](../../../../../jiuwenswarm/server/runtime/a2ui/prompt_instructions.py#L187) |

## `jiuwenswarm/server/runtime/a2ui/protocol.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L1)

**模块职责：** A2UI v0.8 protocol adapter and public protocol facade.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `A2UI_ACTIVE_PROTOCOL_VERSION` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L36) |
| `A2UI_CLIENT_EVENT_TYPE` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L37) |
| `logger` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L38) |
| `_SDK_JSON_OBJECT_WORKFLOW_LINE` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L40) |
| `_JWC_JSON_LIST_WORKFLOW_LINE` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L44) |
| `__all__` | `未显式标注` | [L745](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L745) |

### [`class A2UIProtocolSpec`](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L72)

Versioned A2UI protocol adapter.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, version: str) -> None` | 源码未提供方法级文档字符串。 | [L79](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L79) |
| `@property def catalog(self)` | 源码未提供方法级文档字符串。 | [L91](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L91) |
| `def build_prompt(self, language: str = 'en', *, include_browser_workflows: bool = False) -> str` | 源码未提供方法级文档字符串。 | [L94](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L94) |
| `def load_examples(self) -> list[A2UIExample]` | 源码未提供方法级文档字符串。 | [L156](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L156) |
| `def render_examples(self, *, validate: bool = False) -> str` | 源码未提供方法级文档字符串。 | [L166](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L166) |
| `@staticmethod def parse_response(content: str) -> list[A2UIResponsePart]` | 源码未提供方法级文档字符串。 | [L179](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L179) |
| `def validate_messages(self, messages: list[dict[str, Any]]) -> None` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L182) |
| `def validate_response(self, content: str) -> A2UIValidationResult` | 源码未提供方法级文档字符串。 | [L185](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L185) |
| `@staticmethod def may_contain_a2ui_content(content: str) -> bool` | Return whether content looks like tagged, raw, or JSONL A2UI output. | [L193](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L193) |
| `def build_repair_prompt(self, invalid_content: str, validation_error: str, user_query: str \| None = None) -> str` | 源码未提供方法级文档字符串。 | [L197](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L197) |
| `def format_for_text_channel(self, content: str) -> str` | 源码未提供方法级文档字符串。 | [L222](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L222) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resources_dir() -> Path` | 源码未提供函数级文档字符串。 | [L50](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L50) |
| `def _examples_dir(version: str) -> Path` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L54) |
| `def _load_json_file(path: Path) -> Any` | 源码未提供函数级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L59) |
| `def _normalize_prompt_contract(prompt: str) -> str` | Remove SDK default wording that conflicts with jiuwenswarm's v0.8 contract. | [L64](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L64) |
| `@lru_cache(maxsize=4) def get_protocol_spec(version: str = A2UI_ACTIVE_PROTOCOL_VERSION) -> A2UIProtocolSpec` | Return a cached protocol spec. | [L231](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L231) |
| `def build_a2ui_prompt_section(language: str = 'en', *, include_browser_workflows: bool = False) -> str` | 源码未提供函数级文档字符串。 | [L242](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L242) |
| `def format_a2ui_for_text_channel(content: str, version: str = VERSION_0_8) -> str` | 源码未提供函数级文档字符串。 | [L253](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L253) |
| `def format_content_for_channel(content: str, channel_id: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L257](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L257) |
| `def is_a2ui_client_event(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L267](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L267) |
| `def _log_a2ui_client_event(event: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L271](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L271) |
| `def _get_a2ui_user_action(event: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L290](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L290) |
| `def _get_a2ui_action_context(event: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L296](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L296) |
| `def _get_a2ui_action_name(event: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L302](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L302) |
| `def _get_a2ui_next_action(event: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L307](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L307) |
| `def _is_browser_preflight_submit(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L312](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L312) |
| `def _is_hotel_option_select(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L318](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L318) |
| `def _is_hotel_payment_confirm(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L327](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L327) |
| `def _is_hotel_payment_cancel(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L333](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L333) |
| `def _is_gmail_email_select(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L339](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L339) |
| `def _is_gmail_reply_draft_select(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L348](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L348) |
| `def _is_gmail_send_confirm(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L357](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L357) |
| `def _is_gmail_send_cancel(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L363](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L363) |
| `def _is_gmail_cleanup_select(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L369](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L369) |
| `def _is_gmail_cleanup_confirm(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L375](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L375) |
| `def _is_gmail_cleanup_cancel(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L381](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L381) |
| `def _is_social_post_draft_select(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L387](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L387) |
| `def _is_social_post_confirm(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L396](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L396) |
| `def _is_social_post_cancel(event: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L402](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L402) |
| `def _build_a2ui_event_payload(event: dict[str, Any], channel: str, language: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L408](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L408) |
| `def _build_browser_preflight_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L422](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L422) |
| `def _build_hotel_option_select_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L443](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L443) |
| `def _build_hotel_payment_confirm_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L472](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L472) |
| `def _build_hotel_payment_cancel_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L492](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L492) |
| `def _build_gmail_email_select_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L506](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L506) |
| `def _build_gmail_reply_draft_select_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L532](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L532) |
| `def _build_gmail_send_confirm_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L553](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L553) |
| `def _build_gmail_send_cancel_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L573](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L573) |
| `def _build_gmail_cleanup_select_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L587](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L587) |
| `def _build_gmail_cleanup_confirm_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L606](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L606) |
| `def _build_gmail_cleanup_cancel_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L627](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L627) |
| `def _build_social_post_draft_select_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L641](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L641) |
| `def _build_social_post_confirm_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L663](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L663) |
| `def _build_social_post_cancel_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L683](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L683) |
| `def build_a2ui_client_event_prompt(event: dict[str, Any], channel: str, language: str) -> str` | 源码未提供函数级文档字符串。 | [L697](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L697) |

## `jiuwenswarm/server/runtime/a2ui/protocols/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/protocols/__init__.py#L1)

**模块职责：** Versioned A2UI protocol registry exports.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/a2ui/protocols/__init__.py#L10) |

## `jiuwenswarm/server/runtime/a2ui/runtime/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/__init__.py#L1)

**模块职责：** Runtime helpers for A2UI request handling.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L15](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/__init__.py#L15) |

## `jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L1)

**模块职责：** Final validation and repair for model-emitted A2UI responses.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `RepairCall` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L18) |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L19) |
| `_A2UI_PROTOCOL_LINE_RE` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L21) |
| `__all__` | `未显式标注` | [L164](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L164) |

### [`class A2UIFinalizationResult`](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L27)

Structured finalization result for retry decisions.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `content` | `str` | `—` | [L30](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L30) |
| `status` | `str` | `—` | [L31](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L31) |
| `validation_error` | `str \| None` | `None` | [L32](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L32) |

### [`class A2UIResponseFinalizer`](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L70)

Validate, repair, or safely degrade a complete assistant response.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def finalize(self, content: str, *, user_query: Any, request_id: str, repair_call: RepairCall \| None, max_repair_attempts: int = 2) -> str` | 源码未提供方法级文档字符串。 | [L73](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L73) |
| `async def finalize_result(self, content: str, *, user_query: Any, request_id: str, repair_call: RepairCall \| None, max_repair_attempts: int = 2) -> A2UIFinalizationResult` | 源码未提供方法级文档字符串。 | [L91](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L91) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _coerce_model_message_content(message: Any) -> str` | 源码未提供函数级文档字符串。 | [L35](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L35) |
| `def _a2ui_failure_text(content: str, validation_error: str) -> str` | 源码未提供函数级文档字符串。 | [L50](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L50) |
| `def has_a2ui_protocol_marker(content: str) -> bool` | Return True when content looks like an A2UI payload or fragment. | [L59](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L59) |
| `def should_finalize_a2ui_content(content: str) -> bool` | Return True when the response should enter A2UI validation/repair. | [L65](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L65) |

## `jiuwenswarm/server/runtime/a2ui/runtime/formatter.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/formatter.py#L1)

**模块职责：** Legacy A2UI text formatting helpers kept for compatibility.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/formatter.py#L10) |

## `jiuwenswarm/server/runtime/a2ui/runtime/prompt.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/prompt.py#L1)

**模块职责：** A2UI prompt builders.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/prompt.py#L10) |

## `jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L1)

**模块职责：** Runtime helpers for validating and repairing assistant A2UI responses.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L26) |
| `RetryWithoutA2UI` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L29) |
| `_A2UI_FINALIZATION_TIMEOUT_SECONDS` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L30) |
| `_A2UI_FAST_PATH_VALIDATION_TIMEOUT_SECONDS` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L31) |
| `__all__` | `未显式标注` | [L205](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L205) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def finalize_a2ui_assistant_content(content: str, *, user_query: Any, request_id: str, repair_call: RepairCall \| None, a2ui_enabled: bool, retry_without_a2ui_call: RetryWithoutA2UI \| None = None) -> str` | Validate and repair a complete assistant response when A2UI is enabled. | [L34](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L34) |
| `def _has_parseable_tagged_a2ui_blocks(content: str) -> bool` | 源码未提供函数级文档字符串。 | [L144](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L144) |
| `async def _validate_parseable_tagged_a2ui_fast_path(content: str) -> tuple[bool, str \| None]` | 源码未提供函数级文档字符串。 | [L158](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L158) |
| `def _a2ui_timeout_fallback(content: str) -> str` | 源码未提供函数级文档字符串。 | [L177](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L177) |
| `def _coerce_model_message_content(message: Any) -> str` | 源码未提供函数级文档字符串。 | [L190](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L190) |

## `jiuwenswarm/server/runtime/a2ui/runtime/stream.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/stream.py#L1)

**模块职责：** A2UI streaming output guard.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L7](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/stream.py#L7) |

## `jiuwenswarm/server/runtime/a2ui/stream_guard.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L1)

**模块职责：** Streaming guard for tagged A2UI blocks.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L78](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L78) |

### [`class A2UIStreamGuard`](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L12)

Buffers A2UI blocks during streaming until they can be validated.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, spec: Any \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L15](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L15) |
| `def feed(self, content: str) -> list[str]` | 源码未提供方法级文档字符串。 | [L24](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L24) |
| `def finish(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L30](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L30) |
| `def _drain(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L42](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L42) |

## `jiuwenswarm/server/runtime/a2ui/support.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/support.py#L1)

**模块职责：** Compatibility facade for the modular A2UI feature package.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/a2ui/support.py#L31) |

## `jiuwenswarm/server/runtime/a2ui/text_formatter.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L1)

**模块职责：** Format A2UI responses into plain text for fallback paths.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L113](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L113) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _literal_value(value: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L16](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L16) |
| `def _extract_component_text(component_node: dict[str, Any]) -> list[str]` | 源码未提供函数级文档字符串。 | [L32](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L32) |
| `def _summarize_messages(messages: list[dict[str, Any]]) -> list[str]` | 源码未提供函数级文档字符串。 | [L62](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L62) |
| `def format_for_text_channel(content: str, *, parse_response: Callable[[str], list[A2UIResponsePart]], validate_response: Callable[[str], A2UIValidationResult]) -> str` | 源码未提供函数级文档字符串。 | [L86](../../../../../jiuwenswarm/server/runtime/a2ui/text_formatter.py#L86) |

## `jiuwenswarm/server/runtime/a2ui/types.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L1)

**模块职责：** Shared A2UI runtime data structures.

### [`class A2UIResponsePart`](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L13)

One parsed segment from a mixed text and A2UI assistant response.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `kind` | `Literal['text', 'a2ui']` | `—` | [L16](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L16) |
| `text` | `str` | `''` | [L17](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L17) |
| `messages` | `list[dict[str, Any]] \| None` | `None` | [L18](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L18) |

### [`class A2UIExample`](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L22)

Loaded A2UI example used to ground prompt instructions.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L25](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L25) |
| `path` | `Path` | `—` | [L26](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L26) |
| `messages` | `list[dict[str, Any]]` | `—` | [L27](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L27) |

### [`class A2UIValidationResult`](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L31)

Validation outcome for model-emitted A2UI content.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `valid` | `bool` | `—` | [L34](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L34) |
| `error` | `str` | `''` | [L35](../../../../../jiuwenswarm/server/runtime/a2ui/types.py#L35) |

## `jiuwenswarm/server/runtime/a2ui/validator.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L1)

**模块职责：** A2UI schema and runtime semantic validation.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L329](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L329) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_data_path(path: Any) -> str` | 源码未提供函数级文档字符串。 | [L18](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L18) |
| `def _join_data_path(base_path: str, key: str) -> str` | 源码未提供函数级文档字符串。 | [L24](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L24) |
| `def _validate_value_map_keys(entries: Any, path: str) -> None` | 源码未提供函数级文档字符串。 | [L33](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L33) |
| `def _index_data_model_entries(entries: Any, path: str, index: dict[str, list[dict[str, Any]]]) -> None` | 源码未提供函数级文档字符串。 | [L51](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L51) |
| `def _build_data_model_index(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]` | 源码未提供函数级文档字符串。 | [L68](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L68) |
| `def _iter_templates(value: Any) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L79](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L79) |
| `def _iter_component_references(value: Any, component_ids: set[str]) -> list[str]` | 源码未提供函数级文档字符串。 | [L93](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L93) |
| `def _component_subtree_ids(root_component_id: str, components_by_id: dict[str, dict[str, Any]]) -> list[str]` | 源码未提供函数级文档字符串。 | [L115](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L115) |
| `def _iter_binding_paths(value: Any) -> list[str]` | 源码未提供函数级文档字符串。 | [L134](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L134) |
| `def _component_has_template(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L150](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L150) |
| `def _is_valid_template_item_path(path: str) -> bool` | 源码未提供函数级文档字符串。 | [L154](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L154) |
| `def _template_path_requires_object_item(path: str) -> bool` | 源码未提供函数级文档字符串。 | [L162](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L162) |
| `def _entry_is_object_like(entry: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L173](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L173) |
| `def _iter_image_url_literals(value: Any) -> list[str]` | 源码未提供函数级文档字符串。 | [L186](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L186) |
| `def _validate_image_runtime_semantics(messages: list[dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L202](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L202) |
| `def _validate_template_runtime_semantics(messages: list[dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L212](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L212) |
| `def validate_a2ui_messages(catalog: Any, messages: list[dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L275](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L275) |
| `def validate_a2ui_response(content: str, *, parse_response: Callable[[str], list[A2UIResponsePart]], validate_messages: Callable[[list[dict[str, Any]]], None]) -> A2UIValidationResult` | 源码未提供函数级文档字符串。 | [L283](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L283) |

## `jiuwenswarm/server/runtime/agent_adapter/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L1)

**模块职责：** Unified adapter protocol for JiuWenSwarm SDK backends.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L19) |
| `_SDK_ENV_VAR` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L21) |
| `_DEFAULT_SDK` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L22) |

### [`class AgentAdapter(Protocol)`](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L26)

Minimal capability set every SDK adapter must satisfy.

装饰器：`@runtime_checkable`。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def create_instance(self, config: dict[str, Any] \| None = None, *, mode: str = 'claw', sub_mode: str = None, config_base: dict[str, Any] \| None = None) -> None` | Initialise the underlying SDK agent from config. | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L33) |
| `async def reload_agent_config(self, config_base: dict[str, Any] \| None = None, env_overrides: dict[str, Any] \| None = None, target_session_id: str \| None = None) -> Any` | Hot-reload configuration without restarting the process. | [L49](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L49) |
| `async def process_message_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AgentResponse` | Execute a single non-streaming request and return the response. | [L67](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L67) |
| `async def process_message_stream_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AsyncIterator[AgentResponseChunk]` | Execute a streaming request; yield response chunks. | [L78](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L78) |
| `async def process_interrupt(self, request: AgentRequest) -> AgentResponse` | Handle interrupt requests (pause/resume/cancel/supplement). | [L89](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L89) |
| `async def handle_user_answer(self, request: AgentRequest) -> AgentResponse` | Handle user answer for evolution approval or permission approval. | [L93](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L93) |
| `async def handle_swarmflow_reply(self, request: AgentRequest) -> AgentResponse` | Handle a person's reply to a pending swarmflow human-session turn. | [L97](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L97) |
| `async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse` | Handle heartbeat requests. | [L105](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L105) |
| `def is_working(self, session_tasks: dict, session_queues: dict) -> bool` | Return whether the agent is currently working. | [L108](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L108) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve_sdk_choice() -> str` | Resolve SDK choice from environment variable. | [L121](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L121) |
| `def create_adapter(sdk: str \| None = None, *, mode: str = 'agent', workspace_dir: str \| None = None, agent_id: str \| None = None, service_id: str \| None = None) -> AgentAdapter` | Factory function to create SDK adapter instance. | [L150](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L150) |

## `jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L1)

**模块职责：** CodeAgentRail — 管理 /agents 创建的自定义子智能体。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_SUB_AGENTS_DIR` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L30) |
| `DISALLOWED_FOR_SUBAGENTS` | `set[str]` | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L33) |
| `TOOL_GROUPS` | `dict[str, list[str]]` | [L39](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L39) |
| `_DISPLAY_TO_INTERNAL` | `dict[str, str]` | [L53](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L53) |
| `__all__` | `未显式标注` | [L434](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L434) |

### [`class AgentTool(Tool)`](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L175)

自定义 agent 调度工具。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, card: ToolCard, parent_agent: DeepAgent, custom_agents: list)` | 源码未提供方法级文档字符串。 | [L181](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L181) |
| `def _build_sub_session_id(self, parent_session_id: str, subagent_type: str) -> str` | 源码未提供方法级文档字符串。 | [L186](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L186) |
| `def _create_sub_agent(self, agent_def, sub_session_id: str) -> DeepAgent` | 从 AgentDefinition 直接创建子 DeepAgent，绕过 deep_config.subagents。 | [L189](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L189) |
| `async def invoke(self, inputs, **kwargs)` | 源码未提供方法级文档字符串。 | [L264](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L264) |
| `async def _run_async(self, subagent: DeepAgent, prompt: str, sub_session_id: str, subagent_type: str, parent_session: Session) -> None` | 源码未提供方法级文档字符串。 | [L339](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L339) |
| `async def stream(self, inputs, **kwargs)` | 源码未提供方法级文档字符串。 | [L355](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L355) |

### [`class CodeAgentRail(DeepAgentRail)`](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L359)

Code 模式下的自定义 agent rail。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `priority` | `未显式标注` | `90` | [L366](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L366) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: str)` | 源码未提供方法级文档字符串。 | [L368](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L368) |
| `def init(self, agent: DeepAgent) -> None` | 源码未提供方法级文档字符串。 | [L374](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L374) |
| `def uninit(self, agent: DeepAgent) -> None` | 源码未提供方法级文档字符串。 | [L378](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L378) |
| `def _register_agent_tool(self) -> None` | 源码未提供方法级文档字符串。 | [L382](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L382) |
| `def _unregister_agent_tool(self, agent: DeepAgent) -> None` | 源码未提供方法级文档字符串。 | [L405](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L405) |
| `def _load_custom_agents(self) -> list` | 从 AgentConfigService 加载启用的自定义 agent。 | [L419](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L419) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_display_to_internal_mapping() -> dict[str, str]` | Build reverse mapping: display name → internal name. | [L74](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L74) |
| `def _filter_tool_cards(all_tool_cards: list[ToolCard], allowed_tools: list[str], disallowed_tools: list[str] \| None = None) -> list[ToolCard]` | Filter ToolCards based on agent definition's tools/disallowed_tools fields. | [L83](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L83) |
| `def _build_agent_tool_card(custom_agents: list, agent_id: str \| None = None) -> ToolCard` | 动态构建 Agent 工具卡片，只列出自定义 agent。 | [L120](../../../../../jiuwenswarm/server/runtime/agent_adapter/code_agent_rail.py#L120) |

## `jiuwenswarm/server/runtime/agent_adapter/compact_partial_prompts.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/compact_partial_prompts.py#L1)

**模块职责：** /rewind summarize 的 partial compact prompt 模板

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `NO_TOOLS_PREAMBLE` | `未显式标注` | [L5](../../../../../jiuwenswarm/server/runtime/agent_adapter/compact_partial_prompts.py#L5) |
| `PARTIAL_COMPACT_PROMPT` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/agent_adapter/compact_partial_prompts.py#L14) |
| `PARTIAL_COMPACT_UP_TO_PROMPT` | `未显式标注` | [L109](../../../../../jiuwenswarm/server/runtime/agent_adapter/compact_partial_prompts.py#L109) |

## `jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L1)

**模块职责：** Shared helpers for skill evolution events and status pushes.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L20) |
| `_EVOLUTION_FILENAME` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L22) |
| `TEAM_EVOLUTION_IDLE_SLEEP_SEC` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L23) |
| `TEAM_EVOLUTION_EVENT_TIMEOUT_SEC` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L24) |
| `TEAM_EVOLUTION_EVENT_TIMEOUT_GRACE_SEC` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L25) |
| `TEAM_EVOLUTION_START_STAGE` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L26) |
| `TEAM_EVOLUTION_START_MESSAGE` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L27) |
| `TEAM_EVOLUTION_NOOP_STAGE` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L28) |
| `TEAM_EVOLUTION_NOOP_NO_SKILL_STAGE` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L29) |
| `TEAM_EVOLUTION_NOOP_NO_SIGNAL_STAGE` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L30) |
| `TEAM_EVOLUTION_NOOP_NO_RECORDS_STAGE` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L31) |
| `TEAM_EVOLUTION_HIDDEN_STAGE` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L32) |
| `TEAM_EVOLUTION_NOOP_MARKERS` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L33) |
| `TEAM_EVOLUTION_NO_SKILL_MARKERS` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L38) |
| `TEAM_EVOLUTION_NO_SIGNAL_MARKERS` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L44) |
| `TEAM_EVOLUTION_NOOP_STAGES` | `未显式标注` | [L48](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L48) |
| `TEAM_EVOLUTION_HIDDEN_TERMINAL_STAGES` | `未显式标注` | [L54](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L54) |
| `TEAM_EVOLUTION_VISIBLE_PROGRESS_STAGES` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L55) |
| `REGULAR_EVOLUTION_VISIBLE_START_STAGES` | `未显式标注` | [L61](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L61) |
| `_SDK_PROGRESS_STAGE_MAP` | `未显式标注` | [L91](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L91) |
| `_SDK_PROGRESS_TERMINAL_STAGES` | `未显式标注` | [L104](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L104) |
| `EVOLUTION_ACCEPT_LABELS` | `未显式标注` | [L112](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L112) |
| `EVOLUTION_EXECUTE_LABELS` | `未显式标注` | [L121](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L121) |
| `REGULAR_EVOLUTION_SLASH_WARNING_PHRASES` | `未显式标注` | [L122](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L122) |
| `TEAM_EVOLUTION_SLASH_WARNING_PHRASES` | `未显式标注` | [L126](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L126) |
| `_EVOLUTION_SLASH_COMMANDS` | `未显式标注` | [L130](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L130) |

### [`class EvolutionPushContext`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L69)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `transport` | `Any` | `—` | [L70](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L70) |
| `channel_id` | `str \| None` | `—` | [L71](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L71) |
| `session_id` | `str` | `—` | [L72](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L72) |

### [`class EvolutionStatusUpdate`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L76)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L77](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L77) |
| `status` | `str` | `—` | [L78](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L78) |
| `stage` | `str` | `—` | [L79](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L79) |
| `message` | `str` | `''` | [L80](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L80) |

### [`class EvolutionProgressStatus`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L84)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `stage` | `str` | `—` | [L85](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L85) |
| `message` | `str` | `''` | [L86](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L86) |
| `request_id` | `str \| None` | `None` | [L87](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L87) |
| `terminal` | `bool` | `False` | [L88](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L88) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_skill_dir(store: Any, skill_name: str) -> Path \| None` | 源码未提供函数级文档字符串。 | [L138](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L138) |
| `def read_skill_kind(store: Any, skill_name: str) -> str \| None` | Read the ``kind`` field from a skill's SKILL.md frontmatter. | [L160](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L160) |
| `def merge_evolution_disabled_skills(disabled_skills: list[str] \| set[str] \| tuple[str, ...] \| str \| None = None) -> list[str]` | Union execution-disabled skills with package builtin skills for evolution rails. | [L182](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L182) |
| `def filter_evolution_eligible_skill_names(skill_names: list[str]) -> list[str]` | Drop package builtin skills that must not participate in self-evolution. | [L206](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L206) |
| `def guard_builtin_evolution_skill(skill_name: str) -> str \| None` | Return an error message when *skill_name* is a package builtin skill. | [L224](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L224) |
| `def sync_evolution_disabled_skills(rail: Any, disabled_skills: list[str] \| set[str] \| tuple[str, ...] \| str \| None) -> None` | Keep a rail's mutable ``disabled_skills`` set aligned with the deny-list. | [L235](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L235) |
| `def _available_skill_names(store: Any) -> str` | 源码未提供函数级文档字符串。 | [L249](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L249) |
| `def _skill_exists(store: Any, skill_name: str) -> bool` | 源码未提供函数级文档字符串。 | [L259](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L259) |
| `def _skill_definition_exists(store: Any, skill_name: str) -> bool` | 源码未提供函数级文档字符串。 | [L269](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L269) |
| `def validate_evolution_skill(store: Any, skill_name: str, require_skill_md: bool) -> str \| None` | Validate that an evolution command can target ``skill_name``. | [L283](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L283) |
| `def validate_team_evolution_skill(store: Any, skill_name: str, require_skill_md: bool) -> str \| None` | Validate that an evolution command can target ``skill_name`` in team mode. | [L309](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L309) |
| `def validate_evolution_log_writable(store: Any, skill_name: str) -> str \| None` | Validate the local evolution log target is writable when it can be inspected. | [L326](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L326) |
| `def evolution_status_response(evolve_result: Any, *, generation_failed_output: str, no_records_output: str) -> dict[str, str] \| None` | Map SDK ``EvolutionRequestResult.status`` to a user-facing response. | [L341](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L341) |
| `def _user_facing_generation_error(message: str) -> str` | Hide low-level toolchain error chains from user-facing LLM failure text. | [L379](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L379) |
| `def evolution_slash_display_level(result_type: str, content: str, *, warning_phrases: tuple[str, ...] = REGULAR_EVOLUTION_SLASH_WARNING_PHRASES) -> str` | Return frontend display severity for an evolution slash result. | [L394](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L394) |
| `def evolution_slash_command_name(query: str) -> str` | Return the evolution slash command name without the leading slash. | [L408](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L408) |
| `def evolution_slash_result(command: str, result: dict[str, Any], *, warning_phrases: tuple[str, ...] = REGULAR_EVOLUTION_SLASH_WARNING_PHRASES) -> dict[str, Any]` | Annotate an evolution slash command result for frontend rendering. | [L417](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L417) |
| `def event_payload_dict(evt: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L438](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L438) |
| `def event_type(evt: Any) -> str` | 源码未提供函数级文档字符串。 | [L446](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L446) |
| `def evolution_meta_from_payload(payload: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L455](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L455) |
| `def evolution_meta_from_params(params: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L463](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L463) |
| `def answer_selects_option(answer: Any, labels: tuple[str, ...]) -> bool` | 源码未提供函数级文档字符串。 | [L469](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L469) |
| `def answers_select_option(answers: list[Any], labels: tuple[str, ...]) -> bool` | 源码未提供函数级文档字符串。 | [L482](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L482) |
| `def approved_record_ids_from_answers(answers: list[Any], labels: tuple[str, ...], record_ids_by_index: list[str] \| None = None) -> tuple[bool, list[str] \| None]` | Map generic indexed answers back to SDK record ids when host state has them. | [L486](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L486) |
| `def record_ids_from_pending_approval(rail: Any, request_id: str) -> list[str] \| None` | 源码未提供函数级文档字符串。 | [L521](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L521) |
| `async def approve_evolution_records(rail: Any, request_id: str, approved_record_ids: list[str] \| None, *, legacy_fallback: bool = False) -> None` | 源码未提供函数级文档字符串。 | [L545](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L545) |
| `async def reject_evolution_records(rail: Any, request_id: str, *, legacy_fallback: bool = False) -> None` | 源码未提供函数级文档字符串。 | [L569](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L569) |
| `def resolve_evolution_event_timeout_sec(rail: Any, *, fallback_sec: float \| None = None, grace_sec: float \| None = None) -> float` | Resolve host watcher timeout from the SDK rail's background evolution timeout. | [L586](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L586) |
| `def is_evolution_approval_event(evt: Any) -> bool` | 源码未提供函数级文档字符串。 | [L611](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L611) |
| `def evolution_event_kind(evt: Any) -> str` | 源码未提供函数级文档字符串。 | [L618](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L618) |
| `def is_evolution_outcome_event(evt: Any) -> bool` | 源码未提供函数级文档字符串。 | [L629](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L629) |
| `def is_evolution_progress_event(evt: Any) -> bool` | True for rail `_emit_progress` host events (not model reasoning). | [L633](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L633) |
| `def _skip_evolution_stream_forward(evt: Any) -> bool` | Host progress/approval/outcome must not be re-emitted as chat.reasoning. | [L638](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L638) |
| `def evolution_outcome_from_event(evt: Any) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L648](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L648) |
| `def extract_evolution_request_id(evt: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L662](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L662) |
| `def evolution_progress_status_from_event(evt: Any) -> EvolutionProgressStatus \| None` | 源码未提供函数级文档字符串。 | [L673](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L673) |
| `def visible_evolution_progress_from_events(events: list[Any]) -> list[EvolutionProgressStatus]` | 源码未提供函数级文档字符串。 | [L699](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L699) |
| `def visible_regular_evolution_start_progress(progress_statuses: list[EvolutionProgressStatus]) -> list[EvolutionProgressStatus]` | 源码未提供函数级文档字符串。 | [L707](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L707) |
| `def progress_for_request(progress_statuses: list[EvolutionProgressStatus], request_id: str) -> list[EvolutionProgressStatus]` | 源码未提供函数级文档字符串。 | [L717](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L717) |
| `def terminal_stage(terminal: dict[str, str]) -> str` | 源码未提供函数级文档字符串。 | [L728](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L728) |
| `def terminal_progress_from_events(events: list[Any]) -> list[tuple[str \| None, dict[str, str]]]` | 源码未提供函数级文档字符串。 | [L732](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L732) |
| `def _noop_stage_from_message(message_lower: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L741](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L741) |
| `def team_evolution_terminal_progress(evt: Any) -> dict[str, str] \| None` | 源码未提供函数级文档字符串。 | [L753](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L753) |
| `def build_evolution_status_update(request_id: str, status: str, stage: str, message: str = '') -> EvolutionStatusUpdate` | 源码未提供函数级文档字符串。 | [L801](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L801) |
| `def team_evolution_end_update(request_id: str, terminal: dict[str, str] \| None) -> EvolutionStatusUpdate` | 源码未提供函数级文档字符串。 | [L815](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L815) |
| `def group_evolution_approvals(session_id: str, events: list[Any], *, warn_missing_request_id: Callable[[str], None] \| None = None) -> tuple[dict[str, list[Any]], list[str]]` | 源码未提供函数级文档字符串。 | [L850](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L850) |
| `def make_team_evolution_cycle_request_id(session_id: str, cycle_index: int) -> str` | 源码未提供函数级文档字符串。 | [L869](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L869) |
| `async def push_evolution_status(push_context: EvolutionPushContext, status_update: EvolutionStatusUpdate, build_push_message: Callable[..., dict[str, Any]], *, include_payload_request_id: bool = True) -> None` | 源码未提供函数级文档字符串。 | [L873](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L873) |
| `async def push_evolution_event(push_context: EvolutionPushContext, request_id: str, evt: Any, build_push_message: Callable[..., dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L898](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L898) |
| `async def broadcast_evolution_progress(channel_id: str \| None, session_id: str, events: list[Any], *, parse_stream_chunk: Callable[[Any], dict[str, Any] \| None], broadcast_event: Callable[[str \| None, str, dict[str, Any]], None]) -> None` | 源码未提供函数级文档字符串。 | [L919](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L919) |
| `async def push_evolution_progress(push_context: EvolutionPushContext, request_id: str, events: list[Any], *, parse_stream_chunk: Callable[[Any], dict[str, Any] \| None], build_push_message: Callable[..., dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L935](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_helpers.py#L935) |

## `jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L1)

**模块职责：** Rail-independent handlers for active Skill evolution slash commands.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L28) |
| `_COMMANDS` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L30) |
| `_DEFAULT_REVIEW_AGENT_NAME` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L36) |
| `__all__` | `未显式标注` | [L421](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L421) |

### [`class EvolutionSlashContext`](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L40)

Context needed to resolve evolution slash commands without a rail.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `mode` | `str` | `—` | [L43](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L43) |
| `session_id` | `str` | `—` | [L44](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L44) |
| `skills_dir` | `str \| list[str]` | `—` | [L45](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L45) |
| `evolution_enabled` | `bool` | `True` | [L46](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L46) |
| `language` | `str` | `'cn'` | [L47](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L47) |
| `review_agent_name` | `str` | `_DEFAULT_REVIEW_AGENT_NAME` | [L48](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L48) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def handle_evolution_slash_command(query: Any, context: EvolutionSlashContext) -> dict[str, Any] \| None` | Handle active evolution slash commands without requiring a mounted rail. | [L51](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L51) |
| `def _command_name(stripped: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L96](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L96) |
| `def _subject(store: EvolutionStore, skill_name: str) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L106](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L106) |
| `def _followup_response(action: str, followup_prompt: str, skill_name: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L122](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L122) |
| `def _error(output: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L131](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L131) |
| `def _answer(output: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L135](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L135) |
| `def _validate_skill(store: EvolutionStore, skill_name: str, *, require_skill_md: bool, context: EvolutionSlashContext, subject: dict[str, str]) -> str \| None` | 源码未提供函数级文档字符串。 | [L139](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L139) |
| `def _format_rollback_usage(store: EvolutionStore, context: EvolutionSlashContext, archive_service: EvolutionArchiveService) -> str` | 源码未提供函数级文档字符串。 | [L153](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L153) |
| `def _format_rollback_versions(skill_name: str, pairs: list[EvolutionArchivePair]) -> str` | 源码未提供函数级文档字符串。 | [L182](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L182) |
| `async def _handle_evolve(query: str, store: EvolutionStore, context: EvolutionSlashContext) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L193](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L193) |
| `async def _handle_evolve_list(query: str, store: EvolutionStore, context: EvolutionSlashContext) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L234](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L234) |
| `async def _handle_evolve_simplify(query: str, store: EvolutionStore, context: EvolutionSlashContext) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L288](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L288) |
| `async def _handle_evolve_rebuild(query: str, store: EvolutionStore, context: EvolutionSlashContext) -> dict[str, Any]` | Parse/validate `/evolve_rebuild`; adapter runs the shared merge pipeline. | [L338](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L338) |
| `async def _handle_evolve_rollback(query: str, store: EvolutionStore, context: EvolutionSlashContext) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L375](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_slash.py#L375) |

## `jiuwenswarm/server/runtime/agent_adapter/evolution_version.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L1)

**模块职责：** Host-side Skill evolution version control (rollback / rebuild orchestration).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L31) |
| `_SEMVER_BODY_RE` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L33) |
| `__all__` | `未显式标注` | [L439](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L439) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def safe_path_name(value: Any, label: str = 'skill') -> str` | Reject path traversal / empty names for evolution RPC params. | [L36](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L36) |
| `def skills_root_from_skill_md_path(skill_path: str \| None) -> str \| None` | Return ``…/skills`` root for a control-plane ``…/<name>/SKILL.md`` path. | [L49](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L49) |
| `def extra_trusted_dirs_for_skill_md(skill_md_path: str \| None) -> list[str]` | Skill-dir + ``…/skills`` root for rebuild ``trusted_dirs`` (workspace-outside). | [L62](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L62) |
| `def disk_only_evolution_skill_dirs(params: dict[str, Any] \| None = None) -> list[str]` | Skill roots for disk-only evolution: shared env + explicit skill_path root. | [L80](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L80) |
| `def get_disk_evolution_store(skills_dirs: str \| list[str] \| None = None) -> EvolutionStore` | Build a disk-only EvolutionStore (no EvolutionRail / LLM). | [L101](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L101) |
| `def resolve_subject(store: EvolutionStore, skill_name: str) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L108](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L108) |
| `def list_body_archive_versions(store: EvolutionStore, skill_name: str, *, subject_kind: str \| None = None) -> list[str]` | List paired SemVer body archive filenames (newest-first). | [L124](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L124) |
| `def skill_md_fingerprint(skill_md_path: str \| None) -> str \| None` | Return sha256 of SKILL.md bytes, or None when unreadable. | [L136](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L136) |
| `def validate_rebuild_skill_path(skill_path: str, *, skill_name: str) -> str` | Normalize skill_path; require SKILL.md and matching skill directory name. | [L148](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L148) |
| `def normalize_record_ids(raw: Any) -> list[str] \| None` | 源码未提供函数级文档字符串。 | [L168](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L168) |
| `async def do_evolve_rollback(store: EvolutionStore, skill_name: str, version: str \| None, *, timeout_sec: float = 30.0) -> dict[str, Any]` | Shared rollback used by slash and skills.evolution.rollback RPC. | [L181](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L181) |
| `def make_rebuild_service(store: EvolutionStore, *, llm: Any = None, model: str \| None = None, language: str = 'cn') -> ExperienceRebuildService` | Build ExperienceRebuildService with LLM for changelog classification. | [L304](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L304) |
| `async def prepare_rebuild_followup(store: EvolutionStore, skill_name: str, *, user_intent: str \| None = None, record_ids: Sequence[str] \| None = None, min_score: float = 0.5, language: str = 'cn', llm: Any = None, model: str \| None = None, skill_md_path: str \| None = None) -> dict[str, Any]` | Prepare rebuild context + followup prompt (does not clear live evolutions). | [L320](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L320) |
| `async def finalize_rebuild_followup(store: EvolutionStore, rebuild_context: dict[str, Any], *, llm: Any = None, model: str \| None = None, language: str = 'cn') -> dict[str, Any]` | Bump SemVer, append changelog, clear live evolutions after successful rewrite. | [L399](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L399) |
| `def is_body_archive_name(archive_name: str) -> bool` | 源码未提供函数级文档字符串。 | [L435](../../../../../jiuwenswarm/server/runtime/agent_adapter/evolution_version.py#L435) |

## `jiuwenswarm/server/runtime/agent_adapter/interface.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1)

**模块职责：** JiuWenSwarm Facade - 统一入口与 SDK 适配层.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `PLAN_REMINDER_ORIGINAL_QUERY_KEY` | `未显式标注` | [L78](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L78) |
| `_A2UI_STREAM_PROBE_WINDOW` | `未显式标注` | [L314](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L314) |
| `_A2UI_STREAM_PARTIAL_MARKERS` | `未显式标注` | [L315](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L315) |
| `_A2UI_PENDING_RENDER_DELTA` | `未显式标注` | [L322](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L322) |
| `_A2UI_STREAM_PROTOCOL_START_RE` | `未显式标注` | [L429](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L429) |
| `logger` | `未显式标注` | [L559](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L559) |
| `_SKILLDEV_METHODS` | `frozenset[ReqMethod]` | [L563](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L563) |
| `_SKILL_ROUTES` | `dict[ReqMethod, str]` | [L567](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L567) |
| `_SKILL_EVOLUTION_RAIL_ROUTES` | `frozenset[ReqMethod]` | [L617](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L617) |
| `_SKILLS_WEB_HANDLERS` | `frozenset[str]` | [L626](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L626) |
| `_SKILL_SOURCE_POLICY_ROUTES` | `frozenset[ReqMethod]` | [L633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L633) |
| `_CONTEXT_SIZE_HINT_KEYS` | `tuple[str, ...]` | [L646](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L646) |
| `_PLUGIN_ROUTES` | `dict[ReqMethod, str]` | [L659](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L659) |
| `_SYMPHONY_METHODS` | `frozenset[ReqMethod]` | [L668](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L668) |
| `_SKILL_COMMAND_REGEX` | `未显式标注` | [L678](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L678) |
| `_STATUSLINE_KNOWN_SUBCOMMANDS` | `未显式标注` | [L686](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L686) |
| `_STATUSLINE_PROMPT_REGEX` | `未显式标注` | [L687](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L687) |
| `_STATUSLINE_SETUP_PROMPT` | `未显式标注` | [L692](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L692) |

### [`class _TeamPlanApprovalPayloadError(ValueError)`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L85)

Raised when a structured team.plan approval payload is malformed.

### [`class JiuWenSwarm`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1027)

JiuWenSwarm 统一门面.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: str \| None = None, user_workspace_dir: str \| None = None, agent_id: str \| None = None, service_id: str \| None = None, skill_manager: SkillManager \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L1036](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1036) |
| `def _history_kwargs(self) -> dict[str, Any]` | history/metadata 写入时附带 sessions_root（租户隔离）. | [L1095](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1095) |
| `def _append_history_record(self, **kwargs: Any) -> None` | 源码未提供方法级文档字符串。 | [L1101](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1101) |
| `def _get_skilldev_service(self)` | 懒初始化并返回 SkillDevService 实例. | [L1105](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1105) |
| `def _ensure_adapter(self, *, mode: str = 'agent') -> AgentAdapter` | 确保 adapter 已初始化，如果未初始化则根据环境变量和 mode 创建. | [L1139](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1139) |
| `def ensure_adapter(self, *, mode: str = 'agent') -> AgentAdapter` | Public wrapper for ``_ensure_adapter`` (used by AgentManager reload). | [L1163](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1163) |
| `def _resolve_workspace_dir(self) -> str` | 源码未提供方法级文档字符串。 | [L1167](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1167) |
| `def _bind_tenant_request_context(self) -> tuple[Any, Any]` | 源码未提供方法级文档字符串。 | [L1175](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1175) |
| `@staticmethod def _reset_tenant_request_context(tenant_tokens: Any, mem_token: Any) -> None` | 源码未提供方法级文档字符串。 | [L1210](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1210) |
| `@staticmethod def _adapter_mode_for_request(request: AgentRequest) -> str` | 源码未提供方法级文档字符串。 | [L1238](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1238) |
| `async def create_instance(self, config: dict[str, Any] \| None = None, *, mode: str = 'agent', sub_mode: str = None, config_base: dict[str, Any] \| None = None) -> None` | 初始化 Agent 实例. | [L1249](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1249) |
| `async def _on_skillnet_install_complete(self) -> None` | Reload the agent and refresh active team shared skill links after async install. | [L1282](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1282) |
| `@staticmethod def _refresh_team_shared_skill_links(session_id: str \| None = None) -> None` | Refresh team shared skill links after the global skill root changes. | [L1288](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1288) |
| `async def refresh_enabled_skills_from_db(self) -> None` | 企业账本变更后轻量刷新启用集（直读 DB + 重建 SkillUseRail）。 | [L1297](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1297) |
| `async def _refresh_skill_rails_after_change(self) -> None` | 轻量刷新 skill rail，避免 uninstall 后全量重建 agent 实例. | [L1309](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1309) |
| `async def reload_agent_config(self, config_base: dict[str, Any] \| None = None, env_overrides: dict[str, Any] \| None = None, target_session_id: str \| None = None) -> ReloadResult` | 从配置重新加载. | [L1321](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1321) |
| `def is_working(self) -> bool` | True when SessionManager has in-flight session tasks. | [L1358](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1358) |
| `async def _try_apply_adapter_pending_reload(self) -> None` | Drain deferred adapter reloads when this facade is idle. | [L1368](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1368) |
| `async def prepare_session(self, *, session_id: str, channel_id: str, mode: str, project_dir: str \| None = None) -> None` | Initialize and start the session-owned DeepAgent without sending input. | [L1385](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1385) |
| `def build_inputs(self, request: AgentRequest) -> Tuple[dict[str, Any], str, str]` | 构建 adapter 所需的 inputs 字典（公共接口）. | [L1405](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1405) |
| `def _build_inputs(self, request: AgentRequest) -> Tuple[dict[str, Any], str, str]` | 构建 adapter 所需的 inputs 字典. | [L1409](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1409) |
| `@staticmethod def _team_plan_approval_payload_error_message() -> str` | 源码未提供方法级文档字符串。 | [L1705](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1705) |
| `@classmethod def _is_malformed_team_plan_approval_payload(cls, params: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L1713](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1713) |
| `def _make_retry_without_a2ui_call(self, *, adapter: AgentAdapter, request: AgentRequest)` | 源码未提供方法级文档字符串。 | [L1723](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1723) |
| `@staticmethod def _build_interactive_input_from_answers(request_id: str, answers: list[dict], source: str = '', *, status: str = '', original_request: str = '') -> Any` | 从用户答案构建 InteractiveInput. | [L1760](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1760) |
| `async def _handle_skilldev_request(self, request: AgentRequest) -> AgentResponse \| None` | 处理 SkillDev 相关请求，返回 None 表示不是 SkillDev 请求. | [L1885](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1885) |
| `async def _handle_skills_evolution_rail_request(self, request: AgentRequest) -> AgentResponse \| None` | Forward skills.evolution.archives/rollback/rebuild to DeepAdapter. | [L1914](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1914) |
| `async def _handle_skills_request(self, request: AgentRequest) -> AgentResponse \| None` | 处理 Skills 相关请求，返回 None 表示不是 Skills 请求. | [L1962](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1962) |
| `async def _handle_plugins_request(self, request: AgentRequest) -> AgentResponse \| None` | 处理 Plugin 相关请求，返回 None 表示不是 Plugin 请求. | [L2025](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2025) |
| `async def _handle_symphony_request(self, request: AgentRequest) -> AgentResponse \| None` | 处理 Symphony extension RPC 请求. | [L2059](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2059) |
| `async def _handle_symphony_request_stream(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]` | Stream Symphony RPC progress events, then the final RPC payload. | [L2095](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2095) |
| `async def _process_interrupt(self, request: AgentRequest) -> AgentResponse` | 处理 interrupt 请求. | [L2164](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2164) |
| `@staticmethod def _build_interrupt_result_response(request: AgentRequest, *, intent: str, success: bool, message: str) -> AgentResponse` | 源码未提供方法级文档字符串。 | [L2225](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2225) |
| `async def _process_team_interrupt(self, *, request: AgentRequest, intent: str, session_id: str) -> AgentResponse` | Handle interrupt requests for Team mode. | [L2245](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2245) |
| `async def _cancel_team_work_for_session(self, session_id: str, channel_id: str \| None = None, log_prefix: str = '') -> bool` | 终止当前 session 的 Team runtime（若存在）。 | [L2308](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2308) |
| `@staticmethod def _is_team_plan_confirm_answer(params: dict[str, Any]) -> bool` | Return True for structured team.plan approval answers. | [L2328](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2328) |
| `async def process_message(self, request: AgentRequest) -> AgentResponse` | 处理非流式请求. | [L2345](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2345) |
| `async def process_message_stream(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]` | 处理流式请求. | [L2632](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L2632) |
| `def get_instance(self)` | 源码未提供方法级文档字符串。 | [L3575](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3575) |
| `def get_registered_tools_catalog(self) -> list[dict[str, str]]` | 枚举当前实例已注册工具（name / description / short_description）。 | [L3578](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3578) |
| `async def ensure_instance(self)` | Return the adapter's DeepAgent, building the root one on first use. | [L3590](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3590) |
| `async def apply_package_change_to_session_adapters(self, operation: str, config_path: str) -> None` | Propagate a harness package load/unload to all live session adapters. | [L3606](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3606) |
| `async def compress_context(self, session_id: str, session: Any = None, *, return_state: bool = False) -> dict[str, Any]` | 主动触发上下文压缩。 | [L3621](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3621) |
| `async def get_context_usage(self, session_id: str) -> dict[str, Any]` | 获取当前上下文窗口占用统计。 | [L3648](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3648) |
| `async def generate_recap(self, session_id: str) -> dict[str, Any]` | 生成会话快速回顾（read-only，不修改对话历史）。 | [L3666](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3666) |
| `async def compact_partial(self, session_id: str, turn_index: int, direction: str = 'from') -> dict[str, Any]` | 部分对话压缩 — 对指定 turn 之前或之后的消息进行 LLM 摘要。 | [L3685](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3685) |
| `async def generate_btw_answer(self, session_id: str, question: str) -> dict[str, Any]` | 回答 /btw 侧问题：独立、无工具、单轮 LLM 查询。 | [L3714](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3714) |
| `async def cleanup_session_runtime(self, session_id: str) -> bool` | Release in-memory runtime owned by one session while keeping persisted history. | [L3737](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3737) |
| `def has_session_runtime(self, session_id: str \| None = None) -> bool` | Return whether this facade still owns session-scoped runtime. | [L3749](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3749) |
| `async def cancel_inflight_work(self, log_prefix: str = '[gateway disconnect] ') -> None` | Gateway 与 AgentServer 的 WebSocket 断开时调用：取消 session 流式任务并中止 adapter 内层循环。 | [L3763](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3763) |
| `async def cleanup(self) -> None` | 清理资源，准备销毁实例. | [L3777](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3777) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _permission_response_key(request: AgentRequest) -> str \| None` | Return the opaque request ID for a permission continuation. | [L89](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L89) |
| `def _duplicate_permission_response(request: AgentRequest) -> AgentResponse` | 源码未提供函数级文档字符串。 | [L109](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L109) |
| `def _duplicate_permission_chunk(request: AgentRequest) -> AgentResponseChunk` | 源码未提供函数级文档字符串。 | [L121](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L121) |
| `def _schedule_symphony_session_feedback(session_id: str, request_id: str) -> None` | Submit session-based Symphony learning without delaying the response. | [L133](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L133) |
| `def _history_user_content(params: Any, query: Any) -> Any` | 返回写入历史记录的用户消息内容. | [L149](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L149) |
| `def _should_record_user_history(params: Any) -> bool` | 源码未提供函数级文档字符串。 | [L179](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L179) |
| `def _history_media_string(item: dict[str, Any], *keys: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L192](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L192) |
| `def _history_media_size(item: dict[str, Any]) -> int \| float \| None` | 源码未提供函数级文档字符串。 | [L200](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L200) |
| `def _history_media_record(value: Any, *, default_type: str = 'image') -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L208](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L208) |
| `def _history_user_extra(params: Any) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L239](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L239) |
| `def _compact_stats_from_payload(payload: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L272](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L272) |
| `def _is_successful_compaction_payload(payload: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L280](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L280) |
| `def _append_compact_history_from_payload(*, payload: dict[str, Any], session_id: str, request_id: str, channel_id: str, mode: str) -> None` | 源码未提供函数级文档字符串。 | [L287](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L287) |
| `def _contains_a2ui_marker(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L310](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L310) |
| `def _make_a2ui_pending_render_chunk(*, request_id: str, channel_id: str) -> AgentResponseChunk` | 源码未提供函数级文档字符串。 | [L325](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L325) |
| `def _make_a2ui_final_chunk(*, request_id: str, channel_id: str, session_id: str, content: str) -> AgentResponseChunk` | 源码未提供函数级文档字符串。 | [L334](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L334) |
| `def _should_defer_a2ui_processing_status(*, suppress_a2ui_stream: bool, event_type: str, payload: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L353](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L353) |
| `def _is_duplicate_full_body_delta(pending_chunks: list[str], content: str) -> bool` | True when ``content`` is an exact replay of the already-buffered answer body. | [L366](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L366) |
| `def _normalize_nested_stream_chunk(chunk: AgentResponseChunk) -> AgentResponseChunk \| None` | Keep the facade stream open until its post-processing has finished. | [L380](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L380) |
| `def _extend_a2ui_stream_probe(previous: str, content: str) -> str` | 源码未提供函数级文档字符串。 | [L397](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L397) |
| `def _looks_like_partial_a2ui_marker(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L404](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L404) |
| `def _stream_probe_has_a2ui_marker(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L425](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L425) |
| `def _recent_line_offsets(value: str) -> list[tuple[int, str]]` | 源码未提供函数级文档字符串。 | [L435](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L435) |
| `def _a2ui_marker_start(value: Any) -> int \| None` | 源码未提供函数级文档字符串。 | [L449](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L449) |
| `def _split_a2ui_stream_content(previous_probe: str, content: str) -> tuple[str, str] \| None` | 源码未提供函数级文档字符串。 | [L480](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L480) |
| `def _trigger_auto_memory_extraction(adapter: Any, request: AgentRequest, session_id: str, is_stream: bool = False) -> None` | Trigger auto memory extraction after conversation ends. | [L496](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L496) |
| `def _handle_skills_use_slash_command(query: str) -> Tuple[list, str]` | Handle the /skills use slash command | [L820](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L820) |
| `def _handle_statusline_prompt_command(query: str) -> Tuple[str, str]` | 处理 /statusline <prompt> | [L837](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L837) |
| `def _enterprise_file_download_hint(language: str) -> str` | 源码未提供函数级文档字符串。 | [L872](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L872) |
| `def _normalize_files_for_agent_prompt(files: dict \| list \| Any) -> dict \| list \| Any` | 企业态：有 url 时去掉 Gateway 本地 path，避免 Agent 优先 read_file 失败。 | [L886](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L886) |
| `def build_user_prompt(content: str \| dict, files: dict, channel: str, language: str, *, trusted_dirs: list[str] \| None = None, metadata: dict[str, Any] \| None = None, skills: list[str] \| None = None, supplementary_info: str \| None = None) -> str` | Build user prompt for the agent. | [L905](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L905) |

## `jiuwenswarm/server/runtime/agent_adapter/interface_code.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1)

**模块职责：** JiuWenSwarm Code Adapter — code 模式配置驱动适配器.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L94](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L94) |
| `_PLAN_MODE_SYSTEM_NOTE` | `未显式标注` | [L119](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L119) |
| `_ENTER_PLAN_MODE_INSTRUCTIONS_EN` | `未显式标注` | [L144](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L144) |
| `_EXIT_PLAN_MODE_NOTIFICATION` | `未显式标注` | [L207](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L207) |
| `_RAIL_BUILD_NAMES` | `dict[str, str]` | [L216](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L216) |
| `_TOOL_BUILD_NAMES` | `dict[str, str]` | [L234](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L234) |
| `_CODE_PLAN_ALLOWED_TOOLS` | `list[str]` | [L345](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L345) |

### [`class JiuwenSwarmCodeAdapter(JiuWenSwarmDeepAdapter)`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L360)

Code 模式适配器 — 配置驱动注册 rails/tools.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_FIXED_RAIL_NAMES` | `未显式标注` | `frozenset({'RequestSummaryRail', 'RuntimePromptRail', 'ResponsePromptRail', 'JiuSwarmStreamEventRai…` | [L373](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L373) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L386](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L386) |
| `@staticmethod def _resolve_prompt_language() -> str` | Code mode always uses English for system prompts. | [L406](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L406) |
| `def _resolve_runtime_language(self) -> str` | Resolve runtime prompt language for code profile rails. | [L410](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L410) |
| `def _resolve_output_language(self) -> str` | Resolve user's preferred output language for runtime_state display. | [L414](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L414) |
| `async def create_instance(self, config: dict[str, Any] \| None = None, *, mode: str = 'code', sub_mode: str = None, config_base: dict[str, Any] \| None = None) -> None` | 初始化 DeepAgent 实例（code 模式）. | [L431](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L431) |
| `def _build_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any], *, mode: str = 'code', sub_mode: str \| None = None) -> list[Any]` | Build rails for code mode: fixed rails + dynamic rails from config. | [L604](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L604) |
| `def _build_filesystem_rail(self) -> SysOperationRail \| None` | 构建 SysOperationRail（FileSystemRail）. | [L741](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L741) |
| `def _build_agent_mode_rail(self) -> AgentModeRail \| None` | 构建 CodeAgentModeRail。 | [L751](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L751) |
| `@staticmethod def _build_coding_artifact_post_process_rail(*, coauthor_header_enabled: bool = False) -> CodingArtifactPostProcessRail \| None` | Build code-only artifact post-processing without todo lifecycle hooks. | [L779](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L779) |
| `@staticmethod def _build_code_task_planning_rail() -> CodeTaskPlanningRail \| None` | Register todo tools without openjiuwen todo system prompt injection. | [L795](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L795) |
| `def _build_structured_ask_user_rail(self) -> StructuredAskUserRail \| None` | 构建 StructuredAskUserRail. | [L803](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L803) |
| `def _build_confirm_interrupt_rail(self, tool_names: list[str] \| None = None) -> Any \| None` | 构建 CodeConfirmInterruptRail（控制类工具需用户确认，含可读提示）. | [L811](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L811) |
| `def _build_lsp_rail_via_config(self) -> Any` | 构建 LspRail（带 project_dir 参数）. | [L825](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L825) |
| `def _build_lsp_rail(self, workspace_dir: str \| None = None) -> LspRail \| None` | Build LspRail（code 模式专属）. | [L833](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L833) |
| `def _build_coding_memory_rail(self) -> CodingMemoryRail \| None` | 构建 CodingMemoryRail（主 Agent 和 code_agent subagent 共用）. | [L852](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L852) |
| `def _build_project_memory_rail(self) -> ProjectMemoryRail \| None` | Build ProjectMemoryRail to auto-load JIUWENSWARM.md / CLAUDE.md etc. | [L883](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L883) |
| `def _build_worktree_rail_via_config(self) -> WorktreeRail \| None` | Build WorktreeRail for code mode. | [L938](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L938) |
| `def _build_skill_rail_via_config(self) -> Any` | 构建 SkillUseRail（从 config 读取参数）. | [L967](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L967) |
| `def _build_context_assemble_rail(self) -> Any` | 构建 ContextEngineeringRail. | [L975](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L975) |
| `def _build_context_processor_rail(self) -> Any` | 构建 ContextProcessorRail — 复用父类逻辑. | [L979](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L979) |
| `def _build_skill_evolution_rail_via_config(self) -> Any` | 构建 SkillEvolutionRail. | [L984](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L984) |
| `@staticmethod def _subagent_list_has_name(subagents: list, name: str) -> bool` | 检查 subagents 列表中是否已包含指定名字的 subagent. | [L991](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L991) |
| `def _build_configured_subagents(self, model: Model, config: dict[str, Any], config_base: dict[str, Any] \| None = None) -> tuple[list[Any] \| None, bool]` | Build subagents for code mode: explore_agent + plan_agent + code_agent + browser_agent. | [L1003](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1003) |
| `def _user_interaction_rail_attribute(self) -> str` | 源码未提供方法级文档字符串。 | [L1108](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1108) |
| `async def _update_rails_for_mode(self, mode: str) -> None` | Code 模式下的 rail 生命周期管理. | [L1111](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1111) |
| `async def _reconcile_evolution_rails(self) -> None` | Keep evolution rails disabled for every Code adapter reload. | [L1168](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1168) |
| `def _build_code_agent_rail(self) -> CodeAgentRail \| None` | 构建 CodeAgentRail，管理 /agents 创建的自定义 agent。 | [L1176](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1176) |
| `def _build_plan_approval_rail(self) -> PlanApprovalInterruptRail \| None` | 构建 PlanApprovalInterruptRail，管理 plan 审批生命周期。 | [L1186](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1186) |
| `def _get_current_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any] \| None = None) -> tuple[list[Any], list[Any]]` | 扩展父类方法，将 Code/Plan 专属 Rail 纳入热重载范围。 | [L1200](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1200) |
| `async def _update_runtime_config(self, runtime_config: 'JiuWenSwarmDeepAdapter._RuntimeConfig') -> None` | Code 模式 runtime config: ProjectMemoryRail 语言同步 + rail 模式切换. | [L1220](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1220) |
| `async def _get_tool_cards(self, agent_id: str) -> list[Any]` | 源码未提供方法级文档字符串。 | [L1346](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1346) |
| `def build_code_tool_cards(self, agent_id: str) -> list[Any]` | Get tool cards for code mode — from config.yaml::modes.code.tools. | [L1349](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1349) |
| `def _get_tool_build_func(self, tool_name: str, agent_id: str) -> Any \| None` | 根据 tool 名字调用对应构建方法. | [L1393](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1393) |
| `def _build_web_search_tool(self, agent_id: str) -> Any` | Build unified ``web_search`` tool. | [L1407](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1407) |
| `def _build_web_fetch_webpage_tool(self, agent_id: str) -> Any` | 构建 web_fetch_webpage 工具. | [L1413](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1413) |
| `def _build_user_todos_tool(self, agent_id: str) -> list[Any] \| None` | 注册 user_todos 工具. | [L1419](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1419) |
| `def _build_skill_toolkit(self, agent_id: str) -> list[Any] \| None` | 构建 SkillToolkit 工具（不注册到 Runner，由 _get_tool_cards 统一注册）. | [L1435](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1435) |
| `def _skill_retrieval_tools_enabled_for_runtime(self, config_base: dict[str, Any] \| None = None) -> bool` | Respect code-mode configured tools during runtime skill retrieval sync. | [L1454](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1454) |
| `def _build_skill_retrieval_toolkit(self, agent_id: str) -> list[Any] \| None` | 构建 SkillRetrievalToolkit 工具（不注册到 Runner，由 _get_tool_cards 统一注册）. | [L1465](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1465) |
| `def _build_acp_chat_tool(self, agent_id: str) -> Any \| None` | Register acp_chat when at least one external ACP profile is configured. | [L1485](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1485) |
| `def merge_member_mcp_configs(self, agent: Any, config_base: dict[str, Any]) -> int` | Merge enabled code-mode MCP configs into a team member agent. | [L1493](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1493) |
| `def configure_team_member_agent(self, agent: Any, *, parent_agent: Any \| None = None, skill_manager: Any \| None = None, member_name: str \| None = None, role: str \| None = None, session_id: str \| None = None, channel_id: str \| None = None, project_dir: str \| None = None, runtime_language: str \| None = None, force_english_runtime_prompt: bool = True) -> None` | Apply the code runtime profile to a team member DeepAgent. | [L1522](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1522) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _parse_config_bool(value: Any, *, default: bool = False) -> bool` | Parse YAML and environment-backed boolean config values. | [L97](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L97) |
| `def _resolve_coding_memory_dir(*, project_dir: str \| None, agent_workspace_dir: str) -> str` | Resolve the app-owned CodingMemory directory scoped by project. | [L244](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L244) |
| `def _build_coding_memory_directory_node(coding_memory_path: str, *, description: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L256](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L256) |
| `def _set_workspace_coding_memory_directory(workspace: Any, *, project_dir: str \| None, agent_workspace_dir: str, description: str = 'Coding Agent memory') -> None` | 源码未提供函数级文档字符串。 | [L278](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L278) |
| `def create_coding_memory_rail(*, project_dir: str \| None, agent_workspace_dir: str, config: dict[str, Any] \| None) -> CodingMemoryRail` | Create CodingMemoryRail, falling back when embedding config is incomplete. | [L300](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L300) |
| `def _tool_card_identity(card: Any) -> tuple[str, str]` | 源码未提供函数级文档字符串。 | [L1634](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1634) |
| `def _subagent_name(spec: Any) -> str` | 源码未提供函数级文档字符串。 | [L1641](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1641) |
| `def _iter_agent_rails(agent: Any) -> list[Any]` | 源码未提供函数级文档字符串。 | [L1648](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1648) |
| `def _agent_has_rail_type(agent: Any, rail: Any) -> bool` | 源码未提供函数级文档字符串。 | [L1657](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1657) |
| `def _queue_rail_if_missing(agent: Any, rail: Any) -> bool` | 源码未提供函数级文档字符串。 | [L1661](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1661) |
| `def _merge_tool_cards(agent: Any, tool_cards: list[Any]) -> int` | 源码未提供函数级文档字符串。 | [L1669](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1669) |
| `def _merge_subagents(agent: Any, subagents: list[Any] \| None) -> int` | 源码未提供函数级文档字符串。 | [L1704](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1704) |
| `def _resolve_member_workspace_root(agent: Any) -> str \| None` | 源码未提供函数级文档字符串。 | [L1726](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1726) |
| `def _set_coding_memory_directory(agent: Any, project_dir: str \| None, agent_workspace_dir: str) -> None` | 源码未提供函数级文档字符串。 | [L1735](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1735) |
| `def configure_code_team_member_agent(agent: Any, *, parent_agent: Any \| None = None, skill_manager: Any \| None = None, member_name: str \| None = None, role: str \| None = None, session_id: str \| None = None, channel_id: str \| None = None, project_dir: str \| None = None, runtime_language: str \| None = None, force_english_runtime_prompt: bool = True) -> None` | Apply JiuwenSwarmCodeAdapter's runtime profile to a team member DeepAgent. | [L1750](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L1750) |

## `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1)

**模块职责：** JiuWenSwarm Deep Adapter - 基于 openjiuwen DeepAgent 的适配器实现.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `GOAL_UPDATED_EVENT_TYPE` | `未显式标注` | [L137](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L137) |
| `_ERROR_EVENT` | `未显式标注` | [L138](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L138) |
| `ERROR_EVENT_TYPE` | `未显式标注` | [L141](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L141) |
| `STREAM_SOURCE_ID_FIELD` | `未显式标注` | [L144](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L144) |
| `_INTERRUPT_OUTPUT_ATTACH_RETRY_COUNT` | `未显式标注` | [L145](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L145) |
| `_INTERRUPT_OUTPUT_ATTACH_RETRY_INTERVAL_SECONDS` | `未显式标注` | [L146](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L146) |
| `_SKILL_TURBO_TOOL_ID_SUFFIX` | `未显式标注` | [L150](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L150) |
| `_DEEPRESEARCH_REWRITE_REPLAY_STATE_KEY` | `未显式标注` | [L203](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L203) |
| `_DEEPRESEARCH_REWRITE_REPLAY_SCHEMA_VERSION` | `未显式标注` | [L204](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L204) |
| `_DEEPRESEARCH_REWRITE_REPLAY_MAX_ENTRIES` | `未显式标注` | [L205](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L205) |
| `_DEEPRESEARCH_REWRITE_REQUEST_ID_MAX_LENGTH` | `未显式标注` | [L206](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L206) |
| `_DEEPRESEARCH_REWRITE_REPLAY_MAX_BYTES` | `未显式标注` | [L207](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L207) |
| `_DEEPRESEARCH_REWRITE_REPLAY_PATH_MAX_BYTES` | `未显式标注` | [L208](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L208) |
| `_DEEPRESEARCH_REWRITE_REPLAY_TERMINAL_KINDS` | `未显式标注` | [L209](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L209) |
| `_DEEPRESEARCH_REWRITE_SUCCESS_MESSAGE` | `未显式标注` | [L212](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L212) |
| `_DEEPRESEARCH_REWRITE_DELIVERY_FAILURE_MESSAGE` | `未显式标注` | [L216](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L216) |
| `_DEEPRESEARCH_REWRITE_PUBLISH_UNCERTAIN_MESSAGE` | `未显式标注` | [L219](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L219) |
| `_DEEPRESEARCH_CHECKPOINT_UNCERTAIN` | `未显式标注` | [L222](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L222) |
| `_DEEPRESEARCH_CHECKPOINT_UNCERTAIN_MESSAGE` | `未显式标注` | [L223](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L223) |
| `TodoModifyTool` | `未显式标注` | [L547](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L547) |
| `_react_config` | `未显式标注` | [L551](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L551) |
| `_CRON_TOOL_CHANNEL_ID` | `ContextVar[str]` | [L553](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L553) |
| `_CRON_TOOL_SESSION_ID` | `ContextVar[str \| None]` | [L557](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L557) |
| `_CRON_TOOL_METADATA` | `ContextVar[dict[str, Any] \| None]` | [L561](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L561) |
| `_CRON_TOOL_MODE` | `ContextVar[str \| None]` | [L565](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L565) |
| `_CRON_TOOL_BOUND` | `ContextVar[bool]` | [L569](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L569) |
| `_LLM_TRACE_SESSION_ID` | `ContextVar[str]` | [L574](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L574) |
| `_LLM_TRACE_REQUEST_ID` | `ContextVar[str]` | [L578](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L578) |
| `_LLM_TRACE_ITERATION` | `ContextVar[int \| None]` | [L582](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L582) |
| `_LLM_TRACE_MODEL_NAME` | `ContextVar[str]` | [L586](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L586) |
| `_LATENCY_PRE_LLM_MARKED` | `ContextVar[bool]` | [L591](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L591) |
| `_REASONING_TRACE_LOG_BATCH` | `未显式标注` | [L596](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L596) |
| `_LLM_IO_TRACE_PATCH_APPLIED` | `未显式标注` | [L597](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L597) |
| `logger` | `未显式标注` | [L642](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L642) |
| `_PERSISTENT_CHECKPOINTER_LOCK` | `asyncio.Lock \| None` | [L644](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L644) |
| `_PERSISTENT_CHECKPOINTER_LOCK_LOOP` | `asyncio.AbstractEventLoop \| None` | [L645](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L645) |
| `_PERSISTENT_CHECKPOINTER_LOCK_INIT` | `未显式标注` | [L646](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L646) |
| `_PERSISTENT_CHECKPOINTER_READY` | `未显式标注` | [L647](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L647) |
| `_shared_checkpoint_checkpointer` | `Any` | [L648](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L648) |
| `_shared_mysql_checkpoint_engine` | `Any` | [L649](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L649) |
| `_shared_postgresql_checkpoint_engine` | `Any` | [L650](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L650) |
| `_AGENT_CARD_ID` | `未显式标注` | [L740](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L740) |
| `_SYS_OPERATION_REFCOUNTS` | `dict[str, int]` | [L755](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L755) |
| `_SYS_OPERATION_REFCOUNT_LOCK` | `未显式标注` | [L756](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L756) |
| `_ACP_BLOCKED_DEFAULT_TOOL_NAMES` | `未显式标注` | [L973](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L973) |
| `_SKILL_RETRIEVAL_TOOL_NAMES` | `未显式标注` | [L981](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L981) |
| `_SLOW_RUNTIME_CONFIG_MS` | `未显式标注` | [L991](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L991) |
| `_SLOW_RAIL_BUILD_MS` | `未显式标注` | [L997](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L997) |
| `_STAGE_LOG_THRESHOLD_ENV` | `未显式标注` | [L1003](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1003) |
| `_STABLE_GIT_FACTS` | `dict[str, _StableGitFacts]` | [L1099](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1099) |
| `_STABLE_GIT_FACTS_LOCK` | `未显式标注` | [L1100](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1100) |
| `_CRON_TOOL_NAMES` | `未显式标注` | [L1178](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1178) |
| `_DEFAULT_PROGRESSIVE_EAGER_TOOLS` | `未显式标注` | [L1191](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1191) |
| `_PROGRESSIVE_META_TOOL_NAMES` | `未显式标注` | [L1211](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1211) |
| `_LEGACY_PROGRESSIVE_EAGER_TOOL_ALIASES` | `未显式标注` | [L1212](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1212) |
| `_MODE_DISPLAY_MAP` | `dict[str, dict[str, str]]` | [L1886](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1886) |

### [`class _DeepResearchRouteContextToken`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L612)

源码未提供类级文档字符串。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `token` | `Token[dict[str, object] \| None] \| None` | `—` | [L613](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L613) |

### [`class _RuntimeCronContextTokens`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L617)

源码未提供类级文档字符串。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `channel` | `Token[str] \| None` | `—` | [L618](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L618) |
| `session` | `Token[str \| None] \| None` | `—` | [L619](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L619) |
| `metadata` | `Token[dict[str, Any] \| None] \| None` | `—` | [L620](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L620) |
| `mode` | `Token[str \| None] \| None` | `—` | [L621](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L621) |
| `bound` | `Token[bool] \| None` | `—` | [L622](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L622) |
| `shell` | `Token[str \| None] \| None` | `—` | [L623](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L623) |
| `deepresearch` | `_DeepResearchRouteContextToken \| None` | `—` | [L624](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L624) |
| `send_file` | `Token \| None` | `None` | [L625](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L625) |

### [`class _GitSnapshot`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1036)

Git state as of a conversation's first turn, held for its whole life.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `head` | `str` | `—` | [L1048](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1048) |
| `branch` | `str` | `—` | [L1049](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1049) |
| `status` | `str` | `—` | [L1050](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1050) |
| `recent_commits` | `str` | `—` | [L1051](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1051) |

### [`class _StableGitFacts`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1080)

Git facts about a project that do not change between turns of a chat.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `is_repo` | `bool` | `—` | [L1093](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1093) |
| `user_name` | `str` | `—` | [L1094](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1094) |
| `main_branch` | `str` | `—` | [L1095](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1095) |
| `head_file` | `str` | `—` | [L1096](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1096) |

### [`class _RailBuildInfo`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1160)

One rail's construction recipe, shared by the agent and code rail sets.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `attr_name` | `str` | `—` | [L1170](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1170) |
| `build_func` | `Callable` | `—` | [L1171](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1171) |
| `params` | `dict` | `None` | [L1172](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1172) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __post_init__(self)` | Normalize the optional params mapping to an empty dict. | [L1174](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1174) |

### [`class _RuntimeCronToolContext`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1920)

Stable cron tool context proxy backed by per-task contextvars.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, tool_scope: str) -> None` | 源码未提供方法级文档字符串。 | [L1923](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1923) |
| `def remember_current_binding(self) -> None` | 源码未提供方法级文档字符串。 | [L1930](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1930) |
| `@property def channel_id(self) -> str` | 源码未提供方法级文档字符串。 | [L1938](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1938) |
| `@property def session_id(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L1944](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1944) |
| `@property def metadata(self) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L1950](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1950) |
| `@property def mode(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L1958](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1958) |
| `@property def tool_scope(self) -> str` | 源码未提供方法级文档字符串。 | [L1964](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1964) |

### [`class JiuWenSwarmDeepAdapter`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1989)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `SESSION_ADAPTER_IDLE_TTL_SEC` | `未显式标注` | `2 * 60 * 60` | [L1990](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1990) |
| `SESSION_ADAPTER_EVICT_BATCH_SIZE` | `未显式标注` | `3` | [L1991](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1991) |
| `SESSION_ADAPTER_RELOAD_RETRY_INTERVAL_SEC` | `未显式标注` | `30.0` | [L1992](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1992) |
| `_RUNTIME_STATE_WRITE_LIMIT` | `未显式标注` | `threading.BoundedSemaphore(2)` | [L1993](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1993) |
| `_MEMORY_REINDEX_DELAY_SECONDS` | `float` | `5.0` | [L6887](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6887) |
| `_MEMORY_REINDEX_KEYS` | `set[tuple[str, str]]` | `set()` | [L6888](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6888) |
| `_MEMORY_REINDEX_KEYS_LOCK` | `未显式标注` | `threading.Lock()` | [L6889](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6889) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: str \| None = None, agent_id: str \| None = None, service_id: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L2005](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2005) |
| `def _skill_envs_provider(self) -> dict[str, dict[str, str]]` | 源码未提供方法级文档字符串。 | [L2222](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2222) |
| `def _schedule_runtime_state_write(self, *, mode: str, language: str, channel: str, session_id: str \| None, project_dir: str \| None) -> None` | Persist diagnostic Git/runtime state without delaying chat handling. | [L2227](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2227) |
| `def set_skill_manager(self, skill_manager: SkillManager) -> None` | Inject shared SkillManager from facade for tool reuse. | [L2279](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2279) |
| `def _resolve_skill_dirs(self, extra_skill_dir: str \| None = None) -> list[str]` | 解析 SkillUseRail / evolution 使用的 skills 目录列表. | [L2285](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2285) |
| `@staticmethod def _session_adapter_key(session_id: str \| None) -> str` | 源码未提供方法级文档字符串。 | [L2301](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2301) |
| `def copy_tenant_env_bindings_from(self, source: 'JiuWenSwarmDeepAdapter') -> None` | Copy tenant tip namespace bindings from another adapter instance. | [L2305](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2305) |
| `def _bind_checkpointer_to_rails(self) -> None` | Wire adapter-local checkpointer into rails (align with test/jiuwenclaw). | [L2319](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2319) |
| `def _get_adapter_checkpointer(self) -> Any` | Prefer instance checkpointer; fall back to process default only if unset. | [L2326](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2326) |
| `def _new_session_scoped_adapter(self, session_id: str) -> 'JiuWenSwarmDeepAdapter'` | Create a child adapter that owns one DeepAgent for a single session. | [L2332](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2332) |
| `def mark_as_session_scoped(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L2350](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2350) |
| `def _get_cached_session_adapter(self, session_id: str \| None) -> 'JiuWenSwarmDeepAdapter \| None'` | 源码未提供方法级文档字符串。 | [L2354](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2354) |
| `def _iter_session_adapters_for_reload(self, target_session_id: str \| None = None) -> list[tuple[str, 'JiuWenSwarmDeepAdapter']]` | 源码未提供方法级文档字符串。 | [L2358](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2358) |
| `def _touch_session_adapter(self, session_id: str \| None) -> None` | 源码未提供方法级文档字符串。 | [L2370](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2370) |
| `def _drop_session_adapter_cache_entry(self, session_id: str, *, remove_lock: bool = True, remove_runtime_state: bool = True) -> None` | 源码未提供方法级文档字符串。 | [L2373](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2373) |
| `def _mark_session_adapters_stale_for_reload(self, config_base: dict[str, Any], env_overrides: dict[str, Any] \| None) -> None` | 源码未提供方法级文档字符串。 | [L2398](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2398) |
| `async def _reload_session_adapter_if_stale(self, session_id: str, adapter: 'JiuWenSwarmDeepAdapter') -> None` | 源码未提供方法级文档字符串。 | [L2416](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2416) |
| `async def _evict_idle_session_adapters(self) -> None` | 源码未提供方法级文档字符串。 | [L2463](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2463) |
| `async def cleanup_session_adapter(self, session_id: str \| None) -> bool` | Release an idle session-scoped adapter without deleting session history. | [L2490](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2490) |
| `def _is_session_lock_idle(self, sid: str, lock: asyncio.Lock) -> bool` | Check whether the session lock is the current one and has no active holders or waiters. | [L2541](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2541) |
| `@staticmethod def _session_adapter_lock_has_waiters(lock: asyncio.Lock) -> bool` | 源码未提供方法级文档字符串。 | [L2550](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2550) |
| `async def _get_or_create_session_adapter(self, session_id: str \| None, *, request: AgentRequest \| None = None) -> 'JiuWenSwarmDeepAdapter'` | Return the session-owned adapter, creating and initializing it once. | [L2555](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2555) |
| `@staticmethod def _get_a2x_config(config_base: dict[str, Any]) -> dict[str, Any]` | Resolve A2X config from ``react.a2x_registry`` with safe defaults. | [L2618](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2618) |
| `def _sync_a2x_runtime_state(self) -> None` | Expose A2X runtime state on the underlying DeepAgent instance. | [L2622](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2622) |
| `def _mark_session_active(self, session_id: str) -> None` | Increment the active-task count for *session_id*. | [L2636](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2636) |
| `def _unmark_session_active(self, session_id: str, *, cleanup_rail: bool = True) -> None` | Decrement the active-task count for *session_id*; remove when zero. | [L2641](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2641) |
| `def _is_session_active(self, session_id: str) -> bool` | Return True if at least one task is running for *session_id*. | [L2663](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2663) |
| `def is_session_active(self, session_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L2670](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2670) |
| `def set_working_checker(self, checker: Callable[[], bool] \| None) -> None` | Inject callable returning whether this facade/session has in-flight work. | [L2673](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2673) |
| `def _adapter_is_working(self) -> bool` | 源码未提供方法级文档字符串。 | [L2677](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2677) |
| `def _bind_request_env_overlay(self, env_overrides: dict[str, Any] \| None = None) -> tuple[Any, Any, Any]` | Seal Track-B reads for the current request (formula B tip ± extras). | [L2691](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2691) |
| `def _reset_request_env_bindings(self, ns_token: Any, overlay_token: Any, wk_token: Any = None) -> None` | 源码未提供方法级文档字符串。 | [L2727](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2727) |
| `async def _maybe_apply_pending_reload(self) -> ReloadResult \| None` | 源码未提供方法级文档字符串。 | [L2755](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2755) |
| `def queue_pending_reload(self, config_base: dict[str, Any] \| None, env_overrides: dict[str, Any] \| None) -> None` | Defer a config reload until this adapter is idle. | [L2778](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2778) |
| `@staticmethod def supports_pending_reload() -> bool` | Whether this adapter can defer reload while busy. | [L2787](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2787) |
| `async def apply_pending_reload_if_idle(self) -> ReloadResult \| None` | Apply deferred reload when harness has verified no inflight work. | [L2791](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2791) |
| `def has_session_runtime(self, session_id: str \| None = None) -> bool` | Return whether this adapter still owns session runtime. | [L2846](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2846) |
| `def _session_has_registered_tasks(self, session_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L2867](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2867) |
| `def _deep_agent_loop_session_id(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L2871](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2871) |
| `async def _clear_pending_ask_user_interrupt_for_supplement(self, session_id: str \| None) -> bool` | Drop a superseded pure ask_user round without leaving an open tool call. | [L2895](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2895) |
| `def _is_deep_agent_executing_for_session(self, session_id: str) -> bool` | True when the shared DeepAgent still runs stream/task-loop work for *session_id*. | [L2972](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2972) |
| `def is_deep_agent_executing_for_session(self, session_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L2985](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2985) |
| `def _is_session_live(self, session_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L2988](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2988) |
| `@staticmethod def _is_related_session(target_sid: str, other_sid: str) -> bool` | Return True when *other_sid* belongs to the same session tree as *target_sid*. | [L2996](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2996) |
| `def _other_active_sessions(self, session_id: str) -> int` | Return live tasks for sessions unrelated to *session_id*. | [L3011](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3011) |
| `async def _halt_deep_agent_execution(self, reason: str) -> None` | Cooperatively abort DeepAgent and cancel in-flight scheduler tasks. | [L3027](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3027) |
| `def _register_session_agent_task(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L3072](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3072) |
| `def _unregister_session_agent_task(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L3079](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3079) |
| `async def _cancel_session_agent_tasks(self, session_id: str) -> int` | 源码未提供方法级文档字符串。 | [L3091](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3091) |
| `def _clear_a2x_runtime_state(self) -> None` | Remove exposed A2X runtime state from the underlying DeepAgent instance. | [L3115](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3115) |
| `async def _close_a2x_client(self) -> None` | Close the mounted A2X client if initialized. | [L3131](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3131) |
| `async def _init_a2x_client(self, config_base: dict[str, Any]) -> None` | Initialize and mount AsyncA2XRegistryClient on the adapter instance. | [L3166](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3166) |
| `async def _try_init_a2x_client(self, config_base: dict[str, Any], *, reload: bool = False) -> None` | Best-effort A2X client init that never blocks agent startup. | [L3177](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3177) |
| `@staticmethod def _is_acp_tool_profile(config: dict[str, Any] \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L3237](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3237) |
| `def _filesystem_rail_enabled_for_profile(self) -> bool` | 源码未提供方法级文档字符串。 | [L3246](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3246) |
| `def _task_execution_rail_enabled(self) -> bool` | Whether TaskExecutionRail (task.start/complete/update events) is enabled. | [L3250](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3250) |
| `def _skill_include_tools_for_profile(self) -> bool` | 源码未提供方法级文档字符串。 | [L3262](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3262) |
| `@staticmethod def _resolve_prompt_channel(session_id: str \| None = None) -> str` | Resolve prompt channel from session id. | [L3268](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3268) |
| `@staticmethod def _resolve_prompt_language() -> str` | Resolve configured prompt language for builder input. | [L3281](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3281) |
| `def _resolve_runtime_language(self) -> str` | Resolve normalized runtime language shared by rails and tools. | [L3286](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3286) |
| `def _resolve_model_name(self) -> str` | Resolve current model name from model request config. | [L3290](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3290) |
| `def _resolve_session_git_snapshot(self, project_dir: str, session_id: str \| None, head_file: str, run_git: Callable[[list[str]], str]) -> _GitSnapshot` | Return this conversation's git snapshot, re-taking it if HEAD moved. | [L3296](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3296) |
| `def _write_runtime_state(self, mode: str, language: str, channel: str, *, session_id: str \| None = None, project_dir: str \| None = None) -> None` | 将当前运行时状态写入 config 目录下按 session 隔离的 runtime_state 文件。 | [L3347](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3347) |
| `@staticmethod def _browser_runtime_enabled() -> bool` | Whether browser runtime support is enabled for DeepAgent subagent wiring. | [L3419](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3419) |
| `@staticmethod def _resolve_managed_browser_binary_from_config(config_base: dict[str, Any] \| None = None) -> str` | Resolve managed-browser binary from saved browser config. | [L3433](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3433) |
| `@staticmethod def _resolve_headless_from_config(config_base: dict[str, Any] \| None = None) -> bool` | Read browser.headless from config (default True = headless). | [L3465](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3465) |
| `def _sync_browser_runtime_environment(self, config_base: dict[str, Any] \| None = None) -> None` | Synchronize browser launch settings before browser runtimes are built. | [L3482](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3482) |
| `@staticmethod def _is_subagent_enabled(subagent_cfg: Any) -> bool` | Treat only explicit `enabled: true` as enabled. | [L3517](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3517) |
| `@staticmethod def _is_subagent_default_enabled(subagent_cfg: Any) -> bool` | Default-enabled subagent: enabled unless explicitly set to false. | [L3522](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3522) |
| `def _build_configured_subagents(self, model: Model, config: dict[str, Any], config_base: dict[str, Any] \| None = None) -> tuple[list[Any] \| None, bool]` | Build configured research + browser subagents (agent 模式). | [L3528](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3528) |
| `@staticmethod def _build_mcp_server_config(entry: dict[str, Any]) -> McpServerConfig \| None` | 源码未提供方法级文档字符串。 | [L3646](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3646) |
| `@staticmethod def _extract_enabled_mcp_server_entries(config_base: dict[str, Any]) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L3651](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3651) |
| `async def _register_mcp_server(self, cfg: McpServerConfig, *, tag: str) -> bool` | 源码未提供方法级文档字符串。 | [L3654](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3654) |
| `async def _unregister_mcp_server(self, server_id: str) -> None` | 源码未提供方法级文档字符串。 | [L3717](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3717) |
| `async def register_request_scoped_office_claw_mcp(self, request: AgentRequest) -> OfficeClawMcpRegistration \| None` | Install Relay's legacy OfficeClaw MCP tools for one request only. | [L3734](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3734) |
| `def _owned_office_claw_tool_ids(self) -> frozenset[str]` | 源码未提供方法级文档字符串。 | [L3968](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3968) |
| `def _install_office_claw_ability_card(self, card: ToolCard) -> None` | Register a request-scoped OfficeClaw tool card by short name. | [L3974](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L3974) |
| `async def cleanup_request_scoped_office_claw_mcp(self, registration: OfficeClawMcpRegistration \| None) -> None` | Best-effort removal of tools installed for one Relay request. | [L4047](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4047) |
| `def _sync_office_claw_allowlist_to_progressive_rail(self, tool_ids: tuple[str, ...] \| list[str] \| frozenset[str] \| None) -> None` | Keep ProgressiveToolRail's interaction-round allowlist in sync. | [L4125](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4125) |
| `async def _register_mcp_servers_from_config(self, config_base: dict[str, Any], *, tag: str = 'agent.main') -> None` | 源码未提供方法级文档字符串。 | [L4144](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4144) |
| `async def _sync_mcp_servers_for_runtime(self, config_base: dict[str, Any], *, tag: str = 'agent.reload') -> None` | 源码未提供方法级文档字符串。 | [L4158](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4158) |
| `@staticmethod def _build_vision_model_config(config_base: dict[str, Any]) -> VisionModelConfig \| None` | Build DeepAgent vision config from service config/env mapping. | [L4229](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4229) |
| `@staticmethod def _build_audio_model_config(config_base: dict[str, Any]) -> AudioModelConfig \| None` | Build DeepAgent audio config from service config/env mapping. | [L4254](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4254) |
| `@staticmethod def _build_video_model_config(config_base: dict[str, Any]) -> bool` | Build DeepAgent video config from service config/env mapping. | [L4302](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4302) |
| `@staticmethod def _build_image_gen_model_config(config_base: dict[str, Any]) -> bool` | Build DeepAgent image generation config from service config/env mapping. | [L4322](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4322) |
| `def _iter_runtime_audio_tools(self, agent_id: str \| None) -> list[Any]` | Return metadata-only audio tools unless a complete audio model is configured. | [L4332](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4332) |
| `@staticmethod def _mask_model_secret(value: Any) -> str` | 源码未提供方法级文档字符串。 | [L4347](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4347) |
| `def _resolve_default_model_mcc_for_log(self) -> tuple[dict[str, Any], dict[str, str]]` | 与 ``_create_model`` 同源解析 default 槽位（``get_default_models`` + react 回退）。 | [L4355](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4355) |
| `def _collect_default_model_log_fields(self) -> dict[str, str]` | 收集 default 槽位字段，供启动日志单行输出（空值保留为占位）。 | [L4387](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4387) |
| `def _format_active_model_startup_log(self) -> str` | 仅输出 default 槽位模型配置（启动日志）。 | [L4415](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4415) |
| `def _log_active_model_on_startup(self, *, phase: str = 'create_instance') -> None` | 源码未提供方法级文档字符串。 | [L4435](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4435) |
| `def _merge_enterprise_models_into_config(self, config_base: dict[str, Any]) -> dict[str, Any]` | 若已加载 ``_enterprise_config``，将其模型槽位覆盖到 config 快照上。 | [L4442](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4442) |
| `async def _load_enterprise_config(self, request: AgentRequest) -> None` | 按当前请求的 ``params`` 从 Gateway DB 加载生效企业策略到 ``self._enterprise_config``。 | [L4467](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4467) |
| `async def prepare_skill_source_config(self, request: AgentRequest) -> None` | Load the effective policy before a source RPC, even before chat startup. | [L4515](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4515) |
| `def _inject_extension_config_into_inputs(self, inputs: dict[str, Any]) -> None` | 将企业策略中的 extension_config 注入 inputs（替代 ee gateway channel_context 透传）。 | [L4519](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4519) |
| `def _refresh_multimodal_configs(self, config_base: dict[str, Any]) -> None` | Refresh cached multimodal configs and live tool instances. | [L4549](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4549) |
| `def _sync_tool_group(self, *, current_tools: list[Any], registered: bool, enabled: bool, create_fn: Callable[[], list[Any]], warn_label: str) -> tuple[list[Any], bool]` | 统一处理一组工具的热更新：启用时注册，禁用时移除。 | [L4571](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4571) |
| `def _remove_registered_tools(self, tools: list[Any]) -> None` | Remove tool instances from ability manager and resource manager. | [L4617](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4617) |
| `def _append_tool_card(self, card: ToolCard) -> None` | Append tool card if it is not already tracked. | [L4647](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4647) |
| `def _prioritize_paid_search_tool_card(self) -> None` | Keep paid_search before free_search when both cards are present. | [L4657](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4657) |
| `def _prune_tool_cards(self, tool_names: set[str]) -> None` | Remove tracked tool cards by tool name. | [L4683](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4683) |
| `def _drop_tool_names_from_runtime(self, tool_names: set[str] \| frozenset[str]) -> None` | Best-effort removal for tool cards that may predate tracked tool instances. | [L4693](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4693) |
| `def _create_skill_retrieval_tools(self) -> list[Any]` | Create Agentic skill retrieval tools using the current visible-skill provider. | [L4723](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4723) |
| `@staticmethod def _skill_retrieval_tools_enabled_for_runtime(config_base: dict[str, Any] \| None = None) -> bool` | Return whether runtime tool sync should expose Agentic skill retrieval tools. | [L4745](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4745) |
| `def _sync_skill_retrieval_tools_for_runtime(self, config_base: dict[str, Any] \| None = None) -> None` | Sync Agentic skill retrieval tool registration after config reload. | [L4751](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4751) |
| `async def _sync_skill_retrieval_prompt_rail_for_runtime(self, config_base: dict[str, Any] \| None = None) -> None` | Sync Agentic skill retrieval prompt rail after config reload. | [L4769](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4769) |
| `def _sync_multimodal_tools_for_runtime(self) -> None` | Sync multimodal tool registration after config reload. | [L4810](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4810) |
| `def _tools_in_ability_manager(self, tools: list[Any]) -> bool` | 源码未提供方法级文档字符串。 | [L4859](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4859) |
| `def _sync_preinstance_runtime_tools_to_ability_manager(self) -> None` | Sync tools registered before DeepAgent existed into ability_manager. | [L4871](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4871) |
| `def _sync_paid_search_tool_for_runtime(self) -> None` | Legacy hook: unified ``web_search`` replaces separate paid-search sync. | [L4902](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4902) |
| `def _sync_symphony_tools_for_runtime(self, config_base: dict[str, Any]) -> None` | Sync Symphony tool registration after config reload. | [L4907](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4907) |
| `def _tenant_disk_ids(self) -> tuple[str, str]` | Return ``(service_id, agent_id)`` for on-disk tenant paths. | [L4925](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4925) |
| `async def set_checkpoint(self) -> None` | Create / reuse a per-agent sqlite checkpointer under ``workspace_{key}/.checkpoint``. | [L4942](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L4942) |
| `@classmethod def _normalize_reload_value(cls, value: Any) -> Any` | Normalize config-like values for stable hot-reload comparisons. | [L5009](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5009) |
| `@classmethod def _stable_reload_fingerprint(cls, value: Any) -> str` | 源码未提供方法级文档字符串。 | [L5041](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5041) |
| `@classmethod def _model_reload_fingerprint(cls, deep_cfg: Any \| None) -> str \| None` | 源码未提供方法级文档字符串。 | [L5047](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5047) |
| `@classmethod def _system_prompt_reload_fingerprint(cls, deep_cfg: Any \| None) -> str \| None` | 源码未提供方法级文档字符串。 | [L5066](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5066) |
| `def _previous_model_reload_fingerprint(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L5080](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5080) |
| `def _previous_system_prompt_reload_fingerprint(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L5086](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5086) |
| `def _omit_unchanged_reload_fields(self, deep_cfg: DeepAgentConfig) -> tuple[dict[str, Any], dict[str, str]]` | 源码未提供方法级文档字符串。 | [L5092](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5092) |
| `@staticmethod def _restore_omitted_reload_fields(deep_cfg: DeepAgentConfig, omitted_fields: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L5126](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5126) |
| `def _commit_reload_fingerprints(self, reload_fingerprints: dict[str, str]) -> None` | 源码未提供方法级文档字符串。 | [L5133](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5133) |
| `@staticmethod def _build_model_from_entry(mcc: dict, mco: dict) -> Model` | 根据单个模型条目的 model_client_config / model_config_obj 构建 Model 实例。 | [L5142](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5142) |
| `def _register_model_cache_entry(self, entry: dict[str, Any], name_counter: dict[str, int]) -> None` | Register one model entry into the request-selectable model cache. | [L5204](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5204) |
| `def _build_model_cache_from_defaults(self, config: dict) -> None` | 从 models.defaults 列表构建模型缓存。 | [L5249](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5249) |
| `def _build_model_cache_legacy(self, config: dict) -> None` | 回退到旧格式（models.default / react 段）构建单条目缓存。 | [L5262](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5262) |
| `@staticmethod def _inject_attribution_to_config(config: dict) -> None` | Inject OpenRouter attribution headers into all model_client_config entries in-place. | [L5290](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5290) |
| `def _create_model(self, config: dict) -> Model` | 源码未提供方法级文档字符串。 | [L5295](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5295) |
| `@staticmethod def _models_config_fingerprint(config: dict) -> str` | 计算模型配置段的指纹，用于判断是否需要重建模型缓存。 | [L5345](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5345) |
| `def _resolve_model_by_name(self, requested_model_name: str = '') -> Model \| None` | Resolve the exact model object that will be used. | [L5389](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5389) |
| `def _lookup_model_by_name(self, requested_model_name: str = '') -> Model \| None` | Look up a model by exact name/alias without falling back to default. | [L5408](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5408) |
| `def _resolve_model(self, *, model_name: str = '', model_tier: str = '') -> tuple[Model, str \| None]` | Resolve Model by model_name or model_tier; fall back to adapter default. | [L5423](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5423) |
| `def _resolve_model_for_subagent(self, *, model_name: str = '', model_tier: str = '') -> tuple[Model, str \| None]` | TaskTool / sessions_spawn model selection entrypoint. | [L5471](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5471) |
| `def _bind_subagent_model_resolver(self) -> None` | Expose adapter resolve on the DeepAgent instance for TaskTool. | [L5480](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5480) |
| `def _resolve_model_for_request(self, request: AgentRequest) -> Model` | 根据请求中的 model_name 参数查找对应模型（支持别名），未匹配则回退默认模型。 | [L5486](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5486) |
| `@staticmethod def _prepare_multimodal_image_inputs(request: AgentRequest, inputs: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L5500](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5500) |
| `@staticmethod def _prepare_react_image_tool_prompt(request: AgentRequest, inputs: dict[str, Any], *, enable_read_image_multimodal: bool) -> dict[str, Any]` | Add image file paths to the ReAct prompt when native image input is off. | [L5522](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5522) |
| `@staticmethod def _build_image_tool_fallback_notice(request: AgentRequest, *, enable_read_image_multimodal: bool, model: Any \| None) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L5593](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5593) |
| `def _native_image_input_enabled(self, config: dict[str, Any], model: Any \| None) -> bool` | 源码未提供方法级文档字符串。 | [L5627](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5627) |
| `@staticmethod def _resolve_enable_read_image_multimodal(config: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L5633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5633) |
| `def _apply_model_to_react_agent(self, model: Model) -> None` | 将指定模型应用到 react_agent 实例（替换 _llm 和 _config 字段）。 | [L5639](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5639) |
| `@staticmethod def _resolve_skill_mode(config: dict[str, Any]) -> str` | Validate configured skill mode and fallback safely on invalid values. | [L5672](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5672) |
| `def _visible_skill_names_for_list_skill(self) -> set[str]` | Return the skill names exposed by the matching SkillUseRail setup. | [L5691](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5691) |
| `@staticmethod def _build_response_prompt_rail() -> ResponsePromptRail \| None` | Build ResponsePromptRail so message rules keep priority ordering. | [L5732](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5732) |
| `def _create_sandbox_sys_operation(self, sandbox_url: str, sandbox_type: str, *, runtime: dict[str, Any] \| None = None, project_dir: str \| None = None) -> SysOperationCard \| None` | Create a sandbox SysOperationCard. | [L5742](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5742) |
| `def _resolve_project_dir_for_sandbox(self) -> str \| None` | Best-effort lookup of the user project directory for sandbox builds. | [L5793](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5793) |
| `@staticmethod def _sys_operation_isolation_key(sysop_card: SysOperationCard) -> str \| None` | 源码未提供方法级文档字符串。 | [L5815](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5815) |
| `@staticmethod def _get_registered_sys_operation_by_isolation_key(isolation_key_template: str \| None) -> SysOperation \| None` | 源码未提供方法级文档字符串。 | [L5827](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5827) |
| `def _create_sys_operation(self) -> SysOperation \| None` | Resolve this adapter's sys operation and take a reference on it. | [L5850](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5850) |
| `def _retain_sys_operation(self, sys_operation_id: str) -> None` | Record one adapter-held reference on a registered sys operation. | [L5870](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5870) |
| `def _release_sys_operations(self, sys_operation_ids: list[str] \| None = None) -> None` | Drop adapter-held references and unregister the ones left unused. | [L5882](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5882) |
| `def _resolve_sys_operation(self) -> SysOperation \| None` | Create a sys operation. | [L5947](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L5947) |
| `async def apply_sandbox_runtime_patch(self, runtime: dict[str, Any], *, files_changed: bool) -> None` | 轻量级热更新沙箱 runtime 参数（无需重建 agent）. | [L6020](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6020) |
| `@staticmethod def _build_filesystem_rail() -> SysOperationRail \| None` | Build SysOperationRail. | [L6160](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6160) |
| `@staticmethod def _get_active_package_config_paths() -> list[str]` | Read harness-packages.json to get config_path from active packages. | [L6171](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6171) |
| `async def _load_active_packages(self) -> list[str]` | Load all active packages via load_harness_config. | [L6212](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6212) |
| `async def apply_package_change(self, operation: str, config_path: str) -> list[str] \| None` | Load/unload a single harness package on this adapter's DeepAgent. | [L6248](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6248) |
| `async def apply_package_change_to_session_adapters(self, operation: str, config_path: str) -> None` | Propagate a harness package change to every live session adapter. | [L6275](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6275) |
| `def _build_skill_rail(self, config: dict[str, Any], include_tools: bool = False, extra_skill_dir: str \| None = None) -> SkillUseRail \| None` | Build SkillUseRail. | [L6304](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6304) |
| `def _skill_rail_session_id(self) -> str \| None` | Session id bound into skill rails for session-scoped adapters. | [L6350](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6350) |
| `def _build_skill_active_state_rail(self) -> SkillActiveStateRail \| None` | 源码未提供方法级文档字符串。 | [L6366](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6366) |
| `def _build_skill_credential_injection_rail(self, config: dict[str, Any]) -> SkillCredentialInjectionRail \| None` | 源码未提供方法级文档字符串。 | [L6382](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6382) |
| `def _build_skill_evolution_rail(self, config: dict[str, Any]) -> SkillEvolutionRail \| None` | Build SkillEvolutionRail. | [L6406](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6406) |
| `async def _ensure_active_evolution_rails_registered(self) -> None` | Configure, register, and cache single-agent skill evolution rails. | [L6443](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6443) |
| `async def refresh_enabled_skills_from_db(self) -> None` | workspace Skill 状态变更后刷新启用集并热替换 ``SkillUseRail``。 | [L6493](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6493) |
| `async def _unconfigure_active_evolution_rails(self) -> None` | Remove cached single-agent evolution rails before rebuilding them. | [L6583](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6583) |
| `def _refresh_active_evolution_rail_refs(self) -> None` | Refresh cached rail references after agent-core runtime configure. | [L6612](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6612) |
| `def _sync_active_evolution_review_agent_after_reload(self) -> None` | Restore SkillEvolutionRail-owned review subagent after DeepAgent hot reload. | [L6633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6633) |
| `def _build_skill_create_rail(self, config: dict[str, Any]) -> SkillCreateRail \| None` | Build SkillCreateRail for new skill creation proposals. | [L6650](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6650) |
| `@staticmethod def _build_stream_event_rail() -> JiuSwarmStreamEventRail \| None` | Build JiuSwarmStreamEventRail. | [L6687](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6687) |
| `def _build_deepresearch_execution_rail(self) -> DeepResearchExecutionRail \| None` | Build the native HITL bridge for deepresearch_execute. | [L6697](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6697) |
| `@staticmethod def _build_context_overflow_recovery_rail() -> ContextOverflowRecoveryRail \| None` | Build ContextOverflowRecoveryRail. | [L6715](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6715) |
| `@staticmethod def _build_task_execution_rail() -> TaskExecutionRail \| None` | Build TaskExecutionRail for task.start/complete/update lifecycle events. | [L6726](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6726) |
| `@staticmethod def _build_request_summary_rail() -> Any \| None` | Build RequestSummaryRail for per-request performance summaries. | [L6737](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6737) |
| `@staticmethod def _build_extension_config_debug_rail() -> Any \| None` | Build ExtensionConfigDebugRail for extension config end-to-end debugging. | [L6754](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6754) |
| `@staticmethod def _load_extra_rails_from_env() -> list[Any]` | Load extra DeepAgentRails from AGENT_EXTRA_RAILS env var. | [L6770](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6770) |
| `@staticmethod def _build_multimodal_image_rail(enable_image_multimodal: bool \| None = None) -> MultimodalImageRail \| None` | Build MultimodalImageRail. | [L6815](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6815) |
| `def _build_task_planning_rail(self, config: dict[str, Any] \| None = None) -> TaskPlanningRail \| None` | Build TaskPlanningRail from ``react.task_planning`` config. | [L6829](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6829) |
| `@staticmethod def _build_subagent_rail() -> SubagentRail \| None` | Build SubagentRail for subagent delegation. | [L6856](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6856) |
| `def _build_structured_ask_user_rail(self) -> StructuredAskUserRail \| None` | Build StructuredAskUserRail for agent mode clarification. | [L6866](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6866) |
| `@staticmethod def _build_security_rail() -> SecurityRail \| None` | Build SecurityPromptRail. | [L6875](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6875) |
| `@staticmethod def _embedding_config_fingerprint(config: dict \| None) -> str` | 计算 config.yaml embed 段的配置指纹，用于检测是否变化。 | [L6892](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6892) |
| `def _schedule_memory_reindex(self) -> None` | 延时后对记忆重新索引（debounce：多次触发只跑最后一次）。 | [L6913](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6913) |
| `async def _do_memory_reindex(self, reindex_key: tuple[str, str]) -> None` | 源码未提供方法级文档字符串。 | [L6936](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6936) |
| `async def _get_current_memory_manager(self)` | 获取当前 MemoryRail 对应的 memory manager（必要时按新配置重建）。 | [L6960](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6960) |
| `def _get_memory_workspace(self)` | 构造记忆用的 Workspace 对象（与 _make_deep_agent_config 中构造方式一致）。 | [L6985](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6985) |
| `def _build_memory_rail(self, mode: str) -> MemoryRail \| None` | 源码未提供方法级文档字符串。 | [L6990](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L6990) |
| `@staticmethod def _build_heartbeat_rail() -> HeartbeatRail \| None` | Build HeartbeatRail. | [L7021](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7021) |
| `@staticmethod def _build_avatar_rail() -> Any \| None` | Build AvatarPromptRail for digital avatar mode. | [L7032](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7032) |
| `@staticmethod def _build_llm_retry_rail(config_base: dict[str, Any] \| None = None) -> NotifyingLLMRetryRail \| None` | 源码未提供方法级文档字符串。 | [L7045](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7045) |
| `def _build_runtime_prompt_rail(self) -> RuntimePromptRail \| None` | Build RuntimePromptRail for per-model-call time/channel/runtime injection. | [L7071](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7071) |
| `def _build_skill_retrieval_prompt_rail(self) -> SkillRetrievalPromptRail \| None` | Build lightweight agentic skill retrieval prompt guidance. | [L7091](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7091) |
| `def _build_a2a_outbound_toolkit_rail(self) -> A2AOutboundToolkitRail \| None` | 源码未提供方法级文档字符串。 | [L7104](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7104) |
| `def _get_a2a_outbound_tool_route(self) -> tuple[str, str]` | Return an adapter-owned route that survives DeepAgent task boundaries. | [L7109](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7109) |
| `def _build_progressive_tool_rail(self, config: dict[str, Any]) -> ProgressiveToolRail \| None` | Build progressive tool rail from react.tool_lazy_load config. | [L7127](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7127) |
| `@staticmethod def _build_disabled_tools_rail(config: dict[str, Any]) -> DisabledToolsRail \| None` | Build DisabledToolsRail to filter out disabled tools based on config. | [L7145](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7145) |
| `def _get_deepresearch_tool_context(self) -> dict[str, str]` | Return the adapter-owned route that survives runner task boundaries. | [L7175](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7175) |
| `def _deepresearch_artifact_output_dir(self) -> str` | Return the tenant-owned root used for immutable report artifacts. | [L7212](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7212) |
| `async def refresh_skill_rails(self) -> None` | 轻量刷新 skill 相关 rail，避免全量重建 agent 实例. | [L7219](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7219) |
| `def _build_symphony_orchestration_rail(self) -> SymphonyOrchestrationRail \| None` | Build dynamic Symphony orchestration prompt guidance. | [L7236](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7236) |
| `def _instantiate_rails(self, rail_infos: list['_RailBuildInfo'], config_base: dict[str, Any]) -> list[Any]` | Build each declared rail in order, then attach the two standing ones. | [L7251](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7251) |
| `def _build_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any], *, mode: str = 'agent') -> list[Any]` | Build DeepAgent rails consistently for cold start and hot reload. | [L7342](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7342) |
| `@staticmethod def _resolve_enable_task_loop(config: dict[str, Any], config_base: dict[str, Any] \| None) -> bool` | Resolve enable_task_loop considering evolution rail requirements. | [L7498](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7498) |
| `def _make_deep_agent_config(self, *, model: Model, config: dict[str, Any], config_base: dict[str, Any] \| None = None, agent_card: AgentCard, tool_cards: list[Any], rails: list[Any] \| None = None) -> DeepAgentConfig` | 与 create_deep_agent() 中 DeepAgentConfig 构造保持一致. | [L7539](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7539) |
| `def _update_permission_rail(self, config_base: dict[str, Any] \| None, *, session_id: str \| None = None) -> None` | 原地更新已有 PermissionRail 配置，或在首次启用时新建。 | [L7600](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7600) |
| `def _resolve_permission_config_for_agent(self, *, session_id: str \| None = None) -> dict[str, Any]` | 解析本 Agent 生效 permissions：企业模板 body 优先，否则 yaml/DB 回落。 | [L7631](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7631) |
| `def _build_permission_rail_for_agent(self, config: dict[str, Any] \| None = None, llm: Any = None, model_name: str \| None = None) -> Any \| None` | 冷启动构建 permission rail：注入 Agent 级模板 body。 | [L7658](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7658) |
| `def _bind_agent_permissions_base(self) -> Any` | 将 Agent 模板 permissions body 绑定到当前 Task（供 snapshot/生效读路径）。 | [L7672](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7672) |
| `def _get_current_agent_rails(self, config: dict[str, Any], config_base: dict[str, Any] \| None = None) -> tuple[list[Any], list[Any]]` | Return rail replacements and rails to retire after configure. | [L7676](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7676) |
| `def _runtime_agent_scope_id(self) -> str` | Return AgentCard / tool-owner base id for this adapter. | [L7805](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7805) |
| `def _tool_owner_id(self) -> str` | Return the owner id qualifying this adapter's tool registrations. | [L7820](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7820) |
| `@staticmethod def _register_shared_tool(tool: Any) -> Any` | Declare a tool instance shared across adapters, then register it. | [L7846](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7846) |
| `@staticmethod def _register_agent_owned_tool(tool: Any, owner_id: str) -> Any` | Register a tool instance owned exclusively by this adapter's agent. | [L7867](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7867) |
| `def _register_deepresearch_tool_cards(self, tool_cards: list[Any]) -> None` | Register the formal DeepResearch surface as process-shared tools. | [L7896](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7896) |
| `async def _get_tool_cards(self, agent_id: str)` | Get tool cards. | [L7904](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L7904) |
| `def _build_cron_tools(self) -> list[Any]` | Build cron tools from the shared runtime bridge. | [L8111](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8111) |
| `async def _proc_context_compaction(self) -> None` | Backward-compatible no-op hook for tests and legacy call sites. | [L8121](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8121) |
| `def _skip_own_instance_build(self) -> bool` | Return whether ``create_instance`` should stop before building a DeepAgent. | [L8125](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8125) |
| `async def ensure_instance(self) -> Any` | Return this adapter's own DeepAgent, building it on first use. | [L8142](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8142) |
| `async def create_instance(self, config: dict[str, Any] \| None = None, *, mode: str = 'agent', sub_mode: str = None, config_base: dict[str, Any] \| None = None) -> None` | 初始化 DeepAgent 实例. | [L8174](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8174) |
| `def _register_extension_tools(self) -> None` | 将 ExtensionRegistry 登记的扩展本地工具挂到 Runner 与 ability_manager。 | [L8401](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8401) |
| `@staticmethod def _ensure_project_gitignore_agent_history(project_dir: str \| None) -> None` | Ensure JiuwenSwarm's file operation logs stay out of project git diffs. | [L8445](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8445) |
| `@staticmethod def _gitignore_covers_agent_history(content: bytes) -> bool` | Return True if .gitignore content already ignores .agent_history/. | [L8493](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8493) |
| `async def _sync_prompt_attachments_for_request(self, session_id: str) -> None` | Hot-load prompt attachment files for the current request. | [L8524](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8524) |
| `def _prompt_attachment_root(self) -> Path` | 源码未提供方法级文档字符串。 | [L8544](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8544) |
| `async def load_user_rails(self) -> None` | 动态加载用户自定义的 Rail 扩展. | [L8549](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8549) |
| `async def _apply_reload_config_snapshot(self, config_base: dict[str, Any] \| None, env_overrides: dict[str, Any] \| None) -> dict[str, Any]` | Refresh the cached config snapshot shared by every reload path. | [L8573](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8573) |
| `async def _fan_out_reload_to_session_adapters(self, config_base: dict[str, Any], env_overrides: dict[str, Any] \| None, target_sid: str \| None) -> None` | Cascade a config reload to the live per-session adapters. | [L8647](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8647) |
| `async def reload_agent_config(self, config_base: dict[str, Any] \| None = None, env_overrides: dict[str, Any] \| None = None, target_session_id: str \| None = None, *, _force_apply: bool = False) -> ReloadResult` | 从 config.yaml 重新加载配置，通过 DeepAgent.configure() 热更新当前实例（不新建 DeepAgent）。 | [L8685](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8685) |
| `def _bind_runtime_cron_context(self, *, channel_id: str \| None, session_id: str \| None, metadata: dict[str, Any] \| None, request_id: str \| None, mode: str \| None, project_dir: str \| None = None, params: dict[str, Any] \| None = None) -> _RuntimeCronContextTokens` | 源码未提供方法级文档字符串。 | [L8893](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L8893) |
| `@staticmethod def _reset_deepresearch_route_context(tokens: _RuntimeCronContextTokens) -> None` | Reset one DeepResearch route token at most once. | [L9026](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9026) |
| `@staticmethod def _run_cleanup_steps(steps: list[tuple[str, Callable[[], None]]]) -> BaseException \| None` | Run every cleanup step, returning the first failure without secrets. | [L9036](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9036) |
| `@classmethod def _reset_runtime_cron_context(cls, tokens: _RuntimeCronContextTokens, *, suppress_errors: bool = False) -> None` | 源码未提供方法级文档字符串。 | [L9055](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9055) |
| `def _reset_stream_runtime_context(self, tokens: _RuntimeCronContextTokens, *, stream_consumer_cancelled: bool) -> None` | Always release DeepResearch routing without changing legacy cancel cleanup. | [L9104](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9104) |
| `async def _update_rails_for_mode(self, mode: str) -> None` | 装配 agent 模式 rails。 | [L9116](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9116) |
| `async def _reconcile_evolution_rails(self) -> None` | Apply evolution rail configuration through its runtime owner. | [L9126](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9126) |
| `@staticmethod def _user_interaction_rail_attribute() -> str` | 源码未提供方法级文档字符串。 | [L9137](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9137) |
| `async def _set_user_interaction_enabled(self, enabled: bool) -> None` | Expose ``ask_user`` only when the requesting client can answer it. | [L9140](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9140) |
| `async def _update_agent_rails(self) -> None` | agent 模式：注册 agent 专属 rails（原 plan 档能力并集）。 | [L9164](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9164) |
| `@staticmethod def _acp_runtime_tools_enabled(request_metadata: dict[str, Any] \| None) -> tuple[bool, bool]` | 源码未提供方法级文档字符串。 | [L9244](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9244) |
| `async def _update_tools_for_mode(self, mode: str, session_id: str \| None, request_id: str \| None) -> None` | multi-session 工具装配。 | [L9281](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9281) |
| `def _ensure_cron_tools_registered(self, session_id: str \| None) -> None` | Register this agent's cron tools once, rebuilding only when they change. | [L9300](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9300) |
| `async def _update_session_tools(self, session_id: str \| None, request_id: str \| None, channel_id: str \| None = None) -> None` | 刷新每请求相关的 cron / send_file 工具运行时状态。 | [L9356](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9356) |
| `def _refresh_acp_runtime_tools(self, session_id: str \| None, request_id: str \| None, channel_id: str \| None, request_metadata: dict[str, Any] \| None) -> None` | Refresh ACP tools for the current request based on client capabilities. | [L9421](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9421) |
| `def _update_prompt_for_mode(self, mode: str, resolved_language: str) -> None` | 同步 system_prompt_builder 的语言。 | [L9492](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9492) |
| `def _seed_runtime_cwd(self, cwd: str \| None = None, workspace: str \| None = None) -> None` | Seed Core's CwdState holder from the request/runtime cwd. | [L9499](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9499) |
| `@staticmethod def _extract_request_system_prompt(request: AgentRequest \| None) -> str \| None` | Read non-empty request.params.system_prompt. | [L9541](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9541) |
| `@staticmethod def _extract_request_interactive_ask(request: AgentRequest \| None) -> bool` | Guided mode is params.interactive_ask opt-in; never infer from HITL capability. | [L9553](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9553) |
| `async def configure_session_runtime(self, *, session_id: str, channel_id: str, mode: str, project_dir: str \| None = None) -> None` | Apply session-stable runtime state without request-bound capabilities. | [L9565](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9565) |
| `async def _update_runtime_config(self, runtime_config: '_RuntimeConfig') -> None` | Register per-request tools for current agent execution. | [L9589](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9589) |
| `async def _apply_runtime_config(self, runtime_config: '_RuntimeConfig', *, bind_request: bool) -> None` | 源码未提供方法级文档字符串。 | [L9604](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9604) |
| `async def _apply_runtime_config_stages(self, runtime_config: '_RuntimeConfig', stage_timer: StageTimer, *, bind_request: bool) -> None` | Run the per-request runtime setup, marking each stage as it completes. | [L9633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9633) |
| `@staticmethod def _should_register_acp_runtime_tools(channel_id: str \| None, request_id: str \| None, session_id: str \| None, has_runtime_capability: bool) -> bool` | 源码未提供方法级文档字符串。 | [L9863](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9863) |
| `async def start_interaction(self, session_id: str) -> None` | Bind a product Session and start this adapter's DeepAgent interaction loop. | [L9875](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9875) |
| `async def prepare_session(self, *, session_id: str, channel_id: str, mode: str, project_dir: str \| None = None) -> None` | Create a session child and apply stable runtime state without input. | [L9907](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9907) |
| `@staticmethod def _cron_session_has_context(inner_session: Any) -> bool` | 检测 AgentSession 的 global_state['context'] 是否已带 messages。 | [L9925](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9925) |
| `async def _persist_cron_checkpoint(self, session_id: str, request_id: str) -> None` | 非流式 cron 执行收尾补 commit：把 context_engine 内存 context 落盘到 checkpointer。 | [L9946](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9946) |
| `async def _persist_session_checkpoint(self, session_id: str, request_id: str) -> None` | 流式 chat 收尾补 commit：把 context_engine 内存 context 落盘到 checkpointer。 | [L9998](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L9998) |
| `def _init_skill_turbo_tool(self) -> None` | Initialize skill_turbo tool for SkillTurbo integration. | [L10051](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10051) |
| `@staticmethod def _build_skill_protocol_prompt_rail() -> Any \| None` | 构建 SkillProtocolPromptRail: 注入技能执行规范提示词。 | [L10087](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10087) |
| `@staticmethod def _build_skill_turbo_prompt_rail() -> Any \| None` | 构建 SkillTurboPromptRail: 注入 skill_acceleration_exec 使用指南。 | [L10101](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10101) |
| `def build_skill_turbo_config(self) -> dict[str, Any]` | 构建 SkillTurbo 配置. | [L10124](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10124) |
| `def _create_skill_turbo_fallback_handler(self) -> Any` | 创建 SkillTurbo 节点级 fallback handler。 | [L10148](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10148) |
| `async def spawn_fallback(self, query: str, parent_session: Any \| None = None) -> str` | 执行 SkillTurbo fallback：以隔离 subagent 跑一次任务，返回子代理输出文本。 | [L10159](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10159) |
| `@staticmethod def _suspend_subagent_permission_rails(subagent: Any) -> Callable[[], None]` | 临时禁用 fallback 子代理继承的权限审批 rail，返回恢复函数。 | [L10199](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10199) |
| `async def prepare_interrupt_artifacts_for_request(self, request: AgentRequest) -> None` | 兜底：中断后下一轮请求注入 SkillTurbo 节点产物摘要到 supplementary_info。 | [L10273](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10273) |
| `@staticmethod async def _read_skill_turbo_node_artifacts_summary(session: Any) -> str \| None` | 读取 SkillTurbo 节点产物记录，格式化为可读摘要文本。 | [L10385](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10385) |
| `@staticmethod def _build_skill_turbo_artifacts_summary(nodes: dict[str, Any]) -> list[str]` | 将节点产物 nodes 构建为可读摘要列表。 | [L10407](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10407) |
| `@staticmethod def _new_usage_accumulator() -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L10430](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10430) |
| `@staticmethod def _extract_usage_metadata(payload: Any) -> dict[str, Any] \| None` | Pull GLM/OpenAI usage numbers out of a SkillTurbo or adapter payload. | [L10442](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10442) |
| `@staticmethod def _accumulate_usage(acc: dict[str, Any], usage_meta: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L10459](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10459) |
| `@staticmethod def _extract_deepresearch_sdk_usage(payload: Any) -> tuple[str, dict[str, Any]] \| None` | 源码未提供方法级文档字符串。 | [L10466](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10466) |
| `@classmethod def _account_deepresearch_sdk_usage(cls, payload: Any, *, request_id: str, usage_accumulator: dict[str, Any], accounted_usage_ids: set[str]) -> bool` | 源码未提供方法级文档字符串。 | [L10510](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10510) |
| `@staticmethod def _rewrite_skill_turbo_usage_chunk(chunk: AgentResponseChunk, *, session_id: str) -> tuple[AgentResponseChunk \| None, dict[str, Any] \| None]` | Map SkillTurbo ``chat.llm_usage`` onto the adapter's usage_metadata event. | [L10530](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10530) |
| `@staticmethod def _format_llm_usage_summary(usage_accumulator: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L10567](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10567) |
| `def _context_usage_fields(self, session_id: str) -> tuple[float \| None, int \| None]` | 源码未提供方法级文档字符串。 | [L10586](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10586) |
| `def _log_and_make_usage_summary_chunk(self, *, request_id: str, channel_id: str, session_id: str, usage_accumulator: dict[str, Any], perf_usage_fallback: dict[str, int] \| None = None) -> AgentResponseChunk \| None` | 源码未提供方法级文档字符串。 | [L10621](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10621) |
| `def _deep_agent_has_skill_turbo_interrupt(self) -> bool` | True when DeepAgent still holds an outer skill_acceleration_exec HITL. | [L10665](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10665) |
| `async def _try_skill_turbo_resume(self, request: AgentRequest, inputs: dict[str, Any]) -> AsyncIterator[AgentResponseChunk] \| None` | 检测 resume 请求并走 SkillTurbo resume 路径。 | [L10688](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10688) |
| `def _make_skill_turbo_resume_stream(self, request: AgentRequest, inputs: dict[str, Any], session: Any, resume_ctx: dict[str, Any], answers: list) -> AsyncIterator[AgentResponseChunk] \| None` | 构造 resume 的流式 AsyncIterator。 | [L10750](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10750) |
| `@staticmethod def _skill_turbo_answers_to_confirm_payload(answers: list, resume_ctx: dict[str, Any]) -> Any` | 将前端 answers 转为 ConfirmPayload（权限审批）或结构化 answers（ask_user）。 | [L10908](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10908) |
| `@staticmethod async def _emit_skill_turbo_hitl_chunks(request: AgentRequest, abort_exc: Any) -> AsyncIterator[AgentResponseChunk]` | AbortError → HITL 三件套 chunk。 | [L10943](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L10943) |
| `async def stop_interaction(self) -> None` | Stop this adapter's DeepAgent interaction loop if it was started. | [L11012](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11012) |
| `async def cleanup(self) -> None` | Release adapter-owned external runtime resources. | [L11018](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11018) |
| `def _teardown_agent_owned_tools(self) -> None` | Drop this agent's stateful tool registrations from the global resource manager. | [L11065](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11065) |
| `def _collect_registered_ability_names(self) -> set[str]` | 源码未提供方法级文档字符串。 | [L11086](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11086) |
| `@staticmethod def _select_registered_runtime_tool_names(runtime_tool_candidates: tuple[str, ...], ability_names: set[str]) -> list[str]` | 源码未提供方法级文档字符串。 | [L11095](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11095) |
| `@staticmethod def _resolve_interrupt_session_id(session_id: str \| None) -> str` | 源码未提供方法级文档字符串。 | [L11106](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11106) |
| `async def _stop_session_interrupt_work(self, session_id: str \| None, *, intent: str, reset_for_new_task: bool = False) -> list[dict[str, Any]]` | Per-session teardown: rail abort, shell kill, cancelled tool collection. | [L11109](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11109) |
| `def _collect_cancelled_tools_for_session(self, session_id: str \| None, *, reset_for_new_task: bool = False) -> list[dict[str, Any]]` | Abort rail checkpoints and collect in-flight tools for *session_id*. | [L11151](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11151) |
| `@staticmethod def _append_cancelled_tools_to_history(request: AgentRequest, cancelled_tool_results: list[dict[str, Any]]) -> None` | Persist cancelled tool results so refresh does not leave spinners. | [L11176](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11176) |
| `def _has_active_goal_round(self) -> bool` | Whether DeepAgent is currently executing a goal round. | [L11208](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11208) |
| `def _instance_interaction_started(self) -> bool` | Prefer the public ``interaction_started`` flag; fall back for older SDKs. | [L11224](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11224) |
| `def _goal_record_is_active(self) -> bool` | Whether GoalRecord is ACTIVE (persistent objective still running). | [L11233](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11233) |
| `def _has_active_goal_interaction(self) -> bool` | Whether the shared DeepAgent still owns an active goal interaction. | [L11256](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11256) |
| `def _should_demote_goal_intermediate_final(self) -> bool` | Whether an attempt-boundary ``chat.final`` must become intermediate. | [L11262](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11262) |
| `def _current_interaction_run_kind(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L11281](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11281) |
| `def _begin_visible_chat_content(self, stream_is_user_originated: bool = False) -> dict[str, Any] \| None` | Stamp content provenance; inject a bubble-split final on user→goal. | [L11292](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11292) |
| `def _adapt_goal_intermediate_final(self, parsed: dict \| None) -> dict \| None` | 源码未提供方法级文档字符串。 | [L11324](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11324) |
| `def _should_emit_stream_end_chat_final(self, *, had_assistant_output: bool, emitted_terminal_chat_final: bool) -> bool` | Whether the host must synthesize a terminal ``chat.final``. | [L11336](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11336) |
| `@staticmethod def _approved_plan_exit_resume_tool_call_id(params: Any) -> str` | 返回已批准 plan-exit resume 对应的工具调用 ID。 | [L11360](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11360) |
| `def _plan_exit_fallback_content(self) -> str` | 源码未提供方法级文档字符串。 | [L11385](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11385) |
| `def _reasoning_only_empty_reply_fallback(self) -> str` | 源码未提供方法级文档字符串。 | [L11390](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11390) |
| `@staticmethod def _should_fill_reasoning_only_empty_final(parsed: dict[str, Any] \| None, *, had_reasoning_output: bool, visible_text_since_last_final: bool) -> bool` | Whether an empty chat.final should get the reasoning-only short reply. | [L11396](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11396) |
| `def _fill_reasoning_only_empty_final_if_needed(self, parsed: dict[str, Any] \| None, *, had_reasoning_output: bool, visible_text_since_last_final: bool) -> None` | 源码未提供方法级文档字符串。 | [L11413](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11413) |
| `@staticmethod def _iter_delta_for_unstreamed_final(*, content: str, chunk_payload: Any) -> list[dict[str, Any]]` | 终稿正文未被 chat.delta 真正流式送达时，构造补发 delta 的 payload 列表。 | [L11430](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11430) |
| `@staticmethod def _drain_final_content_to_deltas(parsed: dict[str, Any], *, segment_streamed_text: str, chunk_payload: Any) -> list[dict[str, Any]]` | Move non-empty ``chat.final`` body onto ``chat.delta`` when needed. | [L11453](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11453) |
| `@staticmethod def _apply_reasoning_only_empty_reply_fallback(*, has_streamed_content: bool, had_reasoning_output: bool, fallback_content: str, reasoning_only_fallback: str) -> str` | Fill empty terminal final content for reasoning-only completions. | [L11491](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11491) |
| `async def _reattach_interrupt_output(self, session_id: str) -> Any \| None` | Briefly wait for the interrupted consumer to release its output lease. | [L11512](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11512) |
| `@staticmethod def _resolve_input_dispatch_mode(params: Any) -> InputDispatchMode \| None` | Map host ``input_mode`` / ``runtime_mode`` onto OpenJiuwen dispatch mode. | [L11537](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11537) |
| `@staticmethod def _wants_attach_goal(params: Any) -> bool` | 源码未提供方法级文档字符串。 | [L11555](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11555) |
| `@staticmethod def _should_parse_tui_goal_slash(*, pending_goal_op: dict[str, Any] \| None, attach_goal_request: bool, channel_id: Any, query: Any) -> bool` | Whether to parse chat text ``/goal ...`` (TUI only, when no structured op). | [L11559](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11559) |
| `@staticmethod def _parse_goal_slash_intent(query: str) -> dict[str, Any] \| None` | Parse ``/goal ...`` into an action dict without touching GoalManager. | [L11574](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11574) |
| `def _is_ack_only_dispatch(self, params: Any) -> bool` | Steer / follow_up prefer an existing reader when one is present. | [L11591](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11591) |
| `@staticmethod def _is_interrupt_resume_dispatch(params: Any) -> bool` | HITL answers must inject into the existing interaction when possible. | [L11597](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11597) |
| `def _should_inject_into_existing_interaction(self, params: Any) -> bool` | Whether input must be sent even when this request cannot take the lease. | [L11628](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11628) |
| `@staticmethod def _structured_goal_op_from_request(request: AgentRequest) -> dict[str, Any] \| None` | Map streaming ``command.goal`` set/resume onto the attach→control path. | [L11635](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11635) |
| `async def _abort_shared_agent_if_safe(self, normalized_sid: str, intent: str) -> None` | Global DeepAgent/scheduler abort when safe for unrelated sessions. | [L11664](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11664) |
| `async def process_interrupt(self, request: AgentRequest) -> AgentResponse` | 处理 interrupt 请求. | [L11697](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11697) |
| `async def _process_interaction_interrupt(self, request: 'AgentRequest', intent: str, new_input: Any) -> 'AgentResponse'` | Handle cancel/supplement for an interaction-managed session. | [L11916](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L11916) |
| `def _cancel_scheduler_running_tasks(self) -> None` | Cancel in-flight asyncio.Tasks in the Controller's TaskScheduler. | [L12015](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12015) |
| `async def abort_on_gateway_disconnect(self) -> None` | Gateway 与 AgentServer 的 WebSocket 断开时：与 interrupt(cancel) 同样中止 rail 与 DeepAgent 实例。 | [L12048](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12048) |
| `@staticmethod def _model_looks_usable(model: Model \| None) -> bool` | 源码未提供方法级文档字符串。 | [L12087](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12087) |
| `def _has_valid_model_config(self, requested_model_name: str = '') -> bool` | 检查本次请求实际会使用的模型配置是否有效。 | [L12099](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12099) |
| `@classmethod def is_working(cls, session_tasks: dict[str, asyncio.Task], session_queues: dict[str, asyncio.PriorityQueue]) -> bool` | 返回 Agent 是否正在工作. | [L12106](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12106) |
| `async def handle_user_answer(self, request: AgentRequest) -> AgentResponse` | Handle chat.user_answer request. | [L12155](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12155) |
| `async def handle_swarmflow_reply(self, request: AgentRequest) -> AgentResponse` | Handle chat.swarmflow_reply — deliver a person's reply to a human turn. | [L12206](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12206) |
| `@staticmethod def _is_interrupt_skill_evolution_approval_params(request_id: str, params: Any) -> bool` | 源码未提供方法级文档字符串。 | [L12292](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12292) |
| `@staticmethod def _is_regular_skill_evolution_approval_params(params: Any) -> bool` | 源码未提供方法级文档字符串。 | [L12304](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12304) |
| `async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse \| None` | Handle heartbeat request. Returns None to continue normal flow. | [L12324](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12324) |
| `async def _handle_evolution_approval(self, request_id: str, answers: list) -> bool` | Handle evolution approval via SkillEvolutionRail.on_approve/on_reject. | [L12373](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12373) |
| `@staticmethod def find_team_skill_rail(request_id: str, channel_id: str \| None = None)` | Find TeamSkillEvolutionRail that owns the given pending request_id. | [L12424](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12424) |
| `async def handle_team_skill_evolve_approval(self, request_id: str, answers: list, session_id: str \| None = None, channel_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L12438](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12438) |
| `async def _handle_team_simplify_approval(self, request_id: str, answers: list, session_id: str \| None = None, channel_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L12484](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12484) |
| `async def _handle_simplify_approval(self, request_id: str, answers: list, session_id: str \| None, channel_id: str \| None, evolution_meta: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L12513](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12513) |
| `@staticmethod async def _push_team_skill_evolve_resolution_status(request_id: str, *, session_id: str \| None, channel_id: str \| None, accepted: bool) -> None` | Close the frontend evolution status after a team skill approval is resolved. | [L12542](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12542) |
| `@staticmethod def _approval_chunk_from_event(event: Any) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L12583](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12583) |
| `@staticmethod def _format_approval_summary(*, skill_name: str, questions: list[Any], action_label: str) -> str` | 源码未提供方法级文档字符串。 | [L12596](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12596) |
| `def _approval_response_from_event_or_records(self, *, skill_name: str, event: Any, records: list[Any], action_label: str, no_changes_output: str, invalid_output: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L12607](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12607) |
| `def _approval_response_from_simplify_result(self, *, skill_name: str, simplify_result: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L12633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12633) |
| `def _approval_response_from_evolve_result(self, *, skill_name: str, evolve_result: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L12648](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12648) |
| `async def _handle_governance_approval(self, request_id: str, answers: list, kind: str) -> bool` | Unified handler for simplify governance approvals. | [L12663](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12663) |
| `def _get_disk_evolution_store(self)` | 源码未提供方法级文档字符串。 | [L12695](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12695) |
| `def _should_auto_merge_evolved_skill(self, skill_name: str) -> bool` | Return True when per-skill selfEvolution resolves to ``auto``. | [L12698](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12698) |
| `def _queue_auto_rebuild_skill(self, skill_name: str) -> None` | 源码未提供方法级文档字符串。 | [L12713](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12713) |
| `def _take_pending_auto_rebuild_skills(self) -> list[str]` | 源码未提供方法级文档字符串。 | [L12722](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12722) |
| `async def handle_skills_evolution_archives(self, params: dict) -> dict[str, Any]` | RPC: skills.evolution.archives — list rollback archive versions. | [L12727](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12727) |
| `async def handle_skills_evolution_rollback(self, params: dict) -> dict[str, Any]` | RPC: skills.evolution.rollback — rollback skill via disk EvolutionStore. | [L12745](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12745) |
| `async def handle_skills_evolution_rebuild(self, params: dict) -> dict[str, Any]` | RPC: skills.evolution.rebuild — merge evolution records into a new version. | [L12789](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12789) |
| `async def generate_evolution_merge_version(self, params: dict \| None = None, *, skill_name: str \| None = None, stream_ctx: Any = None) -> dict[str, Any]` | Prepare → LLM rewrite → complete_rebuild (shared by RPC and auto merge). | [L12793](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12793) |
| `def _apply_rebuild_permission_trusted_dirs(self, skill_md_path: str \| None) -> None` | Allow write_file/edit_file on the rebuild SKILL.md even without HITL. | [L12920](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12920) |
| `async def _execute_merge_version_rewrite(self, prompt: str, *, stream_ctx: Any = None) -> bool` | Run LLM rewrite for merge-version; return True when agent call succeeds. | [L12946](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12946) |
| `def _schedule_pending_auto_rebuild(self, request_id: str \| None = None) -> None` | Fire-and-forget version merge for skills queued after experience persist. | [L12981](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12981) |
| `async def _skill_has_live_evolution_records(self, skill_name: str) -> bool` | Return True when disk evolutions.json has at least one live entry. | [L12991](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L12991) |
| `async def _run_auto_rebuild_skills_detached(self, *, request_id: str \| None = None) -> None` | Background auto version merge gated by per-skill selfEvolution=auto. | [L13012](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13012) |
| `@staticmethod def _followup_response(action: str, followup_prompt: str, skill_name: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L13044](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13044) |
| `@staticmethod def _extract_followup_prompt(slash_result: dict[str, Any] \| None) -> str \| None` | Return follow-up prompt when a slash command should continue as an agent turn. | [L13053](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13053) |
| `async def _ensure_evolution_rail_for_slash(self, mode: str) -> str \| None` | Check evolution availability for slash commands; lazily init rail if needed. | [L13065](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13065) |
| `async def _handle_slash_command(self, query: Any, session_id: str = 'default', mode: str = 'agent', channel_id: str \| None = None) -> dict[str, Any] \| None` | Intercept slash commands before agent invocation. | [L13087](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13087) |
| `async def _execute_slash_rebuild_request(self, slash_result: dict[str, Any], *, skills_dirs: list[str] \| None = None) -> dict[str, Any]` | Run shared rebuild pipeline for `/evolve_rebuild` (not a followup turn). | [L13141](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13141) |
| `@staticmethod def _goal_record_payload(record: Any \| None) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L13184](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13184) |
| `@staticmethod def _format_goal_control_message(action: str, goal: dict[str, Any] \| None) -> str` | 源码未提供方法级文档字符串。 | [L13191](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13191) |
| `@staticmethod def _record_goal_set_history_if_needed(request: AgentRequest, *, action: str \| None, result_type: str \| None, goal_payload: dict[str, Any] \| None) -> None` | Write objective as a user history turn only after a successful set. | [L13203](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13203) |
| `@staticmethod def _goal_completed_history_exists(session_id: str, goal_id: str) -> bool` | Return True when this session already persisted a completion card for goal_id. | [L13238](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13238) |
| `@staticmethod def _record_goal_completed_history_if_needed(*, session_id: str, channel_id: str, channel_metadata: dict[str, Any] \| None, mode: str \| None, goal_payload: dict[str, Any] \| None) -> None` | Persist a goal-completed card once when status first becomes completed. | [L13259](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13259) |
| `@staticmethod def _interaction_goal_updated_payload(payload: Any) -> dict[str, Any]` | Normalize goal updates to the public Web/TUI payload shape. | [L13307](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13307) |
| `def _get_goal_manager(self) -> Any` | 源码未提供方法级文档字符串。 | [L13321](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13321) |
| `async def dispatch_goal_control(self, *, action: str, objective: str \| None = None, overwrite_confirmed: bool = False, token_budget: int \| None = None, max_attempts: int \| None = None, session_id: str = 'default') -> dict[str, Any] \| None` | Public Goal control entry used by the facade/session-pool adapter. | [L13326](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13326) |
| `async def _dispatch_goal_control(self, *, action: str, objective: str \| None = None, overwrite_confirmed: bool = False, token_budget: int \| None = None, max_attempts: int \| None = None, session_id: str = 'default', request: AgentRequest \| None = None) -> dict[str, Any] \| None` | Map JiuwenSwarm protocol fields to the independent Goal methods. | [L13346](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13346) |
| `async def handle_goal_command_structured(self, request: AgentRequest, *, session_id: str \| None = None) -> dict[str, Any] \| None` | Structured ``command.goal`` endpoint（业务字段取自 ``request.params``）。 | [L13517](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13517) |
| `async def _handle_goal_slash_command(self, query: str, session_id: str = 'default') -> dict[str, Any] \| None` | Translate only product syntax; the SDK receives capability calls. | [L13541](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13541) |
| `async def _cancel_pending_todos(self, session_id: str) -> list[dict] \| None` | 将未完成的 todo 项标记为 cancelled. | [L13568](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13568) |
| `def _active_deepresearch_session(self, session_id: str) -> Any \| None` | Return the already-bound product session only for an exact id match. | [L13626](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13626) |
| `async def _load_deepresearch_rewrite_html_target(self, session_id: str) -> RewriteHtmlTarget \| None` | Restore the trusted HTML target from the active product session. | [L13643](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13643) |
| `async def _try_deepresearch_rewrite_html_followup(self, query: object, session_id: str) -> RewriteHtmlFollowupResult \| None` | Handle an explicit HTML follow-up without routing through the model. | [L13664](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13664) |
| `async def _run_deepresearch_rewrite_html_transaction(self, query: object, *, session_id: str) -> RewriteHtmlFollowupResult \| None` | Linearize trusted-target loading and HTML generation with Core work. | [L13701](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13701) |
| `async def _try_deepresearch_rewrite_fast_path(self, query: object) -> RewriteFastPathResult \| None` | 源码未提供方法级文档字符串。 | [L13725](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13725) |
| `@staticmethod def _valid_deepresearch_rewrite_request_id(request_id: object) -> bool` | 源码未提供方法级文档字符串。 | [L13745](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13745) |
| `def _decode_deepresearch_rewrite_replays(self, value: object) -> list[tuple[str, str, RewriteFastPathResult, dict[str, object]]]` | 源码未提供方法级文档字符串。 | [L13751](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13751) |
| `def _encode_deepresearch_rewrite_replay_entry(self, request_id: str, query_sha256: str, result: RewriteFastPathResult, target: RewriteHtmlTarget) -> dict[str, object]` | 源码未提供方法级文档字符串。 | [L13860](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13860) |
| `@staticmethod def _deepresearch_rewrite_replay_conflict() -> RewriteFastPathResult` | 源码未提供方法级文档字符串。 | [L13876](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13876) |
| `@staticmethod def _deepresearch_rewrite_publish_uncertain(target: RewriteHtmlTarget \| None = None) -> RewriteFastPathResult` | 源码未提供方法级文档字符串。 | [L13893](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13893) |
| `@staticmethod def _deepresearch_rewrite_terminal_kind(result: RewriteFastPathResult) -> str \| None` | 源码未提供方法级文档字符串。 | [L13921](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13921) |
| `def _load_deepresearch_rewrite_replay(self, session_id: str, request_id: str, query_sha256: str) -> tuple[bool, RewriteFastPathResult \| None]` | 源码未提供方法级文档字符串。 | [L13957](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13957) |
| `async def _run_deepresearch_rewrite_fast_path_transaction(self, query: object, *, session_id: str, request_id: str) -> RewriteFastPathResult \| None` | Linearize the irreversible rewrite and its conversation checkpoint. | [L13995](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L13995) |
| `async def _persist_deepresearch_rewrite_fast_path_turn(self, *, session_id: str, request_id: str \| None = None, query_sha256: str \| None = None, query: object, result: RewriteFastPathResult) -> bool` | Persist a fast rewrite as one valid tool-call conversation turn. | [L14184](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14184) |
| `def _is_stream_rewrite_fast_path_eligible(self, request: AgentRequest, *, pending_goal_op: dict[str, Any] \| None, attach_goal_request: bool, goal_stream_request: bool) -> bool` | Limit rewrite bypasses to an idle, ordinary single-agent turn. | [L14449](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14449) |
| `@staticmethod def _fast_path_chunks(result: RewriteFastPathResult, *, request_id: str, channel_id: str) -> tuple[AgentResponseChunk, ...]` | 源码未提供方法级文档字符串。 | [L14471](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14471) |
| `@staticmethod def _rewrite_html_followup_chunks(result: RewriteHtmlFollowupResult, *, request_id: str, channel_id: str) -> tuple[AgentResponseChunk, ...]` | 源码未提供方法级文档字符串。 | [L14503](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14503) |
| `@staticmethod def _normalize_deepresearch_usage(value: object) -> dict[str, float] \| None` | 源码未提供方法级文档字符串。 | [L14522](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14522) |
| `@staticmethod def _deepresearch_usage_summary(usage: dict[str, float]) -> dict[str, float \| str]` | 源码未提供方法级文档字符串。 | [L14553](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14553) |
| `async def process_message_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AgentResponse` | Execute a single non-streaming request and return the response. | [L14571](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L14571) |
| `async def process_message_stream_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AsyncIterator[AgentResponseChunk]` | Execute a streaming request; yield response chunks. | [L15179](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L15179) |
| `@staticmethod def _stream_text_payload(event_type: str, content: Any) -> dict[str, Any] \| None` | Build a text event without discarding formatting-only chunks. | [L16890](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L16890) |
| `@staticmethod def _is_ask_user_payload(payload: Any) -> bool` | HITL 暂停判定：payload 是否为 ask_user 卡片事件。 | [L16900](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L16900) |
| `@staticmethod def _run_failure(chunk) -> tuple[str, str] \| None` | Return ``(error_type, message)`` if *chunk* is a run-level terminal failure. | [L16905](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L16905) |
| `@staticmethod def _parse_stream_chunk(chunk, *, _has_streamed_content: bool = False, _stage: str = '', _streamed_text: str = '', _protocol_buffer: Any = None) -> dict \| None` | 将 SDK OutputSchema 转为前端可消费的 payload dict. | [L16954](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L16954) |
| `async def _handle_memory_rail_by_config(self, mode: str)` | 源码未提供方法级文档字符串。 | [L17436](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17436) |
| `def _build_external_memory_rail(self)` | 源码未提供方法级文档字符串。 | [L17493](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17493) |
| `async def _handle_external_memory_rail_by_config(self)` | Register / unregister ExternalMemoryRail based on config. | [L17503](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17503) |
| `async def compress_context(self, session_id: str, session: Any = None, *, return_state: bool = False) -> dict[str, Any]` | 主动触发上下文压缩。 | [L17551](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17551) |
| `async def get_context_usage(self, session_id: str) -> dict[str, Any]` | 获取当前上下文窗口占用统计。 | [L17641](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17641) |
| `async def generate_recap(self, session_id: str) -> dict[str, Any]` | 生成会话快速回顾（read-only，不修改对话历史）。 | [L17757](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17757) |
| `def _get_recent_messages(self, session_id: str, window: int = 30) -> list[Any]` | 从当前 agent 对话上下文中提取最近N条消息。 | [L17789](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17789) |
| `async def _get_agent_tools(self, session_id: str) -> list[Any]` | 取主 agent 当前 tools 列表（List[ToolInfo]），用于 btw/recap 透传给模型。 | [L17896](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17896) |
| `def _get_agent_system_prompt(self) -> str` | Return the current agent's system prompt, or empty string if unavailable. | [L17939](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17939) |
| `async def _call_model_for_recap(self, messages: list[Any], prompt: str, system_prompt: str = '', enable_prompt_caching: bool = True, tools: list[Any] \| None = None) -> str \| None` | 调用 model 生成简短回答（单轮、禁工具执行）。 | [L17959](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L17959) |
| `async def generate_btw_answer(self, session_id: str, question: str) -> dict[str, Any]` | 回答 /btw 侧问题：独立、无工具、单轮 LLM 查询。 | [L18062](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18062) |
| `async def repair_model_response(self, prompt: str) -> str \| None` | Run a focused repair prompt using the currently selected chat model. | [L18114](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18114) |
| `async def compact_partial(self, session_id: str, turn_index: int, direction: str = 'from') -> dict[str, Any]` | 部分对话压缩 — /rewind summarize from here 的核心实现。 | [L18133](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18133) |
| `@staticmethod def _build_messages_for_model(records: list[dict[str, Any]]) -> list[Any]` | 源码未提供方法级文档字符串。 | [L18230](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18230) |
| `async def _count_full_context_tokens(self, context: Any, react_agent: Any, session_id: str) -> int` | 计算完整上下文的 token 数（包含 system messages + context messages + tools）。 Args: context: ModelContext 对象 react_agent: ReActAgent 对象 session_id: 会话ID | [L18259](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18259) |
| `async def _watch_evolution_and_push(self, rid: str, cid: str, session_id: str) -> None` | Poll passive evolution events and push progress, approval, and terminal status. | [L18314](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18314) |
| `def _on_evolution_watcher_done(self, task: asyncio.Task) -> None` | Callback when an evolution watcher task completes. | [L18548](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18548) |
| `@staticmethod def _is_approval_event(evt) -> bool` | Check whether an OutputSchema event is an approval request. | [L18560](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18560) |
| `async def try_start_dreaming(self, busy_checker: Callable[[], bool] \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L18569](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18569) |
| `async def try_stop_dreaming(self) -> None` | 源码未提供方法级文档字符串。 | [L18590](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18590) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _propagate_stream_source_id(src_payload: Any, result: dict[str, Any] \| None) -> dict[str, Any] \| None` | 将上游 payload 中的 stream_source_id / task_id 透传到输出帧（原地写入）。 | [L153](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L153) |
| `def _tool_result_lookup(payload: Any, *keys: str) -> Any` | Read a field from a tool_result payload or its nested ``tool_result`` dict. | [L173](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L173) |
| `def _is_outer_react_tool_result(payload: Any) -> bool` | True when this tool_result belongs to the outer ReAct agent, not SkillTurbo internals. | [L187](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L187) |
| `def _log_latency_pre_llm_once(request_id: str \| None) -> None` | Mark ③ endpoint once per request, right before provider call. | [L600](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L600) |
| `def get_runtime_tool_session_id() -> str \| None` | Session id bound for the current agent tool invocation (ContextVar). | [L628](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L628) |
| `def get_runtime_tool_channel_id() -> str` | Channel id bound for the current agent tool invocation (ContextVar). | [L633](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L633) |
| `def get_runtime_tool_metadata() -> dict[str, Any] \| None` | Request metadata bound for the current agent tool invocation (ContextVar). | [L638](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L638) |
| `def reset_shared_checkpoint_for_tests() -> None` | Reset process-wide checkpoint singleton (tests only). | [L653](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L653) |
| `def _gateway_db_pool_kwargs() -> dict[str, Any]` | SQLAlchemy 连接池参数（``GATEWAY_DB_POOL_*`` / ``RUNTIME_DB_POOL_*``）。 | [L666](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L666) |
| `def _running_loop() -> asyncio.AbstractEventLoop \| None` | 源码未提供函数级文档字符串。 | [L689](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L689) |
| `async def _get_persistent_checkpointer_lock() -> asyncio.Lock` | Lazy-init or rebind the process-wide checkpointer lock to the running loop. | [L696](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L696) |
| `def _extract_iteration_from_obj(value: Any) -> int \| None` | Best-effort parse iteration from chunk/payload/dict. | [L759](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L759) |
| `def _extract_iteration_from_chunk(chunk: Any) -> int \| None` | Extract iteration from stream chunk object. | [L786](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L786) |
| `def _apply_llm_io_trace_patch() -> None` | Monkey Patch Model.invoke/stream 添加 LLM IO trace 日志. | [L797](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L797) |
| `def _stage_breakdown_logger(total_ms: float, threshold_ms: float) -> Callable[..., None]` | Pick the level a stage breakdown should be reported at. | [L1006](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1006) |
| `def _read_git_head(head_file: str) -> str` | Read the HEAD pointer directly, without spawning git. | [L1054](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1054) |
| `def _resolve_stable_git_facts(git_bin: str, project_dir: str, run_git: Callable[[list[str]], str]) -> _StableGitFacts` | Resolve the per-project git facts once and reuse them afterwards. | [L1103](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1103) |
| `def _normalize_tool_names(value: Any, default: list[str] \| None = None) -> list[str]` | Normalize comma-separated/list tool-name config values. | [L1217](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1217) |
| `def _ensure_progressive_meta_tools(eager_tools: list[str]) -> list[str]` | Ensure tools_search / invoke_tool are in the eager list. | [L1226](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1226) |
| `def _normalize_progressive_eager_tools(value: Any, default: list[str] \| None = None) -> list[str]` | Normalize eager tools and migrate legacy registered names. | [L1235](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1235) |
| `def is_subagent_tool_lazy_load_enabled(react_config: dict[str, Any] \| None) -> bool` | True when react.tool_lazy_load and subagents are both enabled. | [L1251](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1251) |
| `def build_progressive_tool_rail_from_config(react_config: dict[str, Any], *, language: str, profile: str = 'main', agent_id: str \| None = None, agent_card_id: str \| None = None, subagent_kind: str \| None = None, deepresearch_context_provider: Callable[[], dict[str, str]] \| None = None) -> ProgressiveToolRail \| None` | Build ProgressiveToolRail from react.tool_lazy_load config. | [L1265](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1265) |
| `def _set_skill_evolution_triggers(rail: Any, *, review_trigger: bool, signal_trigger: bool = True) -> None` | 源码未提供函数级文档字符串。 | [L1345](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1345) |
| `def _clean_heartbeat_content(content: str) -> str` | Remove HTML comments and blank lines from HEARTBEAT.md content. | [L1355](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1355) |
| `def init_permission_engine(*_args: Any, **_kwargs: Any) -> None` | Legacy shim for tests/older call sites. | [L1367](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1367) |
| `def _mcc_looks_usable(mcc: dict) -> bool` | 检查 model_client_config 是否包含有效的 API 凭据。 | [L1376](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1376) |
| `def parse_int(value: Any, default: int) -> int` | Parse integer-like values safely. | [L1391](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1391) |
| `def _resolve_instance_config_base(config_base: dict[str, Any] \| None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1401](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1401) |
| `def _deep_agent_context_engine_config(react_cfg: dict[str, Any] \| None) -> ContextEngineConfig` | 供 ``create_deep_agent(..., context_engine_config=...)`` 使用（与 agent-core 集成测试方法二一致）。 | [L1413](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1413) |
| `def _model_provider(model: Any) -> str` | 源码未提供函数级文档字符串。 | [L1446](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1446) |
| `def _deep_agent_kv_cache_affinity_config(react_cfg: dict[str, Any] \| None, model: Model \| None = None) -> KVCacheAffinityConfig` | Build the ReActAgent KV cache affinity config from jiuwenswarm config. | [L1455](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1455) |
| `def _build_context_assemble_rail() -> ContextAssembleRail \| None` | Build ContextAssembleRail. | [L1479](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1479) |
| `def _resolve_session_memory_config(context_engine_cfg: dict[str, Any]) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L1490](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1490) |
| `def _build_context_processor_rail(config: dict[str, Any]) -> ContextProcessorRail \| None` | Build ContextProcessorRail with user config. | [L1502](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1502) |
| `def _patch_compiler_for_on_conflict()` | 使 MySQL 和 PostgreSQL SQLAlchemy 编译器支持 SQLite 的 ON CONFLICT DO UPDATE. | [L1574](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1574) |
| `async def _build_mysql_async_engine()` | 构建 / 复用 checkpoint MySQL AsyncEngine。 | [L1615](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1615) |
| `async def _build_postgresql_async_engine()` | 构建 / 复用 checkpoint PostgreSQL AsyncEngine。 | [L1718](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1718) |
| `async def ensure_persistent_checkpointer() -> None` | Ensure the process-wide default checkpointer uses sqlite / MySQL / PostgreSQL persistence. | [L1805](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1805) |
| `def _try_add_cache_control(msg: Any) -> None` | Add cache_control to the last content block of a message. | [L1897](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1897) |
| `def _agent_ras_kwargs_from_config(config_base: dict[str, Any] \| None) -> dict[str, Any]` | Thin YAML gate for create_deep_agent ``agent_ras``. | [L1968](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1968) |
| `def _agent_def_to_subagent_config(agent_def: AgentDefinition, model: Any, workspace: str, model_cache: dict[str, Any] \| None = None) -> SubAgentConfig` | 将 AgentDefinition 转换为 SubAgentConfig，用于 SubagentRail 注册。 | [L18602](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18602) |
| `def _load_custom_subagents(workspace_dir: str, subagents_cfg: dict \| None, model: Any, workspace: str, logger_name: str, **kwargs: Any) -> list[Any]` | 从 AgentConfigService 加载自定义 agent 并转换为 SubAgentConfig 列表。 | [L18669](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L18669) |

## `jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L1)

**模块职责：** LLM request/reasoning/response tracing for debugging.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_LLM_TRACE_EVENT_ID` | `ContextVar[str]` | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L33) |
| `_TOOL_TRACE_EVENT_ID` | `ContextVar[str]` | [L41](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L41) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def begin_llm_trace_event() -> Token` | 生成一个新的 event_id 并写入 ContextVar，返回 Token 供 finally 重置。 | [L47](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L47) |
| `def end_llm_trace_event(token: Token) -> None` | 重置 event_id ContextVar。 | [L52](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L52) |
| `def begin_tool_trace_event() -> Token` | 生成一个新的工具调用 event_id，返回 Token 供 finally 重置。 | [L57](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L57) |
| `def end_tool_trace_event(token: Token) -> None` | 重置工具调用 event_id ContextVar。 | [L62](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L62) |
| `def _current_event_id() -> str` | 源码未提供函数级文档字符串。 | [L67](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L67) |
| `def _current_tool_event_id() -> str` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L71) |
| `def _env_int(name: str, default: int) -> int` | 源码未提供函数级文档字符串。 | [L75](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L75) |
| `def _llm_trace_active() -> bool` | Emit trace when DEBUG is on for jiuwenswarm logger. | [L85](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L85) |
| `def _serialize_one(msg: Any) -> Any` | 源码未提供函数级文档字符串。 | [L90](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L90) |
| `def format_messages_for_trace(messages: list[Any]) -> str` | 源码未提供函数级文档字符串。 | [L104](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L104) |
| `def _serialize_tool_definition(t: Any) -> Any` | Serialize ToolInfo / dict for request tracing. | [L109](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L109) |
| `def build_jiuwenswarm_llm_request_envelope(*, messages: list[Any], tools: list[Any] \| None, model: str, max_tokens: int \| None, stream: bool, temperature: float \| None = None, top_p: float \| None = None, stop: str \| None = None, timeout: float \| None = None, extra: Mapping[str, Any] \| None = None) -> dict[str, Any]` | Structured request matching the jiuwenswarm → openjiuwen ``Model`` call surface. | [L134](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L134) |
| `def format_jiuwenswarm_llm_request_envelope_json(*, messages: list[Any], tools: list[Any] \| None, model: str, max_tokens: int \| None, stream: bool, temperature: float \| None = None, top_p: float \| None = None, stop: str \| None = None, timeout: float \| None = None, extra: Mapping[str, Any] \| None = None) -> str` | 源码未提供函数级文档字符串。 | [L165](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L165) |
| `def _serialize_tool_calls(tool_calls: list[Any]) -> list[Any]` | 源码未提供函数级文档字符串。 | [L193](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L193) |
| `def format_llm_assistant_for_trace(obj: Any) -> str` | 源码未提供函数级文档字符串。 | [L212](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L212) |
| `def _resolve_trace_ids(session_id: str, request_id: str) -> tuple[str, str]` | 从 session 注册表获取最新 request_id。 | [L224](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L224) |
| `def _trace_header(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, event: str, event_id: str = '') -> str` | 源码未提供函数级文档字符串。 | [L247](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L247) |
| `def _log_body_parts(header: str, body: str) -> None` | 源码未提供函数级文档字符串。 | [L266](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L266) |
| `def _log_llm_request_envelope(*, event: str, session_id: str, request_id: str, iteration: int \| None, model_name: str, envelope: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L277](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L277) |
| `def log_stream_input(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, messages: list[Any], tools: list[Any] \| None, max_tokens: int \| None, temperature: float \| None = None, top_p: float \| None = None, stop: str \| None = None, timeout: float \| None = None, extra: Mapping[str, Any] \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L297](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L297) |
| `def log_invoke_input(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, messages: list[Any], tools: list[Any] \| None, max_tokens: int \| None, temperature: float \| None = None, top_p: float \| None = None, stop: str \| None = None, timeout: float \| None = None, extra: Mapping[str, Any] \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L336](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L336) |
| `def log_reasoning_delta(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, reasoning_seq: int, fragment: str) -> None` | 源码未提供函数级文档字符串。 | [L375](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L375) |
| `def log_stream_output(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, assistant_msg: Any) -> None` | 源码未提供函数级文档字符串。 | [L396](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L396) |
| `def log_invoke_output(*, session_id: str, request_id: str, iteration: int \| None, model_name: str, assistant_msg: Any) -> None` | 源码未提供函数级文档字符串。 | [L417](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L417) |
| `def log_chat_final(*, session_id: str, request_id: str, iteration: int \| None, model_name: str) -> None` | Record the user-facing chat.final boundary without logging final content. | [L438](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L438) |
| `def _tool_trace_header(*, session_id: str, request_id: str, iteration: int \| None, agent: str, tool_name: str, tool_call_id: str, event: str) -> str` | 工具调用 trace 行头部，复用 LLM_IO_TRACE 前缀以便统一日志解析。 | [L458](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L458) |
| `def log_tool_call_input(*, session_id: str, request_id: str, iteration: int \| None, agent: str, tool_name: str, tool_call_id: str, args: Mapping[str, Any] \| None, extra: Mapping[str, Any] \| None = None) -> None` | 记录一次工具调用的入参。仅在 DEBUG 级别有效。 | [L480](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L480) |
| `def log_tool_call_output(*, session_id: str, request_id: str, iteration: int \| None, agent: str, tool_name: str, tool_call_id: str, status: str, duration_ms: float, result: Any = None, error: str \| None = None, extra: Mapping[str, Any] \| None = None) -> None` | 记录一次工具调用的出参。``status`` 取值：``ok`` / ``error`` / ``skipped`` / ``interrupted``。 | [L510](../../../../../jiuwenswarm/server/runtime/agent_adapter/llm_io_trace.py#L510) |

## `jiuwenswarm/server/runtime/agent_adapter/recap_prompts.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/recap_prompts.py#L1)

**模块职责：** /recap 命令的 prompt 模板与常量

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `RECENT_MESSAGE_WINDOW` | `未显式标注` | [L5](../../../../../jiuwenswarm/server/runtime/agent_adapter/recap_prompts.py#L5) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def build_recap_prompt(memory: str \| None, language: str = 'en') -> str` | 构建 /recap prompt | [L8](../../../../../jiuwenswarm/server/runtime/agent_adapter/recap_prompts.py#L8) |
| `def _build_btw_prompt(question: str, language: str = 'en') -> str` | 构建 /btw 侧问题 prompt。 | [L36](../../../../../jiuwenswarm/server/runtime/agent_adapter/recap_prompts.py#L36) |

## `jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L1)

**模块职责：** Request-scoped skill-root binding for disk-only evolution RPCs.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_SESSION_REGISTERED_SKILL_DIRS` | `ContextVar[tuple[str, ...] \| None]` | [L11](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L11) |
| `__all__` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L46) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def bind_session_registered_skill_dirs(dirs: Sequence[str \| Path]) -> Token` | Bind skill roots for the current task/request; return reset token. | [L17](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L17) |
| `def reset_session_registered_skill_dirs(token: Token) -> None` | Reset skill-root binding to the previous value. | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L33) |
| `def get_session_registered_skill_dirs() -> list[str] \| None` | Return request-bound skill roots, or ``None`` when unbound. | [L38](../../../../../jiuwenswarm/server/runtime/agent_adapter/session_skill_dirs.py#L38) |

## `jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L1)

**模块职责：** 定义 _normalize_fs_entry、_sandbox_files_entry_path、_is_strict_path_prefix、validate_sandbox_files_runtime、find_nested_files_conflict、_resolve_shared_dir 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L33) |
| `PreserveFileSharingMode` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L36) |
| `_PRESERVE_FILE_SHARING_MODE` | `PreserveFileSharingMode` | [L37](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L37) |
| `_resolve_workspace_dir` | `未显式标注` | [L167](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L167) |
| `__all__` | `未显式标注` | [L992](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L992) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_fs_entry(entry: Any) -> dict[str, str] \| None` | 源码未提供函数级文档字符串。 | [L40](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L40) |
| `def _sandbox_files_entry_path(entry: Any) -> str \| None` | Extract the path string from a sandbox.files allow/deny entry. | [L56](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L56) |
| `def _is_strict_path_prefix(parent: str, child: str) -> bool` | Return True when ``parent`` is a strict directory ancestor of ``child``. | [L64](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L64) |
| `def validate_sandbox_files_runtime(files: dict[str, Any] \| None) -> None` | Reject invalid ``sandbox.files`` shapes. | [L75](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L75) |
| `def find_nested_files_conflict(path: str, bucket: str, files: dict[str, Any]) -> str \| None` | Return an error message when ``path`` would create unsupported nesting. | [L87](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L87) |
| `def _resolve_shared_dir(shared_dir: str \| Path \| None = None) -> Path \| None` | Resolve sandbox rw bind root (shared downloads + workspace). | [L127](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L127) |
| `def _resolve_config_ro_path() -> Path \| None` | Resolve jiuwenswarm config.yaml for ro bind (internal startup_mode only). | [L170](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L170) |
| `def _resolve_project_dir(override: str \| Path \| None) -> Path \| None` | Resolve the host directory to bind into the sandbox as ``rw``. | [L198](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L198) |
| `def _sandbox_isolation_custom_id(project_dir: str \| Path \| None) -> str` | Stable SysOperation isolation key suffix for per-project sandbox sharing. | [L252](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L252) |
| `def _resolve_agent_root_dir() -> Path \| None` | Resolve agent_root for yuanrong identity mount (enterprise_dev-style). | [L261](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L261) |
| `def _merge_yuanrong_mounts(agent_root_mount: dict[str, Any], config_mounts: list[Any] \| None) -> list[dict[str, Any]]` | Merge forced agent_root mount with optional yaml mounts; dedupe by source+target. | [L292](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L292) |
| `def _build_yuanrong_extra_params() -> dict[str, Any]` | Assemble yuanrong provider extra_params; always identity-mount agent_root. | [L324](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L324) |
| `def build_yuanrong_sandbox_status_view() -> dict[str, Any]` | Read-only yuanrong status for TUI ``/sandbox`` (enabled / executor / mounts). | [L374](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L374) |
| `def build_filesystem_policy(files_runtime: dict[str, Any] \| None, *, project_dir: str \| Path \| None = None, is_code_agent: bool = False, startup_mode: str \| None = None, shared_dir: str \| Path \| None = None) -> tuple[dict[str, Any], list[dict[str, str]]]` | build jiuwenbox filesystem policy. | [L404](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L404) |
| `def create_sandbox_sysop_card(sandbox_url: str, sandbox_type: str, *, files_runtime: dict[str, Any] \| None = None, excluded_commands: list[str] \| None = None, idle_ttl_seconds: int \| None = None, idle_check_interval: int \| None = None, fallback_on_failure: bool = False, project_dir: str \| Path \| None = None, is_code_agent: bool = False, startup_mode: str \| None = None, shared_dir: str \| Path \| None = None) -> SysOperationCard \| None` | Create sandbox SysOperationCard (jiuwenbox or yuanrong). | [L583](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L583) |
| `def create_local_sysop_card() -> SysOperationCard` | 构造本地模式 SysOperationCard. | [L721](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L721) |
| `def _append_unique(target: list[dict[str, str]], entry: dict[str, str]) -> None` | Append ``entry`` to ``target`` if no existing item shares its ``path``. | [L730](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L730) |
| `def _classify_host_kind(path: str) -> str` | 源码未提供函数级文档字符串。 | [L743](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L743) |
| `def _resolve_display_path(raw: str \| Path \| None) -> str \| None` | Resolve ``raw`` into the canonical absolute path used in display/compare. | [L750](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L750) |
| `def _filesystem_policy_to_display_entries(fs_policy: dict[str, Any]) -> dict[str, list[dict[str, str]]]` | Convert ``filesystem_policy.bind_mounts`` into ``/sandbox`` display entries. | [L773](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L773) |
| `def effective_files_from_policy(policy: dict[str, Any]) -> dict[str, list[dict[str, str]]]` | Derive ``/sandbox`` display entries from a cached launcher policy dict. | [L804](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L804) |
| `def list_auto_managed_sandbox_paths(project_dir: str \| Path \| None = None, *, is_code_agent: bool = False, startup_mode: str \| None = None, shared_dir: str \| Path \| None = None) -> dict[str, list[dict[str, str]]]` | Auto-configured sandbox entries that users cannot mutate via ``/sandbox``. | [L812](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L812) |
| `def list_effective_sandbox_files(files_runtime: dict[str, Any] \| None, *, project_dir: str \| Path \| None = None, is_code_agent: bool = False, startup_mode: str \| None = None) -> dict[str, list[dict[str, str]]]` | Read-only "what will the sandbox actually allow / deny writes to" view. | [L886](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L886) |
| `def find_auto_managed_match(path: str, *, project_dir: str \| Path \| None = None, is_code_agent: bool = False, startup_mode: str \| None = None) -> tuple[str, str] \| None` | Return ``(bucket, canonical_path)`` if ``path`` is auto-managed; else ``None``. | [L951](../../../../../jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py#L951) |

## `jiuwenswarm/server/runtime/agent_adapter/team_helpers.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1)

**模块职责：** Team agent streaming helpers.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L86](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L86) |
| `_WORKFLOW_RUNS_STATE_KEY` | `未显式标注` | [L95](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L95) |
| `_TEAM_CREATE_KINDS` | `未显式标注` | [L97](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L97) |
| `_HIDE_DM_PREFIX` | `未显式标注` | [L101](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L101) |
| `_STREAM_TRACE_ENV_KEY` | `未显式标注` | [L102](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L102) |
| `_HIDE_TEAMMATE_ENV_KEY` | `未显式标注` | [L105](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L105) |
| `_FOLLOWUP_INTERACT_BOUNDARY_TIMEOUT_SEC` | `未显式标注` | [L112](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L112) |
| `_FOLLOWUP_INTERACT_POLL_INTERVAL_SEC` | `未显式标注` | [L113](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L113) |
| `_INTERACT_REASON_ERROR_MAP` | `dict[str, str]` | [L127](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L127) |
| `_INNER_TYPE_FANOUT` | `dict[str, Any]` | [L202](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L202) |
| `_ROLE_FANOUT` | `dict[str, Any]` | [L215](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L215) |
| `_GODVIEW_TARGET` | `未显式标注` | [L222](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L222) |
| `_MODEL_OUTPUT_EVENT_TYPES` | `未显式标注` | [L530](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L530) |
| `_CRON_DELEGATION_GRACE_SECONDS` | `未显式标注` | [L836](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L836) |
| `_TEAM_BUILDING_EVENT_TYPES` | `未显式标注` | [L948](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L948) |
| `_TEAM_TOOL_RESULT_TEXT_LIMIT` | `未显式标注` | [L1141](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1141) |
| `_TEAM_MEMBER_SETTLED_STATUSES` | `未显式标注` | [L1168](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1168) |
| `_TEAM_TASK_TERMINAL_STATUSES` | `未显式标注` | [L1169](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1169) |
| `_TEAM_MEMBER_UNSTARTED_STATUS` | `未显式标注` | [L1170](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1170) |
| `_WF_PHASE_STATUS_TO_TASK` | `dict[str, tuple[str, str]]` | [L2759](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2759) |

### [`class _FollowupInteractBoundaryResult`](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L263)

Result of delivering a follow-up across a runtime boundary.

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `success` | `bool` | `—` | [L266](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L266) |
| `reason` | `str \| None` | `—` | [L267](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L267) |
| `first_request_ready` | `bool` | `—` | [L268](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L268) |

### [`class _FirstTeamRequestPreparation`](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L346)

Result of first-request preprocessing.

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `recovered_runtime` | `bool` | `—` | [L349](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L349) |
| `query` | `Any` | `—` | [L350](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L350) |
| `hide_dm` | `bool` | `—` | [L351](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L351) |
| `debug` | `bool` | `—` | [L352](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L352) |
| `error_chunks` | `list[AgentResponseChunk] \| None` | `None` | [L353](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L353) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _safe_team_path_segment(value: str, fallback: str = '_') -> str` | Sanitize a value into one path segment for team workspace paths. | [L116](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L116) |
| `def _team_hide_teammate_enabled() -> bool` | Return whether non-leader teammate frames should be filtered out in team mode. | [L123](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L123) |
| `def _tgt_godview() -> dict` | 源码未提供函数级文档字符串。 | [L151](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L151) |
| `def _tgt_mention(member_names, *, mention_all: bool = False, speaker: str \| None = None) -> dict` | mention intent：投递给被点名成员并带 @（飞书 <at>）。 | [L155](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L155) |
| `def _tgt_private(member_names, *, speaker: str \| None = None) -> dict` | private intent：投递给被点名成员但不带 @。 | [L169](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L169) |
| `def _p2p_fanout(inner: dict) -> list[dict]` | P2P 消息 fan_out：godview + 收件人(mention, 带 @) + 发送方(private, 不带 @)。 | [L178](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L178) |
| `def _build_logical_targets(event: dict) -> list[dict]` | 所有 team 事件 → fan_out 规则（表驱动，依次查两维后兜底 godview）。 | [L225](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L225) |
| `def _is_followup_delivery_boundary_reason(reason: str \| None) -> bool` | Return whether follow-up delivery likely hit a runtime boundary. | [L254](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L254) |
| `async def _deliver_followup_interact_across_boundary(team_manager: Any, session_id: str, query: Any, *, initial_reason: str \| None = None, timeout_sec: float = _FOLLOWUP_INTERACT_BOUNDARY_TIMEOUT_SEC, poll_interval_sec: float = _FOLLOWUP_INTERACT_POLL_INTERVAL_SEC) -> _FollowupInteractBoundaryResult` | Deliver a follow-up until interact succeeds or the session becomes first-run ready. | [L271](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L271) |
| `def _build_team_event_chunk_meta(event: Any) -> tuple[dict \| None, dict]` | 从 team event 统一推导 (agent_ref, metadata)，供所有 team 事件产出路径调用。 | [L304](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L304) |
| `def _extract_query_directives(query: str) -> tuple[str, bool, bool]` | Strip all leading slash directives from the first team query. | [L335](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L335) |
| `async def _prepare_first_team_request(*, team_manager: Any, session_id: str, channel_id: str \| None, request_id: str, query: Any) -> _FirstTeamRequestPreparation` | Apply first-request preprocessing shared by cold starts and fallback starts. | [L356](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L356) |
| `def sync_team_identity_metadata(*, channel_id: str \| None, session_id: str, mode: str, ready_team_name: str, activation_kind: str \| None) -> None` | Persist team identity when a team runtime becomes ready. | [L448](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L448) |
| `def persist_workflow_runs(runs: dict[str, WorkflowRunState], session_id: str) -> None` | Persist WorkflowRunState dict to session metadata (file-based store). | [L480](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L480) |
| `def restore_workflow_runs(session_id: str) -> dict[str, WorkflowRunState] \| None` | Restore WorkflowRunState dict from session metadata. | [L489](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L489) |
| `def _resolve_channel_id(channel_id: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L502](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L502) |
| `def _resolve_request_language(request: Any) -> str` | 源码未提供函数级文档字符串。 | [L506](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L506) |
| `def _safe_query_preview(query: Any, limit: int = DEFAULT_PREVIEW_MAX_CHARS) -> str` | 源码未提供函数级文档字符串。 | [L523](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L523) |
| `def _normalize_team_query(query: Any, *, channel_id: str \| None, language: str) -> Any` | 源码未提供函数级文档字符串。 | [L533](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L533) |
| `async def _team_session_has_runtime(team_manager: TeamManager, session_id: str) -> bool` | 源码未提供函数级文档字符串。 | [L546](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L546) |
| `async def query_team_human_members_for_join(session_id: str, team_name: str) -> list[dict[str, Any]]` | 直查 team.db 取该 team 的全部成员（未 role 过滤，交调用方过滤）。 | [L558](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L558) |
| `async def _current_pool_team_agent(team_name: str) -> Any \| None` | Return the TeamAgent currently owned by Runner for ``team_name``. | [L580](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L580) |
| `async def ensure_monitor_handlers_for_active_runtime(channel_id: str \| None, session_id: str, team_name: str, hide_dm: bool = False, enable_swarmflow: bool = False) -> None` | Attach TeamMonitorHandler and optionally WorkflowMonitorHandler for the active runtime. | [L599](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L599) |
| `def _is_cron_request_id(request_id: str) -> bool` | 源码未提供函数级文档字符串。 | [L756](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L756) |
| `async def _wait_for_cron_team_round_events(*, request_queue: asyncio.Queue, round_state: dict[str, Any], request_id: str, channel_id: str \| None, session_id: str) -> AsyncIterator[dict[str, Any]]` | Yield team events until cron round completion signals align across modes. | [L760](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L760) |
| `async def _finish_cron_team_stream_after_delegation_grace(channel_id: str \| None, session_id: str, round_id: Any) -> None` | Wait briefly after a solo harness final before ending the cron team stream. | [L839](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L839) |
| `async def _finish_cron_team_stream_after_round(channel_id: str \| None, session_id: str, round_id: Any) -> None` | Cancel the background team stream once cron SwarmFlow + leader report are done. | [L859](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L859) |
| `def _try_finish_cron_team_stream(channel_id: str \| None, session_id: str, event: dict[str, Any]) -> None` | End persistent team streams for cron once workflow completes and leader reports. | [L902](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L902) |
| `def _broadcast_event(channel_id: str \| None, session_id: str, event: dict[str, Any]) -> None` | Broadcast an event to all request queues waiting on the same session. | [L953](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L953) |
| `def _approval_chunk_from_event(evt: Any) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L965](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L965) |
| `async def _broadcast_team_state_snapshot(channel_id: str \| None, session_id: str) -> None` | Broadcast a snapshot of all member and task states. | [L978](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L978) |
| `def _approval_result_from_event_or_items(*, skill_name: str, event: Any, items: list[Any], no_changes_output: str, invalid_output: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L1044](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1044) |
| `def _is_leader_output(chunk: Any) -> bool` | Return whether a team OutputSchema chunk should be shown to claw users. | [L1069](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1069) |
| `def _is_teammate_output(chunk: Any) -> bool` | Return whether a team OutputSchema chunk is from a non-leader member. | [L1092](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1092) |
| `def _resolve_chunk_member_name(parsed: dict[str, Any], chunk: Any) -> str` | Resolve the roster member name carried by a team stream chunk. | [L1103](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1103) |
| `def _enrich_teammate_event(parsed: dict[str, Any], chunk: Any) -> dict[str, Any]` | Enrich a parsed teammate event for relay member-card routing. | [L1123](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1123) |
| `def _enrich_leader_event(parsed: dict[str, Any], chunk: Any) -> dict[str, Any]` | Enrich a parsed leader event with the same attribution contract. | [L1132](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1132) |
| `def _truncate_team_tool_result_event(parsed: dict[str, Any]) -> dict[str, Any]` | Trim large team tool result fields before forwarding them to clients. | [L1144](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1144) |
| `def _run_agent_team_streaming(**kwargs: Any) -> AsyncIterator[Any]` | 源码未提供函数级文档字符串。 | [L1173](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1173) |
| `async def _team_has_unread_messages(session_id: str, handler: Any) -> bool` | Return whether the team database has unread direct or broadcast messages. | [L1180](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1180) |
| `def _taskless_completion_enabled(session_id: str) -> bool` | Read TeamSpec.enable_taskless_completion for the session's team. | [L1198](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1198) |
| `async def _team_round_settled(channel_id: str \| None, session_id: str) -> bool` | Return whether the DB-backed team state is safe to finish this round. | [L1224](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1224) |
| `async def _finish_round_if_settled(channel_id: str \| None, session_id: str, round_id: Any, *, reason: str) -> bool` | Re-evaluate _team_round_settled; on True, broadcast processing_status (is_complete=True) with per-round idempotent dedup and return True so the caller can break the stream loop. On False, return False (no broadcast). | [L1288](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1288) |
| `def _is_duplicate_ask_user_question(parsed: dict[str, Any], emitted_request_ids: set[str]) -> bool` | 源码未提供函数级文档字符串。 | [L1327](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1327) |
| `def _team_processing_done_chunk(request_id: str, channel_id: str \| None, session_id: str) -> AgentResponseChunk` | 源码未提供函数级文档字符串。 | [L1342](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1342) |
| `def _group_team_evolution_approvals(session_id: str, events: list[Any]) -> tuple[dict[str, list[Any]], list[str]]` | 源码未提供函数级文档字符串。 | [L1360](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1360) |
| `def ensure_team_evolution_watcher(channel_id: str \| None, session_id: str, *, source: str = 'unknown') -> None` | Launch the per-session team evolution monitor once the team session is ready. | [L1377](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1377) |
| `async def _handle_team_slash_command(channel_id: str \| None, session_id: str, query: str, *, defer_missing_rail: bool = False, skills_dir: str \| list[str] \| None = None, language: str = 'cn') -> dict[str, Any] \| None` | Handle team-only slash commands before entering the team stream. | [L1423](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1423) |
| `async def _execute_team_slash_rebuild(slash_result: dict[str, Any], *, skills_dir: str \| list[str] \| None, rebuild_skill: Any \| None) -> dict[str, Any]` | Run shared rebuild pipeline for team `/evolve_rebuild`. | [L1471](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1471) |
| `def _resolve_team_slash_skills_dir(session_id: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L1527](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1527) |
| `def _team_spec_skills_dir(team_spec: Any) -> str` | 源码未提供函数级文档字符串。 | [L1535](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1535) |
| `def _team_spec_monitor_roots(team_spec: Any, session_id: str \| None = None) -> list[str]` | Return team/member workspace roots where file-op history may be written. | [L1544](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1544) |
| `def _persist_team_file_monitor_roots(session_id: str, team_spec: Any) -> None` | 源码未提供函数级文档字符串。 | [L1595](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1595) |
| `async def _start_team_stream_round(*, channel_id: str \| None, session_id: str, request_id: str, team_manager: Any, team_name: str, team_spec: Any, query: str, hide_dm: bool = False, debug: bool = False, source: str = 'first') -> asyncio.Queue` | Start a team stream round and register its waiter queue. | [L1645](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1645) |
| `async def process_team_message_stream(request: Any, inputs: dict[str, Any], deep_agent: DeepAgent, *, config_base: dict[str, Any] \| None = None, sessions_root: str \| Path \| None = None, rebuild_skill: Any \| None = None) -> AsyncIterator[AgentResponseChunk]` | Process a team-mode streaming request. | [L1696](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L1696) |
| `def _extract_team_usage_metadata(chunk: Any) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L2305](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2305) |
| `async def _consume_stream_with_query(channel_id: str \| None, session_id: str, team_spec: Any, initial_query: str, *, round_id: int, envs: dict[str, Any] \| None = None) -> None` | Consume the team stream in the background and broadcast parsed events. | [L2316](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2316) |
| `async def _consume_monitor_events(channel_id: str \| None, session_id: str, monitor_handler: TeamMonitorHandler) -> None` | Consume monitor events in the background and broadcast them. | [L2710](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2710) |
| `def _team_event_envelope(category: str, session_id: str, event: dict[str, Any]) -> dict[str, Any]` | Wrap an inner team event dict in the standard broadcast envelope. | [L2768](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2768) |
| `def _workflow_updated_to_team_events(event: dict[str, Any], session_id: str, seen_phase: dict[str, str], seen_agent: dict[str, str], spawned_members: set[str]) -> list[dict[str, Any]]` | Convert one ``workflow.updated`` event into web ``team.member`` / ``team.task`` events. | [L2775](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2775) |
| `async def _consume_workflow_events(channel_id: str \| None, session_id: str, workflow_handler: WorkflowMonitorHandler) -> None` | Consume workflow events in the background and broadcast them. | [L2872](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2872) |
| `def _persist_team_history_event(channel_id: str \| None, session_id: str, event: dict[str, Any]) -> None` | Persist team monitor events required by team.history.get panel restore. | [L2959](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L2959) |
| `def _on_team_watcher_done(task: asyncio.Task) -> None` | Callback when a team evolution monitor task completes. | [L3015](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L3015) |
| `async def _watch_team_evolution_and_push(channel_id: str \| None, session_id: str, rail: Any) -> None` | Monitor TeamSkillEvolutionRail and push stable status/approval events for every evolution cycle. | [L3030](../../../../../jiuwenswarm/server/runtime/agent_adapter/team_helpers.py#L3030) |

## `jiuwenswarm/server/runtime/agent_config_service.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L1)

**模块职责：** Agent 配置管理服务 — 管理内置和自定义 agent 定义的 CRUD 操作.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L25) |
| `_TOOL_DESCRIPTIONS` | `dict[str, str]` | [L28](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L28) |
| `AgentSource` | `未显式标注` | [L60](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L60) |
| `BUILTIN_AGENTS` | `list[AgentDefinition]` | [L125](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L125) |
| `_SOURCE_SORT_ORDER` | `dict[str, int]` | [L169](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L169) |

### [`class AgentDefinition`](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L64)

Agent 定义数据模型。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L67](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L67) |
| `description` | `str` | `—` | [L68](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L68) |
| `prompt` | `str` | `—` | [L69](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L69) |
| `source` | `AgentSource` | `—` | [L70](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L70) |
| `file_path` | `str \| None` | `None` | [L71](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L71) |
| `model` | `str \| None` | `None` | [L72](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L72) |
| `tools` | `list[str]` | `field(default_factory=lambda: ['*'])` | [L73](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L73) |
| `disallowed_tools` | `list[str]` | `field(default_factory=list)` | [L74](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L74) |
| `color` | `str \| None` | `None` | [L75](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L75) |
| `permission_mode` | `str \| None` | `None` | [L76](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L76) |
| `memory_scope` | `str \| None` | `None` | [L77](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L77) |
| `shadowed_by` | `AgentSource \| None` | `None` | [L78](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L78) |
| `enabled` | `bool \| None` | `None` | [L79](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L79) |
| `when_to_use` | `str \| None` | `None` | [L80](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L80) |
| `max_iterations` | `int \| None` | `None` | [L81](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L81) |
| `skills` | `list[str] \| None` | `None` | [L82](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L82) |

### [`class CreateAgentParams`](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L86)

创建 agent 的请求参数。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `name` | `str` | `—` | [L89](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L89) |
| `description` | `str` | `—` | [L90](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L90) |
| `prompt` | `str` | `—` | [L91](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L91) |
| `location` | `AgentSource` | `—` | [L92](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L92) |
| `model` | `str \| None` | `None` | [L93](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L93) |
| `tools` | `list[str] \| None` | `None` | [L94](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L94) |
| `color` | `str \| None` | `None` | [L95](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L95) |
| `permission_mode` | `str \| None` | `None` | [L96](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L96) |
| `memory_scope` | `str \| None` | `None` | [L97](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L97) |
| `disallowed_tools` | `list[str] \| None` | `None` | [L98](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L98) |
| `when_to_use` | `str \| None` | `None` | [L99](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L99) |
| `max_iterations` | `int \| None` | `None` | [L100](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L100) |
| `skills` | `list[str] \| None` | `None` | [L101](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L101) |

### [`class UpdateAgentParams`](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L105)

更新 agent 的请求参数（所有字段可选，None 表示不修改）。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `description` | `str \| None` | `None` | [L108](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L108) |
| `when_to_use` | `str \| None` | `None` | [L109](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L109) |
| `prompt` | `str \| None` | `None` | [L110](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L110) |
| `model` | `str \| None` | `None` | [L111](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L111) |
| `tools` | `list[str] \| None` | `None` | [L112](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L112) |
| `color` | `str \| None` | `None` | [L113](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L113) |
| `permission_mode` | `str \| None` | `None` | [L114](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L114) |
| `memory_scope` | `str \| None` | `None` | [L115](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L115) |
| `disallowed_tools` | `list[str] \| None` | `None` | [L116](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L116) |
| `max_iterations` | `int \| None` | `None` | [L117](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L117) |
| `skills` | `list[str] \| None` | `None` | [L118](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L118) |

### [`class AgentConfigService`](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L181)

管理 agent 定义的 CRUD 操作。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, workspace_dir: Path \| str \| None = None)` | 源码未提供方法级文档字符串。 | [L188](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L188) |
| `@staticmethod def _get_user_agents_dir() -> Path` | 源码未提供方法级文档字符串。 | [L194](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L194) |
| `def _get_project_agents_dir(self) -> Path` | 源码未提供方法级文档字符串。 | [L197](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L197) |
| `def _get_local_agents_dir(self) -> Path` | 源码未提供方法级文档字符串。 | [L200](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L200) |
| `def list_agents(self) -> list[AgentDefinition]` | 列出所有 agent（内置 + 自定义），按优先级合并。 | [L205](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L205) |
| `def get_agent(self, name: str) -> AgentDefinition \| None` | 获取单个 agent 完整定义（含 system prompt 正文）。 | [L257](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L257) |
| `def create_agent(self, params: CreateAgentParams) -> AgentDefinition` | 创建新的自定义 agent，写入 markdown 文件。 | [L268](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L268) |
| `def update_agent(self, name: str, params: UpdateAgentParams) -> AgentDefinition` | 更新自定义 agent 定义，覆盖写入文件。 | [L309](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L309) |
| `def delete_agent(self, name: str) -> bool` | 删除自定义 agent 定义文件。 | [L331](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L331) |
| `@staticmethod def list_available_tools() -> dict` | Return available tools with display names, internal names, descriptions, and groups. | [L351](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L351) |
| `def _resolve_location_dir(self, location: str) -> Path` | 源码未提供方法级文档字符串。 | [L405](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L405) |
| `@staticmethod def _load_from_dir(dir_path: Path, source: AgentSource) -> list[AgentDefinition]` | 从目录加载所有 .md agent 定义文件。 | [L416](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L416) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _source_sort_key(agent: AgentDefinition) -> int` | 源码未提供函数级文档字符串。 | [L172](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L172) |
| `def _parse_agent_file(file_path: Path, source: AgentSource) -> AgentDefinition \| None` | 解析 YAML frontmatter + Markdown body 格式的 agent 文件。 | [L436](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L436) |
| `def _format_agent_file(params: CreateAgentParams \| AgentDefinition) -> str` | 生成 YAML frontmatter + Markdown body 格式的 agent 文件内容。 | [L466](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L466) |
| `def _apply_update_params(agent: AgentDefinition, params: UpdateAgentParams) -> None` | 将 UpdateAgentParams 的非 None 字段应用到 AgentDefinition。 | [L498](../../../../../jiuwenswarm/server/runtime/agent_config_service.py#L498) |

## `jiuwenswarm/server/runtime/agent_manager.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1)

**模块职责：** AgentManager - 管理 Agent 实例.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L51](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L51) |
| `ACP_DEFAULT_CAPABILITIES` | `dict[str, Any]` | [L54](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L54) |
| `_DISK_ONLY_EVOLUTION_METHODS` | `frozenset[str]` | [L58](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L58) |

### [`class AgentManager`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L114)

管理多个 Agent 实例.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, agent_id: str \| None = None, service_id: str \| None = None, user_workspace_dir: Any \| None = None, config_base: Any \| None = None, env_overrides: dict[str, Any] \| None = None, last_reload_trace_id: str \| None = None, env_agent_id: str \| None = None, env_service_id: str \| None = None, workspace_key: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L122](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L122) |
| `@property def env_agent_id(self) -> str` | Return the env namespace agent_id for this manager. | [L216](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L216) |
| `@property def env_service_id(self) -> str` | Return the env namespace service_id for this manager. | [L221](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L221) |
| `def _get_agent_create_lock(self, channel_key: str, cache_key: str) -> asyncio.Lock` | 源码未提供方法级文档字符串。 | [L225](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L225) |
| `async def _get_or_create_skill_manager(self)` | Return the one SkillManager owned by this tenant workspace. | [L237](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L237) |
| `def _borrow_agent(self, agent: 'JiuWenSwarm') -> 'JiuWenSwarm'` | 源码未提供方法级文档字符串。 | [L280](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L280) |
| `def _release_agent_borrower(self, agent_id: int, task: asyncio.Task) -> None` | 源码未提供方法级文档字符串。 | [L299](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L299) |
| `def pin_agent(self, agent: 'JiuWenSwarm') -> None` | Keep a cached agent alive for a persistent background owner. | [L313](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L313) |
| `def unpin_agent(self, agent: 'JiuWenSwarm') -> None` | Release one persistent background ownership reference. | [L318](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L318) |
| `def _has_agent_borrowers(self, agent: 'JiuWenSwarm', *, exclude: asyncio.Task \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L328](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L328) |
| `def _schedule_pending_tui_retirement(self, agent_id: int) -> None` | 源码未提供方法级文档字符串。 | [L345](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L345) |
| `def _finish_retirement_task(self, agent_id: int, task: asyncio.Task) -> None` | 源码未提供方法级文档字符串。 | [L366](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L366) |
| `async def _retire_pending_tui_agent(self, agent_id: int) -> None` | 源码未提供方法级文档字符串。 | [L383](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L383) |
| `async def _retire_tui_agent_if_idle(self, cache_key: str, agent: 'JiuWenSwarm', channel_agents: dict[str, 'JiuWenSwarm'], *, exclude_borrower: asyncio.Task \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L399](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L399) |
| `async def _retire_tui_agent_if_idle_locked(self, cache_key: str, agent: 'JiuWenSwarm', channel_agents: dict[str, 'JiuWenSwarm'], *, exclude_borrower: asyncio.Task \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L416](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L416) |
| `@staticmethod def _reload_fingerprint(config: Any, env: Any, *, agent_topology: Any, target_channel_id: str \| None, target_session_id: str \| None, reload_scopes: list[str] \| None = None) -> str` | 源码未提供方法级文档字符串。 | [L485](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L485) |
| `def _reload_agent_topology(self, target_channel_id: str \| None = None) -> dict[str, list[tuple[str, int]]]` | 源码未提供方法级文档字符串。 | [L504](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L504) |
| `def iter_jiuwenswarm_instances(self) -> list['JiuWenSwarm']` | Return initialized agents from the current two-level cache. | [L518](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L518) |
| `async def _create_agent(self, agent_key: str, mode: str = 'agent', config: dict[str, Any] \| None = None, sub_mode: str = None, cache_key: str \| None = None) -> 'JiuWenSwarm'` | 创建 Agent 实例. | [L529](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L529) |
| `async def initialize(self, channel_id: str = '', extra_config: dict[str, Any] \| None = None) -> dict[str, Any] \| None` | 初始化 AgentManager. | [L605](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L605) |
| `async def cancel_all_inflight_work(self, reason: str = '[gateway ws disconnect] ') -> None` | Gateway 与 AgentServer 的 WebSocket 断开时：取消所有已创建 Agent 实例上的在途任务。 | [L643](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L643) |
| `async def cleanup_session_runtime(self, *, channel_id: str = '', session_id: str) -> bool` | Release in-memory runtime for one session across existing channel agents. | [L652](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L652) |
| `def get_client_capabilities(self, channel_id: str = '') -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L749](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L749) |
| `async def create_session(self, channel_id: str = '', session_id: str \| None = None) -> str` | 创建会话. | [L754](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L754) |
| `async def sync_prewarm_channels(self, enabled_channels: list[str], *, config: Any \| None = None, env: Any = None) -> dict[str, int]` | 源码未提供方法级文档字符串。 | [L779](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L779) |
| `async def claim_prewarmed_session(self, *, channel_id: str, project_id: str, project_dir: str \| None, work_mode: str, is_swarm: bool, prewarm_eligible: bool = True, create_token: str \| None = None)` | 源码未提供方法级文档字符串。 | [L796](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L796) |
| `async def wait_for_session_prewarm(self, session_id: str \| None) -> None` | 源码未提供方法级文档字符串。 | [L841](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L841) |
| `async def begin_foreground_chat(self) -> None` | 源码未提供方法级文档字符串。 | [L845](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L845) |
| `async def end_foreground_chat(self) -> None` | 源码未提供方法级文档字符串。 | [L848](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L848) |
| `def activate_session_prewarm(self, session_id: str \| None) -> None` | Mark a claimed prewarm workspace as a normal persisted session. | [L851](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L851) |
| `async def release_session_prewarm_claim(self, session_id: str \| None) -> None` | 源码未提供方法级文档字符串。 | [L856](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L856) |
| `async def get_agent(self, channel_id: str = '', mode: str = 'agent', project_dir: str = None, sub_mode: str = None, request: Any \| None = None) -> 'JiuWenSwarm \| None'` | 获取 Agent 实例（自动创建）. | [L860](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L860) |
| `def get_agent_nowait(self, channel_id: str = '', mode: str \| None = None, project_dir: str \| None = None, sub_mode: str \| None = None) -> 'JiuWenSwarm \| None'` | 获取 Agent 实例（同步，不自动创建）. | [L917](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L917) |
| `async def broadcast_package_change_to_single_agents(self, package_id: str, config_path: str, operation: str, channel_id: str \| None = None, skip_instance: Any \| None = None) -> None` | Broadcast package change to single-agent (agent mode) instances only. | [L963](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L963) |
| `def is_working(self) -> bool` | True when any managed agent reports in-flight work. | [L1053](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1053) |
| `async def reload_agents_config(self, config, env, *, target_channel_id: str \| None = None, target_session_id: str \| None = None, reload_scopes: set[str] \| None = None, reload_trace_id: str \| None = None) -> ReloadAggregateResult` | reload agent config. | [L1071](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1071) |
| `async def apply_sync_config(self, config: dict[str, Any], env: dict[str, Any]) -> ReloadAggregateResult` | Apply sync_agents_configs write-through config/env to live adapters. | [L1240](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1240) |
| `async def _evict_stale_llm_clients(self, effective_config: Any) -> None` | 模型热更新后, 关闭"已从 models.defaults 删除/改掉凭证"的 LLM 连接。 | [L1312](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1312) |
| `async def recreate_agent(self, channel_id: str, *, immediate: bool = True) -> None` | 重建指定 channel 的所有 agent 实例. | [L1366](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1366) |
| `async def _process_disk_only_evolution(self, request: Any) -> Any` | Handle archives/rollback without create_instance / LLM client. | [L1448](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1448) |
| `def _resolve_request_mode(self, request: Any, params: dict) -> str` | 解析请求的 agent 模式. | [L1512](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1512) |
| `def _agent_lookup_from_request(self, request: Any) -> tuple[str, str \| None, str \| None]` | Resolve get_agent keys from a chat request. | [L1571](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1571) |
| `async def process_message(self, request: Any) -> Any` | 处理非流式请求. | [L1630](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1630) |
| `async def process_message_stream(self, request: Any)` | 处理流式请求. | [L1663](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1663) |
| `async def cleanup(self) -> None` | 清理所有 agent 实例. | [L1693](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1693) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_channel_id(channel_id: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L66](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L66) |
| `def _normalize_mode(mode: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L70](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L70) |
| `def _normalize_sub_mode(sub_mode: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L74](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L74) |
| `def _normalize_project_dir(project_dir: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L78](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L78) |
| `def _make_agent_cache_key(mode: str \| None, sub_mode: str \| None, project_dir: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L88](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L88) |
| `def _build_acp_agent_config(extra_config: dict[str, Any] \| None = None) -> dict[str, Any]` | Return the dedicated ACP agent profile config. | [L95](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L95) |

## `jiuwenswarm/server/runtime/agent_warm_pool.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L1)

**模块职责：** Process-local pool of session-bound, ready-to-run DeepAgent instances.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L30) |
| `_PREWARM_ENABLED_ENV_KEY` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L35) |
| `_PREWARM_ON_VALUES` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L36) |
| `_PREWARM_OFF_VALUES` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L37) |

### [`class WarmKey`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L84)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `channel_id` | `str` | `—` | [L85](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L85) |
| `project_id` | `str` | `—` | [L86](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L86) |
| `project_dir` | `str` | `—` | [L87](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L87) |
| `work_mode` | `str` | `—` | [L88](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L88) |
| `is_swarm` | `bool` | `False` | [L89](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L89) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def agent_mode(self) -> str` | 源码未提供方法级文档字符串。 | [L92](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L92) |
| `@property def agent_sub_mode(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L96](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L96) |

### [`class WarmRevision`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L101)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `boot_id` | `str` | `—` | [L102](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L102) |
| `config_fingerprint` | `str` | `—` | [L103](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L103) |
| `sequence` | `int` | `—` | [L104](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L104) |

### [`class WarmSlot`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L108)

源码未提供类级文档字符串。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `key` | `WarmKey` | `—` | [L109](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L109) |
| `session_id` | `str` | `—` | [L110](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L110) |
| `revision` | `WarmRevision` | `—` | [L111](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L111) |
| `agent` | `'JiuWenSwarm'` | `—` | [L112](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L112) |
| `ready_at` | `float` | `—` | [L113](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L113) |

### [`class WarmClaim`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L117)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `session_id` | `str` | `—` | [L118](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L118) |
| `prewarm_hit` | `bool` | `—` | [L119](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L119) |
| `prewarm_status` | `str` | `—` | [L120](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L120) |

### [`class AgentWarmPool`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L123)

Own a bounded set of unclaimed, initialized Agent sessions.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `EXCLUDED_CHANNELS` | `未显式标注` | `frozenset({'acp', 'a2a'})` | [L126](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L126) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, manager: 'AgentManager', *, max_concurrency: int = 1, max_ready_slots: int = 1, max_foreground_concurrency: int = 8, background_cooldown_seconds: float = 0.25, enabled: bool \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L128](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L128) |
| `@property def boot_id(self) -> str` | 源码未提供方法级文档字符串。 | [L176](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L176) |
| `@staticmethod def make_key(*, channel_id: str, project_id: str, project_dir: str \| None, work_mode: str, is_swarm: bool = False) -> WarmKey` | 源码未提供方法级文档字符串。 | [L180](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L180) |
| `@staticmethod def config_fingerprint(config: Any, env: Any = None) -> str` | 源码未提供方法级文档字符串。 | [L197](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L197) |
| `def _next_revision(self, config: Any, env: Any = None) -> WarmRevision` | 源码未提供方法级文档字符串。 | [L207](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L207) |
| `@staticmethod def _new_session_id(channel_id: str) -> str` | 源码未提供方法级文档字符串。 | [L216](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L216) |
| `def _marker_path(self, session_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L220](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L220) |
| `def _write_marker(self, session_id: str, key: WarmKey) -> None` | 源码未提供方法级文档字符串。 | [L223](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L223) |
| `def clear_marker(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L240](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L240) |
| `def _cleanup_stale_markers(self) -> None` | 源码未提供方法级文档字符串。 | [L246](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L246) |
| `def _desired_keys(self, enabled_channels: set[str] \| None = None) -> set[WarmKey]` | 源码未提供方法级文档字符串。 | [L269](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L269) |
| `@staticmethod def _key_priority(key: WarmKey) -> tuple[int, int, int, str, str]` | Prefer the normal Web work slot for the initial global READY slot. | [L295](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L295) |
| `async def sync(self, enabled_channels: list[str], *, config: Any, env: Any = None) -> dict[str, int]` | 源码未提供方法级文档字符串。 | [L305](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L305) |
| `async def refresh(self, *, config: Any, env: Any = None) -> dict[str, int]` | 源码未提供方法级文档字符串。 | [L388](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L388) |
| `def _schedule_prepare_locked(self, key: WarmKey, revision: WarmRevision, *, session_id: str \| None = None, keep_as_slot: bool = True) -> tuple[str, asyncio.Task[None]]` | 源码未提供方法级文档字符串。 | [L395](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L395) |
| `def _enqueue_prepare_locked(self, key: WarmKey, revision: WarmRevision, *, prioritize: bool = False) -> None` | Queue a background slot without creating an unbounded task backlog. | [L415](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L415) |
| `def _pump_background_locked(self) -> None` | Start only the bounded next batch while no user chat is active. | [L430](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L430) |
| `def _schedule_background_pump_locked(self) -> None` | Leave an event-loop window between expensive background sessions. | [L450](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L450) |
| `async def begin_foreground(self) -> None` | Preempt speculative preparation while a real chat is active. | [L469](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L469) |
| `async def end_foreground(self) -> None` | Resume lazy background preparation after the final chat completes. | [L490](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L490) |
| `async def _prepare(self, key: WarmKey, session_id: str, revision: WarmRevision, *, keep_as_slot: bool) -> None` | 源码未提供方法级文档字符串。 | [L502](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L502) |
| `async def claim(self, key: WarmKey) -> WarmClaim` | 源码未提供方法级文档字符串。 | [L637](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L637) |
| `async def wait_for_session(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L675](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L675) |
| `async def release_claim_pin(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L697](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L697) |
| `async def _release_claim_pin_after(self, session_id: str, delay_seconds: float) -> None` | 源码未提供方法级文档字符串。 | [L703](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L703) |
| `async def _dispose_runtime(self, agent: 'JiuWenSwarm', channel_id: str, session_id: str, *, pinned: bool) -> None` | 源码未提供方法级文档字符串。 | [L709](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L709) |
| `async def _dispose_slot(self, slot: WarmSlot) -> None` | 源码未提供方法级文档字符串。 | [L724](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L724) |
| `async def stats(self) -> dict[str, int]` | 源码未提供方法级文档字符串。 | [L729](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L729) |
| `async def close(self) -> None` | 源码未提供方法级文档字符串。 | [L742](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L742) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _prewarm_enabled_by_env() -> bool` | Return whether background session prewarming is switched on. | [L40](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L40) |
| `def _zero_stats() -> dict[str, int]` | Return the pool statistics reported when nothing is being warmed. | [L67](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L67) |
| `def _normalize_project_dir(value: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L76) |

## `jiuwenswarm/server/runtime/code_source_unicode.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L1)

**模块职责：** Python script Unicode escape readability: normalize + artifact hook.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L16) |
| `_ESCAPE_MARKERS` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L18) |
| `_PYTHON_SCRIPT_SUFFIXES` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L19) |
| `_FALSE_STRINGS` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L20) |
| `_SURROGATE_MIN` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L21) |
| `_SURROGATE_MAX` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L22) |
| `_HOOK_REGISTERED` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L23) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_code_unicode_readable_enabled(config: dict[str, Any] \| None = None) -> bool` | 源码未提供函数级文档字符串。 | [L26](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L26) |
| `def _string_prefix(token_str: str) -> str` | 源码未提供函数级文档字符串。 | [L45](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L45) |
| `def _should_skip_string_token(token_str: str) -> bool` | 源码未提供函数级文档字符串。 | [L52](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L52) |
| `def _contains_lone_surrogate(value: str) -> bool` | Return True when *value* has UTF-16 surrogate code units (not valid in UTF-8). | [L57](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L57) |
| `def _quote_python_string(value: str) -> str` | 源码未提供函数级文档字符串。 | [L62](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L62) |
| `def normalize_python_source_unicode_literals(source: str) -> tuple[str, int]` | Decode \u/\U/\x in Python string literals to readable Unicode text. | [L84](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L84) |
| `def normalize_python_script_file(path: str \| Path) -> int` | 源码未提供函数级文档字符串。 | [L131](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L131) |
| `async def _normalize_code_artifact_hook(ctx: Any) -> None` | 源码未提供函数级文档字符串。 | [L160](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L160) |
| `def register_code_source_unicode_hook() -> None` | 源码未提供函数级文档字符串。 | [L176](../../../../../jiuwenswarm/server/runtime/code_source_unicode.py#L176) |

## `jiuwenswarm/server/runtime/cron_local_runtime.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L1)

**模块职责：** AgentServer-only helpers so CronSchedulerService can run without Gateway.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L17) |
| `T` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L19) |

### [`class NopCronMessageHandler`](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L22)

Drop channel pushes when MessageHandler / ChannelManager are not in-process.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@staticmethod async def publish_robot_messages(msg: Any) -> None` | 源码未提供方法级文档字符串。 | [L26](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L26) |

### [`class InProcessAgentServerClient`](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L34)

Invoke ``TenantAgentPool.process_message`` without a WebSocket hop.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, agent_manager: Any \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L40](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L40) |
| `def _resolve_pool(self) -> Any` | 源码未提供方法级文档字符串。 | [L43](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L43) |
| `@staticmethod async def connect(uri: str) -> None` | 源码未提供方法级文档字符串。 | [L63](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L63) |
| `@staticmethod async def disconnect() -> None` | 源码未提供方法级文档字符串。 | [L67](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L67) |
| `@staticmethod def set_or_update_server_config(*, config: dict[str, Any], env: dict[str, str] \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L71](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L71) |
| `async def send_request(self, envelope: E2AEnvelope) -> AgentResponse` | 源码未提供方法级文档字符串。 | [L78](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L78) |
| `async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]` | 源码未提供方法级文档字符串。 | [L83](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L83) |

### [`class AgentCronRegistry`](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L119)

Process-level registry of per-tenant Agent-side ``CronTools`` (+ scheduler).

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_lock` | `未显式标注` | `threading.Lock()` | [L122](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L122) |
| `_tools` | `ClassVar[dict[tuple[str, str], Any]]` | `{}` | [L123](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L123) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@staticmethod def _key(service_id: str, agent_id: str) -> tuple[str, str]` | 源码未提供方法级文档字符串。 | [L126](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L126) |
| `@classmethod def get_or_create(cls, service_id: str, agent_id: str, *, factory: Callable[[], T]) -> T` | Return shared CronTools for ``(service_id, agent_id)``, creating once. | [L133](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L133) |
| `@classmethod def register(cls, service_id: str, agent_id: str, tools: Any) -> None` | Idempotent put (e.g. after scheduler restart on an existing instance). | [L151](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L151) |
| `@classmethod def is_current(cls, service_id: str, agent_id: str, tools: Any) -> bool` | True iff ``tools`` is the live registry entry for the tenant. | [L158](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L158) |
| `@classmethod async def remove(cls, service_id: str, agent_id: str) -> bool` | Stop Agent-side scheduler for the tenant and drop the registry entry. | [L165](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L165) |
| `@classmethod def reset_for_tests(cls) -> None` | 源码未提供方法级文档字符串。 | [L191](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L191) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve_agent_side_cron_deps(*, agent_client: Any \| None = None, message_handler: Any \| None = None) -> tuple[Any, Any]` | Resolve wake client + push handler for Agent-side CronSchedulerService. | [L93](../../../../../jiuwenswarm/server/runtime/cron_local_runtime.py#L93) |

## `jiuwenswarm/server/runtime/debug_trace/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/__init__.py#L1)

**模块职责：** Agent/Code debug trace — request-level human-readable dump.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/debug_trace/__init__.py#L31) |

## `jiuwenswarm/server/runtime/debug_trace/config.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L1)

**模块职责：** Effective debug-trace settings resolution.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L125](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L125) |

### [`class DebugTraceSettings`](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L22)

Resolved debug-trace behaviour for one run.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `mode` | `str` | `—` | [L25](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L25) |
| `enabled` | `bool` | `—` | [L26](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L26) |
| `dump_enabled` | `bool` | `—` | [L27](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L27) |
| `otel_enabled` | `bool` | `—` | [L28](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L28) |
| `include_model_output` | `bool` | `True` | [L30](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L30) |
| `include_reasoning` | `bool` | `True` | [L31](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L31) |
| `include_tool_args` | `bool` | `True` | [L32](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L32) |
| `include_tool_result` | `bool` | `True` | [L33](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L33) |
| `include_subagent_flow` | `bool` | `True` | [L36](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L36) |
| `tool_args_max_chars` | `int` | `2000` | [L38](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L38) |
| `tool_result_max_chars` | `int` | `8000` | [L39](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L39) |
| `generic_payload_max_chars` | `int` | `4000` | [L40](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L40) |
| `max_model_output_chars` | `int \| None` | `None` | [L41](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L41) |
| `redact_prompts` | `bool` | `False` | [L43](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L43) |
| `redact_completions` | `bool` | `False` | [L44](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L44) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _load_debug_trace_config() -> dict[str, Any]` | Best-effort read of the ``debug_trace`` config block. | [L47](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L47) |
| `def _mode_key(mode: str) -> str` | 源码未提供函数级文档字符串。 | [L58](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L58) |
| `def _as_int(value: Any, default: int) -> int` | 源码未提供函数级文档字符串。 | [L62](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L62) |
| `def resolve_debug_trace_settings(*, mode: str, request_debug: bool) -> DebugTraceSettings` | Resolve effective settings for *mode* given the request-level flag. | [L71](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L71) |

## `jiuwenswarm/server/runtime/debug_trace/context.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L1)

**模块职责：** Process-wide ContextVar bridging a run's :class:`DebugTraceLogger` to the subagent dispatch sites (``TaskTool`` in the SDK, ``AgentTool`` in jiuwenswarm).

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_DEBUG_TRACE_LOGGER` | `ContextVar[Optional['DebugTraceLogger']]` | [L24](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L24) |
| `_LOGGERS_BY_SESSION` | `dict[str, 'DebugTraceLogger']` | [L33](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L33) |
| `__all__` | `未显式标注` | [L78](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L78) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_debug_trace_logger() -> Optional['DebugTraceLogger']` | Return the active run's logger, or ``None`` when not in a debug run. | [L36](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L36) |
| `def set_debug_trace_logger(logger: Optional['DebugTraceLogger']) -> Token` | Publish *logger* as the active run's logger; returns a reset token. | [L41](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L41) |
| `def reset_debug_trace_logger(token: Token) -> None` | Restore the previous logger binding using the token from :func:`set`. | [L46](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L46) |
| `def register_debug_trace_logger(session_id: str, logger: 'DebugTraceLogger') -> None` | Register *logger* as the active logger for *session_id*. | [L51](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L51) |
| `def unregister_debug_trace_logger(session_id: str) -> None` | Drop the logger registered for *session_id* (no-op if none / id empty). | [L63](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L63) |
| `def get_debug_trace_logger_for_session(session_id: str) -> Optional['DebugTraceLogger']` | Return the logger registered for *session_id*, or ``None``. | [L69](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L69) |

## `jiuwenswarm/server/runtime/debug_trace/directives.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L1)

**模块职责：** Slash directive parsing — shared between Agent/Code and Team modes.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `DEBUG_PREFIX` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L17) |
| `_LEADING_SYSTEM_REMINDER_RE` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L24) |
| `__all__` | `未显式标注` | [L88](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L88) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def strip_slash_directive(query: str, prefix: str) -> tuple[str, bool]` | Strip a leading ``<prefix> `` directive from *query*. | [L29](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L29) |
| `def _split_leading_system_reminder(query: str) -> tuple[str, str]` | Split a leading ``<system-reminder>...</system-reminder>`` block off. | [L51](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L51) |
| `def strip_debug_directive(query: str) -> tuple[str, bool]` | Strip a leading ``/debug `` directive from *query* (Agent/Code path). | [L64](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L64) |

## `jiuwenswarm/server/runtime/debug_trace/paths.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L1)

**模块职责：** Debug trace directory / file resolution.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L46) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _safe_segment(value: str, fallback: str = '_') -> str` | Sanitize an untrusted string into a single safe path segment. | [L21](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L21) |
| `def debug_trace_dir(mode: str) -> Path` | Return the trace directory for *mode* (``.agent`` or ``.code``). | [L33](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L33) |
| `def debug_trace_file(mode: str, session_id: str) -> Path` | Return the per-session dump file path for *mode*. | [L40](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L40) |

## `jiuwenswarm/server/runtime/debug_trace/stream_logger.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L1)

**模块职责：** Generic Agent/Code debug trace logger.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_logger` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L34) |
| `_CHUNK_LLM_OUTPUT` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L37) |
| `_CHUNK_LLM_REASONING` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L38) |
| `_CHUNK_LLM_USAGE` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L39) |
| `_CHUNK_ANSWER` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L40) |
| `_CHUNK_TOOL_CALL` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L41) |
| `_CHUNK_TOOL_UPDATE` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L42) |
| `_CHUNK_TOOL_RESULT` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L43) |
| `_ACCUMULATING_TYPES` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L45) |
| `_CATEGORY_LEVEL` | `未显式标注` | [L48](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L48) |
| `_SECRET_TOKENS` | `未显式标注` | [L66](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L66) |
| `_UNKNOWN` | `未显式标注` | [L93](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L93) |
| `__all__` | `未显式标注` | [L598](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L598) |

### [`class _Run`](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L188)

A pending accumulation of token-streamed chunks (single source).

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('category', 'source', 'buf', 'llm_output_seen')` | [L191](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L191) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, category: str, source: str = 'main') -> None` | 源码未提供方法级文档字符串。 | [L193](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L193) |

### [`class DebugTraceLogger`](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L203)

Best-effort human-readable dump writer for one Agent/Code run.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, file_path: Path \| str, mode: str, session_id: str, request_id: str \| None, round_id: int \| None = None, settings: DebugTraceSettings) -> None` | 源码未提供方法级文档字符串。 | [L211](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L211) |
| `def start_run(self, *, input_text: str \| None = None, otel_trace_id: str = '', otel_span_id: str = '') -> None` | 源码未提供方法级文档字符串。 | [L252](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L252) |
| `def captures_subagent_flow(self) -> bool` | True if this run should capture subagent streams into the dump. | [L286](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L286) |
| `def feed(self, chunk: Any) -> None` | 源码未提供方法级文档字符串。 | [L290](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L290) |
| `def feed_subagent(self, *, source: str, chunk: Any) -> None` | Feed a chunk from an in-flight subagent stream. | [L293](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L293) |
| `def begin_subagent(self, *, source: str, prompt: str = '') -> None` | 源码未提供方法级文档字符串。 | [L304](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L304) |
| `def end_subagent(self, *, source: str, status: str = 'ok') -> None` | 源码未提供方法级文档字符串。 | [L318](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L318) |
| `def _feed_safe(self, chunk: Any, source: str) -> None` | 源码未提供方法级文档字符串。 | [L330](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L330) |
| `def end_run(self, *, status: str, error: BaseException \| None = None, error_type: str \| None = None, error_message: str \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L338](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L338) |
| `def flush(self) -> None` | 源码未提供方法级文档字符串。 | [L378](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L378) |
| `def _feed_chunk(self, chunk: Any, source: str) -> None` | 源码未提供方法级文档字符串。 | [L396](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L396) |
| `@staticmethod def _unpack(chunk: Any) -> tuple[str \| None, Any]` | Extract (type, payload) from a chunk, tolerating shapes. | [L441](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L441) |
| `def _discrete_summary(self, ctype: str, category: str, payload: Any) -> str` | 源码未提供方法级文档字符串。 | [L450](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L450) |
| `def _tool_call_summary(self, payload: Any, args_limit: int) -> str` | 源码未提供方法级文档字符串。 | [L468](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L468) |
| `def _tool_result_summary(self, payload: Any, result_limit: int) -> str` | 源码未提供方法级文档字符串。 | [L484](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L484) |
| `def _tool_update_summary(self, payload: Any, args_limit: int) -> str` | 源码未提供方法级文档字符串。 | [L505](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L505) |
| `def _usage_summary(self, payload: Any) -> str` | 源码未提供方法级文档字符串。 | [L522](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L522) |
| `def _flush_run(self) -> None` | 源码未提供方法级文档字符串。 | [L537](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L537) |
| `def _emit(self, category: str, source: str, content: str) -> None` | 源码未提供方法级文档字符串。 | [L546](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L546) |
| `def _write_raw(self, body: str) -> None` | 源码未提供方法级文档字符串。 | [L554](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L554) |
| `def _safe_warn(self, msg: str) -> None` | 源码未提供方法级文档字符串。 | [L568](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L568) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _looks_secret(key: str) -> bool` | True if *key* names a secret-like field whose value should be masked. | [L72](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L72) |
| `def _now_ts() -> str` | 源码未提供函数级文档字符串。 | [L96](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L96) |
| `def _truncate(text: str, limit: int \| None) -> str` | Truncate *text* to *limit* chars with a visible original-length marker. | [L100](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L100) |
| `def _mask_secrets(value: Any) -> Any` | Recursively mask values whose dict key looks secret-like. | [L112](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L112) |
| `def _mask_arguments(value: Any) -> Any` | Mask secret keys inside tool-call arguments, tolerating JSON-string shape. | [L134](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L134) |
| `def _extract_content(payload: Any) -> str` | 源码未提供函数级文档字符串。 | [L158](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L158) |
| `def _classify(ctype: str, payload: Any) -> str` | 源码未提供函数级文档字符串。 | [L166](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L166) |
| `def _as_dict(value: Any) -> Any` | Coerce a payload into a dict for masking when it's dict-like. | [L577](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L577) |
| `def _stringify(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L582](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L582) |
| `def _safe_str(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L591](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L591) |

## `jiuwenswarm/server/runtime/debug_trace/subagent_capture.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L1)

**模块职责：** Capture a subagent's stream into the active run's debug dump.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_logger` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L27) |
| `__all__` | `未显式标注` | [L180](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L180) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _ensure_observability_rail(subagent: Any) -> None` | Attach ``ObservabilityRail`` to *subagent* for correct OTel scoping. | [L30](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L30) |
| `async def invoke_subagent_with_trace(subagent: Any, *, inputs: dict, session: Any, source_label: str) -> dict` | Run *subagent*, capturing its stream into the active run's debug dump. | [L80](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L80) |
| `def _reduce_stream_chunk(chunk: Any, output_parts: list[str]) -> dict \| None` | Reduce a stream chunk to an invoke-style result dict (or ``None``). | [L147](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L147) |

## `jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L1)

**模块职责：** Monkeypatch the SDK's builtin ``TaskTool.invoke`` to capture subagent streams.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_PATCH_APPLIED` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L23) |
| `__all__` | `未显式标注` | [L160](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L160) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_sub_session_id(parent_session_id: str, subagent_type: str) -> str` | Mirror of ``TaskTool._build_sub_session_id``. | [L26](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L26) |
| `def apply_task_tool_debug_patch() -> None` | Patch ``TaskTool.invoke`` to capture subagent streams under ``/debug``. | [L40](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L40) |

## `jiuwenswarm/server/runtime/enterprise_config/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/__init__.py#L1)

**模块职责：** 从 Gateway 本地库解析企业级配置生效策略与模板。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L8](../../../../../jiuwenswarm/server/runtime/enterprise_config/__init__.py#L8) |

## `jiuwenswarm/server/runtime/enterprise_config/apply_models.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/apply_models.py#L1)

**模块职责：** 将 ``EffectiveEnterpriseConfig`` 中的模型模板写入 config 快照（覆盖 ``config.yaml`` 对应槽位）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SLOT_TO_CONFIG_KEY` | `dict[TemplateRefSlot, str]` | [L13](../../../../../jiuwenswarm/server/runtime/enterprise_config/apply_models.py#L13) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def model_entity_to_config_entry(entity: dict[str, Any]) -> dict[str, Any]` | 将 ``model_template`` 行转为 ``get_default_models`` 兼容的条目。 | [L21](../../../../../jiuwenswarm/server/runtime/enterprise_config/apply_models.py#L21) |
| `def embedding_entity_to_config_section(entity: dict[str, Any]) -> dict[str, str]` | 将 ``embedding_template`` 行转为 ``config.yaml`` 的 ``embed`` 配置段。 | [L47](../../../../../jiuwenswarm/server/runtime/enterprise_config/apply_models.py#L47) |
| `def apply_enterprise_models_to_config(config_base: dict[str, Any], enterprise: EffectiveEnterpriseConfig) -> tuple[dict[str, Any], bool]` | 深拷贝 ``config_base`` 并写入企业模型与 Embedding 槽位；返回 ``(merged, applied_any)``。 | [L56](../../../../../jiuwenswarm/server/runtime/enterprise_config/apply_models.py#L56) |

## `jiuwenswarm/server/runtime/enterprise_config/expressions.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L1)

**模块职责：** 策略表达式求值（``match_expr``；Agent 规则另可校验 ``agent_id`` 模板）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_VAR_PATTERN` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L12) |
| `_OR_SPLIT_PATTERN` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L13) |
| `_MAPPING_DIM_PATTERN` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L14) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def _lookup_mapping_by_part(part: str, *, template_type: str) -> str \| None` | 将 ``${user::…}`` / ``${group::…}`` 片段解析为映射表中的 ``template_id``。 | [L17](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L17) |
| `async def resolve_template_slot_ref(raw: Any, ctx: RoutingContext, *, template_type: str = TemplateRefSlot.DEFAULT_MODEL) -> str \| None` | 解析**单个槽位**的 ``template_ref`` 原始字符串，返回最终 ``template_id``（非整表映射）。 | [L68](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L68) |
| `async def resolve_slot_template_id_map(refs: dict[str, list[str]], ctx: RoutingContext) -> dict[str, list[str]]` | 将合并后的 ``template_ref``（槽位 -> 原始引用列表）解析为槽位 -> ``template_id`` 列表。 | [L114](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L114) |
| `def substitute_template(template: str, ctx: RoutingContext) -> str` | 将 ``${group_id}`` 等占位符替换为上下文值。 | [L164](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L164) |
| `def agent_rule_matches(rule: dict[str, Any], ctx: RoutingContext) -> bool` | 判断 Agent 策略行是否命中当前路由上下文。 | [L184](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L184) |
| `def evaluate_match_expr(expr: Any, ctx: RoutingContext) -> bool` | 对 ``match_expr`` 求值，判断当前上下文是否满足策略条件。 | [L215](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L215) |
| `def _eval_simple_comparison(text: str, ctx: RoutingContext) -> bool` | 将单行 ``match_expr`` 求值为是否命中。 | [L250](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L250) |
| `def _safe_eval_bool(node: ast.AST, ctx: RoutingContext) -> bool` | 在安全子集内对 ``ast`` 比较/布尔节点求值（仅 ``==``、``!=``、``and``、``or``）。 | [L280](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L280) |
| `def _safe_eval_value(node: ast.AST, env: dict[str, str]) -> Any` | 求值比较表达式中的左/右操作数：字面量常量或路由上下文字段名。 | [L319](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L319) |

## `jiuwenswarm/server/runtime/enterprise_config/gateway_db.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/gateway_db.py#L1)

**模块职责：** Gateway 本地库：企业配置读库 facade（企业版）。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L51](../../../../../jiuwenswarm/server/runtime/enterprise_config/gateway_db.py#L51) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def fetch_template_by_slot(slot: str, template_id: str) -> dict[str, Any] \| None` | 按 ``template_ref`` 槽位与 ``template_id`` 加载一条启用中的模板行。 | [L17](../../../../../jiuwenswarm/server/runtime/enterprise_config/gateway_db.py#L17) |
| `async def list_records(table: str, *, filters: dict[str, Any] \| None = None, order_by: str = '') -> list[dict[str, Any]]` | 列表查询（每网关独立 DB，不加实例隔离列）。 | [L38](../../../../../jiuwenswarm/server/runtime/enterprise_config/gateway_db.py#L38) |

## `jiuwenswarm/server/runtime/enterprise_config/loader.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L1)

**模块职责：** 从 Gateway DB 按实例 Agent 资源加载企业级生效配置。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L259](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L259) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def routing_context_from_request(request: AgentRequest \| Any) -> RoutingContext` | 从顶层 ``user_id`` + ``metadata.routing`` 解析路由上下文。 | [L23](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L23) |
| `def _apply_slot_entities(result: EffectiveEnterpriseConfig, slot: str, entities: list[dict[str, Any]]) -> None` | 源码未提供函数级文档字符串。 | [L36](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L36) |
| `def _any_requested_slot_loaded(result: EffectiveEnterpriseConfig, load_slots: frozenset[str]) -> bool` | 源码未提供函数级文档字符串。 | [L53](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L53) |
| `async def _fetch_slot_entities(slot: str, template_ids: list[str]) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L71) |
| `async def _fetch_instance_agent_resource(resource_id: str) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L89](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L89) |
| `async def _fetch_agent_template_row(template_id: str) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L100](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L100) |
| `def _literal_slot_template_id_map(refs: dict[str, list[str]]) -> dict[str, list[str]]` | 仅接受字面 ``template_id``；跳过 ``${...}`` / ``or`` 等映射表达式。 | [L111](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L111) |
| `async def load_effective_enterprise_config(request: AgentRequest \| Any, slots: Collection[TemplateRefSlot]) -> EffectiveEnterpriseConfig \| None` | 按 ``request.bot_id``（即 ``instance_agent_resource.resource_id``）加载 Agent 实例生效配置。 | [L131](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L131) |

## `jiuwenswarm/server/runtime/enterprise_config/schemas.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L1)

**模块职责：** 企业级配置路由上下文、加载结果与 ``template_ref`` 槽位定义。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SLOT_ENTITY_TABLE` | `dict[TemplateRefSlot, str]` | [L23](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L23) |
| `MODEL_SLOT_KEYS` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L34) |
| `DEFAULT_AGENT_LOAD_SLOTS` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L41) |
| `__all__` | `未显式标注` | [L131](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L131) |

### [`class TemplateRefSlot(StrEnum)`](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L10)

``template_ref`` JSON 键名（与 agent_template.template_ref 槽位一致）。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `DEFAULT_MODEL` | `未显式标注` | `'default_model'` | [L13](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L13) |
| `VIDEO_MODEL` | `未显式标注` | `'video_model'` | [L14](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L14) |
| `AUDIO_MODEL` | `未显式标注` | `'audio_model'` | [L15](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L15) |
| `VISION_MODEL` | `未显式标注` | `'vision_model'` | [L16](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L16) |
| `EMBEDDING_MODEL` | `未显式标注` | `'embedding_model'` | [L17](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L17) |
| `SKILL_WHITELIST` | `未显式标注` | `'skill_whitelist'` | [L18](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L18) |
| `EXTENSION_CONFIG` | `未显式标注` | `'extension_config'` | [L19](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L19) |
| `PERMISSIONS` | `未显式标注` | `'permissions'` | [L20](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L20) |

### [`class RoutingContext`](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L74)

企业配置路由三元组；不含 ``gateway_id``（Agent 业务不消费）。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `group_id` | `str` | `—` | [L77](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L77) |
| `bot_id` | `str` | `—` | [L78](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L78) |
| `user_id` | `str` | `—` | [L79](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L79) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def as_dict(self) -> dict[str, str]` | 源码未提供方法级文档字符串。 | [L81](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L81) |

### [`class EffectiveEnterpriseConfig`](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L90)

单次路由上下文下解析完成的企业级配置快照。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `routing` | `RoutingContext` | `—` | [L93](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L93) |
| `template_ref` | `dict[str, list[str]]` | `field(default_factory=dict)` | [L94](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L94) |
| `models` | `dict[str, list[dict[str, Any]]]` | `field(default_factory=dict)` | [L95](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L95) |
| `embedding` | `list[dict[str, Any]] \| None` | `None` | [L96](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L96) |
| `skill_whitelist` | `list[dict[str, Any]] \| None` | `None` | [L97](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L97) |
| `extension_config` | `list[dict[str, Any]] \| None` | `None` | [L98](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L98) |
| `permissions` | `list[dict[str, Any]] \| None` | `None` | [L99](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L99) |
| `service_id` | `str \| None` | `None` | [L100](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L100) |
| `agent_id` | `str \| None` | `None` | [L101](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L101) |
| `workspace_dir` | `str \| None` | `None` | [L102](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L102) |
| `send_file_allowed` | `bool` | `True` | [L103](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L103) |
| `resource_id` | `str \| None` | `None` | [L104](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L104) |
| `ref_template_id` | `str \| None` | `None` | [L105](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L105) |
| `agent_template` | `dict[str, Any] \| None` | `None` | [L106](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L106) |
| `instance_agent_resource` | `dict[str, Any] \| None` | `None` | [L107](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L107) |
| `debug` | `dict[str, Any]` | `field(default_factory=dict)` | [L108](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L108) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def as_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L110](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L110) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def normalize_template_ref(value: Any) -> dict[str, list[str]]` | 将 ``template_ref`` 规范为 ``{slot: [ref_string, ...]}``；空值键省略。 | [L50](../../../../../jiuwenswarm/server/runtime/enterprise_config/schemas.py#L50) |

## `jiuwenswarm/server/runtime/prewarm.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/prewarm.py#L1)

**模块职责：** AgentServer 端到端启动预热。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/prewarm.py#L32) |

### [`class WarmupModelClient(BaseModelClient)`](../../../../../jiuwenswarm/server/runtime/prewarm.py#L35)

Mock LLM client for startup warmup.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__client_name__` | `未显式标注` | `'warmup'` | [L45](../../../../../jiuwenswarm/server/runtime/prewarm.py#L45) |
| `__client_type__` | `未显式标注` | `'llm'` | [L46](../../../../../jiuwenswarm/server/runtime/prewarm.py#L46) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, model_config: Any, model_client_config: Any) -> None` | 源码未提供方法级文档字符串。 | [L48](../../../../../jiuwenswarm/server/runtime/prewarm.py#L48) |
| `async def invoke(self, messages: Any, **kwargs: Any) -> AssistantMessage` | 源码未提供方法级文档字符串。 | [L53](../../../../../jiuwenswarm/server/runtime/prewarm.py#L53) |
| `async def stream(self, messages: Any, **kwargs: Any)` | 源码未提供方法级文档字符串。 | [L56](../../../../../jiuwenswarm/server/runtime/prewarm.py#L56) |
| `async def generate_image(self, *args: Any, **kwargs: Any) -> None` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/prewarm.py#L59) |
| `async def generate_speech(self, *args: Any, **kwargs: Any) -> None` | 源码未提供方法级文档字符串。 | [L62](../../../../../jiuwenswarm/server/runtime/prewarm.py#L62) |
| `async def generate_video(self, *args: Any, **kwargs: Any) -> None` | 源码未提供方法级文档字符串。 | [L65](../../../../../jiuwenswarm/server/runtime/prewarm.py#L65) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_warmup_config_base() -> dict[str, Any]` | Deep copy 当前 config 并把模型 client_provider 改成 "warmup"。 | [L69](../../../../../jiuwenswarm/server/runtime/prewarm.py#L69) |
| `async def _cleanup_prewarm_agent(agent: Any) -> None` | 释放预热 agent 持有的 sys_operation / tool / rails 引用。 | [L91](../../../../../jiuwenswarm/server/runtime/prewarm.py#L91) |
| `async def warmup_import_and_checkpointer() -> None` | 阶段1/2：interface_deep import + checkpointer。 | [L111](../../../../../jiuwenswarm/server/runtime/prewarm.py#L111) |
| `async def warmup_deep_agent_query(*, query: str = 'hello', channel_id: str = '__prewarm__', mode: str = 'agent', timeout_s: float = 120.0, mock_model: bool = True) -> None` | 阶段3：创建临时 DeepAgent 并执行一次 query（端到端预热）。 | [L150](../../../../../jiuwenswarm/server/runtime/prewarm.py#L150) |
| `async def run_startup_warmup(*, query: str = 'hello', channel_id: str = '__prewarm__', mode: str = 'agent', timeout_s: float = 120.0, mock_model: bool = True) -> None` | 端到端预热：阶段1/2（import+checkpointer）→ 阶段3（临时 DeepAgent+query）。 | [L240](../../../../../jiuwenswarm/server/runtime/prewarm.py#L240) |

## `jiuwenswarm/server/runtime/proactive_adapter.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L1)

**模块职责：** ProactiveEngine 初始化与适配层。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L21) |
| `_proactive_push_inflight` | `set[str]` | [L26](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L26) |

### [`class ProactiveTriggerRequest`](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L30)

一次主动推荐触发请求的具名参数封装（G.FNM.03：多相关参数具名化）。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `session_id` | `str` | `—` | [L37](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L37) |
| `query` | `str` | `—` | [L38](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L38) |
| `decision` | `Any` | `—` | [L39](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L39) |
| `channel_id` | `str \| None` | `None` | [L40](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L40) |
| `on_delivered` | `Any` | `None` | [L41](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L41) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def build_proactive_agent()` | Build the lightweight proactive agent for proactive recommendation decisions. | [L44](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L44) |
| `async def trigger_main_agent(server, request: ProactiveTriggerRequest) -> bool` | Drive the main agent to run one round with the directive-style query. | [L79](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L79) |
| `async def init_proactive_engine(server, config: dict[str, Any] \| None = None) -> None` | 组装 ProactiveEngine + 注入专用 agent + 触发回调，挂到 server 上。 | [L219](../../../../../jiuwenswarm/server/runtime/proactive_adapter.py#L219) |

## `jiuwenswarm/server/runtime/prompt_attachment_loader.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L1)

**模块职责：** Prompt attachment directory loader for jiuwenswarm.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L27) |
| `SESSION_SOURCE` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L29) |
| `DEFAULT_MAX_FILE_CHARS` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L30) |
| `_SAFE_SESSION_CHARS` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L31) |
| `_TEXT_SUFFIXES` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L32) |
| `_README_TEXT` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L33) |
| `_KIND_BY_STEM` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L46) |
| `_USER_SOURCE` | `未显式标注` | [L53](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L53) |
| `_LOCKS_GUARD` | `未显式标注` | [L54](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L54) |
| `_PATH_LOCKS` | `dict[str, threading.Lock]` | [L55](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L55) |
| `__all__` | `未显式标注` | [L612](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L612) |

### [`class PromptAttachmentFileStore`](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L258)

File CRUD helper for session prompt attachment directories.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, root: Path \| str, *, max_file_chars: int = DEFAULT_MAX_FILE_CHARS) -> None` | 源码未提供方法级文档字符串。 | [L261](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L261) |
| `def bind_context(self, ctx: Any) -> 'PromptAttachmentContextStore'` | 源码未提供方法级文档字符串。 | [L265](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L265) |
| `def for_session(self, session_id: str) -> 'PromptAttachmentSessionStore'` | 源码未提供方法级文档字符串。 | [L268](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L268) |
| `def add_markdown(self, *, session_id: str, content: str, name: str \| None = None, section: str \| None = None, priority: int = 100, kind: PromptAttachmentKind \| str = PromptAttachmentKind.TEXT, source: str \| None = None, metadata: dict[str, Any] \| None = None) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L271](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L271) |
| `def update_markdown(self, id_or_name: str, *, session_id: str, content: str \| None = None, priority: int \| None = None, source: str \| None = None, kind: PromptAttachmentKind \| str \| None = None, metadata: dict[str, Any] \| None = None, metadata_replace: bool = False, replace: bool = False) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L298](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L298) |
| `def get(self, id_or_name: str, *, session_id: str) -> PromptAttachment \| None` | 源码未提供方法级文档字符串。 | [L334](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L334) |
| `def delete(self, id_or_name: str, *, session_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L340](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L340) |
| `def list(self, *, session_id: str) -> list[PromptAttachment]` | 源码未提供方法级文档字符串。 | [L350](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L350) |
| `def _item_from_file(self, path: Path, *, session_id: str) -> PromptAttachment \| None` | 源码未提供方法级文档字符串。 | [L359](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L359) |
| `def _resolve_id_or_name(self, id_or_name: str, *, session_id: str) -> Path \| None` | 源码未提供方法级文档字符串。 | [L380](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L380) |
| `def _file_path(self, *, session_id: str, name: str, suffix: str) -> Path` | 源码未提供方法级文档字符串。 | [L397](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L397) |
| `def _session_dir(self, session_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L401](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L401) |
| `@staticmethod def _item_id(*, session_id: str, section: str) -> str` | 源码未提供方法级文档字符串。 | [L405](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L405) |
| `@staticmethod def _section_from_name(name: str \| None) -> str` | 源码未提供方法级文档字符串。 | [L409](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L409) |
| `@staticmethod def _frontmatter(*, section: str, priority: int, kind: PromptAttachmentKind \| str, source: str, metadata: dict[str, Any] \| None) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L417](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L417) |
| `def _read_text_file(self, path: Path, session_dir: Path) -> str \| None` | 源码未提供方法级文档字符串。 | [L435](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L435) |
| `@staticmethod def _coerce_kind(kind: PromptAttachmentKind \| str) -> PromptAttachmentKind` | 源码未提供方法级文档字符串。 | [L456](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L456) |
| `@staticmethod def _safe_relative_file_name(*, name: str, suffix: str) -> Path` | 源码未提供方法级文档字符串。 | [L460](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L460) |
| `@staticmethod def relative_key(path: Path, session_dir: Path) -> str` | 源码未提供方法级文档字符串。 | [L479](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L479) |

### [`class PromptAttachmentContextStore`](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L484)

Context-bound file writer that hides the session id from callers.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, store: PromptAttachmentFileStore, ctx: Any) -> None` | 源码未提供方法级文档字符串。 | [L487](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L487) |
| `def add_markdown(self, **kwargs: Any) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L491](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L491) |
| `def update_markdown(self, id_or_name: str, **kwargs: Any) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L494](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L494) |
| `def get(self, id_or_name: str) -> PromptAttachment \| None` | 源码未提供方法级文档字符串。 | [L497](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L497) |
| `def delete(self, id_or_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L500](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L500) |
| `def list(self) -> list[PromptAttachment]` | 源码未提供方法级文档字符串。 | [L503](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L503) |

### [`class PromptAttachmentSessionStore`](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L507)

Session-bound writer for services that know session_id but not full ctx.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, store: PromptAttachmentFileStore, *, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L510](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L510) |
| `def add_markdown(self, **kwargs: Any) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L514](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L514) |
| `def update_markdown(self, id_or_name: str, **kwargs: Any) -> PromptAttachment` | 源码未提供方法级文档字符串。 | [L517](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L517) |
| `def get(self, id_or_name: str) -> PromptAttachment \| None` | 源码未提供方法级文档字符串。 | [L520](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L520) |
| `def delete(self, id_or_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L523](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L523) |
| `def list(self) -> list[PromptAttachment]` | 源码未提供方法级文档字符串。 | [L526](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L526) |

### [`class PromptAttachmentLoader`](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L530)

Load jiuwenswarm prompt attachment files into a DeepAgent manager.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, root: Path \| str, *, max_file_chars: int = DEFAULT_MAX_FILE_CHARS) -> None` | 源码未提供方法级文档字符串。 | [L533](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L533) |
| `def bind_context(self, ctx: Any) -> PromptAttachmentContextStore` | Return a context-bound file writer facade. | [L538](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L538) |
| `def for_session(self, session_id: str) -> PromptAttachmentSessionStore` | Return a session-bound file writer facade. | [L543](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L543) |
| `def ensure_layout(self) -> None` | Create the root prompt attachment layout. | [L548](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L548) |
| `def load_session_attachments(self, session_id: str) -> list[PromptAttachment]` | Load prompt attachments for one jiuwenswarm session. | [L563](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L563) |
| `async def sync_to_agent(self, agent: Any, *, session_id: str) -> None` | Synchronize current prompt attachment files to a DeepAgent instance. | [L568](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L568) |
| `@staticmethod def kind_for_file(path: Path) -> PromptAttachmentKind` | 源码未提供方法级文档字符串。 | [L604](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L604) |
| `@staticmethod def relative_key(path: Path, session_dir: Path) -> str` | 源码未提供方法级文档字符串。 | [L608](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L608) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _is_relative_to(path: Path, root: Path) -> bool` | 源码未提供函数级文档字符串。 | [L58](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L58) |
| `def _is_reparse_path(path: Path) -> bool` | Return True for symlink, junction, or other Windows reparse-point paths. | [L66](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L66) |
| `def _iter_safe_files(session_dir: Path, suffixes: frozenset[str]) -> Iterable[Path]` | 源码未提供函数级文档字符串。 | [L81](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L81) |
| `def _metadata_with_origin_source(metadata: dict[str, Any], origin_source: str \| None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L117](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L117) |
| `def sanitize_session_id(session_id: str \| None) -> str` | Return a deterministic path-safe session id. | [L124](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L124) |
| `def _safe_id_part(value: str) -> str` | 源码未提供函数级文档字符串。 | [L141](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L141) |
| `def _path_lock(path: Path) -> threading.Lock` | 源码未提供函数级文档字符串。 | [L149](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L149) |
| `def _atomic_write_text(path: Path, text: str) -> None` | 源码未提供函数级文档字符串。 | [L159](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L159) |
| `def _kind_value(kind: PromptAttachmentKind \| str) -> str` | 源码未提供函数级文档字符串。 | [L166](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L166) |
| `def _parse_scalar(value: str) -> Any` | 源码未提供函数级文档字符串。 | [L170](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L170) |
| `def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]` | 源码未提供函数级文档字符串。 | [L184](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L184) |
| `def _dump_frontmatter(data: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L218](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L218) |
| `def _resolve_from_context(ctx: Any, *names: str) -> str \| None` | 源码未提供函数级文档字符串。 | [L232](../../../../../jiuwenswarm/server/runtime/prompt_attachment_loader.py#L232) |

## `jiuwenswarm/server/runtime/reload_result.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/reload_result.py#L1)

**模块职责：** Result types and logging helpers for agent.reload_config hot-reload.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_MISSING` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/reload_result.py#L11) |
| `AGENT_CONFIG_HOT_RELOAD_MARKER` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/reload_result.py#L13) |
| `AGENT_CONFIG_HOT_RELOAD_REPLAY_MARKER` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/reload_result.py#L14) |
| `_SENSITIVE_ERROR_PATTERN` | `未显式标注` | [L16](../../../../../jiuwenswarm/server/runtime/reload_result.py#L16) |
| `_MAX_ERROR_LOG_LEN` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/reload_result.py#L19) |
| `MEMORY_ENV_KEYS` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/reload_result.py#L21) |
| `EXTERNAL_MEMORY_ENV_KEYS` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/reload_result.py#L25) |
| `TASK_MEMORY_ENV_KEYS` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/reload_result.py#L42) |
| `SHARED_SKILLS_ENV_KEYS` | `未显式标注` | [L62](../../../../../jiuwenswarm/server/runtime/reload_result.py#L62) |

### [`class ReloadResult`](../../../../../jiuwenswarm/server/runtime/reload_result.py#L149)

Per-session reload outcome.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `applied` | `bool` | `False` | [L152](../../../../../jiuwenswarm/server/runtime/reload_result.py#L152) |
| `deferred` | `bool` | `False` | [L153](../../../../../jiuwenswarm/server/runtime/reload_result.py#L153) |
| `error` | `str \| None` | `None` | [L154](../../../../../jiuwenswarm/server/runtime/reload_result.py#L154) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L156](../../../../../jiuwenswarm/server/runtime/reload_result.py#L156) |

### [`class ReloadAggregateResult`](../../../../../jiuwenswarm/server/runtime/reload_result.py#L168)

Aggregated reload stats for AgentManager / TenantAgentPool.

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `applied` | `int` | `0` | [L171](../../../../../jiuwenswarm/server/runtime/reload_result.py#L171) |
| `deferred` | `int` | `0` | [L172](../../../../../jiuwenswarm/server/runtime/reload_result.py#L172) |
| `failed` | `list[dict[str, str]]` | `field(default_factory=list)` | [L173](../../../../../jiuwenswarm/server/runtime/reload_result.py#L173) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def merge(self, result: ReloadResult, *, session_key: str = '') -> None` | 源码未提供方法级文档字符串。 | [L175](../../../../../jiuwenswarm/server/runtime/reload_result.py#L175) |
| `def to_payload(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L183](../../../../../jiuwenswarm/server/runtime/reload_result.py#L183) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def env_touches_external_memory(env_overrides: Any) -> bool` | Return True when reload env payload may affect external memory provider. | [L70](../../../../../jiuwenswarm/server/runtime/reload_result.py#L70) |
| `def env_touches_memory(env_overrides: Any) -> bool` | Return True when reload env payload may affect memory / embedding. | [L77](../../../../../jiuwenswarm/server/runtime/reload_result.py#L77) |
| `def env_touches_task_memory(env_overrides: Any) -> bool` | Return True when reload env payload may affect TaskMemoryService. | [L85](../../../../../jiuwenswarm/server/runtime/reload_result.py#L85) |
| `def env_touches_shared_skills_dirs(env_overrides: Any) -> bool` | Return True when reload env payload may change shared skill directories. | [L92](../../../../../jiuwenswarm/server/runtime/reload_result.py#L92) |
| `def embed_config_fingerprint(config: Any) -> tuple[Any, ...]` | 源码未提供函数级文档字符串。 | [L102](../../../../../jiuwenswarm/server/runtime/reload_result.py#L102) |
| `def memory_cache_fingerprint(config: Any) -> str` | Stable short hash for memory manager cache keys (engine + embed triple). | [L113](../../../../../jiuwenswarm/server/runtime/reload_result.py#L113) |
| `def reload_touches_memory(env_overrides: Any, config: Any, *, previous_config: Any = None) -> bool` | Return True when a reload may require rebuilding memory manager caches. | [L123](../../../../../jiuwenswarm/server/runtime/reload_result.py#L123) |
| `def collect_config_path_keys(config: Any, *, prefix: str = '') -> list[str]` | Collect dot-separated paths for all keys in a config dict (values omitted). | [L192](../../../../../jiuwenswarm/server/runtime/reload_result.py#L192) |
| `def _diff_config_paths(old: dict[str, Any], new: dict[str, Any], *, prefix: str = '') -> list[str]` | 源码未提供函数级文档字符串。 | [L205](../../../../../jiuwenswarm/server/runtime/reload_result.py#L205) |
| `def collect_changed_config_paths(previous: Any, current: Any) -> list[str]` | Return config paths that differ between previous and current snapshots. | [L221](../../../../../jiuwenswarm/server/runtime/reload_result.py#L221) |
| `def collect_env_override_keys(env: Any) -> tuple[list[str], list[str]]` | Return (updated_keys, removed_keys) from an incremental env reload payload. | [L230](../../../../../jiuwenswarm/server/runtime/reload_result.py#L230) |
| `def redact_reload_error_message(error: str \| None) -> str` | Return a short, redacted error string safe for hot-reload logs. | [L239](../../../../../jiuwenswarm/server/runtime/reload_result.py#L239) |
| `def format_reload_changed_keys(*, env: Any, config: Any, previous_config: Any = None, updated_param_keys: set[str] \| list[str] \| None = None) -> dict[str, Any]` | Build changed-key fields for logging (keys/paths only, never values). | [L251](../../../../../jiuwenswarm/server/runtime/reload_result.py#L251) |
| `def log_agent_config_hot_reload(logger: logging.Logger, *, reload_trace_id: str \| None, phase: str, source: str, level: int = logging.INFO, **fields: Any) -> None` | Emit a unified hot-reload log line (never includes config/env values). | [L273](../../../../../jiuwenswarm/server/runtime/reload_result.py#L273) |
| `def summarize_reload_payload(payload: Any) -> dict[str, Any]` | Extract applied/deferred/failed summary for completed-phase logs. | [L300](../../../../../jiuwenswarm/server/runtime/reload_result.py#L300) |
| `def log_agent_config_hot_reload_replay(logger: logging.Logger, *, reload_trace_id: str \| None, session: str, agent_key: str, mode: str, config: Any, env: Any, source: str = 'AgentManager') -> None` | Log config replay on new session creation (counts only, no values). | [L323](../../../../../jiuwenswarm/server/runtime/reload_result.py#L323) |
| `def log_reload_config_changes(logger: logging.Logger, *, env: Any, config: Any, previous_config: Any = None, reload_trace_id: str \| None = None, source: str = 'agent.reload_config', updated_param_keys: set[str] \| list[str] \| None = None, config_set_req_id: str \| None = None) -> None` | Log modified config/env keys only (never values) for hot-reload tracing. | [L353](../../../../../jiuwenswarm/server/runtime/reload_result.py#L353) |

## `jiuwenswarm/server/runtime/runtime_scope.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L1)

**模块职责：** Runtime scope key for multi-tenant isolation of process-level managers.

### [`class RuntimeScopeKey`](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L21)

Immutable scope for runtime registries (Team / Rail / Ask / DeepResearch).

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `service_id` | `str` | `'default'` | [L24](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L24) |
| `agent_id` | `str` | `'default'` | [L25](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L25) |
| `workspace_key` | `str` | `'default'` | [L26](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L26) |
| `session_id` | `str` | `''` | [L27](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L27) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def tenant(self) -> tuple[str, str, str]` | 源码未提供方法级文档字符串。 | [L29](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L29) |
| `def session_key(self) -> tuple[str, str, str, str]` | 源码未提供方法级文档字符串。 | [L32](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L32) |
| `def with_session(self, session_id: str \| None) -> RuntimeScopeKey` | 源码未提供方法级文档字符串。 | [L35](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L35) |
| `@classmethod def from_ids(cls, service_id: str \| None = None, agent_id: str \| None = None, session_id: str \| None = None, workspace_key: str \| None = None) -> RuntimeScopeKey` | 源码未提供方法级文档字符串。 | [L44](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L44) |
| `@classmethod def from_request(cls, request: Any, *, include_session: bool = False) -> RuntimeScopeKey` | 源码未提供方法级文档字符串。 | [L59](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L59) |
| `@classmethod def from_adapter(cls, adapter: Any, *, session_id: str \| None = None) -> RuntimeScopeKey` | 源码未提供方法级文档字符串。 | [L78](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L78) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _norm_id(value: str \| None, *, default: str = 'default') -> str` | 源码未提供函数级文档字符串。 | [L15](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L15) |

## `jiuwenswarm/server/runtime/session/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/runtime/session/git_diff_status.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L1)

**模块职责：** DiffStatusService: 面向 Web 的 diff 状态聚合服务(设计文档 §2.4 / §3.5 / §4.1.16)。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L22) |
| `_service_instance` | `DiffStatusService \| None` | [L933](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L933) |
| `_FILES_EVENT_FIELDS` | `tuple[str, ...]` | [L950](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L950) |

### [`class DiffStats`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L75)

Diff 变更统计,``DiffSummary`` 和 ``DiffTurnSummary`` 共用。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `files_changed` | `int` | `0` | [L78](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L78) |
| `lines_added` | `int` | `0` | [L79](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L79) |
| `lines_removed` | `int` | `0` | [L80](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L80) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L82](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L82) |

### [`class DiffHunk`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L91)

单个 hunk 的结构化表示。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `old_start` | `int` | `0` | [L94](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L94) |
| `old_lines` | `int` | `0` | [L95](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L95) |
| `new_start` | `int` | `0` | [L96](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L96) |
| `new_lines` | `int` | `0` | [L97](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L97) |
| `lines` | `list[str]` | `field(default_factory=list)` | [L98](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L98) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L100](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L100) |

### [`class DiffFileEntry`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L111)

单个文件的 diff 条目。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `file_path` | `str` | `''` | [L114](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L114) |
| `status` | `str` | `'modified'` | [L115](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L115) |
| `lines_added` | `int` | `0` | [L116](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L116) |
| `lines_removed` | `int` | `0` | [L117](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L117) |
| `is_binary` | `bool` | `False` | [L118](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L118) |
| `is_new_file` | `bool` | `False` | [L119](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L119) |
| `is_deleted_file` | `bool` | `False` | [L120](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L120) |
| `is_untracked` | `bool` | `False` | [L121](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L121) |
| `is_large_file` | `bool` | `False` | [L122](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L122) |
| `is_truncated` | `bool` | `False` | [L123](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L123) |
| `hunks` | `list[DiffHunk]` | `field(default_factory=list)` | [L124](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L124) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L126](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L126) |

### [`class DiffSummary`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L145)

当前工作区 diff 的摘要对象。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `is_dirty` | `bool` | `False` | [L148](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L148) |
| `stats` | `DiffStats` | `field(default_factory=DiffStats)` | [L149](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L149) |
| `files` | `dict[str, DiffFileEntry]` | `field(default_factory=dict)` | [L150](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L150) |
| `kind` | `str` | `'working_tree'` | [L151](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L151) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L153](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L153) |

### [`class DiffTurnSummary`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L166)

上一轮对话 diff 的摘要对象。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `turn_index` | `int` | `0` | [L169](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L169) |
| `timestamp` | `str` | `''` | [L170](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L170) |
| `user_prompt_preview` | `str` | `''` | [L171](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L171) |
| `stats` | `DiffStats` | `field(default_factory=DiffStats)` | [L172](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L172) |
| `files` | `dict[str, DiffFileEntry]` | `field(default_factory=dict)` | [L173](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L173) |
| `kind` | `str` | `'conversation_turn'` | [L174](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L174) |
| `change_set_id` | `str` | `''` | [L176](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L176) |
| `request_id` | `str` | `''` | [L177](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L177) |
| `assistant_message_id` | `str` | `''` | [L178](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L178) |
| `user_message_id` | `str` | `''` | [L179](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L179) |
| `status` | `str` | `'completed'` | [L180](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L180) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L182) |

### [`class DiffRepoInfo`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L204)

Diff 状态中的仓库元信息子对象。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `is_git` | `bool` | `False` | [L207](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L207) |
| `repo_root` | `str \| None` | `None` | [L208](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L208) |
| `branch` | `str \| None` | `None` | [L209](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L209) |
| `head` | `str \| None` | `None` | [L210](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L210) |
| `transient` | `bool` | `False` | [L211](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L211) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L213](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L213) |

### [`class ProjectGitDiffStatus`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L224)

Diff 状态聚合的顶层返回对象。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `project_id` | `str` | `''` | [L227](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L227) |
| `session_id` | `str \| None` | `None` | [L228](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L228) |
| `work_mode` | `str` | `'work'` | [L229](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L229) |
| `repo` | `DiffRepoInfo` | `field(default_factory=DiffRepoInfo)` | [L230](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L230) |
| `current` | `DiffSummary \| None` | `None` | [L231](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L231) |
| `last_turn` | `DiffTurnSummary \| None` | `None` | [L232](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L232) |
| `generated_at` | `float` | `0.0` | [L233](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L233) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L235](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L235) |

### [`class DiffStatusService`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L706)

面向 Web 的 diff 状态聚合服务(设计文档 §2.4 / §4.1.16)。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@staticmethod def get_project_diff_status(*, project: Any, session_id: str \| None = None, include_files: bool = False, include_hunks: bool = False, hunk_paths: list[str] \| set[str] \| tuple[str, ...] \| None = None) -> ProjectGitDiffStatus` | 聚合当前工作区 diff 和上一轮对话 diff(设计文档 §4.1.16)。 | [L715](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L715) |
| `@staticmethod def get_turn_diff_list(*, project: Any, session_id: str, limit: int = 50, cursor: int = 0) -> dict[str, Any]` | 返回历史轮次摘要列表。 | [L844](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L844) |
| `@staticmethod def get_turn_diff_detail(*, project: Any, session_id: str, turn_index: int \| None = None, change_set_id: str \| None = None, include_files: bool = True, include_hunks: bool = True) -> dict[str, Any] \| None` | 返回指定轮次详情，优先按 ``change_set_id`` 查询。 | [L890](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L890) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _safe_team_path_segment(value: str, fallback: str = '_') -> str` | Sanitize a value into one path segment for team workspace paths. | [L25](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L25) |
| `def _session_team_member_names(session_id: str \| None) -> list[str]` | Return member names observed in persisted team events for a session. | [L32](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L32) |
| `def _to_relative_path(file_path: str, repo_root: str \| None) -> str` | 将绝对路径转换为相对 ``repo_root`` 的路径;无法转换时返回原路径。 | [L247](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L247) |
| `def _infer_file_status(entry: dict[str, Any]) -> str` | 从 DiffService 文件条目推断 ``DiffFileEntry.status`` 字段。 | [L260](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L260) |
| `def _convert_stats(raw_stats: dict[str, Any] \| None) -> DiffStats` | 转换 camelCase stats → snake_case DiffStats。 | [L282](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L282) |
| `def get_session_extra_history_roots(session_id: str \| None, *, sessions_root: str \| Path \| None = None, agent_workspace_root: str \| Path \| None = None) -> list[str]` | Return team/member/worktree/sub-agent roots for file history monitoring. | [L293](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L293) |
| `def _discover_sub_agent_workspaces(session_id: str, add_root: Callable[[Any], None], *, agent_workspace_root: str \| Path \| None = None) -> None` | Scan workspace/sub_agents for sub-agent dirs belonging to *session_id*. | [L401](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L401) |
| `def _convert_hunks(raw_hunks: list[dict[str, Any]] \| None) -> list[DiffHunk]` | 转换 camelCase hunk 列表 → snake_case DiffHunk 列表。 | [L443](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L443) |
| `def _convert_file_entry(file_path: str, entry: dict[str, Any], *, repo_root: str \| None, include_hunks: bool) -> DiffFileEntry` | 转换单个 DiffService 文件条目 → DiffFileEntry。 | [L461](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L461) |
| `def _convert_file_map(raw_files: dict[str, Any] \| None, *, repo_root: str \| None, include_files: bool, include_hunks: bool) -> dict[str, DiffFileEntry]` | 转换 DiffService files 映射 → DiffFileEntry 映射。 | [L489](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L489) |
| `def _convert_current_diff(raw_diff: dict[str, Any] \| None, *, repo_root: str \| None, include_files: bool, include_hunks: bool, repo_is_dirty: bool = False) -> DiffSummary` | 转换 ``DiffService.get_git_diff()`` 返回 → DiffSummary。 | [L510](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L510) |
| `def _convert_turn_diff(turn: dict[str, Any] \| None, *, repo_root: str \| None, include_files: bool, include_hunks: bool) -> DiffTurnSummary \| None` | 转换单个 turn diff dict → DiffTurnSummary。 | [L552](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L552) |
| `def _historical_repo_context(turn: dict[str, Any], fallback: dict[str, Any] \| None = None) -> dict[str, Any]` | 返回持久化的历史 Git 上下文，缺失时使用当前上下文兜底。 | [L583](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L583) |
| `def _convert_turn_summary(turn: dict[str, Any], *, repo_context: dict[str, Any] \| None = None) -> dict[str, Any]` | 转换单个 turn diff dict 为摘要(不含 hunks)。 | [L595](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L595) |
| `def _supports_no_git_fallback(error: Any) -> bool` | Return True when Jiuwen file-op history can back the diff view. | [L633](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L633) |
| `def _repo_context_from_status(project: Any, *, reject_transient: bool = False) -> dict[str, Any]` | 读取 Git 上下文，必要时抛出结构化 Git 错误。 | [L641](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L641) |
| `def _repo_context_for_history(project: Any) -> dict[str, Any]` | 历史轮次回放专用:读取 Git 上下文,失败时降级为 project_dir 兜底。 | [L681](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L681) |
| `def get_diff_status_service() -> DiffStatusService` | 返回 ``DiffStatusService`` 单例。 | [L936](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L936) |
| `def reset_diff_status_service() -> None` | 重置单例(仅供测试)。 | [L944](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L944) |
| `def file_entry_to_dict_no_hunks(entry: dict[str, Any]) -> dict[str, Any]` | 将已序列化的文件条目 dict 转换为不含 hunk 的事件格式。 | [L957](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L957) |
| `def file_map_to_dict_no_hunks(files_dict: dict[str, Any] \| None) -> dict[str, Any]` | 批量转换文件映射:去除 hunk,过滤非 dict 条目。 | [L982](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L982) |
| `def extract_files_from_status(status_dict: dict[str, Any], source: str) -> dict[str, Any] \| None` | 从 ``ProjectGitDiffStatus.to_dict()`` 中提取指定 source 的 files 映射。 | [L1001](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L1001) |
| `def build_summary_entry(current: dict[str, Any] \| None) -> dict[str, Any] \| None` | 构造 summary 事件/快照中的 current 条目(``files`` 固定 ``{}``)。 | [L1023](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L1023) |
| `def build_turn_summary_entry(last_turn: dict[str, Any] \| None) -> dict[str, Any] \| None` | 构造 summary 事件/快照中的 last_turn 条目(``files`` 固定 ``{}``)。 | [L1041](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py#L1041) |

## `jiuwenswarm/server/runtime/session/git_diff_watcher.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1)

**模块职责：** GitDiffWatcherRegistry: diff 实时监控核心逻辑(设计文档 §2.5 / §3.6 / §4.2)。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L21) |
| `POLL_INTERVAL_SEC` | `float` | [L24](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L24) |
| `DEBOUNCE_SEC` | `float` | [L26](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L26) |
| `ERROR_BACKOFF_SEC` | `float` | [L28](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L28) |
| `_STRUCTURAL_ERROR_CODES` | `frozenset[str]` | [L31](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L31) |
| `MAX_PUSH_FAILURES` | `int` | [L37](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L37) |
| `_registry_instance` | `GitDiffWatcherRegistry \| None` | [L1294](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1294) |

### [`class GitDiffWatch`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L41)

单条 diff 监控订阅。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `watch_id` | `str` | `—` | [L53](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L53) |
| `project_id` | `str` | `—` | [L54](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L54) |
| `session_id` | `str` | `—` | [L55](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L55) |
| `ws` | `Any` | `—` | [L56](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L56) |
| `scope` | `str` | `'summary'` | [L57](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L57) |
| `files_source` | `str \| None` | `None` | [L58](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L58) |
| `detail_source` | `str \| None` | `None` | [L59](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L59) |
| `detail_files` | `set[str]` | `field(default_factory=set)` | [L60](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L60) |
| `include_last_turn` | `bool` | `True` | [L61](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L61) |
| `last_summary_fingerprint` | `str` | `''` | [L62](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L62) |
| `last_files_fingerprint` | `str` | `''` | [L63](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L63) |
| `last_detail_fingerprint` | `str` | `''` | [L64](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L64) |
| `wake_event` | `asyncio.Event` | `field(default_factory=asyncio.Event)` | [L65](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L65) |
| `push_failures` | `int` | `0` | [L68](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L68) |

### [`class GitDiffFilesState`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L72)

Snapshot of the files subscription state for rollback.

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `files_source` | `str \| None` | `—` | [L75](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L75) |
| `last_files_fingerprint` | `str` | `—` | [L76](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L76) |

### [`class GitDiffDetailState`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L80)

Snapshot of the detail subscription state for rollback.

装饰器：`@dataclass(frozen=True, slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `detail_source` | `str \| None` | `—` | [L83](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L83) |
| `detail_files` | `set[str]` | `—` | [L84](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L84) |
| `last_detail_fingerprint` | `str` | `—` | [L85](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L85) |

### [`class GitDiffWatcherRegistry`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L187)

diff 实时监控注册中心(设计文档 §2.5 / §4.2)。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, channel: Any = None) -> None` | 源码未提供方法级文档字符串。 | [L197](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L197) |
| `def set_channel(self, channel: Any) -> None` | 注入 WebChannel 实例(用于 send_event)。 | [L205](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L205) |
| `async def add_watch(self, ws: Any, project_id: str, session_id: str, scope: str = 'summary', *, include_last_turn: bool = True, on_initial: Any = None) -> GitDiffWatch` | 新增 diff 监控订阅(设计文档 §4.2.1)。 | [L209](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L209) |
| `async def remove_watch(self, watch_id: str, *, scope: str = 'all', expected_ws: Any = None) -> GitDiffWatch \| None` | 取消监控(设计文档 §4.2.4)。 | [L271](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L271) |
| `async def update_files(self, watch_id: str, source: str, *, expected_ws: Any = None, expected_project_id: str \| None = None) -> GitDiffWatch \| None` | 开启或切换文件列表监控(设计文档 §4.2.2)。 | [L301](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L301) |
| `async def snapshot_files_state(self, watch_id: str, *, expected_ws: Any = None, expected_project_id: str \| None = None) -> GitDiffFilesState \| None` | Capture files subscription state before a tentative update. | [L337](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L337) |
| `async def restore_files_state(self, watch_id: str, state: GitDiffFilesState, *, expected_ws: Any = None) -> None` | Restore files subscription state after a failed first snapshot. | [L361](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L361) |
| `async def update_files_with_restore(self, watch_id: str, source: str, *, expected_ws: Any = None, expected_project_id: str \| None = None, on_snapshot: Any = None) -> GitDiffWatch \| None` | 原子地更新 files 订阅,并提供失败时自动回滚的钩子。 | [L382](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L382) |
| `async def update_detail(self, watch_id: str, source: str, files: list[str], *, expected_ws: Any = None, expected_project_id: str \| None = None) -> GitDiffWatch \| None` | 切换文件内容监控对象(设计文档 §4.2.3)。 | [L432](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L432) |
| `async def snapshot_detail_state(self, watch_id: str, *, expected_ws: Any = None, expected_project_id: str \| None = None) -> GitDiffDetailState \| None` | Capture detail subscription state before a tentative update. | [L467](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L467) |
| `async def restore_detail_state(self, watch_id: str, state: GitDiffDetailState, *, expected_ws: Any = None) -> None` | Restore detail subscription state after a failed first snapshot. | [L492](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L492) |
| `async def update_detail_with_restore(self, watch_id: str, source: str, files: list[str], *, expected_ws: Any = None, expected_project_id: str \| None = None, on_snapshot: Any = None) -> GitDiffWatch \| None` | 原子地更新 detail 订阅,并提供失败时自动回滚的钩子。 | [L514](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L514) |
| `def mark_dirty(self, project_id: str, *, watch_id: str \| None = None) -> None` | 标记脏数据,唤醒轮询任务立即重算(设计文档 §2.5)。 | [L556](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L556) |
| `def seed_summary_fingerprint(self, watch_id: str, status_dict: dict[str, Any]) -> None` | 用首次快照种子 summary fingerprint(内部接口,见 ``commit_initial_summary``)。 | [L568](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L568) |
| `def seed_files_fingerprint(self, watch_id: str, status_dict: dict[str, Any], source: str) -> None` | 用首次快照种子 files fingerprint(内部接口,见 ``commit_initial_files``)。 | [L584](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L584) |
| `def seed_detail_fingerprint(self, watch_id: str, status_dict: dict[str, Any], source: str, detail_files: list[str]) -> None` | 用首次快照种子 detail fingerprint(内部接口,见 ``commit_initial_detail``)。 | [L594](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L594) |
| `def commit_initial_summary(self, watch_id: str, status_dict: dict[str, Any]) -> None` | 提交 summary 首次快照并唤醒轮询。 | [L611](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L611) |
| `def commit_initial_files(self, watch_id: str, status_dict: dict[str, Any], source: str) -> None` | 提交 files 首次快照并唤醒轮询。 | [L632](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L632) |
| `def commit_initial_detail(self, watch_id: str, status_dict: dict[str, Any], source: str, detail_files: list[str]) -> None` | 提交 detail 首次快照并唤醒轮询。 | [L651](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L651) |
| `def cleanup_ws(self, ws: Any) -> None` | 清理该连接下所有 watcher(设计文档 §4.2.0 / §5.2.4)。 | [L671](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L671) |
| `def cleanup_project(self, project_id: str) -> None` | 清理该项目下所有 watcher(设计文档 §4.1.4 / §4.2.0)。 | [L695](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L695) |
| `def _remove_watch_internal(self, watch_id: str) -> GitDiffWatch \| None` | 从所有索引中移除 watcher(调用方需持锁)。 | [L715](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L715) |
| `def _ensure_poll_task(self, project_id: str) -> None` | 确保该 project 的轮询任务已启动。 | [L735](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L735) |
| `def _cancel_poll_task(self, project_id: str) -> None` | 取消并移除该 project 的轮询任务。 | [L751](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L751) |
| `def shutdown(self) -> None` | 取消所有轮询任务,释放后台资源(供单例重置/进程退出时调用)。 | [L757](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L757) |
| `def _wake_project(self, project_id: str) -> None` | 唤醒该 project 所有 watcher 的 wake_event。 | [L762](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L762) |
| `def _get_watches_for_project(self, project_id: str) -> list[GitDiffWatch]` | 获取该 project 的所有活跃 watcher(快照)。 | [L772](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L772) |
| `async def _poll_loop(self, project_id: str) -> None` | 每个 project_id 一个轮询任务,共享 diff 计算。 | [L782](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L782) |
| `async def _compute_and_push(self, project_id: str, watches: list[GitDiffWatch]) -> None` | 计算 diff 并对变化的 watcher 推送事件。 | [L871](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L871) |
| `@staticmethod def _extract_files(status_dict: dict[str, Any], source: str) -> dict[str, Any] \| None` | 从 status_dict 中提取指定 source 的 files 映射。 | [L1053](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1053) |
| `async def _push_diff_changed(self, watch: GitDiffWatch, status_dict: dict[str, Any], fingerprint: str, revision: str \| None = None) -> None` | 推送 ``project.git.diff_changed`` 事件(设计文档 §3.6)。 | [L1067](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1067) |
| `@staticmethod def _summary_entry(current: dict[str, Any] \| None) -> dict[str, Any]` | 从 current diff 提取 summary 事件所需字段(仅统计,files 固定 ``{}``)。 | [L1114](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1114) |
| `@staticmethod def _turn_summary_entry(last_turn: dict[str, Any] \| None) -> dict[str, Any] \| None` | 从 last_turn diff 提取 summary 事件所需字段(仅统计,files 固定 ``{}``)。 | [L1127](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1127) |
| `async def _push_files_changed(self, watch: GitDiffWatch, status_dict: dict[str, Any], files_dict: dict[str, Any] \| None, fingerprint: str, revision: str \| None = None) -> None` | 推送 ``project.git.diff_files_changed`` 事件(设计文档 §3.6)。 | [L1139](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1139) |
| `async def _push_detail_changed(self, watch: GitDiffWatch, status_dict: dict[str, Any], files_dict: dict[str, Any] \| None, fingerprint: str, revision: str \| None = None) -> None` | 推送 ``project.git.diff_detail_changed`` 事件(设计文档 §3.6)。 | [L1177](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1177) |
| `@staticmethod def _last_turn_event_metadata(status_dict: dict[str, Any]) -> dict[str, Any]` | 提取 last_turn 的稳定绑定元数据。 | [L1220](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1220) |
| `async def _on_push_failure(self, watch: GitDiffWatch) -> None` | 推送失败计数;连续失败达到阈值后回收 watcher。 | [L1234](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1234) |
| `async def _push_error_event(self, project_id: str, exc: Exception) -> None` | 推送 ``project.git.error`` 事件(设计文档 §3.6)。 | [L1250](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1250) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _fingerprint(*parts: Any) -> str` | 对任意可序列化部分计算稳定指纹,用于变化检测。 | [L88](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L88) |
| `def _summary_fingerprint(status_dict: dict[str, Any], *, include_last_turn: bool = True) -> str` | summary 层指纹:覆盖 repo 元信息 + current/last_turn 统计 + dirty 状态。 | [L104](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L104) |
| `def _files_fingerprint(files_dict: dict[str, Any] \| None) -> str` | 文件列表层指纹:覆盖文件路径、状态、行数统计(不含 hunk)。 | [L139](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L139) |
| `def _detail_fingerprint(files_dict: dict[str, Any] \| None, detail_files: set[str]) -> str` | 详情层指纹:仅覆盖已订阅文件的 hunk 内容。 | [L157](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L157) |
| `def _build_revision(prefix: str, fingerprint: str) -> str` | 构造 revision 字符串,用于事件 payload 的版本标记。 | [L180](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L180) |
| `def get_git_diff_watcher_registry() -> GitDiffWatcherRegistry` | 返回全局 ``GitDiffWatcherRegistry`` 单例。 | [L1297](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1297) |
| `def reset_git_diff_watcher_registry() -> None` | 重置单例(仅供测试)。 | [L1305](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L1305) |

## `jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L1)

**模块职责：** Session lifecycle hooks for Ascend KV cache affinity.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L17) |
| `KVAction` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L19) |
| `ASCEND_AFFINITY_PROVIDER` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L20) |
| `_BACKGROUND_ACTIONS` | `set[asyncio.Task[KVCacheLifecycleResult]]` | [L251](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L251) |
| `_BACKGROUND_TAILS` | `dict[str, asyncio.Task[KVCacheLifecycleResult]]` | [L252](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L252) |

### [`class KVCacheLifecycleResult`](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L24)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `status` | `Literal['ok', 'failed', 'skipped', 'scheduled']` | `—` | [L25](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L25) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def ok(self) -> bool` | 源码未提供方法级文档字符串。 | [L28](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L28) |
| `@property def failed(self) -> bool` | 源码未提供方法级文档字符串。 | [L32](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L32) |
| `@property def scheduled(self) -> bool` | 源码未提供方法级文档字符串。 | [L36](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L36) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _result(status: Literal['ok', 'failed', 'skipped', 'scheduled']) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L40](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L40) |
| `def is_kv_cache_affinity_enabled(config: dict[str, Any] \| None = None) -> bool` | Return whether jiuwenswarm should emit Ascend KV lifecycle calls. | [L46](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L46) |
| `def _model_supports_kv_cache_affinity(model: Any) -> bool` | 源码未提供函数级文档字符串。 | [L54](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L54) |
| `def _normalize_provider(provider: Any) -> str` | 源码未提供函数级文档字符串。 | [L64](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L64) |
| `def _is_ascend_affinity_provider(provider: Any) -> bool` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L71) |
| `def _provider_from_model(model: Any) -> str` | 源码未提供函数级文档字符串。 | [L75](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L75) |
| `def _default_model_provider(config: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L80](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L80) |
| `def _model_from_jiuwenswarm_agent(agent: Any) -> Any \| None` | 源码未提供函数级文档字符串。 | [L88](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L88) |
| `def _default_model_entry(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] \| None` | 源码未提供函数级文档字符串。 | [L117](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L117) |
| `def _build_default_model(config: dict[str, Any]) -> Any \| None` | 源码未提供函数级文档字符串。 | [L136](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L136) |
| `def resolve_kv_cache_affinity_model(*, agent: Any = None, config: dict[str, Any] \| None = None) -> Any \| None` | 源码未提供函数级文档字符串。 | [L158](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L158) |
| `async def run_session_kv_cache_lifecycle(action: KVAction, *, session_id: str, parent_session_id: str \| None = None, agent: Any = None, config: dict[str, Any] \| None = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | Run a session-level KV lifecycle action when Ascend affinity is enabled. | [L169](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L169) |
| `def dispatch_session_kv_cache_lifecycle(action: Literal['offload', 'prefetch'], *, session_id: str, parent_session_id: str \| None = None, agent: Any = None, config: dict[str, Any] \| None = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | Schedule a root signal while retaining and observing its task. | [L255](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L255) |
| `async def cancel_pending_kv_cache_lifecycle_tasks() -> None` | 源码未提供函数级文档字符串。 | [L332](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L332) |
| `async def prefetch_session_kv_cache(*, session_id: str, parent_session_id: str \| None = None, agent: Any = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L341](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L341) |
| `def dispatch_prefetch_session_kv_cache(*, session_id: str, parent_session_id: str \| None = None, agent: Any = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L357](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L357) |
| `async def offload_session_kv_cache(*, session_id: str, parent_session_id: str \| None = None, agent: Any = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L373](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L373) |
| `def dispatch_offload_session_kv_cache(*, session_id: str, parent_session_id: str \| None = None, agent: Any = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L389](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L389) |
| `async def evict_session_kv_cache(*, session_id: str, parent_session_id: str \| None = None, agent: Any = None, timeout: float \| None = None) -> KVCacheLifecycleResult` | 源码未提供函数级文档字符串。 | [L405](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L405) |

## `jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L1)

**模块职责：** Product-session hooks for Ascend KV cache affinity.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L14) |

### [`class SessionSwitchContext`](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L18)

Facts needed by the product owner and its optional KVC hooks.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `target_is_team` | `bool` | `—` | [L21](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L21) |
| `previous_is_team` | `bool` | `—` | [L22](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L22) |
| `resolved_mode` | `str` | `—` | [L23](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L23) |
| `affinity_enabled` | `bool` | `—` | [L24](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L24) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def cancel_pending_tasks() -> None` | Best-effort cleanup for all Agent-side KVC signal registries. | [L27](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L27) |
| `async def evict_plan_session(*, session_id: str, agent: Any = None, agent_manager: Any = None, channel_id: str \| None = None) -> bool` | Best-effort evict for a permanently deleted non-Team session. | [L66](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L66) |
| `def resolve_session_switch_context(*, target_session_id: str, previous_session_id: str, params: dict[str, Any]) -> SessionSwitchContext` | Resolve switch facts without changing the product runtime. | [L108](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L108) |
| `async def dispatch_session_switch_signals(*, context: SessionSwitchContext, agent_manager: Any, channel_id: str, team_manager: Any, target_session_id: str, previous_session_id: str, reason: str) -> None` | Send optional KVC signals after the product owner handles the switch. | [L184](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L184) |

## `jiuwenswarm/server/runtime/session/permission_response_ledger.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L1)

**模块职责：** Process-local deduplication for permission continuation responses.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_MAX_RECENT_KEYS` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L11) |
| `_PermissionResponseKey` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L12) |

### [`class PermissionResponseReservation`](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L16)

A single permission response's right to enter the runtime.

装饰器：`@dataclass(eq=False)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_ledger` | `PermissionResponseLedger` | `—` | [L19](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L19) |
| `_key` | `_PermissionResponseKey` | `—` | [L20](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L20) |
| `_started` | `bool` | `False` | [L21](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L21) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def key(self) -> _PermissionResponseKey` | Return the session-scoped response key. | [L24](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L24) |
| `@property def started(self) -> bool` | Return whether this reservation has entered the runtime. | [L29](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L29) |
| `def start(self) -> bool` | Claim runtime entry if this reservation is still current. | [L33](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L33) |
| `def complete(self) -> None` | Remember a response after it has entered the runtime. | [L40](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L40) |
| `def release_if_unstarted(self) -> None` | Release a queued reservation so a retry can replace it. | [L44](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L44) |

### [`class PermissionResponseLedger`](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L49)

Track active and recently executed permission responses.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, *, max_recent_keys: int = _MAX_RECENT_KEYS) -> None` | 源码未提供方法级文档字符串。 | [L52](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L52) |
| `def reserve(self, session_id: str, response_id: str) -> PermissionResponseReservation \| None` | Reserve an opaque response ID once for a session. | [L61](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L61) |
| `def is_current_reservation(self, reservation: PermissionResponseReservation) -> bool` | Return whether a reservation is still the current active entry. | [L74](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L74) |
| `def complete_reservation(self, reservation: PermissionResponseReservation) -> None` | Complete a reservation and remember its key if it was started. | [L81](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L81) |
| `def release_if_unstarted(self, reservation: PermissionResponseReservation) -> None` | Release a reservation only if it has not started. | [L95](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py#L95) |

## `jiuwenswarm/server/runtime/session/project_git.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1)

**模块职责：** ProjectGitService: 项目目录的 Git 仓库探测与分支操作服务(设计文档 §3.4 / §6)。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L28) |
| `GIT_COMMAND_TIMEOUT_SEC` | `float` | [L58](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L58) |
| `GIT_DIFF_TIMEOUT_SEC` | `float` | [L59](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L59) |
| `GIT_PUSH_TIMEOUT_SEC` | `float` | [L61](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L61) |
| `_GIT_OUTPUT_TRUNCATE` | `未显式标注` | [L64](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L64) |
| `_service_instance` | `ProjectGitService \| None` | [L2178](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L2178) |

### [`class GitError`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L68)

Git 操作失败时的结构化错误对象。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `code` | `str` | `—` | [L71](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L71) |
| `message` | `str` | `—` | [L72](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L72) |
| `command` | `str` | `''` | [L73](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L73) |
| `exit_code` | `int \| None` | `None` | [L74](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L74) |
| `stdout` | `str` | `''` | [L75](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L75) |
| `stderr` | `str` | `''` | [L76](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L76) |
| `hint` | `str` | `''` | [L77](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L77) |
| `retryable` | `bool` | `False` | [L78](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L78) |
| `repo` | `dict[str, Any] \| None` | `None` | [L79](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L79) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L81](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L81) |

### [`class GitRepoStatus`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L96)

某一时刻项目目录的 Git 仓库完整状态。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `is_git` | `bool` | `False` | [L99](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L99) |
| `repo_root` | `str \| None` | `None` | [L100](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L100) |
| `branch` | `str \| None` | `None` | [L101](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L101) |
| `head` | `str \| None` | `None` | [L102](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L102) |
| `detached` | `bool` | `False` | [L103](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L103) |
| `transient` | `bool` | `False` | [L104](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L104) |
| `upstream` | `str \| None` | `None` | [L105](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L105) |
| `is_dirty` | `bool` | `False` | [L106](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L106) |
| `staged` | `int` | `0` | [L107](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L107) |
| `unstaged` | `int` | `0` | [L108](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L108) |
| `untracked` | `int` | `0` | [L109](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L109) |
| `conflicted` | `int` | `0` | [L110](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L110) |
| `local_branches` | `list[str]` | `field(default_factory=list)` | [L111](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L111) |
| `remote_branches` | `list[str]` | `field(default_factory=list)` | [L112](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L112) |
| `error` | `GitError \| None` | `None` | [L113](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L113) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L115](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L115) |

### [`class GitProbeResult`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L136)

``ensure_on_project_create()`` 返回值。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `status` | `str` | `—` | [L139](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L139) |
| `repo_root` | `str \| None` | `None` | [L140](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L140) |
| `branch` | `str \| None` | `None` | [L141](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L141) |
| `initialized_by_jiuwenswarm` | `bool` | `False` | [L142](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L142) |
| `error` | `GitError \| None` | `None` | [L143](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L143) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L145](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L145) |

### [`class GitOperationResult`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L156)

``switch_branch()`` / ``create_branch()`` / ``commit()`` / ``push()`` 等写操作的返回值。

装饰器：`@dataclass(slots=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `success` | `bool` | `—` | [L159](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L159) |
| `repo_status` | `GitRepoStatus` | `—` | [L160](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L160) |
| `previous_branch` | `str \| None` | `None` | [L161](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L161) |
| `error` | `GitError \| None` | `None` | [L162](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L162) |
| `commit_hash` | `str \| None` | `None` | [L164](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L164) |
| `pushed_remote` | `str \| None` | `None` | [L166](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L166) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L168](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L168) |

### [`class GitOperationError(RuntimeError)`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L179)

Git 操作失败,携带结构化 ``GitError`` 供 handler 层映射错误码。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, git_error: GitError) -> None` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L182) |

### [`class ProjectGitService`](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L837)

项目 Git 服务(设计文档 §3.4 / §6)。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def ensure_on_project_create(self, project: Project) -> GitProbeResult` | 新建项目时探测/初始化 Git(设计文档 §6)。 | [L845](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L845) |
| `def _probe_on_project_create(self, project: Project) -> GitProbeResult` | ``ensure_on_project_create`` 的纯探测逻辑,不持久化。 | [L870](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L870) |
| `@staticmethod def probe(project: Project) -> GitRepoStatus` | 重新探测项目 Git 状态并刷新 ``Project.git`` 快照,不执行 ``git init``。 | [L947](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L947) |
| `@staticmethod def status(project: Project) -> GitRepoStatus` | 查询项目 Git 状态(不持久化)。 | [L953](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L953) |
| `@staticmethod def init(project: Project, initial_branch: str = 'main') -> GitRepoStatus` | 初始化 Git 仓库,写回 ``Project.git`` 快照。 | [L958](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L958) |
| `@staticmethod def switch_branch(project: Project, branch: str, *, require_clean: bool = False) -> GitOperationResult` | 切换分支。 | [L1062](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1062) |
| `@staticmethod def create_branch(project: Project, branch: str, *, checkout: bool = True, start_point: str \| None = None) -> GitOperationResult` | 新建分支,可选同时切换。 | [L1311](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1311) |
| `@staticmethod def _read_branch(project_dir: str) -> str \| None` | 读取当前分支名(辅助 ensure_on_project_create)。 | [L1534](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1534) |
| `@staticmethod def commit(project: Project, message: str, *, stage_all: bool = False, paths: list[str] \| None = None, amend: bool = False, no_verify: bool = False) -> GitOperationResult` | 提交当前工作区改动到当前分支(设计文档 §4.9 ``project.git.commit``)。 | [L1545](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1545) |
| `@staticmethod def push(project: Project, *, remote: str = 'origin', branch: str \| None = None, set_upstream: bool = False, force: bool = False, delete: bool = False) -> GitOperationResult` | 推送本地分支到远程(设计文档 §4.10 ``project.git.push``)。 | [L1904](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L1904) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _env_float(name: str, default: float, *, min_value: float = 0.1) -> float` | Read a float environment variable with a safe fallback. | [L31](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L31) |
| `def resolve_git_project(project_id: str, *, cache_bust: bool = False) -> tuple[Any, str \| None, str \| None]` | 校验并加载可用于 Git 操作的 code 项目(共享 helper)。 | [L187](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L187) |
| `def send_git_error_response(channel: Any, ws: Any, req_id: str, error: Any) -> Any` | 发送 Git 结构化错误响应(共享 helper)。 | [L219](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L219) |
| `def _find_git_executable() -> str \| None` | 查找 git 可执行文件,找不到返回 ``None``。 | [L253](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L253) |
| `def _is_transient_state(project_dir: str) -> tuple[bool, str]` | 检测 merge/rebase/cherry-pick 中间状态。 | [L260](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L260) |
| `def _run_git(args: list[str], *, cwd: str, timeout: float = GIT_COMMAND_TIMEOUT_SEC, stdin_input: str \| None = None) -> subprocess.CompletedProcess[str]` | 执行 git 命令,禁止 ``shell=True``。 | [L289](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L289) |
| `def _dubious_ownership_error_if_needed(project: Project, project_dir: str, result: subprocess.CompletedProcess[str]) -> GitError \| None` | Return a structured error when Git rejects repo ownership. | [L332](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L332) |
| `def _truncate(s: str) -> str` | 源码未提供函数级文档字符串。 | [L353](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L353) |
| `def _is_branch_held_by_worktree(stderr: str) -> bool` | 检测 git stderr 是否表示目标分支被 worktree 占用。 | [L357](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L357) |
| `def _find_worktrees_holding_branch(repo_root: str, branch: str) -> list[str]` | 返回占用目标分支的 worktree 工作目录路径(不含主仓库)。 | [L367](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L367) |
| `def _make_error(code: str, message: str, *, command: str = '', exit_code: int \| None = None, stdout: str = '', stderr: str = '', hint: str = '', retryable: bool = False, project: Project \| None = None) -> GitError` | 源码未提供函数级文档字符串。 | [L404](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L404) |
| `def _make_repo_error(code: str, message: str, project: Project, *, command: str = '', exit_code: int \| None = None, stdout: str = '', stderr: str = '', hint: str = '', retryable: bool = False, branch: str \| None = None, transient: bool = False) -> GitError` | 构造带完整 repo 上下文的 GitError。 | [L437](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L437) |
| `def _file_not_found_error(project: Project, project_dir: str, *, branch: str \| None = None, command: str = '') -> GitError` | 区分 FileNotFoundError 来源:cwd 不存在 → PROJECT_DIR_MISSING,git 可执行文件缺失 → GIT_NOT_FOUND。 | [L470](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L470) |
| `def _git_to_repo_status(project: Project, *, persist: bool = False) -> GitRepoStatus` | 读取项目目录的 Git 状态,返回 ``GitRepoStatus``。 | [L503](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L503) |
| `def _persist_git_snapshot(project: Project, status: GitRepoStatus) -> None` | 将 ``GitRepoStatus`` 写回 ``Project.git`` 子对象并持久化。 | [L685](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L685) |
| `def _persist_probe_result(project: Project, result: GitProbeResult) -> None` | 将 ``GitProbeResult`` 写回 ``Project.git`` 子对象并持久化。 | [L727](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L727) |
| `def _map_status_string(status: GitRepoStatus) -> str` | 从 GitRepoStatus 推断 Project.git.status 字符串。 | [L756](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L756) |
| `def _validate_branch_name(branch: str, project: Project) -> str` | 分支名校验,非法时抛 ``GitOperationError(BRANCH_INVALID)``。 | [L772](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L772) |
| `def get_project_git_service() -> ProjectGitService` | 返回 ``ProjectGitService`` 单例。 | [L2181](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L2181) |
| `def reset_project_git_service() -> None` | 重置单例(仅供测试)。 | [L2189](../../../../../jiuwenswarm/server/runtime/session/project_git.py#L2189) |

## `jiuwenswarm/server/runtime/session/project_store.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L1)

**模块职责：** 项目存储模块 — projects.json 的持久化与 CRUD。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L41) |
| `_VERSION` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L43) |
| `_PROJECT_ID_PREFIX` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L44) |
| `_PROJECT_ID_HEX_LEN` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L45) |
| `_CACHE` | `list[dict[str, Any]] \| None` | [L48](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L48) |
| `_CACHE_LOCK` | `未显式标注` | [L49](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L49) |
| `_LOCK_SUFFIX` | `未显式标注` | [L52](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L52) |
| `_LOCK_TIMEOUT_SEC` | `未显式标注` | [L53](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L53) |
| `_T` | `未显式标注` | [L229](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L229) |
| `_DIR_ILLEGAL_CHARS` | `未显式标注` | [L722](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L722) |
| `_DIR_RESERVED_NAMES` | `未显式标注` | [L724](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L724) |

### [`class Project`](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L112)

项目实体(对应 projects.json 中单个项目记录)。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `project_id` | `str` | `—` | [L115](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L115) |
| `name` | `str` | `—` | [L116](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L116) |
| `project_dir` | `str` | `—` | [L117](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L117) |
| `pinned` | `bool` | `False` | [L118](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L118) |
| `pin_order` | `int` | `0` | [L119](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L119) |
| `hidden` | `bool` | `False` | [L120](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L120) |
| `created_at` | `float` | `0.0` | [L121](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L121) |
| `updated_at` | `float` | `0.0` | [L122](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L122) |
| `work_mode` | `str` | `DEFAULT_WEB_WORK_MODE` | [L124](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L124) |
| `git` | `dict[str, Any]` | `field(default_factory=dict)` | [L127](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L127) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L129](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L129) |
| `@classmethod def from_dict(cls, d: dict[str, Any]) -> 'Project'` | 源码未提供方法级文档字符串。 | [L133](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L133) |

### [`class CronProjectBinding`](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L154)

Resolved project ownership for cron jobs.

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `project_id` | `str` | `—` | [L157](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L157) |
| `work_mode` | `str` | `—` | [L158](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L158) |
| `error` | `str \| None` | `None` | [L159](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L159) |
| `code` | `str \| None` | `None` | [L160](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L160) |
| `hidden` | `bool` | `False` | [L161](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L161) |

### [`class ProjectDirConflict(Exception)`](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L699)

``project_dir`` 与已有可见项目重复(由 ``create_or_restore_project`` 在锁内抛出)。

### [`class ProjectNameConflict(Exception)`](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L703)

``name`` 与已有项目(含隐藏)重复(由 ``create_or_restore_project`` / ``rename_project`` / ``restore_project`` 在锁内抛出)。

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@contextmanager def _file_lock(data_path: Path) -> Iterator[None]` | 跨进程文件锁。锁文件为 ``<data_path>.lock``,与数据文件分离, 因此数据文件的原子替换不会破坏锁。 | [L92](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L92) |
| `def _projects_file() -> Path` | 源码未提供函数级文档字符串。 | [L167](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L167) |
| `def _now() -> float` | 源码未提供函数级文档字符串。 | [L171](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L171) |
| `def _gen_project_id() -> str` | 源码未提供函数级文档字符串。 | [L175](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L175) |
| `def _read_disk_locked(path: Path) -> list[dict[str, Any]]` | 在文件锁内读取磁盘(调用方须已加锁)。文件缺失/损坏时返回空列表。 | [L180](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L180) |
| `def _fsync_dir(directory: Path) -> None` | fsync 父目录,确保 ``os.replace`` 的目录项落盘(断电耐久性)。 | [L196](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L196) |
| `def _write_disk_locked(path: Path, projects: list[dict[str, Any]]) -> None` | 在文件锁内原子写入(调用方须已加锁)。 | [L216](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L216) |
| `def _mutate(fn: Callable[[list[dict[str, Any]]], _T]) -> _T` | 在文件锁保护下: 重读磁盘 → 应用变更 → 原子写回 → 刷新缓存。 | [L232](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L232) |
| `def _load_cache(cache_bust: bool = False) -> list[dict[str, Any]]` | 读取缓存;``cache_bust=True`` 强制读盘(跨进程同步场景)。 | [L248](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L248) |
| `def get_project_by_id(project_id: str, *, cache_bust: bool = False) -> Project \| None` | 按 project_id 查找项目(默认项目不入库,不会命中)。 | [L288](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L288) |
| `def get_project_by_dir(project_dir: str, *, cache_bust: bool = False) -> Project \| None` | 按 project_dir 查找项目(不限 hidden 状态,由调用方判断)。 | [L298](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L298) |
| `def _normalize_path_for_match(path: str) -> str` | 规范化路径用于跨平台匹配(容忍尾部分隔符/大小写差异)。 | [L317](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L317) |
| `def _normalize_work_mode_value(work_mode: str) -> str` | 规范化 work_mode 参数,非法值兜底为 ``"work"``。 | [L325](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L325) |
| `def _wm(raw: dict[str, Any]) -> str` | 归一化 raw dict 中的 work_mode，旧数据缺失时兜底为 ``"work"``。 | [L334](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L334) |
| `def get_project_by_dir_and_mode(project_dir: str, work_mode: str, *, cache_bust: bool = False) -> Project \| None` | 按 ``(work_mode, normalized project_dir)`` 查找项目(不限 hidden 状态)。 | [L343](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L343) |
| `def get_project_by_name_and_mode(name: str, work_mode: str, *, cache_bust: bool = False) -> Project \| None` | 按 ``(work_mode, name)`` 查找项目(不限 hidden 状态)。 | [L378](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L378) |
| `def resolve_session_project_binding(project_id: str, project_dir: str) -> tuple[str, str, str \| None, str \| None]` | 校验并解析 session.create 的 project_id / project_dir 绑定关系。 | [L404](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L404) |
| `def list_projects(*, include_hidden: bool = False, cache_bust: bool = False) -> list[Project]` | 列出项目。``include_hidden=False``(默认)时排除已软删除项目。 | [L465](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L465) |
| `def resolve_cron_project_id(project_dir: str, work_mode: str = DEFAULT_WEB_WORK_MODE) -> str` | cron 侧独立实现的 ``(work_mode, project_dir) → project_id`` 解析。 | [L477](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L477) |
| `def resolve_cron_project_binding(project_id: Any, project_dir: Any, work_mode: str = DEFAULT_WEB_WORK_MODE) -> CronProjectBinding` | Resolve cron job project ownership from project_id/project_dir/work_mode. | [L518](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L518) |
| `def resolve_cron_project_work_mode(project_id: Any, work_mode: str = DEFAULT_WEB_WORK_MODE) -> CronProjectBinding` | Resolve work_mode from a cron job project_id only. | [L588](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L588) |
| `def resolve_cron_job_patch(patch: dict[str, Any], existing_work_mode: str, *, resolve_work_mode_fn: Any \| None = None, channel_id: str \| None = None) -> dict[str, Any]` | 重解析 cron job patch 中的 work_mode / project_id / project_dir(共享 helper)。 | [L596](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L596) |
| `def get_project_dir_by_id(project_id: str) -> str` | 根据 project_id 反查 project_dir(调度器构造执行请求时用)。 | [L684](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L684) |
| `def _gen_unique_project_id(existing_projects: list[dict[str, Any]]) -> str` | 生成不与现有 ``project_id`` 冲突的 ID(须在文件锁内调用)。 | [L709](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L709) |
| `def validate_project_dir_name(name: str) -> str` | 校验项目名能否作为目录名;含非法字符或为保留名时抛 ``ValueError``。 | [L731](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L731) |
| `def resolve_default_project_dir(name: str, work_mode: str = DEFAULT_WEB_WORK_MODE) -> str` | 根据项目名 + work_mode 在默认工作区下生成工作目录绝对路径。 | [L759](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L759) |
| `def create_project(name: str, project_dir: str, work_mode: str = DEFAULT_WEB_WORK_MODE) -> Project` | 新建项目并持久化(不做 ``project_dir`` 去重,供内部/测试使用)。 | [L787](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L787) |
| `def create_or_restore_project(name: str, project_dir: str, work_mode: str = DEFAULT_WEB_WORK_MODE) -> tuple[Project, bool]` | 原子地新建或恢复项目(在文件锁内完成查重/恢复/新建,关闭 TOCTOU 窗口)。 | [L822](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L822) |
| `def save_project(project: Project) -> Project` | 更新已有项目(upsert: 按 project_id 匹配,命中则替换,未命中则追加)。 | [L908](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L908) |
| `def rename_project(project_id: str, name: str) -> Project \| None` | 原子地重命名项目(锁内完成名称冲突检测与写入,关闭 TOCTOU 窗口)。 | [L926](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L926) |
| `def restore_project(project_id: str) -> Project \| None` | 原子地恢复已软删除项目(锁内完成名称冲突检测与恢复,关闭 TOCTOU 窗口)。 | [L963](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L963) |
| `def hide_project(project_id: str) -> Project \| None` | 原子地隐藏(软删除)项目(锁内完成 hidden 翻转与置顶取消,关闭 TOCTOU 窗口)。 | [L999](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L999) |
| `def reindex_project_pin_orders() -> None` | 对所有置顶(pinned=True)项目紧凑重编号为 1..N,消除间隙。 | [L1026](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L1026) |
| `def invalidate_cache() -> None` | 清空进程内缓存(测试/特殊场景使用;正常流程下写操作会自动刷新缓存)。 | [L1045](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L1045) |
| `def find_or_create_code_project_for_dir(project_dir: str) -> Project \| None` | TUI 前置归属解析:按 ``work_mode="code"`` 查找/创建目录对应的 code 项目。 | [L1052](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L1052) |
| `def find_or_create_code_project_for_tui_params(params: dict[str, Any]) -> Project \| None` | Find or create the code project for TUI session params. | [L1114](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L1114) |

## `jiuwenswarm/server/runtime/session/session_history.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1)

**模块职责：** 定义 _PendingState、_strip_tool_arguments_env、_strip_skill_env_from_history_item、collapse_file_content_blocks、is_valid_session_id、_is_ephemeral_heartbeat_session 等符号。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L20) |
| `_FILE_LOCK` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L21) |
| `_WRITE_QUEUE` | `queue.Queue[tuple[str, dict[str, Any], str \| None]]` | [L22](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L22) |
| `_WORKER_STARTED` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L23) |
| `_WORKER_LOCK` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L24) |
| `_LEGACY_HISTORY_FILENAME` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L25) |
| `_JSONL_HISTORY_FILENAME` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L26) |
| `_LEGACY_HISTORY_ENV` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L27) |
| `_HEARTBEAT_OK` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L28) |
| `_VALID_SESSION_ID` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L29) |
| `_FILE_CONTENT_BLOCK_RE` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L35) |
| `BUFFERABLE_EVENT_TYPES` | `未显式标注` | [L105](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L105) |
| `NORMAL_BUFFER_EVENT_TYPES` | `未显式标注` | [L111](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L111) |
| `PENDING_EVENT_TYPE` | `未显式标注` | [L112](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L112) |
| `BUFFER_FLUSH_INTERVAL` | `未显式标注` | [L113](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L113) |
| `BUFFER_MAX_SIZE` | `未显式标注` | [L114](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L114) |
| `PENDING_MAX_SECONDS` | `未显式标注` | [L115](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L115) |
| `_buffer_lock` | `未显式标注` | [L117](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L117) |
| `_session_buffer` | `dict[str, dict[str, Any]]` | [L118](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L118) |
| `_session_buffer_type` | `dict[str, str]` | [L119](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L119) |
| `_session_buffer_request_id` | `dict[str, str]` | [L120](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L120) |
| `_session_buffer_root` | `dict[str, str \| None]` | [L121](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L121) |
| `_session_tool_update_buffer` | `dict[str, 'OrderedDict[str, dict[str, Any]]']` | [L122](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L122) |
| `_session_tool_update_root` | `dict[str, str \| None]` | [L123](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L123) |
| `_session_pending` | `dict[str, '_PendingState']` | [L124](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L124) |
| `_pending_raw_counter` | `int` | [L125](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L125) |
| `_FLUSH_THREAD_STARTED` | `未显式标注` | [L126](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L126) |
| `_FLUSH_THREAD_LOCK` | `未显式标注` | [L127](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L127) |
| `_flush_stop_event` | `未显式标注` | [L128](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L128) |
| `_FLUSH_THREAD` | `threading.Thread \| None` | [L129](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L129) |
| `_SHUTDOWN_DONE` | `bool` | [L130](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L130) |
| `_MERGE` | `未显式标注` | [L336](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L336) |
| `_TEAM_RELEVANT_EVENT_TYPES` | `未显式标注` | [L608](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L608) |

### [`class _PendingState`](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L225)

tool_calls.delta 暂留状态。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `item` | `dict[str, Any]` | `—` | [L231](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L231) |
| `request_id` | `str` | `—` | [L232](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L232) |
| `pending_queue` | `'OrderedDict[Tuple[str, str], dict[str, Any]]'` | `field(default_factory=OrderedDict)` | [L233](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L233) |
| `start_time` | `float` | `0.0` | [L234](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L234) |
| `sessions_root` | `str \| None` | `None` | [L235](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L235) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _strip_tool_arguments_env(arguments: Any) -> Any` | Drop ``env`` from tool-call arguments so skill_envs never hit history disk. | [L41](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L41) |
| `def _strip_skill_env_from_history_item(item: dict[str, Any]) -> None` | Remove tool_args.env from a history record before persist. | [L62](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L62) |
| `def collapse_file_content_blocks(content: str) -> str` | Replace inlined ``<file-content>`` bodies with ``@path`` references. | [L78](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L78) |
| `def is_valid_session_id(session_id: str) -> bool` | Return whether a session id is safe to use as one path component. | [L98](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L98) |
| `def _is_ephemeral_heartbeat_session(session_id: str) -> bool` | Heartbeat sessions are one-shot and should not pollute history.json(l). | [L133](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L133) |
| `def _has_persistable_assistant_payload(*, content_text: str, event_type: str \| None, extra: dict[str, Any] \| None) -> bool` | Return False for blank assistant shells that would show as empty history rows. | [L138](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L138) |
| `def _serialize_value_with_flag(obj: Any) -> tuple[Any, bool]` | 将对象转换为 JSON 可序列化的格式，并返回是否发生降级处理. | [L186](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L186) |
| `def _serialize_value(obj: Any) -> Any` | 源码未提供函数级文档字符串。 | [L220](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L220) |
| `def _is_empty_value(v: Any) -> bool` | None / 空串 / 空集合为"空"。注意数值 0、False 不算空（避免误判）。 | [L238](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L238) |
| `def _merge_delta_events(existing: dict, new: dict) -> dict` | 源码未提供函数级文档字符串。 | [L249](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L249) |
| `def _merge_reasoning_events(existing: dict, new: dict) -> dict` | 源码未提供函数级文档字符串。 | [L260](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L260) |
| `def _merge_tool_update_events(existing: dict, new: dict) -> dict` | 源码未提供函数级文档字符串。 | [L269](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L269) |
| `def _get_tool_call_key(call: dict) -> tuple[str, int]` | 源码未提供函数级文档字符串。 | [L284](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L284) |
| `def _merge_tool_call(existing: dict, new: dict) -> dict` | 源码未提供函数级文档字符串。 | [L290](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L290) |
| `def _merge_tool_calls_delta_events(existing: dict, new: dict) -> dict` | 源码未提供函数级文档字符串。 | [L301](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L301) |
| `def _session_dir(session_id: str, *, create: bool = True, sessions_root: str \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L343](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L343) |
| `def resolve_session_dir(session_id: str, *, create: bool = False, sessions_root: Path \| None = None) -> tuple[Path \| None, str \| None]` | 安全解析 session 目录路径（防路径遍历）。 | [L357](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L357) |
| `def _history_file(session_id: str, *, create: bool = True, sessions_root: str \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L392](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L392) |
| `def _history_jsonl_file(session_id: str, *, create: bool = True, sessions_root: str \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L398](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L398) |
| `def use_legacy_history_json() -> bool` | Prefer ``history.json`` with JSONL content, matching OfficeClaw / test. | [L404](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L404) |
| `def get_write_history_path(session_id: str, sessions_root: str \| None = None) -> Path` | Return the preferred durable history write target for a session. | [L415](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L415) |
| `def get_read_history_path(session_id: str, sessions_root: str \| None = None) -> Path` | Return the preferred history source, falling back to legacy json. | [L422](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L422) |
| `def history_exists(session_id: str) -> bool` | 源码未提供函数级文档字符串。 | [L442](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L442) |
| `def get_history_mtime(session_id: str) -> float \| None` | 源码未提供函数级文档字符串。 | [L446](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L446) |
| `def _peek_first_non_ws_char(path: Path) -> str \| None` | Return the first non-whitespace character, or None if empty/unreadable. | [L456](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L456) |
| `def _history_file_is_json_array(path: Path) -> bool` | True when the file is a legacy pretty/minified JSON array (not JSONL). | [L471](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L471) |
| `def _read_history(path: Path) -> list[dict[str, Any]]` | Read history.json / history.jsonl. Accepts JSONL or a legacy JSON array. | [L476](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L476) |
| `def _read_history_jsonl(path: Path) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L492](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L492) |
| `def load_history_records(session_id: str) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L534](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L534) |
| `def _write_records_to_path(path: Path, records: list[dict[str, Any]]) -> None` | Rewrite history as JSONL (one object per line), including ``history.json``. | [L538](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L538) |
| `def _append_record_jsonl(path: Path, record: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L548](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L548) |
| `def _rewrite_json_array_as_jsonl(path: Path) -> None` | Convert a legacy JSON-array history.json to JSONL before appending. | [L555](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L555) |
| `def _ensure_jsonl_bootstrap(session_id: str, sessions_root: str \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L563](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L563) |
| `def _ensure_legacy_json_bootstrap(session_id: str, sessions_root: str \| None = None) -> Path` | 源码未提供函数级文档字符串。 | [L577](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L577) |
| `def write_history_records(session_id: str, records: list[dict[str, Any]], *, preserve_existing_format: bool = True) -> Path` | Rewrite a session's history in JSONL, defaulting new sessions to history.json. | [L591](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L591) |
| `def _is_team_relevant(item: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L618](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L618) |
| `def read_team_history_records(session_id: str) -> list[dict[str, Any]]` | 读取指定会话的历史记录，仅返回 team 模式相关的记录。 | [L639](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L639) |
| `def _read_history_by_path(path: Path) -> list[dict[str, Any]]` | Read a history file; content (JSONL or JSON array) decides the parser. | [L661](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L661) |
| `def _is_member_relevant(item: dict[str, Any], member_name: str) -> bool` | 判断一条 team 历史记录是否与指定 member 相关（用于飞书 /join 历史推送）。 | [L666](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L666) |
| `def read_member_history_records(session_id: str, member_name: str) -> list[dict[str, Any]]` | 读取 team 历史记录，仅返回与指定 member 相关的记录。 | [L706](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L706) |
| `def read_session_history_records(session_id: str) -> list[dict[str, Any]]` | 读取指定会话的历史记录，返回所有记录。 | [L724](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L724) |
| `def _batch_write_items(session_id: str, items: list[dict], sessions_root: str \| None) -> None` | 批量写入 history.json（一次 open 写多行）。_FILE_LOCK 串行化磁盘写。 | [L749](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L749) |
| `def _write_item(session_id: str, item: dict[str, Any], sessions_root: str \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L765](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L765) |
| `def _ensure_worker_started() -> None` | 源码未提供函数级文档字符串。 | [L769](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L769) |
| `def _flush_buffer_unlocked(session_id: str) -> tuple[list[dict], str \| None]` | 落盘并清空普通缓冲层（调用方已持锁）。返回 (items, recorded_root)， 调用方锁外执行 IO。tool_update 走 per-call 缓冲可返回多条。 | [L792](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L792) |
| `def _flush_buffer(session_id: str, sessions_root: str \| None) -> None` | 落盘并清空普通缓冲层。sessions_root 为 None 时回退到缓冲时记录的 root。 | [L811](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L811) |
| `def _flush_on_request_switch(session_id: str, request_id: str, sessions_root: str \| None) -> None` | 新 request_id 到达：落盘旧请求的缓冲 + 激活的暂留层（按条件 B）。 | [L820](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L820) |
| `def _flush_on_type_switch_unlocked(session_id: str, event_type: str, request_id: str) -> tuple[list[dict], str \| None]` | 类型/请求切换判定（调用方已持锁）。返回 (待落盘 items, recorded_root)。 | [L841](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L841) |
| `def _extract_tool_call_id(item: dict) -> str` | 从 chat.tool_call 提取 id（嵌套 item["tool_call"]["tool_call_id"]）。 | [L852](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L852) |
| `def _extract_pending_call_ids(pending_item: dict) -> set[str]` | 源码未提供函数级文档字符串。 | [L861](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L861) |
| `def _get_buffer_key(item: dict) -> Tuple[str, str]` | 源码未提供函数级文档字符串。 | [L872](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L872) |
| `def _buffer_into(pending_queue: 'OrderedDict[Tuple[str, str], dict]', item: dict, event_type: str) -> None` | 合并事件进 pending_queue（同类型合并，非缓冲事件按到达顺序原样进）。 | [L876](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L876) |
| `def _route_event(sid: str, item: dict, event_type: str, sessions_root_s: str \| None) -> None` | 事件分发：暂留层 / 普通缓冲层 / 非缓冲事件。 | [L887](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L887) |
| `def _ensure_flush_thread_started() -> None` | 启动定时刷新线程：每 BUFFER_FLUSH_INTERVAL 秒刷新普通缓冲层 + 检查暂留超时。 | [L973](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L973) |
| `def _force_flush_all_pending() -> None` | 强制落盘所有剩余暂留（忽略超时），shutdown 收尾兜底。 | [L995](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L995) |
| `def shutdown() -> None` | 进程退出前收尾：停定时线程 + flush 落盘（含强制落剩余暂留）+ 排空异步写队列。 | [L1007](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1007) |
| `def _periodic_flush() -> None` | 定时刷新：普通缓冲层落盘 + 暂留层超时检查（条件 B）。 | [L1044](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1044) |
| `def flush_session_history(session_id: str, sessions_root: str \| None = None) -> None` | Flush in-memory merge buffers for one session, then drain the async writer. | [L1065](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1065) |
| `def enrich_history_messages_session_id(messages: Iterable[dict[str, Any]], resolved_session_id: str) -> list[dict[str, Any]]` | 为缺少 session_id 的历史记录做浅拷贝补全（兼容旧数据）。 | [L1077](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1077) |
| `def append_history_record(*, session_id: str, request_id: str, channel_id: str, role: str, content: Any, timestamp: float, event_type: str \| None = None, extra: dict[str, Any] \| None = None, channel_metadata: dict[str, Any] \| None = None, mode: str \| None = None, sessions_root: str \| Path \| None = None, task_id: str \| None = None) -> None` | 向指定 session 的 history.json 追加一条 JSONL 记录（可合并事件先缓冲）。 | [L1092](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1092) |
| `def append_compact_history_records(*, session_id: str, request_id: str, channel_id: str, summary: str \| None, timestamp: float, trigger: str = 'auto', stats: dict[str, Any] \| None = None, mode: str \| None = None) -> None` | Persist a compact boundary and optional transcript-only summary. | [L1204](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1204) |
| `def truncate_history_records(*, session_id: str, cut_index: int) -> dict[str, Any]` | 截断会话历史到指定位置（线程安全）。 | [L1256](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1256) |

## `jiuwenswarm/server/runtime/session/session_manager.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L1)

**模块职责：** Session Manager - 管理 session 任务队列和并发控制.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L20) |

### [`class SessionManager`](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L23)

Session 任务管理器.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, telemetry: SessionTelemetry \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L29](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L29) |
| `@staticmethod def get_session_id(session_id: str \| None) -> str` | 获取 session_id，默认为 'default'. | [L42](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L42) |
| `def get_session_tasks(self) -> dict[str, asyncio.Task]` | 返回 session_id -> asyncio.Task 映射（用于工作状态判定）. | [L46](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L46) |
| `def get_session_queues(self) -> dict[str, asyncio.PriorityQueue]` | 返回 session_id -> asyncio.PriorityQueue 映射（用于工作状态判定）. | [L50](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L50) |
| `@staticmethod def _is_oneshot_session(session_id: str) -> bool` | 判断是否为一次性 session（心跳/定时任务），其 session_id 永不复用. | [L55](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L55) |
| `async def cancel_session_task(self, session_id: str, log_msg_prefix: str = '', wait_timeout: float \| None = None) -> None` | 取消指定 session 的非流式任务. | [L64](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L64) |
| `async def cancel_all_session_tasks(self, log_msg_prefix: str = '') -> None` | 取消所有 session 的非流式任务. | [L112](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L112) |
| `@staticmethod def _cancel_result_future(result_future: asyncio.Future[Any] \| None) -> None` | 源码未提供方法级文档字符串。 | [L118](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L118) |
| `def _finish_closing_task(self, session_id: str, task: asyncio.Task) -> None` | 源码未提供方法级文档字符串。 | [L122](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L122) |
| `def _track_closing_task(self, session_id: str, task: asyncio.Task) -> None` | 源码未提供方法级文档字符串。 | [L139](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L139) |
| `async def close_session(self, session_id: str, wait_timeout: float \| None = 5.0) -> bool` | 停止并释放指定 session 当前这一代任务处理器. | [L150](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L150) |
| `async def close_all_sessions(self) -> None` | 停止并释放全部 session 任务处理器. | [L220](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L220) |
| `async def ensure_session_processor(self, session_id: str) -> None` | 确保 session 的任务处理器在运行. | [L233](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L233) |
| `async def submit_task(self, session_id: str, task_func: Callable[[], Awaitable[Any]]) -> None` | 提交任务到 session 队列. | [L337](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L337) |
| `async def submit_and_wait(self, session_id: str, task_func: Callable[[], Awaitable[Any]]) -> Any` | 提交任务到 session 队列并等待结果. | [L356](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L356) |
| `def get_current_task(self, session_id: str) -> asyncio.Task \| None` | 获取当前 session 正在执行的任务. | [L407](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L407) |
| `def has_active_processor(self, session_id: str) -> bool` | 检查 session 是否有活跃的处理器. | [L411](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L411) |
| `def has_session_runtime(self, session_id: str \| None = None) -> bool` | Return whether session-owned queue or task state is retained. | [L418](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L418) |
| `def has_active_tasks(self) -> bool` | 是否有活跃的 session 任务（供 dreaming busy_checker 使用）。 | [L437](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L437) |
| `def _session_created(self, session_id: str) -> None` | 源码未提供方法级文档字符串。 | [L441](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L441) |
| `def observe_external_task(self, session_id: str, task: asyncio.Task) -> None` | Observe a scheduler-owned task without changing task ownership. | [L450](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L450) |
| `def _task_started(self, session_id: str, task: asyncio.Task) -> int \| None` | 源码未提供方法级文档字符串。 | [L469](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L469) |
| `def _task_finished(self, session_id: str, task: asyncio.Task, generation: int, state: str) -> None` | 源码未提供方法级文档字符串。 | [L476](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L476) |
| `def _mark_task_cancelled(self, session_id: str, task: asyncio.Task, *, reason: str) -> None` | 源码未提供方法级文档字符串。 | [L488](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L488) |

## `jiuwenswarm/server/runtime/session/session_metadata.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1)

**模块职责：** 会话元数据管理模块

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L24) |
| `_TEAM_TEMPLATE_SNAPSHOT_FILE` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L26) |
| `_METADATA_QUEUE` | `queue.Queue[tuple[str, dict[str, Any], str \| None, bool]]` | [L30](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L30) |
| `_WORKER_STARTED` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L33) |
| `_WORKER_LOCK` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L34) |
| `_FILE_LOCK` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L35) |
| `_METADATA_CACHE` | `dict[str, dict[str, Any]]` | [L38](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L38) |
| `_CACHE_LOCK` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L39) |
| `_TITLE_MAX_LEN` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L42) |
| `_HEARTBEAT_SESSION_PREFIX` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L44) |
| `_DELIVERY_KIND_SERVER_PUSH` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L45) |
| `_INJECTED_TAG_RE` | `未显式标注` | [L55](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L55) |
| `_INJECTED_TAG_START_RE` | `未显式标注` | [L59](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L59) |
| `_SESSION_PIN_LOCK` | `未显式标注` | [L1248](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1248) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def resolve_session_runtime_team_name(metadata: dict[str, Any] \| None) -> str` | Return the Agent Teams runtime identity, with legacy metadata fallback. | [L48](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L48) |
| `def _has_valid_work_mode(value: Any) -> bool` | 源码未提供函数级文档字符串。 | [L64](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L64) |
| `def _build_project_lookup() -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]` | 构建 project 查找映射供 work_mode / project_id 推断使用。 | [L77](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L77) |
| `def _apply_metadata_defaults_with_inference(session_id: str, metadata: dict[str, Any], session_dir: Path \| None = None, *, dir_to_projects: dict[str, list[tuple[str, str]]] \| None = None, id_to_work_mode: dict[str, str] \| None = None, enable_writeback: bool = True, sessions_root: str \| Path \| None = None) -> dict[str, Any]` | 统一兜底 + 推断缺失字段,并在确定性推断时异步写盘。 | [L111](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L111) |
| `def _normalize_path_for_match_safe(path: str) -> str` | 规范化路径用于跨平台匹配(容忍尾部分隔符/大小写差异)。 | [L249](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L249) |
| `def _sanitize_title(title: str) -> str` | 清理标题中的系统注入 XML 标签。 | [L264](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L264) |
| `def _current_timestamp() -> float` | 返回显式使用 UTC 时区的当前时间戳 | [L282](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L282) |
| `def _is_relative_to(path: Path, base: Path) -> bool` | 源码未提供函数级文档字符串。 | [L287](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L287) |
| `def _safe_session_subdir(session_id: str, sessions_root: str \| Path \| None = None) -> Path \| None` | 解析 sessions 根下安全子目录；非法 session_id 返回 None（不 mkdir）。 | [L295](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L295) |
| `def resolve_session_subdir(session_id: str, *, sessions_root: str \| Path \| None = None) -> Path \| None` | Resolve a session directory without escaping the selected sessions root. | [L325](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L325) |
| `def get_session_team_template_snapshot(session_id: str, *, sessions_root: str \| Path \| None = None) -> dict[str, Any] \| None` | Read the private Team template snapshot pinned to one session. | [L334](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L334) |
| `def _write_session_team_template_snapshot(session_id: str, snapshot: dict[str, Any], *, sessions_root: str \| Path \| None = None) -> None` | 源码未提供函数级文档字符串。 | [L354](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L354) |
| `def write_session_team_template_snapshot(session_id: str, snapshot: dict[str, Any], *, sessions_root: str \| Path \| None = None) -> None` | Persist a session's team template snapshot atomically (public wrapper). | [L380](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L380) |
| `def capture_session_team_binding_artifacts(session_id: str, *, sessions_root: str \| Path \| None = None) -> tuple[bytes \| None, bytes \| None]` | Capture metadata and Team snapshot bytes for bind rollback. | [L398](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L398) |
| `def restore_session_team_binding_artifacts(session_id: str, artifacts: tuple[bytes \| None, bytes \| None], *, sessions_root: str \| Path \| None = None) -> None` | Restore metadata and Team snapshot after a failed bind transaction. | [L415](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L415) |
| `def validate_project_dir(path: str, *, default: Path \| None = None) -> Path` | Normalize ``project_dir``; create missing dirs; fall back on failure. | [L448](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L448) |
| `def _normalize_sessions_root_s(sessions_root: str \| Path \| None) -> str \| None` | 源码未提供函数级文档字符串。 | [L472](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L472) |
| `def _metadata_cache_key(session_id: str, sessions_root: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L478](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L478) |
| `def _peek_project_dir_from_root(session_id: str, sessions_root: Path) -> str \| None` | Read ``project_dir`` from ``{sessions_root}/{session_id}/metadata.json`` (no write). | [L483](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L483) |
| `def get_resolved_project_dir(session_id: str, sessions_root: str \| Path \| None = None, *, default: str \| Path \| None = None) -> str` | Resolve per-session ``project_dir`` for IM detect / tools. | [L513](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L513) |
| `def _metadata_file(session_id: str, sessions_root: str \| None = None) -> Path` | 获取会话元数据文件路径 | [L563](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L563) |
| `def _read_metadata(session_id: str, cache_bust: bool = False, *, sessions_root: str \| Path \| None = None) -> dict[str, Any]` | 读取会话元数据(优先从内存缓存读取,避免异步写入未落盘时读到陈旧数据) | [L572](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L572) |
| `def _read_metadata_prefer_root(session_id: str, cache_bust: bool = False, *, sessions_root: str \| Path \| None = None) -> dict[str, Any]` | Read an explicit tenant root, or the legacy global root when omitted. | [L606](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L606) |
| `def _write_metadata_sync(session_id: str, metadata: dict[str, Any], preserve_pin_fields: bool = False, *, sessions_root: str \| None = None) -> dict[str, Any]` | 同步写入会话元数据(由后台 worker 或 fallback 调用) | [L620](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L620) |
| `def _merge_pin_fields(current: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L658](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L658) |
| `def _merge_pin_fields_from_disk(session_id: str, metadata: dict[str, Any], *, sessions_root: str \| None = None) -> dict[str, Any]` | Preserve latest disk pin state for async writes that do not own pin fields. | [L667](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L667) |
| `def _ensure_worker_started() -> None` | 源码未提供函数级文档字符串。 | [L692](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L692) |
| `def _enqueue_write(session_id: str, metadata: dict[str, Any], sync_write: bool = False, preserve_pin_fields: bool = False, *, sessions_root: str \| Path \| None = None) -> None` | 将写入操作放入异步队列,队列满时退化为同步写。 | [L730](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L730) |
| `def _auto_title(content: str) -> str` | 从首条用户消息自动生成会话标题 | [L790](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L790) |
| `def init_session_metadata(*, session_id: str, channel_id: str = '', user_id: str = '', title: str = '', mode: str = 'unknown', team_name: str = '', team_template_id: str = '', project_dir: str = '', project_id: str = '', model: str = '', cron_id: str = '', work_mode: str = '', channel_metadata: dict[str, Any] \| None = None, sessions_root: str \| Path \| None = None) -> None` | 初始化会话元数据(同步写,确保创建后立即可读) | [L803](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L803) |
| `def update_session_metadata(*, session_id: str, channel_id: str \| None = None, user_id: str \| None = None, title: str \| None = None, clear_title: bool = False, increment_message_count: bool = False, set_message_count: int \| None = None, user_content: str \| None = None, channel_metadata: dict[str, Any] \| None = None, mode: str \| None = None, team_name: str \| None = None, runtime_team_name: str \| None = None, team_template_id: str \| None = None, team_template_snapshot: dict[str, Any] \| None = Non…` | 更新会话元数据(异步写入,不阻塞调用方) | [L859](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L859) |
| `def sync_session_request_metadata(*, session_id: str, channel_id: str \| None = None, mode: str \| None = None, model: str \| None = None, project_dir: str \| None = None, project_id: str \| None = None, cron_id: str \| None = None, last_user_message_at: float \| None = None, is_chat_turn: bool = True, explicit_mode_provided: bool = False, explicit_model_provided: bool = False, work_mode: str \| None = None, sessions_root: str \| Path \| None = None) -> str \| None` | 校验请求带来的参数与磁盘 metadata.json 是否需要更新，并按字段语义写入。 | [L1045](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1045) |
| `def get_session_metadata(session_id: str, cache_bust: bool = False, *, enable_writeback: bool = True, sessions_root: str \| Path \| None = None) -> dict[str, Any]` | 获取会话元数据 | [L1206](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1206) |
| `def set_session_pinned(session_id: str, pinned: bool) -> tuple[bool, int] \| None` | 置顶/取消置顶会话,并对所有置顶会话紧凑重编号为 1..N。幂等。 | [L1251](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1251) |
| `def increment_session_round_count(session_id: str) -> int` | 递增并持久化 session 的 round_id，返回递增后的值。 | [L1325](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1325) |
| `def remove_session_metadata_cache(session_id: str, *, sessions_root: str \| Path \| None = None) -> None` | Remove cached session metadata after the session directory is deleted. | [L1340](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1340) |
| `def set_session_delivery_context(*, session_id: str, channel_id: str \| None, source_request_id: str \| None, route_metadata: dict[str, Any] \| None, delivery_kind: str = _DELIVERY_KIND_SERVER_PUSH) -> dict[str, Any]` | 刷新 session 级 delivery context，供异步 server_push 恢复路由上下文。 | [L1353](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1353) |
| `def get_session_delivery_context(session_id: str) -> dict[str, Any] \| None` | 读取 session 级 delivery context。 | [L1430](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1430) |
| `def build_server_push_message(*, session_id: str, request_id: str, payload: dict[str, Any], fallback_channel_id: str \| None = None) -> dict[str, Any]` | 基于 session delivery context 构造 evolution watcher 的 server_push 消息。 | [L1439](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1439) |
| `def remove_team_mode_session_dirs_at_startup() -> None` | Remove only explicitly temporary team sessions at startup. | [L1464](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1464) |
| `def _resolve_legacy_work_mode(raw: dict[str, Any], dir_to_projects: dict[str, list[tuple[str, str]]], id_to_work_mode: dict[str, str]) -> str \| None` | 启动迁移时为老会话推断 work_mode（§5.3.4.1 同路径双模式消歧）。 | [L1508](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1508) |
| `def get_all_sessions_metadata(limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]` | 获取所有会话的元数据。 | [L1572](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1572) |
| `def collect_all_sessions_metadata() -> list[dict[str, Any]]` | 收集全部会话元数据(不分页、不排序),供项目统计与置顶会话聚合使用。 | [L1646](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L1646) |

## `jiuwenswarm/server/runtime/session/session_rename.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/session_rename.py#L1)

**模块职责：** session.rename 共享实现：AgentWebSocketServer 与 cli_channel 本地回退共用。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_RENAME_TITLE_MAX_LEN` | `未显式标注` | [L11](../../../../../jiuwenswarm/server/runtime/session/session_rename.py#L11) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def apply_session_rename(params: Any, connection_session_id: str, *, init_channel_id: str = 'tui') -> tuple[bool, dict[str, Any] \| None, str \| None, str \| None]` | 实现 session.rename 三种语义：查询(None) / 清除(空串 strip 后) / 设置。 | [L14](../../../../../jiuwenswarm/server/runtime/session/session_rename.py#L14) |

## `jiuwenswarm/server/runtime/session/work_mode.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L1)

**模块职责：** 工作模式（work_mode）高层 helper。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L28) |

### [`class SessionWorkModeParams`](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L86)

``session.create`` 参数归一化结果(纯归一化,非最终归属绑定)。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `project_id` | `str` | `—` | [L97](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L97) |
| `project_dir` | `str` | `—` | [L98](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L98) |
| `work_mode` | `str` | `—` | [L99](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L99) |
| `error` | `str \| None` | `None` | [L100](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L100) |
| `code` | `str \| None` | `None` | [L101](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L101) |
| `has_explicit_work_mode` | `bool` | `False` | [L102](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L102) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def default_work_mode_for_channel(channel_id: str \| None) -> str` | 按通道推断默认 ``work_mode``:``tui``→``code``,其他→``work``。 | [L46](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L46) |
| `def infer_legacy_project_work_mode(raw_project: dict[str, Any]) -> str` | 旧项目记录推断 ``work_mode``:有合法字段用之,否则回退 ``"work"``。 | [L53](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L53) |
| `def resolve_request_work_mode(params: dict[str, Any], channel_id: str \| None) -> tuple[str \| None, str \| None]` | 从请求参数解析 ``work_mode``,严格校验。 | [L60](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L60) |
| `def resolve_session_work_mode_params(params: dict[str, Any], *, channel_id: str \| None) -> SessionWorkModeParams` | 为 ``session.create`` 归一化 ``project_id`` / ``project_dir`` / ``work_mode``。 | [L105](../../../../../jiuwenswarm/server/runtime/session/work_mode.py#L105) |

## `jiuwenswarm/server/runtime/sync_agents_configs.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L1)

**模块职责：** Helpers for sync_agents_configs protocol validation and materialization.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L20) |
| `SYNC_ENV_SCHEMA` | `frozenset[str]` | [L22](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L22) |

### [`class AgentSyncResultItem`](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L400)

Per-agent sync_agents_configs result fields (G.FNM.03).

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `agent_id` | `str` | `—` | [L403](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L403) |
| `action` | `str` | `—` | [L404](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L404) |
| `ok` | `bool` | `—` | [L405](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L405) |
| `error` | `str \| None` | `None` | [L406](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L406) |
| `warmup` | `dict[str, Any] \| None` | `None` | [L407](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L407) |
| `reload` | `dict[str, Any] \| None` | `None` | [L408](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L408) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def materialize_sync_env(env_dict: dict[str, Any]) -> dict[str, str]` | Build active-tip map: keep empty strings, omit nulls (deletes). | [L84](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L84) |
| `def _env_bool(value: Any) -> bool \| None` | 源码未提供函数级文档字符串。 | [L97](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L97) |
| `def _ensure_memory_dict(result: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L108](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L108) |
| `def _ensure_react_evolution_dict(result: dict[str, Any]) -> dict[str, Any]` | Prefer config.react.evolution; migrate bare config.evolution into react when needed. | [L116](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L116) |
| `def synthesize_config(config: Any, env: dict[str, Any] \| None = None) -> dict[str, Any]` | Materialize memory/evolution into config; protocol authority is env. | [L138](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L138) |
| `def compute_content_hash(*, config: dict[str, Any], env: dict[str, Any], runtime: dict[str, Any]) -> str` | Stable SHA-256 of config + env + runtime JSON. | [L213](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L213) |
| `def _validate_env_schema(env: Any, *, agent_id: str) -> None` | 源码未提供函数级文档字符串。 | [L233](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L233) |
| `def _validate_shared_env(shared_env: Any) -> None` | 源码未提供函数级文档字符串。 | [L263](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L263) |
| `def validate_sync_payload(params: Any) -> dict[str, Any]` | Validate sync_agents_configs params; raise ValueError on protocol errors. | [L292](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L292) |
| `def build_agent_spec(*, service_id: str, agent_id: str, config: dict[str, Any], env: dict[str, Any], runtime: dict[str, Any], revision: str) -> TenantAgentSpec` | 源码未提供函数级文档字符串。 | [L373](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L373) |
| `def build_agent_result(item: AgentSyncResultItem) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L411](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L411) |

## `jiuwenswarm/server/runtime/team_binding_store.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L1)

**模块职责：** Persistent team entity bindings.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `TEAM_NAME_RE` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L21) |
| `_DEFAULT_STORE` | `TeamBindingStore \| None` | [L296](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L296) |
| `_DEFAULT_STORE_LOCK` | `未显式标注` | [L297](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L297) |

### [`class TeamBindingStoreError(ValueError)`](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L24)

Validation or persistence error for team bindings.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, message: str, *, code: str = 'BAD_REQUEST') -> None` | 源码未提供方法级文档字符串。 | [L27](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L27) |

### [`class TeamBinding`](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L33)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `team_name` | `str` | `—` | [L34](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L34) |
| `template_id` | `str` | `—` | [L35](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L35) |
| `created_at` | `float` | `—` | [L36](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L36) |
| `updated_at` | `float` | `—` | [L37](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L37) |
| `session_ids` | `tuple[str, ...]` | `()` | [L38](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L38) |
| `last_session_id` | `str` | `''` | [L39](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L39) |
| `legacy` | `bool` | `False` | [L40](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L40) |
| `template_snapshot` | `dict[str, Any] \| None` | `None` | [L41](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L41) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def from_dict(cls, data: dict[str, Any]) -> 'TeamBinding'` | 源码未提供方法级文档字符串。 | [L44](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L44) |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L66](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L66) |

### [`class TeamBindingStore`](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L109)

File-backed team binding catalog.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, path: Path \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L112](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L112) |
| `@property def path(self) -> Path` | 源码未提供方法级文档字符串。 | [L117](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L117) |
| `def list(self) -> list[TeamBinding]` | 源码未提供方法级文档字符串。 | [L120](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L120) |
| `def get(self, team_name: str) -> TeamBinding \| None` | 源码未提供方法级文档字符串。 | [L125](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L125) |
| `def create(self, *, team_name: str, template_id: str, template_snapshot: dict[str, Any] \| None = None, legacy: bool = False) -> TeamBinding` | 源码未提供方法级文档字符串。 | [L132](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L132) |
| `def bind_session(self, *, team_name: str, session_id: str) -> TeamBinding` | 源码未提供方法级文档字符串。 | [L162](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L162) |
| `def unbind_session(self, *, session_id: str, team_name: str \| None = None) -> TeamBinding \| None` | 源码未提供方法级文档字符串。 | [L210](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L210) |
| `def delete(self, team_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L243](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L243) |
| `def _read_unlocked(self) -> dict[str, TeamBinding]` | 源码未提供方法级文档字符串。 | [L255](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L255) |
| `def _write_unlocked(self, data: dict[str, TeamBinding]) -> None` | 源码未提供方法级文档字符串。 | [L279](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L279) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _replace_bound_sessions(binding: TeamBinding, *, session_ids: tuple[str, ...], last_session_id: str, updated_at: float) -> TeamBinding` | 源码未提供函数级文档字符串。 | [L78](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L78) |
| `def validate_team_name(team_name: str) -> str` | 源码未提供函数级文档字符串。 | [L97](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L97) |
| `def get_team_binding_store() -> TeamBindingStore` | 源码未提供函数级文档字符串。 | [L300](../../../../../jiuwenswarm/server/runtime/team_binding_store.py#L300) |

## `jiuwenswarm/server/runtime/team_entity_store.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L1)

**模块职责：** Persistent per-team entity metadata stored in the team workspace.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `TEAM_ENTITY_META_DIR` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L29) |
| `TEAM_ENTITY_FILE` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L30) |
| `_SENSITIVE_SNAPSHOT_KEYS` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L32) |
| `_SENSITIVE_HEADER_CONTAINER_KEYS` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L33) |
| `_DEFAULT_STORE` | `TeamEntityStore \| None` | [L429](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L429) |
| `_DEFAULT_STORE_LOCK` | `未显式标注` | [L430](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L430) |

### [`class TeamEntityStoreError(ValueError)`](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L36)

Validation or persistence error for team entity metadata.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, message: str, *, code: str = 'BAD_REQUEST') -> None` | 源码未提供方法级文档字符串。 | [L39](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L39) |

### [`class TeamEntity`](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L45)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `team_name` | `str` | `—` | [L46](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L46) |
| `template_id` | `str` | `—` | [L47](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L47) |
| `created_at` | `float` | `—` | [L48](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L48) |
| `updated_at` | `float` | `—` | [L49](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L49) |
| `template_snapshot` | `dict[str, Any]` | `—` | [L50](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L50) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def from_dict(cls, data: dict[str, Any]) -> 'TeamEntity'` | 源码未提供方法级文档字符串。 | [L53](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L53) |
| `def to_dict(self) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L65](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L65) |

### [`class TeamEntityStore`](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L283)

File-backed team entity metadata catalog.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, teams_home: Path \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L286](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L286) |
| `def _team_path(self, team_name: str) -> Path` | 源码未提供方法级文档字符串。 | [L290](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L290) |
| `def entity_path(self, team_name: str) -> Path` | 源码未提供方法级文档字符串。 | [L294](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L294) |
| `def exists(self, team_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L297](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L297) |
| `def get(self, team_name: str) -> TeamEntity \| None` | 源码未提供方法级文档字符串。 | [L300](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L300) |
| `def write(self, *, team_name: str, template_id: str, template_snapshot: dict[str, Any], created_at: float \| None = None) -> TeamEntity` | 源码未提供方法级文档字符串。 | [L307](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L307) |
| `def ensure(self, *, team_name: str, template_id: str, template_snapshot: dict[str, Any], created_at: float \| None = None) -> TeamEntity` | 源码未提供方法级文档字符串。 | [L342](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L342) |
| `def delete(self, team_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L360](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L360) |
| `def delete_team_directory(self, team_name: str) -> bool` | 源码未提供方法级文档字符串。 | [L369](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L369) |
| `@staticmethod def default_teams_home() -> Path` | 源码未提供方法级文档字符串。 | [L389](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L389) |
| `@staticmethod def _read_unlocked(path: Path) -> TeamEntity \| None` | 源码未提供方法级文档字符串。 | [L393](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L393) |
| `@staticmethod def _write_unlocked(path: Path, entity: TeamEntity) -> None` | 源码未提供方法级文档字符串。 | [L415](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L415) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _yaml() -> YAML` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L76) |
| `def _find_sensitive_snapshot_path(value: Any, path: tuple[str, ...] = ()) -> tuple[str, ...] \| None` | 源码未提供函数级文档字符串。 | [L83](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L83) |
| `def _normalized_text(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L107](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L107) |
| `def _normalized_endpoint(value: Any) -> str` | 源码未提供函数级文档字符串。 | [L111](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L111) |
| `def _resolve_snapshot_model_ref(model: dict[str, Any], config_base: dict[str, Any]) -> str \| None` | 源码未提供函数级文档字符串。 | [L115](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L115) |
| `def _resolve_request_only_model_ref(model: dict[str, Any], config_base: dict[str, Any]) -> str \| None` | Bind a request-only model (model name only, no client config / ref) to a tenant owner. | [L185](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L185) |
| `def _build_snapshot_model_reference(model_ref: str, model: dict[str, Any]) -> dict[str, Any]` | Keep non-sensitive per-member request overrides alongside an owner reference. | [L221](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L221) |
| `def normalize_team_entity_snapshot(template_snapshot: dict[str, Any], config_base: dict[str, Any] \| None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L236](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L236) |
| `def get_team_entity_store() -> TeamEntityStore` | 源码未提供函数级文档字符串。 | [L433](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L433) |
| `def ensure_team_entity(*, team_name: str, template_id: str, template_snapshot: dict[str, Any] \| None = None, config_base: dict[str, Any] \| None = None, created_at: float \| None = None, store: TeamEntityStore \| None = None) -> TeamEntity \| None` | 源码未提供函数级文档字符串。 | [L442](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L442) |
| `def ensure_team_entity_for_binding(binding: Any, *, config_base: dict[str, Any] \| None = None, store: TeamEntityStore \| None = None) -> TeamEntity \| None` | 源码未提供函数级文档字符串。 | [L475](../../../../../jiuwenswarm/server/runtime/team_entity_store.py#L475) |

## `jiuwenswarm/server/runtime/team_snapshot_refresh.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/team_snapshot_refresh.py#L1)

**模块职责：** Reconcile frozen per-session/per-team template snapshots with the live template.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/runtime/team_snapshot_refresh.py#L25) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _canonical(snapshot: dict[str, Any]) -> str` | 源码未提供函数级文档字符串。 | [L28](../../../../../jiuwenswarm/server/runtime/team_snapshot_refresh.py#L28) |
| `def reconcile_session_team_snapshot(*, session_id: str, team_name: str, template_id: str, frozen_snapshot: dict[str, Any], config_base: dict[str, Any] \| None, sessions_root: str \| Path \| None = None) -> dict[str, Any]` | Return the snapshot this session's rebuild should use; refresh both frozen copies when the live template has drifted. | [L38](../../../../../jiuwenswarm/server/runtime/team_snapshot_refresh.py#L38) |
| `def resolve_dissolve_keep_members(*, session_id: str, team_name: str, template_id: str, config_base: dict[str, Any] \| None, sessions_root: str \| Path \| None = None, metadata: dict[str, Any] \| None = None) -> set[str] \| None` | Return the member-name set a dissolve reset should keep; ``None`` on any failure. | [L123](../../../../../jiuwenswarm/server/runtime/team_snapshot_refresh.py#L123) |

## `jiuwenswarm/server/runtime/tenant_agent_pool.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1)

**模块职责：** 定义 TenantAgentPool、filter_cached_agent_managers。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L43) |

### [`class TenantAgentPool`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L63)

多租户 AgentManager 管理器（单例）.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_instance` | `ClassVar[TenantAgentPool \| None]` | `None` | [L72](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L72) |
| `_LLM_CONTROL_EVOLUTION_METHODS` | `frozenset[str]` | `frozenset({'skills.evolution.rebuild', 'skills.evolution.status', 'skills.evolution.get', 'skills.e…` | [L888](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L888) |
| `_DISK_ONLY_EVOLUTION_METHODS` | `frozenset[str]` | `frozenset({'skills.evolution.archives', 'skills.evolution.rollback'})` | [L896](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L896) |
| `_PREFERRED_CONTROL_AGENT_IDS` | `tuple[str, ...]` | `('office', 'jiuwenclaw', 'assistant')` | [L902](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L902) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, cache_max_size: int \| None = None, cache_ttl: int \| None = None) -> None` | 源码未提供方法级文档字符串。 | [L74](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L74) |
| `@classmethod def get_instance(cls) -> 'TenantAgentPool'` | 获取单例实例. | [L94](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L94) |
| `@classmethod def peek_instance(cls) -> 'TenantAgentPool \| None'` | 返回已初始化的单例；若尚未创建则返回 None（不触发构造）。 | [L101](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L101) |
| `@classmethod def reset_instance(cls) -> None` | 重置单例（仅用于测试）. | [L107](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L107) |
| `def _get_lock(self, cache_key: Hashable) -> asyncio.Lock` | 源码未提供方法级文档字符串。 | [L128](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L128) |
| `@staticmethod def build_service_id(chat_id: str \| None, bot_app_id: str \| None) -> str` | 根据 chat_id 和 bot_app_id 构建 service_id. | [L150](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L150) |
| `async def initialize(self, channel_id: str = '', extra_config: dict[str, Any] \| None = None) -> dict[str, Any] \| None` | 初始化默认租户的 AgentManager（主要用于 ACP 通道）. | [L156](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L156) |
| `def get_client_capabilities(self, channel_id: str = '') -> dict[str, Any]` | 获取默认租户的客户端能力. | [L163](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L163) |
| `def get_agent_nowait(self) -> Any \| None` | 获取默认 Agent 实例（同步，不自动创建）. | [L170](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L170) |
| `async def create_session(self, channel_id: str = '', session_id: str \| None = None) -> str` | 创建会话. | [L177](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L177) |
| `async def cleanup(self) -> None` | 清理所有缓存的 AgentManager 实例（用于 shutdown 或重置）. | [L183](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L183) |
| `def is_working(self) -> bool` | 返回是否有任何租户 Agent 正在工作. | [L199](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L199) |
| `async def cancel_all_inflight_work(self, reason: str = '[gateway ws disconnect] ') -> None` | WebSocket 断开时：对每个已缓存 ``AgentManager`` 取消在途任务。 | [L213](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L213) |
| `@staticmethod def _reconcile_reload_env_for_tenant(*, service_id: str, agent_id: str, env: Any, reload_trace_id: str \| None = None) -> dict[str, None]` | Multimodal omission reconcile scoped to one ``(service_id, agent_id)`` bag. | [L228](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L228) |
| `@staticmethod def _upsert_reload_catalog(*, service_id: str, agent_id: str, config: Any) -> None` | Persist a tenant reload snapshot for later cold-start creation. | [L269](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L269) |
| `async def reload_agents_config(self, config: Any, env: Any, *, reload_trace_id: str \| None = None) -> ReloadAggregateResult` | Broadcast reload: reconcile + stage per cached Manager env ns, then configure each. | [L298](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L298) |
| `async def reload_tenant_config(self, agent_id: str, service_id: str, config: Any, env: Any, *, reload_trace_id: str \| None = None) -> ReloadAggregateResult` | 仅对指定租户 (agent_id, service_id) 热重载配置。 | [L382](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L382) |
| `async def _ensure_agent_manager(self, agent_id: str, service_id: str, workspace_key: str, *, config_base: Any = None, env_overrides: Any = None) -> Any` | 确保 agent_id + service_id + workspace_key 对应的 AgentManager 已创建. | [L446](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L446) |
| `def _get_agent_manager_nowait(self, agent_id: str, service_id: str, workspace_key: str) -> Any \| None` | 同步获取 AgentManager 实例（不自动创建）. | [L544](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L544) |
| `@staticmethod def _build_cache_key(agent_id: str, service_id: str \| None, workspace_key: str \| None = None) -> tuple[str, str \| None, str]` | Tenant pool key as a tuple to avoid delimiter collisions. | [L563](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L563) |
| `async def _evict_manager_cache(self, agent_id: str, service_id: str, workspace_key: str = 'default') -> None` | 源码未提供方法级文档字符串。 | [L572](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L572) |
| `async def warmup_tenant(self, agent_id: str, service_id: str, *, channel_id: str = 'officeclaw') -> dict[str, Any]` | Optional smoke create/destroy for a tenant. | [L629](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L629) |
| `async def sync_agents_configs(self, params: dict) -> dict[str, Any]` | Apply sync_agents_configs catalog revision for one service_id. | [L655](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L655) |
| `@staticmethod def _tip_has_api_base(service_id: str, agent_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L905](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L905) |
| `@classmethod def resolve_control_rpc_tenant(cls, request: AgentRequest, agent_id: str, service_id: str) -> tuple[str, str]` | Remap web evolution RPCs off default tip when it lacks API_BASE. | [L910](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L910) |
| `@staticmethod def require_officeclaw_agent(request: AgentRequest) -> AgentResponse \| None` | Allow legacy default/default; require catalog membership for named tenants. | [L961](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L961) |
| `async def _refresh_agent_manager_cache(self, cache_key: Hashable, agent_manager: Any) -> None` | 请求结束后刷新 LRU 时间戳，避免长任务执行期间 AgentManager 被 TTL 淘汰. | [L1001](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1001) |
| `async def process_message(self, request: AgentRequest) -> AgentResponse` | 处理非流式请求. | [L1022](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1022) |
| `async def process_message_stream(self, request: AgentRequest)` | 处理流式请求. | [L1036](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1036) |
| `@staticmethod def normalize_tenant_id(value: str \| None, *, default: str = 'default') -> str` | 源码未提供方法级文档字符串。 | [L1064](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1064) |
| `@staticmethod def extract_ids(request: AgentRequest) -> tuple[str, str, str]` | 从请求中提取 agent_id、service_id 与 workspace_key. | [L1077](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1077) |
| `async def reload_agent_config(self, agent_id: str, config_base: Any = None, env_overrides: dict \| None = None, *, service_id: str \| None = None) -> None` | 重新加载指定租户的 Agent 配置. | [L1097](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1097) |
| `async def get_agent_count(self) -> int` | 获取当前活跃的 AgentManager 实例数量. | [L1121](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1121) |
| `async def get_agent_manager(self, agent_id: str, service_id: str, workspace_key: str = 'default') -> Any` | 获取指定租户的 AgentManager 实例. | [L1125](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1125) |
| `def get_agent_manager_nowait(self, agent_id: str, service_id: str, workspace_key: str = 'default') -> Any \| None` | 同步获取 AgentManager 实例（不自动创建）. | [L1131](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1131) |
| `def iter_agent_managers_nowait(self) -> list[Any]` | Return cached ``AgentManager`` instances without creating new ones. | [L1137](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1137) |
| `def collect_runtime_tools_catalog_nowait(self) -> dict[str, dict[str, str]]` | Union tool catalogs from all initialized JiuWenSwarm instances. | [L1141](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L1141) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def filter_cached_agent_managers(values: Iterable[Any]) -> list[Any]` | Return ``AgentManager`` instances from cache snapshot values. | [L46](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L46) |

## `jiuwenswarm/server/runtime/tenant_catalog_registry.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L1)

**模块职责：** Synced and reloaded agent catalog keyed by request-side ``(service_id, agent_id)``.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L13) |
| `TenantCatalogSpec` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L42) |

### [`class TenantAgentSpec`](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L25)

One catalog agent entry (logical env keys in ``env``).

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `service_id` | `str` | `—` | [L28](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L28) |
| `agent_id` | `str` | `—` | [L29](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L29) |
| `config` | `dict[str, Any]` | `field(default_factory=dict)` | [L30](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L30) |
| `env` | `dict[str, Any]` | `field(default_factory=dict)` | [L31](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L31) |
| `runtime` | `dict[str, Any]` | `field(default_factory=dict)` | [L32](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L32) |
| `revision` | `str \| None` | `None` | [L33](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L33) |
| `content_hash` | `str \| None` | `None` | [L34](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L34) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def cache_key(self) -> tuple[str, str]` | 源码未提供方法级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L37) |

### [`class TenantCatalogRegistry`](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L45)

In-process catalog of synced or explicitly reloaded agents.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_instance` | `TenantCatalogRegistry \| None` | `None` | [L48](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L48) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L50](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L50) |
| `@classmethod def get_instance(cls) -> TenantCatalogRegistry` | 源码未提供方法级文档字符串。 | [L55](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L55) |
| `@classmethod def reset_instance(cls) -> None` | 源码未提供方法级文档字符串。 | [L61](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L61) |
| `@classmethod def reset_for_tests(cls) -> None` | 源码未提供方法级文档字符串。 | [L65](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L65) |
| `def upsert(self, spec: TenantAgentSpec) -> TenantAgentSpec` | 源码未提供方法级文档字符串。 | [L68](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L68) |
| `def remove(self, service_id: str, agent_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L74](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L74) |
| `def clear_service(self, service_id: str) -> int` | 源码未提供方法级文档字符串。 | [L78](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L78) |
| `def get(self, service_id: str, agent_id: str) -> TenantAgentSpec \| None` | 源码未提供方法级文档字符串。 | [L86](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L86) |
| `def contains(self, service_id: str, agent_id: str) -> bool` | 源码未提供方法级文档字符串。 | [L90](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L90) |
| `def list_ids(self, service_id: str \| None = None) -> list[str]` | Return agent_id list (optionally filtered by service_id). | [L93](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L93) |
| `def list_pairs(self, service_id: str \| None = None) -> list[tuple[str, str]]` | 源码未提供方法级文档字符串。 | [L101](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L101) |
| `def snapshot(self) -> dict[tuple[str, str], TenantAgentSpec]` | 源码未提供方法级文档字符串。 | [L108](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L108) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def catalog_cache_key(agent_id: str, service_id: str) -> tuple[str, str]` | Stable catalog/pool-style key — not tip bag key. | [L16](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L16) |

## `jiuwenswarm/server/runtime/tenant_context.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L1)

**模块职责：** Request-scoped tenant workspace bindings via ContextVar.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_TENANT_JIUWENCLAW_WS_CV` | `contextvars.ContextVar[str \| None]` | [L11](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L11) |
| `_TENANT_AGENT_ROOT_CV` | `contextvars.ContextVar[str \| None]` | [L14](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L14) |
| `_TENANT_ROOT_CV` | `contextvars.ContextVar[str \| None]` | [L17](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L17) |
| `_WORKSPACE_KEY_CV` | `contextvars.ContextVar[str \| None]` | [L20](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L20) |

### [`class TenantContextTokens`](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L26)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `jiuwenclaw_ws` | `contextvars.Token` | `—` | [L27](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L27) |
| `agent_root` | `contextvars.Token` | `—` | [L28](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L28) |
| `tenant_root` | `contextvars.Token` | `—` | [L29](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L29) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def bind_tenant_workspace_dirs(*, jiuwenclaw_workspace: str, agent_root: str, tenant_root: str) -> TenantContextTokens` | Bind tenant workspace paths for the current async task. | [L32](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L32) |
| `def reset_tenant_workspace_dirs(token: TenantContextTokens) -> None` | Reset tenant workspace bindings. | [L46](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L46) |
| `def bind_workspace_key(workspace_key: str \| None = None) -> contextvars.Token` | Bind disk ``workspace_key`` for the current task (``workspace_{key}/``). | [L53](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L53) |
| `def reset_workspace_key(token: contextvars.Token) -> None` | Restore the previous ``workspace_key`` binding. | [L61](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L61) |
| `def get_bound_workspace_key() -> str \| None` | Return the currently bound ``workspace_key``, or None if unbound. | [L66](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L66) |
| `def get_bound_jiuwenclaw_workspace() -> Path \| None` | 源码未提供函数级文档字符串。 | [L71](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L71) |
| `def get_bound_agent_root() -> Path \| None` | 源码未提供函数级文档字符串。 | [L76](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L76) |
| `def get_bound_tenant_root() -> Path \| None` | 源码未提供函数级文档字符串。 | [L81](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L81) |
| `def clear_tenant_bindings() -> None` | Reset all tenant ContextVars (tests / request teardown safety). | [L86](../../../../../jiuwenswarm/server/runtime/tenant_context.py#L86) |

## `jiuwenswarm/server/runtime/tool_catalog.py`

[打开源码](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L1)

**模块职责：** Agent 工具目录（内部）：简短描述（面向人/UI）与 ToolCard.description（面向模型）分离。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L17) |
| `_SHORT_DESCRIPTION_MAX_LEN` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L19) |
| `_CJK_RE` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L20) |
| `_FALLBACK_UNKNOWN_TEMPLATE` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L21) |
| `__all__` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L23) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _metadata_entries_to_catalog(entries: Iterable[dict[str, str]]) -> list[dict[str, str]]` | 源码未提供函数级文档字符串。 | [L37](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L37) |
| `def _list_upstream_tool_metadata(module_name: str, language: str) -> list[dict[str, str]]` | 源码未提供函数级文档字符串。 | [L56](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L56) |
| `def get_stable_tools_catalog(language: str = 'cn') -> dict[str, dict[str, str]]` | Return built-in and Agent Team metadata without creating a runtime. | [L70](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L70) |
| `def _is_sentence_terminal(index: int, char: str, text: str) -> bool` | 判断 index 处字符是否为句末标点（排除 schema 可选标记如 description?、id?）。 | [L109](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L109) |
| `def _is_meaningful_english_sentence(text: str) -> bool` | 过滤 JSON/schema 碎片（如 ", id?"），仅保留像自然语言的英文句。 | [L131](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L131) |
| `def _split_sentences(text: str) -> list[str]` | 按句末标点切分（支持中英文）。 | [L143](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L143) |
| `def _has_cjk(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L165](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L165) |
| `def short_description_from_description(description: str) -> str` | 从 ToolCard.description 提取 short_description：中英文各取第一句，再截断至 100 字。 | [L169](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L169) |
| `def _truncate_short_description(text: str, max_len: int = _SHORT_DESCRIPTION_MAX_LEN) -> str` | 源码未提供函数级文档字符串。 | [L199](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L199) |
| `def resolve_short_description(tool_name: str, model_description: str = '') -> str` | 源码未提供函数级文档字符串。 | [L206](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L206) |
| `def tool_catalog_entry_from_card(card: ToolCard) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L216](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L216) |
| `def get_registered_tools_catalog(ability_manager: Any) -> list[dict[str, str]]` | 枚举 ability_manager 中已注册工具（name / description / short_description）。 | [L231](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L231) |
| `def is_placeholder_short_description(text: str) -> bool` | 源码未提供函数级文档字符串。 | [L249](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L249) |
| `def ui_list_short_description(tool_name: str, *, description: str = '', short_description: str = '') -> str` | 源码未提供函数级文档字符串。 | [L258](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L258) |
| `def _catalog_entry_richness(entry: dict[str, str]) -> int` | 源码未提供函数级文档字符串。 | [L270](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L270) |
| `def merge_tools_catalog_entries(catalogs: Iterable[Iterable[dict[str, str]]]) -> dict[str, dict[str, str]]` | Merge tool catalogs by name, keeping the richest description. | [L279](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L279) |
| `def collect_tools_catalog_from_claws(claws: Iterable[Any]) -> dict[str, dict[str, str]]` | Union registered tools from initialized agent runtime wrappers. | [L307](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L307) |
| `def collect_tools_catalog_from_swarms(swarms: Iterable[Any]) -> dict[str, dict[str, str]]` | Backward-compatible Team runtime catalog collector. | [L326](../../../../../jiuwenswarm/server/runtime/tool_catalog.py#L326) |
