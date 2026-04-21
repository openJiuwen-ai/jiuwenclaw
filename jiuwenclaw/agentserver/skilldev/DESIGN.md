# SkillDev 模式设计文档

> 版本：v1.1（与实现同步；代码为准）

---

## 1. 定位与目标

SkillDev 是 JiuWenClaw 平台的一种**运行模式**，专门用于辅助开发者端到端地创建、测试、优化并打包一个 Agent Skill（`.skill` 包）。

它不是一个对话式 Agent，而是一条**确定性工程流水线**：接受用户需求描述，依次经过初始化、需求澄清、规划、代码生成、格式校验、测试、评测、改进、打包与可选描述优化等阶段，最终输出可直接安装的 Skill 产物。

**`SkillDevTaskMode`（`create` / `create_with_resources` / `modify`）** 存在于 `SkillDevState.mode` 中，用于 Todo 分组展示等；**默认值为 `create`**，并随 `state.json` 持久化。**当前实现未在 `skilldev.start` 时根据请求参数自动推断** `create_with_resources` 或 `modify`（`schema.py` 中曾设计的 `determine_task_mode` 逻辑仍为注释参考）。实际上下文区分主要依赖 INIT 阶段写入工作区的数据：

| 模式（状态字段） | 典型工作区事实 | 说明 |
|---|---|---|
| `create` | `skill/` 为空、仅文本需求 | 默认 |
| `create_with_resources` | `resources/ref-files` 等目录存在用户上传资料 | 由 `files` / `skill_packages` / `tool_spec_files` 等写入后，目录非空 |
| `modify` | `skill/` 中已有 SKILL.md 或参考包解压内容 | 依赖工作区已有 skill 内容；非单独请求标志位 |

产品文档若描述「`query` + `resources` 路径列表」类入口，须与 **`skilldev.start` 实际接受的 `params` 键名**（见 §6）对齐。

---

## 2. 整体架构

### 2.1 在 JiuWenClaw 中的位置

```
前端（Web 工作台 / 内嵌面板：WebSocket）
    ↕ JSON 帧：type=req|res|event（见 WebChannel）
Gateway（可选，多实例时建议对同一 task_id 粘性路由）
    ↓
JiuWenClaw.process_message_stream()
    ├── 普通 chat 请求     → ReActAgent（经 Adapter）
    ├── skills.* 请求      → SkillManager 等
    └── skilldev.* 请求    → SkillDevService.handle()  ← 本文档范围
```

`skilldev.*` 在 `process_message_stream` 中**直接委托** `SkillDevService`，不经过主对话 Agent。`ReqMethod` 中所有 `skilldev.` 前缀的方法均属于 SkillDev（见 `interface.py` 中 `_SKILLDEV_METHODS`）。

**WebSocket 侧约定（`web_channel.py` + 前端客户端）**：

- 上行：`{ "type": "req", "id", "method", "params" }`；`params` 须带 `session_id`（缺省时服务端生成）。
- 下行：`type: "res"` 对应一次性请求（`id` 与上行对齐）；`type: "event"` 承载广播事件（`skilldev.start` / `skilldev.respond` 流式推送不与 `req.id` 绑定，靠 `payload.task_id` 关联）。
- `skilldev.start`、`skilldev.respond` 在 WebChannel 中 `is_stream=True`，用于实时推送 `skilldev.*` 事件。

SkillDev 与主 ReActAgent **隔离会话与对话历史**，通过 `SkillDevDeps` 复用：

- 模型配置（`model_name`、`model_client_config`、`model_config_obj`）
- 各阶段 `create_stage_agent()` 内注册的工具（`sysop_config` 当前在 `_get_skilldev_service()` 中可为 `None`，文件工具依赖阶段白名单与 SysOperation 配置）

### 2.2 模块划分

