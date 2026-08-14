# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Issue short-lived SSH key pairs and register public fingerprints."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from jiuwenswarm.extensions.agentos.auth.ssh_key_registry import (
    KeyRegistry,
    KeyRegistryEntry,
)

logger = logging.getLogger(__name__)


class SshKeyIssuer(Protocol):
    """Issue SSH key pairs and register public fingerprints."""

    def issue_ephemeral_key(
        self,
        *,
        user_id: str,
        username: str,
        session_id: str,
        ttl_sec: float,
    ) -> str:
        """Generate a short-lived key pair, register the fingerprint, return private key."""
        ...

    def issue_container_key(
        self,
        *,
        user_id: str,
        username: str,
    ) -> str:
        """Generate a long-lived container key pair, register it, return private key."""
        ...


def _missing_asyncssh_error() -> RuntimeError:
    return RuntimeError(
        "SSH key issuance requires optional dependency "
        "`asyncssh>=2.14.0,<2.24`. Install with "
        '`uv sync --extra ssh` / `pip install "jiuwenswarm[ssh]"`.'
    )


def _export_openssh_private_key(key: object) -> str:
    exported = key.export_private_key("openssh")  # type: ignore[attr-defined]
    if isinstance(exported, bytes):
        return exported.decode("utf-8")
    return str(exported)


class AgentOSSshKeyIssuer:
    """Generate OpenSSH keys and register fingerprints in KeyRegistry."""

    def __init__(
        self,
        registry: KeyRegistry,
        *,
        key_type: str = "ssh-ed25519",
    ) -> None:
        self._registry = registry
        self._key_type = str(key_type or "ssh-ed25519").strip() or "ssh-ed25519"

    @property
    def registry(self) -> KeyRegistry:
        return self._registry

    def issue_ephemeral_key(
        self,
        *,
        user_id: str,
        username: str,
        session_id: str,
        ttl_sec: float,
    ) -> str:
        key, private_key, fingerprint = self._generate_key()
        del key
        now = time.time()
        ttl = max(0.0, float(ttl_sec))
        self._registry.register(
            KeyRegistryEntry(
                fingerprint=fingerprint,
                user_id=str(user_id or "").strip(),
                username=str(username or user_id or "").strip() or "unknown",
                source="tui_switch",
                session_id=str(session_id or "").strip() or None,
                expires_at=(now + ttl) if ttl > 0 else None,
                created_at=now,
            )
        )
        logger.info(
            "[AgentOSAuth] issued ephemeral SSH key: user_id=%s session=%s ttl=%.0fs fp=%s",
            user_id,
            session_id,
            ttl,
            fingerprint,
        )
        return private_key

    def issue_container_key(
        self,
        *,
        user_id: str,
        username: str,
    ) -> str:
        """Generate a long-lived key pair for a jiuwenswarm container SSH client."""
        uid = str(user_id or "").strip()
        uname = str(username or user_id or "").strip() or "unknown"
        key, private_key, fingerprint = self._generate_key()
        del key
        now = time.time()
        # Agent rebuilds mint a new pair; drop the previous container key for this user.
        self._registry.revoke_by_user(uid, source="container")
        self._registry.register(
            KeyRegistryEntry(
                fingerprint=fingerprint,
                user_id=uid,
                username=uname,
                source="container",
                session_id=None,
                expires_at=None,
                created_at=now,
            )
        )
        logger.info(
            "[AgentOSAuth] issued container SSH key: user_id=%s fp=%s",
            uid,
            fingerprint,
        )
        return private_key

    def _generate_key(self) -> tuple[object, str, str]:
        try:
            import asyncssh
        except ImportError as exc:
            raise _missing_asyncssh_error() from exc

        key = asyncssh.generate_private_key(self._key_type)
        return key, _export_openssh_private_key(key), key.get_fingerprint()
