# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""专家会话操作编排（experts.list / expert.load / expert.unload 的业务内核）。

fetch+校验与错误码映射、session metadata 读写时机（先应用成功才写）、
root/child 适配器定位、BUSY 守卫与持锁复验（ExpertApplyBusyError）映射，
全部收敛到本模块；WS handler 只剩 params 提取与 AgentResponse 发包
（见 ``agent_ws_server.py`` 的 ``_handle_expert_*`` 薄壳）。

结果以 :class:`ExpertOpResult` 返回，``payload`` 直接作 ``AgentResponse.payload``：
成功含业务字段（expert_id/applied/pending/previous_expert_id/warnings），
失败含 ``error`` + ``code``（BAD_REQUEST / NOT_FOUND / INVALID_PACKAGE / BUSY /
LOAD_FAILED / REPO_UNAVAILABLE / INTERNAL_ERROR）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from jiuwenswarm.server.runtime.expert import expert_store as _expert_store

logger = logging.getLogger(__name__)

_BUSY_MESSAGE = "当前回合执行中，请等回合结束"


@dataclass
class ExpertOpResult:
    """一次专家操作的结果：ok + 直接可作 AgentResponse.payload 的 dict。"""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)


class ExpertService:
    """专家会话操作：list / load / unload。

    依赖经构造注入：``agent_manager`` 提供按定位键取 agent 的入口，
    ``adapter_resolver`` 从 agent 提取底层适配器（生产环境即
    ``AgentWebSocketServer._resolve_adapter``）。
    """

    def __init__(
            self,
            *,
            agent_manager: Any,
            adapter_resolver: Callable[[Any], Any],
    ) -> None:
        self._agent_manager = agent_manager
        self._adapter_resolver = adapter_resolver

    async def list_experts(self) -> ExpertOpResult:
        try:
            summaries = await _expert_store.get_expert_source().list()
            return ExpertOpResult(
                ok=True,
                payload={"experts": [asdict(s) for s in summaries]},
            )
        except _expert_store.ExpertRepoUnavailable as exc:
            logger.warning("[ExpertService] experts.list 仓库不可达: %s", exc)
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "REPO_UNAVAILABLE"}
            )
        except Exception as exc:
            logger.exception("[ExpertService] experts.list failed: %s", exc)
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "INTERNAL_ERROR"}
            )

    async def load_expert(
            self,
            *,
            channel_id: str,
            session_id: str,
            expert_id: str,
    ) -> ExpertOpResult:
        """召唤/切换专家：先 fetch+校验，再按子适配器是否存在走 applied/pending。

        顺序是「先应用、成功后才写 metadata」——装载失败不留脏 expert_id。
        """
        from jiuwenswarm.server.runtime.agent_adapter.expert_capability import (
            ExpertApplyBusyError,
        )
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
            update_session_metadata,
        )

        if not session_id or not expert_id:
            return ExpertOpResult(
                ok=False,
                payload={"error": "missing session_id or expert_id", "code": "BAD_REQUEST"},
            )

        # 1) fetch + 校验（此步之前不写任何状态）
        try:
            package_dir = await _expert_store.get_expert_source().fetch(expert_id)
            warnings = _expert_store.validate_expert_package(package_dir)
        except _expert_store.ExpertNotFound as exc:
            logger.warning("[ExpertService] expert.load 专家不存在: %s", exc)
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "NOT_FOUND"}
            )
        except _expert_store.ExpertRepoUnavailable as exc:
            logger.warning("[ExpertService] expert.load 仓库不可达: %s", exc)
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "REPO_UNAVAILABLE"}
            )
        except _expert_store.InvalidExpertPackage as exc:
            logger.warning("[ExpertService] expert.load 包非法: %s", exc)
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "INVALID_PACKAGE"}
            )

        # 2) 旧值
        metadata = get_session_metadata(session_id, cache_bust=True)
        if not metadata:
            logger.warning(
                "[session_id=%s] [ExpertService] expert.load: session 不存在", session_id
            )
            return ExpertOpResult(
                ok=False,
                payload={"error": f"session 不存在: {session_id}", "code": "NOT_FOUND"},
            )
        previous_expert_id = str(metadata.get("expert_id") or "")

        # 3) 子适配器不存在 → pending（首次装配由入口 create_instance() 生效）
        root, child = self._locate_session_adapter(
            channel_id,
            session_id,
            mode=metadata.get("mode"),
            project_dir=metadata.get("project_dir"),
        )
        if child is None or not child.has_live_instance():
            update_session_metadata(session_id=session_id, expert_id=expert_id, sync=True)
            return ExpertOpResult(
                ok=True,
                payload={
                    "expert_id": expert_id,
                    "applied": False,
                    "pending": True,
                    "previous_expert_id": previous_expert_id,
                    "warnings": warnings,
                },
            )

        # 4) BUSY 守卫：回合执行中直接拒绝，不排队
        if self._switch_busy(root, child, session_id):
            return ExpertOpResult(
                ok=False, payload={"error": _BUSY_MESSAGE, "code": "BUSY"}
            )

        # 5) 应用（成功后才写 metadata）
        try:
            await child.apply_expert(expert_id, package_dir=package_dir)
        except ExpertApplyBusyError as exc:
            # 持锁复验命中：守卫与 apply 之间的空隙里 chat 开始了回合
            logger.info(
                "[session_id=%s] [ExpertService] expert.load 持锁复验 BUSY: %s",
                session_id, exc,
            )
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "BUSY"}
            )
        except Exception as exc:
            logger.exception(
                "[session_id=%s] [ExpertService] expert.load 应用失败 expert=%s: %s",
                session_id, expert_id, exc,
            )
            return ExpertOpResult(
                ok=False, payload={"error": str(exc), "code": "LOAD_FAILED"}
            )
        update_session_metadata(session_id=session_id, expert_id=expert_id, sync=True)
        return ExpertOpResult(
            ok=True,
            payload={
                "expert_id": expert_id,
                "applied": True,
                "pending": False,
                "previous_expert_id": previous_expert_id,
                "warnings": warnings,
            },
        )

    async def unload_expert(
            self,
            *,
            channel_id: str,
            session_id: str,
    ) -> ExpertOpResult:
        """退出专家：回默认身份。无专家幂等；无活实例只清 metadata。"""
        from jiuwenswarm.server.runtime.agent_adapter.expert_capability import (
            ExpertApplyBusyError,
        )
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
            update_session_metadata,
        )

        if not session_id:
            return ExpertOpResult(
                ok=False,
                payload={"error": "missing session_id", "code": "BAD_REQUEST"},
            )
        metadata = get_session_metadata(session_id, cache_bust=True)
        if not metadata:
            logger.warning(
                "[session_id=%s] [ExpertService] expert.unload: session 不存在", session_id
            )
            return ExpertOpResult(
                ok=False,
                payload={"error": f"session 不存在: {session_id}", "code": "NOT_FOUND"},
            )
        previous_expert_id = str(metadata.get("expert_id") or "")
        if not previous_expert_id:
            return ExpertOpResult(
                ok=True,
                payload={"applied": False, "previous_expert_id": ""},
            )

        root, child = self._locate_session_adapter(
            channel_id,
            session_id,
            mode=metadata.get("mode"),
            project_dir=metadata.get("project_dir"),
        )
        applied = False
        if child is not None and child.has_live_instance():
            if self._switch_busy(root, child, session_id):
                return ExpertOpResult(
                    ok=False, payload={"error": _BUSY_MESSAGE, "code": "BUSY"}
                )
            try:
                await child.apply_expert(None)
                applied = True
            except ExpertApplyBusyError as exc:
                logger.info(
                    "[session_id=%s] [ExpertService] expert.unload 持锁复验 BUSY: %s",
                    session_id, exc,
                )
                return ExpertOpResult(
                    ok=False, payload={"error": str(exc), "code": "BUSY"}
                )
            except Exception as exc:
                logger.exception(
                    "[session_id=%s] [ExpertService] expert.unload 应用失败 previous=%s: %s",
                    session_id, previous_expert_id, exc,
                )
                return ExpertOpResult(
                    ok=False, payload={"error": str(exc), "code": "LOAD_FAILED"}
                )
        update_session_metadata(session_id=session_id, expert_id="", sync=True)
        return ExpertOpResult(
            ok=True,
            payload={"applied": applied, "previous_expert_id": previous_expert_id},
        )

    def _locate_session_adapter(
            self,
            channel_id: str,
            session_id: str,
            *,
            mode: str | None = None,
            project_dir: str | None = None,
    ) -> tuple[Any, Any]:
        """定位 (root_adapter, session_child_adapter)；不强制创建任何实例。

        mode/project_dir 必须取自该会话的 metadata（与 chat 路径同一套定位键）：
        同一 channel 下不同 project_dir 会存在多个 root，只按 channel 定位会拿错
        root——表现为「装载/卸载返回成功但实际会话没变化」。
        """
        agent = self._agent_manager.get_agent_nowait(
            channel_id=channel_id or "default",
            mode=mode or None,
            project_dir=project_dir or None,
        )
        if agent is None:
            return None, None
        root = self._adapter_resolver(agent)
        if root is None:
            return None, None
        if root.is_session_scoped:
            return root, root
        return root, root.get_cached_child_adapter(session_id)

    @staticmethod
    def _switch_busy(root: Any, child: Any, session_id: str) -> bool:
        if root is not None and not root.is_session_scoped:
            return bool(root.expert_switch_blocked(session_id))
        return bool(child is not None and child.is_session_live(session_id))
