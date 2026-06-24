# Tool Calling Guard

对不支持 OpenAI function calling 的模型，在 **LLM HTTP 请求层**剥离 `tools` / `tool_choice`，避免 API 报错或模型行为异常。与 Agent 层的 `disabled_tools` 互补：前者控制「是否向模型发送工具 schema」，后者控制「Agent 是否注册工具」。

## 背景

部分模型不支持 function calling。若仍向 API 发送 `tools` / `tool_choice`，可能导致请求失败或不可预期行为。

Tool Calling Guard 在 `_patched_build_request_params` 中、发往 LLM 之前执行判定；**总开关默认关闭**，现网 standalone 部署行为与改前一致。

## 与 disabled_tools 的分工

| 机制 | 层级 | 作用 |
| :--- | :--- | :--- |
| `react.disabled_tools` + DisabledToolsRail | Agent 能力层 | 从 registry 注销工具 |
| Tool Calling Guard | LLM HTTP 请求层 | 剥离 `tools` / `tool_choice` |

限制名单模型必须在 API 层剥离 schema；仅靠 `disabled_tools` 无法阻止 ReAct 循环向 LLM 发送 tools 字段。

## 配置

位于 `config.yaml` 的 `react.tool_calling_guard`：

```yaml
react:
  tool_calling_guard:
    enabled: ${TOOL_CALLING_GUARD_ENABLED:-false}
    limited_models:
      - qwen3-32b
      - qwen3-30b-a3b
```

| 键 | 类型 | 默认 | 说明 |
| :--- | :--- | :--- | :--- |
| `enabled` | bool | **false** | 总开关；关闭时不读取任何 guard 相关 env |
| `limited_models` | string[] | 见上 | MODEL_NAME 通道限制名单（大小写不敏感） |

## 环境变量

| 变量 | 参与判定 | 用途 |
| :--- | :--- | :--- |
| `TOOL_CALLING_GUARD_ENABLED` | 是（总开关） | 启用/关闭 guard 全部逻辑 |
| `OFFICE_CLAW_DISABLE_TOOL_CALLING` | 是（通道 1） | 显式禁用/强制启用 tool calling |
| `OFFICE_CLAW_SIMPLE_CHAT_MODE_REASON` | 否 | 仅写入 debug 日志 `reason` |
| `MODEL_NAME` | 是（通道 2） | 限制模型名推断 |

开关读取优先级：

1. `TOOL_CALLING_GUARD_ENABLED` 在 staged env / 活跃 env 中**显式存在** → 解析为 bool
2. 否则 → `react.tool_calling_guard.enabled`（config.yaml）
3. 默认 → `false`

## 双通道判定（guard 开启时）

| 优先级 | 输入 | 结果 |
| :--- | :--- | :--- |
| 1 | `OFFICE_CLAW_DISABLE_TOOL_CALLING` 为 truthy | 剥离 tools |
| 2 | 同上为 falsy | **强制保留** tools（escape hatch） |
| 3 | env 未设置 + `MODEL_NAME` ∈ `limited_models` | 剥离 tools |
| 4 | 其他 | 保留 |

truthy：`true` / `1` / `yes` / `on`  
falsy：`false` / `0` / `no` / `off`

## 热加载

Guard 在**每次 LLM 调用**时通过 `read_env()` + `get_config()` 读取，天然支持 `agent.reload_config` 热加载，无需进程重启。

Web 端可通过 `config.set` 切换总开关：

```json
{ "tool_calling_guard_enabled": "true" }
```

对应环境变量 `TOOL_CALLING_GUARD_ENABLED`，写入后触发 reload，下一次对话生效。
