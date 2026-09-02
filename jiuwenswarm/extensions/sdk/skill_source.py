# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Public Skill Source SPI used by AgentServer extensions.

Providers translate a remote catalogue protocol into the common models below.
They deliberately do not install, update, or remove workspace files.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from jiuwenswarm.extensions.sdk.base import BaseExtension


@dataclass(frozen=True)
class SkillRef:
    source_id: str
    skill_id: str


@dataclass(frozen=True)
class ArtifactRef:
    skill_ref: SkillRef
    version_id: str


@dataclass(frozen=True)
class TrustPolicy:
    verification: str = "if-present"
    allowed_algorithms: frozenset[str] = frozenset({"HmacSHA256"})
    hmac_key_refs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: tuple[str, ...] = ()
    max_bytes: int = 64 * 1024 * 1024
    timeout_seconds: float = 60.0


class SecretResolver(ABC):
    """Resolve an approved secret reference without exposing it to Providers."""

    @abstractmethod
    def resolve(self, reference: str) -> str | None:
        raise NotImplementedError


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    provider_type: str
    enabled: bool = True
    priority: int = 0
    endpoint_ref: str | None = None
    auth_ref: str | None = None
    capabilities: frozenset[str] = frozenset()
    download_policy: DownloadPolicy | None = None
    trust_policy: TrustPolicy | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    provider_type: str
    display_name: str
    enabled: bool
    priority: int
    capabilities: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_id,
            "source_id": self.source_id,
            "provider_type": self.provider_type,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "priority": self.priority,
            "capabilities": sorted(self.capabilities),
        }


@dataclass(frozen=True)
class SkillSearchRequest:
    q: str = ""
    page: int = 1
    page_size: int = 20
    filters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillCandidate:
    source_id: str
    skill_id: str
    version_id: str
    name: str
    display_name: str | None = None
    summary: str | None = None
    version: str | None = None
    fingerprint: str | None = None
    namespace: str | None = None
    slug: str | None = None
    canonical_slug: str | None = None
    owner_display_name: str | None = None
    labels: tuple[Mapping[str, Any], ...] = ()
    homepage: str | None = None
    rating_avg: float | None = None
    rating_count: int | None = None
    download_count: int | None = None
    updated_at: datetime | str | int | None = None
    versions: tuple[Mapping[str, Any], ...] = ()
    previous_version: str | None = None
    accessible: bool | None = None
    downloadable: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        updated_at: str | int | None = self.updated_at
        if isinstance(updated_at, datetime):
            updated_at = updated_at.isoformat()
        raw: dict[str, Any] = {
            "source_id": self.source_id,
            "skill_id": self.skill_id,
            "version_id": self.version_id,
            "name": self.name,
            "display_name": self.display_name,
            "summary": self.summary,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "namespace": self.namespace,
            "slug": self.slug,
            "canonical_slug": self.canonical_slug,
            "owner_display_name": self.owner_display_name,
            "labels": list(self.labels),
            "homepage": self.homepage,
            "rating_avg": self.rating_avg,
            "rating_count": self.rating_count,
            "download_count": self.download_count,
            "updated_at": updated_at,
            "versions": list(self.versions),
            "previous_version": self.previous_version,
            "accessible": self.accessible,
            "downloadable": self.downloadable,
            "metadata": dict(self.metadata),
        }
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if value is None or value in ((), [], {}):
                continue
            result[key] = value
        return result


@dataclass(frozen=True)
class SkillSearchResult:
    items: tuple[SkillCandidate, ...]
    total: int | None = None
    next_page: int | None = None


@dataclass(frozen=True)
class InstalledArtifact:
    source_id: str
    skill_id: str
    version_id: str
    version: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True)
class SkillUpdateStatus:
    skill_ref: SkillRef
    current_version_id: str
    latest_version_id: str | None
    has_update: bool
    latest_version: str | None = None
    fingerprint_matched: bool | None = None
    accessible: bool | None = None
    downloadable: bool | None = None
    remote_status: str = "unknown"
    error_code: str | None = None


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_ref: ArtifactRef
    download_url: str
    expires_at: datetime | None = None
    checksum_sha256: str | None = None
    artifact_sha256: str | None = None
    signature: str | None = None
    signature_algorithm: str | None = None
    signature_encoding: str | None = None
    signature_scope: str | None = None
    key_id: str | None = None
    fingerprint: str | None = None
    content_length: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderInvocationContext:
    request_id: str
    trace_id: str | None = None
    authorization_scope: str | None = None
    credential_ref: str | None = None


class SkillSourceProvider(ABC):
    """Remote catalogue adapter. Workspace mutation is intentionally absent."""

    source_id: str
    provider_type: str
    display_name: str
    capabilities: frozenset[str]

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def search(
        self,
        request: SkillSearchRequest,
        context: ProviderInvocationContext,
    ) -> SkillSearchResult:
        raise NotImplementedError

    async def check_updates(
        self,
        installed: Sequence[InstalledArtifact],
        context: ProviderInvocationContext,
    ) -> Sequence[SkillUpdateStatus]:
        raise NotImplementedError

    @abstractmethod
    async def get_artifact(
        self,
        skill_ref: SkillRef,
        version_id: str,
        context: ProviderInvocationContext,
    ) -> ArtifactDescriptor:
        raise NotImplementedError


class SkillSourceExtension(BaseExtension, ABC):
    """Factory registered by a trusted extension package."""

    provider_type: str
    api_version: str = "2"

    @abstractmethod
    def create_provider(self, config: SourceConfig) -> SkillSourceProvider:
        raise NotImplementedError
