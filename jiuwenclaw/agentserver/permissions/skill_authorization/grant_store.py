# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""线程安全的进程内 GrantStore：按 (session_id, agent_scope_id, skill_name) 存取 Skill 授权 Grant。"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
from typing import Any

from jiuwenclaw.agentserver.permissions.skill_authorization.models import (
    GrantDecision,
    GrantStatus,
    SkillGrant,
    SkillManifest,
)

logger = logging.getLogger(__name__)

_ScopeKey = tuple[str, str]

_RETIRED_SESSION_FILTER_BYTES = 1024 * 1024
_RETIRED_SESSION_HASH_COUNT = 7


class _RetiredSessionFilter:
    """固定内存墓碑过滤器；允许假阳性，但绝不允许假阴性。"""

    def __init__(self) -> None:
        self._bits = bytearray(_RETIRED_SESSION_FILTER_BYTES)

    @property
    def memory_bytes(self) -> int:
        return len(self._bits)

    @staticmethod
    def _indexes(session_id: str):
        digest = hashlib.sha256(session_id.encode("utf-8")).digest()
        bit_count = _RETIRED_SESSION_FILTER_BYTES * 8
        for offset in range(0, _RETIRED_SESSION_HASH_COUNT * 4, 4):
            yield int.from_bytes(digest[offset:offset + 4], "big") % bit_count

    def add(self, session_id: str) -> None:
        for index in self._indexes(session_id):
            byte_index, bit_offset = divmod(index, 8)
            self._bits[byte_index] |= 1 << bit_offset

    def __contains__(self, session_id: str) -> bool:
        return all(
            self._bits[byte_index] & (1 << bit_offset)
            for byte_index, bit_offset in (divmod(index, 8) for index in self._indexes(session_id))
        )


def _normalize_scope(session_id: str, agent_scope_id: str) -> _ScopeKey:
    sid = (session_id or "").strip()
    scope = (agent_scope_id or "").strip()
    if not sid or not scope:
        raise ValueError(
            f"session_id 与 agent_scope_id 均不能为空: session_id={session_id!r} agent_scope_id={agent_scope_id!r}",
        )
    return sid, scope