```
jiuwenclaw/agentserver/skilldev/
├── schema.py          # 数据模型层：枚举、状态、事件、挂起点配置、评测数据结构
├── pipeline.py        # 编排层：确定性状态机（运行 & 恢复逻辑）
├── service.py         # 服务层：无状态请求处理器，Method 路由
├── context.py         # 上下文层：阶段运行环境（emit + create_stage_agent）
├── deps.py            # 依赖注入：最小外部依赖集合
├── store.py           # 基础设施：状态持久化（checkpoint）
├── workspace.py       # 基础设施：任务工作区管理
└── stages/            # 阶段处理器层
    ├── base.py              # StageHandler 抽象基类 + StageResult
    ├── init_stage.py        # INIT：工作区初始化与上传资源落盘
    ├── clarify_stage.py     # CLARIFY：生成澄清问题
    ├── plan_stage.py        # PLAN：综合 QA 输出开发计划 → GENERATE
    ├── generate_stage.py    # GENERATE：SKILL.md 生成
    ├── validate_stage.py    # VALIDATE：格式校验
    ├── test_design_stage.py # TEST_DESIGN：测试用例设计
    ├── test_run_stage.py    # TEST_RUN：测试执行
    ├── evaluate_stage.py    # EVALUATE：评分 + 聚合 + 分析
    ├── improve_stage.py     # IMPROVE：根据反馈改进
    ├── package_stage.py     # PACKAGE：打包 .skill
    └── desc_optimize_stage.py # DESC_OPTIMIZE：描述优化循环
```

**分层依赖关系**（只允许上层依赖下层）：

```
service.py
    → pipeline.py
        → stages/*.py
            → context.py
                → deps.py
                    → store.py
                    → workspace.py
    → schema.py（所有层均可依赖）
```

---

## 3. Pipeline 状态机

### 3.1 完整阶段流程

```
INIT → CLARIFY → QUESTION_CLARIFY* → PLAN → GENERATE → VALIDATE
    → TEST_DESIGN → TEST_RUN → EVALUATE → REVIEW*
    → IMPROVE → (循环回 TEST_RUN)
    → DESC_OPTIMIZE_CONFIRM* → DESC_OPTIMIZE(可选) → PACKAGE → COMPLETED

标注 * 的为挂起点（Suspension Point）：Pipeline 在此暂停，等待 `skilldev.respond`
```

| 阶段 | 类型 | 职责 |
|---|---|---|
| `INIT` | 执行 | 创建工作区子目录；将 `files` / `skill_packages` / `tool_spec_files` 解码写入 `resources/` 等；更新目录空标记 |
| `CLARIFY` | 执行 | Agent 生成结构化澄清问题 → `state.clarification_questions`，进入 `QUESTION_CLARIFY` |
| `QUESTION_CLARIFY` | **挂起点** | 推送 `confirm_type: question_clarify`，等待用户提交 `answers` |
| `PLAN` | 执行 | 综合原始需求与澄清答案，输出 `state.plan`，**直接进入 `GENERATE`**（无单独「计划确认」挂起点） |
| `GENERATE` | 执行 | 按 plan 生成 SKILL.md 及支撑文件 |
| `VALIDATE` | 执行 | 静态校验 SKILL.md（frontmatter、命名等）；失败可与 GENERATE 迭代 |
| `TEST_DESIGN` | 执行 | 设计测试用例集（EvalSet） |
| `TEST_RUN` | 执行 | 执行测试用例 |
| `EVALUATE` | 执行 | 评分、聚合 Benchmark、分析报告 |
| `REVIEW` | **挂起点** | `accept` → 描述优化确认；`improve` + `feedback` → `IMPROVE` |
| `IMPROVE` | 执行 | 根据反馈改进 SKILL.md，之后回到 `TEST_RUN` 等 |
| `DESC_OPTIMIZE_CONFIRM` | **挂起点** | `optimize` → `DESC_OPTIMIZE`；`skip` → `PACKAGE` |
| `DESC_OPTIMIZE` | 执行 | 描述优化循环 |
| `PACKAGE` | 执行 | 打包 `.skill` |
| `COMPLETED` | 终态 | 流程结束 |
| `ERROR` | 终态 | 不可恢复错误或取消 |

### 3.2 Pipeline 生命周期

Pipeline **不长驻内存**。每次请求的处理流程：

```
收到请求
  → StateStore 加载状态（或创建新状态）
  → new SkillDevPipeline(state, deps)
  → pipeline.run() 或 pipeline.resume()
  → 执行到挂起点或终态
  → StateStore 保存状态（checkpoint）
  → Pipeline 对象释放
```

这意味着即使服务重启，任务也能从上次 checkpoint 恢复继续执行。

### 3.3 run() 的内部逻辑

```python
while stage not in (COMPLETED, ERROR):
    if stage in SUSPENSION_POINTS:       # 命中挂起点
        emit TODOS_UPDATE                # 更新 Todo 列表
        emit CONFIRM_REQUEST             # 驱动前端弹出确认框
        checkpoint()
        break                            # 暂停，等待下次 resume()

    handler = STAGE_HANDLERS[stage]      # 查找处理器
    emit STAGE_CHANGED                   # 通知前端阶段变更
    emit TODOS_UPDATE                    # 同步 Todo 状态
    result = await handler.execute(ctx)  # 执行阶段逻辑
    state.stage = result.next_stage      # 跳转下一阶段
    checkpoint()
```

