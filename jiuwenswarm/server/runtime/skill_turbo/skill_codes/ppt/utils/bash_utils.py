from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.skill_turbo.plan_node import AbortError, PlanNode

# bash 工具偶发只回 content="Exit code 1\n..."，不带 exit_code 字段；
# 若不识别会把失败当成功（P9 convert 假完成）。
_EXIT_CODE_RE = re.compile(r"(?im)^\s*Exit code\s+(\d+)\s*$")


class BashExecError(RuntimeError):
    """bash 命令执行失败（required=True 时抛出）。"""


@dataclass(frozen=True)
class BashResult:
    exit_code: int
    stdout: str
    stderr: str
    raw: str


_EXIT_CODE_PREFIX_RE = re.compile(r"^Exit code (\d+)\n?", re.IGNORECASE)


def _parse_exit_code_from_text(text: str) -> int | None:
    """从 ``Exit code N`` 文本前缀解析退出码。"""
    m = _EXIT_CODE_PREFIX_RE.match(text.strip())
    return int(m.group(1)) if m else None


def quote_path(path: str) -> str:
    normalized = path.replace('"', '\\"')
    return f'"{normalized}"'


def cli_path(subcommand: str, pptx_root: str) -> str:
    """构建 cli.js 子命令路径。

    prod 版 cli.js 位于 {pptx_root}/packages/cli/dist/cli.js。
    """
    if not str(pptx_root or "").strip():
        raise BashExecError("缺少 pptx_root，无法定位新版 pptx-craft CLI")
    cli_dir = Path(pptx_root) / "packages" / "cli" / "dist"
    cli = cli_dir / "cli.js"
    if not cli.is_file():
        raise BashExecError(f"cli.js 不存在: {cli}")
    return f"node {quote_path(str(cli))} {subcommand}"


def _exit_code_from_text(*parts: str) -> int | None:
    for part in parts:
        if not part:
            continue
        for line in str(part).splitlines():
            matched = _EXIT_CODE_RE.match(line.strip())
            if matched:
                return int(matched.group(1))
    return None


def _coerce_exit_code(
    exit_code: int,
    *,
    stdout: str = "",
    stderr: str = "",
    success: bool | None = None,
    error: str = "",
) -> int:
    if exit_code != 0:
        return exit_code
    if success is False:
        return 1
    if error.strip():
        return 1
    if "[ERROR]" in stdout or "[ERROR]" in stderr:
        return 1
    inferred = _exit_code_from_text(stdout, stderr)
    if inferred is not None and inferred != 0:
        return inferred
    return exit_code