class SkillGrantStore:
    """进程内 Grant 存储（线程安全；进程重启后全部失效）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._grants: dict[_ScopeKey, dict[str, SkillGrant]] = {}
        # 仅记录子 Agent 当前根 Skill 执行窗口；窗口存在时工具调用才进入
        # SubagentPermissionRail。它与 Grant 分离，因此“仅加载不授权”也仍受
        # 原始 PermissionEngine 裁决。
        self._skill_executions: dict[_ScopeKey, str] = {}
        # 回收失败时的独立能力熔断。命中的 scope 即使仍残留 ACTIVE 对象，
        # 读取侧也视为无 overlay，直到新的 Manifest 成功激活。
        self._invalidated_scopes: set[_ScopeKey] = set()
        # 固定内存墓碑：Bloom filter 可能误拒绝，但不会漏掉已删除 session（fail-closed）。
        self._retired_sessions = _RetiredSessionFilter()

    @property
    def retired_session_memory_bytes(self) -> int:
        """墓碑过滤器固定分配的字节数（用于运行时观测）。"""
        return self._retired_sessions.memory_bytes

    # ---------- 写入 ----------

    def enter_skill_execution(
        self,
        session_id: str,
        agent_scope_id: str,
        skill_name: str,
    ) -> None:
        """标记作用域已成功加载根 Skill，后续工具调用进入权限裁决窗口。"""
        key = _normalize_scope(session_id, agent_scope_id)
        name = (skill_name or "").strip()
        if not name:
            raise ValueError("skill_name 不能为空")
        with self._lock:
            if key[0] in self._retired_sessions:
                raise RuntimeError(
                    f"session 已永久删除，不能进入 Skill 执行窗口: session_id={key[0]!r}",
                )
            self._skill_executions[key] = name
        logger.info(
            "[skill_authorization] skill_execution.enter session=%s scope=%s skill=%s",
            key[0], key[1], name,
        )

    def exit_skill_execution(self, session_id: str, agent_scope_id: str) -> None:
        """退出作用域的根 Skill 执行窗口。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            name = self._skill_executions.pop(key, None)
        if name:
            logger.info(
                "[skill_authorization] skill_execution.exit session=%s scope=%s skill=%s",
                key[0], key[1], name,
            )

    def get_active_skill_execution(
        self,
        session_id: str,
        agent_scope_id: str,
    ) -> str | None:
        """返回当前根 Skill 名称；无执行窗口时返回 ``None``。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            return self._skill_executions.get(key)

    def create_pending_grant(
        self,
        session_id: str,
        agent_scope_id: str,
        manifest: SkillManifest,
        *,
        decision: GrantDecision,
        approval_tool_call_id: str | None = None,
    ) -> SkillGrant:
        """审批通过后写入 ``PENDING_ACTIVATION`` Grant（同 skill 重复审批时覆盖旧候选）。

        ``overlay_snapshot`` 取自 Manifest 中规范化后的 overlay。
        """
        key = _normalize_scope(session_id, agent_scope_id)
        if not manifest.authorizable:
            raise ValueError(
                f"不可授权的 SkillManifest 不能创建 Grant: skill={manifest.skill_name!r} errors={list(manifest.errors)}",
            )
        if not manifest.skill_md_hash:
            raise ValueError(
                f"SKILL.md 摘要缺失，不能创建 Grant: skill={manifest.skill_name!r}",
            )
        grant = SkillGrant(
            skill_name=manifest.skill_name,
            source=manifest.source,
            version=manifest.version,
            permissions_hash=manifest.permissions_hash,
            skill_md_hash=manifest.skill_md_hash,
            overlay_snapshot=copy.deepcopy(manifest.overlay),
            decision=decision,
            status=GrantStatus.PENDING_ACTIVATION,
            approval_tool_call_id=approval_tool_call_id,
        )
        with self._lock:
            if key[0] in self._retired_sessions:
                raise RuntimeError(
                    f"session 已永久删除，不能创建 Grant: session_id={key[0]!r}",
                )
            self._grants.setdefault(key, {})[manifest.skill_name] = grant
        logger.info(
            "[skill_authorization] grant.pending_created session=%s scope=%s skill=%s "
            "decision=%s permissions_hash=%s tool_call_id=%s",
            key[0], key[1], manifest.skill_name, decision.value,
            manifest.permissions_hash, approval_tool_call_id,
        )
        return copy.deepcopy(grant)

    def activate_pending(
        self,
        session_id: str,
        agent_scope_id: str,
        skill_name: str,
        manifest: SkillManifest,
        *,
        approval_tool_call_id: str | None = None,
    ) -> SkillGrant | None:
        """正文加载成功后激活匹配的 ``PENDING_ACTIVATION``；原子退出旧 ``ACTIVE``。

        身份五元组（``skill_name + source + version + permissions_hash + skill_md_hash``）与
        ``approval_tool_call_id``（若审批时记录）任一不匹配则不激活：
        Store 层仅清理候选 Grant并返回 ``None``；调用 Rail 负责同步熔断scope。
        """
        key = _normalize_scope(session_id, agent_scope_id)
        name = (skill_name or "").strip()
        with self._lock:
            scope_grants = self._grants.get(key)
            if not scope_grants:
                return None
            candidate = scope_grants.get(name)
            if candidate is None or candidate.status != GrantStatus.PENDING_ACTIVATION:
                return None

            matched = candidate.matches_identity(manifest)
            if matched and candidate.approval_tool_call_id:
                matched = (
                    approval_tool_call_id is not None
                    and candidate.approval_tool_call_id == approval_tool_call_id
                )
            if not matched:
                del scope_grants[name]
                self._drop_empty_scope(key)
                logger.warning(
                    "[skill_authorization] grant.activate_rejected session=%s scope=%s skill=%s "
                    "reason=identity_or_tool_call_mismatch",
                    key[0], key[1], name,
                )
                return None

            # 原子退出旧 ACTIVE：local -> 删除；session -> APPROVED_INACTIVE。
            for other_name, other in list(scope_grants.items()):
                if other_name == name or other.status != GrantStatus.ACTIVE:
                    continue
                if other.decision == GrantDecision.SESSION:
                    other.touch(GrantStatus.APPROVED_INACTIVE)
                    logger.info(
                        "[skill_authorization] grant.active_superseded session=%s scope=%s skill=%s "
                        "action=to_approved_inactive",
                        key[0], key[1], other_name,
                    )
                else:
                    del scope_grants[other_name]
                    logger.info(
                        "[skill_authorization] grant.active_superseded session=%s scope=%s skill=%s "
                        "action=deleted",
                        key[0], key[1], other_name,
                    )

            candidate.touch(GrantStatus.ACTIVE)
            self._invalidated_scopes.discard(key)
            logger.info(
                "[skill_authorization] grant.activated session=%s scope=%s skill=%s "
                "decision=%s permissions_hash=%s",
                key[0], key[1], name, candidate.decision.value, candidate.permissions_hash,
            )
            return copy.deepcopy(candidate)

    # ---------- 读取（深拷贝） ----------

    def get_grant(
        self,
        session_id: str,
        agent_scope_id: str,
        skill_name: str,
    ) -> SkillGrant | None:
        """按 ``(session_id, agent_scope_id, skill_name)`` 取 Grant 深拷贝。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            grant = self._grants.get(key, {}).get((skill_name or "").strip())
            return copy.deepcopy(grant) if grant is not None else None

    def get_active(self, session_id: str, agent_scope_id: str) -> SkillGrant | None:
        """取作用域内唯一的 ``ACTIVE`` Grant 深拷贝（无则 ``None``）。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            if key in self._invalidated_scopes:
                return None
            for grant in self._grants.get(key, {}).values():
                if grant.status == GrantStatus.ACTIVE:
                    return copy.deepcopy(grant)
            return None

    def list_grants(
        self,
        session_id: str,
        agent_scope_id: str | None = None,
    ) -> list[SkillGrant]:
        """列出会话（或指定作用域）内全部 Grant 的深拷贝。"""
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        with self._lock:
            out: list[SkillGrant] = []
            for (stored_sid, scope), grants in self._grants.items():
                if stored_sid != sid:
                    continue
                if agent_scope_id is not None and scope != agent_scope_id.strip():
                    continue
                out.extend(copy.deepcopy(grant) for grant in grants.values())
            return out

    # ---------- 回收 ----------

    def invalidate_scope(
        self,
        session_id: str,
        agent_scope_id: str,
        *,
        reason: str,
    ) -> None:
        """立即熔断 scope 的 ACTIVE overlay；与 Grant 状态回收相互独立。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            self._invalidated_scopes.add(key)
        logger.warning(
            "[skill_authorization] grant.scope_invalidated session=%s scope=%s reason=%s",
            key[0],
            key[1],
            reason,
        )

    def drop_pending(
        self,
        session_id: str,
        agent_scope_id: str,
        skill_name: str,
        *,
        reason: str = "load_failed",
    ) -> bool:
        """清理候选 ``PENDING_ACTIVATION`` Grant（正文加载失败 / 身份不匹配）。

        仅删除 ``PENDING_ACTIVATION`` 状态的候选；``ACTIVE`` 与
        ``APPROVED_INACTIVE`` 一律不动（旧 ``ACTIVE`` 保持不变，fail-closed）。
        """
        key = _normalize_scope(session_id, agent_scope_id)
        name = (skill_name or "").strip()
        if not name:
            return False
        with self._lock:
            scope_grants = self._grants.get(key)
            if not scope_grants:
                return False
            candidate = scope_grants.get(name)
            if candidate is None or candidate.status != GrantStatus.PENDING_ACTIVATION:
                return False
            del scope_grants[name]
            self._drop_empty_scope(key)
        logger.info(
            "[skill_authorization] grant.pending_dropped session=%s scope=%s skill=%s reason=%s",
            key[0], key[1], name, reason,
        )
        return True

    def revoke_grants(
        self,
        session_id: str,
        agent_scope_id: str,
        *,
        skill_name: str | None = None,
        reason: str = "skill_complete",
    ) -> None:
        """生命周期回收（``skill_complete`` / deactivate / 任务取消）。

        ``local`` 决策删除 Grant；``session`` 决策转 ``APPROVED_INACTIVE``
        （仅保留审批记录）。``skill_name`` 为空时作用于整个作用域。
        """
        key = _normalize_scope(session_id, agent_scope_id)
        target = (skill_name or "").strip() or None
        with self._lock:
            self._invalidated_scopes.add(key)
            scope_grants = self._grants.get(key)
            if not scope_grants:
                return
            for name, grant in list(scope_grants.items()):
                if target is not None and name != target:
                    continue
                if grant.decision == GrantDecision.SESSION:
                    if grant.status != GrantStatus.APPROVED_INACTIVE:
                        grant.touch(GrantStatus.APPROVED_INACTIVE)
                        logger.info(
                            "[skill_authorization] grant.revoked session=%s scope=%s skill=%s "
                            "reason=%s action=to_approved_inactive",
                            key[0], key[1], name, reason,
                        )
                else:
                    del scope_grants[name]
                    logger.info(
                        "[skill_authorization] grant.revoked session=%s scope=%s skill=%s "
                        "reason=%s action=deleted",
                        key[0], key[1], name, reason,
                    )
            self._drop_empty_scope(key)

    def clear_session(self, session_id: str) -> None:
        """永久删除会话：清 Grant 并写墓碑，阻止在途调用为同 id 重建授权。"""
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        with self._lock:
            self._retired_sessions.add(sid)
            removed = [key for key in self._grants if key[0] == sid]
            for key in removed:
                count = len(self._grants[key])
                del self._grants[key]
                logger.info(
                    "[skill_authorization] grant.session_cleared session=%s scope=%s grants=%s",
                    key[0], key[1], count,
                )
            for key in [key for key in self._skill_executions if key[0] == sid]:
                self._skill_executions.pop(key, None)
            self._invalidated_scopes = {
                key for key in self._invalidated_scopes if key[0] != sid
            }

    def clear_scope(self, session_id: str, agent_scope_id: str) -> None:
        """子 Agent 销毁：删除该 ``(session_id, agent_scope_id)`` 作用域下全部 Grant。"""
        key = _normalize_scope(session_id, agent_scope_id)
        with self._lock:
            grants = self._grants.pop(key, None)
            self._skill_executions.pop(key, None)
            self._invalidated_scopes.discard(key)
        if grants:
            logger.info(
                "[skill_authorization] grant.scope_cleared session=%s scope=%s grants=%s",
                key[0], key[1], len(grants),
            )

    def clear_all(self) -> None:
        """功能开关运行中关闭等场景：清空全部 Grant。"""
        with self._lock:
            total = sum(len(grants) for grants in self._grants.values())
            self._grants.clear()
            self._skill_executions.clear()
            self._invalidated_scopes.clear()
        logger.info("[skill_authorization] grant.all_cleared grants=%s", total)

    # ---------- 内部 ----------

    def _drop_empty_scope(self, key: _ScopeKey) -> None:
        if key in self._grants and not self._grants[key]:
            del self._grants[key]


