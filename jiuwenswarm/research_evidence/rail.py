# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenSwarm Rail that injects selected, traceable research evidence."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.research_evidence.ledger import ResourceEvent, ResourceLedger
from jiuwenswarm.research_evidence.schemas import EvidenceKind
from jiuwenswarm.research_evidence.selector import EvidenceSelector, SelectorConfig
from jiuwenswarm.research_evidence.store import EvidenceStore
from jiuwenswarm.research_evidence.text import estimate_tokens


class ResearchEvidenceRail(DeepAgentRail):
    """Inject a budgeted evidence context and record lifecycle resource usage.

    The rail is fail-open for availability: malformed or unavailable evidence
    never prevents a model/tool call.  Failures are written to the audit ledger,
    while the prompt receives a conservative instruction not to invent missing
    evidence.  The selector itself remains deterministic and provider-free.
    """

    priority = 96
    SECTION_NAME = "research_evidence"
    SECTION_PRIORITY = 82

    def __init__(
        self,
        project_root: str,
        *,
        enabled: bool = False,
        store_directory: str = ".jiuwen/research_evidence",
        token_budget: int = 2048,
        min_reliability: float = 0.35,
        required_kinds: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__()
        self.enabled = bool(enabled)
        self.project_root = Path(project_root or ".").expanduser().resolve()
        store_path = Path(store_directory)
        if not store_path.is_absolute():
            store_path = self.project_root / store_path
        self.store = EvidenceStore(store_path)
        self.ledger = ResourceLedger(store_path / "resource_events.jsonl")
        parsed_kinds: list[EvidenceKind] = []
        for value in required_kinds or []:
            try:
                parsed_kinds.append(EvidenceKind(str(value)))
            except ValueError:
                continue
        self.selector = EvidenceSelector(
            SelectorConfig(
                token_budget=token_budget,
                min_reliability=min_reliability,
                required_kinds=tuple(parsed_kinds),
            )
        )
        self.system_prompt_builder = None
        self._run_id = uuid.uuid4().hex[:12]

    def init(self, agent: Any) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled or self.system_prompt_builder is None:
            return
        self.system_prompt_builder.remove_section(self.SECTION_NAME)
        key = self._event_key(ctx, "model")
        self.ledger.start(key)
        query = _latest_query(getattr(ctx.inputs, "messages", None))
        stage = str(ctx.extra.get("research_stage") or "general")
        try:
            evidence = self.store.list_evidence()
            claims = self.store.list_claims()
            required_claims = [
                claim.claim_id for claim in claims if claim.status in {"draft", "active", "accepted"}
            ]
            result = self.selector.select(
                query,
                evidence,
                required_claims=required_claims,
            )
            content = render_evidence_context(result, stage=stage)
            self.store.append_event(
                {
                    "event": "context_selection",
                    "run_id": self._run_id,
                    "stage": stage,
                    "query": query,
                    "selection": result.to_dict(),
                }
            )
            ctx.extra["research_evidence_selection"] = result.to_dict()
        except Exception as exc:
            content = (
                "## Research evidence unavailable\n\n"
                "The evidence store could not be read for this call. Do not invent "
                "citations, measurements, or experimental conclusions. Explicitly mark "
                "unsupported statements as hypotheses."
            )
            self.ledger.append(
                ResourceEvent(
                    run_id=self._run_id,
                    stage=stage,
                    event="selection_error",
                    success=False,
                    metadata={"error_type": type(exc).__name__, "message": str(exc)},
                )
            )
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={"cn": content, "en": content},
                priority=self.SECTION_PRIORITY,
            )
        )
        ctx.extra["research_evidence_prompt_tokens"] = estimate_tokens(content)
        ctx.extra["research_evidence_event_key"] = key

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled:
            return
        usage = _extract_usage(getattr(ctx.inputs, "response", None))
        self.ledger.append(
            ResourceEvent(
                run_id=self._run_id,
                stage=str(ctx.extra.get("research_stage") or "general"),
                event="model_call",
                duration_seconds=self.ledger.elapsed(
                    str(ctx.extra.get("research_evidence_event_key") or self._event_key(ctx, "model"))
                ),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                model=usage["model"],
                success=ctx.exception is None,
                metadata={
                    "selected_prompt_tokens_estimate": int(
                        ctx.extra.get("research_evidence_prompt_tokens") or 0
                    )
                },
            )
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled or not isinstance(ctx.inputs, ToolCallInputs):
            return
        key = self._event_key(ctx, f"tool:{ctx.inputs.tool_name}")
        self.ledger.start(key)
        ctx.extra["research_evidence_tool_event_key"] = key

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled or not isinstance(ctx.inputs, ToolCallInputs):
            return
        key = str(
            ctx.extra.get("research_evidence_tool_event_key")
            or self._event_key(ctx, f"tool:{ctx.inputs.tool_name}")
        )
        self.ledger.append(
            ResourceEvent(
                run_id=self._run_id,
                stage=str(ctx.extra.get("research_stage") or "general"),
                event="tool_call",
                duration_seconds=self.ledger.elapsed(key),
                tool=str(ctx.inputs.tool_name or ""),
                success=ctx.exception is None,
                metadata={"result_type": type(ctx.inputs.tool_result).__name__},
            )
        )

    @staticmethod
    def _event_key(ctx: AgentCallbackContext, suffix: str) -> str:
        return f"{id(ctx)}:{suffix}"

    @staticmethod
    def _render_context(result: Any, *, stage: str) -> str:
        """Compatibility wrapper for callers that used the former private helper."""

        return render_evidence_context(result, stage=stage)


def render_evidence_context(result: Any, *, stage: str) -> str:
    """Render a selection as the same evidence contract used by the native Rail.

    Keeping this helper public lets batch workflows and interactive JiuwenSwarm
    agents share one prompt contract rather than maintaining subtly different
    citation and negative-result policies.
    """

    lines = [
        "## Evidence-gated research context",
        "",
        f"Research stage: `{stage}`. Evidence budget: "
        f"{result.used_tokens}/{result.token_budget} estimated tokens.",
        "Use only the evidence cards below for factual citations, measurements, "
        "and empirical conclusions. Cite cards as `[EVID:<id>]`. Preserve conflicts "
        "and negative results; if support is absent, label the statement as a hypothesis.",
        "",
    ]
    if not result.selected:
        lines.append("No evidence card was selected. Do not state empirical findings.")
    for item in result.selected:
        relation_bits: list[str] = []
        if item.supports:
            relation_bits.append("supports=" + ",".join(item.supports))
        if item.contradicts:
            relation_bits.append("contradicts=" + ",".join(item.contradicts))
        relation = f"; {'; '.join(relation_bits)}" if relation_bits else ""
        lines.extend(
            [
                f"### [EVID:{item.evidence_id}] {item.kind.value}",
                f"Source: `{item.source}`; reliability={item.reliability:.2f}{relation}",
                item.summary or item.content,
                "",
            ]
        )
    if result.uncovered_claims:
        lines.append(
            "Uncovered claims (must not be asserted as established): "
            + ", ".join(result.uncovered_claims)
        )
    return "\n".join(lines).strip()


def _latest_query(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        role = _value(message, "role")
        if str(role or "").lower() not in {"user", "human"}:
            continue
        text = _content_text(_value(message, "content"))
        if text:
            return text
    return _content_text(_value(messages[-1], "content")) if messages else ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        values: list[str] = []
        for item in content:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                values.append(str(item.get("text") or item.get("content") or ""))
            else:
                values.append(str(_value(item, "text") or _value(item, "content") or ""))
        return " ".join(value.strip() for value in values if value.strip())
    return str(content or "").strip()


def _extract_usage(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage") or _value(response, "usage_metadata") or {}
    input_tokens = _first_int(
        usage,
        "input_tokens",
        "prompt_tokens",
        "input_token_count",
    )
    output_tokens = _first_int(
        usage,
        "output_tokens",
        "completion_tokens",
        "output_token_count",
    )
    model = str(_value(response, "model") or _value(response, "model_name") or "")
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "model": model}


def _first_int(value: Any, *keys: str) -> int:
    for key in keys:
        candidate = _value(value, key)
        try:
            if candidate is not None:
                return max(0, int(candidate))
        except (TypeError, ValueError):
            continue
    return 0


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


__all__ = ["ResearchEvidenceRail", "render_evidence_context"]
