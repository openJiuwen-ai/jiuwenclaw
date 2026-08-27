# -*- coding: utf-8 -*-
"""只读代码安全审查 Tool。

本模块只提供分析意见，不参与工具调用的放行、确认或拒绝。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import ToolCard


_MAX_FILE_BYTES = 512 * 1024
_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_CHECKS = (
    (
        "secret",
        "high",
        "openai-api-key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        "发现疑似 API Key，请删除硬编码值并改用环境变量或密钥管理服务。",
    ),
    (
        "secret",
        "high",
        "aws-access-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "发现疑似 AWS Access Key，请确认是否泄露并立即轮换真实凭据。",
    ),
    (
        "secret",
        "critical",
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "发现私钥内容，请移出代码和普通文件，并使用专用密钥存储。",
    ),
    (
        "command",
        "critical",
        "destructive-root-command",
        re.compile(r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*)\s+(?:/|~)(?:\s|$)"),
        "发现针对根目录或用户目录的递归强制删除命令。",
    ),
    (
        "command",
        "high",
        "download-and-execute",
        re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh|powershell|pwsh)\b", re.IGNORECASE),
        "发现下载后直接执行，建议先保存、校验来源和内容，再在隔离环境运行。",
    ),
    (
        "code",
        "high",
        "dynamic-code-execution",
        re.compile(r"\b(?:eval|exec)\s*\("),
        "发现动态代码执行，请改用结构化解析或显式允许列表。",
    ),
    (
        "code",
        "high",
        "shell-injection",
        re.compile(r"\bshell\s*=\s*True\b"),
        "发现 shell=True，请改用参数列表并避免命令解释器解析外部输入。",
    ),
)


def _redacted_snippet(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    matched = text[start:end]
    if len(matched) > 8:
        line = line.replace(matched, f"{matched[:4]}…{matched[-2:]}")
    return line[:200]


def review_text(text: str, location: str = "content") -> dict[str, Any]:
    """审查文本并返回纯建议性报告。"""
    findings: list[dict[str, Any]] = []
    for category, severity, check_id, pattern, recommendation in _CHECKS:
        for match in pattern.finditer(text):
            findings.append(
                {
                    "check_id": check_id,
                    "category": category,
                    "severity": severity,
                    "location": location,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "evidence": _redacted_snippet(text, match.start(), match.end()),
                    "recommendation": recommendation,
                }
            )
    risk_level = max(
        (finding["severity"] for finding in findings),
        key=lambda severity: _SEVERITY_RANK[severity],
        default="none",
    )
    recommendations = list(dict.fromkeys(finding["recommendation"] for finding in findings))
    return {
        "risk_level": risk_level,
        "findings": findings,
        "recommendations": recommendations,
        "safer_alternative": (
            "按整改建议移除危险行为或敏感数据后重新审查。"
            if findings
            else "未发现内置检查项；执行陌生代码时仍建议使用隔离环境。"
        ),
    }


def _resolve_workspace_file(path: str, workspace_root: str) -> Path:
    root = Path(workspace_root).resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("只允许审查工作区内的文件") from exc
    if not candidate.is_file():
        raise FileNotFoundError("指定文件不存在")
    return candidate


def _read_file(path: Path) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(_MAX_FILE_BYTES)
    return content, path.stat().st_size > _MAX_FILE_BYTES


class SecurityReviewTool(Tool):
    """对代码、命令或工作区文件进行只读安全审查。"""

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                id="security_review",
                name="security_review",
                description=(
                    "只读审查代码、命令或工作区文件中的安全风险，返回风险发现、证据与整改建议。"
                    "本工具不决定真实工具调用能否执行；执行授权由运行时 Rail 独立处理。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "待审查的代码、命令或文本，与 path 二选一",
                        },
                        "path": {
                            "type": "string",
                            "description": "工作区内待审查文件，与 content 二选一",
                        },
                        "language": {
                            "type": "string",
                            "description": "可选语言提示，仅用于描述审查上下文",
                        },
                    },
                },
            )
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        payload = inputs or {}
        content = payload.get("content")
        path = payload.get("path")
        if bool(content) == bool(path):
            return {"success": False, "error": "content 与 path 必须且只能提供一个"}

        try:
            location = "content"
            truncated = False
            if path:
                from openjiuwen.core.sys_operation.cwd import get_cwd
                from openjiuwen.core.sys_operation.cwd import get_workspace

                workspace_root = get_workspace() or get_cwd()
                file_path = _resolve_workspace_file(str(path), workspace_root)
                text, truncated = await asyncio.to_thread(_read_file, file_path)
                location = str(path)
            else:
                text = str(content)
            report = review_text(text, location)
        except (FileNotFoundError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        except OSError:
            return {"success": False, "error": "文件读取失败"}

        if truncated:
            report["recommendations"].append("文件超过 512KB，仅审查了前部内容。")
        return {"success": True, "source": "coding-guard-review", **report}

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> Any:
        yield await self.invoke(inputs, **kwargs)


__all__ = ["SecurityReviewTool"]
