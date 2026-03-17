# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Enumerations

Defines enumeration types for guardrail risk levels.
"""

from enum import Enum


class RiskLevel(Enum):
    """Risk severity levels.

    Attributes:
        SAFE: No risk detected
        LOW: Low risk, may require monitoring
        MEDIUM: Medium risk, likely needs attention
        HIGH: High risk, should be blocked
        CRITICAL: Critical risk, must be blocked immediately
    """
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
