from __future__ import annotations

from typing import Any


def build_skill_index(manager: Any | None = None) -> dict[str, Any]:
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

    from .index_service import SkillIndexService

    resolved_manager = manager or SkillManager()
    payload = SkillIndexService(resolved_manager).build_index()
    return _tool_payload(payload)


def retrieve_skills(query: str, manager: Any | None = None) -> dict[str, Any]:
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

    from .retrieve_service import SkillRetrieveService

    resolved_manager = manager or SkillManager()
    payload = SkillRetrieveService(resolved_manager).retrieve(query)
    return _tool_payload(payload)


def get_skill_retrieval_status(manager: Any | None = None) -> dict[str, Any]:
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

    from .index_service import SkillIndexService

    resolved_manager = manager or SkillManager()
    return SkillIndexService(resolved_manager).status()


def get_skill_retrieval_tree(manager: Any | None = None) -> dict[str, Any]:
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager

    from .index_service import SkillIndexService

    resolved_manager = manager or SkillManager()
    return SkillIndexService(resolved_manager).tree()


def _tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": bool(payload.get("success")),
        "result": str(payload.get("result") or ""),
    }
