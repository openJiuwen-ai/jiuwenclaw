# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent configuration management service — manages CRUD operations for built-in and custom agent definitions.

Agent definition sources (priority from high to low):
- project: <workspace>/.jiuwenclaw/agents/*.md
- user:    ~/.jiuwenclaw/agents/*.md
- local:   <workspace>/.jiuwenclaw/agents-local/*.md
- builtin: built into code

File format is YAML frontmatter + Markdown body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from jiuwenclaw.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)


_TOOL_DESCRIPTIONS: dict[str, str] = {
    "Read": "读取文件内容",
    "Write": "写入文件",
    "Edit": "编辑文件（精准替换）",
    "Bash": "执行 shell 命令",
    "LS": "列出目录内容",
    "Grep": "搜索文件内容",
    "Glob": "按模式搜索文件名",
    "WebSearch": "网络搜索",
    "WebFetch": "获取网页内容",
    "LSP": "代码智能（定义跳转、引用查找）",
    "TodoWrite": "创建/更新任务列表",
    "TodoList": "查看任务列表",
    "MemorySearch": "搜索记忆",
    "MemoryGet": "获取记忆条目",
    "WriteMemory": "写入记忆",
    "EditMemory": "编辑记忆",
    "CronCreate": "创建定时任务",
    "CronList": "列出定时任务",
    "CronDelete": "删除定时任务",
    "SkillTool": "调用 Skill",
    "VisionQA": "视觉问答",
    "ImageOCR": "图片文字识别",
    "AudioTranscribe": "音频转录",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


AgentSource = Literal["builtin", "user", "project", "local"]


@dataclass
class AgentDefinition:
    """Agent definition data model."""

    name: str
    description: str
    prompt: str
    source: AgentSource
    file_path: str | None = None
    model: str | None = None
    tools: list[str] = field(default_factory=lambda: ["*"])
    disallowed_tools: list[str] = field(default_factory=list)
    color: str | None = None
    permission_mode: str | None = None
    memory_scope: str | None = None
    shadowed_by: AgentSource | None = None
    enabled: bool | None = None  # None = not in config.yaml (built-in agents default to None)
    when_to_use: str | None = None  # Tells the LLM when to schedule this agent
    max_iterations: int | None = None  # Max iterations for sub-agent (openjiuwen SubAgentConfig.max_iterations)
    skills: list[str] | None = None  # Skill names preloaded when the sub-agent starts


@dataclass
class CreateAgentParams:
    """Request parameters for creating an agent."""

    name: str
    description: str
    prompt: str
    location: AgentSource
    model: str | None = None
    tools: list[str] | None = None
    color: str | None = None
    permission_mode: str | None = None
    memory_scope: str | None = None
    disallowed_tools: list[str] | None = None
    when_to_use: str | None = None
    max_iterations: int | None = None
    skills: list[str] | None = None


@dataclass
class UpdateAgentParams:
    """Request parameters for updating an agent (all fields optional, None means no change)."""

    description: str | None = None
    when_to_use: str | None = None
    prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    color: str | None = None
    permission_mode: str | None = None
    memory_scope: str | None = None
    disallowed_tools: list[str] | None = None
    max_iterations: int | None = None
    skills: list[str] | None = None


# ---------------------------------------------------------------------------
# Built-in agent definitions
# ---------------------------------------------------------------------------

BUILTIN_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        name="general-purpose",
        description="通用多步任务 agent，适用于没有专用 agent 的各类任务",
        prompt=(
            "你是一个通用任务 agent。使用可用工具完成用户委派的任务。\n\n"
            "工作原则：\n"
            "1. 将复杂任务分解为可管理的步骤\n"
            "2. 在每个步骤完成后汇报进展\n"
            "3. 遇到阻塞时主动说明需要什么信息"
        ),
        source="builtin",
        tools=["*"],
    ),
    AgentDefinition(
        name="Explore",
        description="快速只读代码库探索 agent，用于定位代码、搜索符号、查找文件",
        prompt=(
            "你是代码库探索专家。你的职责是快速定位代码、搜索符号和查找文件。\n\n"
            "工作原则：\n"
            "1. 只进行只读操作（搜索、读取、列出文件）\n"
            "2. 通过多种搜索策略（文件名模式、grep 符号、目录遍历）确保覆盖全面\n"
            "3. 返回精确的文件路径和行号\n"
            "4. 当结果过多时，缩小搜索范围而不是截断输出"
        ),
        source="builtin",
        tools=["Read", "Bash", "Grep", "Glob"],
    ),
    AgentDefinition(
        name="Plan",
        description="软件架构设计 agent，用于规划实现方案",
        prompt=(
            "你是软件架构师。分析代码库模式和约定，提供完整的实现蓝图。\n\n"
            "工作原则：\n"
            "1. 先理解现有代码库的架构模式和约定\n"
            "2. 设计变更时考虑副作用和依赖关系\n"
            "3. 输出包含：需要创建/修改的文件、组件设计、数据流和构建顺序\n"
            "4. 不写实现代码，只提供设计蓝图"
        ),
        source="builtin",
        tools=["Read", "Bash", "Grep", "Glob"],
    ),
]

_SOURCE_SORT_ORDER: dict[str, int] = {"builtin": 0, "local": 1, "user": 2, "project": 3}


def _source_sort_key(agent: AgentDefinition) -> int:
    return _SOURCE_SORT_ORDER.get(agent.source, 99)


# ---------------------------------------------------------------------------
# AgentConfigService
# ---------------------------------------------------------------------------


class AgentConfigService:
    """Manages CRUD operations for agent definitions.

    Supports four sources of agent definitions: built-in, user-level, project-level, local-level.
    Agents with the same name are overridden by priority: project > user > local > builtin.
    """

    def __init__(self, workspace_dir: Path | str | None = None):
        self._workspace_dir = Path(workspace_dir) if workspace_dir else Path.cwd()

    # ---- Paths ----

    @staticmethod
    def _get_user_agents_dir() -> Path:
        return get_user_workspace_dir() / "agents"

    def _get_project_agents_dir(self) -> Path:
        return self._workspace_dir / ".jiuwenclaw" / "agents"

    def _get_local_agents_dir(self) -> Path:
        return self._workspace_dir / ".jiuwenclaw" / "agents-local"

    # ---- CRUD ----

    def list_agents(self) -> list[AgentDefinition]:
        """Lists all agents (built-in + custom), merged by priority.

        Load order determines priority: later loads override earlier ones, so
        project > user > local > builtin. Overridden agents with the same name are tagged with shadowed_by.
        Also reads the enabled state from react.subagents in config.yaml.
        """
        sources: list[tuple[list[AgentDefinition], AgentSource]] = [
            (list(BUILTIN_AGENTS), "builtin"),
            (self._load_from_dir(self._get_local_agents_dir(), "local"), "local"),
            (self._load_from_dir(self._get_user_agents_dir(), "user"), "user"),
            (self._load_from_dir(self._get_project_agents_dir(), "project"), "project"),
        ]

        # Read the enabled state of react.subagents from config.yaml
        subagent_states: dict[str, bool] = {}
        try:
            from jiuwenclaw.config import get_config

            config = get_config()
            react = config.get("react") if isinstance(config, dict) else None
            subagents_cfg = react.get("subagents") if isinstance(react, dict) else None
            if isinstance(subagents_cfg, dict):
                for name, cfg in subagents_cfg.items():
                    if isinstance(cfg, dict) and "enabled" in cfg:
                        subagent_states[name] = bool(cfg["enabled"])
        except Exception as e:
            logger.debug("Failed to load subagent states from config: %s", e)

        # Group by name, keeping agents from all sources (including shadowed ones)
        grouped: dict[str, list[AgentDefinition]] = {}
        for agents, _ in sources:
            for agent in agents:
                grouped.setdefault(agent.name, []).append(agent)

        # The last one in each group is active (highest priority); earlier ones are tagged with shadowed_by
        result: list[AgentDefinition] = []
        for _name, group in grouped.items():
            active = group[-1]
            active.shadowed_by = None
            for agent in group[:-1]:
                agent.shadowed_by = active.source
                result.append(agent)
            result.append(active)

        # Inject enabled state
        for agent in result:
            if agent.name in subagent_states:
                agent.enabled = subagent_states[agent.name]

        return sorted(result, key=_source_sort_key)

    def get_agent(self, name: str) -> AgentDefinition | None:
        """Gets the complete definition of a single agent (including the system prompt body).

        Returns the active version (not shadowed), consistent with the priority semantics of list_agents.
        """
        agents = self.list_agents()
        for a in agents:
            if a.name == name and a.shadowed_by is None:
                return a
        return None

    def get_agent_file_path(self, name: str, location: AgentSource = "user") -> Path:
        """Return the persisted markdown path for a custom Agent definition."""
        normalized_name = validate_agent_name(name)
        return self._resolve_location_dir(location) / f"{normalized_name}.md"

    def create_agent(self, params: CreateAgentParams) -> AgentDefinition:
        """Creates a new custom agent and writes it to a markdown file.

        Raises:
            ValueError: When a built-in agent with the same name already exists, or the name format is invalid
        """
        validate_agent_name(params.name)

        existing = self.get_agent(params.name)
        if existing is not None and existing.source == "builtin":
            raise ValueError(f"不能覆盖内置 agent: {params.name}")

        file_path = self.get_agent_file_path(params.name, params.location)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        content = _format_agent_file(params)
        file_path.write_text(content, encoding="utf-8")

        logger.info("Created agent '%s' at %s", params.name, file_path)

        return AgentDefinition(
            name=params.name,
            description=params.description,
            prompt=params.prompt,
            source=params.location,
            file_path=str(file_path),
            model=params.model,
            tools=params.tools or ["*"],
            color=params.color,
            permission_mode=params.permission_mode,
            memory_scope=params.memory_scope,
            max_iterations=params.max_iterations,
            skills=params.skills,
        )

    def update_agent(self, name: str, params: UpdateAgentParams) -> AgentDefinition:
        """Updates a custom agent definition, overwriting the file.

        Raises:
            ValueError: When the agent does not exist or is a built-in agent
        """
        agent = self.get_agent(name)
        if agent is None:
            raise ValueError(f"Agent 不存在: {name}")
        if agent.source == "builtin":
            raise ValueError(f"不能修改内置 agent: {name}")
        if not agent.file_path:
            raise ValueError(f"Agent 无文件路径: {name}")

        _apply_update_params(agent, params)

        content = _format_agent_file(agent)
        Path(agent.file_path).write_text(content, encoding="utf-8")

        logger.info("Updated agent '%s' at %s", name, agent.file_path)
        return agent

    def delete_agent(self, name: str) -> bool:
        """Deletes a custom agent definition file.

        Raises:
            ValueError: When the agent is a built-in agent
        """
        agent = self.get_agent(name)
        if agent is None:
            return False
        if agent.source == "builtin":
            raise ValueError(f"不能删除内置 agent: {name}")
        if agent.file_path:
            p = Path(agent.file_path)
            if p.exists():
                p.unlink()
                logger.info("Deleted agent '%s' at %s", name, agent.file_path)
            return True
        return False

    @staticmethod
    def list_available_tools() -> dict:
        """Return available tools with display names, internal names, descriptions, and groups."""
        from jiuwenswarm.server.runtime.agent_adapter.code_agent_rail import TOOL_GROUPS, DISALLOWED_FOR_SUBAGENTS
        from openjiuwen.harness.cli.ui.tool_display import _TOOL_DISPLAY_NAMES

        # Build internal → display mapping (deduplicated)
        internal_to_display: dict[str, str] = {}
        for internal_name, display_name in _TOOL_DISPLAY_NAMES.items():
            if internal_name not in internal_to_display:
                internal_to_display[internal_name] = display_name

        # Build display → group mapping from TOOL_GROUPS
        display_to_group: dict[str, str] = {}
        for group_name, display_names in TOOL_GROUPS.items():
            for dn in display_names:
                display_to_group[dn] = group_name

        # Build tool list from known internal names (deduplicated)
        tools = []
        seen_display = set()
        for internal_name, display_name in internal_to_display.items():
            if display_name in seen_display:
                continue
            seen_display.add(display_name)
            group = display_to_group.get(display_name, "高级")
            description = _TOOL_DESCRIPTIONS.get(display_name, display_name)
            tools.append({
                "name": display_name,
                "internal_name": internal_name,
                "description": description,
                "group": group,
            })

        # Add tools referenced in TOOL_GROUPS but not in _TOOL_DISPLAY_NAMES
        # (e.g., "LSP" whose internal name is "lsp")
        for group_name, display_names in TOOL_GROUPS.items():
            for dn in display_names:
                if dn not in seen_display:
                    seen_display.add(dn)
                    tools.append({
                        "name": dn,
                        "internal_name": dn.lower(),
                        "description": _TOOL_DESCRIPTIONS.get(dn, dn),
                        "group": group_name,
                    })

        return {
            "tools": tools,
            "groups": list(TOOL_GROUPS.keys()),
            "disallowed_for_subagents": list(DISALLOWED_FOR_SUBAGENTS),
        }

    # ---- Internal methods ----

    def _resolve_location_dir(self, location: str) -> Path:
        mapping = {
            "user": self._get_user_agents_dir(),
            "project": self._get_project_agents_dir(),
            "local": self._get_local_agents_dir(),
        }
        if location not in mapping:
            raise ValueError(f"无效的 location: {location}，有效值: user, project, local")
        return mapping[location]

    @staticmethod
    def _load_from_dir(dir_path: Path, source: AgentSource) -> list[AgentDefinition]:
        """Loads all .md agent definition files from the directory."""
        if not dir_path.exists():
            return []
        agents: list[AgentDefinition] = []
        for md_file in sorted(dir_path.glob("*.md")):
            try:
                agent = _parse_agent_file(md_file, source)
                if agent is not None:
                    agents.append(agent)
            except Exception:
                logger.warning("Failed to parse agent file: %s", md_file, exc_info=True)
        return agents


# ---------------------------------------------------------------------------
# File parsing / generation
# ---------------------------------------------------------------------------


def validate_agent_name(name: str) -> str:
    """Validate and normalize a custom Agent filename/name."""
    import re

    normalized = str(name or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{3,50}", normalized):
        raise ValueError(
            f"Agent 名称格式无效: '{normalized}'。要求 3-50 字符，仅允许字母、数字、连字符、下划线"
        )
    return normalized


def _team_member_prompt(member: dict[str, Any]) -> str:
    parts = [
        str(member.get(field_name) or "").strip()
        for field_name in ("prompt", "persona", "prompt_hint", "desc")
    ]
    # Prefer private prompt; keep persona/prompt_hint for relay legacy payloads.
    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return "\n\n".join(ordered)


def _team_member_model_name(agent_template: dict[str, Any], field_name: str) -> str | None:
    model_raw = agent_template.get("model")
    if model_raw is None:
        return None
    if not isinstance(model_raw, dict):
        raise ValueError(f"{field_name}.model must be an object")
    model_name = model_raw.get("model")
    if model_name is None:
        return None
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError(f"{field_name}.model.model must be a non-empty string")
    return model_name.strip()


def build_team_member_agent_params(
    front_payload: dict[str, Any],
    *,
    location: AgentSource = "user",
) -> list[CreateAgentParams]:
    """Build the DeepAgent half of Relay's Team/Deep dual-write payload."""
    if not isinstance(front_payload, dict):
        raise ValueError("teams must be an object")
    agents_raw = front_payload.get("agents")
    if not isinstance(agents_raw, dict):
        raise ValueError("agents must be an object")
    teams_raw = front_payload.get("team")
    if teams_raw is None:
        return []
    if not isinstance(teams_raw, list):
        raise ValueError("team must be an array")

    by_name: dict[str, CreateAgentParams] = {}
    for team_index, team_item in enumerate(teams_raw):
        if not isinstance(team_item, dict):
            raise ValueError(f"team[{team_index}] must be an object")
        members: list[tuple[str, Any]] = [
            (f"team[{team_index}].leader", team_item.get("leader")),
        ]
        predefined_members = team_item.get("predefined_members", [])
        if predefined_members is None:
            predefined_members = []
        if not isinstance(predefined_members, list):
            raise ValueError(f"team[{team_index}].predefined_members must be an array")
        members.extend(
            (f"team[{team_index}].predefined_members[{member_index}]", member)
            for member_index, member in enumerate(predefined_members)
        )

        for field_name, member_raw in members:
            if not isinstance(member_raw, dict):
                raise ValueError(f"{field_name} must be an object")
            member_name = validate_agent_name(member_raw.get("member_name", ""))
            agent_key = str(member_raw.get("agent_key") or "").strip()
            if not agent_key or agent_key not in agents_raw:
                raise ValueError(f"{field_name}.agent_key references unknown agent_key: {agent_key}")
            agent_template = agents_raw[agent_key]
            if not isinstance(agent_template, dict):
                raise ValueError(f"agents.{agent_key} must be an object")

            skills = agent_template.get("skills")
            if skills is not None and not isinstance(skills, list):
                raise ValueError(f"agents.{agent_key}.skills must be an array")
            max_iterations = agent_template.get("max_iterations")
            if max_iterations is not None and not isinstance(max_iterations, int):
                raise ValueError(f"agents.{agent_key}.max_iterations must be an integer")

            description_parts = [
                str(member_raw.get("display_name") or member_name).strip(),
                str(agent_template.get("summary") or "").strip(),
            ]

            params = CreateAgentParams(
                name=member_name,
                description=" - ".join(part for part in description_parts if part),
                prompt=_team_member_prompt(member_raw),
                location=location,
                model=_team_member_model_name(agent_template, f"agents.{agent_key}"),
                max_iterations=max_iterations,
                skills=list(skills) if skills is not None else None,
            )
            previous = by_name.get(member_name)
            if previous is not None and previous != params:
                raise ValueError(f"conflicting member_name across teams: {member_name}")
            by_name[member_name] = params

    return list(by_name.values())


def build_single_agent_params(
    agents_payload: list[dict[str, Any]],
    *,
    location: AgentSource = "user",
) -> list[CreateAgentParams]:
    """Build CreateAgentParams for relay sync ``agents[]`` (standalone single-expert deep half).

    Materializes ``~/.jiuwenclaw/agents/{name}.md`` from relay ``agents[]`` so
    ``mode=agent.plan + target_agent={name}`` can load the expert.
    ``prompt`` comes from relay personality+roleDescription; ``description``
    from displayName.
    """
    if not isinstance(agents_payload, list):
        raise ValueError("agents must be an array")

    by_name: dict[str, CreateAgentParams] = {}
    for index, spec in enumerate(agents_payload):
        if not isinstance(spec, dict):
            raise ValueError(f"agents[{index}] must be an object")
        agent_id = str(spec.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError(f"agents[{index}].agent_id is required")
        name = validate_agent_name(agent_id)

        runtime = spec.get("runtime")
        if runtime is not None and not isinstance(runtime, dict):
            raise ValueError(f"agents[{index}].runtime must be an object")
        runtime = runtime if isinstance(runtime, dict) else {}
        model_name = str(runtime.get("model_name") or "").strip() or None

        skills_raw = runtime.get("skills")
        if skills_raw is not None and not isinstance(skills_raw, list):
            raise ValueError(f"agents[{index}].runtime.skills must be an array")
        skills = list(skills_raw) if isinstance(skills_raw, list) else None

        prompt = str(spec.get("prompt") or "").strip()
        description = str(spec.get("description") or agent_id).strip() or agent_id

        params = CreateAgentParams(
            name=name,
            description=description,
            prompt=prompt,
            location=location,
            model=model_name,
            tools=["*"],
            skills=skills,
        )
        previous = by_name.get(name)
        if previous is not None and previous != params:
            raise ValueError(f"conflicting agent_id across specs: {name}")
        by_name[name] = params

    return list(by_name.values())


def _parse_agent_file(file_path: Path, source: AgentSource) -> AgentDefinition | None:
    """Parses an agent file in YAML frontmatter + Markdown body format."""
    content = file_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    frontmatter = yaml.safe_load(parts[1])
    prompt = parts[2].strip()
    if not frontmatter or "name" not in frontmatter:
        return None
    return AgentDefinition(
        name=frontmatter["name"],
        description=frontmatter.get("description", ""),
        when_to_use=frontmatter.get("when_to_use"),
        prompt=prompt,
        source=source,
        file_path=str(file_path),
        model=frontmatter.get("model"),
        tools=frontmatter.get("tools", ["*"]),
        disallowed_tools=frontmatter.get("disallowed_tools", []),
        color=frontmatter.get("color"),
        permission_mode=frontmatter.get("permission_mode"),
        memory_scope=frontmatter.get("memory_scope"),
        max_iterations=frontmatter.get("max_iterations"),
        skills=frontmatter.get("skills"),
    )


def _format_agent_file(params: CreateAgentParams | AgentDefinition) -> str:
    """Generates agent file content in YAML frontmatter + Markdown body format."""
    frontmatter: dict = {
        "name": params.name,
        "description": params.description,
    }
    prompt: str = params.prompt if hasattr(params, "prompt") else ""

    if hasattr(params, "when_to_use") and params.when_to_use:
        frontmatter["when_to_use"] = params.when_to_use
    if params.model:
        frontmatter["model"] = params.model
    if params.tools and params.tools != ["*"]:
        frontmatter["tools"] = params.tools
    if hasattr(params, "color") and params.color:
        frontmatter["color"] = params.color
    if hasattr(params, "permission_mode") and params.permission_mode:
        frontmatter["permission_mode"] = params.permission_mode
    if hasattr(params, "memory_scope") and params.memory_scope:
        frontmatter["memory_scope"] = params.memory_scope

    if hasattr(params, "disallowed_tools") and params.disallowed_tools:
        frontmatter["disallowed_tools"] = params.disallowed_tools
    if hasattr(params, "max_iterations") and params.max_iterations is not None:
        frontmatter["max_iterations"] = params.max_iterations
    if hasattr(params, "skills") and params.skills:
        frontmatter["skills"] = params.skills

    yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{yaml_str}\n---\n\n{prompt}\n"


def _apply_update_params(agent: AgentDefinition, params: UpdateAgentParams) -> None:
    """Applies the non-None fields of UpdateAgentParams to AgentDefinition."""
    if params.description is not None:
        agent.description = params.description
    if params.when_to_use is not None:
        agent.when_to_use = params.when_to_use
    if params.prompt is not None:
        agent.prompt = params.prompt
    if params.model is not None:
        agent.model = params.model
    if params.tools is not None:
        agent.tools = params.tools
    if params.color is not None:
        agent.color = params.color
    if params.permission_mode is not None:
        agent.permission_mode = params.permission_mode
    if params.memory_scope is not None:
        agent.memory_scope = params.memory_scope
    if params.disallowed_tools is not None:
        agent.disallowed_tools = params.disallowed_tools
    if params.max_iterations is not None:
        agent.max_iterations = params.max_iterations
    if params.skills is not None:
        agent.skills = params.skills
