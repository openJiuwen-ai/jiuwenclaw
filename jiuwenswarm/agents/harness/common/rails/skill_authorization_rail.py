# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillAuthorizationRail — Skill 加载门禁审批 + 激活/回收（priority=95）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from jiuwenswarm.agentserver.deep_agent.skill_lifecycle_events import (
    ROOT_SKILL_FILE,
    SKILL_AUTHORIZATION_GATE_HANDLED_KEY,
    SKILL_TOOL_NAME,
    extract_skill_lifecycle_event,
    is_root_skill_load_call,
    is_skill_complete,
    parse_tool_call_arguments,
)
from jiuwenswarm.agentserver.permissions.models import PermissionLevel
from jiuwenswarm.agentserver.permissions.skill_authorization import (
    SKILL_APPROVAL_CARD_EXTENSION_KEY,
    SKILL_APPROVAL_PAYLOAD_SCHEMA,
    SKILL_PERMISSION_FILENAME,
    GrantDecision,
    GrantStatus,
    SkillApprovalAction,
    SkillApprovalCard,
    SkillGrantStore,
    SkillManifest,
    SkillPermissionDiff,
    SkillTrustLevel,
    compose_skill_permissions,
    effective_file_guard_axis_level,
    get_skill_authorization_context,
    get_skill_authorization_generation,
    get_skill_grant_store,
    is_skill_authorization_enabled,
    load_skill_manifest,
    reset_skill_authorization_context,
    setup_skill_authorization_context,
)

logger = logging.getLogger(__name__)

#: 主 Agent 的授权作用域标识（子 Agent 各自独立作用域）。
MAIN_AGENT_SCOPE_ID = "main"

#: 待确认的 Skill 审批上下文（按 tool_call_id 存 session state，模式同权限审批卡）。
PENDING_SKILL_APPROVAL_CONTEXT_KEY = "jiuwenclaw_pending_skill_approval_contexts"
SKILL_AUTHORIZATION_CONTEXT_TOKEN_KEY = "jiuwenclaw_skill_authorization_context_token"

#: skill 位置解析器返回三元组：（skill 目录，来源标识，版本）。
SkillLocation = tuple[Path, str, str | None]
SkillResolver = Callable[[str], Optional[SkillLocation]]
TrustResolver = Callable[[Path], SkillTrustLevel]
ConfigProvider = Callable[[], dict[str, Any]]
_POLICY_EVALUATION_FAILED = object()


def build_skill_registry_resolver(
    get_skills: Callable[[], Any],
    *,
    skill_dirs_provider: Callable[[], Any] | None = None,
) -> SkillResolver:
    """把 SkillUseRail 注册表访问器（``get_skills_meta``）适配为 ``SkillResolver``。

    主 Agent 与子 Agent（SubagentSkillUseRail）共用；来源取目录绝对路径，
    注册表提供版本时一并纳入 Manifest 身份，缺失时才返回 ``None``。
    """

    def resolve(skill_name: str) -> SkillLocation | None:
        if not skill_name or Path(skill_name).name != skill_name:
            logger.warning(
                "[skill_authorization] invalid skill name for resolver skill=%r",
                skill_name,
            )
            return None
        skills = []
        try:
            skills = get_skills() or []
        except Exception:  # noqa: BLE001
            logger.warning(
                "[skill_authorization] skill registry read failed", exc_info=True,
            )
        for skill in skills:
            if getattr(skill, "name", None) != skill_name:
                continue
            directory = getattr(skill, "directory", None)
            if directory is None:
                return None
            path = Path(str(directory))
            version = _registered_skill_version(skill)
            return path, str(path), version

        scanned_dirs: list[str] = []
        if skill_dirs_provider is not None:
            try:
                raw_dirs = skill_dirs_provider() or []
                if isinstance(raw_dirs, (str, Path)):
                    raw_dirs = [raw_dirs]
                for raw_dir in raw_dirs:
                    root = Path(str(raw_dir))
                    scanned_dirs.append(str(root))
                    candidate = root / skill_name
                    if candidate.is_dir() and (candidate / ROOT_SKILL_FILE).is_file():
                        logger.info(
                            "[skill_authorization] skill resolved by directory fallback "
                            "skill=%s registry_count=%s root=%s",
                            skill_name,
                            len(skills),
                            root,
                        )
                        return candidate, str(candidate), None
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[skill_authorization] skill directory fallback failed skill=%s dirs=%s",
                    skill_name,
                    scanned_dirs,
                    exc_info=True,
                )
        logger.warning(
            "[skill_authorization] skill unresolved skill=%s registry_count=%s scanned_dirs=%s",
            skill_name,
            len(skills),
            scanned_dirs,
        )
        return None

    return resolve


def _default_trust_resolver(skill_dir: Path) -> SkillTrustLevel:
    """默认可信判定：skill 目录位于包内置 skills 目录下视为 ``builtin``。"""
    try:
        from jiuwenswarm.utils import get_builtin_skills_dir

        builtin_root = get_builtin_skills_dir().resolve()
        resolved = Path(skill_dir).resolve()
        return (
            SkillTrustLevel.BUILTIN
            if (resolved == builtin_root or builtin_root in resolved.parents)
            else SkillTrustLevel.OTHER
        )
    except Exception:  # noqa: BLE001 — 判定失败一律按 other（更严格）
        logger.warning("[skill_authorization] trust resolve failed, fallback=other", exc_info=True)
        return SkillTrustLevel.OTHER


def _default_config_provider() -> dict[str, Any]:
    from jiuwenswarm.agentserver.permissions.config_loader import (
        get_effective_permissions_config,
    )

    return get_effective_permissions_config()


