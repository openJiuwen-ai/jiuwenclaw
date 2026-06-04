# Skill 自演进

Agent 系统普遍存在一个问题：能力定义一旦写好，就基本不会再变了。工具调用出错，记录一条日志；用户反馈说理解有误，下次还是同样的逻辑。能力的上限，从部署那天就已经固定了。

JiuwenSwarm 基于 **openJiuwen 自演进框架**，以 `SkillCallOperator` 算子统一管理所有 Skills 的读写与演进分发。在此基础上，系统内置了一套演进信号检测机制，持续监听执行过程和对话内容，将真实使用中遇到的问题转化为 Skills 的改进输入。

## 核心组件

### SkillCallOperator

SkillCallOperator 是 JiuwenSwarm 基于 openJiuwen 框架实现的 Operator 算子，负责 Skills 的统一管理。

作为 JiuwenSwarm 与 Skills 交互的核心入口，它承担以下职责：
- 读取 Skill 定义（SKILL.md）
- 执行 Skill 指令
- 自动加载 Skill 积累的演进经验

当系统检测到需要改进的地方，这些改进会先存入 `evolutions.json`，SkillCallOperator 会把它们合并后一起返回给 Agent。这意味着每次调用 Skill 时，都能获取到最新的演进经验。

### SkillOptimizer

SkillOptimizer 是 JiuwenSwarm 基于 openJiuwen 框架实现的 Optimizer 优化器，负责驱动整个 Skill 演进流程。

它的核心工作包括：
1. **接收信号**：从 SignalDetector 接收异常信号，理解当前 Skill 遇到了什么问题
2. **分析判断**：结合对话上下文，判断这个问题是否值得记录
3. **生成改进**：调用 LLM 生成具体的改进建议
4. **执行记录**：将生成的改进方案写入演进记录

当你使用 `/evolve` 命令时，背后就是 SkillOptimizer 在工作。

### SkillEvolutionManager

SkillEvolutionManager 是演进生命周期的核心管理者，负责协调各个阶段的演进工作：

- **信号扫描**：调用 SignalDetector 提取需要演进的事件
- **记录生成**：调用 LLM 将信号转化为可执行的改进方案
- **存储管理**：维护 `evolutions.json` 文件的读写
- **内容固化**：将待定演进记录合并到原始 SKILL.md

它衔接了 SignalDetector、SkillOptimizer 和 SkillCallOperator，形成完整的演进闭环。

### SignalDetector

SignalDetector 是演进信号的检测器，持续监听对话和执行结果中的异常。

它基于规则工作，不需要调用 LLM，因此响应速度快：
- 监听每一次工具执行的结果，捕捉错误关键词
- 捕捉用户的纠正反馈（如"不对"、"应该"等）
- 判断信号应该归到哪个 Skill 并关联上下文

---

## 识别哪些信号？

信号来源主要分两类：

### 执行异常

包括工具调用超时、接口返回报错、代码执行中的异常中断等。只要任务执行中出现明确的失败字样，系统会自动识别并将其归因到当前正在执行的 Skill 上。

检测关键词包括但不限于：
- 通用错误：`error`、`exception`、`failed`、`failure`、`timeout`
- 网络相关：`connection error`、`econnrefused`、`enoent`
- 其他：`permission denied`、`command not found`

### 用户纠错

当你说“不对”、“应该换个方式”、“你理解错了”这类话语时，系统不会将其当作普通对话略过，而是会识别为一次有效的负反馈。这类信号往往比报错日志更有价值——它直接点出了 Skill 在理解或处理逻辑上的偏差。

检测模式包括：
- 中文：`不对`、`不是这`、`错 了`、`应该 是`、`你搞错了`、`纠正一下`
- 英文：`that's wrong`、`you're wrong`、`should be`、`actually`

---

## 信号捕获之后做什么？

系统会全程追踪当前活跃的 Skill 模块，确保每个信号都能准确对应到具体的 Skill 文档。具体的改写逻辑如下：

### 异常案例 → 排障建议

执行失败的现场记录会被整理成具体的操作建议，补充进 Skills 的 `Troubleshooting`（已知问题与处理方式）部分。下次遇到相同场景，Skill 可以主动提示已知的风险点和应对方式。

```text
原始信号：
Tool 'weather-check' returned: Error: API timeout after 30s

演进为：
## Troubleshooting
- 遇到天气 API 超时错误时，优先检查网络连接，可考虑添加重试机制或降级策略。
```

### 纠错交互 → 示例补充

