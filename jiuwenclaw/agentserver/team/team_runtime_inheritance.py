# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 成员运行时继承模块.

TeamMember 专用 Rail、Ability 继承逻辑，不依赖主 agent adapter。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails import SecurityRail, TaskPlanningRail
from openjiuwen.harness.rails.base import DeepAgentRail

# Optional platform rails — soft-import so missing providers do not break team.
try:
    from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
except ImportError:  # pragma: no cover
    HeartbeatRail = None  # type: ignore[misc, assignment]
from jiuwenclaw.agentserver.deep_agent.rails.avatar_rail import AvatarPromptRail
from jiuwenclaw.agentserver.deep_agent.rails.response_prompt_rail import ResponsePromptRail
from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail
# JiuSwarmStreamEventRail: team variant of stream event rail with member_name/role support.
from jiuwenclaw.agentserver.deep_agent.rails.stream_event_rail import JiuClawStreamEventRail
from jiuwenclaw.agentserver.team.rails.team_workspace_report_path_rail import TeamWorkspaceReportPathRail
from jiuwenclaw.config import get_config


class JiuSwarmStreamEventRail(JiuClawStreamEventRail):
    """Team version of stream event rail with member_name/role support."""

    def __init__(self, *, member_name: str | None = None, role: str | None = None) -> None:
        super().__init__()
        self._member_name = str(member_name or "").strip()
        self._role = str(role or "").strip()

    def get_member_name(self) -> str:
        return self._member_name

    def get_role(self) -> str:
        return self._role


logger = logging.getLogger(__name__)


class TeamTelemetryContextRail(DeepAgentRail):
    """为 team 成员注入 request context（替代 plan 模式 adapter 的 context 注入职责）。

    plan 模式下，JiuWenClawDeepAdapter 在 invoke 前调用 set_telemetry_context /
    set_request_context。team 成员不经 adapter，swarm runner 也不调用这些方法，
    导致 TelemetryRail/RequestSummaryRail 拿不到 session_id/request_id/channel_id，
    RequestSummaryRail 的 after_invoke 因 request_id 为 None 直接 return 不落盘。

    本 rail 在 before_invoke 时从 openjiuwen.agent_teams.context 取 session_id，
    生成唯一 request_id，注入两个 token rail 的 context；after_invoke 时
    finalize_request 落盘 request_summaries.jsonl。
    """

    priority = 9

    def __init__(
        self,
        telemetry_rail: Any | None,
        request_summary_rail: Any | None,
        *,
        channel_id: str = "",
    ) -> None:
        super().__init__()
        self._telemetry_rail = telemetry_rail
        self._request_summary_rail = request_summary_rail
        self._channel_id = str(channel_id or "").strip()

    async def before_invoke(self, ctx: Any) -> None:
        try:
            from openjiuwen.agent_teams.context import get_session_id

            session_id = get_session_id() or "default"
        except Exception:
            session_id = "default"
        request_id = str(time.monotonic_ns())

        if self._telemetry_rail is not None:
            try:
                self._telemetry_rail.set_telemetry_context(
                    channel_id=self._channel_id,
                    session_id=session_id,
                    request_id=request_id,
                    metadata=None,
                )
            except Exception as exc:
                logger.warning("[TeamTelemetryContextRail] set_telemetry_context failed: %s", exc)

        if self._request_summary_rail is not None:
            try:
                self._request_summary_rail.set_request_context(
                    channel_id=self._channel_id,
                    session_id=session_id,
                    request_id=request_id,
                    mode="team",
                )
            except Exception as exc:
                logger.warning("[TeamTelemetryContextRail] set_request_context failed: %s", exc)

    async def after_invoke(self, ctx: Any) -> None:
        try:
            from jiuwenclaw.perf.collector import get_perf_collector
            from jiuwenclaw.perf.context import get_request_context

            req_ctx = get_request_context()
            rid = str(req_ctx.get("request_id") or "").strip() if req_ctx else ""
            if rid:
                status = "error" if getattr(ctx, "error", None) else "ok"
                get_perf_collector().finalize_request(rid, status=status)
        except Exception as exc:
            logger.warning("[TeamTelemetryContextRail] finalize_request failed: %s", exc)


@dataclass
class MemberInfo:
    """成员身份信息."""
    agent_name: str = "team_member"
    model_name: str = "gpt-4"
    role: str | None = None
    # Catalog tip id (same as plan / sync agents[].agent_id); drives tip fallback.
    catalog_agent_id: str | None = None
    # Prefer yaml/agent ``skills`` (tip ENABLED_SKILLS materialized) over tip re-read.
    enabled_skills: str | list[str] | None = None


@dataclass
class RuntimeInfo:
    """运行时环境信息."""
    channel: str = "default"
    language: str = "cn"


@dataclass
class TeamWorkspaceInfo:
    """Team 共享 workspace 信息."""
    root_dir: str | None = None
    skills_dir: str | None = None
    leader_skills_dir: str | None = None
    global_skills_dir: str | None = None
    team_id: str | None = None
    config: dict[str, Any] | None = None
    trajectory_registry: Any | None = None


