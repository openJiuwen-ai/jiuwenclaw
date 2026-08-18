# rail 规范（rails/.py）

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
        self._agent = agent

    def uninit(self, agent: Any) -> None:
        self._agent = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        return

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        return
```



按需实现其它钩子（不必全写）：


| 钩子                                       | 典型用途                   |
| ---------------------------------------- | ---------------------- |
| `before_model_call` / `after_model_call` | 注入或修改 prompt、检查输出、清理注入 |
| `before_tool_call` / `after_tool_call`   | 审计参数、过滤结果、周期计数         |
| `after_task_iteration`                   | 每轮后自省、累计状态             |
| （能力）`force_finish`                       | 强制结束任务                 |




## Rail 实现约束

- **必须继承** `DeepAgentRail`；无参 `__init__(self) -> None`
- 需要 agent / prompt 时在 `init(agent)` 缓存，`uninit` 释放
- **副作用可逆**：`before_`* 注入的 section，在对应 `after_`* 清理
- 可恢复错误打日志后吞掉，勿打断整轮对话
- **不要**向 Tool 构造函数注入自己
- session 标识从 `ctx.session.get_session_id()` 获取
- 模块级全局变量只能作可丢弃缓存，不能作为跨 session 共享状态的事实来源



## 运行时路径规则

- 需要状态时从 `get_workspace() or get_cwd()` 派生并按 session 隔离
- 状态写入使用原子写（写临时文件后 `os.replace`）或锁，避免并发损坏 JSON
- `__init__` / `init` 中不得创建目录或写文件



## 与 Tool 协作

- Rail 负责监听/累计/触发；Tool 读取同一 session 状态并返回结构化报告
- 状态文件是 Rail 与 Tool 之间的事实来源；Tool 不得依赖 Rail 实例字段



## 关键约束

1. `from openjiuwen.harness.rails.base import DeepAgentRail`
2. 类名 PascalCase，文件名 snake_case；manifest `rails[].class` = 类名
3. 回调上下文类型：`AgentCallbackContext`



## 代码质量要求

参考 `@references/code-quality.md`。