# ---------- 全局单例 ----------
_grant_store: SkillGrantStore | None = None
_grant_store_lock = threading.Lock()
_authorization_generation = 0
_authorization_generation_lock = threading.Lock()


def get_skill_authorization_generation() -> int:
    """返回动态授权撤销代次，用于使开关关闭前的迟到答案永久失效。"""
    with _authorization_generation_lock:
        return _authorization_generation


def _advance_skill_authorization_generation() -> int:
    global _authorization_generation
    with _authorization_generation_lock:
        _authorization_generation += 1
        return _authorization_generation


def peek_skill_grant_store() -> SkillGrantStore | None:
    """返回已创建的全局 ``SkillGrantStore``；未创建时返回 ``None``（不触发初始化）。"""
    return _grant_store


def get_skill_grant_store() -> SkillGrantStore:
    """获取进程内全局 ``SkillGrantStore``（懒初始化）。"""
    global _grant_store
    if _grant_store is None:
        with _grant_store_lock:
            if _grant_store is None:
                _grant_store = SkillGrantStore()
    return _grant_store


def set_skill_grant_store(store: SkillGrantStore) -> None:
    """替换全局 ``SkillGrantStore``（测试用）。"""
    global _grant_store
    with _grant_store_lock:
        _grant_store = store


def sync_grants_on_permissions_reload(
    old_config: Any,
    new_config: Any,
) -> None:
    """权限配置热更新联动。

    - ``skill_authorization.enabled`` 由 true 变 false：清空全部 Grant；
    - 由 false 变 true：不自动激活已加载 Skill（仅记日志，须重新调用 ``skill_tool``）；
    - 其余普通热更新：不清 Grant。
    """
    from jiuwenclaw.agentserver.permissions.skill_authorization.schema import (
        is_skill_authorization_enabled,
    )

    was_enabled = is_skill_authorization_enabled(old_config)
    now_enabled = is_skill_authorization_enabled(new_config)
    if was_enabled and not now_enabled:
        from jiuwenclaw.agentserver.permissions.skill_authorization.subagent_approval_registry import (
            get_subagent_approval_registry,
        )

        _advance_skill_authorization_generation()
        get_subagent_approval_registry().clear_all()
        get_skill_grant_store().clear_all()
        logger.info(
            "[skill_authorization] feature disabled at runtime; all grants cleared",
        )
    elif now_enabled and not was_enabled:
        logger.info(
            "[skill_authorization] feature enabled at runtime; "
            "already-loaded skills are NOT auto-activated (reload via skill_tool required)",
        )
