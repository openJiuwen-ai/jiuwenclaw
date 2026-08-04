# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway-scoped OpenAI Account OAuth orchestration shared by Web and TUI."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OpenAIAccountAuthError,
    OpenAIAccountAuthManager,
    OpenAIAccountDeviceCode,
)
from openjiuwen.extensions.external_provider.openai_auth.openai_account_models import (
    OpenAIAccountModelCatalog,
)


OPENAI_ACCOUNT_LOCAL_ERRORS = (OSError, TypeError, ValueError)
_DEFAULT_LOGIN_MAX_TTL_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class _OpenAIAccountLoginJob:
    device_code: OpenAIAccountDeviceCode
    created_at: float
    expires_at: float


def _create_model_catalog(base_url: str) -> OpenAIAccountModelCatalog:
    return OpenAIAccountModelCatalog(base_url=base_url)


def _create_login_id() -> str:
    return uuid.uuid4().hex


def openai_account_auth_error_payload(exc: OpenAIAccountAuthError) -> dict[str, Any]:
    """Return transport-neutral details for a typed Core OAuth error."""
    retriable = not exc.relogin_required and (
        exc.status_code == 429 or str(exc.code).endswith("_network_error")
    )
    return {
        "status": "error",
        "error": str(exc),
        "code": exc.code,
        "status_code": exc.status_code,
        "relogin_required": exc.relogin_required,
        "retriable": retriable,
    }


class OpenAIAccountService:
    """Own Gateway-process OAuth jobs while delegating credentials to agent-core."""

    def __init__(
        self,
        *,
        auth_manager_factory: Callable[
            [], OpenAIAccountAuthManager
        ] = OpenAIAccountAuthManager,
        model_catalog_factory: Callable[
            [str], OpenAIAccountModelCatalog
        ] = _create_model_catalog,
        now: Callable[[], float] = time.time,
        login_id_factory: Callable[[], str] = _create_login_id,
        max_login_ttl_seconds: int = _DEFAULT_LOGIN_MAX_TTL_SECONDS,
    ) -> None:
        self._auth_manager_factory = auth_manager_factory
        self._model_catalog_factory = model_catalog_factory
        self._now = now
        self._login_id_factory = login_id_factory
        self._max_login_ttl_seconds = max(1, int(max_login_ttl_seconds))
        self._jobs: dict[str, _OpenAIAccountLoginJob] = {}
        self._operation_lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._status_payload(self._auth_manager_factory())

    def start_login(self) -> dict[str, Any]:
        with self._operation_lock:
            manager = self._auth_manager_factory()
            now = self._now()
            latest_job = self._latest_job(now)
            if latest_job is not None:
                login_id, job = latest_job
                return self._login_payload(login_id, job, manager, now)

            device_code = manager.start_device_login()
            now = self._now()
            raw_expires_in = device_code.expires_in or self._max_login_ttl_seconds
            expires_in = min(int(raw_expires_in), self._max_login_ttl_seconds)
            job = _OpenAIAccountLoginJob(
                device_code=device_code,
                created_at=now,
                expires_at=now + expires_in,
            )
            login_id = self._login_id_factory()
            self._jobs[login_id] = job
            return self._login_payload(login_id, job, manager, now)

    def pending_login(self) -> dict[str, Any]:
        with self._operation_lock:
            manager = self._auth_manager_factory()
            now = self._now()
            latest_job = self._latest_job(now)
            if latest_job is None:
                return {
                    "status": "none",
                    "auth": self._status_payload(manager),
                }
            login_id, job = latest_job
            return self._login_payload(login_id, job, manager, now)

    def poll_login(self, login_id: str) -> dict[str, Any]:
        normalized_login_id = str(login_id or "").strip()
        if not normalized_login_id:
            raise ValueError("login_id is required")

        with self._operation_lock:
            now = self._now()
            self._cleanup_jobs(now)
            job = self._jobs.get(normalized_login_id)
            if job is None:
                return {"status": "expired", "authenticated": False}

            manager = self._auth_manager_factory()
            try:
                tokens = manager.poll_device_login(job.device_code)
            except OpenAIAccountAuthError as exc:
                if exc.relogin_required:
                    self._jobs.pop(normalized_login_id, None)
                raise
            if tokens is None:
                return {
                    "status": "pending",
                    "authenticated": False,
                    "expires_at": job.expires_at,
                }

            self._jobs.pop(normalized_login_id, None)
            return {
                "status": "authenticated",
                "authenticated": True,
                "auth": self._status_payload(manager),
            }

    def logout(self) -> dict[str, Any]:
        with self._operation_lock:
            manager = self._auth_manager_factory()
            self._jobs.clear()
            logged_out = manager.logout()
            return {
                "logged_out": logged_out,
                "auth": self._status_payload(manager),
            }

    def list_models(self) -> dict[str, Any]:
        with self._operation_lock:
            manager = self._auth_manager_factory()
            catalog = self._model_catalog_factory(manager.base_url)
            models = catalog.list_model_ids(auth_manager=manager)
            return {
                "models": models,
                "base_url": manager.base_url,
                "auth": self._status_payload(manager),
            }

    def _cleanup_jobs(self, now: float) -> None:
        expired_ids = [
            login_id for login_id, job in self._jobs.items() if job.expires_at <= now
        ]
        for login_id in expired_ids:
            self._jobs.pop(login_id, None)

    def _latest_job(self, now: float) -> tuple[str, _OpenAIAccountLoginJob] | None:
        self._cleanup_jobs(now)
        if not self._jobs:
            return None
        return max(self._jobs.items(), key=lambda item: item[1].created_at)

    @staticmethod
    def _status_payload(manager: OpenAIAccountAuthManager) -> dict[str, Any]:
        status = manager.status()
        return {
            "authenticated": status.authenticated,
            "auth_path": str(status.auth_path),
            "has_refresh_token": status.has_refresh_token,
            "expires_at": status.expires_at,
            "needs_refresh": status.needs_refresh,
            "error": status.error,
            "base_url": manager.base_url,
        }

    def _login_payload(
        self,
        login_id: str,
        job: _OpenAIAccountLoginJob,
        manager: OpenAIAccountAuthManager,
        now: float,
    ) -> dict[str, Any]:
        return {
            "status": "pending",
            "login_id": login_id,
            "user_code": job.device_code.user_code,
            "verification_uri": job.device_code.verification_uri,
            "interval": job.device_code.interval,
            "expires_in": max(0, int(job.expires_at - now)),
            "expires_at": job.expires_at,
            "auth": self._status_payload(manager),
        }
