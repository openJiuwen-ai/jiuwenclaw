"""健康顾问安全边界 Rail。

检测高危关键词（胸痛、持续失眠、躯体疼痛、自杀/自伤想法等），
在每次模型调用前注入安全边界规则，检测到高危信号时注入紧急安全提示，
引导用户寻求专业医疗/心理帮助。
"""

from typing import Any

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


HIGH_RISK_KEYWORDS: list[str] = [
    "胸痛",
    "胸闷",
    "心绞痛",
    "chest pain",
    "持续失眠",
    "长期失眠",
    "彻夜不眠",
    "连续失眠",
    "躯体疼痛",
    "剧烈疼痛",
    "持续疼痛",
    "不明原因疼痛",
    "自杀",
    "自伤",
    "自残",
    "不想活",
    "不想活了",
    "想死",
    "了结自己",
    "结束生命",
    "活不下去",
    "suicide",
    "self-harm",
    "kill myself",
    "重度抑郁",
    "幻觉",
    "妄想",
]

SAFETY_BOUNDARY_CN = """## 健康顾问安全边界（强制执行，不可违反）

你是健康生活顾问，不是医生、营养师或心理咨询师。以下安全边界必须严格执行：

1. 禁止疾病诊断：不得根据症状诊断疾病、不得判病、不得开具药方、不得给出用药指导。用户描述身体不适时，引导就医。
2. 不替代专业人员：不替代营养师、医生、心理咨询师、精神科医生。
3. 保健品红线：不做保健品疗效承诺，不推荐保健品。
4. 方案原则：拒绝激进、高强度目标，坚持小步迭代、循序渐进。
5. 高危信号处理：检测到胸痛、持续失眠、躯体疼痛、自杀/自伤等关键词时，必须立即停止健康建议，优先输出安全提示，引导用户寻求专业医疗/心理帮助。提供紧急联系方式：心理援助热线 400-161-9995、急救 120。"""

SAFETY_BOUNDARY_EN = """## Health advisor safety boundary (mandatory)

You are a lifestyle advisor, not a doctor, dietitian, or therapist. Enforce these boundaries:

1. Do not diagnose, prescribe, or give medication guidance. Direct the user to professional care when they report physical symptoms.
2. Do not replace a dietitian, physician, counselor, or psychiatrist.
3. Do not claim supplement efficacy or recommend supplements.
4. Reject aggressive or high-intensity plans; keep changes small and iterative.
5. If chest pain, persistent insomnia, severe somatic pain, or self-harm language appears,
stop lifestyle advice and prioritize safety guidance. Provide emergency contacts appropriate to the user's locale."""

SECTION_BOUNDARY = "health_safety_boundary"
SECTION_EMERGENCY = "health_safety_emergency"


class SafetyGuardRail(DeepAgentRail):
    """健康顾问安全边界 Rail：注入安全规则，检测高危关键词，触发紧急提示。"""

    def __init__(self) -> None:
        super().__init__()
        self._agent: Any | None = None
        self._prompt_builder: Any | None = None
        self._matched: list[str] = []

    def init(self, agent: Any) -> None:
        """缓存运行时 agent 与 prompt builder，供钩子注入安全边界。"""
        self._agent = agent
        self._prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        builder = self._prompt_builder
        if builder is not None:
            builder.remove_section(SECTION_BOUNDARY)
            builder.remove_section(SECTION_EMERGENCY)
        self._agent = None
        self._prompt_builder = None
        self._matched = []

    @staticmethod
    def _detect_high_risk(text: str) -> list[str]:
        """检测文本中的高危关键词。"""
        if not text:
            return []
        text_lower = text.lower()
        return [kw for kw in HIGH_RISK_KEYWORDS if kw.lower() in text_lower]

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    texts.append(part)
            return " ".join(texts)
        return ""

    @staticmethod
    def _latest_user_text(ctx: AgentCallbackContext) -> str:
        """从 ctx.inputs 提取最近一条用户文本。"""
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return ""
        query = getattr(inputs, "query", None)
        if isinstance(query, str) and query.strip():
            return query
        messages = getattr(inputs, "messages", None) or []
        for msg in reversed(list(messages)):
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
            if role != "user":
                continue
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content")
            text = SafetyGuardRail._message_text(content)
            if text.strip():
                return text
        return ""

    def _inject_section(self, name: str, cn: str, en: str) -> None:
        builder = self._prompt_builder
        if builder is None:
            return
        builder.add_section(
            PromptSection(
                name=name,
                content={"cn": cn, "en": en},
            )
        )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """整轮开始时读取 query，记录本轮高危命中。"""
        self._matched = self._detect_high_risk(self._latest_user_text(ctx))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """注入安全边界；检测到高危关键词时注入紧急提示。"""
        self._inject_section(SECTION_BOUNDARY, SAFETY_BOUNDARY_CN, SAFETY_BOUNDARY_EN)
        matched = self._matched or self._detect_high_risk(self._latest_user_text(ctx))
        if not matched:
            builder = self._prompt_builder
            if builder is not None:
                builder.remove_section(SECTION_EMERGENCY)
            return
        joined = ", ".join(matched)
        emergency_cn = (
            "## 高危信号检测 — 紧急安全指令\n\n"
            f"检测到高危关键词：{joined}。\n\n"
            "你必须立即执行以下操作：\n"
            "1. 停止所有健康生活建议\n"
            "2. 优先输出安全提示，引导用户寻求专业医疗/心理帮助\n"
            "3. 提供紧急联系方式：心理援助热线 400-161-9995、急救 120\n"
            "4. 不得对症状进行任何诊断或分析\n"
            "5. 语气关怀但不恐慌，让用户感到被关注和支持\n"
        )
        emergency_en = (
            "## High-risk signal — emergency safety instruction\n\n"
            f"Detected high-risk keywords: {joined}.\n\n"
            "You must immediately:\n"
            "1. Stop all lifestyle advice\n"
            "2. Prioritize safety guidance and professional medical or mental-health help\n"
            "3. Share local emergency contacts when known; otherwise advise calling local emergency services\n"
            "4. Do not diagnose or analyze the symptoms\n"
            "5. Stay caring, not alarming\n"
        )
        self._inject_section(SECTION_EMERGENCY, emergency_cn, emergency_en)
