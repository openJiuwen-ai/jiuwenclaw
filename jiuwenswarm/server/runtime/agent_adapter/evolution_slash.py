# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Rail-independent handlers for active Skill evolution slash commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.experience.query import ExperienceQueryService
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.harness.rails.evolution.commands import (
    build_evolve_review_command_prompt,
    build_rebuild_command_prompt,
    build_simplify_command_prompt,
)

from jiuwenswarm.server.runtime.agent_adapter.evolution_helpers import (
    validate_evolution_log_writable,
    validate_evolution_skill,
    validate_team_evolution_skill,
)
from jiuwenswarm.server.runtime.skill import filter_visible_skill_names

logger = logging.getLogger(__name__)

_COMMANDS = (
    "evolve_simplify",
    "evolve_rebuild",
    "evolve_list",
)
_DEFAULT_REVIEW_AGENT_NAME = "evolution_reviewer"


@dataclass(frozen=True)
class EvolutionSlashContext:
    """Context needed to resolve evolution slash commands without a rail."""

    mode: str
    session_id: str
    skills_dir: str | list[str]
    evolution_enabled: bool = True
    language: str = "cn"
    review_agent_name: str = _DEFAULT_REVIEW_AGENT_NAME


async def handle_evolution_slash_command(
    query: Any,
    context: EvolutionSlashContext,
) -> dict[str, Any] | None:
    """Handle active evolution slash commands without requiring a mounted rail.

    Args:
        query: User query to inspect.
        context: Store and prompt-building context supplied by the caller.

    Returns:
        A slash result dict when handled, or ``None`` when this is not one of
        the active evolution commands owned by this handler.
    """
    if not isinstance(query, str):
        return None

    stripped = query.strip()
    command = _command_name(stripped)
    if command is None:
        return None
    if context.mode not in {"agent.plan", "team"}:
        display_mode = str(context.mode or "当前").strip() or "当前"
        return _error(f"{display_mode} 模式下演进功能不可用。")
    if not context.evolution_enabled:
        return _error("演进功能未启用。")

    try:
        store = EvolutionStore(context.skills_dir)
    except Exception as exc:
        logger.warning("[EvolutionSlash] failed to initialize store: %s", exc)
        return _error(f"演进经验库初始化失败：{exc}")

    if command == "evolve_list":
        return await _handle_evolve_list(stripped, store, context)
    if command == "evolve_simplify":
        return await _handle_evolve_simplify(stripped, store, context)
    if command == "evolve_rebuild":
        return await _handle_evolve_rebuild(stripped, store, context)
    return await _handle_evolve(stripped, store, context)


def _command_name(stripped: str) -> str | None:
    if stripped == "/evolve" or stripped.startswith("/evolve "):
        return "evolve"
    for command in _COMMANDS:
        prefix = f"/{command}"
        if stripped == prefix or stripped.startswith(f"{prefix} "):
            return command
    return None


def _subject(store: EvolutionStore, skill_name: str) -> dict[str, str]:
    resolver = getattr(store, "resolve_subject_payload", None)
    if callable(resolver):
        try:
            payload = resolver(skill_name)
        except Exception:
            logger.debug("[EvolutionSlash] could not resolve subject payload for '%s'", skill_name)
        else:
            if isinstance(payload, dict):
                kind = str(payload.get("kind") or "").strip()
                name = str(payload.get("name") or skill_name).strip() or skill_name
                if kind:
                    return {"kind": kind, "name": name}
    return {"kind": "skill", "name": skill_name}


def _followup_response(action: str, followup_prompt: str, skill_name: str) -> dict[str, Any]:
    return {
        "action": action,
        "followup_prompt": followup_prompt,
        "skill_name": skill_name,
        "result_type": "followup",
    }


def _error(output: str) -> dict[str, Any]:
    return {"output": output, "result_type": "error"}


def _answer(output: str) -> dict[str, Any]:
    return {"output": output, "result_type": "answer"}


def _validate_skill(
    store: EvolutionStore,
    skill_name: str,
    *,
    require_skill_md: bool,
    context: EvolutionSlashContext,
    subject: dict[str, str],
) -> str | None:
    subject_kind = str(subject.get("kind") or "skill")
    if context.mode == "team" and subject_kind == "swarm-skill":
        return validate_team_evolution_skill(store, skill_name, require_skill_md=require_skill_md)
    return validate_evolution_skill(store, skill_name, require_skill_md=require_skill_md)


async def _handle_evolve(
    query: str,
    store: EvolutionStore,
    context: EvolutionSlashContext,
) -> dict[str, Any]:
    parts = query.split(maxsplit=2)
    if len(parts) < 2:
        skill_names = filter_visible_skill_names(store.list_skill_names())
        if not skill_names:
            return _answer("当前 skills_base_dir 下未找到任何 Skill 目录。")
        summary = await store.list_pending_summary(skill_names)
        return _answer(f"**Skills 演进记录：**\n\n{summary}")

    skill_name = parts[1].strip()
    user_intent = parts[2].strip() if len(parts) > 2 else ""
    if not user_intent:
        return _error("请补充演进意图：`/evolve <skill_name> <user_query>`")

    subject = _subject(store, skill_name)
    validation_error = _validate_skill(
        store,
        skill_name,
        require_skill_md=True,
        context=context,
        subject=subject,
    )
    if validation_error is not None:
        return _error(validation_error)
    writable_error = validate_evolution_log_writable(store, skill_name)
    if writable_error is not None:
        return _error(writable_error)

    prompt = build_evolve_review_command_prompt(
        subject=subject,
        user_intent=user_intent,
        review_agent_name=context.review_agent_name,
        language=context.language,
    )
    return _followup_response("run_evolve_followup", prompt, skill_name)


