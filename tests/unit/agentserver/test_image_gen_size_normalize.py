from __future__ import annotations

from jiuwenclaw.agentserver.tools.image_gen_tools import (
    _build_image_gen_kwargs,
    _parse_optional_seed,
    normalize_image_size,
)


def test_normalize_dashscope_keeps_star_separator() -> None:
    assert normalize_image_size("1920*1080", "DashScope") == "1920*1080"
    assert normalize_image_size("1920x1080", "DashScope") == "1920*1080"


def test_normalize_openai_converts_star_to_standard_size() -> None:
    assert normalize_image_size("1920*1080", "OpenAI") == "1792x1024"
    assert normalize_image_size("1080*1920", "OpenAI") == "1024x1792"
    assert normalize_image_size("1024*1024", "OpenAI") == "1024x1024"


def test_normalize_openai_keeps_valid_standard_size() -> None:
    assert normalize_image_size("1024x1024", "OpenAI") == "1024x1024"
    assert normalize_image_size("1792x1024", "OpenRouter") == "1792x1024"


def test_normalize_defaults_by_provider() -> None:
    assert normalize_image_size(None, "DashScope") == "1920*1080"
    assert normalize_image_size("", "OpenAI") == "1024x1024"
    assert (
        normalize_image_size(
            None,
            "OpenAI",
            api_base="https://api.modelarts-maas.com/v1",
        )
        == "1024x1024"
    )


def test_normalize_huawei_maas_passes_through_custom_size() -> None:
    api_base = "https://api.modelarts-maas.com/v1"
    assert (
        normalize_image_size("2048*2048", "OpenAI", api_base=api_base)
        == "2048x2048"
    )
    assert (
        normalize_image_size("2560x1440", "OpenAI", api_base=api_base)
        == "2560x1440"
    )


def test_build_kwargs_omits_dashscope_fields_for_openai() -> None:
    kwargs = _build_image_gen_kwargs(
        "OpenAI",
        {
            "prompt_extend": True,
            "watermark": True,
            "negative_prompt": "blur",
            "seed": 42,
        },
        size="1920*1080",
        n=2,
    )
    assert kwargs == {"size": "1792x1024", "n": 2}


def test_build_kwargs_includes_dashscope_fields() -> None:
    kwargs = _build_image_gen_kwargs(
        "DashScope",
        {"negative_prompt": "blur", "seed": "7"},
        size="1664x928",
        n=1,
    )
    assert kwargs["size"] == "1664*928"
    assert kwargs["negative_prompt"] == "blur"
    assert kwargs["seed"] == 7
    assert kwargs["prompt_extend"] is True


def test_parse_optional_seed_valid_and_invalid() -> None:
    assert _parse_optional_seed({"seed": 42}) == 42
    assert _parse_optional_seed({"seed": "7"}) == 7
    assert _parse_optional_seed({}) is None
    assert _parse_optional_seed({"seed": "not-a-number"}) is None


def test_build_kwargs_huawei_maas_forces_b64_and_single_image() -> None:
    kwargs = _build_image_gen_kwargs(
        "OpenAI",
        {"watermark": True, "seed": 42},
        size="1664*2496",
        n=3,
        api_base="https://api.modelarts-maas.com/v1",
    )
    assert kwargs["size"] == "1664x2496"
    assert kwargs["n"] == 1
    assert kwargs["response_format"] == "b64_json"
    assert kwargs["seed"] == 42
    assert "prompt_extend" not in kwargs
    assert "negative_prompt" not in kwargs
