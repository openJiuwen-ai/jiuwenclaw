# Benchmark 操作手册

## 前置准备

```bash
BENCHMARK_DIR="benchmark"
SKILLS_DIR="~/.jiuwenswarm/agent/workspace/skills"
TEST_DATA_DIR="~/.jiuwenswarm/agent/workspace/benchmark_test_data"

# 1. 复制全部 9 个 skill 到运行时目录
for d in $BENCHMARK_DIR/skills/*/; do
  cp -r "$d" "$SKILLS_DIR/"
done

# 2. 复制测试数据到固定路径
cp -r "$BENCHMARK_DIR/test_data" "$TEST_DATA_DIR"
```

> **路径约定**：以下 prompt 中 `$TD` = `~/.jiuwenswarm/agent/workspace/benchmark_test_data`

每个 case 操作三步：**发对话 → 跑演进 → 看结果**。

---

## 类别一：可修复错误（3 个）

验证目标：算法能否从失败 trace 中识别错误并生成有效经验。

### Case 1: csv-row-counter

**发送对话：**
> 请帮我统计 $TD/employees.csv 有多少行数据。注意：这个文件没有表头行，第一行就是数据。

**观察 Agent 输出：**
- 脚本把首行当表头跳过，输出"数据行数: 99"（实际 100 行）
- 输出中出现"表头: 张三,28,工程师"——但用户已声明没有表头

**运行演进：**
```bash
jiuwenswarm-evolve run --latest 5
```

**评分（4 项，满分 1.0）：**

| 维度 | 权重 | PASS 条件 |
|------|------|-----------|
| Proposal 生成 | 30% | 生成了 ≥1 个 proposal，target_id 指向 csv-row-counter |
| Root Cause | 25% | 包含：`header`, `first row`, `skip`, `首行`, `表头`, `always`, `无条件`, `assume` |
| Fix 质量 | 25% | 包含：`no_header`, `条件判断`, `check`, `parameter`, `首行`, `headerless`, `without header`, `detect` |
| Decision | 20% | state = `ACTIVE` |

---

### Case 2: math-formula-eval

**发送对话：**
> 帮我算一下 3 ⊕ 2

**观察 Agent 输出：**
- SKILL.md 示例写 `3⊕2 = 9+12+4 = 19`，但 9+12+4 实际等于 25
- Agent 可能照抄示例输出 19（算术错），或自己算出 25（与示例矛盾）
- 两种情况 trace 中都有可检出的错误

**运行演进：**
```bash
jiuwenswarm-evolve run --latest 5
```

**评分（4 项，满分 1.0）：**

| 维度 | 权重 | PASS 条件 |
|------|------|-----------|
| Proposal 生成 | 30% | 生成了 1 个 proposal，target_id 指向 math-formula-eval |
| Root Cause | 25% | 包含：`example`, `示例`, `contradiction`, `矛盾`, `19`, `25`, `计算错误`, `wrong`, `incorrect`, `arithmetic` |
| Fix 质量 | 25% | 包含：`25`, `9+12+4=25`, `正确`, `correct`, `example`, `示例` |
| Decision | 20% | state = `ACTIVE` |

---

### Case 3: unit-converter

**发送对话：**
> 将 100 英里转换为公里

**观察 Agent 输出：**
- 应该看到 `100 mile = 180 km`（使用错误因子 1.8）
- 正确应为 ~160.9 km（因子 1.60934），偏差约 12%

**运行演进：**
```bash
jiuwenswarm-evolve run --latest 5
```

**评分（4 项，满分 1.0）：**

| 维度 | 权重 | PASS 条件 |
|------|------|-----------|
| Proposal 生成 | 30% | 生成了 1 个 proposal，target_id 指向 unit-converter |
| Root Cause | 25% | 包含：`mile`, `conversion`, `factor`, `英里`, `换算`, `1.8`, `1609`, `系数` |
| Fix 质量 | 25% | 包含：`1609.34`, `1.60934`, `1.609`, `correct`, `正确`, `mile` |
| Decision | 20% | state = `ACTIVE` |

---

## 类别二：正常无错误（2 个）

验证目标：算法是否对正确工作的 skill 保持沉默。

### Case 4: hash-calculator

**发送对话：**
> 计算 $TD/sample.bin 文件的 SHA-256 哈希值

**观察 Agent 输出：**
- 正确的 SHA-256 哈希值，无报错

**评分（二元制）：**

| PASS | FAIL |
|------|------|
| 零 proposal | 生成了任何 proposal |

---

### Case 5: markdown-toc

**发送对话：**
> 帮我为 $TD/sample_doc.md 生成一个目录

**观察 Agent 输出：**
- 正确的层级目录，带锚点链接

**评分（二元制）：**

| PASS | FAIL |
|------|------|
| 零 proposal | 生成了任何 proposal |

---

## 类别三：错误经验（2 个）

验证目标：算法能否识别 evolutions.json 中高负反馈率的错误经验并提出治理方案。

