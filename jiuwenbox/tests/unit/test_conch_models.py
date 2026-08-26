# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Unit tests for Conch policy models and sandbox_runtime mapping.

These do not require a running jiuwenbox-server or conchd.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jiuwenbox.models.policy import ConchPolicy, SecurityPolicy
from jiuwenbox.models.sandbox import SandboxRef, local_now
from jiuwenbox.server.sandbox_manager import (
    RUNTIME_CONCH,
    RUNTIME_PROCESS,
    normalize_sandbox_runtime,
)
from jiuwenbox.server.runtime.errors import PolicyValidationError


def test_normalize_sandbox_runtime_aliases() -> None:
    assert normalize_sandbox_runtime(None) == RUNTIME_PROCESS
    assert normalize_sandbox_runtime("") == RUNTIME_PROCESS
    assert normalize_sandbox_runtime("bwrap") == RUNTIME_PROCESS
    assert normalize_sandbox_runtime("conch") == RUNTIME_CONCH
    with pytest.raises(PolicyValidationError, match="sandbox_runtime"):
        normalize_sandbox_runtime("docker")


def test_sandbox_ref_json_uses_sandbox_runtime_not_runtime() -> None:
    ref = SandboxRef(id="abc-1", sandbox_runtime="process")
    dumped = ref.model_dump(mode="json")
    assert dumped["sandbox_runtime"] == "process"
    assert "runtime" not in dumped


def test_local_now_is_timezone_aware() -> None:
    now = local_now()
    assert now.tzinfo is not None
    # Same clock as UTC converted to host local, not a naive wall clock.
    utc_local = datetime.now(timezone.utc).astimezone()
    assert abs((now - utc_local).total_seconds()) < 2


def test_conch_vcpu_max_requires_vcpu_num() -> None:
    with pytest.raises(ValidationError, match="vcpu_max requires conch.vcpu_num"):
        ConchPolicy(vcpu_max=4)


def test_conch_network_rejects_ipv6() -> None:
    with pytest.raises(ValidationError, match="IPv4"):
        ConchPolicy(network={"egress": {"blocked_ips": ["2001:db8::1"]}})


def test_default_policy_yaml_includes_conch_block() -> None:
    policy_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "jiuwenbox"
        / "configs"
        / "default-policy.yaml"
    )
    raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy = SecurityPolicy.model_validate(raw)
    dumped = policy.conch.model_dump(mode="json")
    assert dumped["template_id"] == ""
    assert dumped["network"]["egress"]["default"] == "allow"
    assert dumped["filesystem_policy"]["bind_mounts"] == []
