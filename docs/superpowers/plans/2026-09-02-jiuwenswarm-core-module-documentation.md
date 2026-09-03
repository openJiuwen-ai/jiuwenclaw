# JiuwenSwarm Core Module Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 归档覆盖 285 个核心 Python 源文件、可从设计结论跳转源码的中文模块设计说明书与接口说明文档。

**Architecture:** 文档按“总览入口—模块设计分册—接口参考分册—全量文件索引”分层。人工阅读源码形成架构与调用链说明，AST 清单用于穷举接口和校验文件覆盖，最终通过链接检查与集合对账证明文档完整性。

**Tech Stack:** Python 3.11+ 源码、Markdown、Mermaid、Python `ast`、PowerShell、ripgrep。

**Spec:** `docs/superpowers/specs/2026-09-02-jiuwenswarm-core-module-documentation.md`

## Global Constraints

- 只修改 `docs/zh/architecture/jiuwenswarm-core/` 及本计划/需求文件。
- 不修改运行时代码、测试、配置或用户已有的未提交文件。
- 以 Git 可见 `.py` 源文件为范围，排除缓存与生成物。
- 所有源码引用使用相对 Markdown 链接并可实际解析。
- 事实来自当前源码；推断必须明确标记为推断。
- 不创建提交，除非用户另行要求。

---

### Task 1: 建立源码基线与归档骨架

**Files:**
- Create: `docs/zh/architecture/jiuwenswarm-core/README.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/source-inventory.md`

**Interfaces:**
- Consumes: 需求文档中的目标路径与 285 文件验收基线。
- Produces: 后续分册使用的统一范围、术语、源码快照与导航结构。

- [ ] **Step 1: 生成去重后的目标文件集合**

```powershell
$all = rg --files jiuwenswarm | Where-Object { $_ -match '\.py$' }
$scope = $all | Where-Object { $_ -match '^jiuwenswarm[\\/](?:[^\\/]+\.py$|server[\\/]|common[\\/]|instance_manager[\\/])' } | Sort-Object -Unique
$scope.Count
```

Expected: `285`。

- [ ] **Step 2: 记录源码快照与分区数量**

在 `README.md` 中记录生成日期、Git commit、工作区脏状态说明、范围与排除项；在 `source-inventory.md` 中按目录列出每个文件。

- [ ] **Step 3: 建立最终文档导航**

入口必须链接到五份模块设计分册、四份接口参考分册和全量源码索引，并提供推荐阅读顺序。

- [ ] **Step 4: 核对骨架链接**

Run: 仅读取入口中的 `.md` 相对链接并检查目标路径存在。

Expected: 骨架阶段已创建的路径全部存在，待创建分册明确列于计划而不发布断链入口。

### Task 2: 编写根包、公共基础与实例管理设计分册

**Files:**
- Create: `docs/zh/architecture/jiuwenswarm-core/modules/01-bootstrap-common-instance.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/interfaces/01-root-common-instance-api.md`

**Interfaces:**
- Consumes: `jiuwenswarm/*.py`、`jiuwenswarm/common/**/*.py`、`jiuwenswarm/instance_manager/**/*.py`。
- Produces: 启动/补丁注入、配置与请求上下文、E2A、schema、secret/security、thinking、升级与实例锁的设计与 Python API 参考。

- [ ] **Step 1: 阅读实现并绘制启动与公共依赖链**

逐文件核对导入、模块级副作用、核心状态与调用方；设计分册包含 Mermaid 启动时序和公共能力依赖图。

- [ ] **Step 2: 编写分模块真实实现说明**

每一组文件说明职责、协作关系、关键不变量、失败路径、并发/持久化行为及源码链接。

- [ ] **Step 3: 编写接口参考**

按文件列出公开类/函数/方法/数据模型及准确签名；对关键接口补充参数、返回值、异常、副作用与调用约束。

- [ ] **Step 4: 对账 93 个文件**

Expected: 根包 10、`common` 77、`instance_manager` 6 全部在设计分册或接口分册中被引用，并全部进入总索引。

### Task 3: 编写服务入口、协议、分发与 Handler 设计分册

**Files:**
- Create: `docs/zh/architecture/jiuwenswarm-core/modules/02-server-entry-handlers.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/interfaces/02-server-protocol-api.md`

**Interfaces:**
- Consumes: `jiuwenswarm/server` 根文件、`gateway_push`、`handlers`、`hooks`、`sandbox`、`transports`、`utils`。
- Produces: HTTP/WebSocket 服务生命周期、wire 格式、op 分发、Handler 输入输出、推送与沙箱边界说明。

