# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Policy file reader for loading security policies from YAML files.

Shared by SandboxManager and ProxyManager to avoid duplicate policy loading logic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.policy import SecurityPolicy
from jiuwenbox.server.policy_engine import PolicyEngine

configure_logging()
logger = logging.getLogger(__name__)

JIUWENBOX_POLICY_PATH_ENV = "JIUWENBOX_POLICY_PATH"


class PolicyReader:
    """Reads security policy from YAML files."""

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        policy_path: Path | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.policy_path = (
            Path(policy_path)
            if policy_path is not None
            else self._resolve_policy_path()
        )

    @staticmethod
    def _resolve_policy_path() -> Path:
        env_path = os.environ.get(JIUWENBOX_POLICY_PATH_ENV)
        if env_path:
            return Path(env_path).expanduser()
        return Path(__file__).resolve().parents[3] / "configs" / "default-policy.yaml"

    def load_policy(self) -> SecurityPolicy:
        if self.policy_path.exists():
            return self.policy_engine.load_policy_from_file(self.policy_path)

        logger.warning(
            "Default policy file not found at %s, falling back to SecurityPolicy defaults",
            self.policy_path,
        )
        return SecurityPolicy()

    def load_policy_from_file(self, path: Path) -> SecurityPolicy:
        return self.policy_engine.load_policy_from_file(path)
