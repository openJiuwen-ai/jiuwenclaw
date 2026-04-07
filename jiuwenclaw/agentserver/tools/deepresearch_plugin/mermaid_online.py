from __future__ import annotations

import base64
import logging
from pathlib import Path

import requests

from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_common import (
    clean_mermaid_code,
    save_failed_mermaid_source,
)


logger = logging.getLogger(__name__)


def _valid_mermaid_response(
    response: requests.Response,
    *,
    output_format: str,
) -> bool:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code != 200 or not response.content:
        return False
    if output_format == "svg":
        return "svg" in content_type or response.text.lstrip().startswith("<svg")
    return "image/" in content_type


def _error_excerpt(response: requests.Response) -> str:
    content_type = (response.headers.get("Content-Type") or "").lower()
    excerpt = f"status={response.status_code}\ncontent-type={content_type}\n"
    try:
        if "text/" in content_type or "json" in content_type or "xml" in content_type:
            excerpt += response.text[:2000]
        else:
            excerpt += f"binary response, {len(response.content)} bytes"
    except Exception:
        excerpt += "unable to decode response body"
    return excerpt


def _write_response(output_file: Path, response: requests.Response) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(response.content)


def render_mermaid_online(
    code: str,
    output_path: str | Path,
    *,
    output_format: str,
    debug_base_path: Path | None = None,
) -> bool:
    cleaned_code = clean_mermaid_code(code)
    output_file = Path(output_path)
    output_format = output_format.lower()
    last_error = ""

    try:
        encoded = base64.urlsafe_b64encode(cleaned_code.encode("utf-8")).decode("utf-8")
        url = (
            f"https://mermaid.ink/svg/{encoded}"
            if output_format == "svg"
            else f"https://mermaid.ink/img/{encoded}?bgColor=!white&type=png"
        )
        response = requests.get(
            url,
            timeout=30,
            headers={"Accept": "image/svg+xml,image/png,image/*;q=0.9,*/*;q=0.8"},
        )
        if _valid_mermaid_response(response, output_format=output_format):
            _write_response(output_file, response)
            return True
        last_error = "mermaid.ink\n" + _error_excerpt(response)
        logger.debug(
            "mermaid.ink render failed for %s: status=%s content-type=%s",
            output_format,
            response.status_code,
            response.headers.get("Content-Type"),
        )
    except Exception as exc:
        last_error = f"mermaid.ink exception\n{exc}"
        logger.debug("mermaid.ink raised while rendering Mermaid %s: %s", output_format, exc)

    try:
        url = f"https://kroki.io/mermaid/{output_format}"
        response = requests.post(
            url,
            data=cleaned_code.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Accept": "image/svg+xml,image/png,image/*;q=0.9,*/*;q=0.8",
            },
            timeout=30,
        )
        if _valid_mermaid_response(response, output_format=output_format):
            _write_response(output_file, response)
            return True
        last_error += "\n\nkroki\n" + _error_excerpt(response)
        logger.debug(
            "kroki render failed for %s: status=%s content-type=%s",
            output_format,
            response.status_code,
            response.headers.get("Content-Type"),
        )
    except Exception as exc:
        last_error += f"\n\nkroki exception\n{exc}"
        logger.warning("kroki raised while rendering Mermaid %s: %s", output_format, exc)

    save_failed_mermaid_source(
        cleaned_code,
        debug_base_path or output_file,
        extra_text=last_error,
    )
    logger.warning("Online Mermaid rendering failed for %s.", output_format)
    return False
