# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Southbound SSH relay into YuanRong agent instances.

Bridges a northbound ``SshRelaySession`` (accepted by the gateway
``SshChannel`` as an interactive shell) to the YuanRong frontend SSH
endpoint::

    ssh -p 2222 'yr:instance:<instance_id>'@<frontend-host>

``<instance_id>`` is the instance id returned by the YuanRong agent
create API (``POST /api/agent``), resolved by the AgentOS Router.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 2222
DEFAULT_SSH_USER_TEMPLATE = "yr:instance:{instance}"
_RELAY_BUFFER_SIZE = 32768


def _raise_missing_asyncssh(exc: ImportError) -> None:
    raise RuntimeError(
        "SSH relay requires optional dependency `asyncssh>=2.14.0,<2.24`. "
        "Install with `pip install -e \".[ssh]\"` or "
        '`uv sync --extra ssh` / `pip install "jiuwenswarm[ssh]"`.'
    ) from exc


def _import_asyncssh() -> Any:
    try:
        import asyncssh
    except ImportError as exc:
        _raise_missing_asyncssh(exc)
    return asyncssh


@dataclass(frozen=True)
class YuanrongSshSettings:
    """Southbound SSH access settings for the YuanRong frontend."""

    port: int = DEFAULT_SSH_PORT
    user_template: str = DEFAULT_SSH_USER_TEMPLATE
    connect_timeout_s: float = 30.0


def load_yuanrong_ssh_settings(raw: Any) -> YuanrongSshSettings:
    """Build settings from the ``gateway.agentos.ssh`` config block."""
    if not isinstance(raw, dict):
        raw = {}
    return YuanrongSshSettings(
        port=int(raw.get("port") or DEFAULT_SSH_PORT),
        user_template=str(
            raw.get("user_template") or DEFAULT_SSH_USER_TEMPLATE
        ).strip(),
        connect_timeout_s=float(raw.get("connect_timeout_s") or 30.0),
    )


class YuanrongSshRelay:
    """Relay PTY/exec traffic between a northbound SSH session and YuanRong."""

    def __init__(
        self,
        settings: YuanrongSshSettings,
        *,
        frontend_endpoint: str = "",
    ) -> None:
        self._settings = settings
        self._frontend_endpoint = (frontend_endpoint or "").strip()

    @property
    def backend_host(self) -> str:
        parsed = urllib.parse.urlparse(self._frontend_endpoint)
        return parsed.hostname or ""

    @property
    def backend_port(self) -> int:
        return self._settings.port

    def backend_username(self, instance_id: str) -> str:
        instance = str(instance_id or "").strip()
        if not instance:
            raise ValueError("instance_id is required for YuanRong SSH relay")
        return self._settings.user_template.format(instance=instance)

    async def run(self, session: Any, instance_id: str) -> int:
        """Relay *session* to the YuanRong instance; returns the exit code.

        Always resolves ``session.done`` and ``session.exit_code`` so the
        northbound channel waiting in ``_wait_relay_done`` is released.
        """
        exit_code = 1
        try:
            exit_code = await self._relay(session, instance_id)
        except Exception as exc:  # noqa: BLE001 - report any relay failure to the client
            logger.exception(
                "[YuanrongSshRelay] relay failed: session=%s instance=%s",
                session.session_id,
                instance_id,
            )
            self._write_client_error(session, f"yuanrong ssh relay failed: {exc}")
        finally:
            session.exit_code = exit_code
            session.done.set()
        return exit_code

    def fail_session(self, session: Any, reason: str) -> None:
        """Mark *session* failed and release the northbound waiter."""
        self._write_client_error(session, reason)
        session.exit_code = 1
        session.done.set()

    @staticmethod
    def _write_client_error(session: Any, reason: str) -> None:
        try:
            session.process.stdout.write(f"[ssh-relay] {reason}\r\n".encode())
        except Exception:  # noqa: BLE001 - client may already be gone
            logger.debug("[YuanrongSshRelay] client write failed", exc_info=True)

    async def _relay(self, session: Any, instance_id: str) -> int:
        asyncssh = _import_asyncssh()

        host = self.backend_host
        if not host:
            raise ValueError(
                "yuanrong ssh host is empty "
                "(set gateway.agent_client.frontend_endpoint with a hostname)"
            )
        username = self.backend_username(instance_id)

        logger.info(
            "[YuanrongSshRelay] connecting: %s@%s:%s session=%s",
            username,
            host,
            self._settings.port,
            session.session_id,
        )
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host,
                port=self._settings.port,
                username=username,
                known_hosts=None,
            ),
            timeout=self._settings.connect_timeout_s,
        )
        try:
            return await self._relay_over_connection(session, conn)
        finally:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:  # noqa: BLE001
                logger.debug("[YuanrongSshRelay] close failed", exc_info=True)

    async def _relay_over_connection(self, session: Any, conn: Any) -> int:
        process = session.process

        # Interactive shell only (northbound rejects exec requests).
        kwargs: dict[str, Any] = {"encoding": None}
        term_type = process.get_terminal_type() or "xterm"
        kwargs["term_type"] = term_type
        term_size = process.get_terminal_size()
        if term_size and term_size[0]:
            kwargs["term_size"] = term_size
        backend = await conn.create_process(**kwargs)
        try:
            await asyncio.gather(
                self._pump_client_to_backend(session, backend),
                self._pump_backend_to_client(backend.stdout, process.stdout),
                self._pump_backend_to_client(backend.stderr, process.stderr),
            )
            await backend.wait_closed()
        finally:
            backend.close()
        exit_status = backend.exit_status
        return int(exit_status) if exit_status is not None else 0

    @staticmethod
    async def _pump_client_to_backend(session: Any, backend: Any) -> None:
        asyncssh = _import_asyncssh()

        process = session.process
        while True:
            try:
                data = await process.stdin.read(_RELAY_BUFFER_SIZE)
            except asyncssh.TerminalSizeChanged as exc:
                try:
                    backend.change_terminal_size(
                        exc.width, exc.height, exc.pixwidth, exc.pixheight
                    )
                except Exception:  # noqa: BLE001 - exec channels have no PTY
                    logger.debug(
                        "[YuanrongSshRelay] change_terminal_size failed",
                        exc_info=True,
                    )
                continue
            except asyncssh.BreakReceived:
                backend.stdin.write(b"\x03")
                continue
            except (asyncssh.ConnectionLost, ConnectionError):
                break
            if not data:
                try:
                    backend.stdin.write_eof()
                except Exception:  # noqa: BLE001 - backend may already be closed
                    logger.debug(
                        "[YuanrongSshRelay] write_eof failed", exc_info=True
                    )
                break
            backend.stdin.write(data)
            try:
                await backend.stdin.drain()
            except (asyncssh.ConnectionLost, ConnectionError):
                break

    @staticmethod
    async def _pump_backend_to_client(reader: Any, writer: Any) -> None:
        asyncssh = _import_asyncssh()

        while True:
            try:
                data = await reader.read(_RELAY_BUFFER_SIZE)
            except (asyncssh.ConnectionLost, ConnectionError):
                break
            if not data:
                break
            try:
                writer.write(data)
                await writer.drain()
            except (asyncssh.ConnectionLost, ConnectionError):
                break
