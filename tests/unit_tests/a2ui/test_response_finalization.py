# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace

import pytest


VALID_A2UI_RESPONSE = """<a2ui-json>
[
  {
    "beginRendering": {
      "surfaceId": "repair-test",
      "root": "root"
    }
  },
  {
    "surfaceUpdate": {
      "surfaceId": "repair-test",
      "components": [
        {
          "id": "root",
          "component": {
            "Text": {
              "text": {
                "literalString": "Repaired"
              }
            }
          }
        }
      ]
    }
  }
]
</a2ui-json>"""


INVALID_A2UI_RESPONSE = """Here is a form.
<a2ui-json>
[
  {
    "type": "beginRendering",
    "version": "0.8"
  }
]
</a2ui-json>"""


@pytest.mark.asyncio
async def test_finalize_a2ui_assistant_content_repairs_invalid_response(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.runtime.response_finalization import finalize_a2ui_assistant_content

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    async def repair_call(prompt: str):
        assert "A2UI 0.8" in prompt
        return SimpleNamespace(content=VALID_A2UI_RESPONSE)

    result = await finalize_a2ui_assistant_content(
        INVALID_A2UI_RESPONSE,
        user_query="generate a form",
        request_id="req-repair",
        repair_call=repair_call,
        a2ui_enabled=True,
    )

    assert result == VALID_A2UI_RESPONSE


@pytest.mark.asyncio
async def test_finalize_a2ui_assistant_content_leaves_valid_response_unchanged(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.runtime.response_finalization import finalize_a2ui_assistant_content

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    async def repair_call(prompt: str):
        raise AssertionError("valid A2UI response must not trigger repair")

    result = await finalize_a2ui_assistant_content(
        VALID_A2UI_RESPONSE,
        user_query="generate a form",
        request_id="req-valid",
        repair_call=repair_call,
        a2ui_enabled=True,
    )

    assert result == VALID_A2UI_RESPONSE


@pytest.mark.asyncio
async def test_finalize_a2ui_assistant_content_skips_when_disabled(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.runtime.response_finalization import finalize_a2ui_assistant_content

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "false")

    async def repair_call(prompt: str):
        raise AssertionError("disabled A2UI must not trigger repair")

    result = await finalize_a2ui_assistant_content(
        INVALID_A2UI_RESPONSE,
        user_query="generate a form",
        request_id="req-disabled",
        repair_call=repair_call,
        a2ui_enabled=False,
    )

    assert result == INVALID_A2UI_RESPONSE


@pytest.mark.asyncio
async def test_finalize_a2ui_assistant_content_retries_without_a2ui_after_failed_repairs(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.runtime.response_finalization import finalize_a2ui_assistant_content

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")
    repair_prompts = []
    retry_queries = []

    async def repair_call(prompt: str):
        repair_prompts.append(prompt)
        return SimpleNamespace(content=INVALID_A2UI_RESPONSE)

    async def retry_without_a2ui_call(query: str):
        retry_queries.append(query)
        return "plain text result"

    result = await finalize_a2ui_assistant_content(
        INVALID_A2UI_RESPONSE,
        user_query="generate a form",
        request_id="req-retry-without-a2ui",
        repair_call=repair_call,
        a2ui_enabled=True,
        retry_without_a2ui_call=retry_without_a2ui_call,
    )

    assert result == "plain text result"
    assert len(repair_prompts) == 2
    assert retry_queries == ["generate a form"]


@pytest.mark.asyncio
async def test_finalize_a2ui_assistant_content_returns_safe_text_when_retry_fails(monkeypatch):
    from jiuwenswarm.server.runtime.a2ui.runtime.response_finalization import finalize_a2ui_assistant_content

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    async def repair_call(prompt: str):
        return SimpleNamespace(content=INVALID_A2UI_RESPONSE)

    async def retry_without_a2ui_call(query: str):
        _ = query
        return None

    result = await finalize_a2ui_assistant_content(
        INVALID_A2UI_RESPONSE,
        user_query="generate a form",
        request_id="req-retry-fails",
        repair_call=repair_call,
        a2ui_enabled=True,
        retry_without_a2ui_call=retry_without_a2ui_call,
    )

    assert "<a2ui-json>" not in result
    assert "[A2UI content could not be rendered]" not in result
    assert "A2UI 界面生成失败" not in result
    assert result == "Here is a form."