用户纠错的对话片段会作为新的 `Example`（正确用法示例）写入 Skills 文档，让后续的调用更容易理解用户的真实意图。

```text
原始信号：
User: 不对，我说的是查询上海不是北京

演进为：
## Examples
- 用户说"查询上海天气"时应调用上海的经纬度参数，而非默认北京
```

---

## 演进流程

```text
用户对话 / 工具执行
        │
        ▼
┌───────────────────┐
│  SignalDetector   │  监听并识别信号
│   检测执行异常     │
│   检测用户纠错     │
└────────┬──────────┘
         │
         ▼
┌─────────────────────────────┐
│    SkillEvolutionManager    │
│         .scan()            │  提取演进信号
└────────────┬───────────────┘
             │
             ▼
┌─────────────────────────────┐
│    SkillEvolutionManager    │
│       .generate()          │  LLM 生成演进记录
└────────────┬───────────────┘
             │
             ▼
┌─────────────────────────────┐
│      evolutions.json        │  写入待固化记录
│    (Skill 目录下)          │
└────────────┬───────────────┘
             │
             ▼ (可选)
┌─────────────────────────────┐
│         .solidify()         │  合并到 SKILL.md
└─────────────────────────────┘
```

---

## 演进文件

演进记录存储在每个 Skill 目录下的 `evolutions.json` 文件中：

```json
{
  "skill_id": "<skill_name>",
  "version": "1.0.0",
  "updated_at": "2024-01-15T10:30:00Z",
  "entries": [
    {
      "id": "ev_1234abcd",
      "source": "execution_failure",
      "timestamp": "2024-01-15T10:30:00Z",
      "context": "API timeout after 30s",
      "change": {
        "section": "Troubleshooting",
        "action": "append",
        "content": "## 常见问题\n- 遇到 API 超时错误时..."
      },
      "applied": false
    }
  ]
}
```

其中 `applied: false` 表示待固化状态，`applied: true` 表示已固化到 SKILL.md。
 
---

## 演进效果

这套机制让 Skills 不再是一次性的静态文档，而是随着真实使用持续迭代的活文档。不需要任何人工干预，智能体在日常运转过程中就完成了对自身的改进。

演进后的 Skill 在下次被调用时，会自动检查 Skill 目录下是否存在 `evolutions.json` 文件，存在时会自动加载演进经验的内容，从而：
- 主动提示已知的风险点和应对方式
- 更准确地理解用户的真实意图
- 持续优化自身的表现

---

## 如何使用

使用 Skill 自演进时，可以先按下面顺序判断自己要做什么：

- 想让系统在后台自动发现并沉淀经验：开启自动扫描。
- 想立即针对某个已有 Skill 生成经验：使用 `/evolve` 系列命令。
- 想让系统在缺少合适 Skill 时自动提出新 Skill 创建建议：开启 Skill 自动创建。

### 自演进配置开关

在配置页的 **自演进配置** 中，可以按需要开启：

- **自动检测可演进信号**：让系统在对话和工具执行后自动扫描失败、纠错等演进信号；对应配置 `evolution.auto_scan`，默认关闭。
- **自动建议创建新技能**：让系统在缺少合适 Skill 时提出新 Skill 创建建议；对应配置 `evolution.skill_create`，默认关闭。

如果通过配置文件管理，对应写法是：

```yaml
evolution:
  auto_scan: false
  skill_create: false
```

- 环境变量 `EVOLUTION_AUTO_SCAN` 会覆盖 `auto_scan`，`SKILL_CREATE` 会覆盖 `skill_create`。

![打开自演进自动检测](../assets/images/skill演进_自动检测开关.png)

### 自动演进

开启 **自动检测可演进信号** 后，系统会在工具执行和对话结束后自动检测演进信号。常见信号包括工具失败、执行报错、用户纠错和明确负反馈。

如果检测到有效信号，系统会为相关 Skill 生成演进经验。下次调用该 Skill 时，这些经验会被自动加载，帮助 Agent 避免重复错误。

![自动触发](../assets/images/skill演进_自动触发.png)

### 手动触发已有 Skill 演进

如果你刚遇到某个 Skill 的失败、偏差或需要补充的经验，可以直接输入：

```bash
/evolve <skill_name> [user_query]
```

例如：

```bash
/evolve xlsx 创建发票文件前需要向我确定具体要求
```

![手动触发](../assets/images/skill演进_手动触发.png)

在**规划模式**下，系统会优先扫描当前会话中的工具失败和用户纠错信号。如果没有检测到明确演进信号，可以在命令后补充 `user_query`，直接说明希望 Skill 改进什么。

