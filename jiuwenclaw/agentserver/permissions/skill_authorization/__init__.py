# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""技能级动态授权：以 Skill 生命周期为作用域的临时权限授权。"""

from jiuwenclaw.agentserver.permissions.skill_authorization.composer import (
    SkillAuthorizationContext,
    command_rule_fingerprint,
    compose_skill_permissions,
    effective_file_guard_axis_level,
    get_skill_authorization_context,
    reset_skill_authorization_context,
    setup_skill_authorization_context,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.grant_store import (
    SkillGrantStore,
    get_skill_authorization_generation,
    get_skill_grant_store,
    set_skill_grant_store,
    sync_grants_on_permissions_reload,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.manifest import (
    load_skill_manifest,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.models import (
    SKILL_APPROVAL_CARD_KIND,
    SKILL_APPROVAL_CARD_VERSION,
    GrantDecision,
    GrantStatus,
    SkillApprovalAction,
    SkillApprovalCard,
    SkillGrant,
    SkillManifest,
    SkillPermissionDiff,
    SkillTrustLevel,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.schema import (
    SKILL_APPROVAL_CARD_EXTENSION_KEY,
    SKILL_APPROVAL_PAYLOAD_SCHEMA,
    SKILL_AUTHORIZATION_ENABLED_ENV,
    SKILL_PERMISSION_FILENAME,
    SkillPermissionValidationError,
    canonical_skill_permission_json,
    compute_permissions_hash,
    compute_skill_md_hash,
    is_skill_authorization_enabled,
    normalize_skill_risk,
    normalize_skill_permission,
    validate_skill_permission,
)
from jiuwenclaw.agentserver.permissions.skill_authorization.subagent_approval_registry import (
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    SubagentApprovalRequest,
    get_subagent_approval_registry,
)

__all__ = [
    # Models
    "GrantDecision",
    "GrantStatus",
    "SkillApprovalAction",
    "SkillGrant",
    "SkillManifest",
    "SkillTrustLevel",
    # Approval protocol (Web / CLI 契约)
    "SKILL_APPROVAL_CARD_KIND",
    "SKILL_APPROVAL_CARD_VERSION",
    "SkillApprovalCard",
    "SkillPermissionDiff",
    # Schema
    "SKILL_PERMISSION_FILENAME",
    "SKILL_AUTHORIZATION_ENABLED_ENV",
    "SkillPermissionValidationError",
    "canonical_skill_permission_json",
    "compute_permissions_hash",
    "compute_skill_md_hash",
    "normalize_skill_risk",
    "normalize_skill_permission",
    "validate_skill_permission",
    # Feature flag + 审批响应协议
    "is_skill_authorization_enabled",
    "SKILL_APPROVAL_PAYLOAD_SCHEMA",
    "SKILL_APPROVAL_CARD_EXTENSION_KEY",
    # Direct-subagent delegated approval
    "SubagentApprovalKind",
    "SubagentApprovalRegistry",
    "SubagentApprovalRequest",
    "get_subagent_approval_registry",
    # Manifest
    "load_skill_manifest",
    # Grant store
    "SkillGrantStore",
    "get_skill_authorization_generation",
    "get_skill_grant_store",
    "set_skill_grant_store",
    "sync_grants_on_permissions_reload",
    # Composer + request context
    "SkillAuthorizationContext",
    "command_rule_fingerprint",
    "compose_skill_permissions",
    "effective_file_guard_axis_level",
    "get_skill_authorization_context",
    "reset_skill_authorization_context",
    "setup_skill_authorization_context",
]