### 3.4 resume() 的内部逻辑

```python
def resume(data: dict):
    suspension = SUSPENSION_POINTS[state.stage]  # 当前必须是挂起点
    suspension.on_resume(state, data)             # 更新状态（写入用户的 plan/反馈）
    next_stage = suspension.next_stage            # 计算下一阶段
    if callable(next_stage):
        next_stage = next_stage(data)             # REVIEW 的下一阶段由用户 action 决定
    state.stage = next_stage
    yield from run()                              # 继续执行
```

---

## 4. 挂起点（Suspension Points）机制

挂起点是 Pipeline 的**结构化暂停**：Pipeline 到达该阶段时不执行任何 Agent 逻辑，而是向前端推送确认请求，然后等待用户响应。

### 4.1 SuspensionConfig 结构

```python
@dataclass
class SuspensionConfig:
    confirm_type: str           # 标识确认类型（前端用于选择弹框样式）
    title: str                  # 弹框标题
    message: str                # 弹框描述文字
    actions: list[dict]         # 按钮列表：[{"id": "confirm", "label": "确认", "style": "primary"}]
    extract_data: Callable      # (state) → dict，从 state 提取要展示的数据
    on_resume: Callable         # (state, data) → None，根据用户响应更新 state
    next_stage: Stage | Callable # 下一阶段（REVIEW 的下一阶段取决于用户选择）
```

### 4.2 挂起点配置（与 `schema.py` 中 `SUSPENSION_POINTS` 一致）

**QUESTION_CLARIFY（需求澄清）**
- 推送：`CONFIRM_REQUEST`，`confirm_type: "question_clarify"`，`data.questions` 来自 `state.clarification_questions`
- 用户通过 `skilldev.respond` 提交：`action` 与配置中按钮 `id` 一致（当前为 `"submit"`），并携带 `answers: [{ "question_id", "answer" }, ...]`
- `on_resume`：写入 `state.clarification_answers`；下一阶段固定为 `PLAN`

**REVIEW（评测审阅）**
- 推送：`confirm_type: "review"`，`data` 含 `benchmark`、`report`、`iteration`
- `action: "accept"` → 下一阶段 `DESC_OPTIMIZE_CONFIRM`；`action: "improve"` → `IMPROVE`（若提供 `feedback` 会追加到 `state.feedback_history`）

**DESC_OPTIMIZE_CONFIRM（描述优化确认）**
- 推送：`confirm_type: "desc_optimize_confirm"`，`data` 含 `current_description`（来自 `state.plan.description`）
- `action: "optimize"` → `DESC_OPTIMIZE`；`action: "skip"`（默认）→ `PACKAGE`

---

## 5. 事件系统

后端通过 WebSocket 流式推送 `AgentResponseChunk`，前端根据 `event_type` 直接映射 UI 动作。

### 5.1 事件分类

| 事件类型 | 触发时机 | 前端响应 |
|---|---|---|
| `skilldev.started` | `skilldev.start` 已接受并开始跑 Pipeline | 记录 `task_id`，重置进度视图 |
| `skilldev.stage_changed` | 进入可执行阶段前（挂起点之前） | 更新阶段指示 / 时间线 |
| `skilldev.progress` | 阶段内进度文案 | 时间线或日志区展示 |
| `skilldev.agent_thinking` | Agent 推理流（delta） | 流式展示思考 |
| `skilldev.agent_output` | Agent 正文输出流（delta） | 与 thinking 区分展示 |
| `skilldev.test_progress` | 测试执行中 | 测试进度 |
| `skilldev.tool_call` / `skilldev.tool_result` | 工具调用与结果 | 结构化展示（若 UI 支持） |
| `skilldev.todos_update` | 阶段切换或挂起点 | **更新 Todo 列表**（内容由 `compute_todos` 决定） |
| `skilldev.confirm_request` | 命中挂起点 | **弹出确认框** |
| `skilldev.artifact_ready` | 产物就绪 | **更新产物列表** |
| `skilldev.eval_ready` | EVALUATE 完成 | 展示 benchmark 等 |
| `skilldev.validate_result` | VALIDATE 完成 | 展示校验结果 |
| `skilldev.desc_opt_ready` | DESC_OPTIMIZE 完成 | 展示描述前后对比等 |
| `skilldev.error` | 错误 / 取消 | 展示错误 |
| `skilldev.suspended` | 本轮 `run`/`resume` 在挂起点或正常暂停 | 等待用户 `respond` |
| `skilldev.completed` | 到达 `COMPLETED` | 流程结束 |

