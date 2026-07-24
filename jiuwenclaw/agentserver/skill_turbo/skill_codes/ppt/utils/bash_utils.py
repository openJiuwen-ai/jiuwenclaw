from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode


class BashExecError(RuntimeError):
    """bash 命令执行失败（required=True 时抛出）。"""


@dataclass(frozen=True)
class BashResult:
    exit_code: int
    stdout: str
    stderr: str
    raw: str


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

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return BashResult(exit_code=0, stdout=stripped, stderr="", raw=text)

    if not isinstance(payload, dict):
        return BashResult(exit_code=0, stdout=stripped, stderr="", raw=text)

    exit_code = int(payload.get("exit_code", 0) or 0)
    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    if exit_code == 0 and "[ERROR]" in stdout:
        exit_code = 1
    return BashResult(exit_code=exit_code, stdout=stdout, stderr=stderr, raw=text)


def _extract_bash_result(raw: Any) -> BashResult | None:
    """从 raw tool 返回值直接提取 exit_code/stdout/stderr。

    node.call_tool 返回的 raw 对象结构为 raw.result.data.exit_code（嵌套两层），
    normalize_tool_text 只提取 stdout 纯文本会丢失 exit_code。
    此函数兼容多种嵌套结构，提取失败时返回 None 由调用方 fallback。
    """
    data: dict[str, Any] | None = None

    if hasattr(raw, "data") and isinstance(raw.data, dict):
        data = raw.data
    elif isinstance(raw, dict):
        if "data" in raw and isinstance(raw["data"], dict):
            data = raw["data"]
        elif "result" in raw:
            inner = raw["result"]
            if hasattr(inner, "data") and isinstance(inner.data, dict):
                data = inner.data
            elif isinstance(inner, dict) and "data" in inner and isinstance(inner["data"], dict):
                data = inner["data"]

    if data is None:
        return None

    exit_code = int(data.get("exit_code", 0) or 0)
    stdout = str(data.get("stdout") or "")
    stderr = str(data.get("stderr") or "")
    if exit_code == 0 and "[ERROR]" in stdout:
        exit_code = 1
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
