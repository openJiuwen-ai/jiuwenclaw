# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 成员运行时继承模块.

TeamMember 专用 Rail、Ability 继承逻辑，不依赖主 agent adapter。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails import SecurityRail, TaskPlanningRail

# Optional platform rails — soft-import so missing providers do not break team.
try:
    from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
except ImportError:  # pragma: no cover
    HeartbeatRail = None  # type: ignore[misc, assignment]
try:
    from openjiuwen.harness.rails import SysOperationRail
except ImportError:  # pragma: no cover
    SysOperationRail = None  # type: ignore[misc, assignment]
try:
    from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
except ImportError:  # pragma: no cover
    ContextProcessorRail = None  # type: ignore[misc, assignment]

from jiuwenclaw.agentserver.deep_agent.rails.ask_user_rail import StructuredAskUserRail
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


@dataclass
class MemberInfo:
    """成员身份信息."""
    agent_name: str = "team_member"
    model_name: str = "gpt-4"
    role: str | None = None


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
    team_id: str | None = None
    config: dict[str, Any] | None = None
    trajectory_registry: Any | None = None


RAIL_WHITELIST = frozenset({
    "RuntimePromptRail",
    "ResponsePromptRail",
    "JiuSwarmStreamEventRail",
    "TaskPlanningRail",
    "SecurityRail",
    "HeartbeatRail",
    "AvatarPromptRail",
    "StructuredAskUserRail",
    "FileSystemRail",
    "SysOperationRail",
    "TeamWorkspaceReportPathRail",
    "ContextProcessorRail",
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

    rails_list = []

    try:
        rail = RuntimePromptRail(
            language=language,
            channel=channel,
        )
        rails_list.append(rail)
        logger.info("[TeamRuntime] RuntimePromptRail created: channel=%s", channel)
    except Exception as exc:
        logger.warning("[TeamRuntime] RuntimePromptRail failed: %s", exc)

    try:
        rail = ResponsePromptRail()
        rail.set_channel(channel)
        rails_list.append(rail)
        logger.info("[TeamRuntime] ResponsePromptRail created: channel=%s", channel)
    except Exception as exc:
        logger.warning("[TeamRuntime] ResponsePromptRail failed: %s", exc)

    try:
        if SysOperationRail is not None:
            rail = SysOperationRail()
            rails_list.append(rail)
            logger.info("[TeamRuntime] FileSystemRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] FileSystemRail failed: %s", exc)

    try:
        rail = JiuSwarmStreamEventRail(
            member_name=member_info.agent_name,
            role=member_info.role,
        )
        rails_list.append(rail)
        logger.info("[TeamRuntime] JiuSwarmStreamEventRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] JiuSwarmStreamEventRail failed: %s", exc)

    if role == "leader":
        try:
            rail = StructuredAskUserRail(language=language)
            rails_list.append(rail)
            logger.info("[TeamRuntime] StructuredAskUserRail created for leader")
        except Exception as exc:
            logger.warning("[TeamRuntime] StructuredAskUserRail failed: %s", exc)

    try:
        if role != "leader":
            rail = TaskPlanningRail()
            rails_list.append(rail)
            logger.info("[TeamRuntime] TaskPlanningRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] TaskPlanningRail failed: %s", exc)

    try:
        rail = SecurityRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] SecurityRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] SecurityRail failed: %s", exc)

    try:
        if HeartbeatRail is not None:
            rail = HeartbeatRail()
            rails_list.append(rail)
            logger.info("[TeamRuntime] HeartbeatRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] HeartbeatRail failed: %s", exc)

    try:
        rail = AvatarPromptRail()
        rails_list.append(rail)
        logger.info("[TeamRuntime] AvatarPromptRail created")
    except Exception as exc:
        logger.warning("[TeamRuntime] AvatarPromptRail failed: %s", exc)

    if team_ws_root:
        try:
            rail = TeamWorkspaceReportPathRail(
                root_dir=team_ws_root,
                team_id=team_id,
                language=language,
            )
            rails_list.append(rail)
            logger.info(
                "[TeamRuntime] TeamWorkspaceReportPathRail created: root_dir=%s",
                team_ws_root,
            )
        except Exception as exc:
            logger.warning("[TeamRuntime] TeamWorkspaceReportPathRail failed: %s", exc)

    # Context compression rail for all members (leader + teammates).
    # ``config`` here is the full config.yaml mapping (hot-reload path passes
    # ``get_config()``); strip the ``react`` outer key so the rail builder sees
    # the same ``{"context_engine_config": ...}`` shape the swarm provider path
    # passes (see member_rails._build_context_processor).
    if get_context_engine_enabled(config):
        react = config.get("react", {}) if isinstance(config, dict) else {}
        react = react if isinstance(react, dict) else {}
        rail = _build_context_processor_rail(
            {"context_engine_config": react.get("context_engine_config", {})}
        )
        if rail is not None:
            rails_list.append(rail)

    logger.info("[TeamRuntime] Total rails built: %d", len(rails_list))
    return rails_list


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


