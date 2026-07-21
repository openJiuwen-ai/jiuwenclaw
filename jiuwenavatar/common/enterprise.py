# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enterprise deployment helpers for multi-tenant JiuwenAvatar.

This module is intentionally small and dependency-light.  Standalone mode keeps
using the existing single-user workspace; enterprise mode binds request-scoped
tenant routing into ContextVars so existing path helpers can resolve isolated
workspaces without changing every call site at once.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TENANT_CONTEXT: ContextVar["TenantRuntimeContext | None"] = ContextVar(
    "jiuwenavatar_tenant_runtime_context",
    default=None,
)


@dataclass(frozen=True)
class TenantRuntimeContext:
    """Request-scoped routing and isolation identity."""

    service_id: str = ""
    agent_id: str = ""
    avatar_id: str = ""
    group_id: str = ""
    user_id: str = ""

    @property
    def has_identity(self) -> bool:
        return any((self.service_id, self.agent_id, self.avatar_id, self.group_id, self.user_id))


def is_enterprise_mode() -> bool:
    """Return whether enterprise/cloud runtime should be enabled."""

    deployment_mode = os.getenv("DEPLOYMENT_MODE", "").strip().lower()
    agent_deploy_mode = os.getenv("AGENT_SERVER_DEPLOY_MODE", "").strip().lower()
    explicit = os.getenv("JIUWENAVATAR_ENTERPRISE_MODE", "").strip().lower()
    return (
        deployment_mode in {"enterprise", "active-standby", "cloud", "saas"}
        or agent_deploy_mode in {"k8s", "kubernetes", "runtime_management"}
        or explicit in {"1", "true", "yes", "on"}
    )


def safe_segment(value: Any, *, default: str = "default", max_len: int = 96) -> str:
    """Normalize arbitrary tenant identifiers into a filesystem-safe segment."""

    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    if not cleaned:
        cleaned = default
    return cleaned[:max_len]


def make_service_id(group_id: str, avatar_id: str) -> str:
    """Stable enterprise service id aligned with the cloud design."""

    raw = f"{group_id or 'default'}::{avatar_id or 'default'}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def get_tenant_context() -> TenantRuntimeContext | None:
    return _TENANT_CONTEXT.get()


@contextmanager
def bind_tenant_context(context: TenantRuntimeContext | None) -> Iterator[None]:
    token = _TENANT_CONTEXT.set(context if context and context.has_identity else None)
    try:
        yield
    finally:
        _TENANT_CONTEXT.reset(token)


def tenant_workspace_root() -> Path:
    """Enterprise workspace root, defaulting under the normal data dir."""

    raw = os.getenv("JIUWENAVATAR_TENANT_WORKSPACE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "tenants"


def resolve_tenant_agent_root(context: TenantRuntimeContext | None = None) -> Path | None:
    """Return an isolated agent root when enterprise context is active."""

    ctx = context or get_tenant_context()
    if not is_enterprise_mode() or ctx is None or not ctx.has_identity:
        return None

    group = safe_segment(ctx.group_id or ctx.service_id or "default-group")
    user = safe_segment(ctx.user_id or ctx.agent_id or "default-user")
    avatar = safe_segment(ctx.avatar_id or ctx.service_id or "default-avatar")
    return tenant_workspace_root() / group / user / avatar / "agent"


def extract_routing(payload: Any) -> TenantRuntimeContext:
    """Extract enterprise routing fields from params/metadata/channel context."""

    data = payload if isinstance(payload, dict) else {}
    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    tenant = data.get("tenant") if isinstance(data.get("tenant"), dict) else {}

    avatar_id = str(data.get("avatar_id") or routing.get("avatar_id") or tenant.get("avatar_id") or "").strip()
    group_id = str(data.get("group_id") or routing.get("group_id") or tenant.get("group_id") or "").strip()
    user_id = str(data.get("user_id") or routing.get("user_id") or tenant.get("user_id") or "").strip()
    service_id = str(data.get("service_id") or routing.get("service_id") or "").strip()
    agent_id = str(data.get("agent_id") or routing.get("agent_id") or user_id or "").strip()

    if not service_id and avatar_id:
        service_id = make_service_id(group_id or "default", avatar_id)

    return TenantRuntimeContext(
        service_id=service_id,
        agent_id=agent_id,
        avatar_id=avatar_id,
        group_id=group_id,
        user_id=user_id or agent_id,
    )


@dataclass(frozen=True)
class TenantListFilters:
    """Tenant scoping for list/query APIs in enterprise mode."""

    group_id: str
    user_id: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.group_id.strip())