在**集群模式**下，`/evolve <skill_name>` 必须带上演进意图，例如：

```bash
/evolve pptx 让团队报告导出失败时给出可恢复步骤
```

### 查看和整理演进经验

想查看某个 Skill 的详细经验库和评分，可以输入：

```bash
/evolve_list <skill_name> [--sort score]
```

例如：

```bash
/evolve_list xlsx --sort score
```

在**规划模式**下，无参数 `/evolve` 仍会返回当前可见 Skill 的待处理演进记录摘要；Team 模式不支持裸 `/evolve`，必须提供 Skill 名称和演进意图。因此日常查看经验库时，建议使用 `/evolve_list <skill_name>`。

如果某个 Skill 的经验库开始重复、过长或价值不清，可以让系统生成整理方案：

```bash
/evolve_simplify <skill_name> [user_intent]
```

例如：

```bash
/evolve_simplify xlsx 合并重复的导出失败经验
```

整理方案不会静默落盘。系统会弹出审批，确认后才执行，拒绝后会丢弃本次整理。

![查看和整理演进经验](../assets/images/skill演进_查看和整理经验.png)

### 重建 Skill 文档

当某个 Skill 积累了较多演进经验，希望把经验重新组织进 `SKILL.md` 时，可以输入：

```bash
/evolve_rebuild <skill_name> [user_intent]
```

例如：

```bash
/evolve_rebuild xlsx 增加环境缺少工具时的应对策略
```

这个命令会生成后续执行任务，并继续作为普通 Agent / Team 任务运行。它不是直接覆盖 `SKILL.md` 的快捷按钮，实际改动仍会通过任务执行和审批流程完成。

![重建SKILL](../assets/images/skill演进_重建.png)

### Skill 自动创建

已有 Skill 自演进解决的是“已有 Skill 怎么变好”。如果当前任务暴露出缺少合适 Skill 的问题，可以开启 Skill 自动创建：

```yaml
evolution:
  skill_create: true
```

开启后，系统会注册 `SkillCreateRail`。在集群模式中，会注册 `TeamSkillCreateRail`。当系统判断当前任务需要沉淀成新 Skill 时，会提出创建建议，并通过后续任务完成 Skill 创建。

注意：

- `skill_create` 默认关闭，适合在希望主动沉淀新能力时开启。
- 环境变量 `SKILL_CREATE=true` 会覆盖配置文件。

### 适用模式

| 模式 | 支持情况 |
|---|---|
| 规划模式 `agent.plan` | 支持已有 Skill 演进、经验查看、整理、重建和 Skill 自动创建。 |
| 集群模式 `team` | 支持团队 Skill 演进、经验查看、整理、重建；`/evolve <skill_name>` 必须带演进意图。 |
| Code 模式 / `agent.fast` | 不支持 `/evolve` 系列 Skill 自演进命令。 |

### 审批和状态

- `/evolve` 和 `/evolve_simplify` 生成变更后不会静默写入，会推送确认问题。
- 接受后，后端接受本次演进记录并写入或固化；拒绝后丢弃本次生成内容。
- Team 技能演进接受后会同步团队技能目录。
- 演进或审批未完成时，后续输入会先排队，等待演进完成后再发送。

### 如何管理演进经验

演进经验存储在 Skill 目录下的 `evolutions.json` 文件中。日常建议优先使用：

- Web 前端 **查看技能经验**：编辑经验内容 `change.content`，或删除整条经验后保存。
- `/evolve_list <skill_name>`：查看经验库和评分。
- `/evolve_simplify <skill_name> [user_intent]`：整理、合并、清理经验库。
- `/evolve_rebuild <skill_name> [user_intent]`：把经验重新组织进 `SKILL.md`。

不要修改 `change.content` 之外的字段，例如 `id`、`source`、`timestamp`、`context`、`section`、`action`、`target`、`relevant`、`applied`。这些字段由系统生成和维护。

**目录位置：**

```
~/.jiuwenswarm/workspace/agent/skills/<skill_name>/
├── SKILL.md           # Skill 源文档
├── evolutions.json    # 演进经验记录
└── ...
```

**演进记录示例：**

```json
{
  "entries": [
    {
      "id": "ev_1cdbc3a5",
      "source": "execution_failure",
      "timestamp": "2026-03-09T09:33:08Z",
      "context": "错误上下文",
      "change": {
        "section": "Troubleshooting",
        "action": "append",
        "content": "演进内容",
        "relevant": true
      },
      "applied": false
    }
  ]
}
```

前端保存后，下次对话会自动加载更新后的经验内容。
