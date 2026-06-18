# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests
from openjiuwen.core.foundation.llm import Model, UserMessage
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.agentserver.tools.image_gen_post_watermark import (
    PostWatermarkConfig,
    apply_post_watermark_to_file,
    load_post_watermark_config,
)
from jiuwenclaw.agentserver.tools.multimodal_config import apply_image_gen_model_config_from_yaml
from jiuwenclaw.agentserver.tools.ssl_config import get_requests_verify
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_effective_request_workspace_dir,
)
from jiuwenclaw.config import get_config
from jiuwenclaw.utils import get_agent_workspace_dir, get_config_file


logger = logging.getLogger(__name__)

_DEFAULT_SIZE_DASHSCOPE = "1920*1080"
_DEFAULT_SIZE_OPENAI = "1024x1024"
_DEFAULT_SIZE_HUAWEI_MAAS = "1024x1024"
_SIZE_DIMENSIONS_RE = re.compile(r"^(\d+)\s*[*xX×]\s*(\d+)$")
_OPENAI_STANDARD_SIZES = frozenset({(1024, 1024), (1792, 1024), (1024, 1792)})
_DASHSCOPE_PROVIDER_NAMES = frozenset({"dashscope"})
_HUAWEI_MAAS_API_MARKERS = ("modelarts-maas.com", "modelarts-maas.cn")
_OUTPUT_SUBDIR = "generated_images"


def _is_dashscope_provider(provider: str) -> bool:
    return provider.strip().lower() in _DASHSCOPE_PROVIDER_NAMES


def _is_huawei_maas_api_base(api_base: str) -> bool:
    base = (api_base or "").lower()
    if any(marker in base for marker in _HUAWEI_MAAS_API_MARKERS):
        return True
    return "modelarts" in base and "maas" in base


def _is_huawei_maas_config(provider: str, api_base: str) -> bool:
    """Huawei MaaS image API is OpenAI-path compatible; detect by api_base."""
    _ = provider
    return _is_huawei_maas_api_base(api_base)


def _default_size_for_provider(provider: str, api_base: str = "") -> str:
    if _is_dashscope_provider(provider):
        return _DEFAULT_SIZE_DASHSCOPE
    if _is_huawei_maas_config(provider, api_base):
        return _DEFAULT_SIZE_HUAWEI_MAAS
    return _DEFAULT_SIZE_OPENAI


def _map_to_openai_compatible_size(width: int, height: int) -> str:
    """Map arbitrary dimensions to common OpenAI-compatible size enums."""
    if (width, height) in _OPENAI_STANDARD_SIZES:
        return f"{width}x{height}"
    if width == height:
        return _DEFAULT_SIZE_OPENAI
    if width > height:
        return "1792x1024"
    return "1024x1792"


def normalize_image_size(
    size: str | None,
    provider: str,
    *,
    api_base: str = "",
) -> str:
    """Normalize size for DashScope (*), Huawei MaaS (pass-through x), or OpenAI enums."""
    raw = str(size or "").strip()
    if not raw:
        return _default_size_for_provider(provider, api_base)

    match = _SIZE_DIMENSIONS_RE.match(raw)
    if not match:
        return raw

    width, height = int(match.group(1)), int(match.group(2))
    if _is_dashscope_provider(provider):
        return f"{width}*{height}"
    if _is_huawei_maas_config(provider, api_base):
        return f"{width}x{height}"
    return _map_to_openai_compatible_size(width, height)


def _parse_optional_seed(inputs: dict[str, Any]) -> int | None:
    """Parse seed from tool inputs; ignore invalid values with a debug log."""
    if "seed" not in inputs:
        return None
    raw = inputs["seed"]
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.debug("[text_to_image] ignoring invalid seed value: %r", raw)
        return None


def _build_image_gen_kwargs(
    provider: str,
    inputs: dict[str, Any],
    *,
    size: str,
    n: int,
    api_base: str = "",
) -> dict[str, Any]:
    gen_kwargs: dict[str, Any] = {
        "size": normalize_image_size(size, provider, api_base=api_base),
        "n": n,
    }
    if _is_huawei_maas_config(provider, api_base):
        # Huawei MaaS: single image, b64_json only; supports seed/watermark.
        gen_kwargs["n"] = 1
        gen_kwargs["response_format"] = "b64_json"
        gen_kwargs["watermark"] = bool(inputs.get("watermark", False))
        seed = _parse_optional_seed(inputs)
        if seed is not None:
            gen_kwargs["seed"] = seed
        return gen_kwargs

    if not _is_dashscope_provider(provider):
        return gen_kwargs

    gen_kwargs["prompt_extend"] = bool(inputs.get("prompt_extend", True))
    gen_kwargs["watermark"] = bool(inputs.get("watermark", False))
    negative_prompt = inputs.get("negative_prompt")
    if negative_prompt is not None and str(negative_prompt).strip():
        gen_kwargs["negative_prompt"] = str(negative_prompt).strip()
    seed = _parse_optional_seed(inputs)
    if seed is not None:
        gen_kwargs["seed"] = seed
    return gen_kwargs


