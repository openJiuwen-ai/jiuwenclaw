# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace

import pytest


VALID_A2UI_RESPONSE = """<a2ui-json>
[
  {
    "beginRendering": {
      "surfaceId": "default",
      "root": "root"
    }
  },
  {
    "surfaceUpdate": {
      "surfaceId": "default",
      "components": [
        {
          "id": "root",
          "component": {
            "Text": {
              "text": {
                "literalString": "Valid repaired UI"
              }
            }
          }
        }
      ]
    }
  }
]
</a2ui-json>"""


INVALID_A2UI_RESPONSE = """Here is the UI.
<a2ui-json>
[
  {
    "type": "beginRendering",
    "version": "0.8"
  }
]
</a2ui-json>"""


@pytest.mark.asyncio
async def test_a2ui_finalizer_repairs_invalid_tagged_response():
    from jiuwenavatar.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer

    prompts = []

    async def repair_call(prompt: str):
        prompts.append(prompt)
        return SimpleNamespace(content=VALID_A2UI_RESPONSE)

    result = await A2UIResponseFinalizer().finalize(
        INVALID_A2UI_RESPONSE,
        user_query="生成库存展示界面",
        request_id="req-finalizer-repair",
        repair_call=repair_call,
    )

    assert result == VALID_A2UI_RESPONSE
    assert len(prompts) == 1
    assert "生成库存展示界面" in prompts[0]
    assert "A2UI 0.8" in prompts[0]


@pytest.mark.asyncio
async def test_a2ui_finalizer_falls_back_after_failed_repairs():
    from jiuwenavatar.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer

    async def repair_call(prompt: str):
        return SimpleNamespace(content=INVALID_A2UI_RESPONSE)

    result = await A2UIResponseFinalizer().finalize(
        INVALID_A2UI_RESPONSE,
        user_query="生成库存展示界面",
        request_id="req-finalizer-fallback",
        repair_call=repair_call,
    )

    assert "<a2ui-json>" not in result
    assert "A2UI 界面生成失败" in result


@pytest.mark.asyncio
async def test_a2ui_finalizer_leaves_plain_text_untouched():
    from jiuwenavatar.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer

    async def repair_call(prompt: str):
        raise AssertionError("plain text must not trigger repair")

    result = await A2UIResponseFinalizer().finalize(
        "plain answer",
        user_query="你好",
        request_id="req-finalizer-plain",
        repair_call=repair_call,
    )

    assert result == "plain answer"