### 5.2 关键事件 Payload 结构

**`skilldev.confirm_request`**（驱动前端弹窗；`task_id` 由 Pipeline `_emit` 统一注入）：
```json
{
  "event_type": "skilldev.confirm_request",
  "task_id": "sd_xxx",
  "confirm_type": "question_clarify",
  "title": "请回答以下问题",
  "message": "AI 需要了解更多信息以生成精准的开发计划",
  "actions": [
    {"id": "submit", "label": "提交回答", "style": "primary"}
  ],
  "data": {
    "questions": [
      {
        "id": "q1",
        "question": "...",
        "options": [{"id": "a", "label": "..."}],
        "allow_custom": true
      }
    ]
  }
}
```

**`skilldev.todos_update`**（驱动前端 Todo 列表；`id`/`label` 与 `compute_todos` 输出一致）：
```json
{
  "event_type": "skilldev.todos_update",
  "task_id": "sd_xxx",
  "todos": [
    {"id": "plan", "label": "需求澄清与规划", "status": "completed"},
    {"id": "generate", "label": "技能生成与校验", "status": "in_progress"},
    {"id": "test", "label": "测试与评测", "status": "pending"},
    {"id": "review", "label": "评测审阅", "status": "pending"},
    {"id": "improve", "label": "优化改进", "status": "pending"},
    {"id": "desc_optimize", "label": "描述优化", "status": "pending"},
    {"id": "package", "label": "打包", "status": "pending"}
  ]
}
```

**`skilldev.artifact_ready`**（驱动前端产物列表）：
```json
{
  "event_type": "skilldev.artifact_ready",
  "task_id": "sd_xxx",
  "artifact": {
    "id": "skill_package",
    "name": "my_skill.skill",
    "type": "skill_package",
    "size_bytes": 12345,
    "browsable": true,
    "downloadable": true
  }
}
```

### 5.3 后端驱动原则

**Todo 列表的计算完全由后端控制**，前端只做渲染。`compute_todos()`（`schema.py`）根据 `current_stage` 和可选 `mode` 过滤分组，并为各组计算 `completed` / `in_progress` / `pending`（错误终态为 `cancelled`）。当前 `_STAGE_GROUPS` 为：

- `plan`：`INIT`, `CLARIFY`, `QUESTION_CLARIFY`, `PLAN`
- `generate`：`GENERATE`, `VALIDATE`
- `test`：`TEST_DESIGN`, `TEST_RUN`, `EVALUATE`
- `review`：`REVIEW`
- `improve`：`IMPROVE`
- `desc_optimize`：`DESC_OPTIMIZE_CONFIRM`, `DESC_OPTIMIZE`
- `package`：`PACKAGE`

---

## 6. 外部 API 接口

凡 `ReqMethod` 中以 `skilldev.` 为前缀的方法，均由 `JiuWenClaw.process_message_stream()` 路由到 `SkillDevService.handle()`（见 `interface.py` 中 `_SKILLDEV_METHODS`）。**Web 通道**上：`skilldev.start` / `skilldev.respond` 为流式（`WebChannel.is_stream=True`），其余多为单次响应（`res` 帧）。

### 6.1 接口总览

| Method | 类型 | 说明 |
|---|---|---|
| `skilldev.start` | 流式 | 新建任务并执行 Pipeline 直至挂起或完成 |
| `skilldev.respond` | 流式 | 在挂起点恢复执行 |
| `skilldev.status` | 一次性 | 查询单任务 / 列出 `task_id` 列表 |
| `skilldev.parse_skill` | 一次性 | 任务开始前导入 `.zip`/`.skill` 到工作区 `skill/` |
| `skilldev.download` | 一次性 | 下载打包产物（Base64） |
| `skilldev.cancel` | 一次性 | 设置取消信号（在 Pipeline 循环边界生效） |
| `skilldev.file.list` | 一次性 | `skill/` 目录文件树 |
| `skilldev.file.read` | 一次性 | 读取 `skill/` 下相对路径文件文本 |

### 6.2 接口详情

#### `skilldev.start` — 发起新任务

