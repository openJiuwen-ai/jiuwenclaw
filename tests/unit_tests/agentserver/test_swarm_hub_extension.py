from __future__ import annotations

import pytest

from jiuwenswarm.extensions.registry import ExtensionRegistry
from jiuwenswarm.extensions.sdk.skill_source import (
    ArtifactDescriptor,
    ArtifactRef,
    DownloadPolicy,
    SkillRef,
    SkillSearchRequest,
    SkillSearchResult,
    SkillSourceProvider,
    SourceConfig,
)
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
from jiuwenswarm.server.runtime.skill.source_registry import SourceRegistry


class _CustomVerifyProvider(SkillSourceProvider):
    """覆盖 download_artifact 与 verify_artifact 的客户 Provider。"""

    source_id = "hub-c"
    provider_type = "customhub"
    display_name = "CustomHub"
    capabilities = frozenset({"search", "check_updates", "get_artifact"})

    def __init__(self) -> None:
        self.download_calls: list[tuple[ArtifactDescriptor, DownloadPolicy | None]] = []
        self.verify_calls: list[tuple[ArtifactDescriptor, bytes]] = []

    async def search(self, request: SkillSearchRequest, context) -> SkillSearchResult:
        return SkillSearchResult(items=())

    async def get_artifact(self, skill_ref: SkillRef, version_id: str, context) -> ArtifactDescriptor:
        return ArtifactDescriptor(
            artifact_ref=ArtifactRef(skill_ref=skill_ref, version_id=version_id),
            download_url="https://example.com/demo.zip",
        )

    async def download_artifact(self, descriptor, download_policy=None) -> bytes:
        self.download_calls.append((descriptor, download_policy))
        return b"zip-bytes"

    async def verify_artifact(self, descriptor, content):
        self.verify_calls.append((descriptor, content))
        return {"verified": True, "artifact_sha256": "custom-sha"}


@pytest.mark.asyncio


async def test_install_path_uses_provider_download_and_verify_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(ExtensionRegistry, "_instance", None)
    manager = SkillManager(workspace_dir=str(tmp_path))

    provider = _CustomVerifyProvider()
    registry = SourceRegistry()
    registry.register(
        SourceConfig(
            source_id="hub-c",
            provider_type="customhub",
            capabilities=frozenset({"get_artifact"}),
            download_policy=DownloadPolicy(allowed_hosts=("example.com",)),
        ),
        provider,
    )
    manager._source_registry = registry

    descriptor, body, verification = await manager._fetch_verified_source_artifact(
        source_id="hub-c",
        skill_id="demo",
        version_id="1.0.0",
        params={},
    )

    assert body == b"zip-bytes"
    assert descriptor.download_url == "https://example.com/demo.zip"
    # 下载经 Provider 覆写实现（平台默认下载被绕过），download_policy 透传
    assert len(provider.download_calls) == 1
    _, download_policy = provider.download_calls[0]
    assert download_policy is not None
    assert download_policy.allowed_hosts == ("example.com",)
    # 验签经 Provider 覆写实现：返回的审计 Mapping 直接入账（不再 to_audit_dict）
    assert verification == {"verified": True, "artifact_sha256": "custom-sha"}
    assert len(provider.verify_calls) == 1
    _, content = provider.verify_calls[0]
    assert content == b"zip-bytes"


