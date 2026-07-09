# 规格与文档对齐审查指引（spec）

> 对照 Aidlc `doc/<module>/` 文档与变更意图，判断**实现是否兑现需求、设计、计划与测试承诺**。

## 适用时机

- 已确定 `<module>` 且存在或应存在 `doc/<module>/` 文档
- Bug/Feature 返工：核对是否关闭 Leader 指定的 MF/SF
- 文档缺失：仍须审查，但在 `limitations` 登记「文档上下文缺口」

## 必读文档（按优先级）

| 文档 | 审查焦点 |
|------|----------|
| `requirements.md` | 功能范围、验收标准、边界、非目标 |
| `design.md` | 模块边界、接口契约、数据模型、约束 |
| `dev_plan.md` | 必要开发项是否落地（可用 `reviewer_plan_check.py` 查状态，**禁止改 checklist**） |
| `test_plan.md` | 关键/异常/边界路径是否有测试或等价验证 |

## 审查流程

```
1. 从 diff/Issue 归纳 change_intent
2. 在 requirements 中定位对应需求 ID/章节
3. 用 design 核对实现是否越界或违背契约
4. 对照 dev_plan / test_plan 未完成项 → 遗漏是否阻塞合并
5. 文档与代码不一致 → 判定责任方（代码 / 文档 / 需求待确认）
```

## 需求对齐（Requirements）

| 检查项 | Must Fix | Should Fix |
|--------|----------|------------|
| 验收标准 | 明确 AC 未满足或行为相反 | AC 满足但缺少可观测证据 |
| 范围 | 实现未要求功能或省略声明能力 | 范围灰区未在 §4.6/注释说明 |
| 边界输入 | requirements 列出的边界未处理 | 边界处理与文档描述措辞不一 |
| 非功能 | 声明的 SLA/兼容性/迁移未体现 | 文档未写但代码合理补全 → 建议回写文档 |
| Bug 修复 | 无回归测试且 test_plan 要求有 | 复现步骤与修复逻辑不对应 |

**可验证性**：每条 Must Fix 应能指向「哪条 AC / 哪段 requirements 原文」。

## 设计对齐（Design）

- API 形状、错误码、分页/限流是否与 `design.md` 一致
- 数据模型：字段、索引、迁移策略是否匹配
- 模块边界：是否引入 design 禁止的反向依赖或跨层调用
- 安全/性能：**设计层承诺**（如「必须鉴权」）未实现 → Must Fix，细节见 `security.md` / `performance.md`

## 层级对齐审查（Layer Alignment）

- requirements 若定位根因在 L2/L3，而实现仅改 L0（如 `_tool.py` 拼装）→ **Must Fix**
- design 声明基础设施改造，但 `dev_plan`/diff 未覆盖对应路径 → **Must Fix / REWORK**
- 测试只验证返回格式，不验证机制行为（超时、取消、进程清理等）→ **Must Fix**
- 对于 bench 目标为系统升级的场景，表层补丁默认标记 `leader_escalate: true`

## 计划对齐（dev_plan / test_plan）

- `dev_plan.md` 中与本 diff 相关的**必要项**仍为 `[ ]` → 通常 Must Fix 或 `gate_verdict: REWORK`
- `test_plan.md` 要求的路径无测试且无 Leader 书面豁免 → Must Fix
- 仅勾选计划但未改代码 → 在 findings 中点名「计划与 diff 不符」

查询示例（只读）：

```powershell
python scripts/reviewer_plan_check.py --module "<MODULE>" --repo-root "<REPO>" status --plan both --format json
```

## 文档 ↔ 代码不一致

按以下决策树落 verdict 与建议：

```
不一致
├── 代码明显错误、文档正确 → Must Fix 代码
├── 实现合理、文档过时 → Should Fix：更新文档（标 owner）；若阻塞验收 → leader_escalate
├── 需求/设计本身矛盾 → HOLD + 列出待 Leader/分析确认项
└── 无法判断（缺上下文）→ limitations + 保守 FAIL/HOLD
```

在 finding 中写清：**不一致点**、**证据**（文件:行 + 文档章节）、**建议动作**。

## 变更说明与可追溯性

- PR/提交说明是否能让后人**不读 diff** 仍懂「做什么、为什么」
- 反模式：「修 bug」「阶段一」「移动代码」—— 要求补充上下文
- Issue/需求 ID 是否在说明或 `review/result.json` 中可关联

## 返工轮（REWORK）附加项

- 核对 Leader **本轮必改 SF** 列表与 MF 关闭状态
- 修复是否仅文档/注释：若声称无需 regression，须**显式依据**；否则默认要求 tester 证据
- `DIFF_SCOPE` 是否覆盖所有声称已修项

## 与 `review/result.json` 的映射

| 情形 | `category` / 字段 |
|------|-------------------|
| 未满足 AC | `correctness` 或 `testing` |
| 违背 design 契约 | `maintainability` / `correctness` |
| 计划项遗漏 | `testing` 或 `maintainability` |
| 文档缺口 | `limitations` + `assumptions` |
| 需产品确认 | `gate_verdict: HOLD` |
| 层级错位（根因层级≠改动层级） | `layer_alignment: FAIL`，且 `gate_verdict` 不得 PASS |

`security_review` / 性能专篇**不替代**本维度的需求覆盖判断。

## 快速清单

```markdown
- [ ] change_intent 与 requirements 范围一致
- [ ] 验收标准可逐条对照，未满足项已分级
- [ ] 实现符合 design 边界与接口/数据契约
- [ ] dev_plan 必要项、test_plan 关键路径已覆盖或有豁免说明
- [ ] 文档与代码冲突已判定责任方
- [ ] 返工轮 MF/SF 关闭状态与证据已核对
```

## 信息不足时

- 在 `assumptions` 写明假设的 AC/设计 interpretation
- 在 `limitations` 列出缺失文档与建议补全路径
- 勿在无 spec 证据时 silent PASS；关键缺口用 `HOLD` 或 `FAIL`
