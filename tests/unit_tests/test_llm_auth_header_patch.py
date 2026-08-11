"""Authorization must survive openjiuwen sanitize for Huawei MaaS Basic auth."""

from __future__ import annotations

from openjiuwen.core.foundation.llm import headers_helper
from openjiuwen.core.foundation.llm.model_clients import openai_model_client as oai_client_mod

from jiuwenswarm.llm_sse_patch import apply_openai_auth_header_patch


def test_build_base_headers_keeps_authorization() -> None:
    apply_openai_auth_header_patch()
    headers = headers_helper.build_base_headers(
        custom_headers={
            "Authorization": "Basic dGVzdA==",
            "X-Extra": "1",
        }
    )
    assert headers.get("Authorization") == "Basic dGVzdA=="
    assert headers.get("X-Extra") == "1"


def test_openai_client_module_binding_keeps_authorization() -> None:
    """from-import binding on the client module must also be patched."""
    apply_openai_auth_header_patch()
    headers = oai_client_mod.build_base_headers(
        custom_headers={"Authorization": "Basic Y2xpZW50"}
    )
    assert headers.get("Authorization") == "Basic Y2xpZW50"


def test_merge_request_headers_keeps_request_authorization() -> None:
    apply_openai_auth_header_patch()
    headers = headers_helper.merge_request_headers(
        {"X-Base": "b"},
        {"Authorization": "Basic cmVx", "X-Req": "r"},
    )
    assert headers.get("Authorization") == "Basic cmVx"
    assert headers.get("X-Base") == "b"
    assert headers.get("X-Req") == "r"