**请求参数（`SkillDevService._handle_start`，以代码为准）**：

| 字段 | 说明 |
|---|---|
| `session_id` | Web 会话；与 `task_id` 二选一作为任务标识 |
| `task_id` | 可选；**缺省时使用 `session_id` 作为 `task_id`** |
| `query` | 用户需求文本，写入 `state.input["query"]` |
| `files` | 可选；参考资料列表，元素为带 `filename`、`base64Data` 的对象（INIT 写入 `resources/ref-files`）；若项含可下载 `url`/`uri`，`WebChannel._process_files` 可先落盘并补 `path` |
| `skill_packages` | 可选；参考 Skill 压缩包列表（写入 `resources/ref-skills`，支持 `.zip`/`.skill`） |
| `tool_spec_files` | 可选；工具说明文件列表（写入 `resources/tool_specs`，并解析为 `state.external_tools`） |

示例：

```json
{
  "session_id": "sess_xxx",
  "query": "帮我创建一个能搜索 arXiv 的 Skill",
  "files": [],
  "skill_packages": [],
  "tool_spec_files": []
}
```

**说明**：文档或旧前端若仍使用 `tools` / `resources`（路径字符串）/ `existing_skill` 等键名，**当前 `SkillDevService` 不会读取**；须与上表键名一致，或通过 `skilldev.parse_skill` / 上传 `files` 填充工作区。

**响应事件流（示意）**：

```
→ skilldev.started { task_id }
→ skilldev.stage_changed / skilldev.todos_update / skilldev.progress / …
→ （CLARIFY / PLAN / GENERATE … 各阶段事件）
→ 若进入挂起点：skilldev.confirm_request … → skilldev.suspended { stage }
→ 若跑完：skilldev.completed { stage: "completed" }
```

首次人机交互挂起点为 **`question_clarify`**，而非「计划确认」。

#### `skilldev.respond` — 统一确认入口

**约束**：仅当 `state.stage` 为 `SUSPENSION_POINTS` 中某一挂起点时可用；否则返回 `skilldev.error`。

**请求参数（示例）**：

```json
{
  "task_id": "sd_xxx",
  "session_id": "sess_xxx",
  "action": "submit",
  "answers": [{ "question_id": "q1", "answer": "..." }]
}
```

- `action`：须与当前 `CONFIRM_REQUEST.actions[].id` 一致（澄清为 `submit`；审阅为 `accept`/`improve`；描述优化确认为 `optimize`/`skip`）。
- `answers`：仅 **QUESTION_CLARIFY** 使用。
- `feedback`：**REVIEW** 且 `action: improve` 时使用。
- `plan`：当前挂起点配置**不**消费通用 `plan` 覆盖（计划由 `PLAN` 阶段直接生成）；旧文档中的 `plan_confirm` 流程已移除。

**响应**：与 `start` 类似，推送阶段事件直至下一次 `suspended` 或 `completed`。

#### `skilldev.status` — 查询状态

**请求参数**：
- 单任务：`{ "task_id": "sd_xxx" }`（可配合 `session_id`）
- 列表：`{}` 或不传 `task_id`

**响应**：
- 列表：`{ "ok": true, "tasks": ["id1", ...] }`
- 单任务：`{ "ok": true, "task_id", "stage", "mode", "iteration", "plan", "eval_results", "created_at", "updated_at", "error"? }`；不存在时 `ok: false` 且含 `error` 说明（见 `service.py`）

#### `skilldev.parse_skill` — 导入本地包

在对应任务**尚未产生 `state.json`（未正式开始）**时可调用。

**请求参数**：`task_id` 或 `session_id`，以及 `skill_package: { "filename", "base64Data" }`（后缀 `.zip` 或 `.skill`）。

**响应**：`{ "ok": true, "task_id", "message" }` 或错误信息。

#### `skilldev.download` — 下载产物

**请求参数**：`{ "task_id": "sd_xxx" }`

**响应**：
```json
{
  "ok": true,
  "filename": "arxiv_searcher.skill",
  "content_base64": "UEsDB...",
  "size_bytes": 12345
}
```

前提：`state.zip_path` 已设置且文件存在（打包完成后）。

#### `skilldev.cancel` — 取消任务

**请求参数**：`{ "task_id": "sd_xxx" }`

**行为**：若 Pipeline 正在运行且已注册 `cancel_events[task_id]`，则 `set()`；下一轮 Pipeline 循环将置 `ERROR` 并推送错误信息。未运行时返回提示性 `message`（见 `service.py`）。

