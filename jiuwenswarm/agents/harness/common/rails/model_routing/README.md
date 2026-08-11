# 模型路由 (Model Routing) — 代码结构文档

> 更新日期：2026-07-24（dolores 分支，classifier 外置 + 纯分数路由重构后）

## 目录结构总览

```
model_routing/
├── __init__.py              包入口，汇总公开 API + __all__
├── model_routing_rail.py    核心 Rail 类（DeepAgentRail 子类）
├── types.py                 数据类（TaskAnalysis / RoutingDecision / PriorModelCall）+ 上下文工具
├── capability.py            ModelCapability 数据类 + 能力表构建 + 厂商映射
├── classifier.py            分类器加载工具（mapper / exec 注入 / 分数查找）
├── routing.py               _decide_and_select 纯分数路由选择
├── privacy.py               隐私正则检测
├── stats.py                 持久化 token 统计 + Classifier 类型别名

resources/model_routing/       （包内模板，首次启动拷到用户目录）
├── classifier_mapper.json    分类/难度/分数表 + classifier.source 文本
├── model_capability_map.json 厂商前缀映射(22) + 模型分数覆盖(~70)
├── model_routing_privacy.json 隐私正则(6)

用户目录 ~/.jiuwenswarm/config/routing_state/   （运行时实际加载位置）
├── classifier_mapper.json     用户可自定义覆盖包内模板
├── model_capability_map.json  同上
├── model_routing_privacy.json 同上
├── model_routing_list.json    stats 持久化（自动生成，不要手动改）

tests/unit_tests/agentserver/
└── test_model_routing_rail.py  单元测试（33 个，覆盖路由/隐私/统计/OTel/热重载）

调用方（adapter 层）
├── interface_code.py   → JiuwenSwarmCodeAdapter._build_model_routing()
└── interface_deep.py   → JiuWenSwarmDeepAdapter._build_model_routing()
```

---

## 模块职责

### `model_routing_rail.py` — Rail 主体

**类** `ModelRoutingRail(DeepAgentRail)`，priority=95（早于 TaskPlanningRail(90))。

构造参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `capability_table` | `list[ModelCapability]` | `[]` | 可选模型能力表 |
| `classifier` | `Any` | `None` | 异步分类器函数 `async (prompt_text) → (raw_score, category, difficulty)` |
| `mapper` | `dict` | `{}` | 分数表 + 默认分（由 `load_mapper_config()` 加载） |
| `stats` | `_ModelUsageStats` | 单例 | token 统计存储 |
| `stats_path` | `str` | `None` | 统计文件路径 |
| `apply_routing` | `bool` | `False` | 是否真切换模型（`set_llm`） |
| `privacy_check` | `bool` | `False` | 是否启用隐私检测 |

生命周期钩子：

- **`before_invoke(ctx)`** — 重置 trace_id + 前置调用链
- **`before_model_call(ctx)`** — 核心路由逻辑（四段式）
- **`after_model_call(ctx)`** — 累积前置调用 + 持久化 token 统计

`before_model_call` 路由流程（四段式）：

```
1. 单模型跳过     → caps ≤ 1 → 直接输出唯一模型，不走分类器/隐私
2. 隐私分流       → privacy_check=True + _check_privacy 命中 → 选最强 trusted / 无 trusted → force_finish
3. 正常路由       → classifier → (raw_score, category, difficulty)
                  → task_score(category, difficulty, mapper) → target 分数
                  → _decide_and_select(target, caps, ctx, category, difficulty) → 约束 + 最近分数匹配
4. Hard 特长约束 → difficulty=="hard" → 优先选 expertise 匹配的模型；无匹配 → 全表兜底
```

`_emit_decision` 构造 `RoutingDecision` 写入 `ctx.extra["model_routing_decision"]`；
`apply_routing=True` 时额外调 `ctx.agent.set_llm()` + 同步 config 字段。

---

### `types.py` — 数据类 + 上下文工具

| 类 | 字段 | 说明 |
|----|------|------|
| `PriorModelCall` | model, input_tokens, output_tokens, iteration, trace_id, span_id, start/end_time | 前置调用 → `to_otel_span()` 序列化 |
| `TaskAnalysis` | category, difficulty, **target_score**, predicted_input_tokens, agent_info | 任务分析结果 |
| `RoutingDecision` | recommended_model_id, analysis, reasoning, prior_calls_otel, model_usage_stats, privacy_hit | 完整路由决策 |