def _resolve_session_id(ctx: AgentCallbackContext) -> str | None:
    """从回调上下文提取 session_id（主 Agent 与子 Agent rail 共用）。"""
    session = getattr(ctx, "session", None)
    if session is None:
        return None
    for attr_name in ("get_session_id", "session_id"):
        value = getattr(session, attr_name, None)
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001
                continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_answer_to_action(
    answer: Any,
    *,
    allow_session: bool = True,
) -> SkillApprovalAction | None:
    """统一解析审批答案；无法识别返回 ``None``（fail-closed）。显式 ``action``
    无法识别时直接拒绝，不回落选项文案；``allow_session=False``（子 Agent）拒绝
    ``approve_session``。"""
    action: SkillApprovalAction | None = None
    if isinstance(answer, SkillApprovalAction):
        action = answer
    elif isinstance(answer, list):
        for item in answer:
            action = _parse_answer_to_action(item, allow_session=allow_session)
            if action is not None:
                return action
        return None
    elif isinstance(answer, dict):
        raw = answer.get("action") or answer.get("skill_action")
        if raw is not None:
            try:
                action = SkillApprovalAction(str(raw).strip())
            except ValueError:
                return None
        else:
            selected = answer.get("selected_options")
            if isinstance(selected, list):
                labels = {str(item).strip() for item in selected}
                if "本次允许" in labels or "允许" in labels:
                    action = SkillApprovalAction.APPROVE_ONCE
                elif "仅加载不授权" in labels or "拒绝" in labels:
                    action = SkillApprovalAction.CONTINUE_WITHOUT_OVERLAY
    elif isinstance(answer, str):
        text = answer.strip()
        try:
            action = SkillApprovalAction(text)
        except ValueError:
            try:
                parsed = json.loads(text)
            except Exception:  # noqa: BLE001
                return None
            return _parse_answer_to_action(parsed, allow_session=allow_session)
    if action is SkillApprovalAction.APPROVE_SESSION and not allow_session:
        return None
    return action


@dataclass(frozen=True)
class _SkillApprovalCall:
    """一次根 Skill 加载门禁的调用上下文（模板钩子参数封装，G.FNM.03）。"""

    ctx: AgentCallbackContext
    tool_call: Any
    tool_name: str
    tool_call_id: str
    session_id: str
    agent_scope_id: str
    manifest: SkillManifest
    diff: SkillPermissionDiff
    authorization_generation: int