#### `skilldev.file.list` — 获取文件树

**请求参数**：`{ "task_id": "sd_xxx" }`

**说明**：树仅覆盖工作区下 **`skill/`** 目录；目录节点的 `path` 以 `/` 结尾（实现见 `service._build_file_tree`）。

**响应**：
```json
{
  "ok": true,
  "tree": [
    {"path": "SKILL.md", "type": "file", "size": 2048},
    {"path": "tools/",   "type": "dir",  "children": [
      {"path": "tools/search.py", "type": "file", "size": 512}
    ]}
  ]
}
```

#### `skilldev.file.read` — 读取文件内容

**请求参数**：`{ "task_id": "sd_xxx", "path": "SKILL.md" }`（`path` 为相对 `skill/` 的路径）

**响应**：
```json
{
  "ok": true,
  "path": "SKILL.md",
  "content": "---\nname: arxiv_searcher\n..."
}
```

---

## 7. 核心数据模型

### 7.1 SkillDevState — 运行时状态（唯一可信源）

以 `schema.py` 中 `SkillDevState` 为准，核心字段如下（省略与 checkpoint 一一对应的细项时，请直接读源码）：

```python
@dataclass
class SkillDevState:
    task_id: str
    stage: SkillDevStage
    mode: SkillDevTaskMode       # 默认 create；持久化至 state.json
    iteration: int

    input: dict                  # 至少含 start 传入的 query；以及 files/skill_packages/tool_spec_files 等原始输入

    # 工作区「是否为空」标记（INIT 后更新）
    skill_dir_empty: bool
    ref_files_dir_empty: bool
    ref_skills_dir_empty: bool
    tool_specs_dir_empty: bool

    reference_texts: list[str]
    existing_skill_md: str | None
    clarification_questions: list[dict]
    clarification_answers: list[dict]
    plan: dict | None
    generate_retries: int
    last_validate_error: str | None
    evals: dict | None
    eval_results: dict | None
    feedback_history: list[dict]
    external_tools: list[dict]   # 来自 tool_spec_files
    desc_optimize_result: dict | None

    zip_path: str | None
    zip_size: int

    created_at: str
    updated_at: str
    error: str | None
```

**State 的生命周期**：
- `SkillDevService._handle_start()` 创建初始 State
- Pipeline 各阶段的 StageHandler 通过 `ctx.state` 读写
- `pipeline._checkpoint()` 在每个阶段边界将 State 序列化到 `state.json`
- `SkillDevService._handle_respond()` 从 `state.json` 加载并恢复

### 7.2 评测相关数据结构

