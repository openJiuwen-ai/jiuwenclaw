# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Context

Defines the GuardrailContext class that serves as the contract between
ConcreteGuardrail and GuardrailBackend. Inspired by AgentCallbackContext
but focused specifically on security detection scenarios.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from abc import ABC, abstractmethod

from openjiuwen.core.security.guardrail.enums import GuardrailContentType


@dataclass
class GuardrailContext:
    """Guardrail detection context.

    This is the contract between ConcreteGuardrail (event binding and
    data preprocessing) and GuardrailBackend (detection logic).

    Compared to AgentCallbackContext:
    - AgentCallbackContext: Contains full agent context (agent, session, etc.)
    - GuardrailContext: Focused only on data needed for security detection

    Attributes:
        content_type: Type of the content to check
        content: The actual content (type varies based on content_type)
        event: Original event name (e.g., "llm_invoke_input")
        metadata: Additional metadata specific to the event type
    """

    # ========== Core: Content to check ==========
    content_type: GuardrailContentType
    content: Any

    # ========== Event information ==========
    event: str

    # ========== Metadata (populated based on event type) ==========
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ========== Helper methods ==========

    def get_text(self) -> Optional[str]:
        """Get text content if content_type is TEXT.

        Returns:
            The text content, or None if not TEXT type
        """
        if self.content_type == GuardrailContentType.TEXT:
            return self.content
        return None

    def get_messages(self) -> Optional[List[Any]]:
        """Get message list if content_type is MESSAGES.

        Returns:
            The message list, or None if not MESSAGES type
        """
        if self.content_type == GuardrailContentType.MESSAGES:
            return self.content
        return None

    def get_tool_name(self) -> Optional[str]:
        """Get tool name if content_type is TOOL_CALL.

        Returns:
            The tool name from metadata, or None
        """
        return self.metadata.get("tool_name")


# ========== Model Output Parsers ==========



from openjiuwen.core.security.guardrail.enums import RiskLevel
from openjiuwen.core.security.guardrail.models import RiskAssessment


class ModelOutputParser(ABC):
    """Base class for model output parsers.

    Converts raw model output to RiskAssessment.
    Users can implement custom parsers by inheriting from this class.
    """

    @abstractmethod
    def parse(self, model_output: Any) -> RiskAssessment:
        """Parse model output to RiskAssessment.

        Args:
            model_output: Raw model output (format varies by model)

        Returns:
            RiskAssessment with risk level and details
        """
        pass


