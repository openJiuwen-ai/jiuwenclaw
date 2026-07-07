# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Proactive recommendation LLM prompt templates.

拆出 proactive_actions，便于独立维护话术——改 prompt 不碰逻辑代码。
"""

from __future__ import annotations

# ── 决策 prompt：画像更新 + 推荐决策 ──────────────────────────────

UNIFIED_ANALYSIS_PROMPT = """\
你是用户洞察与推荐助手。以下是用户的综合情境信息（含画像、对话摘要、历史推荐、待办、日程与候选 skill）。

{conversation_summary}

请完成以下任务，输出 JSON：

1. 更新用户画像（增量更新，只输出需要变更的字段，未变更的字段可以省略或保持原样）：

   ⚠️⚠️⚠️ 画像提取铁律（违反会严重污染用户画像，务必严格遵守）：

   【来源限制】画像**只能从对话摘要中 `[User]:` 开头的消息**提取，且必须是用户**字面明确陈述**的事实。
   - `[Assistant]:` 是系统/助手的回复（含主动推荐话术、skill 介绍、建议），**绝对不是用户意图**
   - 「候选 Skill」列表是系统安装的工具清单，**绝对不是用户兴趣**——禁止把 skill 名（如 gaode-taxi、taobao-shopping-assistant）当用户 interests
   - 「历史推荐记录」是系统推送过的推荐，**不是用户意图**
   - 日程事件只用于决策提醒，不算用户表达的 goals

   【禁止推理】禁止从用户行为/问题**推理/引申/联想**出画像：
   - 用户问天气 ≠ 用户要去旅游，禁止推断"旅游规划/行程/行李/打车/购物"为 goals/interests
   - 用户问代码 ≠ 用户要学 CI/CD，禁止推断
   - 只记录用户**字面说的**事实（如"我是Python开发者"→preferences；"明天要交房租"→commitments）
   - 用户没明确说的，一律不提取，宁可画像为空也不要推理填空

   【commitments 严格要求】必须是用户原话表达的"我要去做X"意图，禁止把 assistant 的建议/提议（如"要不要试试"）当用户承诺。

   【撤销意图】用户明确表示"不想X了""不要X了""取消了"时，必须从 goals/commitments/interests
   中删掉对应条目（在本次输出中不输出该条目即可）。用户改变主意后不应继续推已撤销的意图。

   【清理既有误提取】现有画像可能含历史误提取条目（从 assistant/skill/推荐推理的），
   发现无 `[User]:` 字面依据的，应在本次输出中删掉（不输出该条目即可）。

   字段说明：
   - preferences: 长期偏好（技术栈、工作习惯、沟通风格），只保留反复出现的模式或用户明确表达的偏好
   - goals: 用户明确说的当前短期任务，已完成或过期的删掉
   - interests: 用户主动提及想了解的方向（非推理）
   - commitments: 用户明确说"我要去做X"但还没做的

2. 推荐决策：根据画像、对话、日程和历史推荐记录，决定是否需要主动与用户对话。

   优先级（从高到低）：
   a. 用户明确表达未完成的事 → "task_reminder"
      另：若「即将到来的日程」中有近期事件（如会议、约会），可用 "task_reminder" 提醒，target 为事件标题。
   b. 场景化 skill 推荐：结合用户对话、画像和「即将到来的日程」，判断是否有
      已安装的候选 Skill 能帮用户应对当前或即将到来的场景。
      - 综合分析日程事件（标题/地点/时间）+ 候选 Skill 列表，判断哪个 skill 与场景相关
      - reason 必须说明场景依据（引用日程事件或 [User]: 对话内容）
      → "skill_recommend"
   c. 从用户明确表达的兴趣推理潜在方向 → "need_exploration"
      （target 是探索方向，非 skill 名；必须有用户明确表达的兴趣为依据，禁止凭空联想）

   ⚠️ 约束：
   - decision.type 为 "skill_recommend" 时，decision.target 必须是上方「候选 Skill」列表里实际存在的 skill 名称。
   - 若没有完全匹配的已存在 skill，改用 "need_exploration" 或返回 null，不要编造不存在的 skill。
   - 「候选 Skill」列表仅供 skill_recommend 选 target 用，不作为用户兴趣来源。
   - 「历史推荐记录」仅用于避免重复推荐同类内容，不作为用户意图来源。
   - 如当前无合适推荐，decision 返回 null。

输出 JSON 格式：
{{
  "preferences": ["..."],
  "goals": ["..."],
  "interests": ["..."],
  "commitments": ["..."],
  "decision": {{
    "type": "skill_recommend|task_reminder|need_exploration",
    "target": "skill名称/待办事项/探索方向",
    "reason": "推荐原因（引用 [User]: 对话内容或日程事件，禁止引用 [Assistant]: 或候选 skill）",
    "urgency": 0.0-1.0
  }} | null
}}"""


# ── 指令 prompt：把决策包成消息发给主 agent 生成话术 ─────────────

DIRECTIVE_PROMPT = """\
[主动推荐指令]
推荐类型：{rec_type}
推荐内容：{target}
推荐原因：{reason}

请基于以上信息，以助手身份自然地向用户发起这条推荐。要求：
- 2-3句话，口语化，不像广告
- 不要直接说"这是系统推荐"，自然融入对话
- 给出行动引导（如"要不要现在试试" / "需要我帮你开始吗"）
- 语气按推荐类型调整：
  · task_reminder：关切提醒，像贴心的助手
  · skill_recommend：从用户痛点切入，自然引出工具
  · need_exploration：像同事间的建议，让用户觉得这个方向有意思
输出纯文本话术，不要 JSON，不要标题。
"""