评测阶段（TEST_DESIGN → TEST_RUN → EVALUATE）使用以下结构，设计参考 [official skill-creator](https://github.com/anthropics/anthropic-quickstarts/tree/main/skill-creator)：

```
EvalSet
  └── EvalCase[]         # 每个测试用例（id, prompt, expectations[]）

GradingResult            # 单次运行的评分结果
  └── GradingExpectation[] # 每条 assertion 的 pass/fail + 证据

RunTiming                # 单次运行的耗时/token 数据

Benchmark                # 完整基准测试结果
  └── BenchmarkRun[]     # with_skill vs baseline 的对比 run 记录

DescOptimizeIteration    # 描述优化的单轮迭代结果
```

---

## 8. 基础设施

### 8.1 StateStore — 状态持久化

**职责**：在阶段边界将 `SkillDevState` 序列化为 JSON 文件（checkpoint），支持断点续传。

**存储路径**（相对 JiuWenClaw 工作区根目录）：

```
{get_workspace_dir()}/skilldev/{task_id}/state.json
```

`get_workspace_dir()` 来自全局配置（见 `jiuwenclaw.utils`），**不等同于** DeepAgent 的 `get_agent_workspace_dir()`。

**核心接口**：
```python
await store.save_state(task_id, state)      # checkpoint（阶段结束时调用）
await store.load_state(task_id)             # 恢复（resume 时调用）
store.load_state_sync(task_id)              # 同步版（status 查询时调用）
store.list_tasks()                          # 列出所有有效 task_id
```

**扩展点**：当前为本地文件实现，多实例部署时可替换为 Redis 实现，接口不变。

### 8.2 WorkspaceProvider — 任务工作区

**职责**：为每个 task_id 维护独立、标准化的工作区目录。

**目录结构**：
```
{get_workspace_dir()}/skilldev/{task_id}/
├── state.json          ← StateStore 的 checkpoint 文件
├── resources/          ← ref-files / ref-skills / tool_specs 等
├── skill/              ← 生成的 Skill 目录（Agent 的写入区；file.list/read 仅暴露此目录）
│   ├── SKILL.md
│   └── ...（工具实现文件等）
├── evals/
│   ├── evals.json          ← 测试用例定义（EvalSet）
│   └── iteration-{N}/      ← 第 N 轮测试的结果文件
│       ├── grading.json
│       └── timing.json
└── output/
    └── {skill_name}.skill  ← 最终打包产物
```

**核心接口**：
```python
workspace = await provider.ensure_local(task_id)  # 确保目录存在，返回路径
path = provider.get_local_path(task_id)           # 仅返回路径（不创建）
await provider.sync_to_remote(task_id)            # 扩展点：同步到远程存储
```

### 8.3 SkillDevDeps — 依赖注入

`SkillDevService` 不依赖 `JiuWenClaw` 实例，只接收最小外部依赖（`deps.py`）：

```python
@dataclass
class SkillDevDeps:
    model_name: str
    model_client_config: dict
    model_config_obj: dict
    sysop_config: object | None        # 当前门面中可为 None；阶段内工具通过 SysOperation 白名单注册
    state_store: StateStore
    workspace_provider: WorkspaceProvider
    cancel_events: dict[str, asyncio.Event]  # task_id → 取消信号
```

由 `JiuWenClaw._get_skilldev_service()` 懒初始化并注入（首次 `skilldev.*` 请求触发）。

---

## 9. 阶段处理器开发指南

### 9.1 StageHandler 合同

每个阶段实现一个 `StageHandler` 子类：

```python
class MyStageHandler(StageHandler):
    async def execute(self, ctx: SkillDevContext) -> StageResult:
        # 1. 从 ctx.state 读取上游数据
        plan = ctx.state.plan

        # 2. 通过 ctx.emit() 向前端推送进度事件
        await ctx.emit(SkillDevEventType.PROGRESS, {"message": "开始处理..."})

        # 3. 通过 ctx.create_stage_agent() 创建隔离 Agent 执行 AI 逻辑
        agent = ctx.create_stage_agent(
            stage_name="my_stage",
            system_prompt=MY_SYSTEM_PROMPT,
            tools=["file_read", "file_write"],
        )
        result = await agent.run(prompt)

        # 4. 将结果写入 ctx.state
        ctx.state.some_field = result

        # 5. 返回下一阶段
        return StageResult(next_stage=SkillDevStage.NEXT_STAGE)
```

**关键约束**：
- StageHandler 不得持有跨请求的状态（不能有实例变量保存业务数据）
- 所有业务状态通过 `ctx.state` 读写
- Agent 通过 `ctx.create_stage_agent()` 创建，每阶段独立，不共享上下文
- 通过 `ctx.workspace` 访问任务目录（Path 对象）

### 9.2 每阶段 Agent 隔离原则

各阶段实际工具白名单以 `context.py` 中 `STAGE_TOOL_WHITELIST` 及对应 `*_stage.py` 为准。下表为概念指引：

| 阶段 | 工具焦点（示例） | System Prompt 焦点 |
|---|---|---|
| CLARIFY | `file_read`, `file_glob`, `file_listdir` | 产出澄清问题 JSON |
| PLAN | `file_read`, `file_glob`, `file_listdir` | 综合 QA，输出结构化 plan |
| GENERATE | `file_read`, `file_write`, … | 按 plan 生成 SKILL.md 及支撑文件 |
| TEST_DESIGN | 依白名单 | 设计 EvalSet |
| TEST_RUN | 依白名单 | 执行测试 |
| EVALUATE | 依白名单 | 评分与聚合 |
| IMPROVE | 依白名单 | 按反馈改 SKILL.md |
| DESC_OPTIMIZE | 依白名单 | 描述优化循环 |

### 9.3 注册新阶段的步骤

1. 在 `stages/` 下创建 `{name}_stage.py`，实现 `StageHandler`
2. 在 `stages/__init__.py` 导出新 Handler
3. 在 `schema.py` 的 `SkillDevStage` 枚举中添加新阶段值
4. 在 `pipeline.py` 的 `STAGE_HANDLERS` 字典中注册
5. 如需在 Todo 列表中显示，在 `schema.py` 的 `_STAGE_GROUPS` 中配置归属分组

---

## 10. 端到端调用示例

以下是一次典型流程的接口时序（前端视角；中间省略进度与 thinking 等事件）：

```
① 发起任务
  → skilldev.start { session_id, query, files?, skill_packages?, tool_spec_files? }
  ← skilldev.started { task_id }
  ← … CLARIFY / PLAN 等阶段事件 …
  ← skilldev.confirm_request { confirm_type: "question_clarify", data: { questions } }
  ← skilldev.suspended { stage: "question_clarify" }

② 用户提交澄清答案
  → skilldev.respond { task_id, action: "submit", answers: [...] }
  ← … PLAN → GENERATE → VALIDATE → 测试/评测 …
  ← skilldev.confirm_request { confirm_type: "review", … }
  ← skilldev.suspended { stage: "review" }

③ 评测审阅 — 继续改进
  → skilldev.respond { task_id, action: "improve", feedback: "…" }
  ← … IMPROVE 与后续测试/评测迭代 …
  ← 再次 skilldev.confirm_request { confirm_type: "review" } …

④ 评测审阅 — 通过
  → skilldev.respond { task_id, action: "accept" }
  ← skilldev.confirm_request { confirm_type: "desc_optimize_confirm" }
  ← skilldev.suspended { stage: "desc_optimize_confirm" }

⑤ 描述优化确认
  → skilldev.respond { task_id, action: "skip" }   # 或 "optimize"
  ← … PACKAGE … / 或 DESC_OPTIMIZE 再 PACKAGE …
  ← skilldev.artifact_ready { artifact: { … } }
  ← skilldev.completed

⑥ 下载与浏览
  → skilldev.download { task_id }
  → skilldev.file.list { task_id }
  → skilldev.file.read { task_id, path: "SKILL.md" }
```

（可选）任务开始前导入已有包：`skilldev.parse_skill { task_id|session_id, skill_package }`。

---

## 11. 关键设计决策与约束

### 决策一：Pipeline 不长驻内存
**Why**：避免大量并发任务的内存积压；强制所有状态经过 StateStore 持久化，使服务重启透明。
**Trade-off**：每次请求都有 `load_state` / `save_state` 的文件 I/O 开销，但对于分钟级的 AI 任务可以忽略不计。

### 决策二：单一 `skilldev.respond` 确认入口
**Why**：前端不需要知道当前处于哪个挂起点，只需将用户的决策数据（`action` + 附加字段）发给后端，后端自动根据 `task_id` 当前阶段路由。
**扩展影响**：新增挂起点时，前端代码无需修改，只需在 `SUSPENSION_POINTS` 中注册新的 `SuspensionConfig`。

### 决策三：后端驱动 UI 状态
**Why**：Todo 列表、弹框内容、产物列表等 UI 状态全部由后端事件携带，前端纯渲染，避免前后端状态同步问题。
**实现**：`compute_todos()` 是 Todo 状态的唯一计算来源；`CONFIRM_REQUEST` 事件携带弹框的完整描述（标题、描述、按钮列表、展示数据）。

### 决策四：每阶段独立 Agent
**Why**：工具隔离、Prompt 隔离、内存隔离。
**实现**：`SkillDevContext.create_stage_agent()` 基于 `create_deep_agent()` 构造阶段 Agent，并按 `STAGE_TOOL_WHITELIST` 注册工具（见 `context.py`）。

### 决策五：工作区路径统一
**Why**：SkillDev 任务目录集中在可配置的工作区根下，便于备份与多实例扩展。
**约定**：`get_workspace_dir() / "skilldev" / {task_id}`（见 `interface._get_skilldev_service` 与 `WorkspaceProvider`）。

---

## 12. 扩展点与后续工作

| 项目 | 位置 | 说明 |
|---|---|---|
| `SkillDevTaskMode` 自动推断 | `service.py` / `init_stage.py` | 可按工作区内容与 params 设置 `state.mode`，与 Todo 分组过滤对齐 |
| `sysop_config` 注入 | `interface.py:_get_skilldev_service()` | 当前为 `None`；若需严格文件权限，可从全局 Agent 配置构造 `SysOperationCard` |
| 远程存储同步 | `workspace.py:sync_to_remote()` | 多实例时在工作区与对象存储/NFS 间同步 |
| StateStore 替换 | `store.py` | 本地 JSON 之外可实现 Redis 等，保持接口不变 |
| 前端 params 对齐 | `web_skilldev` 等 | 启动请求宜与 §6.2 键名一致，避免仅发送 `tools`/`resources` 字符串而后端未消费 |
