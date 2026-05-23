from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse

from jiuwenclaw.jiuwen_core_patch import (
    PatchOpenAIModelClient,
    _is_huawei_maas_api_base,
    _normalize_huawei_image_size,
    _strip_b64_data_uri_prefix,
)


def test_is_huawei_maas_api_base() -> None:
    assert _is_huawei_maas_api_base("https://api.modelarts-maas.com/v1")
    assert not _is_huawei_maas_api_base("https://api.openai.com/v1")


def test_strip_b64_data_uri_prefix() -> None:
    raw = "data:image/jpg;base64,QUJD"
    assert _strip_b64_data_uri_prefix(raw) == "QUJD"
    assert _strip_b64_data_uri_prefix("QUJD") == "QUJD"


def test_normalize_huawei_image_size() -> None:
    assert _normalize_huawei_image_size("2048*2048") == "2048x2048"
    assert _normalize_huawei_image_size("1024×1024") == "1024x1024"


@pytest.mark.asyncio
async def test_patched_generate_image_strips_huawei_b64_prefix(monkeypatch) -> None:
    async def _fake_generate_image(self, messages, **kwargs):
        _ = self, messages, kwargs
        return ImageGenerationResponse(
            model="qwen-image",
            images=[],
            images_base64=["data:image/png;base64,QUJD"],
            created=1,
        )

    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch._ORIGINAL_GENERATE_IMAGE",
        _fake_generate_image,
    )

    client = PatchOpenAIModelClient.__new__(PatchOpenAIModelClient)
    client.model_client_config = SimpleNamespace(
        api_base="https://api.modelarts-maas.com/v1",
        client_provider="OpenAI",
    )
    client.model_config = SimpleNamespace(model_name="qwen-image")

    result = await PatchOpenAIModelClient.generate_image(
        client,
        [],
        size="2048*2048",
        n=2,
        watermark=True,
        seed=7,
    )
    assert result.images_base64 == ["QUJD"]
