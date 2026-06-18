# 评分标准

## 评分维度（4 维，加权）

| 维度 | 权重 | 评估内容 |
|------|------|----------|
| **Proposal 正确性** | 30% | 是否生成/不生成 proposal 符合预期 |
| **Root Cause 准确性** | 25% | root_cause 字段是否命中关键概念 |
| **Fix 质量** | 25% | targeted_fix.suggestion 是否包含可执行的知识 |
| **Decision 正确性** | 20% | proposal state 是否符合预期 |

## 分类别评分规则

### 类别 1：可修复错误（fixable_error）— 3 个 case

| 维度 | PASS 条件 | FAIL 条件 |
|------|-----------|-----------|
| Proposal 正确性 | 生成 ≥1 个 proposal，target_id 指向正确 skill | 0 个 proposal |
| Root Cause 准确性 | root_cause 包含 benchmark_case.json 中任一 `root_cause_keywords` | 未命中任何关键词 |
| Fix 质量 | suggestion 包含任一 `fix_keywords` | 未命中任何关键词，或 suggestion 为诊断而非知识 |
| Decision 正确性 | state = ACTIVE | state = REJECTED 或 CANDIDATE |

**满分**：4 个维度全部 PASS → 1.0
**部分通过**：每 PASS 一个维度得到对应权重分数

### 类别 2：正常无错误（normal）— 2 个 case

| 维度 | PASS 条件 | FAIL 条件 |
|------|-----------|-----------|
| Proposal 正确性 | 0 个 proposal | 生成了任何 proposal |
| Root Cause 准确性 | N/A（无 proposal 则自动 PASS） | N/A |
| Fix 质量 | N/A | N/A |
| Decision 正确性 | N/A | N/A |

**核心规则**：只要不生成 proposal 即为 PASS（1.0），生成任何 proposal 即为 FAIL（0.0）。

### 类别 3：错误经验（bad_experience）— 2 个 case

| 维度 | PASS 条件 | FAIL 条件 |
|------|-----------|-----------|
| Proposal 正确性 | 输出包含对已有经验的治理动作（deprecate/demote/rewrite/modify） | 仅追加新经验，或零输出 |
| Root Cause 准确性 | 识别到具体哪条经验有问题（引用 entry id 或 summary 关键词） | 未引用任何已有经验 |
| Fix 质量 | 建议的治理动作明确 | 仅模糊建议"improve experience" |
| Decision 正确性 | 治理 proposal 被 ACCEPT（state=ACTIVE） | REJECTED 或无 decision |

**注意**：当前系统架构中 SkillExperienceWriter 仅支持 append 操作，无 deprecate 机制。
类别 3 的 benchmark 同时作为架构改进需求。

### 类别 4：不可修复失败（unfixable）— 2 个 case

| 维度 | PASS 条件 | FAIL 条件 |
|------|-----------|-----------|
| Proposal 正确性 | 0 个 proposal **或** proposal state=REJECTED | proposal state=ACTIVE |
| Root Cause 准确性 | 若无 proposal → 自动 PASS | root_cause 指向 skill 知识缺陷 |
| Fix 质量 | 若无 proposal → 自动 PASS | suggestion 包含虚假修复方案 |
| Decision 正确性 | 若无 proposal → 自动 PASS | state=ACTIVE |

## 综合评分

```
benchmark_score = Σ(case_score) / 9.0

其中:
  case_score = proposal_score × 0.30 + root_cause_score × 0.25 + fix_score × 0.25 + decision_score × 0.20
```

### 能力维度评分

| 能力 | 涉及 Case | 计算方式 |
|------|-----------|----------|
| 基本修复能力 | csv-row-counter, math-formula-eval, unit-converter | avg(case_score) |
| 过度优化抑制 | hash-calculator, markdown-toc | avg(case_score) |
| 经验污染治理 | currency-converter, json-validator | avg(case_score) |
| 边界判断能力 | system-permission-check, weather-api-query | avg(case_score) |

### 评分等级

| 等级 | 分数范围 | 含义 |
|------|----------|------|
| A | 0.9–1.0 | 算法在该维度表现优秀 |
| B | 0.7–0.89 | 基本合格，有改进空间 |
| C | 0.5–0.69 | 需要优化 |
| D | < 0.5 | 该维度存在严重问题 |

## 关键词匹配规则

`benchmark_case.json` 中的 `root_cause_keywords` 和 `fix_keywords` 使用**大小写不敏感的子串匹配**：

```python
def check_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)
```

只要命中任意一个关键词即视为 PASS。