> **注意**：复制 skill 时确保 `evolutions.json` 一起复制——这是错误经验的载体。

### Case 6: currency-converter

**发送对话：**
> 把 500 美元换成人民币

**观察 Agent 输出：**
- Agent 使用实时 API 得到正确汇率（~6.77），输出正确结果
- 但 evolutions.json 中仍有一条建议 6.80 的过时经验在注入
- usage_stats 显示 53% 负反馈率（15 次呈现，8 次负反馈）

**评分（4 项，满分 1.0）：**

| 维度 | 权重 | PASS 条件 |
|------|------|-----------|
| 治理动作 | 30% | 输出中包含对已有经验 `ev_7a3f91b2` 的治理（非追加新经验） |
| Root Cause | 25% | 包含：`stale`, `outdated`, `过时`, `hardcoded`, `6.80`, `fallback`, `旧汇率`, `负反馈` |
| Fix 质量 | 25% | 包含：`deprecate`, `remove`, `废弃`, `删除`, `rewrite`, `改写`, `demote`, `降级` |
| Decision | 20% | 治理 proposal state = `ACTIVE` |

**架构缺口**：当前 LLMProposer 无法读取 evolutions.json 的 usage_stats，此 case 测试的是系统尚未具备的能力。

---

### Case 7: json-validator

**发送对话：**
> 用 $TD/component_schema.json 验证一下 $TD/component.json

**观察 Agent 输出：**
- schema 声明了 `$schema: Draft 2020-12`
- Agent 受精经验影响用 Draft-07 验证，报 `$dynamicRef is not a recognized keyword`
- 建议用户降级到 Draft-07——与 schema 声明矛盾

**评分（4 项，满分 1.0）：**

| 维度 | 权重 | PASS 条件 |
|------|------|-----------|
| 治理动作 | 30% | 输出中包含对 `ev_c4d8e2f1` 或 `ev_a1b2c3d4` 的治理 |
| Root Cause | 25% | 包含：`Draft-07`, `overfitting`, `过拟合`, `强制`, `降级`, `2020-12`, `dynamicRef` |
| Fix 质量 | 25% | 包含：`deprecate`, `remove`, `废弃`, `改写`, `尊重版本`, `自动检测` |
| Decision | 20% | 治理 proposal state = `ACTIVE` |

---

## 类别四：不可修复失败（2 个）

验证目标：算法能否区分基础设施/环境问题与知识缺陷。

### Case 8: system-permission-check

**发送对话：**
> 检查当前系统的 CPU 核心数、内存使用情况和进程列表

**观察 Agent 输出：**
- 脚本报 `psutil not installed`，尝试 pip install 被权限拒绝

**评分（二元制）：**

| PASS | FAIL |
|------|------|
| 零 proposal **或** proposal state = `REJECTED` | proposal state = `ACTIVE` |

---

### Case 9: weather-api-query

**发送对话：**
> 查询北京今天的天气

**观察 Agent 输出：**
- 脚本报 `ERROR: OpenWeatherMap API is unavailable (503)`

**评分（二元制）：**

| PASS | FAIL |
|------|------|
| 零 proposal **或** proposal state = `REJECTED` | proposal state = `ACTIVE` |

---

## 快速评分表

```
| # | Skill                  | 类别     | Proposal | Root Cause | Fix  | Decision | 得分  |
|---|------------------------|----------|----------|------------|------|----------|-------|
| 1 | csv-row-counter        | 可修复   | □ 0.30   | □ 0.25     | □ 0.25 | □ 0.20   |       |
| 2 | math-formula-eval      | 可修复   | □ 0.30   | □ 0.25     | □ 0.25 | □ 0.20   |       |
| 3 | unit-converter         | 可修复   | □ 0.30   | □ 0.25     | □ 0.25 | □ 0.20   |       |
| 4 | hash-calculator        | 正常     | □ 1.0    | —          | —      | —        |       |
| 5 | markdown-toc           | 正常     | □ 1.0    | —          | —      | —        |       |
| 6 | currency-converter     | 错误经验 | □ 0.30   | □ 0.25     | □ 0.25 | □ 0.20   |       |
| 7 | json-validator         | 错误经验 | □ 0.30   | □ 0.25     | □ 0.25 | □ 0.20   |       |
| 8 | system-permission-check| 不可修复 | □ 1.0    | —          | —      | —        |       |
| 9 | weather-api-query      | 不可修复 | □ 1.0    | —          | —      | —        |       |
|   |                        | 总计     |          |            |       |          | /9.0  |
```

## 能力维度汇总

| 能力 | 涉及 Case | 得分 | 等级 |
|------|-----------|------|------|
| 基本修复能力 | 1–3 平均分 | | A/B/C/D |
| 过度优化抑制 | 4–5 平均分 | | A/B/C/D |
| 经验污染治理 | 6–7 平均分 | | A/B/C/D |
| 边界判断能力 | 8–9 平均分 | | A/B/C/D |

等级标准：A (≥0.9) / B (0.7–0.89) / C (0.5–0.69) / D (<0.5)
