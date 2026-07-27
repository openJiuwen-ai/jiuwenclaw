# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, ClassVar

from jiuwenclaw.local_env_config import (
    apply_env_overrides_to_active,
    effective_tip,
    get_local_config,
    set_os_environ,
)
from jiuwenclaw.agentserver.session_history import (
    enrich_history_messages_session_id,
    read_history_records_for_frontend,
)
from jiuwenclaw.agentserver.gateway_push.wire import build_server_push_wire
from jiuwenclaw.agentserver.tools.acp_output_tools import get_acp_output_manager
from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.utils import (
    FileTransferStartParams,
    get_agent_sessions_dir,
    get_config_file,
    resolve_tenant_sessions_dir,
)
from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.constants import (
    FILE_TRANSFER_START,
    FILE_TRANSFER_CHUNK,
    FILE_TRANSFER_COMPLETE,
    FILE_TRANSFER_EVENT_TYPES,
)
from jiuwenclaw.e2a.gateway_normalize import (
    E2A_FALLBACK_FAILED_KEY,
    E2A_INTERNAL_CONTEXT_KEY,
    E2A_LEGACY_AGENT_REQUEST_KEY,
)
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
    encode_json_parse_error_wire,
)
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.extensions.types import WsHandlerContext
from jiuwenclaw.agentserver.extensions import get_rail_manager
from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.permissions.patterns import persist_cli_trusted_directory
from jiuwenclaw.schema.hooks_context import (
    AgentServerChatHookContext,
    AgentWsServerStartHookContext,
    AgentReloadConfigHookContext,
)
from jiuwenclaw.agentserver.agent_manager import AgentManager, ACP_DEFAULT_CAPABILITIES
from jiuwenclaw.e2a.acp.protocol import build_acp_session_new_result
from jiuwenclaw.agentserver.permissions.config_rpc import get_permissions_config_req_methods
from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool
from jiuwenclaw.agentserver.file_transfer_manager import get_file_transfer_manager
from jiuwenclaw.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_allowed_browser_origin,
)


logger = logging.getLogger(__name__)

# 流式处理心跳间隔：当 Agent 处理时间超过此阈值时，发送心跳 chunk 保持 WebSocket 连接活跃
# 避免 ping_timeout 导致连接关闭。默认 10 秒，小于服务端 ping_timeout=20s。
_STREAM_HEARTBEAT_INTERVAL_SECONDS = 10.0

_SYSTEM_PROMPT_USER_HISTORY_PATTERN = re.compile(r"(\[[^\]\n]*用户\]\s*)(.*?)(\s*\[/对话历史\])", re.DOTALL)


def _mask_text_for_log(value: str) -> str:
    return "******" if len(value) <= 20 else f"{value[:5]}******{value[-5:]}"


def _mask_system_prompt_for_log(system_prompt: str) -> str:
    return _SYSTEM_PROMPT_USER_HISTORY_PATTERN.sub(
        lambda match: f"{match.group(1)}{_mask_text_for_log(match.group(2).strip())}{match.group(3)}",
        system_prompt,
    )


def _mask_query_for_log(data: dict[str, Any]) -> dict[str, Any]:
    params = data.get("params")
    if not isinstance(params, dict):
        return data

    masked_params = dict(params)
    query = params.get("query")
    if isinstance(query, str) and query:
        masked_params["query"] = _mask_text_for_log(query)

    system_prompt = params.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt:
        masked_params["system_prompt"] = _mask_system_prompt_for_log(system_prompt)

    supplementary_info = params.get("supplementary_info")
    if isinstance(supplementary_info, str) and supplementary_info:
        masked_params["supplementary_info"] = _mask_text_for_log(supplementary_info)

    if masked_params == params:
        return data
    return {**data, "params": masked_params}


def _sessions_dir_for_request(request: AgentRequest) -> Path:
    """Resolve tenant sessions root from request agent_id/service_id."""
    agent_id, service_id = TenantAgentPool.extract_ids(request)
    return resolve_tenant_sessions_dir(service_id, agent_id)


def _payload_to_request(data: dict[str, Any]) -> AgentRequest:
    """将 Gateway 发送的 JSON 载荷解析为 AgentRequest."""
    from jiuwenclaw.schema.message import ReqMethod

    req_method = data.get("req_method")
    if req_method is not None and isinstance(req_method, str):
        req_method = ReqMethod(req_method)

    return AgentRequest(
        request_id=data["request_id"],
        channel_id=data.get("channel_id", ""),
        session_id=data.get("session_id"),
        req_method=req_method,
        params=data.get("params", {}),
        is_stream=data.get("is_stream", False),
        timestamp=data.get("timestamp", 0.0),
        metadata=data.get("metadata"),
        agent_id=data.get("agent_id"),
        service_id=data.get("service_id"),
    )