上下文工具函数（`_` 前缀，包内可用，不导出）：

- `_extract_prompt_text(messages)` — 取最后 user 消息，自动解包 TUI 信封
- `_unwrap_user_message(text)` — 解 `"你收到一条消息\n{json}"` → content 字段
- `_agent_model_name(ctx)` / `_extract_agent_info(ctx)` / `_get_session_id(ctx)` — 上下文信息提取
- `_new_trace_id()` / `_new_span_id()` — 随机 OTel ID

---

### `capability.py` — 模型能力表

**`ModelCapability`** 数据类（重构后字段）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `model_name` | `str` | — | 模型名（必填） |
| `max_length` | `int` | `65535` | 上下文窗口 |
| `model_group` | `str` | `"unknown"` | 品牌组（本地映射） |
| `model_provider` | `str` | `"unknown"` | 厂商（本地映射） |
| `model_expertise_category` | `list[str]` | `[]` | 专长标签 |
| `model_cost` | `int` | `0` | 相对成本 |
| `model_performance` | `int` | `0` | 基准得分 |
| `model_score` | `int` | `0` | **综合评分（路由核心）** |
| `is_trusted` | `bool` | `False` | 隐私可信 |
| `model_type` | `str` | `""` | 模型类型（`"vision"` / `""`普通） |
| `model_id` | `str|None` | `None` | client_id（token 统计 key） |
| `model` | `Any|None` | `None` | openjiuwen Model 实例（真切换用） |
| `token_used` | `dict` | `{}` | 持久化累积 token 用量 |

> **已删除字段**：`model_size`、`model_load`、`is_local`、`support_tool_call`、`support_vision`
> → 路由改为纯 `model_score` 分数匹配 + `model_type` 类型约束

关键函数：

- `build_capability_table_from_config(config, model_builder)` — 从 config.yaml 构建，含 vision 专用模型
- `_build_cap_from_entry(entry, model_builder)` — 单条目构建，合并 model_capability_map.json 覆盖
- `_capability_rank(cap)` — 排序值（model_score → model_performance → 0）
- `_map_model_group_provider(model_name)` — 子串映射 → `(group, provider)`
- `_ensure_user_copy(filename)` — 包内模板 → 用户目录（仅缺时拷）
- `_load_capability_map()` — 加载 model_capability_map.json（用户 > 包内兜底）

---

### `classifier.py` — 分类器加载 + 分数查找

> 本次重构核心变化：分类器从硬编码 `LLMClassifier` 类改为 **JSON 配置 + exec 文本注入**

公开函数（`__all__` 导出）：

| 函数 | 签名 | 说明 |
|------|------|------|
| `ensure_routing_state_files()` | `→ None` | 检查三个 JSON 文件缺失则补拷 |
| `load_mapper_config()` | `→ dict` | 加载 classifier_mapper.json → {categories, difficulties, score_table, default_score, classifier} |
| `load_classifier_impl(mapper)` | `→ (classify_fn, tag)` | exec 编译 mapper.classifier.source → async classify 函数 |
| `validate_score(raw)` | `→ int` | 0-100 校验，否则兜底 50 |
| `task_score(category, difficulty, mapper)` | `→ int` | score_table 查 → default_score 查 → 50 |

包内工具函数（source 文本可 import 引用）：

| 函数 | 说明 |
|------|------|
| `_build_llm_model(extras)` | 从 extras dict 构建 LLM Model（带缓存），classifier source 内可 `from .classifier import _build_llm_model` |
| `_parse_classifier_response(content, ...)` | 解析 LLM 输出 → (category, difficulty)，含 regex fallback |
| `_lookup_score(category, difficulty, ...)` | 查 score_table → default_score → 50 |

exec 注入机制：

```
classifier_mapper.json.classifier.source → 函数体文本
→ compile("async def classify(prompt_text):\n{source}")
→ exec(code, namespace, local_ns)
→ 得到 classify 函数对象

namespace 注入（仅数据，不注入函数）：
  _EXTRAS         classifier.extras dict
  _CATEGORIES     categories tuple
  _DIFFICULTIES   difficulties tuple
  _SCORE_TABLE    score_table {(cat,diff)→int}
  _DEFAULT_SCORE  default_score {diff→int}
  imports 列表    指定模块（如 re、json）

工具函数不自动注入——source 文本需要时自行 import：
  from jiuwenswarm.agents.harness.common.rails.model_routing.classifier import (
      _build_llm_model, _parse_classifier_response, _lookup_score
  )
```

