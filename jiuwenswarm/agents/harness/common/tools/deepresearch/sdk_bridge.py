"""Standalone, stdlib-only bridge to the isolated DeepSearch report styler."""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import sys
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import BinaryIO

try:
    from .path_safety import is_direct_regular_file, private_mode_is_compatible
except ImportError:  # Executed as a standalone script by runtime.py.
    from path_safety import is_direct_regular_file, private_mode_is_compatible

BRIDGE_INPUT_MAX_BYTES = 64 * 1024 * 1024
BRIDGE_ARCHIVE_MAX_BYTES = 128 * 1024 * 1024
BRIDGE_JSON_MAX_DEPTH = 64
BRIDGE_JSON_MAX_NODES = 100_000
BRIDGE_JSON_MAX_CONTAINER = 20_000
BRIDGE_ZIP_MAX_MEMBERS = 1024
BRIDGE_ZIP_MEMBER_MAX_BYTES = 64 * 1024 * 1024
BRIDGE_ZIP_TOTAL_MAX_BYTES = 128 * 1024 * 1024
_TLS_KEYS = frozenset({"LLM_SSL_VERIFY", "TOOL_SSL_VERIFY"})


class BridgeError(RuntimeError):
    """Safe bridge failure carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _validate_json_tree(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    seen: set[int] = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > BRIDGE_JSON_MAX_NODES or depth > BRIDGE_JSON_MAX_DEPTH:
            raise BridgeError("bridge_request_invalid")
        if item is None or isinstance(item, (str, int, bool)):
            continue
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise BridgeError("bridge_request_invalid")
            continue
        if not isinstance(item, (dict, list)):
            raise BridgeError("bridge_request_invalid")
        identity = id(item)
        if identity in seen:
            raise BridgeError("bridge_request_invalid")
        seen.add(identity)
        if len(item) > BRIDGE_JSON_MAX_CONTAINER:
            raise BridgeError("bridge_request_invalid")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise BridgeError("bridge_request_invalid")
            stack.extend((child, depth + 1) for child in item.values())
        else:
            stack.extend((child, depth + 1) for child in item)


def read_request(stream: BinaryIO) -> dict[str, object]:
    """Read one bounded, versioned JSON request without importing the SDK."""
    collected = bytearray()
    while True:
        chunk = stream.read(min(64 * 1024, BRIDGE_INPUT_MAX_BYTES + 1 - len(collected)))
        if not chunk:
            break
        collected.extend(chunk)
        if len(collected) > BRIDGE_INPUT_MAX_BYTES:
            raise BridgeError("bridge_input_too_large")
    payload = bytes(collected)
    try:
        request = json.loads(payload.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError) as exc:
        raise BridgeError("bridge_request_invalid") from exc
    _validate_json_tree(request)
    if not isinstance(request, dict) or set(request) != {
        "schema_version",
        "final_result",
        "llm_config",
        "tls",
    }:
        raise BridgeError("bridge_request_invalid")
    schema_version = request["schema_version"]
    is_exact_schema_version_type = (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version.__class__ is int
    )
    if not is_exact_schema_version_type or schema_version != 1:
        raise BridgeError("bridge_request_invalid")
    if not isinstance(request["final_result"], dict) or not isinstance(request["llm_config"], dict):
        raise BridgeError("bridge_request_invalid")
    tls = request["tls"]
    invalid_tls = (
        not isinstance(tls, dict)
        or set(tls) != _TLS_KEYS
        or any(not isinstance(value, bool) for value in tls.values())
    )
    if invalid_tls:
        raise BridgeError("bridge_request_invalid")
    return request


def _decode_archive(convert_content: object) -> bytes:
    if not isinstance(convert_content, str):
        raise BridgeError("bridge_archive_invalid")
    if len(convert_content) > ((BRIDGE_ARCHIVE_MAX_BYTES + 2) // 3) * 4:
        raise BridgeError("bridge_archive_too_large")
    try:
        archive = base64.b64decode(convert_content, validate=True)
    except ValueError as exc:
        raise BridgeError("bridge_archive_invalid") from exc
    if len(archive) > BRIDGE_ARCHIVE_MAX_BYTES:
        raise BridgeError("bridge_archive_too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) > BRIDGE_ZIP_MAX_MEMBERS:
                raise BridgeError("bridge_archive_invalid")
            total = 0
            for member in members:
                if member.file_size > BRIDGE_ZIP_MEMBER_MAX_BYTES:
                    raise BridgeError("bridge_archive_invalid")
                total += member.file_size
                if total > BRIDGE_ZIP_TOTAL_MAX_BYTES:
                    raise BridgeError("bridge_archive_invalid")
            if bundle.testzip() is not None:
                raise BridgeError("bridge_archive_invalid")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise BridgeError("bridge_archive_invalid") from exc
    return archive


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _same_unchanged_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_file(left, right) and left.st_ctime_ns == right.st_ctime_ns


def write_convert_content(output: str | Path, convert_content: object) -> None:
    """Write a validated archive only through a precreated private file."""
    target = Path(output)
    if not target.is_absolute():
        raise BridgeError("bridge_output_invalid")
    try:
        before = target.lstat()
    except OSError as exc:
        raise BridgeError("bridge_output_invalid") from exc
    invalid_target = (
        not is_direct_regular_file(before)
        or before.st_nlink != 1
        or not private_mode_is_compatible(before, 0o600)
        or before.st_size != 0
    )
    if invalid_target:
        raise BridgeError("bridge_output_invalid")
    archive = _decode_archive(convert_content)
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, mode=0o600)
    except OSError as exc:
        raise BridgeError("bridge_output_invalid") from exc
    try:
        opened = os.fstat(descriptor)
        invalid_open_file = (
            not is_direct_regular_file(opened)
            or opened.st_nlink != 1
            or not private_mode_is_compatible(opened, 0o600)
            or opened.st_size != 0
            or not _same_unchanged_file(before, opened)
        )
        if invalid_open_file:
            raise BridgeError("bridge_output_invalid")
        os.ftruncate(descriptor, 0)
        written = 0
        while written < len(archive):
            count = os.write(descriptor, archive[written:])
            if count <= 0:
                raise BridgeError("bridge_output_write_failed")
            written += count
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        invalid_written_file = (
            not is_direct_regular_file(after)
            or not _same_file(opened, after)
            or after.st_size != len(archive)
            or after.st_nlink != 1
            or not private_mode_is_compatible(after, 0o600)
        )
        if invalid_written_file:
            raise BridgeError("bridge_output_invalid")
    except OSError as exc:
        raise BridgeError("bridge_output_write_failed") from exc
    finally:
        os.close(descriptor)


def _sdk_llm_config(raw: dict[str, object]) -> dict[str, object]:
    # JSON transports the secret as text; the SDK accepts a mutable bytearray.
    result = dict(raw)
    for key, value in list(result.items()):
        if isinstance(value, dict):
            nested = dict(value)
            if "api_key" in nested:
                if not isinstance(nested["api_key"], str):
                    raise BridgeError("bridge_request_invalid")
                nested["api_key"] = bytearray(nested["api_key"], "utf-8")
            result[key] = nested
    if "api_key" in result:
        if not isinstance(result["api_key"], str):
            raise BridgeError("bridge_request_invalid")
        result["api_key"] = bytearray(result["api_key"], "utf-8")
    return result


async def stylize_request(request: dict[str, object], output: str | Path) -> dict[str, object]:
    """Apply child-only TLS, lazily call the SDK, and persist its ZIP result."""
    tls = request["tls"]
    if __debug__ and not isinstance(tls, dict):
        raise AssertionError()
    previous = {key: os.environ.get(key) for key in _TLS_KEYS}
    try:
        for key in _TLS_KEYS:
            os.environ[key] = "true" if tls[key] else "false"
        with redirect_stdout(sys.stderr):
            from openjiuwen_deepsearch.algorithm.report_style.service import stylize_report
            from openjiuwen_deepsearch.framework.openjiuwen.llm.report_style_runtime import report_style_llm_context

            llm_config = _sdk_llm_config(request["llm_config"])
            async with report_style_llm_context(llm_config) as llm:
                result = await stylize_report(request["final_result"], llm)
        write_convert_content(output, getattr(result, "convert_content", None))
        return {
            "schema_version": 1,
            "status": "completed",
            "output_path": str(Path(output)),
            "style_applied": bool(getattr(result, "style_applied", False)),
            "style_status": str(getattr(result, "style_status", "fallback")),
        }
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("sdk_stylize_failed", "SDK report styling failed") from exc
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _safe_error(exc: BaseException) -> dict[str, object]:
    code = exc.code if isinstance(exc, BridgeError) else "bridge_internal_error"
    return {"schema_version": 1, "status": "error", "error_code": code, "error": code}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("operation", choices=("stylize-report",))
    parser.add_argument("--config-stdin", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
        request = read_request(sys.stdin.buffer)
        result = asyncio.run(stylize_request(request, args.output))
        status = 0
    except BaseException as exc:  # always one safe protocol frame on stdout
        result = _safe_error(exc)
        status = 1
    protocol_logger = logging.Logger(f"{__name__}.protocol", level=logging.INFO)
    protocol_handler = logging.StreamHandler(sys.stdout)
    protocol_handler.setFormatter(logging.Formatter("%(message)s"))
    protocol_logger.addHandler(protocol_handler)
    protocol_logger.propagate = False
    try:
        protocol_logger.info(
            "%s", json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        )
    finally:
        protocol_handler.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
