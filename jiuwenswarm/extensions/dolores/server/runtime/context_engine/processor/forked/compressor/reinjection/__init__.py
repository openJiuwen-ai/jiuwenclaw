from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.reinjection.builders import (
    build_file_reinjected_content,
    build_plan_mode_reinjected_content,
    build_plan_reinjected_content,
    build_skill_reinjected_content,
    build_task_status_reinjected_content,
    build_todo_reinjected_content,
    build_tool_result_hint_reinjected_content,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.reinjection.reinjector import (
    ReinjectBuilder,
    ReinjectBuilderSpec,
    ReinjectContext,
    StateReinjector,
    build_single_reinjected_state_message as _build_single_reinjected_state_message,
)

DEFAULT_REINJECT_BUILDERS = [
    ReinjectBuilderSpec(name="plan", label="PLAN", builder=build_plan_reinjected_content),
    ReinjectBuilderSpec(name="skills", label="SKILLS", builder=build_skill_reinjected_content),
    ReinjectBuilderSpec(name="task_status", label="TASK_STATUS", builder=build_task_status_reinjected_content),
    ReinjectBuilderSpec(name="plan_mode", label="PLAN_MODE", builder=build_plan_mode_reinjected_content),
    ReinjectBuilderSpec(name="read_file", label="READ_FILE", builder=build_file_reinjected_content),
    ReinjectBuilderSpec(name="todo", label="TODO", builder=build_todo_reinjected_content),
]


def build_single_reinjected_state_message(
    ctx: ReinjectContext,
    builder_names: list[str] | None = None,
):
    return _build_single_reinjected_state_message(
        ctx,
        DEFAULT_REINJECT_BUILDERS,
        only=builder_names,
    )


__all__ = [
    "DEFAULT_REINJECT_BUILDERS",
    "ReinjectBuilder",
    "ReinjectBuilderSpec",
    "ReinjectContext",
    "StateReinjector",
    "build_file_reinjected_content",
    "build_plan_mode_reinjected_content",
    "build_plan_reinjected_content",
    "build_skill_reinjected_content",
    "build_single_reinjected_state_message",
    "build_task_status_reinjected_content",
    "build_todo_reinjected_content",
    "build_tool_result_hint_reinjected_content",
]
