# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework

Security detection and interception framework for agent execution.
Integrates with the callback system to detect risks at critical execution points.
"""

# Enumerations
from openjiuwen.core.security.guardrail.enums import RiskLevel

# Data Models
from openjiuwen.core.security.guardrail.models import (
    GuardrailResult,
    RiskAssessment,
)

# Base Classes
from openjiuwen.core.security.guardrail.backends import GuardrailBackend
from openjiuwen.core.security.guardrail.guardrail import BaseGuardrail

# Builtin Guardrails
from openjiuwen.core.security.guardrail.builtin import (
    UserInputGuardrail,
)

# Exceptions
from openjiuwen.core.common.exception.errors import GuardrailError

__all__ = [
    # Enumerations
    "RiskLevel",
    # Data Models
    "GuardrailResult",
    "RiskAssessment",
    # Base Classes
    "GuardrailBackend",
    "BaseGuardrail",
    # Builtin Guardrails
    "UserInputGuardrail",
    # Exceptions
    "GuardrailError",
]
