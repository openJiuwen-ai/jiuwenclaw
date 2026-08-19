# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""沙箱配置 RPC: E2A / AgentRequest 入口, 返回 AgentResponse."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jiuwenclaw.schema.agent import AgentRequest, AgentResponse
from jiuwenclaw.schema.message import ReqMethod

logger = logging.getLogger(__name__)

_SANDBOX_CFG_METHODS: frozenset[ReqMethod] = frozenset(
    {
        ReqMethod.SANDBOX_ENABLED_GET,
        ReqMethod.SANDBOX_ENABLED_SET,
        ReqMethod.SANDBOX_STARTUP_MODE_GET,
        ReqMethod.SANDBOX_STARTUP_MODE_SET,
        ReqMethod.SANDBOX_FILES_GET,
        ReqMethod.SANDBOX_FILES_SET,
        ReqMethod.SANDBOX_NETWORK_GET,
        ReqMethod.SANDBOX_NETWORK_SET,
    }
)


def get_sandbox_config_req_methods() -> frozenset[ReqMethod]:
    """返回 sandbox 配置 req_method 集合, 供 agent_ws_server 分组派发."""
    return _SANDBOX_CFG_METHODS


def _ok(request: AgentRequest, payload: dict[str, Any] | None) -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id,
        channel_id=request.channel_id,
        ok=True,
        payload=payload or {},
        metadata=request.metadata,
    )


def _err(
    request: AgentRequest, message: str, *, code: str = "BAD_REQUEST"
) -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id,
        channel_id=request.channel_id,
        ok=False,
        payload={"error": message, "code": code},
        metadata=request.metadata,
    )


def _teardown_registered_sandbox_sysops() -> int:
    """关闭沙箱开关时, 清理 Runner.resource_mgr 里残留的沙箱 sysop."""
    try:
        from openjiuwen.core.runner import Runner
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sandbox] teardown: openjiuwen 未就绪, 跳过 (%s)", exc)
        return 0
    rm = getattr(Runner, "resource_mgr", None)
    if rm is None:
        return 0
    try:
        registered = rm.get_sys_operation()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sandbox] teardown: get_sys_operation 失败 (%s)", exc)
        return 0
    # get_sys_operation(None) 可能返回单实例/列表/None.
    if registered is None:
        return 0
    if not isinstance(registered, list):
        registered = [registered]
    removed = 0
    for sysop in registered:
        if sysop is None:
            continue
        try:
            iso_key = getattr(sysop, "isolation_key_template", None)
        except Exception:  # noqa: BLE001
            iso_key = None
        if not iso_key:
            continue
        op_id = getattr(sysop, "id", None)
        try:
            rm.remove_sys_operation(op_id)
            removed += 1
            logger.info(
                "[sandbox] teardown: 移除残留沙箱 sysop id=%s isolation_key=%s",
                op_id, iso_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[sandbox] teardown: 移除沙箱 sysop id=%s 失败: %s", op_id, exc
            )
    return removed


async def _apply_sandbox_change(kind: str) -> None:
    """set 后的生效动作 (异步, 不阻塞 RPC 响应)."""
    try:
        if kind == "enabled":
            from jiuwenclaw.config import get_sandbox_runtime
            if not bool(get_sandbox_runtime().get("enabled")):
                removed = _teardown_registered_sandbox_sysops()
                from jiuwenclaw.agentserver.sandbox_lifecycle import shutdown_jiuwenbox_sandboxes
                released = await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
                logger.info(
                    "[sandbox] enabled 变更为关闭, 已移除 %d 个残留沙箱 sysop, 释放 %d 个残留沙箱进程, 下轮 _create_sys_operation 读新值生效",
                    removed, released,
                )
            else:
                logger.info("[sandbox] enabled 变更为开启, 下轮 _create_sys_operation 读新值生效")
            return
        if kind == "startup_mode":
            from jiuwenclaw.config import get_sandbox_startup_mode
            from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner

            runner = JiuwenBoxRunner.instance()
            mode = get_sandbox_startup_mode()
            if mode == "external" and runner.owns_process:  # noqa: SLF001 - JiuwenBoxRunner 内部状态访问
                logger.info(
                    "[sandbox] startup_mode=external, 停掉 agent-server 拉起的 box-server"
                )
                await runner.stop()
            # internal 时下次 _bootstrap_internal_jiuwenbox 拉起; 这里不主动拉.
            return
        if kind in ("files", "network"):
            from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner

            runner = JiuwenBoxRunner.instance()
            if not runner.owns_process or runner.process is None:  # noqa: SLF001 - JiuwenBoxRunner 内部状态访问
                logger.info(
                    "[sandbox] %s 变更但 box-server 非 agent-server 拉起 (external), 跳过重启",
                    kind,
                )
                return
            logger.info(
                "[sandbox] %s 变更, 重启 box-server 重载运行时 policy 副本", kind,
            )
            await runner.ensure_running(
                host=runner.host,
                port=runner.port,
                startup_mode="internal",
                policy_path=runner.spawned_policy_path,
                timeout=120.0,
            )
            return
        logger.warning("[sandbox] unknown apply kind: %s", kind)
    except Exception:  # noqa: BLE001
        logger.exception("[sandbox] _apply_sandbox_change(%s) failed", kind)


