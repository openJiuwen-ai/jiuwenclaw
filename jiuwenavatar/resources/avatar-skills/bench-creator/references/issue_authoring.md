# Bench Issue 正文编写（防泄题）

bench Issue 是**评测题面**，读者是 `dev-analyzer` / `dev-coder` 等 Agent，须在 **bench-issue-N** 分支上自行读代码定位根因。  
正文应像**真实用户报 Bug**，而不是「带答案的 code review」。

通用模板与创建命令仍见 `skills/gitcode-repo` 的 `issue_guide.md`；本节为 **bench 专用加严规则**，冲突时以本节为准。

## 目标

| 要写清 | 不要写 |
|--------|--------|
| 用户/集成方可见的现象 | 该改哪几个函数、哪几行 |
| 可执行的复现步骤与判定标准 | 根因链条（「因为 A 没做 B 所以应 C」） |
| 实际结果 vs 期望结果（可测试） | upstream 已合入的修复 PR/MR 链接或编号 |
| 环境：bench 分支名、`module`、**粗粒度**路径（如 `.../evolution/`） | `parent_sha` / `fix_sha`、具体 commit 短 SHA |
| 日志/事件字段名（若为用户可见契约） | patch 片段、diff 行、`+/-` 对照 |

## 「初步分析」节 — 最小粗粒度（必填但宜短）

`issue_template.md` 中的 **「初步分析（事实与代码证据）」** 在 bench Issue 里**要保留**，但只做**最小、最基础、粗粒度**的归纳，帮助 reader 知道「从哪块系统、哪类观测入手」，**不能**替代他们自己读代码找根因。

**篇幅**：通常 **1–3 条短句**，每条一行；不写长段落。

### 允许写什么（粗粒度）

| 粒度 | 示例 |
|------|------|
| 子系统 / 模块 | 「与 harness 下 skill 自动演进、host 进度事件相关」 |
| 目录或单文件（无行号） | 「相关实现大致在 `.../rails/evolution/` 一带」 |
| 观测到的**事件/状态序列**（用户契约） | 「复现时 `_evolution_meta.stage` 出现 `generating_updates`，未见 `completed`」 |
| 场景边界（仍属现象） | 「多见于「有演进信号但未产出可保存记录」一类路径」 |
| 已跑过的**外部**验证 | 「对 `bench-issue-N` 跑过某类集成/单测，事件序列如上」 |

### 禁止写什么（细粒度 = 泄题）

| 禁止 | 示例 |
|------|------|
| 函数/方法名 | `run_evolution`、`_handle_evolution_from_signals` |
| 行号、commit SHA | 「约 471–479 行」「`d975739e`」 |
| 根因/缺失逻辑结论 | 「未检查返回值」「应在 X 判断 None」 |
| 修复动作 | 「应 emit completed」「补上 _emit_progress」 |
| 答案来源 | upstream 修复 PR #1466、patch、照抄提示 |

**原则**：初步分析 = **指路牌（哪条线、看什么现象）**，不是 **路线图（改哪行、怎么改）**。

### 反面示例（禁止，会帮 Agent 偷懒）

```markdown
## 初步分析（事实与代码证据）

1. 在 `run_evolution` 中，对每个 `skill_groups` 条目调用
   `await self._handle_evolution_from_signals(...)`，**未检查返回值**
   （`d975739e` 提交，`skill_evolution_rail.py` 约 471–479 行）。
2. `_handle_evolution_from_signals` 在开始时发出 `generating_updates`；
   当 `_stage_evolution_from_signals` 为 `None` 时直接 `return None`，
   **不再发出后续进度**（同文件约 775–787 行）。
3. upstream 已合入修复 PR #1466（bench 评测请勿直接照抄补丁）。
```

**为何禁止**：已给出函数名、行号、缺失逻辑、事件阶段名，并指向标准答案 PR——Agent 几乎无需分析即可照抄。

### 正面示例（可接受的粗粒度「初步分析」）

```markdown
## 初步分析（事实与代码证据）

1. 现象集中在 harness 的 skill 自动演进与 host 侧进度事件上报，实现大致在 `openjiuwen/harness/rails/evolution/` 相关代码。
2. 复现时 host 事件里 `_evolution_meta.stage` 可见 `generating_updates`，在同一轮分析结束前未再出现 `completed`。
3. 与「已识别到可归属 skill 的演进信号，但最终未形成可保存记录」这类场景同时出现（具体分支逻辑待读代码确认）。
```