def normalize_tool_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result

    data = result.data if hasattr(result, "data") else None
    if isinstance(data, dict):
        for key in ("stdout", "output", "result", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(data, ensure_ascii=False)

    if isinstance(result, dict):
        for key in ("stdout", "output", "result", "content"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("stdout", "output", "result", "content"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        return json.dumps(result, ensure_ascii=False)

    error = result.error if hasattr(result, "error") else None
    if isinstance(error, str) and error.strip():
        return f"[ERROR]: {error}"
    return str(result)


def parse_bash_payload(text: str) -> BashResult:
    stripped = text.strip()
    if stripped.startswith("[ERROR]"):
        return BashResult(exit_code=1, stdout="", stderr=stripped, raw=text)

    parsed_exit = _parse_exit_code_from_text(stripped)
    if parsed_exit is not None:
        stdout = _EXIT_CODE_PREFIX_RE.sub("", stripped, count=1)
        return BashResult(exit_code=parsed_exit, stdout=stdout, stderr="", raw=text)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        exit_code = _coerce_exit_code(0, stdout=stripped)
        return BashResult(exit_code=exit_code, stdout=stripped, stderr="", raw=text)

    if not isinstance(payload, dict):
        exit_code = _coerce_exit_code(0, stdout=stripped)
        return BashResult(exit_code=exit_code, stdout=stripped, stderr="", raw=text)

    exit_code = int(payload.get("exit_code", 0) or 0)
    stdout = str(payload.get("stdout") or payload.get("content") or "")
    stderr = str(payload.get("stderr") or "")
    success = payload.get("success")
    error = str(payload.get("error") or "")
    exit_code = _coerce_exit_code(
        exit_code,
        stdout=stdout,
        stderr=stderr,
        success=success if isinstance(success, bool) else None,
        error=error,
    )
    return BashResult(exit_code=exit_code, stdout=stdout, stderr=stderr, raw=text)


def _extract_bash_result(raw: Any) -> BashResult | None:
    """从 raw tool 返回值直接提取 exit_code/stdout/stderr。

    node.call_tool 返回的 raw 对象结构为 raw.result.data.exit_code（嵌套两层），
    normalize_tool_text 只提取 stdout 纯文本会丢失 exit_code。
    此函数兼容多种嵌套结构，提取失败时返回 None 由调用方 fallback。
    """
    data: dict[str, Any] | None = None
    success: bool | None = None

    if hasattr(raw, "data") and isinstance(raw.data, dict):
        data = raw.data
        if hasattr(raw, "success"):
            success = raw.success
    elif isinstance(raw, dict):
        if "data" in raw and isinstance(raw["data"], dict):
            data = raw["data"]
            success = raw.get("success")
        elif "result" in raw:
            inner = raw["result"]
            if hasattr(inner, "data") and isinstance(inner.data, dict):
                data = inner.data
                if hasattr(inner, "success"):
                    success = inner.success
            elif isinstance(inner, dict) and "data" in inner and isinstance(inner["data"], dict):
                data = inner["data"]
                success = inner.get("success")

    if data is None:
        return None

    exit_code = int(data.get("exit_code", 0) or 0)
    # 与 normalize_tool_text 保持一致：stdout 缺失时依次回退到 content/output/result
    stdout = ""
    for key in ("stdout", "content", "output", "result"):
        value = data.get(key)
        if isinstance(value, str) and value:
            stdout = value
            break
    stderr = str(data.get("stderr") or "")
    success = data.get("success")
    if not isinstance(success, bool) and hasattr(raw, "success"):
        # 直接属性访问；禁止 getattr（builtin skill_code AST 校验）
        raw_success = raw.success
        success = raw_success if isinstance(raw_success, bool) else None
    error = str(data.get("error") or "")
    if not error and hasattr(raw, "error"):
        raw_error = raw.error
        if isinstance(raw_error, str):
            error = raw_error
    exit_code = _coerce_exit_code(
        exit_code,
        stdout=stdout,
        stderr=stderr,
        success=success if isinstance(success, bool) else None,
        error=error,
    )
    return BashResult(exit_code=exit_code, stdout=stdout, stderr=stderr, raw=str(raw))


async def run_bash(
    node: PlanNode,
    command: str,
    *,
    timeout_seconds: int = 300,
    required: bool = True,
    workdir: str | None = None,
) -> BashResult:
    last_error: Exception | None = None

    tool_attempts: list[tuple[str, dict[str, Any]]] = [
        (
            "bash",
            _build_bash_kwargs(command, timeout_seconds, workdir, with_timeout=True),
        ),
        (
            "bash",
            _build_bash_kwargs(command, timeout_seconds, workdir, with_timeout=False),
        ),
    ]

    for tool_name, kwargs in tool_attempts:
        try:
            raw = await node.call_tool(tool_name, **kwargs)
            parsed = _extract_bash_result(raw) or parse_bash_payload(
                normalize_tool_text(raw)
            )
            if parsed.exit_code != 0 and required:
                detail = parsed.stderr or parsed.stdout or parsed.raw
                raise BashExecError(
                    f"命令执行失败 (exit={parsed.exit_code}): {command}\n{detail}"
                )
            return parsed
        except BashExecError:
            raise
        except ValueError:
            continue
        except Exception as exc:
            if isinstance(exc, AbortError):
                raise
            last_error = exc
            continue

    if last_error is not None:
        raise BashExecError(f"无法执行 bash 命令: {command}") from last_error
    raise BashExecError(f"未注册 bash 工具，无法执行: {command}")


def _build_bash_kwargs(
    command: str,
    timeout_seconds: int,
    workdir: str | None,
    *,
    with_timeout: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"command": command}
    if with_timeout:
        kwargs["timeout"] = timeout_seconds
    if workdir:
        kwargs["workdir"] = workdir
    return kwargs


def combined_output(result: BashResult) -> str:
    return f"{result.stdout}\n{result.stderr}".strip()
