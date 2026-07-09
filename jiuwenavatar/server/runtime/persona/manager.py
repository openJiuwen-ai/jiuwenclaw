# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PersonaManager — Persona 身份模板与 Avatar 分身实例的管理器.

职责：
1. 加载和管理 Persona 模板（builtin + user custom）
2. 创建和管理 Avatar 实例（基于 Persona 实例化）
3. 持久化 Avatar 实例到用户 workspace
4. 提供 WebSocket API 的 handler 方法
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid as uuid_lib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from jiuwenavatar.server.runtime.persona.models import (
    AvatarConfig,
    AvatarStatus,
    PersonaConfig,
    PersonaTriggerTemplate,
)
from jiuwenavatar.server.runtime.persona.loader import get_user_personas_dir, load_all_personas
from jiuwenavatar.server.runtime.coding import CODING_ENGINE_CLAUDE_CODE, CODING_ENGINE_CODEX

logger = logging.getLogger(__name__)

_AVATARS_DIR_NAME = "avatars"
_PERSONA_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")
_ALLOWED_CODING_ENGINES = {"jiuwen-coding", CODING_ENGINE_CLAUDE_CODE, CODING_ENGINE_CODEX}


def _start_coding_engine_install_for_selection(kind: str | None) -> None:
    if kind not in {CODING_ENGINE_CLAUDE_CODE, CODING_ENGINE_CODEX}:
        return
    from jiuwenavatar.server.runtime.coding import get_coding_engine
    from jiuwenavatar.server.runtime.coding.bootstrap import start_cli_install_background

    engine = get_coding_engine(kind)
    if engine.is_cli and not engine.is_available():
        detail = start_cli_install_background(kind)
        logger.info("Started coding CLI background install: engine=%s detail=%s", kind, detail)


def _resolve_coding_engine(
    persona: PersonaConfig,
    coding_engine: str | None,
    *,
    explicit: bool,
) -> str | None:
    """解析分身应使用的编码引擎；显式选择 CLI 时校验平台凭据并后台安装 CLI。"""
    if not persona.coding_capable or not persona.coding_engines:
        return None

    from jiuwenavatar.server.runtime.coding import assert_coding_engine_selectable, get_coding_engine

    def is_selectable(kind: str) -> bool:
        engine = get_coding_engine(kind)
        return not engine.is_cli or engine.is_credentials_configured()

    ordered: list[str] = []
    if coding_engine:
        ordered.append(coding_engine)
    if persona.default_coding_engine:
        ordered.append(persona.default_coding_engine)
    for kind in persona.coding_engines:
        if kind not in ordered:
            ordered.append(kind)

    for kind in ordered:
        if kind not in persona.coding_engines:
            continue
        if is_selectable(kind):
            if explicit and kind == coding_engine:
                _start_coding_engine_install_for_selection(kind)
            return kind
        if explicit and kind == coding_engine:
            assert_coding_engine_selectable(kind)

    return persona.coding_engines[0]


_AVATARS_JSON = "avatars.json"


def _get_avatars_dir() -> Path:
    """Get the avatars storage directory in user workspace."""
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / _AVATARS_DIR_NAME


def _get_avatars_json_path() -> Path:
    """Get the avatars.json file path."""
    return _get_avatars_dir() / _AVATARS_JSON


def _safe_persona_dir(persona_id: str) -> Path:
    if not _PERSONA_ID_RE.fullmatch(persona_id):
        raise ValueError("Persona ID 只能包含字母、数字、短横线或下划线，长度 2-64，且必须以字母或数字开头")
    base = get_user_personas_dir().resolve()
    target = (base / persona_id).resolve()
    try:
        if target.parent != base:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Invalid persona_id") from exc
    return target


