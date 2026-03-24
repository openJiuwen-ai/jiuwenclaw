# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillRail implementation for DeepAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.deepagents.prompts.sections.skills import (
    build_all_mode_skill_prompt,
    build_auto_list_mode_skill_prompt,
    build_skill_line,
    build_skill_lines,
    build_skills_section,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.tools import BashTool, CodeTool, ReadFileTool
from openjiuwen.deepagents.tools.list_skill import ListSkillTool


class SkillRail(DeepAgentRail):
    """Rail that manages skill prompt injection and tool registration."""

    priority = 100

    SKILL_MODE_ALL = "all"
    SKILL_MODE_AUTO_LIST = "auto_list"
    _VALID_SKILL_MODES = {SKILL_MODE_ALL, SKILL_MODE_AUTO_LIST}

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        skill_mode: str = SKILL_MODE_AUTO_LIST,
        list_skill_model: Optional[Model] = None,
        enable_cache: bool = True,
        include_tools: bool = True,
        enabled_skills: Optional[Union[str, List[str]]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
        language: str = "cn",
    ):
        """Initialize SkillRail.

        Args:
            skills_dir: Skill root directory or directories.
            skill_mode: Skill expose mode, supports:
                - "all": inject all enabled skills into system prompt
                - "auto_list": add list_skill tool and let model decide when to inspect skills
            list_skill_model: Optional model used by list_skill tool.
            enable_cache: Whether to cache loaded skills across invokes.
            include_tools: Whether to register read_file / code / bash tools.
            enabled_skills: Optional allow-list of skill names. Supports str or List[str].
            disabled_skills: Optional deny-list of skill names. Supports str or List[str].
        """
        super().__init__()

        if skill_mode not in self._VALID_SKILL_MODES:
            raise ValueError(
                f"Unsupported skill_mode: {skill_mode}. "
                f"Expected one of {sorted(self._VALID_SKILL_MODES)}"
            )

        self.skills_dir = skills_dir
        self.skill_mode = skill_mode
        self.list_skill_model = list_skill_model
        self.enable_cache = enable_cache
        self.include_tools = include_tools
        self.enabled_skills = self._normalize_name_set(enabled_skills)
        self.disabled_skills = self._normalize_name_set(disabled_skills)
        self.language = language

        self.skills: List[Skill] = []
        self._builder = None

        # Cache loaded skills across invokes.
        self._skill_cache: Dict[str, Skill] = {}
        self._skill_update_at: Dict[str, float] = {}
        self._skill_order: List[str] = []

        # Track tools added by this rail only.
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()

    @property
    def skills_meta(self) -> List[Skill]:
        """Return all managed skills."""
        return list(self.skills)

    async def _prepare_skills(self) -> None:
        """Refresh skills incrementally from skills_dir and apply filters."""
        if not self.enable_cache:
            self._skill_cache.clear()
            self._skill_update_at.clear()
            self._skill_order.clear()

        await self._refresh_skills_incrementally()
        self.skills = self._filter_skills(self._collect_skills_in_order())

    async def _refresh_skills_incrementally(self) -> None:
        """Refresh skills by loading only new or updated SKILL.md files."""
        roots = self._normalize_skill_dirs(self.skills_dir)
        if not roots:
            raise ValueError("skills_dir is empty")

        discovered_keys: Set[str] = set()
        ordered_keys: List[str] = []

        for root in roots:
            if not root.exists():
                raise FileNotFoundError(f"skills_dir does not exist: {root}")
            if not root.is_dir():
                raise NotADirectoryError(f"skills_dir is not a directory: {root}")

            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if not item.is_dir():
                    continue

                skill_md_path = item / "SKILL.md"
                if not skill_md_path.exists():
                    continue

                key = str(item.resolve())
                update_at = skill_md_path.stat().st_mtime

                discovered_keys.add(key)
                ordered_keys.append(key)

                cached_skill = self._skill_cache.get(key)
                cached_update_at = self._skill_update_at.get(key)

                if cached_skill is None or cached_update_at != update_at:
                    skill = await self._load_skill(item, update_at)
                    self._skill_cache[key] = skill
                    self._skill_update_at[key] = update_at

        stale_keys = [key for key in self._skill_cache.keys() if key not in discovered_keys]
        for key in stale_keys:
            self._skill_cache.pop(key, None)
            self._skill_update_at.pop(key, None)

        self._skill_order = [key for key in ordered_keys if key in self._skill_cache]

    async def _load_skill(self, skill_dir: Path, update_at: float) -> Skill:
        """Load one skill from a skill directory."""
        skill_md_path = skill_dir / "SKILL.md"

        description = ""
        try:
            description = await self._load_description(skill_md_path)
        except Exception as exc:
            logger.warning(f"Failed to load description from {skill_md_path}: {exc}")

        skill = Skill(
            name=skill_dir.name,
            description=description or f"Skill located in {skill_dir}",
            directory=skill_dir,
        )
        try:
            setattr(skill, "update_at", update_at)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug(
                "[SkillRail] skip setting update_at for skill '%s': %s",
                skill.name,
                exc,
            )
        return skill

    def _collect_skills_in_order(self) -> List[Skill]:
        """Collect cached skills in directory traversal order and deduplicate by name."""
        collected: List[Skill] = []
        seen_names: Set[str] = set()

        for key in self._skill_order:
            skill = self._skill_cache.get(key)
            if skill is None:
                continue

            if skill.name in seen_names:
                logger.warning(
                    f"[SkillRail] duplicate skill name detected: '{skill.name}'. "
                    f"keep first loaded skill, skip '{skill.directory}'."
                )
                continue

            seen_names.add(skill.name)
            collected.append(skill)

        return collected

    def _filter_skills(self, skills: List[Skill]) -> List[Skill]:
        """Filter skills by enabled_skills and disabled_skills."""
        filtered: List[Skill] = []

        for skill in skills:
            if self.enabled_skills and skill.name not in self.enabled_skills:
                continue
            if skill.name in self.disabled_skills:
                continue
            filtered.append(skill)

        return filtered

    def init(self, agent):
        """Register tool cards into agent and concrete tools into resource manager."""
        self._builder = getattr(agent, '_prompt_builder', None)

        tools = []

        if self.include_tools:
            tools.extend(
                [
                    ReadFileTool(self.sys_operation, language=self.language),
                    CodeTool(self.sys_operation, language=self.language),
                    BashTool(self.sys_operation, language=self.language),
                ]
            )

        if self.skill_mode == self.SKILL_MODE_AUTO_LIST:
            tools.append(
                ListSkillTool(
                    get_skills=lambda: self.skills,
                    list_skill_model=self.list_skill_model,
                    language=self.language,
                )
            )

        for tool in tools:
            try:
                existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
                if existing_tool is None:
                    Runner.resource_mgr.add_tool(tool)
                    self._owned_tool_ids.add(tool.card.id)
            except Exception as exc:
                logger.warning(
                    f"[SkillRail] failed to add tool resource '{tool.card.id}' "
                    f"to resource_mgr: {exc}"
                )

        if hasattr(agent, "ability_manager"):
            for tool in tools:
                try:
                    result = agent.ability_manager.add(tool.card)
                    if result.added:
                        self._owned_tool_names.add(tool.card.name)
                except Exception as exc:
                    logger.warning(
                        f"[SkillRail] failed to add tool card '{tool.card.name}' "
                        f"to ability_manager: {exc}"
                    )

    def uninit(self, agent):
        """Remove tool cards from agent ability manager."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[SkillRail] failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )

        self._owned_tool_names.clear()
        self._owned_tool_ids.clear()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Prepare skills before invoke."""
        _ = ctx
        await self._prepare_skills()

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject skill prompt before model call."""
        if self._builder is not None:
            skills_section = self._build_skills_section()
            if skills_section is not None:
                self._builder.add_section(skills_section)
            else:
                self._builder.remove_section("skills")
            prompt = self._builder.build()
            self._replace_system_message(ctx, prompt)
        else:
            if self.skill_mode == self.SKILL_MODE_ALL:
                skill_prompt = self._build_all_mode_prompt()
            else:
                skill_prompt = build_auto_list_mode_skill_prompt(language=self.language)
            self._inject_prompt(ctx, skill_prompt)

    def _build_skills_section(self):
        """Build PromptSection from current skills."""
        if self.skill_mode == self.SKILL_MODE_ALL:
            body_lines: List[str] = []
            for idx, skill in enumerate(self.skills):
                body_lines.append(
                    build_skill_line(
                        index=idx,
                        skill_name=skill.name,
                        description=skill.description,
                        skill_md_path=str(self._skill_md_path(skill)),
                    )
                )
            return build_skills_section(
                skill_lines=build_skill_lines(body_lines),
                language=self.language,
                mode="all",
            )
        else:
            return build_skills_section(
                skill_lines="",
                language=self.language,
                mode="auto_list",
            )

    def _replace_system_message(self, ctx: AgentCallbackContext, prompt: str) -> None:
        """Replace (not append) the system message."""
        inputs = getattr(ctx, "inputs", None)
        messages = getattr(inputs, "messages", None)
        if not isinstance(messages, list):
            return

        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                msg["content"] = prompt
                return
            if isinstance(msg, SystemMessage):
                msg.content = prompt
                return

        messages.insert(0, SystemMessage(content=prompt))

    def _build_all_mode_prompt(self) -> str:
        """Build skill prompt for all mode."""
        body_lines: List[str] = []

        for idx, skill in enumerate(self.skills):
            body_lines.append(
                build_skill_line(
                    index=idx,
                    skill_name=skill.name,
                    description=skill.description,
                    skill_md_path=str(self._skill_md_path(skill)),
                )
            )

        return build_all_mode_skill_prompt(build_skill_lines(body_lines), language=self.language)

    def _inject_prompt(self, ctx: AgentCallbackContext, skill_prompt: str) -> None:
        """Inject skill prompt into the current system message, without duplicate append."""
        inputs = getattr(ctx, "inputs", None)
        messages = getattr(inputs, "messages", None)
        if not isinstance(messages, list):
            return

        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                original = msg.get("content", "") or ""
                if skill_prompt in original:
                    return
                msg["content"] = (original.rstrip() + "\n\n" + skill_prompt).strip()
                return

            if isinstance(msg, SystemMessage):
                original = msg.content or ""
                if skill_prompt in original:
                    return
                msg.content = (original.rstrip() + "\n\n" + skill_prompt).strip()
                return

        messages.insert(0, SystemMessage(content=skill_prompt))

    @staticmethod
    def _normalize_name_list(raw: Optional[Union[str, List[str]]]) -> List[str]:
        """Normalize env-style or list-style skill name inputs."""
        if raw is None:
            return []

        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            normalized = text.replace(";", ",")
            return [item.strip() for item in normalized.split(",") if item.strip()]

        names: List[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            normalized = text.replace(";", ",")
            names.extend([part.strip() for part in normalized.split(",") if part.strip()])
        return names

    @classmethod
    def _normalize_name_set(cls, raw: Optional[Union[str, List[str]]]) -> Set[str]:
        """Normalize skill names into a set."""
        return set(cls._normalize_name_list(raw))

    async def _load_yaml(self, path: Path) -> Tuple[Optional[dict], str]:
        """Load YAML front matter and markdown body from SKILL.md."""
        result = await self.sys_operation.fs().read_file(
            str(path),
            mode="text",
            encoding="utf-8",
        )

        if getattr(result, "code", 0) != 0:
            raise FileNotFoundError(
                getattr(result, "message", f"read_file failed: {path}")
            )

        data = getattr(result, "data", None)
        content = getattr(data, "content", None) if data is not None else None
        if content is None:
            raise FileNotFoundError(f"read_file content is None: {path}")

        text = content if isinstance(content, str) else str(content)

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                _, yaml_block, body = parts
                yaml_data = yaml.safe_load(yaml_block) or {}
                return yaml_data, body.lstrip()

        return None, text

    async def _load_description(self, path: Path) -> str:
        """Load description from YAML front matter."""
        yaml_data, _ = await self._load_yaml(path)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("SKILL.md file does not contain a description field")
        return str(yaml_data["description"])

    @staticmethod
    def _skill_md_path(skill: Skill) -> Path:
        """Return SKILL.md path for a skill."""
        return skill.directory / "SKILL.md"

    @staticmethod
    def _parse_skill_dirs(raw: str) -> List[str]:
        """Parse env-style multi-skill-dir string."""
        if not raw or not raw.strip():
            return []
        normalized = raw.replace(",", ";")
        return [item.strip() for item in normalized.split(";") if item.strip()]

    @classmethod
    def _normalize_skill_dirs(cls, skills_dir: Union[str, List[str]]) -> List[Path]:
        """Normalize one or more skill directories."""
        if isinstance(skills_dir, str):
            raw_dirs = cls._parse_skill_dirs(skills_dir)
            if not raw_dirs and skills_dir.strip():
                raw_dirs = [skills_dir.strip()]
        else:
            raw_dirs = []
            for item in skills_dir:
                if isinstance(item, str):
                    parsed = cls._parse_skill_dirs(item)
                    if parsed:
                        raw_dirs.extend(parsed)
                    elif item.strip():
                        raw_dirs.append(item.strip())

        normalized: List[Path] = []
        for raw in raw_dirs:
            if not raw or not str(raw).strip():
                continue
            normalized.append(Path(raw).expanduser().resolve())

        return normalized

    @classmethod
    async def load_skills_from_dir(
        cls,
        skills_dir: Union[str, List[str]],
    ) -> List[Skill]:
        """Load skills from one or more skills directories."""
        roots = cls._normalize_skill_dirs(skills_dir)
        if not roots:
            raise ValueError("skills_dir is empty")

        skill_map: Dict[str, Skill] = {}

        loader = cls(
            skills_dir=skills_dir,
            skill_mode=cls.SKILL_MODE_ALL,
            include_tools=False,
        )

        for root in roots:
            if not root.exists():
                raise FileNotFoundError(f"skills_dir does not exist: {root}")
            if not root.is_dir():
                raise NotADirectoryError(f"skills_dir is not a directory: {root}")

            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if not item.is_dir():
                    continue

                skill_md_path = item / "SKILL.md"
                if not skill_md_path.exists():
                    continue

                update_at = skill_md_path.stat().st_mtime
                skill = await loader._load_skill(item, update_at)

                if skill.name in skill_map:
                    prev_dir = skill_map[skill.name].directory
                    logger.warning(
                        f"[SkillRail] duplicate skill name detected: '{skill.name}'. "
                        f"keep='{prev_dir}', skip='{item}'."
                    )
                    continue

                skill_map[skill.name] = skill

        return list(skill_map.values())


__all__ = [
    "SkillRail",
]