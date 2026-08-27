# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host adapters that wire AgentGroup packages into OrganizationRuntime."""

from __future__ import annotations

__all__ = [
    "JiuwenExpertGroupCatalog",
]


def __getattr__(name: str):
    if name == "JiuwenExpertGroupCatalog":
        from jiuwenswarm.agents.harness.team.expert_org.catalog import JiuwenExpertGroupCatalog

        return JiuwenExpertGroupCatalog
    raise AttributeError(name)
