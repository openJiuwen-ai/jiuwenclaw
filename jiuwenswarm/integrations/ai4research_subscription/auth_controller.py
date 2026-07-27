"""Instance-scoped Codex subscription authentication controller."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import ParseResult, urlparse

from .app_server import CodexAppServerClient
from .consumer_policy import codex_subscription_enabled, require_codex_enabled
from .errors import CodexProviderError
from .locking import acquire_profile_lock_async, release_profile_lock
from .process_lifecycle import await_task_uninterruptibly
from .profiles import ensure_codex_profile, verify_codex_auth_file
from .quarantine import (
    profile_is_quarantined,
    quarantine_ownership,
    reconcile_profile_quarantine,
)


_LOGIN_TIMEOUT_SECONDS = 10 * 60.0
_DEVICE_LOGIN_HOSTS = {"auth.openai.com", "chatgpt.com"}
_RECONCILIATION_DELAYS_SECONDS = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)


def _is_valid_device_login_handoff(
    provider_login_id: Any,
    user_code: Any,
    parsed_url: ParseResult | None,
) -> bool:
    if not isinstance(provider_login_id, str):
        return False
    if not provider_login_id:
        return False
    if not isinstance(user_code, str):
        return False
    if not user_code:
        return False
    if parsed_url is None:
        return False
    if parsed_url.scheme != "https":
        return False
    return parsed_url.hostname in _DEVICE_LOGIN_HOSTS


async def _close_client_and_release_lock(
    client: CodexAppServerClient,
    lock_handle: Any,
    *,
    initial_cancellation: asyncio.CancelledError | None = None,
) -> None:
    """Close the App Server before directly releasing its lock, despite cancellation."""

    async def finalize() -> None:
        cleanup_error: BaseException | None = None
        try:
            await client.close()
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None and profile_is_quarantined(client.profile):
            try:
                quarantine_ownership(
                    client.profile,
                    process=None,
                    pgid=None,
                    lock_handle=lock_handle,
                )
            except BaseException:
                pass
        else:
            try:
                release_profile_lock(lock_handle)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error

    task = asyncio.create_task(finalize(), name="codex-auth-finalizer")
    _, cancellation = await await_task_uninterruptibly(task, initial_cancellation)
    if cancellation is not None:
        raise cancellation


@dataclass
class _LoginOperation:
    public_id: str
    provider_login_id: str
    client: CodexAppServerClient
    lock_handle: Any
    started_at: float
    task: asyncio.Task | None = None
    state: str = "waiting_for_user"
    cleaned_up: bool = False


def _sanitized_account_state(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise CodexProviderError("auth_protocol_error", "Codex returned an invalid account state.")
    account = result.get("account")
    has_account = isinstance(account, dict)
    account_type = str(account.get("type") or "")[:40] if has_account else None
    connected = has_account and account_type == "chatgpt"
    state = "connected" if connected else "not_connected"
    if has_account and not connected:
        state = "wrong_auth_method"
    return {
        "provider": "AI4ResearchCodex",
        "enabled": codex_subscription_enabled(),
        "available": True,
        "connected": connected,
        "state": state,
        "auth_type": account_type,
        "requires_openai_auth": bool(result.get("requiresOpenaiAuth", True)),
    }


def _managed_auth_file_exists() -> bool:
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    return auth_path.exists() or auth_path.is_symlink()


async def _reconcile_login(client: CodexAppServerClient) -> dict[str, Any]:
    for delay in _RECONCILIATION_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        status = _sanitized_account_state(
            await client.request("account/read", {"refreshToken": False}, timeout=20.0)
        )
        if status["state"] == "wrong_auth_method":
            raise CodexProviderError(
                "wrong_auth_method",
                "The managed Codex profile is not connected through ChatGPT.",
            )
        if status["connected"]:
            verify_codex_auth_file(ensure_codex_profile())
            return status
    raise CodexProviderError("auth_failed", "Codex login could not be reconciled.")


async def _reconcile_logout(client: CodexAppServerClient) -> dict[str, Any]:
    for delay in _RECONCILIATION_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        status = _sanitized_account_state(
            await client.request("account/read", {"refreshToken": False}, timeout=20.0)
        )
        if not status["connected"] and not _managed_auth_file_exists():
            return status
    raise CodexProviderError(
        "logout_failed", "Codex remained connected or retained its managed credential after logout."
    )


class CodexAuthController:
    def __init__(self) -> None:
        self._mutex = asyncio.Lock()
        self._operation: _LoginOperation | None = None
        self._last_error: str | None = None

    async def _new_client_with_lock(self) -> tuple[CodexAppServerClient, Any]:
        profile = ensure_codex_profile()
        await reconcile_profile_quarantine(profile)
        lock_handle = await acquire_profile_lock_async(profile)
        client = CodexAppServerClient(profile)
        try:
            await client.start()
        except BaseException as exc:
            await _close_client_and_release_lock(
                client,
                lock_handle,
                initial_cancellation=(
                    exc if isinstance(exc, asyncio.CancelledError) else None
                ),
            )
            raise
        return client, lock_handle

    async def status(self) -> dict[str, Any]:
        async with self._mutex:
            operation = self._operation
            if operation is not None:
                return {
                    "provider": "AI4ResearchCodex",
                    "enabled": codex_subscription_enabled(),
                    "available": True,
                    "connected": False,
                    "state": operation.state,
                    "operation_id": operation.public_id,
                    "started_at": operation.started_at,
                }
        if not codex_subscription_enabled():
            return {
                "provider": "AI4ResearchCodex",
                "enabled": False,
                "available": True,
                "connected": False,
                "state": "disabled",
            }
        try:
            client, lock_handle = await self._new_client_with_lock()
        except CodexProviderError as exc:
            return {
                "provider": "AI4ResearchCodex",
                "enabled": codex_subscription_enabled(),
                "available": exc.code not in {"cli_unavailable", "unsupported_cli"},
                "connected": False,
                "state": "busy" if exc.code == "provider_busy" else "unavailable",
                "error_code": exc.code,
            }
        try:
            result = await client.request("account/read", {"refreshToken": False})
            status = _sanitized_account_state(result)
            if status["connected"]:
                verify_codex_auth_file(ensure_codex_profile())
            if self._last_error:
                status["last_error_code"] = self._last_error
            return status
        finally:
            await _close_client_and_release_lock(client, lock_handle)

    async def start_device_login(self) -> dict[str, Any]:
        require_codex_enabled()
        async with self._mutex:
            if self._operation is not None:
                raise CodexProviderError("auth_busy", "A Codex login is already in progress.")
            client, lock_handle = await self._new_client_with_lock()
            owns_resources = True
            try:
                account = _sanitized_account_state(
                    await client.request("account/read", {"refreshToken": False})
                )
                if account["connected"]:
                    verify_codex_auth_file(ensure_codex_profile())
                    owns_resources = False
                    await _close_client_and_release_lock(client, lock_handle)
                    return account
                if account["state"] == "wrong_auth_method":
                    raise CodexProviderError(
                        "wrong_auth_method",
                        "The managed Codex profile is not connected through ChatGPT.",
                    )
                result = await client.request(
                    "account/login/start",
                    {"type": "chatgptDeviceCode"},
                    timeout=30.0,
                )
                if not isinstance(result, dict) or result.get("type") != "chatgptDeviceCode":
                    raise CodexProviderError("auth_protocol_error", "Codex returned an invalid login response.")
                provider_login_id = result.get("loginId")
                verification_url = result.get("verificationUrl")
                user_code = result.get("userCode")
                parsed_url = urlparse(verification_url) if isinstance(verification_url, str) else None
                if not _is_valid_device_login_handoff(
                    provider_login_id,
                    user_code,
                    parsed_url,
                ):
                    raise CodexProviderError("auth_protocol_error", "Codex returned an invalid login response.")
                operation = _LoginOperation(
                    public_id=secrets.token_urlsafe(18),
                    provider_login_id=provider_login_id,
                    client=client,
                    lock_handle=lock_handle,
                    started_at=time.time(),
                )
                self._operation = operation
                owns_resources = False
                operation.task = asyncio.create_task(self._watch_login(operation))
                self._last_error = None
                return {
                    "provider": "AI4ResearchCodex",
                    "enabled": codex_subscription_enabled(),
                    "available": True,
                    "connected": False,
                    "state": "waiting_for_user",
                    "operation_id": operation.public_id,
                    "verification_url": verification_url,
                    "user_code": user_code,
                    "expires_in_seconds": int(_LOGIN_TIMEOUT_SECONDS),
                }
            except BaseException:
                if owns_resources:
                    owns_resources = False
                    await _close_client_and_release_lock(client, lock_handle)
                raise

    async def _watch_login(self, operation: _LoginOperation) -> None:
        error_code: str | None = None
        try:
            completed = await operation.client.wait_notification(
                "account/login/completed",
                lambda params: params.get("loginId") == operation.provider_login_id,
                timeout=_LOGIN_TIMEOUT_SECONDS,
            )
            if completed.get("success") is not True:
                raise CodexProviderError("auth_failed", "Codex login was not approved.")
            operation.state = "reconciling"
            await _reconcile_login(operation.client)
        except asyncio.CancelledError:
            return
        except CodexProviderError as exc:
            error_code = exc.code
        except Exception:
            error_code = "auth_failed"
        finally:
            await self._cleanup_operation(operation, error_code=error_code)

    async def _cleanup_operation(
        self,
        operation: _LoginOperation,
        *,
        error_code: str | None,
    ) -> None:
        async with self._mutex:
            if operation.cleaned_up:
                return
            operation.cleaned_up = True
            if self._operation is operation:
                self._operation = None
                self._last_error = error_code
        await _close_client_and_release_lock(operation.client, operation.lock_handle)

    async def cancel(self, operation_id: str) -> dict[str, Any]:
        async with self._mutex:
            operation = self._operation
            if operation is None or not secrets.compare_digest(operation.public_id, str(operation_id or "")):
                raise CodexProviderError("stale_auth_operation", "The Codex login operation is no longer active.")
            operation.state = "canceling"
            await operation.client.request(
                "account/login/cancel",
                {"loginId": operation.provider_login_id},
            )
            task = operation.task
            self._operation = None
            self._last_error = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._cleanup_operation(operation, error_code=None)
        return await self.status()

    async def logout(self) -> dict[str, Any]:
        async with self._mutex:
            if self._operation is not None:
                raise CodexProviderError("auth_busy", "Cancel the active Codex login before disconnecting.")
            client, lock_handle = await self._new_client_with_lock()
            try:
                await client.request("account/logout", {})
                status = await _reconcile_logout(client)
                self._last_error = None
                return status
            finally:
                await _close_client_and_release_lock(client, lock_handle)

    async def shutdown(self) -> None:
        async with self._mutex:
            operation = self._operation
            self._operation = None
        if operation is None:
            return
        task = operation.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._cleanup_operation(operation, error_code=None)


_CONTROLLER: CodexAuthController | None = None


def get_codex_auth_controller() -> CodexAuthController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = CodexAuthController()
    return _CONTROLLER