def _get_image_gen_credentials() -> tuple[str, str, str, str]:
    api_key = os.environ.get("IMAGE_GEN_API_KEY", "").strip()
    api_base = os.environ.get("IMAGE_GEN_API_BASE", "").strip()
    model_name = os.environ.get("IMAGE_GEN_MODEL_NAME", "").strip()
    provider = os.environ.get("IMAGE_GEN_PROVIDER", "").strip() or "DashScope"
    return api_key, api_base, model_name, provider


def _make_missing_key_error() -> str:
    return (
        "[ERROR]: IMAGE_GEN_API_KEY is not configured for text-to-image. "
        f"Set models.image_gen.model_client_config.api_key in {get_config_file()} "
        "or configure image_gen_api_key via Web/CLI config."
    )


def _make_incomplete_config_error() -> str:
    return (
        "[ERROR]: IMAGE_GEN_API_BASE and IMAGE_GEN_MODEL_NAME are required when "
        "IMAGE_GEN_API_KEY is set for text-to-image."
    )


def _sanitize_filename_part(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", value.strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_")
    if not cleaned:
        return "image"
    return cleaned[:max_len]


def _output_dir() -> Path:
    """Resolve save directory: effective_project_dir/generated_images, else agent workspace."""
    request_workspace = get_effective_request_workspace_dir()
    if request_workspace and str(request_workspace).strip():
        out = Path(str(request_workspace).strip()) / _OUTPUT_SUBDIR
    else:
        out = get_agent_workspace_dir() / _OUTPUT_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def _download_image(url: str, dest: Path, timeout: int = 120) -> None:
    response = requests.get(url, timeout=timeout, verify=get_requests_verify())
    response.raise_for_status()
    dest.write_bytes(response.content)


def _write_base64_image(data: str, dest: Path) -> None:
    payload = data.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    dest.write_bytes(base64.b64decode(payload))


def _append_url_or_b64(items: list[dict[str, Any]], url: Any, b64: Any = None) -> None:
    url_s = str(url or "").strip()
    b64_s = str(b64 or "").strip()
    if url_s or b64_s:
        items.append({"url": url_s, "b64_json": b64_s})


def _extend_from_image_list(items: list[dict[str, Any]], block: Any) -> None:
    if not isinstance(block, list):
        return
    for entry in block:
        if isinstance(entry, str):
            text = entry.strip()
            if text.startswith("data:") and "," in text:
                _append_url_or_b64(items, None, text.split(",", 1)[1])
            elif text.startswith("http://") or text.startswith("https://"):
                _append_url_or_b64(items, text)
            elif text:
                _append_url_or_b64(items, None, text)
        elif isinstance(entry, dict):
            url = entry.get("url") or entry.get("image")
            b64 = entry.get("b64_json") or entry.get("b64") or entry.get("image_base64")
            _append_url_or_b64(items, url, b64)
        else:
            url = getattr(entry, "url", None) or getattr(entry, "image", None)
            b64 = (
                getattr(entry, "b64_json", None)
                or getattr(entry, "b64", None)
                or getattr(entry, "image_base64", None)
            )
            _append_url_or_b64(items, url, b64)


def _try_model_dump(response: Any) -> dict[str, Any] | None:
    """Best-effort pydantic model_dump; returns None when unavailable or failing."""
    dump_fn = getattr(response, "model_dump", None)
    if not callable(dump_fn):
        return None
    try:
        dumped = dump_fn()
    except (TypeError, ValueError, AttributeError) as exc:
        logger.debug("[text_to_image] model_dump failed: %s", exc)
        return None
    return dumped if isinstance(dumped, dict) else None


def _extend_from_nested_output(items: list[dict[str, Any]], node: Any) -> None:
    if node is None:
        return
    if isinstance(node, list):
        _extend_from_image_list(items, node)
        return
    if isinstance(node, dict):
        if node.get("url") or node.get("b64_json") or node.get("image"):
            items.append(
                {
                    "url": str(node.get("url") or node.get("image") or "").strip(),
                    "b64_json": str(
                        node.get("b64_json") or node.get("b64") or node.get("image_base64") or ""
                    ).strip(),
                }
            )
        for key in ("results", "data", "images", "choices"):
            _extend_from_nested_output(items, node.get(key))
        output = node.get("output")
        if isinstance(output, dict):
            _extend_from_nested_output(items, output.get("results"))
            for choice in output.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message") or {}
                if isinstance(message, dict):
                    _extend_from_nested_output(items, message.get("content"))
        return
    message = getattr(node, "output", None)
    if message is not None and message is not node:
        _extend_from_nested_output(items, message)


