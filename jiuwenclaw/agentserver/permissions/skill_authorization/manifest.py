# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill Manifest 生成：加载并校验 skill_permissions.json，产出 SkillManifest。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jiuwenclaw.agentserver.permissions.skill_authorization.models import (
    SkillManifest,
    SkillTrustLevel,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.schema import (
    SKILL_PERMISSION_FILENAME,
    SkillPermissionValidationError,
    compute_permissions_hash,
    compute_skill_md_hash,
    normalize_skill_permission,
    normalize_skill_risk,
)

logger = logging.getLogger(__name__)


def _read_skill_md_hash(skill_dir: Path) -> str:
    """计算 ``SKILL.md`` 摘要；缺失或读取失败时返回空串（身份复核将失败，fail-closed）。"""
    skill_md = skill_dir / "SKILL.md"
    try:
        return compute_skill_md_hash(skill_md.read_bytes())
    except OSError:
        logger.warning(
            "[skill_authorization] manifest.skill_md_unreadable skill_dir=%s",
            skill_dir,
        )
        return ""


def load_skill_manifest(
    skill_dir: str | Path,
    *,
    trust: SkillTrustLevel | str,
    source: str | None = None,
    version: str | None = None,
    skill_name: str | None = None,
) -> SkillManifest | None:
    """从 skill 目录加载并校验 ``skill_permissions.json``，生成 ``SkillManifest``。

    ``trust`` 由调用方判定（本模块不做来源可信性判断）；``source`` 缺省为目录
    绝对路径，``skill_name`` 缺省为目录名，``version`` 可空（本地 Skill）。

    文件缺失返回空 overlay 的可识别 Manifest（静默加载，不创建 Grant）；
    校验失败返回 ``authorizable=False`` 的 Manifest（fail-closed，不抛异常）。
    """
    directory = Path(skill_dir)
    name = (skill_name or "").strip() or directory.name
    resolved_source = (source or "").strip() or str(directory)
    trust_level = trust if isinstance(trust, SkillTrustLevel) else SkillTrustLevel(str(trust))

    permission_file = directory / SKILL_PERMISSION_FILENAME
    if not permission_file.is_file():
        empty_overlay: dict[str, object] = {}
        # 以空嵌套声明的摘要占位：裸 {} 不兼容嵌套包装的 schema 校验。
        empty_declaration: dict[str, object] = {"permissions": {}}
        return SkillManifest(
            skill_name=name,
            source=resolved_source,
            version=version,
            trust=trust_level,
            permissions_hash=compute_permissions_hash(empty_declaration),
            skill_md_hash=_read_skill_md_hash(directory),
            authorizable=True,
            overlay=empty_overlay,
        )

    skill_md_hash = _read_skill_md_hash(directory)

    try:
        raw = json.loads(permission_file.read_text(encoding="utf-8"))
        permissions_hash = compute_permissions_hash(raw)
        overlay = normalize_skill_permission(raw)
        risk_level, risk_status = normalize_skill_risk(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[skill_authorization] manifest.parse_failed skill=%s file=%s error=%s",
            name,
            permission_file,
            exc,
        )
        return SkillManifest(
            skill_name=name,
            source=resolved_source,
            version=version,
            trust=trust_level,
            permissions_hash="",
            skill_md_hash=skill_md_hash,
            authorizable=False,
            errors=(f"skill_permissions.json 解析失败: {exc}",),
        )
    except SkillPermissionValidationError as exc:
        logger.warning(
            "[skill_authorization] manifest.validation_failed skill=%s file=%s errors=%s",
            name,
            permission_file,
            exc.errors,
        )
        return SkillManifest(
            skill_name=name,
            source=resolved_source,
            version=version,
            trust=trust_level,
            permissions_hash="",
            skill_md_hash=skill_md_hash,
            authorizable=False,
            errors=tuple(exc.errors),
        )

    return SkillManifest(
        skill_name=name,
        source=resolved_source,
        version=version,
        trust=trust_level,
        permissions_hash=permissions_hash,
        skill_md_hash=skill_md_hash,
        authorizable=True,
        risk_level=risk_level,
        risk_status=risk_status,
        overlay=overlay,
    )