def resolve_member_catalog_agent_id(
    config: dict[str, Any] | None,
    *,
    member_name: str,
    role: str | None = None,
    team_id: str | None = None,
) -> str | None:
    """Look up roster ``agent_id`` for a team member from ``modes.team``.

    ``team_id`` may be the template key (``oc_team_…``) or a session-scoped
    runtime name (``{template}_{session_id}``). Both must resolve to the same
    roster so SkillUse can read that member's catalog tip ``ENABLED_SKILLS``.
    """
    if not isinstance(config, dict):
        logger.warning(
            "[TeamRuntime] catalog agent_id resolve skipped: config missing "
            "(member=%s role=%s team_id=%s)",
            member_name,
            role,
            team_id,
        )
        return None
    modes = config.get("modes")
    if not isinstance(modes, dict):
        logger.warning(
            "[TeamRuntime] catalog agent_id resolve skipped: modes missing "
            "(member=%s role=%s team_id=%s)",
            member_name,
            role,
            team_id,
        )
        return None
    teams = modes.get("team")
    if not isinstance(teams, dict) or not teams:
        logger.warning(
            "[TeamRuntime] catalog agent_id resolve skipped: modes.team empty "
            "(member=%s role=%s team_id=%s)",
            member_name,
            role,
            team_id,
        )
        return None

    team_raw = _select_modes_team_entry(teams, team_id)
    if not isinstance(team_raw, dict):
        logger.warning(
            "[TeamRuntime] catalog agent_id resolve failed: no modes.team match "
            "for team_id=%s keys=%s member=%s role=%s",
            team_id,
            list(teams.keys())[:20],
            member_name,
            role,
        )
        return None

    name = str(member_name or "").strip()
    leader = team_raw.get("leader")
    if isinstance(leader, dict):
        leader_name = str(leader.get("member_name") or "").strip()
        matches_leader = role == "leader" or name == "leader"
        if not matches_leader and name and name == leader_name:
            matches_leader = True
        if matches_leader:
            aid = str(leader.get("agent_id") or "").strip()
            if aid:
                logger.info(
                    "[TeamRuntime] catalog agent_id resolved: member=%s role=%s "
                    "team_id=%s → %s (leader)",
                    member_name,
                    role,
                    team_id,
                    aid,
                )
                return aid

    for item in team_raw.get("predefined_members") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("member_name") or "").strip() != name:
            continue
        aid = str(item.get("agent_id") or "").strip()
        if aid:
            logger.info(
                "[TeamRuntime] catalog agent_id resolved: member=%s role=%s "
                "team_id=%s → %s (predefined_member)",
                member_name,
                role,
                team_id,
                aid,
            )
            return aid
        break

    logger.warning(
        "[TeamRuntime] catalog agent_id resolve failed: roster miss "
        "member=%s role=%s team_id=%s leader=%s predefined=%s",
        member_name,
        role,
        team_id,
        (leader or {}).get("member_name") if isinstance(leader, dict) else None,
        [
            str(m.get("member_name") or "").strip()
            for m in (team_raw.get("predefined_members") or [])
            if isinstance(m, dict)
        ][:12],
    )
    return None


def _select_modes_team_entry(
    teams: dict[str, Any],
    team_id: str | None,
) -> dict[str, Any] | None:
    """Pick ``modes.team`` entry for template id or session-scoped runtime name."""
    tid = str(team_id or "").strip()
    if tid and isinstance(teams.get(tid), dict):
        return teams[tid]  # type: ignore[return-value]

    if tid:
        for key, entry in teams.items():
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("team_name") or "").strip()
            key_name = str(key or "").strip()
            # Exact team_name match.
            if entry_name and tid == entry_name:
                return entry
            # Session-scoped runtime name: ``{template}_{session_id}``.
            for base in (entry_name, key_name):
                if base and (tid.startswith(f"{base}_") or tid == base):
                    return entry
        # Explicit team_id that matched nothing — do not guess.
        return None

    # No team_id: only safe when a single team is configured.
    if len(teams) == 1:
        only = next(iter(teams.values()))
        return only if isinstance(only, dict) else None
    return None


def enabled_skills_from_member_or_tip(
    *,
    enabled_skills: str | list[str] | None = None,
    catalog_agent_id: str | None = None,
) -> str | None:
    """Prefer materialized agent ``skills``; fall back to catalog tip."""
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config

    yaml_skills = resolve_string_or_list_config(enabled_skills)
    if yaml_skills:
        text = ",".join(yaml_skills)
        logger.info(
            "[TeamRuntime] ENABLED_SKILLS from agent/yaml skills count=%s preview=%s",
            len(yaml_skills),
            (text[:80] + "...") if len(text) > 80 else text,
        )
        return text
    return enabled_skills_from_catalog_tip(catalog_agent_id)