---

### `routing.py` — 纯分数路由选择 + hard 特长约束

> 重构后：删除了 tool/agentic 过滤，只做分数最近匹配 + 类型约束 + hard expertise 约束

| 函数 | 说明 |
|------|------|
| `_has_image(ctx)` | 检测请求是否含图（message content `type=image/image_url` 或 `ctx.extra._multimodal_image_files`） |
| `_model_score(cap)` | `cap.model_score` → float，NaN/无效 → 0 |
| `_pick_closest_score(caps, target)` | 选 `|model_score - target|` 最小的 cap，等距偏高分 |
| `_decide_and_select(target_score, caps, ctx, category, difficulty, privacy_trusted_only)` | ① privacy trusted-only 过滤 ② **hard expertise 约束** ③ vision 类型约束 ④ 最近分数匹配 |

路由选择逻辑：

```
hard 难度特长约束（difficulty=="hard" 时）：
  → 候选限到 model_expertise_category 含 category 的模型（如 coding/hard → coding 专长模型）
  → 无特长匹配 → 保持原表兜底（不做空约束）

含图请求 → 候选限到 model_type=="vision"（无 vision 则保持原表兜底）
非含图   → 排除 model_type=="vision"（全 vision 则保持原表兜底）
→ |model_score - target_score| 最小 → 等距偏高分（质量优先）
→ reason 含 "expertise=coding" 标记（仅 hard+有匹配时）
```

---

### `privacy.py` — 隐私正则检测

- `_load_privacy_patterns()` — 加载 `model_routing_privacy.json`（用户 > 包内兜底），编译 regex
- `_check_privacy(text)` — 任一正则命中 → `True`
- 硬编码 fallback 6 条正则（credentials / 中国隐私词 / 身份证 / 手机号 / bearer token / email）

---

### `stats.py` — 持久化统计

`_ModelUsageStats` — 线程安全 JSON 持久化，原子写入（tmp → replace）。

| 方法 | 说明 |
|------|------|
| `record(model_id, model_name, tokens, *)` | 累积 per-model token + call_count |
| `persist_table(caps)` | 合并能力表快照 + 保留已删模型统计 |
| `snapshot()` | deep-copy 返回当前数据 |

持久化字段：`model_name, model_provider, model_group, is_trusted, model_type, max_length, model_performance, model_score, model_cost, model_expertise_category, token_used`

进程级单例：`get_stats_store(path)` / `reset_stats_store_for_test(path)`

`Classifier` 类型别名：`Callable[[str], Any]`（async classify: prompt_text → 3-tuple）

---

## JSON 配置文件

### `classifier_mapper.json`（分类/分数/分类器配置）

```json
{
  "categories": ["chat", "reasoning", "coding", "summarization", "format"],
  "difficulties": ["easy", "medium", "hard"],
  "score": {
    "chat.easy": 5, "chat.medium": 20, "chat.hard": 45,
    "reasoning.easy": 10, "reasoning.medium": 35, "reasoning.hard": 55,
    "coding.easy": 15, "coding.medium": 40, "coding.hard": 65,
    "summarization.easy": 5, "summarization.medium": 30, "summarization.hard": 50,
    "format.easy": 5, "format.medium": 20, "format.hard": 35
  },
  "classifier": {
    "imports": ["re", "json"],
    "source": "async def classify(prompt_text) 的函数体文本...",
    "extras": {
      "api_base": "...", "api_key": "...", "model_name": "...",
      "client_provider": "...", "temperature": 0, "system_prompt": "..."
    }
  }
}
```

- `score` 字段：`"category.difficulty"` → 整数，被 `load_mapper_config()` 解析为 `{(cat, diff)→int}` dict
- `classifier.source`：函数体文本，被 `load_classifier_impl()` exec 编译为 async classify 函数
- `classifier.extras`：注入 `_EXTRAS` namespace，source 内可引用

### `model_capability_map.json`（厂商映射 + 模型分数覆盖）

