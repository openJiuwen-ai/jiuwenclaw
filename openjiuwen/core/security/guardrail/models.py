# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Data Models

Defines data structures and dataclasses for guardrail detection and risk assessment.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    Optional,
)

from openjiuwen.core.security.guardrail.enums import RiskLevel


@dataclass
class GuardrailResult:
    """Result of guardrail detection.

    Contains the detection outcome, risk level, and optional details.

    Attributes:
        is_safe: Whether the content passed the guardrail check.
        risk_level: The severity level of detected risk.
        risk_type: Optional identifier for the type of risk detected.
        details: Optional dictionary with detailed detection information.
        modified_data: Optional modified/sanitized version of the data.
    """

    is_safe: bool
    risk_level: RiskLevel
    risk_type: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    modified_data: Optional[Dict[str, Any]] = None

    @classmethod
    def pass_(cls, details: Optional[Dict[str, Any]] = None) -> "GuardrailResult":
        """Create a pass result.

        Args:
            details: Optional detection details.

        Returns:
            GuardrailResult with is_safe=True and risk_level=SAFE.
        """
        return cls(
            is_safe=True,
            risk_level=RiskLevel.SAFE,
            details=details
        )

    @classmethod
    def block(
            cls,
            risk_level: RiskLevel,
            risk_type: str,
            details: Optional[Dict[str, Any]] = None,
            modified_data: Optional[Dict[str, Any]] = None
    ) -> "GuardrailResult":
        """Create a block result.

        Args:
            risk_level: The severity level of the risk.
            risk_type: Identifier for the type of risk.
            details: Optional detection details.
            modified_data: Optional sanitized version of data.

        Returns:
            GuardrailResult with is_safe=False.
        """
        return cls(
            is_safe=False,
            risk_level=risk_level,
            risk_type=risk_type,
            details=details,
            modified_data=modified_data
        )


@dataclass
class RiskAssessment:
    """Result of risk analysis from a guardrail backend.

    This is the output format from detection backends, which will be
    converted to GuardrailResult by the guardrail framework.

    Attributes:
        has_risk: Whether risk was detected.
        risk_level: The severity level of detected risk.
        risk_type: Identifier for the type of risk.
        confidence: Confidence score (0.0 to 1.0).
        details: Detailed analysis information.
    """

    has_risk: bool
    risk_level: RiskLevel
    risk_type: Optional[str] = None
    confidence: float = 0.0
    details: Optional[Dict[str, Any]] = None