def parse_tenant_list_filters(payload: Any) -> TenantListFilters | None:
    """Parse tenant filters for list APIs.

    Standalone mode returns ``None`` (no tenant filtering).
    Enterprise mode always scopes results; missing ``group_id`` yields an
    invalid filter so callers can return empty lists instead of leaking
    standalone data.
    """

    if not is_enterprise_mode():
        return None

    data = payload if isinstance(payload, dict) else {}
    group_id = str(data.get("group_id") or "").strip()
    user_id = str(data.get("user_id") or data.get("owner_user_id") or "").strip()
    return TenantListFilters(group_id=group_id, user_id=user_id)


@dataclass(frozen=True)
class EnterpriseAuthContext:
    """Authenticated enterprise user context from WebSocket params."""

    group_id: str
    user_id: str = ""
    role: str = "member"

    @property
    def is_org_admin(self) -> bool:
        normalized = str(self.role or "").strip().lower()
        return normalized in {"org_admin", "orgadmin", "admin", "platform_admin", "tenant_admin", "group_admin"}


def parse_enterprise_auth(payload: Any) -> EnterpriseAuthContext | None:
    """Parse enterprise auth context from API params.

    Standalone mode returns ``None``. Enterprise mode reads ``group_id``,
    ``user_id``/``owner_user_id``, and optional ``role`` (``org_admin`` / ``member``).
    """

    if not is_enterprise_mode():
        return None

    data = payload if isinstance(payload, dict) else {}
    group_id = str(data.get("group_id") or "").strip()
    user_id = str(data.get("user_id") or data.get("owner_user_id") or "").strip()
    role = str(data.get("role") or "member").strip().lower() or "member"
    return EnterpriseAuthContext(group_id=group_id, user_id=user_id, role=role)


def require_org_admin(auth: EnterpriseAuthContext | None) -> EnterpriseAuthContext:
    if auth is None:
        raise PermissionError("Enterprise auth context is required")
    if not auth.group_id:
        raise PermissionError("group_id is required in enterprise mode")
    if not auth.is_org_admin:
        raise PermissionError("org_admin role required")
    return auth


def merge_routing(
    params: dict[str, Any],
    *,
    service_id: str = "",
    agent_id: str = "",
    avatar_id: str = "",
    group_id: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """Return params with a normalized `routing` object."""

    merged = dict(params or {})
    current = merged.get("routing") if isinstance(merged.get("routing"), dict) else {}
    routing = {
        **current,
        "service_id": service_id or current.get("service_id") or "",
        "agent_id": agent_id or current.get("agent_id") or user_id or "",
        "avatar_id": avatar_id or current.get("avatar_id") or merged.get("avatar_id") or "",
        "group_id": group_id or current.get("group_id") or merged.get("group_id") or "",
        "user_id": user_id or current.get("user_id") or merged.get("user_id") or agent_id or "",
    }
    if not routing["service_id"] and routing["avatar_id"]:
        routing["service_id"] = make_service_id(routing["group_id"] or "default", routing["avatar_id"])
    merged["routing"] = {k: v for k, v in routing.items() if v}
    if routing.get("avatar_id"):
        merged.setdefault("avatar_id", routing["avatar_id"])
    if routing.get("group_id"):
        merged.setdefault("group_id", routing["group_id"])
    if routing.get("user_id"):
        merged.setdefault("user_id", routing["user_id"])
    return merged