def _build_context_processor_rail(config: dict[str, Any] | None) -> Any | None:
    """Build a preset ContextProcessorRail for team members with user config thresholds.

    Expects a pre-extracted mapping of shape ``{"context_engine_config": {...}}`` —
    the ``react`` outer key must already be stripped by the caller. Both call sites
    pass this shape:

    * ``build_member_rails`` (hot-reload path, full config source) extracts
      ``react.context_engine_config`` before calling;
    * ``member_rails._build_context_processor`` (swarm provider) bakes the
      section into ``ContextProcessorInput`` at spec-build time.

    Mirrors :func:`interface_deep._build_context_processor_rail`, which takes the
    same shape (the ``react`` section itself).
    """
    if ContextProcessorRail is None:
        logger.info("[TeamRuntime] ContextProcessorRail skipped (provider not available)")
        return None
    try:
        from typing import List, Tuple

        from openjiuwen.harness.prompts import resolve_language

        user_processors: List[Tuple[str, dict]] = []
        ctx_cfg: dict[str, Any] = {}
        if isinstance(config, dict):
            raw = config.get("context_engine_config", {})
            ctx_cfg = raw if isinstance(raw, dict) else {}

        offloader_cfg = ctx_cfg.get("message_summary_offloader_config", {})
        if isinstance(offloader_cfg, dict) and offloader_cfg:
            user_processors.append(("MessageSummaryOffloader", offloader_cfg))

        compressor_cfg = ctx_cfg.get("dialogue_compressor_config", {})
        if isinstance(compressor_cfg, dict) and compressor_cfg:
            user_processors.append(("DialogueCompressor", compressor_cfg))

        current_round_cfg = ctx_cfg.get("current_round_compressor_config", {})
        if isinstance(current_round_cfg, dict) and current_round_cfg:
            user_processors.append(("CurrentRoundCompressor", current_round_cfg))

        round_level_cfg = ctx_cfg.get("round_level_compressor_config", {})
        if isinstance(round_level_cfg, dict) and round_level_cfg:
            user_processors.append(("RoundLevelCompressor", round_level_cfg))

        reasoning_loop_cfg = ctx_cfg.get("reasoning_tool_loop_compact_config", {})
        if isinstance(reasoning_loop_cfg, dict) and reasoning_loop_cfg:
            reasoning_loop_cfg = {
                **reasoning_loop_cfg,
                "language": resolve_language(
                    str(get_config().get("preferred_language", "zh")).strip().lower()
                ),
            }
            user_processors.append(("ReasoningToolLoopCompactProcessor", reasoning_loop_cfg))

        rail = ContextProcessorRail(
            processors=user_processors if user_processors else None,
            preset=True,
        )
        logger.info(
            "[TeamRuntime] ContextProcessorRail created (preset=True), "
            "user_processors=%s",
            [p[0] for p in user_processors] if user_processors else "none",
        )
        return rail
    except Exception as exc:
        logger.warning("[TeamRuntime] ContextProcessorRail creation failed: %s", exc, exc_info=True)
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
    """Mount TeamPermissionPolicyRail (leader) and TeamPermissionRail (teammate)."""
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
        from jiuwenclaw.agentserver.permissions.core import get_permission_engine

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
        engine = get_permission_engine()
        try:
            rail = TeamPermissionRail(config=cfg, engine=engine, host=host)
        except TypeError:
            rail = TeamPermissionRail(config=cfg, host=host)
        rails.append(rail)
        logger.info(
            "[TeamRuntime] TeamPermissionRail created for teammate member=%s",
            member_name,
        )
    except Exception as exc:
        logger.warning("[TeamRuntime] TeamPermissionRail failed: %s", exc, exc_info=True)
    return rails
