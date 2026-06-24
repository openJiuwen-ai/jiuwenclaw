from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.runner.callback import AbortError

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode


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


_PPT_DIR = Path(__file__).resolve().parent.parent


def cli_path(subcommand: str, pptx_root: str | None = None) -> str:
    """[TEMP-EXTERNAL-SKILL] 构建 cli.js 子命令路径。

    pptx_root 为外部 skill 目录时，scripts 在 {pptx_root}/scripts/ 下；
    pptx_root 为 None 时 fallback 到内置 _PPT_DIR/scripts/（仅兼容旧调用）。
    """
    if pptx_root:
        scripts_dir = Path(pptx_root) / "scripts"
    else:
        scripts_dir = _PPT_DIR / "scripts"
    cli = scripts_dir / "cli.js"
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
            parsed = parse_bash_payload(normalize_tool_text(raw))
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