```json
{
  "vendor_map": [                    // 22 条前缀映射
    {"prefix": "glm", "group": "GLM", "provider": "zhipu"},
    {"prefix": "qwen", "group": "Qwen", "provider": "alibaba"},
    ...
  ],
  "models": {                        // ~70 条精确覆盖
    "GLM-5.1": {"model_score": 57},
    "deepseek-chat": {"model_score": 35},
    ...
  }
}
```

- `vendor_map`：model_name 子串首匹配 → `(group, provider)`
- `models`：model_name 精确匹配 → 覆盖 `model_score` 等能力字段

### `model_routing_privacy.json`（隐私正则配置）

```json
{
  "patterns": [
    {"label": "credentials", "regex": "(?:password|secret|token|api_key|apikey)..."},
    {"label": "chinese_privacy", "regex": "身份证|密码|银行卡..."},
    {"label": "chinese_id", "regex": "\\d{6}(?:19|20)\\d{2}(?:0[1-9]|1[0-2])..."},
    {"label": "phone", "regex": "1[3-9]\\d{9}"},
    {"label": "bearer_token", "regex": "Bearer [A-Za-z0-9._-]+"},
    {"label": "email", "regex": "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"}
  ]
}
```

---

## Adapter 调用方

`interface_code.py` / `interface_deep.py` 各有一个 `_build_model_routing()` 方法：

```
config["model_routing"] → enabled?
  → build_capability_table_from_config(config, model_builder)
  → ensure_routing_state_files()
  → load_mapper_config() → load_classifier_impl(mapper)  （失败则 classifier=None）
  → ModelRoutingRail(caps, classifier, mapper, apply_routing, stats_path, privacy_check)
```

两个 adapter 的逻辑完全相同，仅 logger 前缀和 `model_builder` 引用不同。

---

## 测试覆盖

`test_model_routing_rail.py` — 33 个测试：

| 类别 | 测试 | 数量 |
|------|------|------|
| 路由核心 | coding→big / format→small / medium→mid / classifier fallback / no classifier | 5 |
| 隐私分流 | trusted 优先 / 无 trusted 取消 / privacy_check=False 正常路由 / 多 trusted 选最强 | 4 |
| 模型切换 | apply=True set_llm / apply=False 不切 / privacy+apply / 无 trusted abandon | 4 |
| 统计持久化 | record+reload / token_used 合并 / persist 保留已删模型 / stashed cap client_id | 4 |
| OTel | span 字段 / trace 共享 / prior_calls 出现 | 3 |
| 能力表构建 | vendor+默认 / 空配置 / reload / client_id 区分(3场景) / explicit override | 6 |
| 视觉约束 | 含图 → vision 模型 | 1 |
| 单/空模型 | 单模型跳过 / 空表跳过 | 2 |
| 厂商映射 | _map_model_group_provider | 1 |
| persist 字段 | 能力字段 + expertise + model_type | 1 |
| 统计合并 | reload 后 token_used 重新合并 | 1 |

---

## 重构变化摘要（对比旧版）

| 维度 | 旧版 | 新版 |
|------|------|------|
| 分类器 | `LLMClassifier` 硬编码类，通过 agent._llm 调用 | JSON 配置 `classifier.source` + exec 注入，独立 async 函数 |
| 分数来源 | `_task_score(category, difficulty)` 硬编码映射 | `task_score(category, difficulty, mapper)` 从 mapper 动态查 |
| 路由算法 | category→label(Heavy/Medium/Light) + is_local 偏好 + tool/agentic 过滤 | 纯 `model_score` 最近匹配 + `model_type` 类型约束 + hard expertise 约束 |
| 模型区分 | `model_size` / `is_local` / `support_tool_call` / `support_vision` | `model_score` / `is_trusted` / `model_type` |
| 配置方式 | config.yaml `model_routing.classifier` 内联 api_base/api_key | `classifier_mapper.json` 外置 source 文本 + extras |
| 分数表 | Python 硬编码 dict | JSON `classifier_mapper.json` score 字段 |
| 隐私正则 | Python 硬编译 | JSON `model_routing_privacy.json` patterns |
| 厂商映射 | Python 硬编码 dict | JSON `model_capability_map.json` vendor_map |
| TaskAnalysis | `predicted_output_tokens` | `target_score` |
| Classifier 签名 | `Callable[[str, AgentCallbackContext], Any]` | `Callable[[str], Any]` |
