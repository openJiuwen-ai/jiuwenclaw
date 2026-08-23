# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for shared AutoReviewer evidence redaction."""

from __future__ import annotations

import json

from jiuwenswarm.agents.harness.common.rails.permissions.reviewer_redaction import (
    PERMISSION_UI_REDACTED,
    PERMISSION_UI_TRUNCATED,
    PERMISSION_UI_UNAVAILABLE,
    redact_json_value,
    redact_reviewable_payload_text,
    redact_text,
    redact_url,
    sanitize_permission_ui_payload,
)


def test_redact_text_removes_auth_values_before_assignment_redaction() -> None:
    redacted = redact_text(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789 "
        "authorization=Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="
    )

    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert "QWxhZGRpbjpvcGVuIHNlc2FtZQ" not in redacted
    assert "[redacted]" in redacted


def test_redact_text_removes_private_key_assignment_blocks() -> None:
    redacted = redact_text(
        "private_key: -----BEGIN PRIVATE KEY----- "
        "MIIEvAIBADANBgkqhkiG9w0BAQEFAASC "
        "-----END PRIVATE KEY-----"
    )

    assert "BEGIN PRIVATE KEY" not in redacted
    assert "MIIEvAIB" not in redacted
    assert "END PRIVATE KEY" not in redacted
    assert "[redacted]" in redacted


def test_redact_text_preserves_task_relevance_words() -> None:
    redacted = redact_text(
        "Grep is task-relevant and task-related for the current request."
    )

    assert redacted == (
        "Grep is task-relevant and task-related for the current request."
    )
    assert "[redacted]" not in redacted


def test_redact_text_removes_long_sk_tokens() -> None:
    redacted = redact_text(
        "api token sk-abcdefghijklmnopqrstuvwxyz0123456789 is present"
    )

    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert "[redacted]" in redacted


def test_redact_text_removes_cross_platform_user_local_paths() -> None:
    redacted = redact_text(
        r"/home/alice/.ssh/id_rsa ~/.aws/credentials "
        r"C:\Users\Alice\.aws\credentials C:/Users/Alice/Documents/report.md"
    )

    assert "/home/alice" not in redacted
    assert "~/.aws" not in redacted
    assert r"C:\Users\Alice" not in redacted
    assert "C:/Users/Alice" not in redacted
    assert redacted.count("[path]") == 4


def test_redact_reviewable_payload_text_redacts_system_paths() -> None:
    redacted = redact_reviewable_payload_text(
        "open('/etc/passwd').read(); print('/var/log/system.log')"
    )

    assert "[path]" in redacted
    assert "/etc" not in redacted
    assert "passwd" not in redacted
    assert "/var" not in redacted
    assert "system.log" not in redacted


def test_redact_reviewable_payload_text_redacts_workspace_tmp_and_unc_paths() -> None:
    redacted = redact_reviewable_payload_text(
        r"cat /tmp/private-cache /workspace/session-output "
        r"C:\Temp\report.txt \\fileserver\share\quarterly-plan"
    )

    assert redacted.count("[path]") == 4
    assert "/tmp" not in redacted
    assert "private-cache" not in redacted
    assert "/workspace" not in redacted
    assert "session-output" not in redacted
    assert r"C:\Temp" not in redacted
    assert "fileserver" not in redacted
    assert "quarterly-plan" not in redacted


def test_redact_reviewable_payload_text_preserves_non_file_slashes() -> None:
    redacted = redact_reviewable_payload_text(
        r"endpoint='/api/v1/search'; pattern=r'/\d+/'"
    )

    assert "/api/v1/search" in redacted
    assert r"/\d+/" in redacted


def test_redact_reviewable_payload_text_covers_shell_path_shapes() -> None:
    redacted = redact_reviewable_payload_text(
        'cat "/Users/alice/My Secrets/.env" '
        r"/Users/alice/My\ Secrets/token.txt "
        'python "scripts/create_ppt.py --out outputs/report.pptx',
        redact_relative_paths=True,
    )

    assert redacted.count("[path]") == 4
    for fragment in (
        "/Users/alice",
        "My Secrets",
        ".env",
        "token.txt",
        "create_ppt.py",
        "report.pptx",
    ):
        assert fragment not in redacted


