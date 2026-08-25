# Rail 规范（rails/<name>_rail.py）

继承 `openjiuwen.harness.rails.base.DeepAgentRail`，通过生命周期钩子监听或修改执行过程。何时选用 Rail 见 `fill-package.md` §2。

## 基类骨架

```python
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail


class MyRail(DeepAgentRail):
    """描述 Rail 的触发时机和效果。"""

    def __init__(self) -> None:
        super().__init__()
        self._agent: Any | None = None

    def init(self, agent: Any) -> None:
        self._agent = agent

    def uninit(self, agent: Any) -> None:
        self._agent = None
```

这是结构起点，不是完整 Rail。`init` 接收运行时 agent，钩子通过 `self._agent` 访问其 prompt builder 等能力；事件数据仍从 `ctx.inputs` 读取。根据场景添加状态和钩子，最终至少实现一个有效钩子。

## 生命周期钩子

回调统一使用 `AgentCallbackContext`，业务字段从 `ctx.inputs` 读取。

| 钩子 | `ctx.inputs` | 作用 |
| --- | --- | --- |
| `before_invoke` | `InvokeInputs` | 整轮开始前读取 query、初始化本轮状态 |
| `after_invoke` | `InvokeInputs` | 整轮结束后读取 result、汇总或落盘 |
| `before_model_call` | `ModelCallInputs` | 读取 messages、动态注入 prompt |
| `after_model_call` | `ModelCallInputs` | 审计或修改 response |
| `before_tool_call` | `ToolCallInputs` | 校验或修改 tool_name、tool_args |
| `after_tool_call` | `ToolCallInputs` | 审计或过滤 tool_result、累计状态 |
| `before_task_iteration` | `TaskIterationInputs` | 每轮任务迭代前检查或调整 query |
| `after_task_iteration` | `TaskIterationInputs` | 每轮后计数、提醒、纠偏或结束任务 |

只实现需求需要的钩子。不要使用不存在的 `ctx.messages`、`ctx.prompt` 或 `ctx.prompt_assembler`。

## 按场景实现

实现前确定四项：**钩子、读取字段、产生效果、清理或状态方式**。

### 动态 prompt 增强

- 钩子：`before_model_call`
- 实现：在 `init` 缓存 `agent.system_prompt_builder`；用 `PromptSection` 调用 `add_section`
- 清理：条件不满足或 `uninit` 时 `remove_section`；同名 section 避免重复
- 禁止：`add_section({...})`，必须传 `PromptSection` 对象

### 模型输出审计或改写

- 钩子：`after_model_call`
- 读取：`ctx.inputs.response`
- 实现：只读检查，或确定性修改 `response.content`；content 可能是 `str` 或列表
- 注意：流式 token 可能已发送，不能把该钩子当作流式内容撤回机制

### 工具调用校验或处理

- 前置：`before_tool_call` 读取/修改 `tool_name`、`tool_args`
- 后置：`after_tool_call` 读取/修改 `tool_result`
- 拒绝：设置 `ctx.extra["_skip_tool"] = True`，并同时填充运行时需要的 `tool_result` 和 `tool_msg`
- `tool_msg` 必须是带当前 `tool_call_id` 的 `ToolMessage`，不能是字符串或字典，否则写入上下文时会触发消息类型校验错误
- 不要只设置跳过标志而留下空结果

```python
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


def reject_tool(ctx: AgentCallbackContext, message: str) -> None:
    """跳过当前工具调用，并向模型返回结构正确的拒绝结果。"""
    tool_call_id = str(getattr(ctx.inputs.tool_call, "id", "") or "unknown-tool-call")
    ctx.extra["_skip_tool"] = True
    ctx.inputs.tool_result = {"status": "blocked", "reason": message}
    ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)
```

### 周期检查或强制结束

- 钩子：`after_task_iteration`
- 读取：`iteration`、`result`
- 实现：累计次数、写状态、用 `ctx.push_steering(...)` 提醒纠偏
- 必须结束时用 `ctx.request_force_finish({"output": "...", "result_type": "answer"})`

### 整轮汇总

- 钩子：`after_invoke`
- 读取：`ctx.inputs.result`
- 实现：审计、统计或持久化最终结果；不用于修改已经输出的流式 token

### 与自定义 Tool 协作

仅在模板同时包含 Rail 和 Tool 时使用：

- Rail 负责监听、累计、审计和触发；Tool 负责主动查询或执行动作。
- 双方以同一 session 状态文件为事实来源，不共享 Rail 实例字段。
- Rail 从 `ctx.session.get_session_id()` 取 session；Tool 从 `kwargs["session"].get_session_id()` 取 session。
- 状态根从 `get_workspace() or get_cwd()` 派生，按 session 隔离，如 `.state/<session_id>.json`。
- 使用临时文件加 `os.replace` 原子写入；Tool 侧同时遵守 `tool-spec.md` 的「与 Rail 协作」。
- 禁止 Tool 扫描 `agent.rails`，或通过构造函数注入 Rail、agent、session、workspace。

## 实现约束

- 必须无参构造；类名 PascalCase，文件名 snake_case，manifest `rails[].class` 与类名一致。
- 最终代码不保留只有 `pass` / `return` 的占位钩子；`get_callbacks()` 必须非空。
- `__init__` / `init` 不创建目录、不写文件；`uninit` 清理 section 和运行时引用。
- 模块级全局变量只能作可丢弃缓存，不能作为跨 session 状态来源。
- 可恢复故障记录日志后降级；安全拒绝等策略结果不能被误吞。
- 代码质量遵守 `@references/code-quality.md`。