def enabled_skills_from_catalog_tip(agent_id: str | None) -> str | None:
    """Read ``ENABLED_SKILLS`` from a catalog tip (plan-equivalent).

    Falls back to the bound tip when ``agent_id`` is missing — that path is a
    last-resort shell tip (often ``agentteam``) and must be logged loudly.
    """
    from jiuwenclaw.agentserver.skill_manager import enabled_skills_from_environ
    from jiuwenclaw.local_env_config import effective_tip

    aid = str(agent_id or "").strip()
    if not aid:
        bound = enabled_skills_from_environ()
        logger.warning(
            "[TeamRuntime] ENABLED_SKILLS fallback to bound tip (no catalog "
            "agent_id); bound_set=%s preview=%s",
            bool(bound),
            (str(bound)[:80] + "...")
            if isinstance(bound, str) and len(bound) > 80
            else bound,
        )
        return bound
    tip = effective_tip(service_id="default", agent_id=aid) or {}
    raw = tip.get("ENABLED_SKILLS")
    if raw is None:
        logger.warning(
            "[TeamRuntime] catalog tip %s has no ENABLED_SKILLS key",
            aid,
        )
        return None
    text = str(raw).strip()
    if not text:
        logger.warning(
            "[TeamRuntime] catalog tip %s ENABLED_SKILLS is empty",
            aid,
        )
        return None
    logger.info(
        "[TeamRuntime] ENABLED_SKILLS from catalog tip agent_id=%s count=%s preview=%s",
        aid,
        len([p for p in text.replace(";", ",").split(",") if p.strip()]),
        (text[:80] + "...") if len(text) > 80 else text,
    )
    return text


def _list_skill_dir_names(skills_dir: str | None) -> list[str]:
    """Return immediate child directory names under a skill root (best-effort)."""
    from pathlib import Path

    root = str(skills_dir or "").strip()
    if not root:
        return []
    path = Path(root)
    if not path.is_dir():
        return []
    names: list[str] = []
    try:
        for item in path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                names.append(item.name)
    except OSError:
        return []
    return names


def merge_tip_enabled_skills_with_leader_prompt(
    tip_enabled: str | None,
    *,
    leader_skills_dir: str | None,
    role: str,
) -> str | None:
    """Union tip ``ENABLED_SKILLS`` with Leader-only prompt-mounted skills.

    UI path: user already in a team, then clicks ``+`` to add a skill → Relay
    injects ``使用 <skill> 技能`` into the prompt and we mount into
    ``leader-skills``. That skill is often **not** in the catalog tip whitelist.
    Members must not inherit it; only Leader's rail allowlist is extended.
    When tip has no whitelist (``None``), SkillUse loads all scanned dirs — no
    merge needed.
    """
    if str(role or "").strip().lower() != "leader":
        return tip_enabled
    prompt_names = _list_skill_dir_names(leader_skills_dir)
    if not prompt_names:
        return tip_enabled
    if tip_enabled is None:
        # No tip allowlist → all dirs already visible; keep None.
        return None

    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config

    merged: list[str] = []
    seen: set[str] = set()
    for name in [*resolve_string_or_list_config(tip_enabled), *prompt_names]:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(name)
    return ",".join(merged) if merged else tip_enabled


RAIL_WHITELIST = frozenset({
    "RuntimePromptRail",
    "JiuWenSkillUseRail",
    "ResponsePromptRail",
    "JiuSwarmStreamEventRail",
    "TaskPlanningRail",
    "SecurityRail",
    "HeartbeatRail",
    "AvatarPromptRail",
    "FileSystemRail",
    "AskUserQuestionToolRail",
    "TeamWorkspaceReportPathRail",
    "JiuClawContextEngineeringRail",
    "ContextOverflowRecoveryRail",
    "JiuClawQABlockFreezeRail",
    "JiuClawQABlockAssemblyRail",
    "JiuClawQAArtifactRail",
    "MemberSkillToolkitRail",
    "TeamSkillStoragePolicyRail",
    "TeamSharedSkillLinkRefreshRail",
    "DisabledToolsRail",
})

TOOL_WHITELIST = frozenset({
    "free_search",
    "fetch_webpage",
    "paid_search",
    "vision",
    "audio",
    "image_ocr",
    "visual_question_answering",
    "generate_image",
    "audio_transcription",
    "audio_question_answering",
    "audio_metadata",
    "video_understanding",
    "search_skill",
    "install_skill",
    "uninstall_skill",
    "skill_index_build",
    "skill_branch_explore",
    "skill_branch_peek",
    "user_todos",
    "get_user_location",
    "create_note",
    "search_notes",
    "modify_note",
    "create_calendar_event",
    "search_calendar_event",
    "search_contact",
    "search_photo_gallery",
    "upload_photo",
    "search_file",
    "upload_file",
    "call_phone",
    "send_message",
    "search_message",
    "create_alarm",
    "search_alarms",
    "modify_alarm",
    "delete_alarm",
    "xiaoyi_collection",
    "image_reading",
    "xiaoyi_gui_agent",
    "web_free_search",
    "web_fetch_webpage",
    "web_paid_search",
    "skill_toolkit",
    "acp_chat",
    "ask_user_question",
})