- [ ] **Step 1: 还原服务启动与请求分发链**

从应用入口追踪到 HTTP/WS 路由、上下文构造、op 注册、Handler 调用、流式发送与清理。

- [ ] **Step 2: 建立 Handler 操作矩阵**

为 15 个 Handler 文件列出注册操作、核心输入、响应/事件、状态修改、权限与主要失败结果。

- [ ] **Step 3: 编写协议和 Python 接口参考**

覆盖外部路径、WebSocket 操作、wire 编解码、传输抽象、Hook 执行与服务工具函数。

- [ ] **Step 4: 与注册表和路由声明交叉核对**

Expected: 文档中的每个外部入口能指向实际注册/路由源码；没有仅凭函数名猜测的协议。

### Task 4: 编写服务运行时设计与接口分册

**Files:**
- Create: `docs/zh/architecture/jiuwenswarm-core/modules/03-runtime-session-agent.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/modules/04-runtime-skill.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/modules/05-runtime-skill-turbo.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/interfaces/03-runtime-core-api.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/interfaces/04-skill-runtime-api.md`

**Interfaces:**
- Consumes: `jiuwenswarm/server/runtime/**/*.py` 的 149 个文件。
- Produces: Agent/tenant/session 生命周期、A2UI、调试、企业配置、普通 Skill 与 Skill Turbo 的架构和 API 参考。

- [ ] **Step 1: 还原 Runtime 生命周期和状态所有权**

说明 AgentManager、warm pool、tenant pool/catalog、session/project store、运行时 scope 与外层 server 的协作及清理顺序。

- [ ] **Step 2: 说明 Agent Adapter、A2UI、调试与企业配置**

记录协议转换、提示词/rail 注入、事件终结、追踪开关、配置装载/表达式求值及失败策略。

- [ ] **Step 3: 说明普通 Skill 运行时**

覆盖 CRUD、扫描/加载、元数据、依赖、白名单、Hub/Bundle、快照、搜索、进化、凭证与版本控制。

- [ ] **Step 4: 说明 Skill Turbo 执行图**

覆盖 planner、plan node、executor、权限桥、工具装载、artifact/ask rail、PPT 内置流水线节点、回退与恢复。

- [ ] **Step 5: 编写运行时接口参考并对账 149 个文件**

Expected: 所有运行时文件均有源码链接和符号说明；关键 async、上下文管理器、数据模型与注册接口带准确签名。

### Task 5: 汇总总体架构、验证完整性并归档

**Files:**
- Create: `docs/zh/architecture/jiuwenswarm-core/module-design.md`
- Create: `docs/zh/architecture/jiuwenswarm-core/interface-reference.md`
- Modify: `docs/zh/architecture/jiuwenswarm-core/README.md`
- Modify: `docs/zh/architecture/jiuwenswarm-core/source-inventory.md`

**Interfaces:**
- Consumes: Tasks 1–4 的全部分册。
- Produces: 单一归档入口、跨域总体架构、接口导航与可机器核验的源码覆盖表。

- [ ] **Step 1: 编写总体模块设计说明书**

整合系统边界、分层架构、启动/请求/会话/Agent/Skill 主链、关键数据所有权、并发模型、安全边界、扩展点与已知实现约束。

- [ ] **Step 2: 编写接口说明总览**

按外部协议、内部服务、数据模型、扩展/注册点和兼容补丁分类导航到详细接口分册。

- [ ] **Step 3: 生成并核对全量源码索引**

每个文件记录所属模块、主要职责、顶级符号和相对源码链接；空 `__init__.py` 也明确标注。

- [ ] **Step 4: 执行覆盖与链接验证**

Run: 比较目标源码集合与文档索引提取的源码链接集合，并逐一解析本地 Markdown 链接。

Expected: 源码 `missing=0`、`extra=0`；本地文件链接 `broken=0`。

- [ ] **Step 5: 执行内容抽样复核**

抽样至少覆盖 `app.py`、`start_services.py`、HTTP/WS 入口、`dispatch.py`、每个 Handler、`agent_manager.py`、`session_manager.py`、普通 Skill 执行入口、Skill Turbo planner/executor、secret store 与 instance lock。

Expected: 签名、调用方向、状态所有权和失败路径均与当前源码一致；发现差异时先修正文档再重新运行验证。