class AgentWebSocketServer:
    """Gateway 与 AgentServer 之间的 WebSocket 服务端（多例）.

    监听来自 Gateway (WebSocketAgentServerClient) 的连接，按协议约定处理请求：
    - 收到 JSON：E2AEnvelope（或过渡期 legacy + 兜底信封）
    - is_stream=False：``process_message`` → 一条 **E2AResponse** JSON（``jiuwenclaw.e2a.wire_codec``）
    - is_stream=True：逐条 **E2AResponse** JSON（chunk/complete/error）
    - 例外：首帧 ``connection.ack`` 仍为 ``type/event`` 事件帧

    支持 send_push：推送帧亦为 E2AResponse 线格式（由 chunk 编码）。
    """

    _instance: ClassVar[AgentWebSocketServer | None] = None

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18000,
        *,
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> None:
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        # 当前 Gateway 连接，用于 send_push 主动推送
        self._current_ws: Any = None
        self._current_send_lock: asyncio.Lock | None = None
        self._acp_client_capabilities_by_ws: dict[int, dict[str, Any]] = {}
        self._agent_manager = None # TenantAgentPool 实例
        get_acp_output_manager().set_send_push_callback(
            lambda msg: asyncio.create_task(self.send_push(msg))
        )

    @staticmethod
    def _ws_capabilities_key(ws: Any) -> int:
        return id(ws)

    def _set_ws_acp_client_capabilities(self, ws: Any, capabilities: dict[str, Any] | None) -> None:
        key = self._ws_capabilities_key(ws)
        if isinstance(capabilities, dict):
            self._acp_client_capabilities_by_ws[key] = dict(capabilities)
        else:
            self._acp_client_capabilities_by_ws.pop(key, None)

    def _get_ws_acp_client_capabilities(self, ws: Any) -> dict[str, Any]:
        key = self._ws_capabilities_key(ws)
        caps = self._acp_client_capabilities_by_ws.get(key)
        return dict(caps) if isinstance(caps, dict) else {}

    def _clear_ws_acp_client_capabilities(self, ws: Any) -> None:
        self._acp_client_capabilities_by_ws.pop(self._ws_capabilities_key(ws), None)

    @classmethod
    def get_instance(
        cls,
        *,
        host: str = "127.0.0.1",
        port: int = 18000,
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> "AgentWebSocketServer":
        """返回多例实例。

        首次调用时创建实例，后续调用返回已存在的实例。
        """
        if cls._instance is not None:
            return cls._instance
        cls._instance = cls(
            host=host,
            port=port,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ---------- 生命周期 ----------

    @staticmethod
    def _parse_sandbox_host_port(url: str) -> tuple[str, int]:
        """从 sandbox url 解析 host:port; 默认 127.0.0.1:8321."""
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8321
        except Exception:  # noqa: BLE001
            host, port = "127.0.0.1", 8321
        return host, int(port)

    @staticmethod
    def _is_tcp_port_bindable(host: str, port: int) -> bool:
        """``True`` 表示能在 ``host:port`` 上 bind 成功 (即没被占用)。"""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                sock.bind((host, port))
            except OSError:
                return False
            return True
        finally:
            sock.close()

    @staticmethod
    def _pick_free_tcp_port(host: str) -> int:
        """让内核挑一个空闲端口 (``bind`` 到 0)。"""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def _allocate_internal_jiuwenbox_port(self, host: str, preferred_port: int) -> int:
        """internal 模式下确定 jiuwenbox 实际监听端口。

        - 若本 runner 已在 ``host:preferred_port`` 上拥有一个仍在跑的 jiuwenbox, 复用;
        - 否则若 ``preferred_port`` 当前无人占用, 用之;
        - 再否则让内核挑一个空闲端口。
        """
        from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner

        runner = JiuwenBoxRunner.instance()
        if runner.is_owned_listener(host, preferred_port):
            return preferred_port
        if self._is_tcp_port_bindable(host, preferred_port):
            return preferred_port
        new_port = self._pick_free_tcp_port(host)
        logger.warning(
            "[AgentWebSocketServer] jiuwenbox preferred port %s:%d is busy; "
            "allocating fresh port %d for new jiuwenbox instance",
            host, preferred_port, new_port,
        )
        return new_port

    async def _bootstrap_internal_jiuwenbox(self) -> None:
        """启动时按 ``config.yaml::sandbox`` 自动拉起 jiuwenbox 子进程。

        触发条件: ``sandbox.startup_mode`` **显式**写为 ``internal``
        (走 :func:`get_sandbox_startup_mode_explicit`, 字段缺失不拉, 避免没在用
        沙箱的用户突然多出 jiuwenbox 进程)。不单独依赖 ``sandbox.enabled``:
        只要 ``startup_mode=internal`` 就拉, 成功后落盘真实 url。

        平台: 支持 Linux 与 Windows (Windows 走 ``_create_windows`` 分支); 其他平台跳过。
        best-effort: 任何失败只 warning, 不让 agent-server 自身启动失败。
        """
        try:
            if sys.platform not in ("linux", "win32"):
                logger.info(
                    "[AgentWebSocketServer] skipping jiuwenbox auto-start: "
                    "sandbox is only supported on Linux/Windows (current: %r)",
                    sys.platform,
                )
                return
            from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner
            from jiuwenclaw.config import (
                DEFAULT_SANDBOX_POLICY_FILE,
                get_sandbox_endpoint,
                get_sandbox_startup_mode_explicit,
                resolve_sandbox_policy_path,
                update_sandbox_endpoint,
            )

            explicit_mode = get_sandbox_startup_mode_explicit()
            if explicit_mode is None:
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode 未在 config.yaml "
                    "中显式配置, skipping jiuwenbox auto-start (如需 agent-server "
                    "自动拉起 jiuwenbox 子进程, 设置 sandbox.startup_mode: internal)"
                )
                return
            if explicit_mode != "internal":
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode=%r, skipping "
                    "jiuwenbox auto-start (external 模式由用户自行拉起 jiuwenbox-server)",
                    explicit_mode,
                )
                return

            endpoint = get_sandbox_endpoint()
            url = endpoint.get("url") or "http://127.0.0.1:8321"
            sandbox_type = endpoint.get("type") or "jiuwenbox"
            raw_policy = endpoint.get("policy_file") or ""
            effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
            policy_path = resolve_sandbox_policy_path(effective_policy_file)
            if policy_path is None or not policy_path.is_file():
                logger.warning(
                    "[AgentWebSocketServer] sandbox auto-start skipped: "
                    "policy_file=%r 无法解析到存在的文件 (resolved=%s).",
                    effective_policy_file, policy_path,
                )
                return

            host, preferred_port = self._parse_sandbox_host_port(url)
            port = self._allocate_internal_jiuwenbox_port(host, preferred_port)
            if port != preferred_port:
                url = f"http://{host}:{port}"
                logger.info(
                    "[AgentWebSocketServer] jiuwenbox auto-start: "
                    "preferred port %d busy, using %d",
                    preferred_port, port,
                )

            # 注入动态路径 env 给 box-server 子进程 (Windows 沙箱用):
            #   JIUWENBOX_BUNDLED_PYTHON / JIUWENBOX_VENV_DIR
            #   JIUWENBOX_RUNNER_PYTHON = runner 用的标准 CPython (非 uv venv).
            #     jbx-sandbox 跑不了 uv trampoline/venv launcher (WinError 5 / os error 5),
            #     runner 必须用自包含的标准 CPython. dev 实测设 D:\Files\python313,
            #     打包环境设 tools/python/python.exe. 探测候选路径, 找到即注入.
            try:
                from jiuwenclaw.runtime.pip_env import (
                    ensure_runtime_venv, resolve_base_python,
                )
                venv_dir = ensure_runtime_venv()
                os.environ["JIUWENBOX_VENV_DIR"] = str(venv_dir)
                bundled_python = resolve_base_python()
                os.environ["JIUWENBOX_BUNDLED_PYTHON"] = str(bundled_python.parent)
                if not (os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip():
                    for _cand in (
                        r"D:\Files\python313\python.exe",  # dev 实测机
                        r"C:\Python313\python.exe",
                        r"C:\Python312\python.exe",
                        str(Path(__file__).resolve().parents[2] / "tools" / "python" / "python.exe"),  # 打包
                    ):
                        if _cand and Path(_cand).is_file():
                            os.environ["JIUWENBOX_RUNNER_PYTHON"] = _cand
                            break
                logger.info(
                    "[AgentWebSocketServer][sandbox] injected env: "
                    "JIUWENBOX_VENV_DIR=%s, JIUWENBOX_BUNDLED_PYTHON=%s, "
                    "JIUWENBOX_RUNNER_PYTHON=%s",
                    venv_dir, bundled_python.parent,
                    os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "<未注入>",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] inject JIUWENBOX_BUNDLED_PYTHON/VENV_DIR failed: %s",
                    exc,
                )

            logger.info(
                "[AgentWebSocketServer][sandbox] spawning box-server (startup_mode=internal)..."
            )
            runner = JiuwenBoxRunner.instance()
            ok = await runner.ensure_running(
                host=host,
                port=port,
                startup_mode="internal",
                policy_path=policy_path,
            )
            if not ok:
                stderr_tail = runner.get_stderr_tail(20)
                hint = "\n--- jiuwenbox stderr (tail) ---\n" + stderr_tail if stderr_tail else ""
                logger.warning(
                    "[AgentWebSocketServer] jiuwenbox auto-start failed at %s:%d "
                    "(policy=%s).%s",
                    host, port, policy_path, hint,
                )
                return

            # 落盘最终生效的 url (端口可能被换过), 让后续会话/agent 直接读到正确端点。
            actual_url = runner.base_url
            if actual_url and actual_url != endpoint.get("url"):
                try:
                    update_sandbox_endpoint(
                        actual_url, sandbox_type,
                        startup_mode="internal",
                        policy_file=effective_policy_file,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[AgentWebSocketServer] persist sandbox endpoint failed "
                        "after auto-start: %s", exc,
                    )
            logger.info(
                "[AgentWebSocketServer][sandbox] box-server ready at %s, "
                "sandbox_id 按需 lazy 创建",
                actual_url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentWebSocketServer] jiuwenbox auto-start bootstrap failed: %s", exc,
            )

    async def start(self) -> None:
        """启动 WebSocket 服务端，开始监听连接。优先使用 legacy.server.serve 以与 Gateway 的 legacy client 握手兼容."""
        await self._trigger_before_ws_server_start_hook()
        # 初始化 TenantAgentPool
        if self._agent_manager is None:
            self._agent_manager = TenantAgentPool.get_instance()
            logger.info("[AgentWebSocketServer] 已初始化 TenantAgentPool")

        if self._server is not None:
            logger.warning("[AgentWebSocketServer] 服务端已在运行")
            return

        # 启动文件传输管理器的清理任务
        ft_manager = get_file_transfer_manager()
        if ft_manager.enabled:
            await ft_manager.start_cleanup_task()

        try:
            from websockets.legacy.server import serve as legacy_serve
            self._server = await legacy_serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        except ImportError:
            import websockets
            self._server = await websockets.serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        logger.info(
            "[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port,
            extra={'user_visible': 'critical'},
        )
        # 按 config.yaml::sandbox.startup_mode 自动拉起 jiuwenbox 子进程 (internal 模式)。
        await self._bootstrap_internal_jiuwenbox()

    async def _process_request(self, *args: Any) -> Any:
        """在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")

        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "[AgentWebSocketServer] 握手检查 path=%s origin=%s allowed=%s",
            path,
            origin,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "[AgentWebSocketServer] 握手拒绝 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def stop(self) -> None:
        """停止 WebSocket 服务端."""
        # 停止文件传输管理器的清理任务
        ft_manager = get_file_transfer_manager()
        if ft_manager.enabled:
            await ft_manager.stop_cleanup_task()

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        logger.info("[AgentWebSocketServer] 已停止", extra={'user_visible': 'critical'})

    # ---------- 连接处理 ----------

    async def _connection_handler(self, ws: Any) -> None:
        """处理单个 Gateway WebSocket 连接，同一连接可并发处理多个请求."""
        import websockets
        # 和客户端连接成功后, 触发agentserver启动成功的回调事件
        await self._trigger_agent_server_started_hook()

        remote = ws.remote_address
        logger.info("[AgentWebSocketServer] 新连接: %s", remote, extra={'user_visible': 'critical'})

        send_lock = asyncio.Lock()
        self._current_ws = ws
        self._current_send_lock = send_lock

        # 触发身份获取（在发送 connection.ack 之前）
        try:
            from jiuwenclaw.extensions.identity_provider import IdentityStore
            identity = await IdentityStore.get_instance().fetch_and_store()
            if identity is not None:
                logger.info(
                    "[AgentWebSocketServer] 身份信息已获取: user_id=%s domain_id=%s app_id=%s",
                    identity.user_id,
                    identity.domain_id,
                    identity.app_id,
                    extra={'user_visible': 'progress'}
                )
            else:
                logger.debug("[AgentWebSocketServer] 未获取到身份信息（无 provider 或获取失败）", 
                             extra={'user_visible': 'progress'})
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 身份获取异常: %s", e, extra={'user_visible': 'progress'})

        # 发送 connection.ack 事件，通知 Gateway 服务端已就绪
        try:
            ack_frame = {
                "type": "event",
                "event": "connection.ack",
                "payload": {"status": "ready"},
            }
            await ws.send(json.dumps(ack_frame, ensure_ascii=False))
            logger.info("[AgentWebSocketServer] 已发送 connection.ack: %s", remote,
                       extra={'user_visible': 'critical'})
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 发送 connection.ack 失败: %s", e,
                          extra={'user_visible': 'critical'})

        tasks: set[asyncio.Task] = set()

        try:
            async for raw in ws:
                task = asyncio.create_task(self._handle_message(ws, raw, send_lock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except websockets.exceptions.ConnectionClosed as e:
            # websockets>=12: 正式字段为 rcvd/sent；勿用已 deprecate 的 e.code/e.reason
            rcvd = getattr(e, "rcvd", None)
            sent = getattr(e, "sent", None)
            logger.warning(
                "[AgentWebSocketServer] 连接关闭 remote=%s "
                "rcvd_code=%s rcvd_reason=%r sent_code=%s sent_reason=%r "
                "rcvd_then_sent=%s detail=%s",
                remote,
                getattr(rcvd, "code", None),
                getattr(rcvd, "reason", None) or "",
                getattr(sent, "code", None),
                getattr(sent, "reason", None) or "",
                getattr(e, "rcvd_then_sent", None),
                e,
                extra={"user_visible": "critical"},
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] 连接处理异常 (%s): %s", remote, e, 
                             extra={'user_visible': 'critical'})
        finally:
            self._current_ws = None
            self._current_send_lock = None
            self._clear_ws_acp_client_capabilities(ws)
            # Gateway 进程退出/端口关闭时，必须先取消各 session 内流式生产者（SessionManager）
            # 并中止 DeepAgent 内层循环；否则仅等待 _handle_message 任务结束会一直阻塞到任务自然完成。
            try:
                await self._agent_manager.cancel_all_inflight_work(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] cancel_all_inflight_work failed", 
                                 extra={'user_visible': 'progress'})
            try:
                from jiuwenclaw.agentserver.team import get_team_manager

                await get_team_manager().cancel_all_stream_tasks(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] team stream cancel failed", extra={'user_visible': 'progress'})
            if tasks:
                for t in list(tasks):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _get_custom_ws_handler(method: str):
        """检查 method 是否为已注册的自定义 WebSocket 处理器。"""
        if not method:
            return None
        try:
            from jiuwenclaw.extensions.registry import ExtensionRegistry
            return ExtensionRegistry.get_instance().get_ws_handler(method)
        except RuntimeError:
            # ExtensionRegistry 未初始化
            return None

    async def _handle_message(self, ws: Any, raw: str | bytes, send_lock: asyncio.Lock) -> None:
        """解析一条 JSON 请求并分发到 IAgentServer 处理."""
        try:
            data = json.loads(raw)
            logger.info(
                "[AgentWebSocketServer] Inbound raw payload: %s",
                _mask_query_for_log(data),
                extra={'user_visible': 'critical'}
            )
            logger.info(
                f"[AgentWebSocketServer] Inbound raw payload json 解析成功: request_id={data.get('request_id', '')}"
            )
        except json.JSONDecodeError as e:
            logger.error(
                f"[AgentWebSocketServer] Inbound raw payload json 解析失败: error={str(e)}"
            )
            wire = encode_json_parse_error_wire(
                request_id="",
                channel_id="",
                message=f"Inbound raw payload json 解析失败: {e}",
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            env = E2AEnvelope.from_dict(data)
            logger.info(
                f"[AgentWebSocketServer] Inbound raw payload E2A协议解析成功: request_id={env.request_id}"
            )
        except Exception as parse_err:
            logger.warning(
                "[AgentWebSocketServer] Inbound raw payload E2A协议解析失败，按旧 payload 解析: %s",
                parse_err,
            )
            request = _payload_to_request(data)
        else:
            jw = (env.channel_context or {}).get(E2A_INTERNAL_CONTEXT_KEY)
            if isinstance(jw, dict) and jw.get(E2A_FALLBACK_FAILED_KEY):
                legacy = jw.get(E2A_LEGACY_AGENT_REQUEST_KEY)
                logger.warning(
                    "[E2A][fallback] using legacy_agent_request request_id=%s",
                    env.request_id,
                )
                if not isinstance(legacy, dict):
                    raise ValueError("legacy_agent_request missing or not a dict")
                request = _payload_to_request(legacy)
            # 文件传输请求：method 不在 ReqMethod 枚举中，需要特殊处理
            elif env.method in (FILE_TRANSFER_START, FILE_TRANSFER_CHUNK, FILE_TRANSFER_COMPLETE):
                logger.info(
                    "[E2A][in] request_id=%s channel=%s method=%s is_stream=%s",
                    env.request_id,
                    env.channel,
                    env.method,
                    env.is_stream,
                )
                # 直接构造 AgentRequest，不走 e2a_to_agent_request
                request = AgentRequest(
                    request_id=env.request_id or "",
                    channel_id=env.channel or "",
                    session_id=env.session_id,
                    req_method=None,  # 文件传输没有对应的 ReqMethod
                    params=dict(env.params or {}),
                    is_stream=False,
                    timestamp=0.0,
                    metadata=None,
                )
            else:
                logger.info(
                    "[E2A][in] request_id=%s channel=%s method=%s is_stream=%s",
                    env.request_id,
                    env.channel,
                    env.method,
                    env.is_stream,
                )
                # 检查是否为自定义 WebSocket 处理器
                raw_method = env.method or ""
                custom_entry = self._get_custom_ws_handler(raw_method)
                if custom_entry:
                    # 自定义处理器：直接构造 AgentRequest，不需要 ReqMethod
                    request = AgentRequest(
                        request_id=env.request_id or "",
                        channel_id=env.channel or "",
                        session_id=env.session_id,
                        req_method=None,
                        params=dict(env.params or {}),
                        is_stream=False,
                        timestamp=0.0,
                        metadata={"_custom_ws_handler_entry": custom_entry},
                    )
                else:
                    request = e2a_to_agent_request(env)

        logger.info(
            "[AgentWebSocketServer] 收到请求: request_id=%s channel_id=%s is_stream=%s",
            request.request_id,
            request.channel_id,
            request.is_stream,
            extra={'user_visible': 'critical'}
        )

        from jiuwenclaw.interface_resp import maybe_track_e2a_resp

        async with maybe_track_e2a_resp(
            ws,
            req_method=request.req_method,
            params=request.params if isinstance(request.params, dict) else None,
            request_id=request.request_id,
            channel_id=request.channel_id or "",
            session_id=request.session_id,
        ):
            await self._handle_agent_request_body(ws, request, send_lock)

    async def _handle_agent_request_body(self, ws: Any, request: Any, send_lock: asyncio.Lock) -> None:
        from jiuwenclaw.schema.message import ReqMethod

        try:
            if request.channel_id == "acp" and request.req_method != ReqMethod.INITIALIZE:
                metadata = dict(request.metadata or {})
                ws_caps = self._get_ws_acp_client_capabilities(ws)
                metadata.setdefault(
                    "acp_client_capabilities",
                    ws_caps or self._agent_manager.get_client_capabilities("acp"),
                )
                request.metadata = metadata

            # 检查是否为自定义 WebSocket 处理器（在 ReqMethod 路由之前）
            from jiuwenclaw.extensions.types import WsHandlerEntry

            custom_entry_from_meta = request.metadata.get("_custom_ws_handler_entry") if request.metadata else None
            if isinstance(custom_entry_from_meta, WsHandlerEntry):
                ctx = WsHandlerContext(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    session_id=request.session_id,
                    params=request.params,
                    metadata=request.metadata,
                )
                # 清除内部标记
                if request.metadata:
                    request.metadata.pop("_custom_ws_handler_entry", None)
                await self._handle_custom_ws_handler(ws, request, custom_entry_from_meta, ctx, send_lock)
                return

            await self._trigger_before_chat_request_hook(request)

            if request.req_method == ReqMethod.SESSION_LIST:
                logger.info(f"[AgentWebSocketServer] 处理 session.list: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_session_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_RENAME:
                logger.info(f"[AgentWebSocketServer] 处理 session.rename: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_session_rename(ws, request, send_lock)
                return
            if request.req_method in get_permissions_config_req_methods():
                logger.info(f"[AgentWebSocketServer] 处理 permissions.config: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_permissions_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HISTORY_GET:
                logger.info(f"[AgentWebSocketServer] 处理 history.get: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                if request.is_stream:
                    await self._handle_history_get_stream(ws, request, send_lock)
                else:
                    await self._handle_history_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_ADD_DIR:
                logger.info(f"[AgentWebSocketServer] 处理 command.add_dir: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_add_dir(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_CHROME:
                logger.info(f"[AgentWebSocketServer] 处理 command.chrome: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_chrome(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_COMPACT:
                logger.info(f"[AgentWebSocketServer] 处理 command.compact: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_compact(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_DIFF:
                logger.info(f"[AgentWebSocketServer] 处理 command.diff: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_diff(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_LS:
                logger.info(f"[AgentWebSocketServer] 处理 command.ls: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_ls(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_VIEW:
                logger.info(f"[AgentWebSocketServer] 处理 command.view: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_view(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_MODEL:
                logger.info(f"[AgentWebSocketServer] 处理 command.model: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_model(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_RESUME:
                logger.info(f"[AgentWebSocketServer] 处理 command.resume: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_resume(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_SESSION:
                logger.info(f"[AgentWebSocketServer] 处理 command.session: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_command_session(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_START:
                logger.info(f"[AgentWebSocketServer] 处理 browser.start: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_browser_start(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_RUNTIME_RESTART:
                logger.info(f"[AgentWebSocketServer] 处理 browser.runtime_restart: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_browser_runtime_restart(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.CONFIG_CACHE_CLEAR:
                logger.info(f"[AgentWebSocketServer] 处理 config.cache_clear: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_config_cache_clear(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENT_RELOAD_CONFIG:
                logger.info(f"[AgentWebSocketServer] 处理 agent.reload_config: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_agent_reload_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SYNC_AGENTS_CONFIGS:
                logger.info(
                    "[AgentWebSocketServer] 处理 sync_agents_configs: request_id=%s",
                    request.request_id,
                    extra={'user_visible': 'progress'},
                )
                await self._handle_sync_agents_configs(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_LIST:
                logger.info(f"[AgentWebSocketServer] 处理 extensions.list: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_extensions_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_IMPORT:
                logger.info(f"[AgentWebSocketServer] 处理 extensions.import: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_extensions_import(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_DELETE:
                logger.info(f"[AgentWebSocketServer] 处理 extensions.delete: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_extensions_delete(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_TOGGLE:
                logger.info(f"[AgentWebSocketServer] 处理 extensions.toggle: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_extensions_toggle(ws, request, send_lock)
                return
            # 文件传输处理
            event_type = request.params.get("event_type") if isinstance(request.params, dict) else None
            if event_type in FILE_TRANSFER_EVENT_TYPES:
                logger.info(f"[AgentWebSocketServer] 处理 file_transfer: event_type={event_type}, \
                            request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_file_transfer(ws, request, send_lock)
                return
            if request.is_stream:
                logger.info(f"[AgentWebSocketServer] 处理 chat 流式请求: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_stream(ws, request, send_lock)
            else:
                logger.info(f"[AgentWebSocketServer] 处理 chat 非流式请求: request_id={request.request_id}",
                           extra={'user_visible': 'progress'})
                await self._handle_unary(ws, request, send_lock)
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 处理请求失败: request_id=%s: %s",
                request.request_id,
                e,
                extra={'user_visible': 'critical'}
            )
            error_resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(
                error_resp, response_id=request.request_id
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    async def _trigger_before_ws_server_start_hook() -> None:
        """在首次启动之前触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        from jiuwenclaw.utils import get_agent_skills_dir

        ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.BEFORE_WS_SERVER_START, ctx)


    @staticmethod
    async def _trigger_agent_server_started_hook() -> None:
        """在agentserver启动成功触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        from jiuwenclaw.utils import get_agent_skills_dir

        ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.AGENT_SERVER_STARTED, ctx)


    @staticmethod
    def _should_trigger_before_chat_request_hook(request: AgentRequest) -> bool:
        from jiuwenclaw.schema.message import ReqMethod

        return request.req_method in (
            ReqMethod.CHAT_SEND,
            ReqMethod.CHAT_RESUME,
            ReqMethod.CHAT_ANSWER,
        )

    async def _trigger_before_chat_request_hook(self, request: AgentRequest) -> None:
        if not self._should_trigger_before_chat_request_hook(request):
            return
        from jiuwenclaw.extensions.registry import ExtensionRegistry

        params = request.params if isinstance(request.params, dict) else {}
        if not isinstance(request.params, dict):
            request.params = params

        ctx = AgentServerChatHookContext(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            req_method=request.req_method.value if request.req_method is not None else None,
            params=params,
        )

        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.BEFORE_CHAT_REQUEST, ctx)

    async def _handle_custom_ws_handler(
        self,
        ws: Any,
        request: AgentRequest,
        entry,  # WsHandlerEntry
        ctx: WsHandlerContext,
        send_lock: asyncio.Lock,
    ) -> None:
        """调用自定义 WebSocket 处理器并返回响应。"""
        logger.info(
            "[AgentWebSocketServer] 调用自定义处理器: method=%s request_id=%s",
            entry.method,
            request.request_id,
        )

        try:
            payload = await entry.handler(ctx)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
                metadata=ctx.response_metadata,
            )
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 自定义处理器异常: method=%s request_id=%s: %s",
                entry.method,
                request.request_id,
                e,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e), "error_type": type(e).__name__},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
        logger.info(
            "[AgentWebSocketServer] 自定义处理器响应已发送: request_id=%s ok=%s",
            request.request_id,
            resp.ok,
            extra={'user_visible': 'progress'}
        )

    async def _handle_unary(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """非流式处理：调用 process_message，返回一条 E2AResponse 线 JSON。"""
        from jiuwenclaw.schema.message import ReqMethod

        channel_id = request.channel_id or "default"

        if request.req_method == ReqMethod.INITIALIZE:
            await self._handle_initialize(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.SESSION_CREATE:
            await self._handle_session_create(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.SESSION_DELETE:
            await self._handle_session_delete(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.ACP_TOOL_RESPONSE:
            await self._handle_acp_tool_response(ws, request, send_lock)
            return

        resp = await self._agent_manager.process_message(request)

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
        logger.info(
            "[AgentWebSocketServer] 非流式响应已发送: request_id=%s",
            request.request_id,
            extra={'user_visible': 'progress'}
        )

    async def _handle_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """流式处理：调用 process_message_stream，逐条发送 E2AResponse 线 JSON。"""
        # 诊断日志：确认 _handle_stream 是否被调用
        logger.info(
            "[AgentWebSocketServer] DIAGNOSTIC: _handle_stream 开始执行 "
            "| request_id=%s | is_stream=%s | channel=%s",
            request.request_id,
            request.is_stream,
            request.channel_id,
        )
        channel_id = request.channel_id or "default"

        chunk_count = 0
        # 心跳控制：当有真实 chunk 发送时重置，空闲时发送心跳
        heartbeat_event = asyncio.Event()
        heartbeat_task: asyncio.Task | None = None

        async def _heartbeat_loop() -> None:
            """后台心跳任务：在空闲期间定期发送 keepalive chunk."""
            try:
                while True:
                    # 等待心跳间隔，如果期间有真实 chunk 发送则 heartbeat_event 被设置，重置等待
                    try:
                        await asyncio.wait_for(
                            heartbeat_event.wait(),
                            timeout=_STREAM_HEARTBEAT_INTERVAL_SECONDS,
                        )
                        # 有真实 chunk 发送，重置 event 继续等待
                        heartbeat_event.clear()
                    except asyncio.TimeoutError:
                        # 超时：空闲超过心跳间隔，发送 keepalive chunk
                        heartbeat_chunk = AgentResponseChunk(
                            request_id=request.request_id,
                            channel_id=channel_id,
                            payload={"event_type": "keepalive"},
                            is_complete=False,
                        )
                        wire = encode_agent_chunk_for_wire(
                            heartbeat_chunk,
                            response_id=request.request_id,
                            sequence=-1,  # 心跳使用特殊序列号 -1
                        )
                        async with send_lock:
                            await ws.send(json.dumps(wire, ensure_ascii=False))
                        logger.debug(
                            "[AgentWebSocketServer] keepalive chunk 发送: request_id=%s",
                            request.request_id,
                        )
            except asyncio.CancelledError:
                pass

        # 启动心跳任务
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            async for chunk in self._agent_manager.process_message_stream(request):
                chunk_count += 1

                # 流式响应开始标记（第一个chunk）
                if chunk_count == 1:
                    logger.info(
                        f"[AgentWebSocketServer] 流式响应开始: request_id={request.request_id}",
                        extra={'user_visible': 'critical'}
                    )
                # 流式响应进度标记（每10个chunk）
                elif chunk_count % 10 == 0:
                    logger.info(
                        f"[AgentWebSocketServer] 流式响应进度: request_id={request.request_id} chunk_count={chunk_count}"
                    )

                # 通知心跳任务有真实 chunk 发送，重置心跳计时
                heartbeat_event.set()
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=chunk_count - 1,
                )
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                # 清除 event，让心跳任务重新开始计时
                heartbeat_event.clear()
        finally:
            # 停止心跳任务
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        logger.info(
            "[AgentWebSocketServer] 流式响应已发送: request_id=%s 共 %s 个 chunk",
            request.request_id,
            chunk_count,
        )

    async def _handle_session_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.list 请求：扫描 sessions 目录，返回历史会话基础信息列表.

        使用 TenantAgentPool.extract_ids 获取租户 ID，默认为 ('default', 'default')。
        """
        from jiuwenclaw.agentserver.session_metadata import get_session_metadata

        # extract_ids 现在总是返回有效值（默认或指定的 tenant ID）
        sessions_dir = _sessions_dir_for_request(request)
        sessions = []

        try:
            if sessions_dir.exists():
                for entry in sorted(sessions_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
                    if not entry.is_dir():
                        continue
                    meta = get_session_metadata(entry.name, sessions_root=sessions_dir)
                    if not meta:
                        meta = {
                            "session_id": entry.name,
                            "channel_id": "",
                            "title": "",
                            "message_count": 0,
                            "last_message_at": entry.stat().st_mtime,
                        }
                    sessions.append(meta)
        except Exception as exc:
            logger.warning("[AgentWebSocketServer] 扫描 sessions 目录失败: %s", exc)

        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"sessions": sessions},
            metadata=request.metadata,
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_rename(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.rename：与 CLI Gateway 本地回退共用 apply_session_rename。"""
        from jiuwenclaw.agentserver.session_rename import apply_session_rename

        sid = request.session_id or ""
        ch = (request.channel_id or "").strip() or "tui"
        ok, payload, err, code = apply_session_rename(
            request.params,
            sid,
            init_channel_id=ch,
            sessions_root=_sessions_dir_for_request(request),
        )
        if ok:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload or {},
                metadata=request.metadata,
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": err or "session.rename failed", "code": code or ""},
                metadata=request.metadata,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_permissions_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 permissions.* E2A 请求（与 Web ``register_method`` 同名 method）。"""
        from jiuwenclaw.agentserver.permissions.config_rpc import dispatch_permissions_config_request

        pool = self._agent_manager
        catalog_fn = pool.collect_runtime_tools_catalog_nowait if pool is not None else None
        resp = dispatch_permissions_config_request(
            request,
            get_runtime_tools_catalog=catalog_fn,
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_history_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(
            session_id=session_id,
            page_idx=page_idx,
            sessions_root=_sessions_dir_for_request(request),
        )
        if data is None:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "invalid page_idx or session history not found"},
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=data,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_history_get_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(
            session_id=session_id,
            page_idx=page_idx,
            sessions_root=_sessions_dir_for_request(request),
        )
        if data is None:
            err_chunk = AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={
                    "event_type": "chat.error",
                    "error": "invalid page_idx or session history not found",
                },
                is_complete=True,
            )
            wire = encode_agent_chunk_for_wire(
                err_chunk,
                response_id=request.request_id,
                sequence=0,
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        messages = data.get("messages", [])
        total_pages = data.get("total_pages")
        page = data.get("page_idx")
        if isinstance(messages, list):
            for seq, item in enumerate(messages):
                chunk = AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={
                        "event_type": "history.message",
                        "message": item,
                        "total_pages": total_pages,
                        "page_idx": page,
                    },
                    is_complete=False,
                )
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=seq,
                )
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))

        done_chunk = AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={
                "event_type": "history.message",
                "status": "done",
                "total_pages": total_pages,
                "page_idx": page,
            },
            is_complete=True,
        )
        done_seq = len(messages) if isinstance(messages, list) else 0
        wire_done = encode_agent_chunk_for_wire(
            done_chunk,
            response_id=request.request_id,
            sequence=done_seq,
        )
        async with send_lock:
            await ws.send(json.dumps(wire_done, ensure_ascii=False))

    async def _handle_command_add_dir(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            directory_path = params.get("path")
            remember = params.get("remember", False)
            persist: dict[str, Any]
            if directory_path is None or (
                isinstance(directory_path, str) and not directory_path.strip()
            ):
                persist = {"ok": False, "error": "path is required"}
            else:
                persist = persist_cli_trusted_directory(str(directory_path))
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=bool(persist.get("ok", False)),
                payload={
                    "path": directory_path,
                    "remember": remember,
                    "persist": persist,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.add_dir failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_chrome(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.chrome failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_ls(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from datetime import datetime
        from jiuwenclaw.agentserver.session_metadata import get_resolved_project_dir
        logger.info("[AgentWebSocketServer] command.ls %s", request.params)
        try:
            params = request.params or {}
            relative_path = str(params.get("path", ".")).strip()

            session_id = request.session_id or "default"
            workspace_dir = Path(
                get_resolved_project_dir(session_id, _sessions_dir_for_request(request))
            )
            logger.info("[AgentWebSocketServer] command.ls workspace_dir: %s", workspace_dir)
            target_path = (workspace_dir / relative_path).resolve()
            logger.info("[AgentWebSocketServer] command.ls target_path: %s", target_path)

            try:
                target_path.relative_to(workspace_dir.resolve())
            except ValueError:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "Path outside workspace"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            entries = []
            if target_path.is_dir():
                for item in sorted(target_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    try:
                        stat = item.stat()
                        entries.append({
                            "name": item.name,
                            "is_dir": item.is_dir(),
                            "size": stat.st_size if item.is_file() else None,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        })
                    except OSError as e:
                        entries.append({
                            "name": item.name,
                            "is_dir": item.is_dir(),
                            "error": str(e),
                        })
            logger.info("[AgentWebSocketServer] command.ls entries: %s", entries)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "path": str(target_path),
                    "relative_path": relative_path,
                    "entries": entries,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] command.ls failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_view(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenclaw.agentserver.session_metadata import get_resolved_project_dir
        logger.info("[AgentWebSocketServer] command.view %s", request.params)
        try:
            params = request.params or {}
            relative_path = str(params.get("path", "")).strip()
            from_line = int(params.get("from_line", 1))
            lines = params.get("lines")

            if not relative_path:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "Missing path parameter"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            session_id = request.session_id or "default"
            workspace_dir = Path(
                get_resolved_project_dir(session_id, _sessions_dir_for_request(request))
            )
            target_path = (workspace_dir / relative_path).resolve()

            try:
                target_path.relative_to(workspace_dir.resolve())
            except ValueError:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "Path outside workspace"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            if not target_path.is_file():
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": f"Not a file: {relative_path}"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
            except UnicodeDecodeError:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "Binary file, cannot display"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            start_idx = max(0, from_line - 1)
            if lines is not None and lines > 0:
                end_idx = min(len(all_lines), start_idx + lines)
            else:
                end_idx = len(all_lines)

            selected_lines = all_lines[start_idx:end_idx]

            numbered = []
            for i, line in enumerate(selected_lines, start=start_idx + 1):
                numbered.append(f"{i:4d} | {line.rstrip()}")
            numbered_content = '\n'.join(numbered)
            summary = (
                f"\n\n---\n"
                f"📄 文件: `{relative_path}`\n"
                f"📊 总行数: {len(all_lines)}, 显示: {len(selected_lines)} 行 "
                f"(第 {start_idx + 1}-{end_idx} 行)"
            )
            
            content = f"```\n{numbered_content}\n```{summary}"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "path": str(target_path),
                    "relative_path": relative_path,
                    "content": content,
                    "from_line": from_line,
                    "total_lines": len(all_lines),
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] command.view failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_compact(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            custom_instructions = params.get("instructions")
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"instructions": custom_instructions},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.compact failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_diff(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenclaw.agentserver.diff_service import get_diff_service

        try:
            session_id = request.session_id or "default"
            agent_id, service_id = TenantAgentPool.extract_ids(request)
            diff_service = get_diff_service()
            turns = diff_service.get_turn_diffs(
                session_id,
                service_id=service_id,
                agent_id=agent_id,
            )

            logger.info(
                "[AgentWebSocketServer] command.diff response: session_id=%s turns=%s",
                session_id,
                turns,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "type": "list",
                    "turns": turns,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] command.diff failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_model(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            if request.channel_id == "officeclaw":
                guard = TenantAgentPool.require_officeclaw_agent(request)
                if guard is not None:
                    resp = guard
                    wire = encode_agent_response_for_wire(
                        resp, response_id=request.request_id
                    )
                    async with send_lock:
                        await ws.send(json.dumps(wire, ensure_ascii=False))
                    return

            params = request.params or {}
            action = params.get("action")

            if action == "add_model":
                target = str(params.get("target", "")).strip()
                logger.info("[command.model] add_model: target=%s", target)
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "model_added", "name": target},
                )

            elif action == "switch_model":
                target = str(params.get("model", "")).strip()
                env_updates = params.get("env_updates", {})
                logger.info(
                    "[command.model] switch_model: target=%s, env_updates=%s",
                    target,
                    {k: (v if k != "API_KEY" else "***") for k, v in env_updates.items()},
                )

                if not env_updates:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "No env_updates provided"},
                    )
                else:
                    agent_id, service_id = TenantAgentPool.extract_ids(request)
                    apply_env_overrides_to_active(
                        env_updates,
                        service_id=service_id,
                        agent_id=agent_id,
                    )
                    for k, v in env_updates.items():
                        set_os_environ(
                            k, v, service_id=service_id, agent_id=agent_id
                        )
                    tip = effective_tip(service_id, agent_id)
                    model_name = str(tip.get("MODEL_NAME") or "unknown")
                    logger.info("[command.model] tip 已更新, MODEL_NAME=%s", model_name)

                    try:
                        from jiuwenclaw.agentserver.memory.config import clear_config_cache
                        clear_config_cache()
                        logger.info("[command.model] config cache 已清除")
                    except Exception as e:
                        logger.debug("[command.model] clear_config_cache skipped: %s", e)

                    try:
                        await self._agent_manager.reload_tenant_config(
                            agent_id,
                            service_id,
                            None,
                            env_updates,
                        )
                        logger.info("[command.model] agent config 已重载")
                    except Exception as e:
                        logger.debug("[command.model] reload_tenant_config skipped: %s", e)

                    current = str(
                        effective_tip(service_id, agent_id).get("MODEL_NAME") or "unknown"
                    )
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={
                            "current": current,
                            "requested": target,
                            "type": "switched",
                            "applied": True,
                        },
                    )
                    logger.info("[command.model] 切换完成: current=%s", current)

            else:
                agent_id, service_id = TenantAgentPool.extract_ids(request)
                current = str(
                    effective_tip(service_id, agent_id).get("MODEL_NAME")
                    or get_local_config("MODEL_NAME", "unknown")
                    or "unknown"
                )
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"current": current, "available": ["default-model"]},
                )

        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.model failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_resume(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            query = params.get("query")
            session_id = query if isinstance(query, str) and query.strip() else "sess_mock_resume"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "query": query if isinstance(query, str) else "",
                    "resumed": True,
                    "preview": "Mock resumed conversation",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.resume failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_session(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "sess_mock"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "remote_url": f"https://example.com/session/{session_id}",
                    "qr_text": f"session:{session_id}",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.session failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_browser_start(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """启动浏览器并返回执行结果（returncode）。"""
        try:
            from jiuwenclaw.agentserver.tools.browser_start_client import start_browser

            config_path = str(get_config_file())
            returncode = start_browser(dry_run=False, config_file=config_path)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"returncode": returncode},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.start failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_browser_runtime_restart(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from jiuwenclaw.agentserver.tools.browser_tools import (
                restart_local_browser_runtime_server,
            )

            agent_id, service_id = TenantAgentPool.extract_ids(request)
            result = restart_local_browser_runtime_server(
                service_id=service_id,
                agent_id=agent_id,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"result": result},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.runtime_restart failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_config_cache_clear(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from jiuwenclaw.agentserver.memory.config import clear_config_cache

            clear_config_cache()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"cleared": True},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] config.cache_clear failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agent_reload_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        reload_trace_id = request.request_id
        try:
            params = request.params or {}
            config_payload = params.get("config")
            env_overrides = params.get("env")

            # 触发 AGENT_RELOAD_CONFIG hook
            try:
                from jiuwenclaw.extensions.registry import ExtensionRegistry
                ctx = AgentReloadConfigHookContext(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    config=config_payload,
                    env=env_overrides,
                )
                await ExtensionRegistry.get_instance().trigger(
                    AgentServerHookEvents.AGENT_RELOAD_CONFIG, ctx
                )
                # 允许扩展修改配置
                config_payload = ctx.config
                env_overrides = ctx.env
            except RuntimeError:
                # ExtensionRegistry 未初始化，跳过
                pass

            from jiuwenclaw.agentserver.reload_result import (
                log_agent_config_hot_reload,
                summarize_reload_payload,
            )
            if request.channel_id == "officeclaw":
                guard = TenantAgentPool.require_officeclaw_agent(request)
                if guard is not None:
                    resp = guard
                    wire = encode_agent_response_for_wire(
                        resp, response_id=request.request_id
                    )
                    async with send_lock:
                        await ws.send(json.dumps(wire, ensure_ascii=False))
                    return

            raw_agent = getattr(request, "agent_id", None)
            agent_id, service_id = TenantAgentPool.extract_ids(request)

            if (
                request.channel_id == "officeclaw"
                or (raw_agent is not None and str(raw_agent).strip())
            ):
                aggregate = await self._agent_manager.reload_tenant_config(
                    agent_id,
                    service_id,
                    config=config_payload,
                    env=env_overrides,
                    reload_trace_id=reload_trace_id,
                )
            else:
                aggregate = await self._agent_manager.reload_agents_config(
                    config=config_payload,
                    env=env_overrides,
                    reload_trace_id=reload_trace_id,
                )
            payload = aggregate.to_payload()
            log_agent_config_hot_reload(
                logger,
                reload_trace_id=reload_trace_id,
                phase="completed",
                source="AgentWebSocketServer",
                **summarize_reload_payload(payload),
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=not aggregate.failed or aggregate.applied > 0 or aggregate.deferred > 0,
                payload=payload,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] agent.reload_config failed: %s", e)
            from jiuwenclaw.agentserver.reload_result import (
                log_agent_config_hot_reload,
                redact_reload_error_message,
            )
            log_agent_config_hot_reload(
                logger,
                reload_trace_id=reload_trace_id,
                phase="failed",
                source="AgentWebSocketServer",
                level=logging.ERROR,
                error=redact_reload_error_message(str(e)),
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_sync_agents_configs(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        from jiuwenclaw.schema.message import EventType

        params = request.params if isinstance(request.params, dict) else {}
        try:
            payload = await self._agent_manager.sync_agents_configs(params)
            agents = payload.get("agents") if isinstance(payload, dict) else []
            all_ok = (
                isinstance(agents, list)
                and all(isinstance(item, dict) and item.get("ok") for item in agents)
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=all_ok,
                payload={
                    "event_type": EventType.SYNC_AGENTS_CONFIGS_RESULT.value,
                    **payload,
                },
            )
        except ValueError as exc:
            logger.warning(
                "[AgentWebSocketServer] sync_agents_configs rejected: %s", exc
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "event_type": EventType.SYNC_AGENTS_CONFIGS_RESULT.value,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            logger.exception(
                "[AgentWebSocketServer] sync_agents_configs failed: %s", exc
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "event_type": EventType.SYNC_AGENTS_CONFIGS_RESULT.value,
                    "error": str(exc),
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """获取所有 Rail 扩展列表."""
        try:
            manager = get_rail_manager(RuntimeScopeKey.from_request(request))
            extensions = manager.list_extensions()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"extensions": extensions},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_import(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """导入新的 Rail 扩展（文件夹结构）."""
        try:
            params = request.params or {}
            folder_path = params.get("folder_path")

            if not folder_path:
                raise ValueError("缺少 folder_path 参数")

            source_path = Path(folder_path)
            if not source_path.exists() or not source_path.is_dir():
                raise ValueError(f"文件夹不存在或不是目录: {folder_path}")

            manager = get_rail_manager(RuntimeScopeKey.from_request(request))
            extension = manager.import_extension(folder_path)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.import failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """删除 Rail 扩展."""
        try:
            params = request.params or {}
            name = params.get("name")

            if not name:
                raise ValueError("缺少 name 参数")

            manager = get_rail_manager(RuntimeScopeKey.from_request(request))
            manager.delete_extension(name)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"deleted": True, "name": name},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_toggle(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """切换 Rail 扩展的启用状态，并触发热更新."""
        try:
            params = request.params or {}
            name = params.get("name")
            enabled = params.get("enabled", False)

            if name is None:
                raise ValueError("缺少 name 参数")
            if enabled is None:
                raise ValueError("缺少 enabled 参数")

            manager = get_rail_manager(RuntimeScopeKey.from_request(request))

            # 1. 确保 agent 实例已设置（用于热更新）
            agent = self._agent_manager.get_agent_nowait()
            if agent is not None:
                agent_instance = agent.get_instance()
                if agent_instance is not None:
                    manager.set_agent_instance(agent_instance)

            # 2. 更新配置文件中的启用状态
            extension = manager.toggle_extension(name, enabled)

            # 3. 触发热更新：根据 enabled 状态注册或注销 rail
            await manager.hot_reload_rail(name, enabled)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.toggle failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def send_push(self, msg) -> None:
        """AgentServer 主动向 Gateway 推送消息。

        payload 格式与 AgentResponse.payload 一致，
        可含 event_type 等字段供 Gateway 转为 Message 派发到 Channel。
        """
        if self._current_ws is None or self._current_send_lock is None:
            logger.warning(
                "[AgentWebSocketServer] send_push 失败: 无活跃 Gateway 连接"
            )
            return

        try:
            wire = build_server_push_wire(msg)
            async with self._current_send_lock:
                await self._current_ws.send(json.dumps(wire, ensure_ascii=False))
            response_kind = str(msg.get("response_kind") or "").strip()
            if response_kind:
                logger.info(
                    "[AgentWebSocketServer] send_push response_kind wire sent: channel_id=%s kind=%s",
                    msg.get("channel_id", ""),
                    response_kind,
                )
            else:
                logger.info(
                    "[AgentWebSocketServer] send_push 已发送(E2A wire): channel_id=%s",
                    msg.get("channel_id", ""),
                )
        except Exception as e:
            logger.warning("[AgentWebSocketServer] send_push 失败: %s", e)

    def get_agent(self):
        """获取 default agent 实例（向后兼容）."""
        return self._agent_manager.get_agent_nowait()

    def get_agent_manager(self) -> TenantAgentPool:
        """获取 AgentManager 实例."""
        return self._agent_manager

    # =========================================================================
    # 文件传输处理
    # =========================================================================

    async def _handle_file_transfer(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理文件传输请求（Gateway -> AgentServer）.

        支持：
        - file.transfer.start: 开始传输
        - file.transfer.chunk: 传输分片
        - file.transfer.complete: 完成传输
        """
        params = request.params if isinstance(request.params, dict) else {}
        event_type = params.get("event_type", "")
        transfer_id = params.get("transfer_id", "")

        ft_manager = get_file_transfer_manager()

        # 检查是否启用分布式模式
        if not ft_manager.enabled:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "file transfer not enabled (distributed mode required)",
                    "event_type": event_type,
                },
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            if event_type == FILE_TRANSFER_START:
                ft_params = FileTransferStartParams(
                    transfer_id=transfer_id,
                    filename=params.get("filename", "unnamed"),
                    file_size=params.get("file_size", 0),
                    sha256=params.get("sha256", ""),
                    total_chunks=params.get("total_chunks", 0),
                    chunk_size=params.get("chunk_size", 65536),
                    mime_type=params.get("mime_type", ""),
                    session_id=request.session_id or "",
                    service_id=str(
                        params.get("service_id")
                        or getattr(request, "service_id", None)
                        or ""
                    ),
                    agent_id=str(
                        params.get("agent_id")
                        or getattr(request, "agent_id", None)
                        or ""
                    ),
                )
                result = await ft_manager.handle_transfer_start(ft_params)
            elif event_type == FILE_TRANSFER_CHUNK:
                result = await ft_manager.handle_transfer_chunk(
                    transfer_id=transfer_id,
                    chunk_index=params.get("chunk_index", 0),
                    base64_data=params.get("base64_data", ""),
                )
            elif event_type == FILE_TRANSFER_COMPLETE:
                result = await ft_manager.handle_transfer_complete(
                    transfer_id=transfer_id,
                    sha256=params.get("sha256", ""),
                )
            else:
                result = {
                    "accepted": False,
                    "error": f"unknown event_type: {event_type}",
                }

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=result.get("success", result.get("accepted", False)),
                payload={
                    "event_type": event_type,
                    **result,
                },
            )
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 文件传输处理失败: %s",
                e,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": str(e),
                    "event_type": event_type,
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
            
    @staticmethod
    def get_conversation_history(
        session_id: str,
        page_idx: int,
        *,
        sessions_root: str | Path | None = None,
    ) -> dict[str, Any] | None:
        # 按照 session_id 和分页消息获取历史记录
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        if not isinstance(page_idx, int) or page_idx <= 0:
            return None

        root = Path(sessions_root) if sessions_root else get_agent_sessions_dir()
        history_path: Path = root / session_id.strip() / "history.json"
        if not history_path.exists():
            return None
        try:
            raw = read_history_records_for_frontend(history_path)
        except Exception as e:
            logger.warning("Failed to read history for session %s: %s", session_id, e)
            return None
        if not isinstance(raw, list):
            return None

        page_size = 50
        total = len(raw)
        total_pages = max(1, math.ceil(total / page_size))
        if page_idx > total_pages:
            return None

        ordered = list(reversed(raw))
        start = (page_idx - 1) * page_size
        end = start + page_size
        resolved_sid = session_id.strip()
        page_slice = ordered[start:end]
        messages_out = enrich_history_messages_session_id(page_slice, resolved_sid)
        return {
            "messages": messages_out,
            "total_pages": total_pages,
            "page_idx": page_idx,
        }

    async def _handle_initialize(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 initialize 方法（非流式）.

        调用 AgentManager.initialize 完成初始化，返回 capabilities。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] initialize: request_id=%s channel_id=%s", request.request_id, request.channel_id)

        try:
            params = request.params if isinstance(request.params, dict) else {}
            client_capabilities = params.get("clientCapabilities", {})
            logger.info(
                "[AgentServer] initialize clientCapabilities: %s",
                client_capabilities,
            )

            extra_config = {
                "protocol_version": params.get("protocolVersion", "0.1.0"),
                "client_capabilities": client_capabilities,
            }
            if request.channel_id == "acp":
                self._set_ws_acp_client_capabilities(ws, client_capabilities)

            channel_id = request.channel_id or "default"
            capabilities = await self._agent_manager.initialize(
                channel_id=channel_id,
                extra_config=extra_config,
            )
            if capabilities is None:
                capabilities = ACP_DEFAULT_CAPABILITIES.copy()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=capabilities,
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] initialize completed: capabilities=%s", capabilities)

        except Exception as e:
            logger.exception("[AgentServer] initialize failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_create(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 session.create 方法.

        调用 AgentManager.create_session 创建会话，返回 session_id。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] session.create: request_id=%s", request.request_id)

        try:
            channel_id = request.channel_id or "default"
            params = request.params if isinstance(request.params, dict) else {}
            explicit_session_id = params.get("session_id")
            session_id = await self._agent_manager.create_session(
                channel_id=channel_id,
                session_id=str(explicit_session_id).strip() if isinstance(explicit_session_id, str) else None,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=build_acp_session_new_result(session_id),
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] session.create completed: session_id=%s", session_id)

        except Exception as e:
            logger.exception("[AgentServer] session.create failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_delete(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 session.delete 请求：删除 Agent 本机 sessions 目录下的会话目录。"""
        import shutil

        from jiuwenclaw.agentserver.session_id_safe import (
            normalize_safe_session_id,
            resolve_session_dir_under_root,
        )

        logger.info("[AgentServer] session.delete: request_id=%s", request.request_id)

        try:
            params = request.params if isinstance(request.params, dict) else {}
            raw_sid = str(params.get("session_id") or "").strip()
            if not raw_sid:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "session_id is required", "code": "BAD_REQUEST"},
                )
            else:
                safe_sid = normalize_safe_session_id(raw_sid)
                if safe_sid is None:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                    )
                else:
                    workspace_session_dir = _sessions_dir_for_request(request)
                    session_dir = resolve_session_dir_under_root(workspace_session_dir, safe_sid)
                    if session_dir is None:
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                        )
                    elif not session_dir.exists():
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session not found", "code": "NOT_FOUND"},
                        )
                    elif not session_dir.is_dir():
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session is not a directory", "code": "BAD_REQUEST"},
                        )
                    else:
                        await asyncio.to_thread(shutil.rmtree, session_dir)
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=True,
                            payload={"session_id": safe_sid},
                        )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentServer] session.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_acp_tool_response(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
    ) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        jsonrpc_id = params.get("jsonrpc_id")
        response_payload = params.get("response")
        if not isinstance(response_payload, dict):
            response_payload = {}

        if get_acp_output_manager().complete_jsonrpc_response(jsonrpc_id, response_payload):
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"accepted": True},
            )
        else:
            logger.info(
                "[AgentServer] ignore unknown/late acp tool response: jsonrpc_id=%s request_id=%s",
                jsonrpc_id,
                request.request_id,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "accepted": False,
                    "ignored": True,
                    "reason": "unknown_or_late_response",
                    "jsonrpc_id": jsonrpc_id,
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def handle_acp_tool_response_for_test(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
    ) -> None:
        """Public test helper that delegates to ACP tool-response handling."""
        await self._handle_acp_tool_response(ws, request, send_lock)

    def is_working(self) -> bool:
        """返回 Agent 是否正在工作.

        用于沙箱保活校验。

        Returns:
            bool: 是否正在工作
        """
        return self._agent_manager.is_working()