def build_member_rails(
    member_info: MemberInfo | None = None,
    runtime: RuntimeInfo | None = None,
    team_workspace: TeamWorkspaceInfo | None = None,
) -> list[Any]:
    """为 Team 成员创建 rails 列表.

    Args:
        member_info: 成员身份信息（agent_name, role）
        runtime: 运行时环境信息（channel, language）
        team_workspace: 团队共享 workspace 信息，其中 skills_dir 为 team shared skills root

    Returns:
        rail 实例列表
    """
    member_info = member_info or MemberInfo()
    runtime = runtime or RuntimeInfo()
    team_workspace = team_workspace or TeamWorkspaceInfo()

    role = member_info.role
    channel = runtime.channel
    language = runtime.language
    team_ws_root = team_workspace.root_dir
    team_id = team_workspace.team_id
    config = team_workspace.config
    model_name = member_info.model_name or get_default_model_name(config)
    if not str(member_info.catalog_agent_id or "").strip():
        member_info.catalog_agent_id = resolve_member_catalog_agent_id(
            config if isinstance(config, dict) else None,
            member_name=member_info.agent_name,
            role=role,
            team_id=team_id,
        )

    rails_list = []

    # Skill loading: single JiuWenSkillUseRail per member (see _build_team_skill_rails).
    # Do not also mount openjiuwen SkillUseRail — same tools would register twice.

    # SysOperationRail is mounted declaratively via agent_configurator
    # (core.sys_operation). Imperative mount here would double-register tools.

    # Every team member gets the current date via environment_context.
    # Full RuntimePromptRail is not mounted for members (its workspace section
    # carries main-agent paths/semantics); sections=("time",) injects date only.
    try:
        rails_list.append(RuntimePromptRail(language=language, sections=("time",)))
        logger.info(
            "[TeamRuntime] RuntimePromptRail(time-only) created: language=%s", language
        )
    except Exception as exc:
        logger.warning("[TeamRuntime] RuntimePromptRail(time-only) failed: %s", exc)

    # Leader-only structured ask tool.
    if role == "leader":
        try:
            from jiuwenclaw.agentserver.team.rails.ask_user_question_tool_rail import (
                AskUserQuestionToolRail,
            )

            rails_list.append(AskUserQuestionToolRail())
            logger.info("[TeamRuntime] AskUserQuestionToolRail created for leader")
        except Exception as exc:
            logger.warning("[TeamRuntime] AskUserQuestionToolRail failed: %s", exc)

    # Team-specific rails (not provided by plan adapter).

    if team_ws_root:
        try:
            rail = TeamWorkspaceReportPathRail(
                root_dir=team_ws_root,
                team_id=team_id,
                language=language,
                enable_send_file_guidance=(role == "leader"),
            )
            rails_list.append(rail)
            logger.info(
                "[TeamRuntime] TeamWorkspaceReportPathRail created: root_dir=%s "
                "send_file_guidance=%s",
                team_ws_root,
                role == "leader",
            )
        except Exception as exc:
            logger.warning("[TeamRuntime] TeamWorkspaceReportPathRail failed: %s", exc)

    # Team skill management (ENT SkillToolkit install/uninstall + team storage/link)
    # plus skill *loading* via JiuWenSkillUseRail → skill_tool (same roots as plan).
    rails_list.extend(
        _build_team_skill_rails(
            team_workspace,
            role=role,
            catalog_agent_id=member_info.catalog_agent_id,
            enabled_skills=member_info.enabled_skills,
        )
    )

    # Same react.disabled_tools gate as plan; only touch this member's ability_manager.
    disabled_rail = _build_team_disabled_tools_rail(config)
    if disabled_rail is not None:
        rails_list.append(disabled_rail)

    # Context compression for all members (leader + teammates): same engine as
    # ENT agent.plan (JiuClawContextEngineeringRail + chain A/B).
    # Leader also mounts plan-style QA block/artifact; teammates keep QA off.
    if get_context_engine_enabled(config):
        enable_qa = role == "leader"
        ce_rail = _build_team_context_engineering_rail(config, enable_qa=enable_qa)
        if ce_rail is not None:
            rails_list.append(ce_rail)
        overflow_rail = _build_team_context_overflow_recovery_rail()
        if overflow_rail is not None:
            rails_list.append(overflow_rail)
        if enable_qa:
            rails_list.extend(_build_team_leader_qa_rails(config))

    # ── token 可观测 rail（镜像 plan：context 注入 → TelemetryRail → RequestSummaryRail）──
    # team 成员不经主 adapter，这里补挂，使成员的 LLM/工具调用同样被统计 token 消耗
    # 并写 request_summaries.jsonl。TeamTelemetryContextRail(priority 9) 先注入
    # session/request/channel 上下文，TelemetryRail(10)/RequestSummaryRail(11) 再采集。
    telemetry_rail = None
    request_summary_rail = None
    try:
        from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

        telemetry_rail = TelemetryRail()
    except Exception as exc:
        logger.warning("[TeamRuntime] TelemetryRail creation failed: %s", exc)
    try:
        from jiuwenclaw.perf.request_summary_rail import RequestSummaryRail

        request_summary_rail = RequestSummaryRail(record_only=True)
    except Exception as exc:
        logger.warning("[TeamRuntime] RequestSummaryRail creation failed: %s", exc)
    if telemetry_rail is not None or request_summary_rail is not None:
        # priority 顺序：context(9) → telemetry(10) → request_summary(11)
        rails_list.insert(0, TeamTelemetryContextRail(
            telemetry_rail, request_summary_rail, channel_id=channel))
        if request_summary_rail is not None:
            rails_list.insert(0, request_summary_rail)
        if telemetry_rail is not None:
            rails_list.insert(0, telemetry_rail)
        logger.info(
            "[TeamRuntime] token rails mounted: telemetry=%s request_summary=%s",
            telemetry_rail is not None,
            request_summary_rail is not None,
        )

    logger.info("[TeamRuntime] Total rails built: %d", len(rails_list))
    return rails_list


