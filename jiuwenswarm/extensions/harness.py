# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Declarative harness capabilities contributed by JiuwenSwarm extensions.

Extensions contribute serializable ``BuiltinToolSpec`` / ``RailSpec`` values;
they never mutate a live agent. JiuwenSwarm can therefore consume the same
contribution in both runtime adapters and the distributed team-spec assembly
path while Agent Core remains the single owner of provider materialization.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.deep_agent_spec import BuiltinToolSpec, RailSpec
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentRail


@dataclass
class ExtensionBuildContext(BuildContext):
    """Runtime information exposed to extension harness contributors.

    The class deliberately extends Agent Core's ``BuildContext`` so the same
    object can be forwarded unchanged to Tool/Rail providers after contribution
    collection. Team assembly uses ``SwarmBuildContext`` (another subclass)
    directly; contributors should therefore depend only on fields they need and
    use ``getattr`` for platform-specific optional values.
    """

    mode: str = "agent"
    sub_mode: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    channel_id: str | None = None
    request_metadata: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


@dataclass
class HarnessContribution:
    """Declarative Tool/Rail specs supplied by one extension."""

    tools: list[BuiltinToolSpec] = field(default_factory=list)
    rails: list[RailSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tools = list(self.tools or [])
        self.rails = list(self.rails or [])
        for index, spec in enumerate(self.tools):
            if not isinstance(spec, BuiltinToolSpec):
                raise TypeError(
                    "HarnessContribution.tools[%d] must be BuiltinToolSpec, got %s"
                    % (index, type(spec).__name__)
                )
            _validate_serializable_spec(spec, field_name=f"tools[{index}]")
        for index, spec in enumerate(self.rails):
            if not isinstance(spec, RailSpec):
                raise TypeError(
                    "HarnessContribution.rails[%d] must be RailSpec, got %s"
                    % (index, type(spec).__name__)
                )
            _validate_serializable_spec(spec, field_name=f"rails[{index}]")


HarnessContributor = Callable[[BuildContext], HarnessContribution | None]
HarnessFailurePolicy = Literal["skip", "raise"]


@dataclass(frozen=True)
class NamedHarnessContribution:
    """A validated contribution paired with its extension registration name."""

    name: str
    contribution: HarnessContribution
    failure_policy: HarnessFailurePolicy = "skip"


@dataclass
class ResolvedHarnessContribution:
    """Live Tool/Rail instances materialized from one contribution."""

    tools: list[Tool | ToolCard] = field(default_factory=list)
    rails: list[AgentRail] = field(default_factory=list)


def _spec_payload(spec: BuiltinToolSpec | RailSpec) -> str:
    try:
        return json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception as exc:  # noqa: BLE001 - normalize Pydantic serializers
        raise TypeError(
            f"{type(spec).__name__} must contain JSON-serializable parameters"
        ) from exc


def _validate_serializable_spec(
    spec: BuiltinToolSpec | RailSpec,
    *,
    field_name: str,
) -> None:
    try:
        _spec_payload(spec)
    except TypeError as exc:
        raise TypeError(
            f"HarnessContribution.{field_name} must be JSON-serializable"
        ) from exc


def _spec_key(spec: BuiltinToolSpec | RailSpec) -> tuple[str, str]:
    return type(spec).__name__, _spec_payload(spec)


def _dedupe_specs(specs: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        key = _spec_key(spec)
        if key in seen:
            continue
        seen.add(key)
        result.append(spec)
    return result


def merge_harness_contributions(
    contributions: Iterable[HarnessContribution],
) -> HarnessContribution:
    """Merge contributions in registration order and remove exact duplicates."""

    tools: list[BuiltinToolSpec] = []
    rails: list[RailSpec] = []
    for contribution in contributions:
        if not isinstance(contribution, HarnessContribution):
            raise TypeError(
                "contributions must contain HarnessContribution values, got %s"
                % type(contribution).__name__
            )
        tools.extend(contribution.tools)
        rails.extend(contribution.rails)
    return HarnessContribution(
        tools=_dedupe_specs(tools),
        rails=_dedupe_specs(rails),
    )


def snapshot_harness_contribution(
    contribution: HarnessContribution,
) -> HarnessContribution:
    """Return an isolated, freshly validated contribution snapshot.

    ``HarnessContribution`` is intentionally a simple mutable dataclass so it
    remains convenient for extension authors. A contributor may therefore
    retain and mutate either the contribution or one of its Pydantic specs
    after construction. Collection must not trust the earlier ``__post_init__``
    validation: copy every spec and run the boundary validation again.
    """

    if not isinstance(contribution, HarnessContribution):
        raise TypeError("contribution must be HarnessContribution")
    try:
        tools = deepcopy(contribution.tools)
        rails = deepcopy(contribution.rails)
    except Exception as exc:  # noqa: BLE001 - normalize extension values
        raise TypeError("HarnessContribution specs must be safely copyable") from exc
    return HarnessContribution(tools=tools, rails=rails)


def merge_harness_specs(
    *,
    tools: Iterable[Any] | None,
    rails: Iterable[Any] | None,
    contribution: HarnessContribution | None,
) -> tuple[list[Any], list[Any]]:
    """Append a contribution to existing spec lists without exact duplicates."""

    merged_tools = list(tools or [])
    merged_rails = list(rails or [])
    if contribution is None:
        return merged_tools, merged_rails
    if not isinstance(contribution, HarnessContribution):
        raise TypeError("contribution must be HarnessContribution or None")

    existing_tool_keys = {
        _spec_key(spec) for spec in merged_tools if isinstance(spec, BuiltinToolSpec)
    }
    for spec in contribution.tools:
        key = _spec_key(spec)
        if key not in existing_tool_keys:
            merged_tools.append(spec)
            existing_tool_keys.add(key)

    existing_rail_keys = {
        _spec_key(spec) for spec in merged_rails if isinstance(spec, RailSpec)
    }
    for spec in contribution.rails:
        key = _spec_key(spec)
        if key not in existing_rail_keys:
            merged_rails.append(spec)
            existing_rail_keys.add(key)
    return merged_tools, merged_rails


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return [value]


def _canonicalize_tool_card_reference(
    card: ToolCard,
    *,
    context: BuildContext,
) -> ToolCard:
    """Validate a pure ToolCard and return the registered canonical card."""
    registered_tool = Runner.resource_mgr.get_tool(card.id) if card.id else None
    if registered_tool is None:
        raise TypeError(
            f"tool provider returned ToolCard '{card.name}' without a "
            "registered Tool instance"
        )
    registered_card = getattr(registered_tool, "card", None)
    if not isinstance(registered_card, ToolCard):
        raise TypeError(f"registered Tool '{card.id}' does not expose a valid ToolCard")
    # ``ToolCard.input_params`` may be a Pydantic ``BaseModel`` class. Such a
    # class is a valid Agent Core value but cannot be serialized in JSON mode.
    # Python-mode dumps preserve the class object and therefore compare the
    # cards using the same runtime semantics Agent Core itself consumes.
    if registered_card.model_dump(mode="python") != card.model_dump(mode="python"):
        raise TypeError(
            f"tool provider returned ToolCard '{card.name}' that does not "
            "match the registered Tool"
        )
    if registered_card.stateless:
        return registered_card

    owner_value = getattr(context, "member_card_id", None)
    if owner_value is not None and not isinstance(owner_value, str):
        raise TypeError("BuildContext.member_card_id must be a string or None")
    owner_id = (owner_value or "").strip()
    if not owner_id:
        raise TypeError(
            f"stateful ToolCard '{registered_card.name}' requires an agent owner id"
        )
    expected_id = AbilityManager.qualify_tool_id(registered_card, owner_id)
    if registered_card.id != expected_id:
        raise TypeError(
            f"stateful ToolCard '{registered_card.name}' must be registered as "
            f"'{expected_id}' for this agent"
        )
    return registered_card


def resolve_harness_contribution(
    contribution: HarnessContribution,
    *,
    context: BuildContext,
) -> ResolvedHarnessContribution:
    """Materialize one contribution through Agent Core's provider registries."""

    if not isinstance(contribution, HarnessContribution):
        raise TypeError("contribution must be HarnessContribution")
    if not isinstance(context, BuildContext):
        raise TypeError("context must be an Agent Core BuildContext")

    rails: list[AgentRail] = []
    tools: list[Tool | ToolCard] = []
    # Resolve Rails first. If a safety Rail cannot be constructed, Tool
    # providers are never invoked, which avoids exposing or side-effectfully
    # registering an unguarded extension tool.
    for spec in contribution.rails:
        built_rails = _as_list(
            spec.build(
                language=context.language,
                workspace=context.workspace,
                context=context,
            )
        )
        if not built_rails:
            raise ValueError(f"rail provider '{spec.type}' returned no resources")
        for rail in built_rails:
            if not isinstance(rail, AgentRail):
                raise TypeError(
                    "rail provider returned %s instead of an Agent Core AgentRail"
                    % type(rail).__name__
                )
            rails.append(rail)
    for spec in contribution.tools:
        built_tools = _as_list(
            spec.build(
                language=context.language,
                context=context,
            )
        )
        if not built_tools:
            raise ValueError(f"tool provider '{spec.type}' returned no resources")
        for tool in built_tools:
            if isinstance(tool, Tool):
                if not isinstance(tool.card, ToolCard):
                    raise TypeError(
                        "tool provider returned a Tool with an invalid card"
                    )
            elif isinstance(tool, ToolCard):
                tool = _canonicalize_tool_card_reference(tool, context=context)
            else:
                raise TypeError(
                    "tool provider returned %s instead of an Agent Core "
                    "Tool or ToolCard" % type(tool).__name__
                )
            tools.append(tool)
    return ResolvedHarnessContribution(tools=tools, rails=rails)


__all__ = [
    "ExtensionBuildContext",
    "HarnessContribution",
    "HarnessContributor",
    "HarnessFailurePolicy",
    "NamedHarnessContribution",
    "ResolvedHarnessContribution",
    "merge_harness_contributions",
    "merge_harness_specs",
    "resolve_harness_contribution",
    "snapshot_harness_contribution",
]