def _iter_response_image_items(response: Any) -> list[dict[str, Any]]:
    """Normalize openjiuwen ImageGenerationResponse and vendor payloads to url/b64 items."""
    items: list[dict[str, Any]] = []

    # openjiuwen ImageGenerationResponse: images / images_base64 are List[str]
    _extend_from_image_list(items, getattr(response, "images", None))
    _extend_from_image_list(items, getattr(response, "images_base64", None))

    data = getattr(response, "data", None)
    if isinstance(data, list):
        _extend_from_image_list(items, data)

    if not items:
        _extend_from_nested_output(items, response)

    if not items:
        dumped = _try_model_dump(response)
        if dumped:
            _extend_from_image_list(items, dumped.get("images"))
            _extend_from_image_list(items, dumped.get("images_base64"))
            _extend_from_image_list(items, dumped.get("data"))
            _extend_from_nested_output(items, dumped)

    return items


def _describe_response_for_error(response: Any) -> str:
    dumped = _try_model_dump(response)
    if dumped is not None:
        return str(dumped)[:500]
    return repr(response)[:500]


def _save_generated_images(
    response: Any,
    *,
    prompt: str,
    watermark_config: PostWatermarkConfig | None = None,
) -> list[Path]:
    items = _iter_response_image_items(response)
    if not items:
        raise ValueError(
            "Image generation API returned no image data. "
            f"Response preview: {_describe_response_for_error(response)}"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = _sanitize_filename_part(prompt)
    saved: list[Path] = []
    out_dir = _output_dir()
    wm_cfg = watermark_config if watermark_config is not None else load_post_watermark_config(
        get_config()
    )

    for idx, item in enumerate(items, start=1):
        url = str(item.get("url") or "").strip()
        b64 = str(item.get("b64_json") or item.get("b64") or "").strip()
        suffix = f"{stamp}_{slug}_{idx}"
        dest = out_dir / f"{suffix}.png"

        if url:
            _download_image(url, dest)
            if dest.stat().st_size == 0:
                raise ValueError(f"Downloaded empty image from URL: {url}")
            apply_post_watermark_to_file(dest, wm_cfg)
            saved.append(dest.resolve())
            continue
        if b64:
            _write_base64_image(b64, dest)
            apply_post_watermark_to_file(dest, wm_cfg)
            saved.append(dest.resolve())
            continue

    if not saved:
        raise ValueError("Image generation response did not contain url or base64 payloads.")
    return saved


def _build_image_gen_model(api_key: str, api_base: str, model_name: str, provider: str) -> Model:
    model_client_config = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
        verify_ssl=False,
        timeout=1800.0,
    )
    model_request_config = ModelRequestConfig(model=model_name)
    return Model(
        model_client_config=model_client_config,
        model_config=model_request_config,
    )


async def _text_to_image_impl(inputs: dict[str, Any]) -> str:
    apply_image_gen_model_config_from_yaml(get_config())
    api_key, api_base, model_name, provider = _get_image_gen_credentials()
    if not api_key:
        return _make_missing_key_error()
    if not api_base or not model_name:
        return _make_incomplete_config_error()

    prompt = str(inputs.get("prompt", "") or "").strip()
    if not prompt:
        return "[ERROR]: prompt cannot be empty."

    size_raw = str(inputs.get("size") or "").strip()
    n_raw = inputs.get("n", 1)
    try:
        n = max(1, min(int(n_raw), 4))
    except (TypeError, ValueError):
        n = 1

    gen_kwargs = _build_image_gen_kwargs(
        provider,
        inputs,
        size=size_raw or _default_size_for_provider(provider, api_base),
        n=n,
        api_base=api_base,
    )

    model = _build_image_gen_model(api_key, api_base, model_name, provider)
    logger.info(
        "[text_to_image] model=%s provider=%s api_base=%s size=%s n=%s",
        model_name,
        provider,
        api_base,
        gen_kwargs.get("size"),
        n,
    )
    response = await model.generate_image(
        messages=[UserMessage(content=prompt)],
        model=model_name,
        **gen_kwargs,
    )
    paths = _save_generated_images(
        response,
        prompt=prompt,
        watermark_config=load_post_watermark_config(get_config()),
    )
    lines = [
        f"Generated {len(paths)} image(s) from prompt.",
        "Local file paths (use for attachments or send_file):",
    ]
    lines.extend(f"- {path}" for path in paths)
    return "\n".join(lines)


@tool(
    name="text_to_image",
    description=(
        "根据文本提示生成图片（文生图）。用户要求创作、绘制或根据描述生成图片时使用。"
        "输入：prompt（必填文本描述）；可选 size"
        "（DashScope: 1920*1080；OpenAI/华为 MaaS: 1024x1024，* 与 x 均可）、"
        "negative_prompt、n（图片数量；DashScope 独有 prompt_extend；"
        "华为 MaaS/OpenAI 兼容 watermark、seed）。"
        "保存后可配置后处理水印（默认右下角半透明「AI Generated」，"
        "见 config models.image_gen.post_watermark）。"
        "输出为本地文件路径。有 effective_project_dir 时保存到其 generated_images/；"
        "否则保存到 agent 工作区 generated_images。"
    ),
)
async def text_to_image(inputs: dict[str, Any], **kwargs) -> str:
    _ = kwargs
    try:
        return await _text_to_image_impl(inputs or {})
    except Exception as exc:
        logger.warning("[text_to_image] failed: %s", exc, exc_info=True)
        return f"[ERROR]: text-to-image failed: {exc}"
