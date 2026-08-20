# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillUseRail 子类：补 agent-core 的 session baseline「只增不删」缺陷。

背景
----
``SkillUseRail`` 在每个 ``before_model_call`` / ``before_invoke`` 里把「session 起始可见
技能集」快照进 ``session.state["skill_use"]["baseline_skills"]``（``_ensure_session_baseline``，
``skill_use_rail.py:522``-528）：**只在 state 不存在时存一次，之后永不刷新**。

专家装载时 ``_bind_skill`` → ``reload_skills`` 把专家技能加入 ``self.skills``，下一轮首次
``_ensure_session_baseline`` 把它们一并写进 baseline 快照；专家卸载时（jiuwenswarm 的
``_purge_expert_skill_mounts`` 已清 ``self.skills``/``skills_dir``/``enabled_skills``，见
``expert_capability`` PM-18 第一层），**baseline 不被任何路径刷新**——于是：

- 主 system 的 ``# 技能`` 段用 baseline 渲染（``_build_skills_section(baseline_skills)``，
  ``skill_use_rail.py:440``）→ 仍列已卸载专家的技能概述（用户感知：system 没变）；
- ``_update_runtime_skill_change_content`` 算 ``removals``（baseline 有、``self.skills`` 无）
  → 每轮注入 ``<prompt-attachment type="skill">...已移除...</prompt-attachment>``。

纯 jiuwenswarm 侧没有稳定的 session 节点句柄可直接 ``update_state({"skill_use": None})``
（``SessionManager`` 只管任务队列；DeepAgent 的 ``_loop_session`` 是 invoke 期 transient），
故本子类在 rail 自身的 ``before_model_call`` / ``before_invoke`` 入口（有 ``ctx.session``）
前置一步 baseline 刷新：剔除 baseline 里「directory 已不在 ``skills_dir`` 任何 root 下」的
技能（确定是卸载，而非 reload 时序导致 ``self.skills`` 暂缺），若有变化则重存 baseline =
当前 ``self.skills``。刷新发生在 agent-core 的 ``_ensure_session_baseline`` 之前，故后者见
state 已存在 → 跳过 → 用刷新后的 baseline，system 段与 attachment diff 同步对齐。

幂等：directory 仍在 ``skills_dir`` 的技能保留，仅清已卸载的；无 baseline（state 为空）时
不动（下轮 ``_ensure_session_baseline`` 会用 ``self.skills`` 建首份）。与上游 openjiuwen
若将来修 ``_ensure_session_baseline``/``_unbind`` 同步刷 baseline，本子类与之共存不冲突
（``_reconcile`` 无变化时直接 return，等价于透传）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.rails import SkillUseRail

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

logger = logging.getLogger(__name__)


class ReconcilingSkillUseRail(SkillUseRail):
    """SkillUseRail 子类：before_model_call/before_invoke 前置 baseline 刷新。

    仅扩展 baseline 刷新；其余行为（技能扫描、过滤、section 渲染、attachment 注入）
    全部继承自 ``SkillUseRail``。
    """

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._reconcile_session_baseline(ctx)
        await super().before_model_call(ctx)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._reconcile_session_baseline(ctx)
        await super().before_invoke(ctx)

    def _reconcile_session_baseline(self, ctx: AgentCallbackContext) -> None:
        """双向对齐 session baseline 与当前 self.skills（mounted 的）。

        agent-core 的 ``_ensure_session_baseline`` 只在 session 首次有 skill 调用时建
        一份 baseline=``self.skills``，之后永不刷新（``skill_use_rail.py:522``-528）。
        导致两种残留：

        - **删方向**（卸载）：purge 清了 ``self.skills``/``skills_dir`` 但 baseline 仍含
          已卸载专家技能 → 主 system ``# 技能`` 段用旧 baseline 渲染残留 + attachment
          每轮注入 ``已移除`` diff；
        - **增方向**（装载）：若 baseline 首建于会话无专家时（``self.skills`` 空），后
          装载专家 ``self.skills`` 变非空但 baseline 仍空 → 主 system ``# 技能`` 段渲染
          NO_SKILL fallback（``当前任务没有选择任何技能``），attachment 又把专家技能算
          成 additions 注入 ``新增可用 Skill``。

        本方法在 ``_ensure_session_baseline`` 之前双向对齐：删 baseline 里 directory
        已不在 ``skills_dir`` 的（确定卸载，不依赖 ``self.skills`` 避 reload 时序误删），
        补 ``self.skills`` 里有但 baseline 无、且其 directory 仍在 ``skills_dir`` 的（确定
        装载）。有任一变化即重存 baseline=当前 ``self.skills``，使主 system 段与 attachment
        都基于一致的技能集。
        """
        session = getattr(ctx, "session", None)
        if session is None:
            return
        state = self._load_session_state(session)
        if state is None:
            return  # 无 baseline：下轮 _ensure_session_baseline 用 self.skills 建首份

        baseline = state.get("baseline_skills")
        baseline = baseline if isinstance(baseline, list) else []
        baseline_names = {
            item.get("name") for item in baseline
            if isinstance(item, dict) and item.get("name")
        }

        mounted_roots = self._mounted_skill_roots()
        # skills_dir 为空：reload_skills 不可达，无 mount 可判；交由 _prepare_skills 兜底
        if not mounted_roots:
            return

        # 删方向：baseline 含、directory 已不在 skills_dir 任何 root 下 → stale
        stale: list = []
        for item in baseline:
            if not isinstance(item, dict):
                continue
            directory = item.get("directory")
            if not directory:
                continue
            try:
                resolved = Path(str(directory)).expanduser().resolve()
            except (OSError, ValueError):
                stale.append(item)
                continue
            # 叶子目录：mount_root = parent；目录形态：mount_root = 自身。
            # 与 _skill_paths_to_rail_mounts（extension_binder.py:185-186）口径一致。
            mount_root = str(resolved.parent) if (resolved / "SKILL.md").is_file() else str(resolved)
            if mount_root not in mounted_roots:
                stale.append(item)

        # 增方向：self.skills 有、baseline 无、directory 仍在 skills_dir → missing
        missing: list = []
        for skill in self.skills:
            if skill.name in baseline_names:
                continue
            try:
                resolved = Path(str(skill.directory)).expanduser().resolve()
            except (OSError, ValueError):
                continue
            mount_root = str(resolved.parent) if (resolved / "SKILL.md").is_file() else str(resolved)
            if mount_root in mounted_roots:
                missing.append(skill)

        if not stale and not missing:
            return

        try:
            self._save_session_baseline(session, self.skills)
        except Exception as exc:  # never block the model call on baseline bookkeeping
            logger.warning(
                "[ReconcilingSkillUseRail] reconcile baseline failed, leaving stale "
                "baseline in place: %s",
                exc,
            )
            return
        logger.info(
            "[ReconcilingSkillUseRail] reconciled session baseline: purged %d stale, "
            "added %d missing",
            len(stale),
            len(missing),
        )

    def _mounted_skill_roots(self) -> set[str]:
        """Return resolved mount roots currently in skills_dir (parent for leaves)."""
        roots: set[str] = set()
        for raw in self._skill_values_iter(self.skills_dir):
            try:
                path = Path(str(raw)).expanduser().resolve()
            except (OSError, ValueError):
                continue
            roots.add(str(path))
        return roots

    @staticmethod
    def _skill_values_iter(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw]
        return []