async def _handle_evolve_list(
    query: str,
    store: EvolutionStore,
    context: EvolutionSlashContext,
) -> dict[str, Any]:
    parts = query.split()
    skill_name = parts[1] if len(parts) > 1 else ""
    if not skill_name or skill_name.startswith("--"):
        return _error("请指定 Skill 名称：`/evolve_list <skill_name>`")

    subject = _subject(store, skill_name)
    validation_error = _validate_skill(
        store,
        skill_name,
        require_skill_md=False,
        context=context,
        subject=subject,
    )
    if validation_error is not None:
        return _error(validation_error)

    records = await store.get_records_by_score(skill_name, subject_kind=subject["kind"])
    if not records:
        return _answer(f"Skill '{skill_name}' 暂无演进经验。")

    avg_score = sum(record.score for record in records) / len(records)
    lines = [
        f'📊 Skill "{skill_name}" — 经验库摘要\n',
        f"共 {len(records)} 条经验 | 平均分：{avg_score:.2f}\n",
        "| # | Score | Used | Effect | Section | Content (preview) |",
        "|---|---:|---|---|---|---|",
    ]
    for index, record in enumerate(records, 1):
        stats = record.usage_stats
        if stats:
            used_str = (
                f"{stats.times_used}/{stats.times_presented}"
                if stats.times_presented
                else "0/0"
            )
            effect_str = f"+{stats.times_positive}/-{stats.times_negative}"
        else:
            used_str = "0/0"
            effect_str = "+0/-0"
        section = str(record.change.section).replace("|", "\\|")
        preview = record.change.content.split("\n")[0][:40].replace("|", "\\|")
        lines.append(
            f"| {index} | {record.score:.2f} | {used_str} | {effect_str} | {section} | {preview} |"
        )

    lines.append(f"\n提示：使用 /evolve_simplify {skill_name} 执行智能整理")
    return _answer("\n".join(lines))


async def _handle_evolve_simplify(
    query: str,
    store: EvolutionStore,
    context: EvolutionSlashContext,
) -> dict[str, Any]:
    parts = query.split(maxsplit=2)
    skill_name = parts[1] if len(parts) > 1 else ""
    user_intent = parts[2] if len(parts) > 2 else None

    if not skill_name:
        return _error("请指定 Skill 名称：`/evolve_simplify <skill_name> [user_intent]`")

    subject = _subject(store, skill_name)
    validation_error = _validate_skill(
        store,
        skill_name,
        require_skill_md=True,
        context=context,
        subject=subject,
    )
    if validation_error is not None:
        return _error(validation_error)
    writable_error = validate_evolution_log_writable(store, skill_name)
    if writable_error is not None:
        return _error(writable_error)

    query_service = ExperienceQueryService(store=store)
    index = await query_service.list_experiences(
        subject,
        min_score=None,
        limit=100,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )
    if not index.get("items"):
        return _answer(f"Skill '{skill_name}' 暂无演进经验，无需整理。")

    prompt = build_simplify_command_prompt(
        subject=subject,
        user_intent=user_intent,
        full_index=index,
        index_complete=not bool(index.get("has_more")),
        language=context.language,
    )
    return _followup_response("run_simplify_followup", prompt, skill_name)


async def _handle_evolve_rebuild(
    query: str,
    store: EvolutionStore,
    context: EvolutionSlashContext,
) -> dict[str, Any]:
    parts = query.split(maxsplit=2)
    skill_name = parts[1] if len(parts) > 1 else ""
    user_intent = parts[2] if len(parts) > 2 else None

    if not skill_name:
        return _error("请指定 Skill 名称：`/evolve_rebuild <skill_name> [user_intent]`")

    subject = _subject(store, skill_name)
    validation_error = _validate_skill(
        store,
        skill_name,
        require_skill_md=False,
        context=context,
        subject=subject,
    )
    if validation_error is not None:
        return _error(validation_error)

    rebuild_service = ExperienceRebuildService(store=store)
    rebuild_context = await rebuild_service.prepare_rebuild_context(
        subject,
        user_intent=user_intent,
    )
    if rebuild_context is None:
        return _error(f"Skill '{skill_name}' 未生成可执行的重建指令。")

    prompt = build_rebuild_command_prompt(
        subject=subject,
        user_intent=user_intent,
        rebuild_context=rebuild_context,
        language=context.language,
    )
    return _followup_response("run_rebuild_followup", prompt, skill_name)


__all__ = ["EvolutionSlashContext", "handle_evolution_slash_command"]