仍**不写**具体函数名、行号、commit、upstream PR、应如何改代码。

问题描述 / 期望结果可另节写清用户可见现象（见上文「问题描述」示例），与初步分析分工：前者面向用户，后者面向「从哪读代码」的粗指向。

## 全文禁止出现的「泄题」内容

发布前全文搜索并删除（含同义改写）：

| 类别 | 示例（均禁止） |
|------|----------------|
| 定位提示 | 「在 `foo()` 中」「约 471–479 行」「未检查返回值」 |
| 修复暗示 | 「应 emit completed」「需判断 request is None」「补上 _emit_progress」 |
| 答案来源 | upstream / 主仓 **修复 PR** 链接、`#1466`、`fix_sha`、`parent_sha` |
| 元话术 | 「bench 请勿照抄」「参考已合入 PR」「标准答案」 |
| Diff 泄露 | PR patch、`+`/`-` 行、与修复一字不差的代码片段 |
| 测试剧透 | 新增单测函数名、断言里出现的精确字符串（若来自修复 PR） |

**允许**：bench 分支名（`bench-issue-N`）、`module`、子系统目录、**用户可见** API/事件字段（如 `_evolution_meta.stage` 作为**观测点**，不说明该改哪段实现）。

## 各节填写要点（bench）

| 节 | 要求 |
|----|------|
| 元信息 | `module` 必填；类型/优先级按影响面填 |
| 问题描述 | 现象 + 影响，不出现「应如何实现」 |
| 复现步骤 | 从用户/测试视角，可含 Mock 场景描述，不指定内部函数 |
| 实际/期望结果 | 可判定、可写测试；不写实现方案 |
| 环境信息 | `bench-issue-N` 分支；相关路径到**目录或文件名**即可，**无行号** |
| 初步分析 | **保留**；1–3 条**粗粒度**指向（子系统/目录/事件序列），无函数名/行号/根因/修复 PR |
| 关联信息 | **不得**链接 upstream 修复 PR；可写「本题来源：合入 PR（内部记录，勿写入 Issue）」仅留在 Agent 交付表，不进正文 |

## 从 upstream PR 取材时的内部/外部分工

| 信息 | Agent 内部（交付表、JSON，勿进 Issue） | Issue 正文 |
|------|--------------------------------------|------------|
| `parent_sha` / `fix_sha` | ✅ | ❌ |
| PR 文件列表与 patch | ✅ 用于写题面时脱敏 | ❌ 不得粘贴 |
| PR 标题 | 可改写为现象摘要 | ✅ 现象向标题，避免 `fix(...):` 式修复标题 |
| 修复涉及符号名 | ✅ | ❌ |

## Leader 内部评分附录（不进 Issue 正文）

用于 bench-runner 评测前置备注，仅供 Leader 内部记录，禁止写入用户可见 Issue：

- **期望改动层级**：L0/L1/L2/L3（可多选，标主层级）
- **机制关键点**：如超时保真、进程清理、双流时序、交互阻断
- **反模式补丁特征**：仅 `_tool.py` 拼装、仅字符串断言、无机制测试
- **建议标签**：`infra-bug` / `shell-io`（可选）

标题建议用 **`[Bug] <module>：<用户可见问题摘要>`**，不要直接用 upstream 的 `fix(scope): ...` 作为 Issue 标题。

## 发布前检查清单

- [ ] 正文无函数/行号/「未检查返回值」类定位
- [ ] 无 upstream 修复 PR/MR 链接或编号
- [ ] 无 `parent_sha` / `fix_sha` / commit 短 SHA
- [ ] 「初步分析」仅粗粒度（子系统/目录/观测），无函数名、行号、根因句、修复 PR
- [ ] 「关联信息」无修复 PR；模板注释与 `> 填写指引` 已删
- [ ] 期望结果不预设具体函数名或补丁结构

## 与 dev-analyzer 的关系

bench Issue 输入应迫使 analyzer **读 bench 分支代码** 自行建立 `requirements.md`。  
若 Issue 已写明根因与改法，评测失效。
