# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OpenAIAccountAuthError,
)

from jiuwenswarm.gateway.channel_manager.openai_account_service import (
    OpenAIAccountService,
    openai_account_auth_error_payload,
)


class FakeAuthState:
    def __init__(self) -> None:
        self.authenticated = False
        self.start_count = 0
        self.poll_result = None
        self.poll_error: OpenAIAccountAuthError | None = None
        self.logged_out = False


class FakeAuthManager:
    base_url = "https://example.test/codex"

    def __init__(self, state: FakeAuthState) -> None:
        self.state = state

    def status(self):
        return SimpleNamespace(
            authenticated=self.state.authenticated,
            auth_path=Path("fake-auth.json"),
            has_refresh_token=self.state.authenticated,
            expires_at=123.0 if self.state.authenticated else None,
            needs_refresh=False,
            error=None,
        )

    def start_device_login(self):
        self.state.start_count += 1
        return SimpleNamespace(
            user_code="ABCD-EFGH",
            device_auth_id="device-secret",
            verification_uri="https://example.test/device",
            interval=5,
            expires_in=600,
        )

    def poll_device_login(self, device_code):
        assert device_code.device_auth_id == "device-secret"
        if self.state.poll_error is not None:
            raise self.state.poll_error
        if self.state.poll_result is not None:
            self.state.authenticated = True
        return self.state.poll_result

    def logout(self):
        self.state.authenticated = False
        self.state.logged_out = True
        return True


def _fixed_time() -> float:
    return 100.0


def make_service(
    state: FakeAuthState,
    *,
    now=_fixed_time,
    model_catalog_factory=None,
) -> OpenAIAccountService:
    kwargs = {}
    if model_catalog_factory is not None:
        kwargs["model_catalog_factory"] = model_catalog_factory
    return OpenAIAccountService(
        auth_manager_factory=lambda: FakeAuthManager(state),
        now=now,
        login_id_factory=lambda: "login-1",
        max_login_ttl_seconds=300,
        **kwargs,
    )


def test_start_login_reuses_pending_job_and_caps_ttl():
    state = FakeAuthState()
    service = make_service(state)

    started = service.start_login()
    resumed = service.start_login()

    assert started == resumed
    assert started["login_id"] == "login-1"
    assert started["expires_in"] == 300
    assert state.start_count == 1
    assert "device-secret" not in repr(started)


def test_expired_job_is_not_returned_as_pending():
    state = FakeAuthState()
    current_time = [100.0]
    service = make_service(state, now=lambda: current_time[0])
    service.start_login()

    current_time[0] = 401.0

    assert service.pending_login()["status"] == "none"
    assert service.poll_login("login-1") == {
        "status": "expired",
        "authenticated": False,
    }


def test_poll_login_transitions_to_authenticated_without_returning_tokens():
    state = FakeAuthState()
    state.poll_result = SimpleNamespace(
        access_token="access-secret",
        refresh_token="refresh-secret",
    )
    service = make_service(state)
    service.start_login()

    result = service.poll_login("login-1")

    assert result["status"] == "authenticated"
    assert result["auth"]["authenticated"] is True
    assert "access-secret" not in repr(result)
    assert "refresh-secret" not in repr(result)
    assert service.pending_login()["status"] == "none"


def test_relogin_required_poll_error_discards_pending_job():
    state = FakeAuthState()
    state.poll_error = OpenAIAccountAuthError(
        "authorization expired",
        code="expired_token",
        relogin_required=True,
    )
    service = make_service(state)
    service.start_login()

    with pytest.raises(OpenAIAccountAuthError, match="authorization expired"):
        service.poll_login("login-1")

    assert service.pending_login()["status"] == "none"


def test_list_models_uses_manager_base_url_and_refreshed_status():
    state = FakeAuthState()

    class FakeCatalog:
        def __init__(self, base_url: str) -> None:
            assert base_url == FakeAuthManager.base_url

        def list_model_ids(self, *, auth_manager):
            auth_manager.state.authenticated = True
            return ["gpt-test", "gpt-test-mini"]

    service = make_service(state, model_catalog_factory=FakeCatalog)

    result = service.list_models()

    assert result["models"] == ["gpt-test", "gpt-test-mini"]
    assert result["base_url"] == FakeAuthManager.base_url
    assert result["auth"]["authenticated"] is True


@pytest.mark.parametrize(
    ("error", "expected_retriable"),
    [
        (
            OpenAIAccountAuthError(
                "temporary network failure",
                code="openai_account_device_code_poll_network_error",
            ),
            True,
        ),
        (
            OpenAIAccountAuthError(
                "rate limited",
                code="openai_account_rate_limited",
                status_code=429,
            ),
            True,
        ),
        (
            OpenAIAccountAuthError(
                "login required",
                code="invalid_grant",
                relogin_required=True,
                status_code=401,
            ),
            False,
        ),
    ],
)
def test_auth_error_payload_classifies_retryable_failures(error, expected_retriable):
    payload = openai_account_auth_error_payload(error)

    assert payload["retriable"] is expected_retriable
    assert payload["relogin_required"] is error.relogin_required
