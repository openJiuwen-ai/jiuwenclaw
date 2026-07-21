# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SSH proxy server: accept clients and hand off to MessageHandler."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from jiuwenswarm.gateway.channel_manager.protocol.ssh.config import ProxyConfig

logger = logging.getLogger(__name__)

try:
    import asyncssh

    ASYNCSSH_AVAILABLE = True
except ImportError:
    ASYNCSSH_AVAILABLE = False
    asyncssh = None  # type: ignore[assignment]


def _raise_missing_asyncssh(exc: ImportError) -> None:
    raise RuntimeError(
        "SSH channel is enabled but optional dependency `asyncssh>=2.14.0,<2.24` "
        "is not installed. Install with `pip install -e \".[ssh]\"` or "
        '`uv sync --extra ssh` / `pip install "jiuwenswarm[ssh]"`.'
    ) from exc


@dataclass
class SSHAgentHooks:
    register_session: Callable[..., Awaitable[None]]
    unregister_session: Callable[[str], Awaitable[None]]
    submit_relay: Callable[[str, dict[str, Any]], Awaitable[None]]
    wait_relay_done: Callable[[str], Awaitable[int]]


class ProxySSHServer(asyncssh.SSHServer if ASYNCSSH_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.client_username: str | None = None

    def connection_made(self, conn: Any) -> None:
        conn.set_extra_info(proxy_auth=self)

    def begin_auth(self, username: str) -> bool:
        self.client_username = username
        return True

    def password_auth_supported(self) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: Any) -> bool:
        del key
        self.client_username = username
        return True

    def validate_password(self, username: str, password: str) -> bool:
        del password
        self.client_username = username
        return True


class SSHProxy:
    def __init__(self, config: ProxyConfig, agent_hooks: SSHAgentHooks | None = None) -> None:
        self.config = config
        self.agent_hooks = agent_hooks
        self._server: Any = None

    async def start(self) -> None:
        if not ASYNCSSH_AVAILABLE:
            try:
                import asyncssh as _asyncssh  # noqa: F401
            except ImportError as exc:
                _raise_missing_asyncssh(exc)

        host_key = await self._ensure_host_key(self.config.host_key_path)

        self._server = await asyncssh.create_server(
            lambda: ProxySSHServer(self.config),
            self.config.listen_host,
            self.config.listen_port,
            server_host_keys=[host_key],
            process_factory=self._handle_client,
            encoding=None,
        )
        logger.info(
            "[SSHChannel] listening on %s:%s -> MessageHandler "
            "(southbound via agent client)",
            self.config.listen_host,
            self.config.listen_port,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[SSHChannel] SSH proxy stopped")

    async def wait_closed(self) -> None:
        if self._server is not None:
            await self._server.wait_closed()

    async def _ensure_host_key(self, key_path: str) -> Any:
        path = Path(key_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return asyncssh.read_private_key(str(path))

        logger.info("[SSHChannel] generating new host key at %s", path)
        key = asyncssh.generate_private_key("ssh-rsa", key_size=2048)
        key.write_private_key(str(path))
        key.write_public_key(f"{path}.pub")
        return key

    @staticmethod
    def _client_addr(process: Any) -> str:
        peer = process.get_extra_info("peername")
        if peer:
            return f"{peer[0]}:{peer[1]}"
        return "unknown"

    async def _handle_client(self, process: Any) -> None:
        await self._handle_ssh_client(process)

    async def _handle_ssh_client(self, process: Any) -> None:
        if self.agent_hooks is None:
            process.stdout.write(b"SSH channel is not configured\r\n")
            process.exit(1)
            return

        conn = process.channel.get_connection()
        server = conn.get_extra_info("proxy_auth")
        if not isinstance(server, ProxySSHServer):
            logger.error("[SSHChannel] missing proxy auth context")
            process.exit(1)
            return

        client_addr = self._client_addr(process)
        username = process.get_extra_info("username") or server.client_username or "unknown"

        subsystem = process.subsystem
        if subsystem:
            logger.warning("[SSHChannel] subsystem request denied: %s", subsystem)
            process.exit(1)
            return

        # Interactive shell only; reject remote-command (exec) requests.
        command = (process.command or "").strip() or None
        if command:
            logger.warning(
                "[SSHChannel] exec request denied (shell only): user=%s cmd=%s",
                username,
                command,
            )
            process.stdout.write(b"SSH channel supports interactive shell only\r\n")
            process.exit(1)
            return

        session_id = f"ssh_{username}_{uuid.uuid4().hex[:8]}"
        metadata = {
            "username": username,
            "client_addr": client_addr,
            "ssh_session_id": session_id,
        }

        try:
            await self.agent_hooks.register_session(
                session_id=session_id,
                process=process,
                username=username,
                client_addr=client_addr,
            )
            await self.agent_hooks.submit_relay(session_id, metadata)
            exit_code = await self.agent_hooks.wait_relay_done(session_id)
            try:
                process.exit(exit_code)
            except Exception:  # noqa: BLE001 - may already be closed by southbound teardown
                logger.debug(
                    "[SSHChannel] process.exit after relay done failed for %s",
                    username,
                    exc_info=True,
                )
        except asyncssh.Error as exc:
            logger.error("[SSHChannel] session error for %s: %s", username, exc)
            try:
                process.stdout.write(f"Proxy error: {exc}\n".encode())
            except Exception:  # noqa: BLE001
                pass
            try:
                process.exit(1)
            except Exception:  # noqa: BLE001
                pass
        except Exception:
            logger.exception("[SSHChannel] SSH session failed for %s", username)
            try:
                process.exit(1)
            except Exception:  # noqa: BLE001
                pass
        finally:
            await self.agent_hooks.unregister_session(session_id)
