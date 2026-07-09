---
name: record-bugs
description: **Sub-skill** of `openjiuwen-qa-guideline` — append suspected bugs to `PENDING_BUGS.md`. **Read `openjiuwen-qa-guideline/SKILL.md` first** for when to route here (guideline **C 类**); usually after a product `openjiuwen-*` skill confirms evidence. When a bug is confirmed or the user asks to record one, load this skill and **run** `scripts/record_bug.py` — never only say「已记录」. Applies to agent-core, agent-runtime, deepsearch, agent-studio, agent-core-java, jiuwenswarm, etc.
---

# record-bugs

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（疑似问题记录），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，确认属于 **疑似问题记录（C 类）** 且（通常）已完成对应产品线 skill 的取证——除非用户已 `@openjiuwen-qa-guideline`，或用户仅要求登记且证据与 `--module` 已齐全。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 疑似问题记录（guideline **C 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **典型前置** | 某 `openjiuwen-*` 组件 skill（B 类）读快照后确认 Bug |
| **输出** | 项目根目录 `PENDING_BUGS.md`（状态：待确认） |

本技能**不负责**：组件答疑、社区统计、在 GitCode 创建 Issue/PR。

---

## 何时记录

- 已读代码/文档并有证据，**确认是 Bug**（非猜测、非设计争议）
- 用户明确要求记录

不记录：未验证的猜测、纯咨询、跨版本未声明的差异。

## 执行（必须跑命令或调用工具）

**优先方式**：调用 Agent 工具 **`record_suspected_bug`**（由 `RecordBugRail` 自动挂载，无需手动跑脚本）。

**备选方式**：从 **skills 集合根目录**（含 `record-bugs/` 与各 `openjiuwen-*` 包的目录）执行：

```bash
python record-bugs/scripts/record_bug.py \
  --title "简短标题" \
  --file "相对路径:行号" \
  --module "<模块名>" \
  --severity "中" \
  --desc "一句话描述" \
  --analysis "根因与证据"
```

可选：`--fix "修复建议"`；若 `PENDING_BUGS.md` 不在 git 根目录，加 `--root /path/to/project`。

**Windows** 可将 `python` 换为 `python3` 或 `py -3`，路径用 `\` 亦可。

### `--module` 取值

| 产品线 skill | `--module` |
|--------------|------------|
| openjiuwen-agent-core | `agent-core` |
| openjiuwen-agent-core-java | `agent-core-java` |
| openjiuwen-agent-studio | `agent-studio` |
| openjiuwen-agent-runtime | `agent-runtime` |
| openjiuwen-deepsearch | `deepsearch` |
| openjiuwen-jiuwenswarm | `jiuwenswarm` |
| 其他 | 与仓库/产品名一致的短标识 |

`--file` 写 **快照内相对路径**（如 `openjiuwen/foo.py:42`），不要写本地 `assets/vX.Y.Z/` 前缀。

### `--severity`

| 值 | 含义 |
|----|------|
| 高 | 崩溃、数据丢失、安全、阻塞主流程 |
| 中 | 功能错误但有绕行 |
| 低 | 边界、文案、轻微体验 |

## 记录后

1. 确认脚本 JSON 输出 `"success": true`
2. 告知用户：**已记录此问题，后续会找对应责任人确认并提交 Issue 跟踪。**

## 脚本行为

- 目标文件：`<jiuwenswarm-root>/PENDING_BUGS.md`（通常为 `~/.jiuwenswarm/PENDING_BUGS.md` 或源码仓库根目录）
- 不存在则自动创建模板；每条记录状态为 **待确认**