def _build_team_disabled_tools_rail(config: dict[str, Any] | None) -> Any | None:
    """Mirror plan's DisabledToolsRail for team members.

    Config: ``react.disabled_tools`` (yaml list or ``${DISABLED_TOOLS:-}``).
    Empty list → skip (nothing to disable). Uses ``touch_shared_resource_mgr=False``
    so in-process multi-member teams do not evict shared Runner tools.
    """
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.disabled_tools_rail import (
            DisabledToolsRail,
        )
        from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config

        react = (config or {}).get("react") if isinstance(config, dict) else {}
        react = react if isinstance(react, dict) else {}
        # Same field plan uses: react.disabled_tools (list or DISABLED_TOOLS env).
        disabled_list = resolve_string_or_list_config(react.get("disabled_tools"))
        if not disabled_list:
            return None
        rail = DisabledToolsRail(
            disabled_tools=disabled_list,
            touch_shared_resource_mgr=False,
        )
        logger.info(
            "[TeamRuntime] DisabledToolsRail created: disabled_tools=%s",
            disabled_list,
        )
        return rail
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] DisabledToolsRail creation failed: %s",
            exc,
            exc_info=True,
        )
        return None


def _build_team_skill_rails(
    team_workspace: TeamWorkspaceInfo,
    *,
    role: str = "teammate",
    catalog_agent_id: str | None = None,
    enabled_skills: str | list[str] | None = None,
) -> list[Any]:
    """Mount team skill loading + management rails.

    Who: every team member (leader + teammate).
    What:
      - One ``JiuWenSkillUseRail`` so ``skill_tool`` sees ENT global skills (+ team
        shared ``team-workspace/skills`` links; leader also gets ``leader_skills_dir``
        for prompt-mounted skills). Not a second bare ``SkillUseRail``.
      - Prefer materialized agent/yaml ``skills`` (from tip ``ENABLED_SKILLS``);
        fall back to catalog tip when yaml list is empty.
      - SkillToolkit install/uninstall + where-to-write prompt + refresh shared links.
    """
    from pathlib import Path

    from jiuwenclaw.utils import get_agent_skills_dir, get_agent_workspace_dir

    rails: list[Any] = []
    global_skills = str(
        team_workspace.global_skills_dir or get_agent_skills_dir()
    ).strip()
    if not global_skills:
        logger.info("[TeamRuntime] Skill rails skipped: no global_skills_dir")
        return rails

    team_skills = str(team_workspace.skills_dir or "").strip() or None
    leader_skills = (
        str(team_workspace.leader_skills_dir or "").strip() or None
        if role == "leader"
        else None
    )
    # SkillManager expects agent workspace root (parent of ``skills/``).
    agent_workspace_root = str(Path(global_skills).parent)
    if not Path(agent_workspace_root).exists():
        agent_workspace_root = str(get_agent_workspace_dir())

    # skill_tool catalog: global (+ team shared; leader prompt view when present).
    skill_scan_dirs: list[str] = [global_skills]
    if team_skills and team_skills not in skill_scan_dirs:
        skill_scan_dirs.append(team_skills)
    if leader_skills and leader_skills not in skill_scan_dirs:
        skill_scan_dirs.append(leader_skills)
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.jiuwen_skill_use_rail import (
            JiuWenSkillUseRail,
        )
        from jiuwenclaw.agentserver.skill_manager import (
            resolve_string_or_list_config,
        )
        from jiuwenclaw.config import get_config as _get_config

        config = team_workspace.config if isinstance(team_workspace.config, dict) else _get_config()
        config = config if isinstance(config, dict) else {}
        react_cec = (config.get("react") or {}).get("context_engine_config")
        max_bodies = 1
        if isinstance(react_cec, dict) and react_cec.get("max_active_skill_bodies") is not None:
            try:
                max_bodies = int(react_cec["max_active_skill_bodies"])
            except (TypeError, ValueError):
                max_bodies = 1

        member_skills = enabled_skills
        rail_enabled = merge_tip_enabled_skills_with_leader_prompt(
            enabled_skills_from_member_or_tip(
                enabled_skills=member_skills,
                catalog_agent_id=catalog_agent_id,
            ),
            leader_skills_dir=leader_skills,
            role=role,
        )
        # File tools come from team SysOperation; only register skill_tool / skill_complete.
        skill_rail = JiuWenSkillUseRail(
            skills_dir=skill_scan_dirs,
            skill_mode="all",
            include_tools=False,
            include_skill_body_tools=True,
            max_active_skill_bodies=max_bodies,
            enabled_skills=rail_enabled,
            disabled_skills=resolve_string_or_list_config(config.get("disabled_skills")),
        )
        rails.append(skill_rail)
        if not rail_enabled:
            logger.warning(
                "[TeamRuntime] JiuWenSkillUseRail empty whitelist "
                "role=%s catalog_agent_id=%s team_id=%s",
                role,
                catalog_agent_id or "(none)",
                team_workspace.team_id,
            )
        logger.info(
            "[TeamRuntime] JiuWenSkillUseRail created: role=%s catalog_agent_id=%s "
            "team_id=%s enabled_skills=%s skills_dir=%s",
            role,
            catalog_agent_id or "(bound)",
            team_workspace.team_id,
            (rail_enabled[:80] + "...")
            if isinstance(rail_enabled, str) and len(rail_enabled) > 80
            else rail_enabled,
            skill_scan_dirs,
        )
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] JiuWenSkillUseRail failed: %s",
            exc,
            exc_info=True,
        )

    def _refresh_links(_result: dict[str, object] | None = None) -> None:
        try:
            from jiuwenclaw.agentserver.team.team_manager import (
                refresh_team_shared_skill_links_across_managers,
            )

            refresh_team_shared_skill_links_across_managers()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[TeamRuntime] refresh team skill links failed: %s", exc)

    try:
        from jiuwenclaw.agentserver.team.rails.team_member_skill_toolkit_rail import (
            MemberSkillToolkitRail,
        )

        rails.append(
            MemberSkillToolkitRail(
                workspace_dir=agent_workspace_root,
                refresh_links=_refresh_links,
            )
        )
        logger.info(
            "[TeamRuntime] MemberSkillToolkitRail created: workspace=%s",
            agent_workspace_root,
        )
    except Exception as exc:
        logger.warning("[TeamRuntime] MemberSkillToolkitRail failed: %s", exc, exc_info=True)

    try:
        from jiuwenclaw.agentserver.team.rails.team_skill_storage_policy_rail import (
            TeamSkillStoragePolicyRail,
        )

        rails.append(
            TeamSkillStoragePolicyRail(
                global_skills_dir=global_skills,
                team_workspace_root=team_ws_root,
                team_skills_dir=team_skills,
            )
        )
        logger.info("[TeamRuntime] TeamSkillStoragePolicyRail created")
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] TeamSkillStoragePolicyRail failed: %s", exc, exc_info=True
        )

    try:
        from jiuwenclaw.agentserver.team.rails.team_shared_skill_link_refresh_rail import (
            TeamSharedSkillLinkRefreshRail,
        )

        rails.append(
            TeamSharedSkillLinkRefreshRail(
                global_skills_dir=Path(global_skills),
                refresh_links=lambda: _refresh_links(None),
            )
        )
        logger.info("[TeamRuntime] TeamSharedSkillLinkRefreshRail created")
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] TeamSharedSkillLinkRefreshRail failed: %s",
            exc,
            exc_info=True,
        )

    return rails


