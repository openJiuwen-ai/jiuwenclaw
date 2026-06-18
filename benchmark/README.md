# Skill Self-Evolution Benchmark

验证 JiuwenSwarm 自演进算法（LLMProposer → RulePolicy + EvalPolicy → SkillExperienceWriter）在四种核心场景下的行为正确性。

## 设计目标

| 能力维度 | 测试什么 | 通过条件 |
|----------|----------|----------|
| **基本修复能力** | 算法能否从失败 trace 中识别错误并生成有效经验 | 生成 proposal 且 state=ACTIVE |
| **过度优化抑制** | 算法是否对正确工作的 skill 保持沉默 | 零 proposal |
| **经验污染治理** | 算法能否识别并处置 evolutions.json 中的错误经验 | 输出包含 deprecate/demote/rewrite 动作 |
| **边界判断能力** | 算法能否区分"不可修复"的失败，不输出无效建议 | 零 proposal 或 state=REJECTED |

## 分类与 Skill 清单（9 个）

### 类别 1：可修复错误（Fixable Error）— 3 个

Skill 中存在人为植入的错误，导致任务失败，可通过新增 evolutions.json 条目修复。

| Skill | 植入错误 | 领域 |
|-------|----------|------|
| `csv-row-counter` | 无条件跳过首行当表头，无表头 CSV 少计 1 行 | 文件处理 |
| `math-formula-eval` | 示例 3⊕2=9+12+4=19，正确应为 25 | 数学运算 |
| `unit-converter` | 英里→公里转换因子写错（1.8 vs 1.60934） | 单位换算 |

**期望行为**：LLMProposer 分析失败 trace → 生成包含准确 root_cause 和 actionable fix 的 proposal → RulePolicy 通过 + EvalPolicy score ≥ 0.60 → state=ACTIVE → 写入 evolutions.json

### 类别 2：正常无错误（Normal）— 2 个

Skill 本身正确，任务正常完成。

| Skill | 功能 | 领域 |
|-------|------|------|
| `hash-calculator` | 计算文件哈希（MD5/SHA-1/256/512） | 安全校验 |
| `markdown-toc` | 解析 Markdown 生成层级目录 | 文本处理 |

**期望行为**：LLMProposer system prompt 明确写了 "If everything is correct, return `{"proposals": []}`"。期望零 proposal。

### 类别 3：错误经验（Bad Experience）— 2 个

evolutions.json 中记载了错误或过拟合的经验，误导后续执行。

| Skill | 错误经验 | usage_stats 负反馈率 |
|-------|----------|---------------------|
| `currency-converter` | 硬编码过时汇率 1 USD = 6.80 CNY | 53% (8/15) |
| `json-validator` | 强制使用 Draft-07，拒绝 Draft 2020-12 特性 | 67% (12/18) |

**期望行为**：算法识别高负反馈率经验 → 提出 deprecate/demote/rewrite（而非追加新经验）。
**已知架构缺口**：(1) SkillExperienceWriter 仅支持 append，无 deprecate 动作 (2) LLMProposer 无法读取 evolutions.json 的 usage_stats。此 benchmark 同时揭示这些设计缺陷。

### 类别 4：不可修复失败（Unfixable）— 2 个

任务因外部不可抗力失败，无法通过经验修复。

| Skill | 失败原因 | 不可修复原因 |
|-------|----------|------------|
| `weather-api-query` | 外部 API 返回 503 | 基础设施问题，非知识缺陷 |
| `system-permission-check` | psutil 未安装且无 pip 权限 | 环境/权限限制 |

**期望行为**：LLMProposer prompt 写了 "Do NOT report infrastructure issues" 和 "Things that can't be fixed by adding knowledge"。期望零 proposal；若生成 → Decision 应 REJECT。

## 文件结构

```
benchmark/
├── README.md                # 本文档
├── run_guide.md             # 人工操作手册（含 prompt 和评分表）
├── scoring_guide.md         # 评分标准详细说明
├── run_benchmark.py         # 一键自动化脚本
├── report/                  # 运行报告输出（gitignore）
├── test_data/               # 共享测试数据
│   ├── employees.csv
│   ├── sample_doc.md
│   ├── sample.bin
│   └── component_schema.json / component.json
└── skills/                  # 9 个 benchmark skill
    ├── csv-row-counter/
    │   ├── SKILL.md
    │   ├── scripts/count_rows.py
    │   └── benchmark_case.json
    ├── math-formula-eval/
    │   ├── SKILL.md
    │   └── benchmark_case.json
    ├── unit-converter/
    │   ├── SKILL.md
    │   ├── scripts/convert.py
    │   └── benchmark_case.json
    ├── currency-converter/
    │   ├── SKILL.md
    │   ├── evolutions.json         # 预置过时汇率经验
    │   ├── evolution/              # 渲染后的经验详情
    │   └── benchmark_case.json
    ├── json-validator/
    │   ├── SKILL.md
    │   ├── evolutions.json         # 预置过拟合经验
    │   ├── evolution/              # 渲染后的经验详情
    │   └── benchmark_case.json
    ├── hash-calculator/ ...
    ├── markdown-toc/ ...
    ├── system-permission-check/ ...
    └── weather-api-query/ ...
```

`benchmark_case.json` 字段说明：

| 字段 | 用途 |
|------|------|
| `category` | 所属类别：fixable_error / normal / bad_experience / unfixable |
| `test_task` | 用户会提出的任务描述 |
| `failure_mode` | 失败触发条件、症状、根因摘要 |
| `sample_trace_summary` | 模拟的对话 trace（用户消息、Agent 响应） |
| `expected_behavior` | 算法应表现的行为（是否生成 proposal、关键词、期望 state） |
| `scoring` | 四维评分标准及权重 |

## 运行方式

### 一键运行

```bash
# 前提：Agent 服务已启动，telemetry 已开启
python benchmark/run_benchmark.py

# 自定义参数
python benchmark/run_benchmark.py --host 127.0.0.1 --port 18092 --timeout 300

# 只跑对话生成 trace，不跑演进
python benchmark/run_benchmark.py --skip-evolve

# 只跑演进和评分（trace 已生成）
python benchmark/run_benchmark.py --skip-prompts

# 重置到演进前状态（清理 traces、evolutions、恢复 skill 原始文件）
python benchmark/run_benchmark.py --reset
```

脚本自动完成：setup（复制 skill + test_data + 配置权限）→ 发送 9 条 prompt → 调用 evolve pipeline → 读取 evolution.db 评分 → 输出报告到 `benchmark/report/`。

每次运行生成带时间戳的报告（`report_YYYYMMDD_HHMMSS.md` + `.json`），同时更新 `last_report.md` / `last_report.json` 指向最新结果。

### 演进前后对比工作流

```bash
# 1. 清理
python benchmark/run_benchmark.py --reset

# 2. 基线：对话 + 演进
python benchmark/run_benchmark.py
#    → report/report_XXX_1.md（演进前的 agent 表现 + 生成的经验）

# 3. 重启 agent（加载新经验）

# 4. 演进后：只跑对话，对比基线
python benchmark/run_benchmark.py --skip-evolve
#    → report/report_XXX_2.md（演进后的 agent 表现）

# 5. 手动对比两份报告
diff report/report_XXX_1.md report/report_XXX_2.md

# 6. 清理
python benchmark/run_benchmark.py --reset
```

### 手动逐条验证

详见 [run_guide.md](run_guide.md)。

## 评分汇总

详见 [scoring_guide.md](scoring_guide.md)。