def test_redact_url_removes_userinfo_and_sensitive_query_values() -> None:
    redacted = redact_url(
        "https://alice:secret@example.com/docs/open?safe=topic"
        "&oauth_token=tok_123&signature=sig_123&code=oauth_code"
        "#token=fragment-secret"
    )

    assert redacted.startswith("https://example.com/docs/open?")
    assert "alice" not in redacted
    assert "secret" not in redacted
    assert "tok_123" not in redacted
    assert "sig_123" not in redacted
    assert "oauth_code" not in redacted
    assert "fragment-secret" not in redacted
    assert "safe=topic" in redacted
    assert "#token=[redacted]" in redacted
    assert "[redacted]" in redacted


def test_redact_url_never_exposes_file_uri_path() -> None:
    redacted = redact_url("file:///etc/.env")

    assert redacted == "file://[redacted-path]"
    assert "/etc/.env" not in redacted


def test_reviewable_payload_text_redacts_embedded_file_uri() -> None:
    redacted = redact_reviewable_payload_text(
        '{"callbackUrl":"file:///root/.ssh/id_ed25519"}'
    )

    assert "file://[redacted-path]" in redacted
    assert "/root/.ssh/id_ed25519" not in redacted


def test_permission_ui_redacts_nested_file_uri() -> None:
    redacted = sanitize_permission_ui_payload(
        {"params": {"callbackUrl": "file:///workspace/.env"}}
    )

    assert redacted["params"]["callbackUrl"] == "file://[redacted-path]"
    assert "/workspace/.env" not in str(redacted)


def test_redact_json_value_preserves_url_safe_query_after_redaction() -> None:
    value = {
        "target_urls": [
            (
                "https://alice:secret@example.com/docs/open?"
                "oauth_token=tok_123&signature=sig_123&safe=topic"
            )
        ]
    }

    redacted = redact_json_value(value)

    assert redacted == {
        "target_urls": [
            (
                "https://example.com/docs/open?"
                "oauth_token=[redacted]&signature=[redacted]&safe=topic"
            )
        ]
    }


def test_permission_ui_payload_redacts_secret_key_variants_recursively() -> None:
    payload = {
        "db_password": "hunter2",
        "dbPassword": "hunter3",
        "x-api-key": "plain-api-key",
        "x-auth-token": "plain-auth-token",
        "authHeader": "plain-auth-header",
        "proxyAuth": "plain-proxy-auth-short",
        "clientSecret": "plain-client-secret",
        "accessKey": "plain-access-key",
        "awsAccessKeyId": "plain-aws-access-key",
        "signingKey": "plain-signing-key",
        "headers": {
            "Authorization": "plain-authorization",
            "proxy-authorization": "plain-proxy-auth",
            "cookie": "session=plain-cookie",
            "set-cookie": "session=plain-set-cookie",
        },
        "nested": {"credentials": {"username": "alice", "value": "plain"}},
        "query": "public search terms",
    }

    redacted = sanitize_permission_ui_payload(payload)

    assert redacted["query"] == "public search terms"
    assert redacted["db_password"] == PERMISSION_UI_REDACTED
    assert redacted["dbPassword"] == PERMISSION_UI_REDACTED
    assert redacted["x-api-key"] == PERMISSION_UI_REDACTED
    assert redacted["x-auth-token"] == PERMISSION_UI_REDACTED
    assert redacted["authHeader"] == PERMISSION_UI_REDACTED
    assert redacted["proxyAuth"] == PERMISSION_UI_REDACTED
    assert redacted["clientSecret"] == PERMISSION_UI_REDACTED
    assert redacted["accessKey"] == PERMISSION_UI_REDACTED
    assert redacted["awsAccessKeyId"] == PERMISSION_UI_REDACTED
    assert redacted["signingKey"] == PERMISSION_UI_REDACTED
    assert set(redacted["headers"].values()) == {PERMISSION_UI_REDACTED}
    assert redacted["nested"]["credentials"] == PERMISSION_UI_REDACTED
    serialized = json.dumps(redacted)
    for secret in (
        "hunter2",
        "hunter3",
        "plain-api-key",
        "plain-auth-token",
        "plain-auth-header",
        "plain-proxy-auth-short",
        "plain-client-secret",
        "plain-access-key",
        "plain-aws-access-key",
        "plain-signing-key",
        "plain-authorization",
        "plain-proxy-auth",
        "plain-cookie",
        "plain-set-cookie",
    ):
        assert secret not in serialized


