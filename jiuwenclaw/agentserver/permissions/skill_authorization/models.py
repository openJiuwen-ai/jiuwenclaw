# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""技能级动态授权数据模型：SkillManifest / SkillGrant 及相关枚举。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillTrustLevel(str, Enum):
    """Skill 可信标记（v1 二值；``approve_session`` 仅对 ``BUILTIN`` 开放）。"""

    BUILTIN = "builtin"
    OTHER = "other"


class GrantStatus(str, Enum):
    """Grant 状态机。

    - ``PENDING_ACTIVATION``：用户已批准，等待正文加载成功（不产生权限）。
    - ``ACTIVE``：正文加载成功且身份匹配（参与权限合成）。
    - ``APPROVED_INACTIVE``：仅保留会话内审批缓存（不产生权限）。
    """

    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    APPROVED_INACTIVE = "APPROVED_INACTIVE"


class GrantDecision(str, Enum):
    """授权范围。

    - ``LOCAL``：本次允许，仅当前 Skill 激活期有效，``skill_complete`` 即回收。
    - ``SESSION``：会话内允许，Skill 结束后保留审批记录（``APPROVED_INACTIVE``）。
    """

    LOCAL = "local"
    SESSION = "session"


class SkillApprovalAction(str, Enum):
    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    CONTINUE_WITHOUT_OVERLAY = "continue_without_overlay"


@dataclass(frozen=True)
class SkillManifest:
    """Skill 身份记录（``skill_permissions.json`` 校验产物）。

    身份五元组为 ``skill_name + source + version + permissions_hash + skill_md_hash``。

    - ``authorizable=True``：校验通过，可进入审批 / 授权流程。
    - ``authorizable=False``：``skill_permissions.json`` 非法或解析失败，
      ``errors`` 携带失败原因；overlay 视为空，Skill 本体按原有权限运行（fail-closed）。
    """

    skill_name: str
    source: str
    version: str | None
    trust: SkillTrustLevel
    permissions_hash: str
    skill_md_hash: str = ""
    authorizable: bool = True
    errors: tuple[str, ...] = ()
    risk_level: str | None = None
    risk_status: str = "missing"
    overlay: dict[str, Any] = field(default_factory=dict, compare=False)

    def identity_tuple(self) -> tuple[str, str, str | None, str, str]:
        return (
            self.skill_name,
            self.source,
            self.version,
            self.permissions_hash,
            self.skill_md_hash,
        )


@dataclass
class SkillGrant:
    """一份 Skill 授权记录。

    绑定 Manifest 身份五元组，只保存用户批准的 Skill overlay 快照
    （``overlay_snapshot``），不保存权限合成结果；激活前必须重新核验
    Manifest 身份与摘要，任一不一致则不激活。
    """

    skill_name: str
    source: str
    version: str | None
    permissions_hash: str
    skill_md_hash: str
    overlay_snapshot: dict[str, Any]
    decision: GrantDecision
    status: GrantStatus
    approval_tool_call_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def identity_tuple(self) -> tuple[str, str, str | None, str, str]:
        return (
            self.skill_name,
            self.source,
            self.version,
            self.permissions_hash,
            self.skill_md_hash,
        )

    def matches_identity(self, manifest: SkillManifest) -> bool:
        return self.identity_tuple() == manifest.identity_tuple()

    def touch(self, status: GrantStatus | None = None) -> None:
        if status is not None:
            self.status = status
        self.updated_at = time.time()


# ---------- 审批卡协议（Web / CLI 对接契约，同版本冻结） ----------

#: 审批卡结构化 payload 的协议版本；前端按 ``kind + version`` 识别。
SKILL_APPROVAL_CARD_KIND = "skill_approval"
SKILL_APPROVAL_CARD_VERSION = 1


@dataclass(frozen=True)
class SkillPermissionDiff:
    """Skill overlay 相对当前生效配置的权限差分。

    - ``widened``：放宽项（需用户批准才生效），展示在最前；
    - ``tightened``：收紧项（同样需用户批准才生效），展示其次；
    - ``rejected``：试图突破全局 / 父路径 ``deny`` 而被丢弃的声明，单列。
    """

    widened: tuple[str, ...] = ()
    tightened: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "widened": list(self.widened),
            "tightened": list(self.tightened),
            "rejected": list(self.rejected),
        }


@dataclass(frozen=True)
class SkillApprovalCard:
    """Skill 加载审批卡的结构化协议（前端渲染契约）。

    ``to_dict()`` 输出为稳定 JSON 结构；用户回传动作取值见
    ``SkillApprovalAction``（``approve_once`` / ``approve_session`` /
    ``continue_without_overlay``），其中 ``approve_session`` 仅在
    ``trust == builtin`` 时出现在 ``actions`` 中。无法识别的 action
    由后端默认拒绝。
    """

    skill_name: str
    source: str
    version: str | None
    trust: SkillTrustLevel
    permissions_hash: str
    agent_scope_id: str
    diff: SkillPermissionDiff
    actions: tuple[str, ...]
    cached_decision: str | None = None
    kind: str = SKILL_APPROVAL_CARD_KIND
    schema_version: int = SKILL_APPROVAL_CARD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "skill_name": self.skill_name,
            "source": self.source,
            "version": self.version,
            "trust": self.trust.value,
            "permissions_hash": self.permissions_hash,
            "agent_scope_id": self.agent_scope_id,
            "cached_decision": self.cached_decision,
            "diff": self.diff.to_dict(),
            "actions": list(self.actions),
        }