class PersonaManager:
    """Singleton manager for Persona templates and Avatar instances."""

    _instance: PersonaManager | None = None

    def __init__(self) -> None:
        self._personas: dict[str, PersonaConfig] = {}
        self._avatars: dict[str, AvatarConfig] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> PersonaManager:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def ensure_loaded(self, *, log: bool = True) -> None:
        """Load personas and avatars if not already done."""
        if self._loaded:
            return
        self._load_personas(log=log)
        self._load_avatars(log=log)
        self._loaded = True

    def reload(self, *, log: bool = True) -> None:
        """Force reload all personas and avatars."""
        self._personas.clear()
        self._avatars.clear()
        self._loaded = False
        self.ensure_loaded(log=log)

    def _load_personas(self, *, log: bool = True) -> None:
        """Load all persona templates from disk."""
        personas = load_all_personas()
        for p in personas:
            self._personas[p.id] = p
        if log:
            logger.info("Loaded %d persona templates", len(self._personas))

    def _load_avatars(self, *, log: bool = True) -> None:
        """Load avatar instances from user workspace."""
        json_path = _get_avatars_json_path()
        if not json_path.exists():
            return

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for avatar_data in data.get("avatars", []):
                avatar = AvatarConfig(**avatar_data)
                self._avatars[avatar.id] = avatar
            if log:
                logger.info("Loaded %d avatar instances", len(self._avatars))
        except Exception:
            logger.warning("Failed to load avatars.json", exc_info=True)

    def _save_avatars(self) -> None:
        """Persist avatar instances to disk."""
        json_path = _get_avatars_json_path()
        json_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "avatars": [a.model_dump() for a in self._avatars.values()],
        }
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # Persona Templates (read-only)
    # ------------------------------------------------------------------

    def list_personas(self) -> list[dict[str, Any]]:
        """List all available persona templates."""
        self.ensure_loaded()
        return [p.model_dump() for p in sorted(self._personas.values(), key=lambda p: p.id)]

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        """Get a single persona template by ID."""
        self.ensure_loaded()
        persona = self._personas.get(persona_id)
        return persona.model_dump() if persona else None

    def _validate_persona_payload(
        self,
        payload: dict[str, Any],
        *,
        existing_id: str | None = None,
        allow_overwrite: bool = False,
    ) -> PersonaConfig:
        """Validate and normalize a user-provided Persona payload."""
        if not isinstance(payload, dict):
            raise ValueError("persona payload must be an object")

        data = dict(payload)
        persona_id = str(data.get("id") or existing_id or "").strip()
        if not persona_id:
            raise ValueError("缺少 Persona ID")
        _safe_persona_dir(persona_id)
        data["id"] = persona_id

        display_name = str(data.get("display_name") or "").strip()
        if not display_name:
            raise ValueError("缺少模板名称")
        data["display_name"] = display_name
        data["description"] = str(data.get("description") or "").strip()
        data["icon"] = str(data.get("icon") or "avatar").strip() or "avatar"
        data["version"] = str(data.get("version") or "1.0.0").strip() or "1.0.0"
        data["builtin"] = False

        data["tags"] = [str(x).strip() for x in data.get("tags") or [] if str(x).strip()]
        data["skills"] = [str(x).strip() for x in data.get("skills") or [] if str(x).strip()]
        data["system_prompt"] = str(data.get("system_prompt") or "")

        coding_capable = bool(data.get("coding_capable", False))
        coding_engines = [
            str(x).strip()
            for x in data.get("coding_engines") or []
            if str(x).strip()
        ]
        for engine in coding_engines:
            if engine not in _ALLOWED_CODING_ENGINES:
                raise ValueError(f"不支持的编码后端: {engine}")
        default_engine = data.get("default_coding_engine")
        default_engine = str(default_engine).strip() if default_engine else None
        if default_engine and default_engine not in coding_engines:
            raise ValueError("默认编码后端必须在可选编码后端中")
        data["coding_capable"] = coding_capable
        data["coding_engines"] = coding_engines if coding_capable else []
        data["default_coding_engine"] = default_engine if coding_capable else None

        data.setdefault("trigger_templates", [])
        data.setdefault("report_template", {"title": "执行报告", "sections": []})

        if existing_id and persona_id != existing_id:
            raise ValueError("不允许修改 Persona ID")
        if not allow_overwrite and persona_id in self._personas:
            raise ValueError(f"Persona 已存在: {persona_id}")

        return PersonaConfig(**data)

    @staticmethod
    def _persona_to_yaml_dict(persona: PersonaConfig) -> dict[str, Any]:
        data = persona.model_dump()
        data["builtin"] = False
        return {"persona": data}

    def _write_user_persona(self, persona: PersonaConfig) -> None:
        persona_dir = _safe_persona_dir(persona.id)
        persona_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = persona_dir / "persona.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                self._persona_to_yaml_dict(persona),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

    def create_or_update_persona(
        self,
        payload: dict[str, Any],
        *,
        existing_id: str | None = None,
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create or update a user custom Persona template."""
        self.ensure_loaded()
        persona = self._validate_persona_payload(
            payload,
            existing_id=existing_id,
            allow_overwrite=allow_overwrite or existing_id is not None,
        )
        current = self._personas.get(persona.id)
        if current is not None and current.builtin and existing_id is not None:
            raise ValueError("内置 Persona 不能直接编辑，请先复制为自定义模板")
        if current is not None and current.builtin and not allow_overwrite:
            raise ValueError("不能覆盖内置 Persona，请使用其他 ID")

        self._write_user_persona(persona)
        self.reload()
        saved = self._personas.get(persona.id, persona)
        logger.info("Saved custom persona %s", persona.id)
        return saved.model_dump()

    def duplicate_persona(
        self,
        *,
        source_id: str,
        new_id: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Copy an existing Persona as a new custom Persona."""
        self.ensure_loaded()
        source = self._personas.get(source_id)
        if source is None:
            raise ValueError(f"Persona not found: {source_id}")
        if new_id in self._personas:
            raise ValueError(f"Persona 已存在: {new_id}")

        data = source.model_dump()
        data["id"] = new_id
        data["display_name"] = display_name or f"{source.display_name} 副本"
        data["builtin"] = False
        return self.create_or_update_persona(data)

    async def delete_persona(self, persona_id: str, *, cascade_avatars: bool = False) -> list[str]:
        """Delete a user custom Persona template."""
        self.ensure_loaded()
        persona = self._personas.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona not found: {persona_id}")
        if persona.builtin:
            raise ValueError("内置 Persona 不能删除")

        linked_avatar_ids = [
            avatar.id for avatar in self._avatars.values()
            if avatar.persona_id == persona_id
        ]
        if linked_avatar_ids and not cascade_avatars:
            raise ValueError("已有分身基于该模板创建，请确认是否一起删除")
        for avatar_id in linked_avatar_ids:
            await self.delete_avatar_async(avatar_id)

        persona_dir = _safe_persona_dir(persona_id)
        if persona_dir.exists():
            shutil.rmtree(persona_dir)
        else:
            yaml_path = persona_dir.with_suffix(".yaml")
            if yaml_path.exists():
                yaml_path.unlink()
        self.reload()
        logger.info("Deleted custom persona %s with %d linked avatar(s)", persona_id, len(linked_avatar_ids))
        return linked_avatar_ids

    # ------------------------------------------------------------------
    # Avatar Instances (CRUD)
    # ------------------------------------------------------------------

    def create_avatar(
        self,
        *,
        persona_id: str,
        name: str | None = None,
        system_prompt: str | None = None,
        extra_skills: list[str] | None = None,
        report_channels: list[str] | None = None,
        coding_engine: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Avatar instance based on a Persona template.

        Args:
            persona_id: The persona template to instantiate.
            name: Display name for the avatar. Defaults to "<Persona display_name>".
            system_prompt: Custom system prompt (overrides persona default).
            extra_skills: Additional skills beyond persona defaults.
            report_channels: Channels to push reports to.

        Returns:
            The created AvatarConfig as a dict.

        Raises:
            ValueError: If persona_id does not exist.
        """
        self.ensure_loaded()

        persona = self._personas.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona not found: {persona_id}")

        # Build skill list: persona defaults + extras
        skills = list(persona.skills)
        if extra_skills:
            for s in extra_skills:
                if s not in skills:
                    skills.append(s)

        resolved_coding_engine = _resolve_coding_engine(
            persona,
            coding_engine,
            explicit=coding_engine is not None,
        )

        avatar = AvatarConfig(
            name=name or persona.display_name,
            persona_id=persona_id,
            persona_version=persona.version,
            skills=skills,
            coding_engine=resolved_coding_engine,
            system_prompt=system_prompt,
            report_channels=report_channels or [],
        )

        self._avatars[avatar.id] = avatar
        self._save_avatars()

        logger.info("Created avatar %s from persona %s", avatar.id, persona_id)
        return avatar.model_dump()

    async def _provision_triggers_from_persona(
        self,
        avatar: AvatarConfig,
        persona: PersonaConfig,
    ) -> None:
        """根据 Persona 预置触发器模板，为新建分身自动创建触发器."""
        if not persona.trigger_templates:
            return

        from jiuwenavatar.gateway.trigger import TriggerEngine

        engine = TriggerEngine.get_instance()
        trigger_ids: list[str] = []

        for tmpl in persona.trigger_templates:
            params = self._trigger_params_from_template(tmpl, avatar.id)
            result = await engine.handle_triggers_create(**params)
            trigger = result.get("trigger") if isinstance(result, dict) else None
            if isinstance(trigger, dict) and trigger.get("id"):
                trigger_ids.append(trigger["id"])
            elif isinstance(result, dict) and result.get("error"):
                logger.warning(
                    "Failed to provision trigger '%s' for avatar %s: %s",
                    tmpl.name,
                    avatar.id,
                    result["error"],
                )

        if trigger_ids:
            avatar.trigger_ids = trigger_ids
            avatar.updated_at = datetime.now().isoformat()
            self._save_avatars()
            logger.info("Provisioned %d triggers for avatar %s", len(trigger_ids), avatar.id)

    async def _install_persona_skills(
        self,
        avatar: AvatarConfig,
        persona: PersonaConfig,
    ) -> None:
        """创建分身时，将 Persona 关联的内置 Skill 安装到用户工作区."""
        effective_skills = avatar.get_effective_skills(persona)
        if not effective_skills:
            return

        from jiuwenavatar.common.utils import get_agent_workspace_dir
        from jiuwenavatar.server.runtime.persona.avatar_factory import AvatarFactory
        from jiuwenavatar.server.runtime.skill.skill_manager import SkillManager

        skill_manager = SkillManager(workspace_dir=str(get_agent_workspace_dir()))
        runtime = await AvatarFactory.instantiate_avatar(
            avatar, persona, skill_manager=skill_manager,
        )
        missing = runtime.get("missing_skills") or []
        if missing:
            logger.warning(
                "Avatar %s missing skills after install: %s",
                avatar.id,
                ", ".join(missing),
            )

    @staticmethod
    def _trigger_params_from_template(tmpl: PersonaTriggerTemplate, avatar_id: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": tmpl.name,
            "type": tmpl.type,
            "avatar_id": avatar_id,
            "trigger_prompt": tmpl.prompt,
            "enabled": False,
        }
        if tmpl.cron_expr:
            params["cron_expr"] = tmpl.cron_expr
        if tmpl.interval_seconds is not None:
            params["interval_seconds"] = tmpl.interval_seconds
        if tmpl.active_hours:
            params["active_hours"] = tmpl.active_hours
        if tmpl.webhook_path:
            params["webhook_path"] = tmpl.webhook_path
        if tmpl.event_source:
            params["event_source"] = tmpl.event_source
        if tmpl.event_type:
            params["event_type"] = tmpl.event_type
        return params

    def list_avatars(self) -> list[dict[str, Any]]:
        """List all avatar instances."""
        self.ensure_loaded()
        return [a.model_dump() for a in sorted(self._avatars.values(), key=lambda a: a.created_at)]

    def get_avatar(self, avatar_id: str) -> dict[str, Any] | None:
        """Get a single avatar instance by ID."""
        self.ensure_loaded()
        avatar = self._avatars.get(avatar_id)
        return avatar.model_dump() if avatar else None

    def update_avatar(
        self,
        avatar_id: str,
        *,
        name: str | None = None,
        system_prompt: str | None = None,
        skills: list[str] | None = None,
        report_channels: list[str] | None = None,
        coding_engine: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update an existing avatar instance.

        Only provided fields will be updated; None means "no change".
        To reset system_prompt back to persona default, pass system_prompt="".
        """
        self.ensure_loaded()

        avatar = self._avatars.get(avatar_id)
        if avatar is None:
            raise ValueError(f"Avatar not found: {avatar_id}")

        if name is not None:
            avatar.name = name
        if system_prompt is not None:
            # Empty string means "use persona default"
            avatar.system_prompt = system_prompt if system_prompt else None
        if skills is not None:
            avatar.skills = skills
        if report_channels is not None:
            avatar.report_channels = report_channels
        if coding_engine is not None:
            persona = self._personas.get(avatar.persona_id)
            if persona and persona.coding_capable:
                if coding_engine == "" or coding_engine is None:
                    avatar.coding_engine = None
                elif not persona.coding_engines or coding_engine in persona.coding_engines:
                    resolved = _resolve_coding_engine(
                        persona,
                        coding_engine,
                        explicit=True,
                    )
                    avatar.coding_engine = resolved
        if extra is not None:
            avatar.extra = extra

        avatar.updated_at = datetime.now().isoformat()
        self._save_avatars()

        logger.info("Updated avatar %s", avatar_id)
        return avatar.model_dump()

    def delete_avatar(self, avatar_id: str) -> None:
        """Delete an avatar instance (sync — prefer ``delete_avatar_async`` from handlers)."""
        self.ensure_loaded()

        if avatar_id not in self._avatars:
            raise ValueError(f"Avatar not found: {avatar_id}")

        del self._avatars[avatar_id]
        self._save_avatars()

        logger.info("Deleted avatar %s", avatar_id)

    async def delete_avatar_async(self, avatar_id: str) -> None:
        """Delete avatar and cascade-remove triggers, missions, and reports."""
        self.ensure_loaded()

        avatar = self._avatars.get(avatar_id)
        if avatar is None:
            raise ValueError(f"Avatar not found: {avatar_id}")

        await self._delete_avatar_triggers(avatar_id, avatar)

        from jiuwenavatar.gateway.report.manager import ReportManager

        ReportManager.get_instance().purge_avatar_records(avatar_id)

        del self._avatars[avatar_id]
        self._save_avatars()

        logger.info(
            "Deleted avatar %s with triggers/missions/reports cleanup",
            avatar_id,
        )

    async def _delete_avatar_triggers(self, avatar_id: str, avatar: AvatarConfig) -> None:
        from jiuwenavatar.gateway.trigger.store import TriggerStore

        store = TriggerStore()
        trigger_ids: set[str] = set(avatar.trigger_ids)
        for trigger in store.list_triggers_by_avatar(avatar_id):
            trigger_ids.add(trigger.id)

        if not trigger_ids:
            return

        try:
            from jiuwenavatar.gateway.trigger import TriggerEngine

            engine = TriggerEngine.get_instance()
            for trigger_id in trigger_ids:
                try:
                    await engine.delete_trigger(trigger_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete trigger %s for avatar %s: %s",
                        trigger_id,
                        avatar_id,
                        exc,
                    )
                    store.delete_trigger(trigger_id)
        except Exception:
            for trigger_id in trigger_ids:
                store.delete_trigger(trigger_id)

        logger.info("Deleted %d triggers for avatar %s", len(trigger_ids), avatar_id)

    def set_avatar_status(self, avatar_id: str, status: AvatarStatus) -> None:
        """Update avatar runtime status."""
        self.ensure_loaded()

        avatar = self._avatars.get(avatar_id)
        if avatar is None:
            return

        avatar.status = status
        avatar.updated_at = datetime.now().isoformat()
        self._save_avatars()

    # ------------------------------------------------------------------
    # WebSocket API Handlers
    # ------------------------------------------------------------------

    async def handle_personas_list(self, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.list` — list all persona templates."""
        return {"personas": self.list_personas()}

    async def handle_personas_get(self, *, persona_id: str, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.get` — get a single persona template."""
        persona = self.get_persona(persona_id)
        if persona is None:
            return {"error": f"Persona not found: {persona_id}"}
        return {"persona": persona}

    async def handle_personas_create(self, **kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.create` — create a custom persona template."""
        try:
            payload = kwargs.get("persona") if isinstance(kwargs.get("persona"), dict) else kwargs
            persona = self.create_or_update_persona(dict(payload or {}))
            return {"persona": persona}
        except (TypeError, ValueError) as e:
            return {"error": str(e)}

    async def handle_personas_update(self, *, persona_id: str, **kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.update` — update a custom persona template."""
        try:
            payload = kwargs.get("persona") if isinstance(kwargs.get("persona"), dict) else kwargs
            persona = self.create_or_update_persona(
                dict(payload or {}),
                existing_id=persona_id,
                allow_overwrite=True,
            )
            return {"persona": persona}
        except (TypeError, ValueError) as e:
            return {"error": str(e)}

    async def handle_personas_duplicate(
        self,
        *,
        source_id: str,
        new_id: str,
        display_name: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Handler for `personas.duplicate` — copy a persona as a custom template."""
        try:
            persona = self.duplicate_persona(
                source_id=source_id,
                new_id=new_id,
                display_name=display_name,
            )
            return {"persona": persona}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_personas_generate(self, *, prompt: str, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.generate` — draft a persona from a natural language goal."""
        prompt = str(prompt or "").strip()
        if not prompt:
            return {"error": "请输入一句话描述"}
        try:
            self.ensure_loaded()
            from jiuwenavatar.common.config import get_default_models
            from jiuwenavatar.common.utils import get_agent_workspace_dir
            from jiuwenavatar.server.runtime.skill.skill_manager import SkillManager
            from openjiuwen.core.foundation.llm import Model
            from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

            skills_result = await SkillManager(workspace_dir=str(get_agent_workspace_dir())).handle_skills_list(
                {"with_installed": True}
            )
            available_skills: list[str] = []
            for item in skills_result.get("skills") or []:
                name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
                if name:
                    available_skills.append(name)
            for plugin in skills_result.get("plugins") or []:
                if not isinstance(plugin, dict) or plugin.get("enabled") is False:
                    continue
                for name in plugin.get("skills") or []:
                    text = str(name).strip()
                    if text:
                        available_skills.append(text)
            available_skills = sorted(set(available_skills))

            defaults = get_default_models()
            if not defaults:
                return {"error": "未配置默认模型，无法自动生成 Persona"}
            entry = defaults[0]
            mcc = dict(entry.get("model_client_config") or {})
            mco = dict(entry.get("model_config_obj") or {})
            model_name = str(mcc.get("model_name") or mco.get("model") or "").strip()
            if not model_name:
                return {"error": "默认模型缺少 model_name"}
            mcc_fields = {k: v for k, v in mcc.items() if k != "model_name"}
            if not mcc_fields.get("client_provider"):
                mcc_fields["client_provider"] = "OpenAI"
            llm = Model(
                model_client_config=ModelClientConfig(**mcc_fields),
                model_config=ModelRequestConfig(model=model_name, temperature=0.3),
            )
            instruction = (
                "你是 JiuwenAvatar 的 Persona 模板设计助手。"
                "请根据用户一句话生成一个 JSON 对象，禁止输出 Markdown。"
                "JSON 字段必须包含: id, display_name, description, tags, skills, system_prompt, "
                "coding_capable, coding_engines, default_coding_engine。"
                "skills 只能从 available_skills 中选择；如果没有合适技能，返回空数组。"
                "id 使用小写英文、数字、短横线，长度不超过 32。"
                "system_prompt 用中文，清晰定义角色、职责、边界、输出风格。"
            )
            user_content = json.dumps(
                {
                    "user_goal": prompt,
                    "available_skills": available_skills[:120],
                    "allowed_coding_engines": sorted(_ALLOWED_CODING_ENGINES),
                },
                ensure_ascii=False,
            )
            result = await llm.invoke(
                [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=1800,
                temperature=0.3,
            )
            raw = getattr(result, "content", None) or str(result)
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                return {"error": "模型未返回有效 JSON"}
            data = json.loads(match.group(0))
            if not isinstance(data, dict):
                return {"error": "模型返回格式错误"}
            base_id = str(data.get("id") or "custom-persona").strip().lower()
            base_id = re.sub(r"[^a-z0-9_-]+", "-", base_id).strip("-_") or "custom-persona"
            persona_id = base_id[:32]
            while persona_id in self._personas:
                persona_id = f"{base_id[:23]}-{uuid_lib.uuid4().hex[:6]}"
            data["id"] = persona_id
            data["icon"] = "avatar"
            data["version"] = "1.0.0"
            data["builtin"] = False
            data.setdefault("trigger_templates", [])
            data.setdefault("report_template", {"title": "执行报告", "sections": []})
            persona = self._validate_persona_payload(data)
            return {"persona": persona.model_dump()}
        except Exception as e:
            logger.exception("Generate persona failed: %s", e)
            return {"error": str(e)}

    async def handle_personas_delete(self, *, persona_id: str, cascade_avatars: bool = False, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `personas.delete` — delete a custom persona template."""
        try:
            deleted_avatar_ids = await self.delete_persona(
                persona_id,
                cascade_avatars=bool(cascade_avatars),
            )
            return {"success": True, "deleted_avatar_ids": deleted_avatar_ids}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_avatars_list(self, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `avatars.list` — list all avatar instances."""
        return {"avatars": self.list_avatars()}

    async def handle_avatars_get(self, *, avatar_id: str, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `avatars.get` — get a single avatar instance."""
        avatar = self.get_avatar(avatar_id)
        if avatar is None:
            return {"error": f"Avatar not found: {avatar_id}"}
        return {"avatar": avatar}

    async def handle_avatars_create(self, **kwargs: Any) -> dict[str, Any]:
        """Handler for `avatars.create` — create a new avatar."""
        try:
            persona_id = kwargs["persona_id"]
            avatar_dict = self.create_avatar(
                persona_id=persona_id,
                name=kwargs.get("name"),
                system_prompt=kwargs.get("system_prompt"),
                extra_skills=kwargs.get("extra_skills"),
                report_channels=kwargs.get("report_channels"),
                coding_engine=kwargs.get("coding_engine"),
            )
            avatar = AvatarConfig(**avatar_dict)
            persona = self._personas.get(persona_id)
            if persona is not None:
                await self._provision_triggers_from_persona(avatar, persona)
                await self._install_persona_skills(avatar, persona)
                avatar = self._avatars.get(avatar.id, avatar)
                avatar_dict = avatar.model_dump()
            return {"avatar": avatar_dict}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_avatars_update(self, *, avatar_id: str, **kwargs: Any) -> dict[str, Any]:
        """Handler for `avatars.update` — update an avatar."""
        try:
            skills_changed = kwargs.get("skills") is not None
            avatar = self.update_avatar(
                avatar_id,
                name=kwargs.get("name"),
                system_prompt=kwargs.get("system_prompt"),
                skills=kwargs.get("skills"),
                report_channels=kwargs.get("report_channels"),
                coding_engine=kwargs.get("coding_engine"),
                extra=kwargs.get("extra"),
            )
            if skills_changed:
                persona = self._personas.get(avatar["persona_id"])
                if persona is not None:
                    await self._install_persona_skills(AvatarConfig(**avatar), persona)
                    updated = self._avatars.get(avatar_id)
                    if updated is not None:
                        avatar = updated.model_dump()
            return {"avatar": avatar}
        except ValueError as e:
            return {"error": str(e)}

    async def handle_avatars_delete(self, *, avatar_id: str, **_kwargs: Any) -> dict[str, Any]:
        """Handler for `avatars.delete` — delete an avatar."""
        try:
            await self.delete_avatar_async(avatar_id)
            return {"success": True}
        except ValueError as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def get_persona_manager() -> PersonaManager:
    """Get the singleton PersonaManager instance."""
    return PersonaManager.get_instance()