def filter_inheritable_ability_cards(main_agent: Any) -> list[ToolCard]:
    """从主 agent 获取可继承的 ToolCard 白名单.

    Args:
        main_agent: 主 DeepAgent 实例

    Returns:
        白名单内的 ToolCard 列表
    """
    result = []
    try:
        abilities = main_agent.ability_manager.list()
        for ability in abilities:
            if isinstance(ability, ToolCard):
                if ability.name in TOOL_WHITELIST:
                    result.append(ability)
                else:
                    logger.debug("[TeamRuntime] Tool '%s' not in whitelist, skipped", ability.name)
            else:
                logger.debug(
                    "[TeamRuntime] Skipping non-ToolCard ability: %s",
                    getattr(ability, "name", type(ability)),
                )
    except Exception as exc:
        logger.warning("[TeamRuntime] Failed to filter inheritable abilities: %s", exc)
    return result


def get_default_model_name(config: dict[str, Any] | None = None) -> str:
    """从配置获取默认 model_name.

    Args:
        config: 可选的配置字典

    Returns:
        model_name 字符串，默认为 "gpt-4"
    """
    if config is None:
        try:
            config = get_config()
        except Exception as exc:
            logger.warning("[TeamRuntime] Failed to load config for default model: %s", exc)
            return "gpt-4"

    try:
        model_name = config.get("models", {}).get("default", {}).get(
            "model_client_config", {}
        ).get("model_name")
        if model_name:
            return model_name
    except Exception as exc:
        logger.warning("[TeamRuntime] Failed to resolve default model name: %s", exc)

    return "gpt-4"