def _trigger_apply(kind: str) -> None:
    """在运行中的事件循环里异步触发生效 (不阻塞 RPC 响应)."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_apply_sandbox_change(kind))
    except RuntimeError:
        logger.warning(
            "[sandbox] 无运行事件循环, 跳过异步生效 (kind=%s)", kind
        )


def dispatch_sandbox_config_request(request: AgentRequest) -> AgentResponse:
    """执行一条 sandbox 配置 RPC (与 dispatch_permissions_config_request 同形态)."""
    from jiuwenclaw.config import (
        get_sandbox_runtime,
        update_sandbox_runtime,
        get_sandbox_startup_mode,
        update_sandbox_startup_mode,
    )
    from jiuwenclaw.agentserver.sandbox_policy_render import (
        get_sandbox_files_config,
        set_sandbox_files_config,
        get_sandbox_network_config,
        set_sandbox_network_config,
    )

    m = request.req_method
    params = request.params if isinstance(request.params, dict) else {}
    tag = m.value if m is not None else ""

    try:
        # ---- 沙箱开关 (存 config.yaml, 基础配置) ----
        if m == ReqMethod.SANDBOX_ENABLED_GET:
            return _ok(
                request,
                {"enabled": bool(get_sandbox_runtime().get("enabled"))},
            )

        if m == ReqMethod.SANDBOX_ENABLED_SET:
            value = params.get("enabled")
            if not isinstance(value, bool):
                return _err(request, "enabled must be boolean")
            update_sandbox_runtime({"enabled": value})
            _trigger_apply("enabled")
            return _ok(request, {"enabled": value})

        # ----沙箱启动方式 (存 config.yaml, 基础配置) ----
        if m == ReqMethod.SANDBOX_STARTUP_MODE_GET:
            return _ok(request, {"startup_mode": get_sandbox_startup_mode()})

        if m == ReqMethod.SANDBOX_STARTUP_MODE_SET:
            mode = params.get("startup_mode")
            if not isinstance(mode, str) or not mode.strip():
                return _err(request, "startup_mode is required")
            try:
                normalized = update_sandbox_startup_mode(mode)
            except ValueError as exc:
                return _err(request, str(exc))
            _trigger_apply("startup_mode")
            return _ok(request, {"startup_mode": normalized})

        # ---- 接口2: 文件安全 (读写运行时副本, 不碰 config.yaml) ----
        if m == ReqMethod.SANDBOX_FILES_GET:
            return _ok(request, {"files": get_sandbox_files_config()})

        if m == ReqMethod.SANDBOX_FILES_SET:
            allow = params.get("allow")
            deny = params.get("deny")
            if not isinstance(allow, list) or not isinstance(deny, list):
                return _err(request, "allow and deny must be lists")
            try:
                files = set_sandbox_files_config(allow, deny)
            except ValueError as exc:
                return _err(request, str(exc))
            _trigger_apply("files")
            return _ok(request, {"files": files})

        # ---- 接口3: 网络安全 (读写运行时副本) ----
        if m == ReqMethod.SANDBOX_NETWORK_GET:
            return _ok(request, {"network": get_sandbox_network_config()})

        if m == ReqMethod.SANDBOX_NETWORK_SET:
            disable_all = params.get("disable_all")
            allow_domains = params.get("allow_domains")
            deny_domains = params.get("deny_domains")
            if not isinstance(disable_all, bool):
                return _err(request, "disable_all must be boolean")
            if not isinstance(allow_domains, list) or not isinstance(deny_domains, list):
                return _err(request, "allow_domains and deny_domains must be lists")
            try:
                network = set_sandbox_network_config(
                    disable_all, allow_domains, deny_domains
                )
            except ValueError as exc:
                return _err(request, str(exc))
            _trigger_apply("network")
            return _ok(request, {"network": network})

    except ValueError as exc:
        return _err(request, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] %s", tag, exc)
        return _err(request, str(exc), code="INTERNAL_ERROR")

    return _err(request, "unknown sandbox req_method", code="BAD_REQUEST")


__all__ = [
    "dispatch_sandbox_config_request",
    "get_sandbox_config_req_methods",
]
