import json
import logging

from jiuwenswarm.channels.web.app_web import _SpaStaticHandler


def _capture_ws_business_log(caplog, direction: str, message: dict) -> str:
    logger = logging.getLogger(f"{__name__}.{direction}")
    handler = object.__new__(_SpaStaticHandler)
    handler.logger = logger

    logger.addHandler(caplog.handler)
    logger.propagate = False
    caplog.set_level(logging.INFO, logger=logger.name)
    try:
        handler._log_ws_business_message(direction, json.dumps(message))
    finally:
        logger.removeHandler(caplog.handler)
        logger.propagate = True

    return caplog.text


def test_ws_request_log_redacts_nested_codex_auth_capabilities(caplog):
    canaries = {
        "operation": "operation-canary-4ab78c6c",
        "login": "login-canary-372c8aa1",
        "verification": "https://auth.openai.com/device?canary=2a601d6e",
        "auth_url": "https://auth.openai.com/authorize?canary=fd8d3124",
        "code": "CODE-CANARY-8F31",
    }
    message = {
        "type": "req",
        "id": "req-visible-17",
        "method": "provider.codex.auth.cancel",
        "params": {
            "operation_id": canaries["operation"],
            "nested": [
                {"loginId": canaries["login"]},
                {
                    "verificationUrl": canaries["verification"],
                    "auth_url": canaries["auth_url"],
                    "userCode": canaries["code"],
                },
            ],
            "diagnostic": "safe-request-detail",
        },
    }

    logged = _capture_ws_business_log(caplog, "frontend->backend", message)

    assert all(canary not in logged for canary in canaries.values())
    assert "frontend->backend" in logged
    assert "req-visible-17" in logged
    assert "provider.codex.auth.cancel" in logged
    assert "safe-request-detail" in logged
    assert logged.count("[redacted]") == 5


def test_ws_response_log_redacts_nested_codex_auth_capabilities(caplog):
    canaries = {
        "operation": "operation-canary-c3be75d4",
        "login": "login-canary-cc19a0db",
        "verification": "https://chatgpt.com/device?canary=9e47b105",
        "auth_url": "https://chatgpt.com/authorize?canary=0087d3d0",
        "code": "CODE-CANARY-61D2",
    }
    message = {
        "type": "res",
        "id": "req-visible-23",
        "ok": True,
        "payload": {
            "state": "waiting_for_user",
            "handoff": {
                "operationId": canaries["operation"],
                "login_id": canaries["login"],
                "verification_url": canaries["verification"],
                "authUrl": canaries["auth_url"],
                "user_code": canaries["code"],
            },
            "diagnostic": "safe-response-detail",
        },
        "error": None,
        "code": None,
    }

    logged = _capture_ws_business_log(caplog, "backend->frontend", message)

    assert all(canary not in logged for canary in canaries.values())
    assert "backend->frontend" in logged
    assert "req-visible-23" in logged
    assert "waiting_for_user" in logged
    assert "safe-response-detail" in logged
    assert logged.count("[redacted]") == 5