def test_permission_ui_payload_redacts_url_credentials_and_query_secrets() -> None:
    redacted = sanitize_permission_ui_payload(
        {
            "url": (
                "https://alice:secret@example.com/open?safe=topic"
                "&access_token=plain-token&signature=plain-signature"
            )
        }
    )

    assert redacted["url"].startswith("https://example.com/open?")
    assert "safe=topic" in redacted["url"]
    assert "alice" not in redacted["url"]
    assert "plain-token" not in redacted["url"]
    assert "plain-signature" not in redacted["url"]
    assert PERMISSION_UI_REDACTED in redacted["url"]


def test_permission_ui_payload_omits_secret_context_at_every_depth() -> None:
    redacted = sanitize_permission_ui_payload(
        {
            "secret_context": "outer-secret",
            "nested": {"secretContext": "inner-secret", "safe": "visible"},
        }
    )

    assert redacted == {"nested": {"safe": "visible"}}


def test_permission_ui_payload_marks_all_structural_limits() -> None:
    redacted = sanitize_permission_ui_payload(
        {
            "deep": {"level": {"value": "hidden-by-depth"}},
            "many": [1, 2, 3, 4],
            "long": "x" * 80,
        },
        max_depth=2,
        max_items=3,
        max_string_length=32,
        max_total_bytes=1024,
    )

    assert redacted["deep"]["level"] == PERMISSION_UI_TRUNCATED
    assert redacted["many"][-1] == PERMISSION_UI_TRUNCATED
    assert redacted["long"].endswith(PERMISSION_UI_TRUNCATED)


def test_permission_ui_payload_total_size_includes_truncation_marker() -> None:
    redacted = sanitize_permission_ui_payload(
        {"items": [f"value-{index}-{'x' * 40}" for index in range(20)]},
        max_total_bytes=192,
    )
    serialized = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))

    assert len(serialized.encode("utf-8")) <= 192
    assert PERMISSION_UI_TRUNCATED in serialized


def test_permission_ui_payload_handles_cycles_and_non_finite_numbers() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    assert sanitize_permission_ui_payload(cyclic) == {"self": PERMISSION_UI_UNAVAILABLE}
    assert sanitize_permission_ui_payload(
        {"nan": float("nan"), "positive": float("inf"), "negative": -float("inf")}
    ) == {
        "nan": PERMISSION_UI_UNAVAILABLE,
        "positive": PERMISSION_UI_UNAVAILABLE,
        "negative": PERMISSION_UI_UNAVAILABLE,
    }


def test_permission_ui_payload_never_stringifies_unsupported_values() -> None:
    class SecretObject:
        def __str__(self) -> str:
            return "RAW-CUSTOM-SECRET"

    class ThrowingString:
        def __str__(self) -> str:
            raise RuntimeError("must not escape")

    redacted = sanitize_permission_ui_payload(
        {
            "safe": "visible",
            "opaque": SecretObject(),
            "throwing": ThrowingString(),
            "bytes": b"RAW-BYTE-SECRET",
            "buffer": bytearray(b"RAW-BUFFER-SECRET"),
            "set": {"RAW-SET-SECRET"},
        }
    )

    assert redacted == {
        "safe": "visible",
        "opaque": PERMISSION_UI_UNAVAILABLE,
        "throwing": PERMISSION_UI_UNAVAILABLE,
        "bytes": PERMISSION_UI_UNAVAILABLE,
        "buffer": PERMISSION_UI_UNAVAILABLE,
        "set": PERMISSION_UI_UNAVAILABLE,
    }
    serialized = json.dumps(redacted)
    assert "RAW-" not in serialized


def test_permission_ui_payload_never_stringifies_non_string_mapping_keys() -> None:
    class SecretKey:
        def __str__(self) -> str:
            return "RAW-CUSTOM-KEY-SECRET"

    redacted = sanitize_permission_ui_payload(
        {SecretKey(): "hidden", 7: "also-hidden", "safe": True}
    )

    assert redacted == {
        "[UNAVAILABLE]": PERMISSION_UI_UNAVAILABLE,
        "[UNAVAILABLE] (2)": PERMISSION_UI_UNAVAILABLE,
        "safe": True,
    }
    assert "RAW-CUSTOM-KEY-SECRET" not in json.dumps(redacted)