class BertBinaryParser(ModelOutputParser):
    """BERT binary classification model output parser.

    Output has two classes:
    - Class 0: SAFE (non-attack)
    - Class 1: ATTACK

    To reduce false positives, we use:
    1. Predicted class as primary indicator
    2. High confidence threshold for attack classification
    3. Conservative risk level mapping

    Risk level mapping (class=1, attack):
    - confidence < 0.7: SAFE (too uncertain, avoid false positive)
    - 0.7 <= confidence < 0.85: LOW
    - 0.85 <= confidence < 0.95: MEDIUM
    - confidence >= 0.95: HIGH

    When class=0 (safe), always return SAFE regardless of confidence.
    """

    DEFAULT_THRESHOLDS = {
        "low": 0.7,
        "medium": 0.85,
        "high": 0.95
    }

    def __init__(
        self,
        risk_type: str = "attack_detected",
        confidence_thresholds: Optional[Dict[str, float]] = None,
        attack_class_id: int = 1
    ):
        self.risk_type = risk_type
        self.thresholds = confidence_thresholds or self.DEFAULT_THRESHOLDS.copy()
        self.attack_class_id = attack_class_id

    def parse(self, model_output: Any) -> RiskAssessment:
        predicted_class, confidence = self._extract_prediction(model_output)
        risk_level = self._determine_risk_level(predicted_class, confidence)
        has_risk = risk_level != RiskLevel.SAFE

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=self.risk_type if has_risk else None,
            confidence=confidence,
            details={
                "predicted_class": predicted_class,
                "attack_confidence": confidence if predicted_class == self.attack_class_id else 1 - confidence
            }
        )

    def _extract_prediction(self, model_output: Any) -> tuple:
        if isinstance(model_output, dict):
            if "predicted_class" in model_output:
                predicted_class = int(model_output["predicted_class"])
                confidence = float(model_output.get("confidence", 0.0))
                return predicted_class, confidence
            if "label" in model_output:
                predicted_class = int(model_output["label"])
                confidence = float(model_output.get("score", model_output.get("confidence", 0.0)))
                return predicted_class, confidence
            if "probabilities" in model_output:
                probs = model_output["probabilities"]
                if isinstance(probs, (list, tuple)) and len(probs) >= 2:
                    predicted_class = 1 if probs[1] > probs[0] else 0
                    confidence = float(max(probs))
                    return predicted_class, confidence
            if "logits" in model_output:
                logits = model_output["logits"]
                if isinstance(logits, (list, tuple)) and len(logits) >= 2:
                    import math
                    exp_logits = [math.exp(x) for x in logits]
                    sum_exp = sum(exp_logits)
                    probs = [e / sum_exp for e in exp_logits] if sum_exp > 0 else [0.5, 0.5]
                    predicted_class = 1 if probs[1] > probs[0] else 0
                    confidence = float(max(probs))
                    return predicted_class, confidence
        if isinstance(model_output, (list, tuple)) and len(model_output) >= 2:
            predicted_class = 1 if model_output[1] > model_output[0] else 0
            confidence = float(max(model_output))
            return predicted_class, confidence
        if isinstance(model_output, (int, float)):
            confidence = float(model_output)
            predicted_class = self.attack_class_id if confidence > 0.5 else 0
            return predicted_class, confidence
        return 0, 0.0

    def _determine_risk_level(self, predicted_class: int, confidence: float) -> RiskLevel:
        if predicted_class != self.attack_class_id:
            return RiskLevel.SAFE
        if confidence < self.thresholds["low"]:
            return RiskLevel.SAFE
        if confidence < self.thresholds["medium"]:
            return RiskLevel.LOW
        if confidence < self.thresholds["high"]:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH


class QwenGuardParser(ModelOutputParser):
    """Qwen3Guard model output parser.

    Standard output format:
    '''
    Safety: Unsafe
    Categories: Violent
    '''

    Safety values: Safe, Unsafe, Controversial
    Categories: Violent, Sexual, Hate, Harassment, Self-Harm, etc.

    Risk level mapping:
    - Safe -> SAFE
    - Controversial -> MEDIUM
    - Unsafe -> HIGH
    """

    RISK_LEVEL_MAP = {
        "safe": RiskLevel.SAFE,
        "controversial": RiskLevel.MEDIUM,
        "unsafe": RiskLevel.HIGH,
    }

    def __init__(
        self,
        risk_type: str = "content_risk",
        default_risk_level: RiskLevel = RiskLevel.SAFE
    ):
        self.risk_type = risk_type
        self.default_risk_level = default_risk_level

    def parse(self, model_output: Any) -> RiskAssessment:
        if isinstance(model_output, dict):
            return self._parse_dict(model_output)

        text = str(model_output) if model_output is not None else ""

        json_result = self._try_parse_json(text)
        if json_result:
            return self._parse_dict(json_result)

        return self._parse_standard_format(text)

    def _try_parse_json(self, text: str) -> Optional[Dict]:
        import json
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return None

    def _parse_standard_format(self, text: str) -> RiskAssessment:
        safety_value = self._extract_safety(text)
        categories = self._extract_categories(text)

        risk_level = self._map_risk_level(safety_value)
        has_risk = risk_level != RiskLevel.SAFE

        risk_type = categories[0] if categories else self.risk_type

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=risk_type if has_risk else None,
            details={
                "safety": safety_value,
                "categories": categories,
                "raw_output": text
            }
        )

    def _extract_safety(self, text: str) -> str:
        import re
        lines = text.strip().split('\n')
        for line in lines:
            line_stripped = line.strip()
            if ':' in line_stripped:
                key, _, value = line_stripped.partition(':')
                key_lower = key.strip().lower()
                if key_lower == 'safety':
                    return value.strip()
        safety_pattern = re.compile(r'safety\s*:\s*(\w+)', re.IGNORECASE)
        match = safety_pattern.search(text)
        if match:
            return match.group(1)
        for keyword in ["unsafe", "controversial", "safe"]:
            if keyword in text.lower():
                return keyword
        return "unknown"

    def _extract_categories(self, text: str) -> List[str]:
        import re
        lines = text.strip().split('\n')
        for line in lines:
            line_stripped = line.strip()
            if ':' in line_stripped:
                key, _, value = line_stripped.partition(':')
                key_lower = key.strip().lower()
                if key_lower in ('categories', 'category'):
                    categories_str = value.strip()
                    if categories_str:
                        return [c.strip() for c in categories_str.replace(',', ' ').split() if c.strip()]
        categories_pattern = re.compile(r'categories?\s*:\s*(.+?)(?:\n|$)', re.IGNORECASE)
        match = categories_pattern.search(text)
        if match:
            categories_str = match.group(1).strip()
            if categories_str:
                return [c.strip() for c in categories_str.replace(',', ' ').split() if c.strip()]
        return []

    def _parse_dict(self, data: Dict) -> RiskAssessment:
        if "safety" in data:
            return self._parse_dict_standard_format(data)
        if "analysis" in data:
            return self._parse_full_format(data)
        if "judgment" in data:
            return self._parse_simple_format(data)
        if "severity_level" in data:
            return self._parse_api_format(data)
        return self._parse_generic_dict(data)

    def _parse_dict_standard_format(self, data: Dict) -> RiskAssessment:
        safety = data.get("safety", "unknown")
        categories = data.get("categories", [])
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.replace(',', ' ').split() if c.strip()]

        risk_level = self._map_risk_level(safety)
        has_risk = risk_level != RiskLevel.SAFE

        risk_type = categories[0] if categories else self.risk_type

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=risk_type if has_risk else None,
            details={
                "safety": safety,
                "categories": categories,
            }
        )

    def _parse_full_format(self, data: Dict) -> RiskAssessment:
        analysis = data.get("analysis", {})
        risk_level_str = analysis.get("risk_level", "safe")
        risk_level = self._map_risk_level(risk_level_str)
        has_risk = risk_level != RiskLevel.SAFE

        risk_categories = analysis.get("risk_categories", [])
        evidence = analysis.get("evidence", "")
        language = analysis.get("language", "unknown")
        decision = data.get("decision", "unknown")

        risk_type = risk_categories[0] if risk_categories else self.risk_type

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=risk_type if has_risk else None,
            details={
                "risk_categories": risk_categories,
                "evidence": evidence,
                "language": language,
                "decision": decision,
                "version": data.get("version", "unknown"),
            }
        )

    def _parse_simple_format(self, data: Dict) -> RiskAssessment:
        judgment = data.get("judgment", "Safe")
        risk_level = self._map_risk_level(judgment)
        has_risk = risk_level != RiskLevel.SAFE

        reason = data.get("reason", "")
        language = data.get("language", "unknown")

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=self.risk_type if has_risk else None,
            details={
                "reason": reason,
                "language": language,
                "judgment": judgment,
            }
        )

    def _parse_api_format(self, data: Dict) -> RiskAssessment:
        severity = data.get("severity_level", "safe")
        risk_level = self._map_risk_level(severity)
        has_risk = risk_level != RiskLevel.SAFE

        reason = data.get("reason", "")
        language = data.get("language", "unknown")

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=self.risk_type if has_risk else None,
            details={
                "reason": reason,
                "language": language,
                "severity_level": severity,
            }
        )

    def _parse_generic_dict(self, data: Dict) -> RiskAssessment:
        risk_level_str = data.get("risk_level", data.get("level", "safe"))
        risk_level = self._map_risk_level(risk_level_str)
        has_risk = risk_level != RiskLevel.SAFE

        if isinstance(has_risk, str):
            has_risk = has_risk.lower() in ("true", "yes", "1")

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=risk_level,
            risk_type=data.get("risk_type", self.risk_type) if has_risk else None,
            details=data
        )

    def _map_risk_level(self, level_str: str) -> RiskLevel:
        level_lower = level_str.lower().strip()
        if level_lower in self.RISK_LEVEL_MAP:
            return self.RISK_LEVEL_MAP[level_lower]
        sorted_keywords = sorted(self.RISK_LEVEL_MAP.keys(), key=len, reverse=True)
        for keyword in sorted_keywords:
            if keyword in level_lower:
                return self.RISK_LEVEL_MAP[keyword]
        return self.default_risk_level
