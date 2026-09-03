# JiuwenSwarm 核心模块设计与接口文档需求

## 目标

为当前代码快照中的以下范围建立可归档、可导航、可追溯到源码的中文设计资料：

- `jiuwenswarm/*.py`
- `jiuwenswarm/server/**/*.py`
- `jiuwenswarm/common/**/*.py`
- `jiuwenswarm/instance_manager/**/*.py`
- `jiuwenswarm/server/handlers/**/*.py`（已包含在 `server` 范围内，但需要单独突出说明）

## 交付要求

1. 模块设计说明必须准确反映当前真实实现，包括模块边界、职责、依赖、关键状态、生命周期、并发/异步模型、数据流、错误与降级策略。
2. 接口说明必须覆盖外部协议入口和源码中可调用的类、函数、方法、数据模型、常量与注册入口；签名以 AST/源码核对结果为准。
3. 每个纳入范围的 Python 源文件至少在全量文件索引中出现一次，并带有可点击的相对源码链接。
4. `server/handlers` 需要给出操作分发映射、请求上下文、输入/输出与副作用的专门说明。
5. 必要时引用范围外的启动配置、测试或调用方，以解释真实架构，但不得把推测写成既定事实。
6. 归档目录固定为 `docs/zh/architecture/jiuwenswarm-core/`，入口为该目录的 `README.md`。
7. 忽略 `__pycache__`、`.pyc` 及其他生成物；范围统计按 Git 可见的 `.py` 源文件执行。
8. 不改动运行时代码，不覆盖工作区中已有的未提交文件。

## 验收标准

- 源文件基线为 285 个唯一 `.py` 文件：根目录 10、`server` 192、`common` 77、`instance_manager` 6；`handlers` 15 已计入 `server`。
- 文档入口能导航到所有设计分册、接口分册和文件索引。
- 文档内全部本地 Markdown 链接均能从其所在文件解析到现有路径或现有锚点。
- 文件索引与当前源文件集合双向一致：没有遗漏，也没有指向范围外文件的误计数。
- 抽样核对关键启动链、WebSocket/HTTP 分发链、会话/Agent 生命周期、Skill 与 Skill Turbo 执行链、公共安全/配置组件。
