"""Agent-facing Symphony tools."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.extensions.registry import ExtensionRegistry
from jiuwenswarm.symphony.config import load_symphony_config
from jiuwenswarm.agents.harness.common.tools.symphony_status_events import (
    emit_symphony_status,
)

logger = logging.getLogger(__name__)


class SymphonyToolkit:
    """Expose Symphony extension RPC methods as model-callable tools."""

    @staticmethod
    def _resolve_timeout_s(default_s: float = 1800.0) -> float:
        return default_s

    async def _call_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.info(
            "[SymphonyToolkit] calling RPC: method=%s params_keys=%s",
            method,
            sorted(params),
        )
        try:
            registry = ExtensionRegistry.get_instance()
        except RuntimeError as exc:
            return {
                "success": False,
                "detail": f"Symphony extension RPC unavailable: {method}: {exc}",
            }

        handler = registry.get_rpc_handler(method)
        if handler is None:
            return {
                "success": False,
                "detail": f"Symphony extension RPC unavailable: {method}: handler not registered",
            }

        timeout_s = self._resolve_timeout_s()
        try:
            result = handler(params, request=None)
            payload = await asyncio.wait_for(
                result if inspect.isawaitable(result) else _return_value(result),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return {"success": False, "detail": f"{method}: timeout after {timeout_s}s"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Symphony RPC failed: %s", method)
            return {"success": False, "detail": f"{method}: {exc}"}

        return payload if isinstance(payload, dict) else {"success": True, "result": payload}

    async def score_status(self) -> dict[str, Any]:
        return await self._call_rpc("symphony.score_status", {})

    async def refresh_score(self) -> dict[str, Any]:
        return await self._call_rpc("symphony.build_score", {})

    @staticmethod
    def _score_needs_build(status: dict[str, Any]) -> bool:
        if not bool(status.get("exists", False)):
            return True
        if bool(status.get("stale", False)):
            return True
        for key in ("added_count", "changed_count", "removed_count"):
            try:
                if int(status.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _score_summary_markdown(
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> str:
        lines = ["## Symphony score", ""]
        if status.get("success"):
            state = "stale" if status.get("stale") else "fresh"
            if not status.get("exists"):
                state = "missing"
            reason = str(status.get("reason") or "").strip()
            lines.append(f"- Status: `{state}`")
            if reason:
                lines.append(f"- Detail: {reason}")
            for key, label in (
                ("added_count", "Added"),
                ("changed_count", "Changed"),
                ("removed_count", "Removed"),
            ):
                value = status.get(key)
                if value not in (None, ""):
                    lines.append(f"- {label}: `{value}`")
        else:
            detail = str(status.get("detail") or "score status failed").strip()
            lines.append("- Status: `failed`")
            lines.append(f"- Detail: {detail}")
        if update is not None:
            update_state = "succeeded" if update.get("success") else "failed"
            lines.append(f"- Update: `{update_state}`")
            detail = str(update.get("detail") or update.get("reason") or "").strip()
            if detail:
                lines.append(f"- Update detail: {detail}")
        else:
            lines.append("- Update: `not required`")
        return "\n".join(lines)

    @classmethod
    def _attach_display_payload(
        cls,
        payload: dict[str, Any],
        status: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> None:
        score_markdown = cls._score_summary_markdown(status, update)
        presentation = payload.get("presentation")
        presentation_markdown = (
            presentation.get("markdown") if isinstance(presentation, dict) else None
        )
        presentation_mermaid = (
            presentation.get("mermaid") if isinstance(presentation, dict) else None
        )
        rendered = (
            payload.get("content")
            or payload.get("markdown")
            or presentation_markdown
        )
        mermaid = payload.get("mermaid") or presentation_mermaid
        if isinstance(mermaid, str) and mermaid.strip():
            payload.setdefault("mermaid", mermaid.strip())
        if not isinstance(rendered, str):
            rendered = ""
        rendered = rendered.strip()
        combined = f"{score_markdown}\n\n{rendered}".strip() if rendered else score_markdown
        payload["content"] = combined
        payload["markdown"] = combined
        payload["summary"] = combined
        payload.setdefault("display_format", "markdown")
        payload.setdefault("direct_display", True)

    @staticmethod
    def _failure_detail(payload: dict[str, Any], fallback: str) -> str:
        return str(
            payload.get("detail")
            or payload.get("reason")
            or payload.get("error")
            or fallback
        ).strip()

    async def plan(self, query: str, mode: str | None = None) -> dict[str, Any]:
        await emit_symphony_status(
            "checking_score",
            "正在读取 Symphony 总谱...",
        )
        status = await self.score_status()
        if not status.get("success"):
            detail = self._failure_detail(status, "symphony.score_status failed")
            await emit_symphony_status(
                "checking_score",
                f"Symphony 总谱读取失败: {detail}",
                status="failed",
                detail=detail,
            )
            return {
                "success": False,
                "detail": "symphony.score_status failed before planning",
                "score_status": status,
            }
        update: dict[str, Any] | None = None
        if status.get("success") and self._score_needs_build(status):
            await emit_symphony_status(
                "building_score",
                "正在构建 Symphony 总谱...",
            )
            update = await self.refresh_score()
            if not update.get("success"):
                detail = self._failure_detail(update, "symphony.build_score failed")
                await emit_symphony_status(
                    "building_score",
                    f"Symphony 总谱构建失败: {detail}",
                    status="failed",
                    detail=detail,
                )
                return {
                    "success": False,
                    "detail": "symphony.build_score failed before planning",
                    "score_status": status,
                    "score_build": update,
                }

        params: dict[str, Any] = {
            "query": str(query or "").strip(),
        }
        mode_text = str(mode or "").strip()
        if mode_text:
            params["mode"] = mode_text
        await emit_symphony_status(
            "planning",
            "正在编排技能执行乐谱...",
        )
        payload = await self._call_rpc("symphony.plan", params)
        if isinstance(payload, dict):
            if payload.get("success") is False:
                detail = self._failure_detail(payload, "symphony.plan failed")
                await emit_symphony_status(
                    "planning",
                    f"Symphony 技能执行计划生成失败: {detail}",
                    status="failed",
                    detail=detail,
                )
            payload.setdefault("score_status", status)
            if update is not None:
                payload.setdefault("score_build", update)
            self._attach_display_payload(payload, status, update)
        return payload

    @staticmethod
    def is_enabled() -> bool:
        try:
            return bool(load_symphony_config().enabled)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load Symphony config; tools disabled: %s", exc)
            return False

    def get_tools(self) -> list[Tool]:
        if not self.is_enabled():
            return []

        def make_tool(
            name: str,
            description: str,
            input_params: dict[str, Any],
            func: Callable[..., Any],
        ) -> Tool:
            card = ToolCard(
                id=name,
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                "symphony_read_score",
                "Read whether the Symphony score exists or is stale before composing skill execution.",
                {"type": "object", "properties": {}},
                self.score_status,
            ),
            make_tool(
                "symphony_refresh_score",
                "Extract installed skill features and refresh the Symphony score.",
                {"type": "object", "properties": {}},
                self.refresh_score,
            ),
            make_tool(
                "symphony_compose_score",
                (
                    "MUST call before answering when the user says to use skill(s) "
                    "or 技能 to complete a task. Do not manually list skill names "
                    "or choose a skill chain before calling this tool. This is the "
                    "Symphony entrypoint: it reads the score, "
                    "refreshes stale or missing scores, then composes the skill execution graph. "
                    "After it returns, present its content/markdown result directly to the user; "
                    "do not call individual skill tools just to manually recreate the plan. "
                    "Do not use for ordinary single-step tasks that do not ask to use installed skills."
                ),
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The original user task to complete with currently installed skills.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["fast"],
                            "description": (
                                "Optional planning mode. The current Symphony runtime "
                                "supports fast planning only."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                self.plan,
            ),
        ]


async def _return_value(value: Any) -> Any:
    return value