class SkillAuthorizationRail(BaseInterruptRail):
    """Skill 加载门禁 rail（priority=95，高于 PermissionInterruptRail 的 90）。"""

    priority: int = 95

    #: 门禁日志前缀；子 Agent 委托版覆盖为 ``subagent.skill``。
    _gate_log_tag = "gate"

    def __init__(
        self,
        *,
        engine: Any = None,
        skill_resolver: SkillResolver | None = None,
        trust_resolver: TrustResolver | None = None,
        grant_store: SkillGrantStore | None = None,
        config_provider: ConfigProvider | None = None,
        agent_scope_id: str = MAIN_AGENT_SCOPE_ID,
    ) -> None:
        super().__init__(tool_names=[SKILL_TOOL_NAME, "skill_complete"])
        if engine is None:
            from jiuwenswarm.agentserver.permissions.core import get_permission_engine

            engine = get_permission_engine()
        self._engine = engine
        self._skill_resolver = skill_resolver
        self._trust_resolver = trust_resolver or _default_trust_resolver
        self._grant_store = grant_store
        self._config_provider = config_provider or _default_config_provider
        self._agent_scope_id = agent_scope_id
        # Skill 目录 -> ((permission 指纹, SKILL.md 指纹), Manifest|None)；任一变化重校验。
        self._manifest_cache: dict[
            str,
            tuple[tuple[tuple[int, int] | None, tuple[int, int] | None], SkillManifest | None],
        ] = {}

    # ---------- 基础设施 ----------

    @property
    def store(self) -> SkillGrantStore:
        if self._grant_store is None:
            self._grant_store = get_skill_grant_store()
        return self._grant_store

    def _feature_enabled(self) -> bool:
        """功能开关（决策时经缓存访问器读取，不用启动时快照）。"""
        try:
            return is_skill_authorization_enabled(self._config_provider())
        except Exception:  # noqa: BLE001 — 开关读取失败按关闭处理（fail-closed）
            logger.warning("[skill_authorization] feature flag read failed", exc_info=True)
            return False

    @staticmethod
    def _preserve_legacy_scene() -> bool:
        """数字分身使用既有专用裁决，动态授权不得接管。"""
        try:
            from jiuwenswarm.agentserver.deep_agent.permissions.owner_scopes import (
                TOOL_PERMISSION_CONTEXT,
            )

            permission_context = TOOL_PERMISSION_CONTEXT.get()
            return (
                permission_context is not None
                and permission_context.scene == "group_digital_avatar"
            )
        except Exception:  # noqa: BLE001 — 读取失败不扩大旁路范围
            return False

    @staticmethod
    def _mark_gate_handled(ctx: AgentCallbackContext) -> None:
        """标记本次调用已由动态授权门禁裁决，避免原权限 Rail 重复处理。

        写入 ``tool_call.id`` 而非布尔值：Agent 循环复用同一 ``ctx.extra`` 字典，
        只有标记值与当前调用 id 一致时 ``PermissionInterruptRail`` 才短路，使残留
        标记不会误伤同一次调用后续的其它工具（包括第二次 skill_tool 加载）。
        """
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        tool_call = getattr(getattr(ctx, "inputs", None), "tool_call", None)
        call_id = str(getattr(tool_call, "id", "") or "")
        extra[SKILL_AUTHORIZATION_GATE_HANDLED_KEY] = call_id or True

    @staticmethod
    def _clear_gate_handled(ctx: AgentCallbackContext) -> None:
        """仅清理当前 tool_call 的门禁标记，使原 PermissionRail 可接管。"""
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        tool_call = getattr(getattr(ctx, "inputs", None), "tool_call", None)
        call_id = str(getattr(tool_call, "id", "") or "")
        marker = extra.get(SKILL_AUTHORIZATION_GATE_HANDLED_KEY)
        if marker is True or marker == call_id:
            extra.pop(SKILL_AUTHORIZATION_GATE_HANDLED_KEY, None)

    def _claim_gate(
        self,
        ctx: AgentCallbackContext,
        authorization_generation: int,
    ) -> bool:
        """仅在开关与撤销代次仍有效时接管本次调用。"""
        if (
            authorization_generation != get_skill_authorization_generation()
            or not self._feature_enabled()
        ):
            self._clear_gate_handled(ctx)
            return False
        self._mark_gate_handled(ctx)
        return True

    def _resolve_scope(self, ctx: AgentCallbackContext) -> tuple[str, str] | None:
        """授权作用域：优先 ``SkillAuthorizationContext``，缺失时回落 session + 本 rail 作用域。

        Context 的 ``agent_scope_id`` 必须与本 rail 的作用域一致才采用——防止子 Agent
        任务继承主 Agent 请求 Context（scope="main"）而误读 / 误写主 Agent 的 Grant
        （设计：任何形式的授权继承都禁止）。
        """
        authz = get_skill_authorization_context()
        if authz is not None and authz.session_id:
            if authz.agent_scope_id and authz.agent_scope_id == self._agent_scope_id:
                return authz.session_id, authz.agent_scope_id
        session_id = _resolve_session_id(ctx)
        if not session_id:
            return None
        return session_id, self._agent_scope_id

    def _invoke_session_id(self, ctx: AgentCallbackContext) -> str | None:
        """before_invoke 绑定的 session：主 Agent 取当前会话。"""
        return _resolve_session_id(ctx)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """为整个 Agent 调用绑定本 rail 的独立权限作用域。

        主 Agent 和 fork/spawn 入口已有显式 Context 时会形成可恢复的嵌套绑定。
        """
        if not self._feature_enabled():
            return
        session_id = self._invoke_session_id(ctx)
        if not session_id:
            return
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        request_id = str(getattr(ctx, "request_id", "") or "")
        extra[SKILL_AUTHORIZATION_CONTEXT_TOKEN_KEY] = setup_skill_authorization_context(
            session_id,
            self._agent_scope_id,
            request_id,
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """恢复进入 Agent 调用前的授权 Context。"""
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        token = extra.pop(SKILL_AUTHORIZATION_CONTEXT_TOKEN_KEY, None)
        if token is not None:
            reset_skill_authorization_context(token)

    # ---------- Manifest（mtime 缓存） ----------

    def _load_manifest_cached(self, location: SkillLocation, skill_name: str) -> SkillManifest | None:
        skill_dir, source, version = location
        directory = Path(skill_dir)
        permission_file = directory / SKILL_PERMISSION_FILENAME
        skill_md_file = directory / ROOT_SKILL_FILE
        cache_key = str(directory)
        fingerprint = (
            _file_fingerprint(permission_file),
            _file_fingerprint(skill_md_file),
        )
        cached = self._manifest_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        manifest = load_skill_manifest(
            skill_dir,
            trust=self._trust_resolver(Path(skill_dir)),
            source=source,
            version=version,
            skill_name=skill_name,
        )
        self._manifest_cache[cache_key] = (fingerprint, manifest)
        return manifest

    # ---------- 待确认审批上下文 ----------

    @staticmethod
    def _read_pending_pool(ctx: AgentCallbackContext) -> dict[str, Any]:
        session = getattr(ctx, "session", None)
        if session is None:
            return {}
        try:
            data = session.get_state(PENDING_SKILL_APPROVAL_CONTEXT_KEY)
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}

    def _store_pending_approval(
        self,
        ctx: AgentCallbackContext,
        tool_call_id: str,
        payload: dict[str, Any],
    ) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not tool_call_id:
            return
        pending = dict(self._read_pending_pool(ctx))
        pending[tool_call_id] = payload
        session.update_state({PENDING_SKILL_APPROVAL_CONTEXT_KEY: pending})

    def _pop_pending_approval(
        self,
        ctx: AgentCallbackContext,
        tool_call_id: str,
    ) -> dict[str, Any] | None:
        session = getattr(ctx, "session", None)
        if session is None or not tool_call_id:
            return None
        pending = dict(self._read_pending_pool(ctx))
        payload = pending.pop(tool_call_id, None)
        session.update_state({PENDING_SKILL_APPROVAL_CONTEXT_KEY: pending})
        return payload if isinstance(payload, dict) else None

    # ---------- 权限差分 ----------

    @staticmethod
    def _effective_tool_level(base: dict[str, Any], tool_name: str) -> str:
        tools = base.get("tools")
        if isinstance(tools, dict):
            raw = tools.get(tool_name)
            if isinstance(raw, str) and raw.strip():
                return raw.strip().lower()
        raw = base.get("defaults")
        return raw.strip().lower() if isinstance(raw, str) and raw.strip() else "guard"

    @staticmethod
    def _file_guard_adjudication_view(
        base: dict[str, Any],
        merged: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], Any, bool] | None:
        """裁决口径的 file_guard 视图：归一后的 global 映射 + workspace 根 + rw_enabled。

        供差分按真实裁决语义计算 before/after；失败时返回 None，调用方回退到
        同路径条目口径。
        """
        try:
            from jiuwenswarm.agentserver.permissions.file_guard import (
                FileGuardChecker,
                merged_file_guard_config,
            )

            base_map = merged_file_guard_config(base).get("global")
            merged_map = merged_file_guard_config(merged).get("global")
            if not isinstance(base_map, dict) or not isinstance(merged_map, dict):
                return None
            ws_cfg = merged_file_guard_config(merged).get("workspace")
            rw_enabled = True
            if isinstance(ws_cfg, dict) and "rw_enabled" in ws_cfg:
                rw_enabled = bool(ws_cfg.get("rw_enabled"))
            ws_root = FileGuardChecker(merged).workspace_root()
            return base_map, merged_map, ws_root, rw_enabled
        except Exception:  # noqa: BLE001 — 展示判定失败时保留原有差分口径
            return None

    def _compute_permission_diff(
        self,
        base: dict[str, Any],
        overlay: dict[str, Any],
    ) -> SkillPermissionDiff:
        """权限差分：放宽项在前，收紧项其次，被 deny 拦截的声明最后。"""
        widened: list[str] = []
        tightened: list[str] = []
        rejected: list[str] = []
        merged = compose_skill_permissions(base, overlay)

        overlay_tools = overlay.get("tools") if isinstance(overlay.get("tools"), dict) else {}
        merged_tools = merged.get("tools") if isinstance(merged.get("tools"), dict) else {}
        for tool_name, declared in overlay_tools.items():
            declared_level = str(declared).strip().lower()
            before = self._effective_tool_level(base, tool_name)
            after = str(merged_tools.get(tool_name, before)).strip().lower()
            if before == "deny":
                rejected.append(f"工具 `{tool_name}`：全局 deny 不可改变（声明 {declared_level} 已丢弃）")
            elif after == before:
                continue
            elif after == "allow":
                widened.append(f"工具 `{tool_name}`：{before} → allow")
            else:
                tightened.append(f"工具 `{tool_name}`：{before} → {after}")

        overlay_rules = overlay.get("rules") if isinstance(overlay.get("rules"), list) else []
        for rule in overlay_rules:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern") or "").strip()
            action = str(rule.get("action") or "").strip().lower()
            if not pattern:
                continue
            if action == "allow":
                widened.append(f"新增命令允许规则：`{pattern}`")
            elif action == "deny":
                tightened.append(f"新增命令禁止规则：`{pattern}`")

        overlay_fg = overlay.get("file_guard") if isinstance(overlay.get("file_guard"), dict) else {}
        overlay_global = overlay_fg.get("global") if isinstance(overlay_fg.get("global"), dict) else {}
        merged_fg = merged.get("file_guard") if isinstance(merged.get("file_guard"), dict) else {}
        merged_global = merged_fg.get("global") if isinstance(merged_fg.get("global"), dict) else {}
        base_fg = base.get("file_guard") if isinstance(base.get("file_guard"), dict) else {}
        base_global = base_fg.get("global") if isinstance(base_fg.get("global"), dict) else {}
        adjudication = self._file_guard_adjudication_view(base, merged)
        for path, entry in overlay_global.items():
            if not isinstance(entry, dict):
                continue
            base_entry = base_global.get(path) if isinstance(base_global.get(path), dict) else {}
            merged_entry = merged_global.get(path) if isinstance(merged_global.get(path), dict) else {}
            for axis, declared in entry.items():
                declared_level = str(declared).strip().lower()
                before = after = None
                if adjudication is not None:
                    base_map, merged_map, ws_root, rw_enabled = adjudication
                    before = effective_file_guard_axis_level(
                        base_map, str(path), str(axis),
                        workspace_root=ws_root, rw_enabled=rw_enabled,
                    )
                    after = effective_file_guard_axis_level(
                        merged_map, str(path), str(axis),
                        workspace_root=ws_root, rw_enabled=rw_enabled,
                    )
                if before is None or after is None:
                    # 展示判定失败时回退到同路径条目口径。
                    before = str(base_entry.get(axis) or "ask").strip().lower()
                    after = str(merged_entry.get(axis, before)).strip().lower()
                if declared_level in ("allow", "ask") and after == "deny":
                    # 含 base 祖先 deny 与同一 overlay 自声明祖先 deny 两种拦截。
                    rejected.append(
                        f"文件 `{path}` {axis}：父路径/全局 deny 不可改变（声明 {declared_level} 已丢弃）"
                    )
                elif after == before:
                    # 声明不改变裁决（如同档声明、workspace 内 read/write 短路），不展示。
                    continue
                elif after == "allow":
                    widened.append(f"文件 `{path}` {axis}：{before} → allow")
                else:
                    tightened.append(f"文件 `{path}` {axis}：{before} → {after}")
        return SkillPermissionDiff(
            widened=tuple(widened),
            tightened=tuple(tightened),
            rejected=tuple(rejected),
        )

    # ---------- 审批卡 ----------

    def _build_approval_card(
        self,
        manifest: SkillManifest,
        diff: SkillPermissionDiff,
        cached_grant: Any,
        agent_scope_id: str,
    ) -> SkillApprovalCard:
        """组装结构化审批卡（Web / CLI 对接契约，见 ``SkillApprovalCard``）。"""
        actions = [SkillApprovalAction.APPROVE_ONCE.value]
        if manifest.trust == SkillTrustLevel.BUILTIN:
            actions.append(SkillApprovalAction.APPROVE_SESSION.value)
        actions.append(SkillApprovalAction.CONTINUE_WITHOUT_OVERLAY.value)
        return SkillApprovalCard(
            skill_name=manifest.skill_name,
            source=manifest.source,
            version=manifest.version,
            trust=manifest.trust,
            permissions_hash=manifest.permissions_hash,
            agent_scope_id=agent_scope_id,
            diff=diff,
            actions=tuple(actions),
            cached_decision=cached_grant.decision.value if cached_grant is not None else None,
        )

    @staticmethod
    def _render_approval_message(card: SkillApprovalCard) -> str:
        trust_label = "内置" if card.trust == SkillTrustLevel.BUILTIN else "外部"
        version = card.version or "本地（无版本）"
        parts = [
            f"**Skill 加载审批：`{card.skill_name}`**\n\n",
            f"> 来源：{card.source}（{trust_label}）　版本：{version}\n",
            f"> 权限声明摘要：`{card.permissions_hash[:12]}`\n",
        ]
        if card.cached_decision is not None:
            decision_label = "本次允许" if card.cached_decision == GrantDecision.LOCAL.value else "会话内允许"
            parts.append(f"> 该 Skill 已在本会话获批（上次决策：{decision_label}），确认后复用审批记录。\n")
        if card.diff.widened:
            parts.append("\n**放宽项（批准后生效）：**\n")
            parts.extend(f"\n- {item}" for item in card.diff.widened)
            parts.append("\n")
        if card.diff.tightened:
            parts.append("\n**收紧项（批准后生效）：**\n")
            parts.extend(f"\n- {item}" for item in card.diff.tightened)
            parts.append("\n")
        if card.diff.rejected:
            parts.append("\n**被安全策略丢弃的声明（deny 不可突破）：**\n")
            parts.extend(f"\n- {item}" for item in card.diff.rejected)
            parts.append("\n")
        if not any((card.diff.widened, card.diff.tightened, card.diff.rejected)):
            parts.append("\n该 Skill 声明的权限与当前配置一致，无有效差分。\n")
        parts.append(
            "\n请选择：`approve_once`（本次允许，仅本次 Skill 激活期有效）"
        )
        if SkillApprovalAction.APPROVE_SESSION.value in card.actions:
            parts.append(" / `approve_session`（会话内允许，仅内置 Skill 可选）")
        parts.append(" / `continue_without_overlay`（仅加载不授权，按原权限运行）。")
        return "".join(parts)

    @staticmethod
    def _build_ui_options(card: SkillApprovalCard) -> list[dict[str, str]]:
        labels = {
            SkillApprovalAction.APPROVE_ONCE.value: (
                "本次允许", "批准 Skill 权限声明，仅本次激活期有效",
            ),
            SkillApprovalAction.APPROVE_SESSION.value: (
                "会话内允许", "批准后在会话内保留审批记录，Skill 结束后失活",
            ),
            SkillApprovalAction.CONTINUE_WITHOUT_OVERLAY.value: (
                "仅加载不授权", "仅加载 Skill，不授予额外权限（按原有权限运行）",
            ),
        }
        options: list[dict[str, str]] = []
        for action in card.actions:
            label_and_description = labels.get(action)
            if label_and_description is None:
                logger.warning(
                    "[skill_authorization] approval_card.unknown_action action=%s",
                    action,
                )
                continue
            label, description = label_and_description
            options.append({
                "label": label,
                "action": action,
                "description": description,
            })
        return options

    # ---------- 门禁（before_tool_call） ----------

    def _evaluate_skill_tool_deny(
        self,
        tool_args: dict[str, Any],
        session_id: str | None,
    ) -> str | None | object:
        """按原权限流程裁决 skill_tool；DENY 返回原因，否则返回 ``None``。"""
        try:
            from jiuwenswarm.agentserver.permissions.checker import TOOL_PERMISSION_CHANNEL_ID

            channel_id = TOOL_PERMISSION_CHANNEL_ID.get() or "web"
        except Exception:  # noqa: BLE001
            channel_id = "web"
        try:
            level, matched_rule, _, _ = self._engine.evaluate_global_policy_with_details(
                SKILL_TOOL_NAME,
                tool_args,
                channel_id=channel_id,
                session_id=session_id,
                apply_skill_overlay=False,
            )
        except Exception:  # noqa: BLE001 — 调用方必须走原 Rail 或直接拒绝
            logger.warning("[skill_authorization] gate policy evaluation failed", exc_info=True)
            return _POLICY_EVALUATION_FAILED
        if level == PermissionLevel.DENY:
            return str(matched_rule or "tiered_policy")
        return None

    def _resolve_manifest_for_call(
        self,
        skill_name: str,
    ) -> SkillManifest | None:
        if self._skill_resolver is None or not skill_name:
            return None
        try:
            location = self._skill_resolver(skill_name)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[skill_authorization] skill resolve failed skill=%s", skill_name, exc_info=True,
            )
            return None
        if location is None:
            logger.info(
                "[skill_authorization] skill not in registry skill=%s（注册表未扫到或未注册，按无声明处理）",
                skill_name,
            )
            return None
        try:
            return self._load_manifest_cached(location, skill_name)
        except Exception:  # noqa: BLE001 — Manifest 校验失败按不可授权处理（fail-closed）
            logger.warning(
                "[skill_authorization] manifest load failed skill=%s", skill_name, exc_info=True,
            )
            return None

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """门禁模板：主 Agent 与子 Agent 委托版共用裁决骨架，差异走钩子方法。"""
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if tool_name not in (SKILL_TOOL_NAME, "skill_complete"):
            return
        tool_call = getattr(inputs, "tool_call", None)
        tool_args = parse_tool_call_arguments(tool_call)
        if self._preserve_legacy_scene():
            return
        authorization_generation = get_skill_authorization_generation()
        if not self._feature_enabled():
            return
        if tool_name == "skill_complete":
            self._claim_gate(ctx, authorization_generation)
            return
        if not is_root_skill_load_call(tool_name, tool_args):
            return

        skill_name = str(tool_args.get("skill_name") or "").strip()
        tool_call_id = self._resolve_tool_call_id(tool_call)
        scope = self._resolve_scope(ctx)
        session_id = scope[0] if scope else _resolve_session_id(ctx)

        # 1. 原权限流程裁决：DENY 直接拒载，不弹卡。
        deny_rule = self._evaluate_skill_tool_deny(tool_args, session_id)
        if deny_rule is _POLICY_EVALUATION_FAILED:
            self._on_policy_evaluation_failed(ctx, tool_call, tool_name)
            return
        if deny_rule is not None:
            if not self._claim_gate(ctx, authorization_generation):
                return
            logger.warning(
                "[skill_authorization] %s.deny skill=%s session=%s rule=%s",
                self._gate_log_tag, skill_name, session_id, deny_rule,
            )
            self._apply_decision(ctx, tool_call, tool_name, self.reject(
                tool_result=f"[PERMISSION_DENIED] skill_tool 被权限策略拒绝加载 (rule: {deny_rule})",
            ))
            return

        # 2. Manifest（mtime 缓存）。无法解析时按无声明放行，不创建 Grant。
        manifest = self._resolve_manifest_for_call(skill_name)
        if manifest is None:
            if not self._claim_gate(ctx, authorization_generation):
                return
            logger.warning(
                "[skill_authorization] %s.no_overlay skill=%s session=%s reason=manifest_unresolved",
                self._gate_log_tag, skill_name, session_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        # 3. 作用域缺失 → 无法绑定 Grant，按无声明放行。
        if scope is None:
            if not self._claim_gate(ctx, authorization_generation):
                return
            logger.warning(
                "[skill_authorization] %s.no_overlay skill=%s reason=missing_scope",
                self._gate_log_tag, skill_name,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return
        session_id, agent_scope_id = scope

        # 4. 只有实际改变当前权限的声明才触发动态授权，否则不创建 Grant。
        if (
            not manifest.authorizable
            or not manifest.skill_md_hash
            or not _overlay_has_effect(manifest.overlay)
        ):
            if not self._claim_gate(ctx, authorization_generation):
                return
            logger.info(
                "[skill_authorization] %s.no_overlay skill=%s session=%s scope=%s "
                "reason=manifest_has_no_authorizable_overlay",
                self._gate_log_tag, manifest.skill_name, session_id, agent_scope_id,
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        base_cfg = self._base_effective_config(session_id)
        diff = self._compute_permission_diff(base_cfg, manifest.overlay)
        if not diff.widened and not diff.tightened:
            if not self._claim_gate(ctx, authorization_generation):
                return
            logger.info(
                "[skill_authorization] %s.no_overlay skill=%s session=%s scope=%s "
                "reason=no_effective_permission_change rejected=%s",
                self._gate_log_tag,
                manifest.skill_name, session_id, agent_scope_id, list(diff.rejected),
            )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        # 5. 基础 DENY 与差分裁决完成，标记由本 rail 专属处理，避免原权限链重复弹卡。
        if not self._claim_gate(ctx, authorization_generation):
            return

        # session 决策复用：已批准过的 skill 不重复弹卡。
        approval_call = _SkillApprovalCall(
            ctx=ctx, tool_call=tool_call, tool_name=tool_name, tool_call_id=tool_call_id,
            session_id=session_id, agent_scope_id=agent_scope_id, manifest=manifest,
            diff=diff, authorization_generation=authorization_generation,
        )
        if self._try_reuse_session_approval(approval_call):
            return

        if (
            authorization_generation != get_skill_authorization_generation()
            or not self._feature_enabled()
        ):
            # 开关在本次门禁计算期间关闭
            self._on_gate_stale(ctx, tool_call, tool_name)
            return

        # 合法 low 风险声明自动批准；仅写本作用域 PENDING_ACTIVATION，待正文加载后激活。
        if manifest.risk_status == "valid" and manifest.risk_level == "low":
            if (
                authorization_generation != get_skill_authorization_generation()
                or not self._feature_enabled()
            ):
                self._apply_decision(
                    ctx, tool_call, tool_name,
                    self.reject(tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更"),
                )
                return
            if self._create_pending_grant_quiet(
                session_id, agent_scope_id, manifest, tool_call_id,
                decision=GrantDecision.LOCAL, log_tag=f"{self._gate_log_tag}.auto_approve_failed",
            ):
                logger.info(
                    "[skill_authorization] %s.auto_approved skill=%s session=%s scope=%s "
                    "risk=low decision=local",
                    self._gate_log_tag, manifest.skill_name, session_id, agent_scope_id,
                )
            self._proceed_tool_call(ctx, tool_call, tool_name)
            return

        # 6. 非 low 变更需用户批准。
        await self._run_approval_flow(approval_call)

    # ---------- 门禁模板钩子（子 Agent 委托版覆盖） ----------

    def _proceed_tool_call(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        """主 Agent：门禁已标记 handled，原权限 Rail 跳过后调用自然继续。"""

    def _on_policy_evaluation_failed(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        """主 Agent：不写 handled 标记，让原 PermissionInterruptRail 接管。"""

    def _on_gate_stale(self, ctx: AgentCallbackContext, tool_call: Any, tool_name: str) -> None:
        """主 Agent：门禁计算期间配置变更，交还原权限 Rail 重新裁决。"""
        self._clear_gate_handled(ctx)

    def _try_reuse_session_approval(self, call: _SkillApprovalCall) -> bool:
        """复用可信 session 审批记录；本次调用已处理（无论成败）返回 ``True``。"""
        session_id, agent_scope_id, manifest = call.session_id, call.agent_scope_id, call.manifest
        reusable = self._find_reusable_approval(session_id, agent_scope_id, manifest)
        if reusable is None:
            return False
        if (
            call.authorization_generation != get_skill_authorization_generation()
            or not self._feature_enabled()
        ):
            self._apply_decision(
                call.ctx, call.tool_call, call.tool_name,
                self.reject(tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更"),
            )
            return True
        # 复用失败按无 overlay 放行
        if self._create_pending_grant_quiet(
            session_id, agent_scope_id, manifest, call.tool_call_id,
            decision=GrantDecision.SESSION, log_tag=f"{self._gate_log_tag}.session_reuse_failed",
        ):
            logger.info(
                "[skill_authorization] %s.session_reused skill=%s session=%s scope=%s",
                self._gate_log_tag, manifest.skill_name, session_id, agent_scope_id,
            )
        return True

    def _create_pending_grant_quiet(
        self, session_id: str, agent_scope_id: str, manifest: SkillManifest,
        tool_call_id: str, *, decision: GrantDecision, log_tag: str,
    ) -> bool:
        """写 PENDING_ACTIVATION Grant；失败只记日志返回 False（调用方按无 overlay 放行）。"""
        try:
            self.store.create_pending_grant(
                session_id, agent_scope_id, manifest,
                decision=decision, approval_tool_call_id=tool_call_id,
            )
            return True
        except Exception:  # noqa: BLE001
            logger.warning(
                "[skill_authorization] %s skill=%s session=%s scope=%s",
                log_tag, manifest.skill_name, session_id, agent_scope_id, exc_info=True,
            )
            return False

    async def _run_approval_flow(self, call: _SkillApprovalCall) -> None:
        """主 Agent 审批：首次弹 checkpoint interrupt 卡，恢复后处理审批结果。"""
        ctx, tool_call, tool_name = call.ctx, call.tool_call, call.tool_name
        tool_call_id, session_id, agent_scope_id = call.tool_call_id, call.session_id, call.agent_scope_id
        manifest, diff = call.manifest, call.diff
        authorization_generation = call.authorization_generation
        user_input = self._get_user_input(ctx, tool_call_id)
        if user_input is None:
            if (
                authorization_generation != get_skill_authorization_generation()
                or not self._feature_enabled()
            ):
                self._apply_decision(
                    ctx, tool_call, tool_name,
                    self.reject(tool_result="[PERMISSION_DENIED] Skill 动态授权配置已变更"),
                )
                return
            card = self._build_approval_card(manifest, diff, None, agent_scope_id)
            self._store_pending_approval(ctx, tool_call_id, {
                "skill_name": manifest.skill_name,
                "source": manifest.source,
                "version": manifest.version,
                "permissions_hash": manifest.permissions_hash,
                "skill_md_hash": manifest.skill_md_hash,
                "authorization_generation": authorization_generation,
            })
            logger.info(
                "[skill_authorization] gate.ask skill=%s session=%s hash=%s cached=%s "
                "risk_status=%s risk_level=%s",
                manifest.skill_name, session_id, manifest.permissions_hash,
                False, manifest.risk_status, manifest.risk_level,
            )
            self._apply_decision(ctx, tool_call, tool_name, self.interrupt(InterruptRequest(
                message=self._render_approval_message(card),
                payload_schema={
                    **SKILL_APPROVAL_PAYLOAD_SCHEMA,
                    SKILL_APPROVAL_CARD_EXTENSION_KEY: card.to_dict(),
                },
                ui_options=self._build_ui_options(card),
                allow_auto_confirm=False,
            )))
            return

        decision = self._handle_approval_response(
            ctx, tool_call_id, (session_id, agent_scope_id), manifest, user_input,
        )
        self._apply_decision(ctx, tool_call, tool_name, decision)

    def _find_reusable_approval(
        self,
        session_id: str,
        agent_scope_id: str,
        manifest: SkillManifest,
    ) -> Any:
        """返回可自动复用的可信 session 审批记录。"""
        try:
            grant = self.store.get_grant(session_id, agent_scope_id, manifest.skill_name)
        except Exception:  # noqa: BLE001
            logger.warning("[skill_authorization] grant store read failed", exc_info=True)
            return None
        if grant is None:
            return None
        reusable = (
            grant.status == GrantStatus.APPROVED_INACTIVE
            and grant.decision == GrantDecision.SESSION
            and manifest.trust == SkillTrustLevel.BUILTIN
        )
        if not reusable:
            return None
        return grant if grant.identity_tuple() == manifest.identity_tuple() else None

    def _base_effective_config(self, session_id: str | None) -> dict[str, Any]:
        """差分基线：全局 + 会话 overlay（不含 Skill overlay）。"""
        try:
            from jiuwenswarm.agentserver.permissions.config_loader import (
                merge_session_permissions_overlay,
            )

            base = merge_session_permissions_overlay(
                getattr(self._engine, "config", {}) or {},
                session_id=session_id,
            )
            return base if isinstance(base, dict) else {}
        except Exception:  # noqa: BLE001
            logger.warning("[skill_authorization] base config resolve failed", exc_info=True)
            return {}

    def _retire_scope_after_ungranted_load(
        self,
        session_id: str,
        agent_scope_id: str,
        *,
        reason: str,
    ) -> None:
        """当前加载未形成 ACTIVE Grant 时退出旧 overlay。

        正文加载失败、用户选择不授权、Manifest 漂移或激活失败时，
        都不得继续沿用上一个 Skill 的权限。
        """
        try:
            self.store.invalidate_scope(session_id, agent_scope_id, reason=reason)
            self.store.revoke_grants(
                session_id,
                agent_scope_id,
                reason=reason,
            )
        except Exception:  # noqa: BLE001 — 回收失败不覆盖已成功的工具结果
            logger.warning(
                "[skill_authorization] previous grant retire failed "
                "session=%s scope=%s reason=%s",
                session_id,
                agent_scope_id,
                reason,
                exc_info=True,
            )

    def _handle_approval_response(
        self,
        ctx: AgentCallbackContext,
        tool_call_id: str,
        scope: tuple[str, str],
        manifest: SkillManifest,
        user_input: Any,
    ) -> Any:
        session_id, agent_scope_id = scope
        pending = self._pop_pending_approval(ctx, tool_call_id)
        if pending is None:
            logger.warning(
                "[skill_authorization] gate.reject skill=%s reason=pending_context_missing",
                manifest.skill_name,
            )
            return self.reject(tool_result="[PERMISSION_REJECTED] Skill 审批上下文不存在或已失效。")
        if pending.get("authorization_generation") != get_skill_authorization_generation():
            logger.warning(
                "[skill_authorization] gate.reject skill=%s reason=authorization_generation_changed",
                manifest.skill_name,
            )
            return self.reject(tool_result="[PERMISSION_REJECTED] Skill 审批已因授权配置关闭而失效。")
        if not _pending_matches_manifest(pending, manifest):
            logger.warning(
                "[skill_authorization] gate.reject skill=%s reason=pending_identity_mismatch",
                manifest.skill_name,
            )
            return self.reject(tool_result="[PERMISSION_REJECTED] Skill 审批上下文与当前加载不匹配。")

        action = _parse_answer_to_action(user_input)
        if action is None:
            logger.warning(
                "[skill_authorization] gate.reject skill=%s reason=unrecognized_approval_payload",
                manifest.skill_name,
            )
            return self.reject(tool_result="[PERMISSION_REJECTED] 无法识别的 Skill 审批结果。")

        if action == SkillApprovalAction.CONTINUE_WITHOUT_OVERLAY:
            logger.info(
                "[skill_authorization] gate.no_overlay skill=%s session=%s reason=user_continue_without_overlay",
                manifest.skill_name, session_id,
            )
            return self.approve()

        if action == SkillApprovalAction.APPROVE_SESSION and manifest.trust != SkillTrustLevel.BUILTIN:
            logger.warning(
                "[skill_authorization] gate.reject skill=%s reason=approve_session_not_builtin",
                manifest.skill_name,
            )
            return self.reject(tool_result="[PERMISSION_REJECTED] 会话内允许仅对内置 Skill 开放。")

        # 防御性校验：空声明或非法声明不得因旧的恢复载荷产生 Grant。
        if (
            not manifest.authorizable
            or not manifest.skill_md_hash
            or not _overlay_has_effect(manifest.overlay)
        ):
            logger.info(
                "[skill_authorization] gate.load_confirmed skill=%s session=%s overlay_authorized=false",
                manifest.skill_name, session_id,
            )
            return self.approve()

        decision = (
            GrantDecision.LOCAL
            if action == SkillApprovalAction.APPROVE_ONCE
            else GrantDecision.SESSION
        )
        # 写 Grant 失败按不授权放行（fail-closed）
        if not self._create_pending_grant_quiet(
            session_id, agent_scope_id, manifest, tool_call_id,
            decision=decision, log_tag=f"{self._gate_log_tag}.grant_create_failed",
        ):
            return self.approve()
        logger.info(
            "[skill_authorization] gate.approved skill=%s session=%s decision=%s",
            manifest.skill_name, session_id, decision.value,
        )
        return self.approve()

    # ---------- 激活 / 回收（after_tool_call） ----------

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._feature_enabled():
            return
        if self._preserve_legacy_scene():
            return
        event = extract_skill_lifecycle_event(ctx)
        if event is None:
            return
        scope = self._resolve_scope(ctx)
        if scope is None:
            return
        session_id, agent_scope_id = scope

        if is_skill_complete(event):
            try:
                self.store.invalidate_scope(
                    session_id,
                    agent_scope_id,
                    reason="skill_complete",
                )
                self.store.revoke_grants(
                    session_id,
                    agent_scope_id,
                    skill_name=event.skill_name or None,
                    reason="skill_complete",
                )
            except Exception:  # noqa: BLE001 — 回收失败不影响工具结果
                logger.warning("[skill_authorization] grant revoke failed", exc_info=True)
            return

        if event.tool_name != SKILL_TOOL_NAME or event.relative_file_path != ROOT_SKILL_FILE:
            return

        if not event.is_skill_body:
            # 根 SKILL.md 加载失败：清候选并退出旧 overlay，回落基础权限。
            if event.skill_name:
                self.store.drop_pending(
                    session_id,
                    agent_scope_id,
                    event.skill_name,
                    reason="skill_body_load_failed",
                )
            self._retire_scope_after_ungranted_load(
                session_id,
                agent_scope_id,
                reason="skill_body_load_failed",
            )
            return

        # 正文加载成功：重新核验 Manifest 身份（含 permissions_hash / skill_md_hash）后激活。
        manifest = self._resolve_manifest_for_call(event.skill_name)
        if manifest is None or not manifest.authorizable:
            self.store.drop_pending(
                session_id, agent_scope_id, event.skill_name, reason="manifest_mismatch",
            )
            self._retire_scope_after_ungranted_load(
                session_id,
                agent_scope_id,
                reason="skill_load_manifest_mismatch",
            )
            return
        expected_md_hash = manifest.skill_md_hash
        from jiuwenswarm.agentserver.permissions.skill_authorization import compute_skill_md_hash

        body = _read_loaded_skill_body(ctx, event)
        if expected_md_hash and (body is None or compute_skill_md_hash(body) != expected_md_hash):
            logger.warning(
                "[skill_authorization] activate.rejected skill=%s reason=skill_md_hash_mismatch",
                event.skill_name,
            )
            self.store.drop_pending(
                session_id, agent_scope_id, event.skill_name, reason="skill_md_hash_mismatch",
            )
            self._retire_scope_after_ungranted_load(
                session_id,
                agent_scope_id,
                reason="skill_load_body_hash_mismatch",
            )
            return
        try:
            activated = self.store.activate_pending(
                session_id,
                agent_scope_id,
                event.skill_name,
                manifest,
                approval_tool_call_id=event.tool_call_id or None,
            )
        except Exception:  # noqa: BLE001 — 激活失败不影响工具结果（fail-closed）
            logger.warning("[skill_authorization] activate failed", exc_info=True)
            self._retire_scope_after_ungranted_load(
                session_id,
                agent_scope_id,
                reason="skill_load_activation_failed",
            )
            return
        if activated is None:
            logger.info(
                "[skill_authorization] activate.skipped skill=%s session=%s reason=no_matching_pending",
                event.skill_name, session_id,
            )
            self._retire_scope_after_ungranted_load(
                session_id,
                agent_scope_id,
                reason="skill_load_without_grant",
            )


# ---------- 模块级小工具 ----------


def _registered_skill_version(skill: Any) -> str | None:
    """从注册表对象提取稳定版本；空白或非标量值按缺失处理。"""
    candidates = [getattr(skill, "version", None)]
    metadata = getattr(skill, "metadata", None)
    if isinstance(metadata, dict):
        candidates.append(metadata.get("version"))
        candidates.append(metadata.get("skill_version"))
    if isinstance(skill, dict):
        candidates.append(skill.get("version"))
        candidates.append(skill.get("skill_version"))
    for candidate in candidates:
        if isinstance(candidate, (str, int, float)):
            normalized = str(candidate).strip()
            if normalized:
                return normalized
    return None


def _overlay_has_effect(overlay: Any) -> bool:
    """规范化 overlay 是否含有实际权限声明（tools/rules/file_guard.global 任一非空）。"""
    if not isinstance(overlay, dict):
        return False
    if isinstance(overlay.get("tools"), dict) and overlay["tools"]:
        return True
    if isinstance(overlay.get("rules"), list) and overlay["rules"]:
        return True
    fg = overlay.get("file_guard")
    return bool(
        isinstance(fg, dict)
        and isinstance(fg.get("global"), dict)
        and fg["global"]
    )


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    """返回足以使 Manifest 缓存失效的纳秒 mtime 与大小。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _pending_matches_manifest(pending: dict[str, Any], manifest: SkillManifest) -> bool:
    """恢复的审批上下文与当前 Manifest 身份必须一致（防跨调用串用）。"""
    return (
        pending.get("skill_name") == manifest.skill_name
        and pending.get("source") == manifest.source
        and pending.get("version") == manifest.version
        and pending.get("permissions_hash") == manifest.permissions_hash
        and pending.get("skill_md_hash") == manifest.skill_md_hash
    )


def _read_loaded_skill_body(ctx: AgentCallbackContext, event: Any) -> str | None:
    """读取本次加载的 SKILL.md 正文：优先 session active state，回落 tool_msg.content。"""
    try:
        from openjiuwen.core.context_engine.active_skill_bodies import (
            ACTIVE_SKILL_BODIES_STATE_KEY,
            _state_key,
            normalize_skill_relative_file_path,
        )

        ctx_model = getattr(ctx, "context", None)
        session = getattr(ctx_model, "_session_ref", None) if ctx_model is not None else None
        if session is None:
            session = getattr(ctx, "session", None)
        if session is not None:
            active = session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY) or {}
            if isinstance(active, dict):
                key = _state_key(
                    event.skill_name,
                    normalize_skill_relative_file_path(event.relative_file_path),
                )
                entry = active.get(key)
                if isinstance(entry, dict):
                    body = entry.get("body")
                    if isinstance(body, str) and body:
                        return body
    except Exception:  # noqa: BLE001
        logger.debug("[skill_authorization] read body from session failed", exc_info=True)
    inputs = getattr(ctx, "inputs", None)
    tool_msg = getattr(inputs, "tool_msg", None) if inputs is not None else None
    content = getattr(tool_msg, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    return None


__all__ = [
    "MAIN_AGENT_SCOPE_ID",
    "PENDING_SKILL_APPROVAL_CONTEXT_KEY",
    "SKILL_APPROVAL_PAYLOAD_SCHEMA",
    "SkillAuthorizationRail",
    "build_skill_registry_resolver",
]
