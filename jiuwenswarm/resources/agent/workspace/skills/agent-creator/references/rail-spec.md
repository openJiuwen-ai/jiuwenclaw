# rail 规范（rails/<name>.py）

继承 `openjiuwen.harness.rails.base.DeepAgentRail`，用生命周期钩子介入执行。何时选用见 `fill-package.md` §2。

## 基类骨架

```python
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


class MyRail(DeepAgentRail):
    """Rail 必须继承 DeepAgentRail。"""

    def __init__(self) -> None:
        super().__init__()
        self._agent: Optional[Any] = None

    def init(self, agent: Any) -> None:
        """注册到 agent 时缓存需要的运行时对象。"""
        self._agent = agent

    def uninit(self, agent: Any) -> None:
        self._agent = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        # 注入 / 拦截
        return

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        # 清理本 rail 注入的临时状态
        return
```

按需实现其它钩子（不必全写）：

| 钩子 | 典型用途 |
|------|----------|
| `before_model_call` / `after_model_call` | 注入或修改 prompt、检查输出、清理注入 |
| `before_tool_call` / `after_tool_call` | 审计参数、过滤结果、周期计数 |
| `after_task_iteration` | 每轮后自省、累计状态 |
| （能力）`force_finish` | 强制结束任务 |

## Rail 实现约束

- **必须继承 `DeepAgentRail`**；无参 `__init__(self) -> None`
- 需要 agent / prompt 装配对象时在 `init(agent)` 缓存，`uninit` 释放
- **副作用可逆**：`before_*` 注入的 section/attachment，在对应 `after_*` 清理
- 可恢复错误打日志后吞掉，勿打断整轮对话
- **不要**向 Tool 构造函数注入自己；与 Tool 共享数据用文件系统状态（见 tool-spec「与 Rail 协作」）
- 若提醒「先调某 tool」：文案写清 tool 名与禁止手改路径；与 persona / Skill 对齐

## 运行时路径规则

- Rail 不定义或缓存自己的根目录；需要状态时，从 `get_workspace() or get_cwd()` 派生并按 session 隔离
- `__init__` / `init` 中不得创建目录或写文件；需要状态时，在回调中懒创建
- Rail 不直接生成用户产物；需要文件产物时，引导调用 Tool

## 关键约束

1. `from openjiuwen.harness.rails.base import DeepAgentRail`
2. 类名 PascalCase，文件名 snake_case；manifest `rails[].class` = 类名
3. 回调上下文类型：`AgentCallbackContext`

## 代码质量要求

参考 `@references/code-quality.md`。
