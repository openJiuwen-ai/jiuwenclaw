# JiuwenSwarm 核心模块设计与接口文档归档

本目录归档 JiuwenSwarm 核心启动、公共基础设施、Agent Server、运行时、Skill 体系与多实例管理的实现级设计资料。文档面向维护者和二次开发者，描述的是当前源码真实行为，不是目标态方案。

## 文档快照

| 项目 | 值 |
| --- | --- |
| 归档完成日期 | 2026-09-03 |
| 基线提交 | `ecd8629a8567c0195282ad140435972461f7c8dd` |
| Python 源文件 | 285 个唯一文件 |
| 根包直接 `.py` | 10 |
| `server/**/*.py` | 192（含 15 个 `handlers` 文件） |
| `common/**/*.py` | 77 |
| `instance_manager/**/*.py` | 6 |
| 生成物 | 不含 `__pycache__`、`.pyc` 等缓存/构建产物 |

> 工作区另有用户未提交内容，但目标源码目录在取证时没有相对基线提交的修改。本归档自身是新增文档。

## 总览文档

1. [核心模块设计说明书](module-design.md)：跨模块架构、进程/请求/运行时所有权、并发、持久化、安全和扩展点。
2. [接口说明总览](interface-reference.md)：外部协议、HTTP/WS、Handler、Runtime、Skill 与兼容性语义。
3. [全量源码文件索引](source-inventory.md)：285 个文件的职责、主要符号和源码跳转入口。

## 模块设计分册

1. [根包、公共基础与实例管理](modules/01-bootstrap-common-instance.md)：93 个文件；包含逐文件实现记录。
2. [Agent Server 入口、协议与 Handler](modules/02-server-entry-handlers.md)：43 个非 Runtime Server 文件；包含 181 条声明式 REST 路由、7 条特殊路由和 96 个显式分派操作。
3. [Runtime、Session 与 Agent Adapter](modules/03-runtime-session-agent.md)：实例所有权、预热、会话、持久化、Adapter、A2UI、Debug Trace 和企业配置。
4. [Skill、SkillDev 与来源管理](modules/04-runtime-skill.md)：安装状态、来源、白名单、凭证、检索、进化和开发状态机。
5. [Skill Turbo 与内置 PPT 流水线](modules/05-runtime-skill-turbo.md)：计划生成、校验执行、权限/HITL、Artifact、恢复与 PPT 构建链。

## Python 接口明细

1. [根包、公共基础与实例管理 Python API](interfaces/01-root-common-instance-api.md)：93 个文件的 AST 级接口清单。
2. [Server 入口、协议与 Handler Python API](interfaces/02-server-protocol-api.md)：43 个非 Runtime Server 文件的接口清单。
3. [Server Runtime Core Python API](interfaces/03-runtime-core-api.md)：79 个 Runtime Core 文件的接口清单。
4. [Skill 与 Skill Turbo Runtime Python API](interfaces/04-skill-runtime-api.md)：70 个 Skill Runtime 文件的接口清单。

建议先读设计总览，再按分册理解生命周期与边界；需要精确签名、字段、行号时进入接口明细。四份接口明细合计覆盖 285 个唯一 Python 文件。

## 范围解释

- 用户列出的 `jiuwenswarm/server/handlers/` 是 `jiuwenswarm/server/` 的子集，因此文件总数只计一次，但设计说明会把 Handler 注册与操作矩阵作为独立专题。
- “所有文件”按 Python 模块设计任务的语义解释为目标路径中的 Git 可见 `.py` 源文件。缓存、字节码和运行时生成文件不代表可维护模块，不纳入设计/API 统计。
- 为解释调用链，设计分册可能引用 `gateway`、`agents`、配置文件或测试中的调用方；这些补充引用不会计入 285 文件覆盖基线。

## 阅读约定

- 文件链接使用仓库相对路径；在支持 Markdown 本地导航或 Git 仓库浏览的工具中可直接打开。
- `#L<number>` 指向取证快照中的源码行。源码后续变化可能导致行号漂移，但文件级链接仍然有效。
- 接口清单同时列出公开和内部符号。以下划线开头的类、函数或方法属于实现细节，不构成稳定兼容承诺。
- 设计分册会区分“源码已显式保证的行为”和“依据调用关系得到的推断”；未经证实的目标态不会写成当前事实。
