# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified ``skill_step`` facade tool.

The facade collapses the previous ``skill_step_create / _complete /
_complete_batch / _insert / _remove / _list`` surface into a single
``skill_step`` tool that the model sees, with an ``action`` discriminator
selecting the underlying operation.

The class still inherits from :class:`TodoToolkit` and writes to
``skill_step.md`` under the session directory, so existing call sites that
rely on ``load_tasks`` / ``clear_tasks`` / ``resolve_todo_path`` (e.g.
``SkillComplianceRail``) keep working unchanged. Only :meth:`get_tools`
is overridden to expose the unified facade tool instead of the six
``skill_step_*`` tools the parent class would emit.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.agentserver.tools.todo_toolkits import TodoToolkit


class _SkillStepBaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SkillStepCreateInput(_SkillStepBaseInput):
    action: Literal["create"]
    tasks: List[str] = Field(..., min_length=1, description="Step descriptions to create.")


class _SkillStepCompleteInput(_SkillStepBaseInput):
    action: Literal["complete"]
    idx: int = Field(..., ge=1, description="1-based index of the step to complete.")
    result: str = Field("", description="Brief result or outcome.")


class _SkillStepCompleteBatchInput(_SkillStepBaseInput):
    action: Literal["complete_batch"]
    indices: List[int] = Field(
        ...,
        min_length=1,
        description=(
            "1-based indices of completed steps; strictly ascending, contiguous, "
            "and starting at the first open step."
        ),
    )
    results: Optional[List[str]] = Field(
        default=None,
        description="Per-step brief outcomes aligned 1:1 with indices.",
    )


class _SkillStepInsertInput(_SkillStepBaseInput):
    action: Literal["insert"]
    idx: int = Field(..., ge=1, description="1-based index where new steps are inserted.")
    tasks: List[str] = Field(..., min_length=1, description="New step descriptions to insert.")


class _SkillStepRemoveInput(_SkillStepBaseInput):
    action: Literal["remove"]
    idx: int = Field(..., ge=1, description="1-based index of the step to remove.")


class _SkillStepListInput(_SkillStepBaseInput):
    action: Literal["list"]


class SkillStepInput(RootModel[
    Annotated[
        _SkillStepCreateInput
        | _SkillStepCompleteInput
        | _SkillStepCompleteBatchInput
        | _SkillStepInsertInput
        | _SkillStepRemoveInput
        | _SkillStepListInput,
        Field(discriminator="action"),
    ]
]):
    """Discriminated-union input schema for the unified skill_step tool."""


_BACKEND_NAME_REWRITES = {
    "skill_step_complete_batch": 'skill_step(action="complete_batch")',
    "skill_step_complete": 'skill_step(action="complete")',
    "skill_step_create": 'skill_step(action="create")',
    "skill_step_insert": 'skill_step(action="insert")',
    "skill_step_remove": 'skill_step(action="remove")',
    "skill_step_list": 'skill_step(action="list")',
}


class SkillStepToolkit(TodoToolkit):
    """Skill step toolkit with a unified ``skill_step`` facade tool.

    Inherits all storage / locking / TodoOpResult publishing from
    :class:`TodoToolkit`, only changing the persisted filename and tool
    prefix. Overrides :meth:`get_tools` to expose a single ``skill_step``
    tool instead of one tool per action, so the agent sees one tool card
    rather than six.

    The instance methods (``todo_create`` / ``todo_complete`` / etc.) are
    kept available because :class:`SkillComplianceRail`, tests, and a few
    other call sites use them directly to seed or inspect the on-disk
    plan; only the LLM-facing tool surface is collapsed.
    """

    TODO_FILENAME: ClassVar[str] = "skill_step.md"
    TOOL_PREFIX: ClassVar[str] = "skill_step"
    EXPOSE_START: ClassVar[bool] = False
    EXPOSE_COMPLETE_BATCH: ClassVar[bool] = True

    TOOL_NAME: ClassVar[str] = "skill_step"

    async def skill_step(self, **inputs: Any) -> str:
        """Dispatch a skill step action to the underlying backend tool."""
        action = inputs.get("action")
        backend_tool_name, backend_inputs = self._build_backend_call(action, inputs)
        for tool in super().get_tools():
            if tool.card.name == backend_tool_name:
                result = await tool.invoke(
                    backend_inputs,
                    skip_none_value=True,
                    skip_inputs_validate=False,
                )
                return self._rewrite_backend_tool_names(str(result))
        return f"Error: backend tool {backend_tool_name!r} is unavailable."

    @staticmethod
    def _build_backend_call(
        action: Any, inputs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        match action:
            case "create":
                return "skill_step_create", {"tasks": inputs["tasks"]}
            case "complete":
                return "skill_step_complete", {
                    "idx": inputs["idx"],
                    "result": inputs.get("result", ""),
                }
            case "complete_batch":
                backend_inputs: dict[str, Any] = {"indices": inputs["indices"]}
                if inputs.get("results") is not None:
                    backend_inputs["results"] = inputs["results"]
                return "skill_step_complete_batch", backend_inputs
            case "insert":
                return "skill_step_insert", {
                    "idx": inputs["idx"],
                    "tasks": inputs["tasks"],
                }
            case "remove":
                return "skill_step_remove", {"idx": inputs["idx"]}
            case "list":
                return "skill_step_list", {}
            case _:
                return "skill_step_unknown", {}

    @staticmethod
    def _rewrite_backend_tool_names(message: str) -> str:
        for old, new in _BACKEND_NAME_REWRITES.items():
            message = message.replace(old, new)
        return message

    def get_tools(self) -> List[Tool]:
        card = ToolCard(
            name=self.TOOL_NAME,
            description=(
                "Track SKILL execution steps. Use action=create, complete, "
                "complete_batch, insert, remove, or list. This is the only "
                "tool for SKILL step planning and progress updates."
            ),
            input_params=SkillStepInput,
        )
        return [LocalFunction(card=card, func=self.skill_step)]


__all__ = ["SkillStepInput", "SkillStepToolkit"]