def get_context_engine_enabled(config: dict[str, Any] | None) -> bool:
    """Check whether context compression is enabled in config.

    Reads ``react.context_engine_config.enabled`` (default True).
    """
    if not isinstance(config, dict):
        return True
    react = config.get("react", {})
    if isinstance(react, dict):
        ctx_cfg = react.get("context_engine_config", {})
        if isinstance(ctx_cfg, dict):
            return ctx_cfg.get("enabled", True)
    return True


def _react_section_for_team_context(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize full config.yaml or a react-shaped dict for the CE rail builder."""
    if not isinstance(config, dict):
        return {}
    react = config.get("react")
    if isinstance(react, dict):
        return react
    # Already react-shaped (or stripped ``{"context_engine_config": ...}``).
    if "context_engine_config" in config:
        return config
    return {}


def _build_team_context_engineering_rail(
    config: dict[str, Any] | None,
    *,
    enable_qa: bool = False,
) -> Any | None:
    """Build context compression rail for team members.

    Reuses :func:`interface_deep._build_context_engineering_rail` with
    ``mode="agent.plan"``. Teammates pass ``enable_qa=False`` (strip QA via
    :func:`react_config_for_subagent`); leader keeps yaml QA so FullCompact
    can attach the qa_artifact safety net.
    """
    try:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import (
            _build_context_engineering_rail,
            react_config_for_subagent,
        )

        react_raw = _react_section_for_team_context(config)
        react_config = (
            react_raw if enable_qa else react_config_for_subagent(react_raw)
        )
        ctx_cfg = react_config.get("context_engine_config", {}) or {}
        if isinstance(ctx_cfg, dict) and ctx_cfg.get("enabled") is False:
            logger.info("[TeamRuntime] ContextEngineeringRail skipped (enabled=false)")
            return None

        rail = _build_context_engineering_rail(
            react_config,
            mode="agent.plan",
            minimal=False,
        )
        if rail is not None:
            logger.info(
                "[TeamRuntime] JiuClawContextEngineeringRail created "
                "(enable_qa=%s)",
                enable_qa,
            )
        return rail
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] JiuClawContextEngineeringRail creation failed: %s",
            exc,
            exc_info=True,
        )
        return None


def _build_team_leader_qa_rails(config: dict[str, Any] | None) -> list[Any]:
    """Mount plan-equivalent QA freeze/assembly/artifact rails for the team leader."""
    try:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import (
            JiuWenClawDeepAdapter,
            _resolve_qa_block_config,
            _resolve_session_memory_for_context_rail,
        )

        react_cfg = _react_section_for_team_context(config)
        if _resolve_qa_block_config(react_cfg) is None:
            logger.info("[TeamRuntime] Leader QA rails skipped (qa_block disabled)")
            return []

        context_engine_cfg = react_cfg.get("context_engine_config", {})
        if not isinstance(context_engine_cfg, dict):
            context_engine_cfg = {}
        session_memory = _resolve_session_memory_for_context_rail(context_engine_cfg)

        freeze_rail = JiuWenClawDeepAdapter._build_qa_block_freeze_rail(react_cfg)  # pylint: disable=protected-access
        assembly_rail = JiuWenClawDeepAdapter._build_qa_block_assembly_rail(react_cfg)  # pylint: disable=protected-access
        artifact_rail = JiuWenClawDeepAdapter._build_qa_artifact_rail(  # pylint: disable=protected-access
            react_cfg, session_memory
        )

        if freeze_rail is not None:
            mgr = (
                artifact_rail.qa_artifact_manager
                if artifact_rail is not None
                else None
            )
            freeze_rail.attach_qa_artifact(mgr)
            if assembly_rail is not None:
                assembly_rail.attach_freeze_rail(freeze_rail)

        rails = [r for r in (freeze_rail, assembly_rail, artifact_rail) if r is not None]
        logger.info(
            "[TeamRuntime] Leader QA rails created: %s",
            [type(r).__name__ for r in rails],
        )
        return rails
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] Leader QA rails creation failed: %s",
            exc,
            exc_info=True,
        )
        return []


def _build_team_context_overflow_recovery_rail() -> Any | None:
    """Mount plan's overflow recovery rail so 413 can force SessionMemory/compact."""
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.context_overflow_recovery_rail import (
            ContextOverflowRecoveryRail,
        )

        rail = ContextOverflowRecoveryRail(max_recovery_attempts=3)
        logger.info("[TeamRuntime] ContextOverflowRecoveryRail created")
        return rail
    except Exception as exc:
        logger.warning(
            "[TeamRuntime] ContextOverflowRecoveryRail creation failed: %s",
            exc,
            exc_info=True,
        )
        return None


def _team_permissions_snapshot(
    *,
    permissions_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read live ``permissions`` from config, optionally narrowed for a teammate."""
    from openjiuwen.agent_teams.security.narrowing import narrow_permissions

    cfg = get_config()
    perm = cfg.get("permissions") if isinstance(cfg, dict) else {}
    perm = dict(perm) if isinstance(perm, dict) else {}
    if permissions_override:
        return narrow_permissions(perm, permissions_override)
    return perm


def build_team_permission_rails(
    *,
    role: str,
    language: str,
    permissions_config: dict[str, Any],
    team_backend: Any,
    messager: Any,
    member_name: str,
    leader_member_name: str,
    permissions_override: dict[str, str] | None = None,
) -> list[Any]:
    """Mount permission rails for team members.

    When ``permissions.enabled`` is on:
    - **leader**: ``TeamPermissionPolicyRail`` (spawn narrowing prompt) **plus**
      ``PermissionInterruptRail`` so leader tool calls ASK/ALLOW/DENY against
      the detailed tools list and surface user HITL.
    - **teammate**: ``TeamPermissionRail`` + jiuwenclaw engine (via adapter)
      so ``tools.X=allow`` applies; ASK → leader ``approve_tool`` (host),
      never user-facing permission HITL.
    """
    if not permissions_config.get("enabled"):
        return []

    rails: list[Any] = []
    lang = language if language in ("cn", "en") else "cn"

    if role == "leader":
        try:
            from jiuwenclaw.agentserver.team.rails.team_permission_policy_rail import (
                TeamPermissionPolicyRail,
            )

            rails.append(
                TeamPermissionPolicyRail(
                    permissions_config=permissions_config,
                    language=lang,
                )
            )
            logger.info("[TeamRuntime] TeamPermissionPolicyRail created for leader")
        except Exception as exc:
            logger.warning("[TeamRuntime] TeamPermissionPolicyRail failed: %s", exc)

        # Intercept leader tools with the same rail and tools list.
        # Without this, enabling the approval guardrail has no effect on leader
        # calls (deepresearch_*, bash, ask_user_question gate, …).
        try:
            from jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers import (
                build_permission_rail,
            )

            leader_perm = build_permission_rail(
                config={"permissions": permissions_config},
            )
            if leader_perm is not None:
                rails.append(leader_perm)
                logger.info(
                    "[TeamRuntime] PermissionInterruptRail created for leader"
                )
        except Exception as exc:
            logger.warning(
                "[TeamRuntime] leader PermissionInterruptRail failed: %s",
                exc,
                exc_info=True,
            )
        return rails

    if role != "teammate" or team_backend is None or messager is None:
        return []

    try:
        from openjiuwen.agent_teams.rails.team_permission_rail import (
            TeamApprovalOrchestrator,
            TeamPermissionRail,
        )
        from openjiuwen.agent_teams.security.narrowing import narrow_permissions
        from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
        from openjiuwen.harness.security.host import ToolPermissionHost
        from jiuwenclaw.agentserver.permissions.core import PermissionEngine as JiuwenclawPermissionEngine
        from jiuwenclaw.agentserver.team.rails.permission_engine_adapter import (
            JiuwenclawPermissionEngineAdapter,
        )

        cfg = permissions_config
        if permissions_override:
            cfg = narrow_permissions(permissions_config, permissions_override)

        message_manager = TeamMessageManager(
            team_backend.team_name,
            member_name,
            team_backend.db,
            messager,
        )
        orchestrator = TeamApprovalOrchestrator(
            message_manager=message_manager,
            leader_member_name=leader_member_name,
        )

        def _snapshot() -> dict[str, Any]:
            return _team_permissions_snapshot(permissions_override=permissions_override)

        host = ToolPermissionHost(
            get_permissions_snapshot=_snapshot,
            request_permission_confirmation=orchestrator.handle_approval_request,
        )
        # Evaluate with jiuwenclaw engine (allow/ask/deny + file_guard),
        # adapt levels for harness TeamPermissionRail; ASK still goes to leader via host.
        engine = JiuwenclawPermissionEngineAdapter(JiuwenclawPermissionEngine(config=cfg))
        try:
            rail = TeamPermissionRail(config=cfg, engine=engine, host=host)
        except TypeError:
            rail = TeamPermissionRail(config=cfg, host=host)
        rails.append(rail)
        logger.info(
            "[TeamRuntime] TeamPermissionRail created for teammate member=%s "
            "(jiuwenclaw engine via adapter → leader host)",
            member_name,
        )
    except Exception as exc:
        logger.warning("[TeamRuntime] TeamPermissionRail failed: %s", exc, exc_info=True)
    return rails
