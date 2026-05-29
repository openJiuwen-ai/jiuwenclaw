# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import socket
from typing import Any, AsyncIterator, Callable, Optional

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IServiceHandler,
    IServiceInstanceFactory,
    ISessionRequest,
)
from openjiuwen_runtime.management.session.k8s_service_handler import (
    ContainerSpec,
    HostPathMount,
    K8sDeployController,
    K8sServiceHandler,
)
from openjiuwen_runtime.management.session.process_service_handler import (
    ProcessDeployController,
    ProcessServiceHandler,
)
from openjiuwen_runtime.management.session.models import (
    AccessConfig,
    MessagePriority,
    SessionConfig,
)
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import (
    QueueItem,
    ServiceManager,
)
from openjiuwen_runtime.management.session.timer import Timer
from openjiuwen_runtime.management.session.ws_client_channel import (
    WSServiceMessageChannel,
)

from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.models import E2AEnvelope, E2AResponse
from jiuwenclaw.e2a.wire_codec import (
    is_e2a_response_wire_dict,
    parse_agent_server_wire_chunk,
)
from jiuwenclaw.gateway.agent_client import AgentServerClient, _wire_request_id_key
from jiuwenclaw.schema import AgentRequest
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk

logger = logging.getLogger(__name__)


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])

_load_effective_enterprise_config: Any | None = None
_service_config_slot: Any | None = None
_extension_config_slot: Any | None = None


def _ensure_enterprise_config_loader() -> tuple[Any, Any, Any]:
    global _load_effective_enterprise_config, _service_config_slot, _extension_config_slot
    if (
        _load_effective_enterprise_config is not None
        and _service_config_slot is not None
        and _extension_config_slot is not None
    ):
        return _load_effective_enterprise_config, _service_config_slot, _extension_config_slot

    from jiuwenclaw.gateway.channel_config_db import (
        _EXT_PKG,
        _ensure_extension_package,
        _resolve_manager_ws_client_root,
    )

    ext_root = _resolve_manager_ws_client_root()
    if ext_root is None:
        raise ImportError("manager_ws_client extension not found")
    _ensure_extension_package(ext_root)
    loader_mod = importlib.import_module(f"{_EXT_PKG}.core.enterprise_config.loader")
    schemas_mod = importlib.import_module(f"{_EXT_PKG}.core.enterprise_config.schemas")
    _load_effective_enterprise_config = loader_mod.load_effective_enterprise_config
    _service_config_slot = schemas_mod.TemplateRefSlot.SERVICE_CONFIG
    _extension_config_slot = schemas_mod.TemplateRefSlot.EXTENSION_CONFIG
    return _load_effective_enterprise_config, _service_config_slot, _extension_config_slot


async def load_effective_service_config_for_request(request: AgentRequest) -> Any | None:
    """按当前请求路由上下文加载 ``service_config`` 与 ``extension_config`` 槽位。"""
    try:
        load_fn, service_config_slot, extension_config_slot = _ensure_enterprise_config_loader()
        loaded = await load_fn(request, [service_config_slot, extension_config_slot])
    except Exception as exc:
        logger.warning(
            "[RuntimeManagementAgentClient] load service_config failed: %s",
            exc,
        )
        return None

    if loaded is None:
        logger.warning("[RuntimeManagementAgentClient] no service_config loaded")
        return None

    entities = loaded.service_config or []
    if entities:
        logger.info(
            "[RuntimeManagementAgentClient] service_config loaded: %s",
            entities,
        )
    return loaded


def _resolve_invoke_ids_from_request(msg: AgentRequest) -> tuple[str, str | None]:
    """Require gateway-filled ``service_id`` / ``agent_id`` (no session_id parsing fallback)."""
    svc = str(msg.service_id or "").strip()
    if not svc:
        raise ValueError(
            "RuntimeManagementAgentClient requires AgentRequest.service_id "
            "(set gateway-side, e.g. SessionMap path)"
        )
    ag = str(msg.agent_id or "").strip()
    return svc, ag if ag else None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class _SessionRequest(ISessionRequest):
    """ISessionRequest 实现。"""

    def __init__(
        self,
        msg: AgentRequest,
        envelope: E2AEnvelope,
        service_template: Optional[dict[str, Any]] = None,
    ) -> None:
        self._req = msg
        self._envelope = envelope
        self._service_template = service_template
        service_id, agent_id = _resolve_invoke_ids_from_request(self._req)
        self._service_id = service_id
        self._req.service_id = service_id
        self._req.agent_id = agent_id or ""
        self._envelope.service_id = service_id
        self._envelope.agent_id = agent_id or None

    @property
    def session_id(self) -> str:
        return self._service_id

    @property
    def session_concurrency(self) -> int:
        return int(os.getenv("AGENT_SERVER_SESSION_CONCURRENCY"))

    @property
    def session_ttl(self) -> int:
        return int(os.getenv("AGENT_SERVER_SESSION_TTL"))

    @property
    def priority(self) -> "MessagePriority":
        return MessagePriority.LOW

    @property
    def request_id(self) -> Optional[str]:
        return self._req.request_id

    @property
    def raw_msg(self) -> Any:
        return json.dumps(self._envelope.to_dict(), ensure_ascii=False)

    @property
    def service_template(self) -> Optional[dict[str, Any]]:
        return self._service_template


class E2aEnvelopResponseParser(IResponseParser):
    """解析 AgentServer WebSocket 下行 JSON。"""

    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        rid = data.get("request_id")
        if rid is None:
            rid = data.get("response_id")
        if rid is None:
            rid = data.get("id")
        if rid is None:
            return None
        out = _wire_request_id_key(rid)
        return out if out else None

    def is_completed(self, data: dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            return True
        if data.get("type") == "event":
            return False
        if is_e2a_response_wire_dict(data):
            try:
                e2a = E2AResponse.from_dict(dict(data))
                return bool(e2a.is_final)
            except Exception:
                return False
        return bool(data.get("is_complete"))

    def response(self, data: dict[str, Any]) -> Any:
        return parse_agent_server_wire_chunk(data)


class RuntimeManagementAgentClient(AgentServerClient):
    """Runtime Management Agent Client."""

    _POD_LABEL_KEY = "jiuwenclaw-component"
    _POD_LABEL_VALUE = "agentserver"
    _POD_LABEL = {_POD_LABEL_KEY: _POD_LABEL_VALUE}
    _POD_LABEL_SELECTOR = f"{_POD_LABEL_KEY}={_POD_LABEL_VALUE}"

    def __init__(self) -> None:
        """初始化 Runtime Management 客户端。"""

        agent_image = os.getenv("AGENT_SERVER_IMAGE")
        agent_runtime = os.getenv("AGENT_RUNTIME")
        namespace = os.getenv("AGENT_SERVER_NAMESPACE")
        self._namespace = os.getenv("AGENT_SERVER_NAMESPACE")
        container_name = os.getenv("AGENT_SERVER_CONTAINER_NAME")
        container_port = int(os.getenv("AGENT_SERVER_PORT"))
        port_name = os.getenv("AGENT_SERVER_PORT_NAME")
        image_pull_policy = os.getenv("AGENT_SERVER_IMAGE_PULL_POLICY")
        min_idle_services = int(os.getenv("AGENT_SERVER_MIN_IDLE_SERVICES"))
        max_services = int(os.getenv("AGENT_SERVER_MAX_SERVICES"))
        service_concurrency = int(os.getenv("AGENT_SERVER_SERVICE_CONCURRENCY"))
        service_ttl = int(os.getenv("AGENT_SERVER_SERVICE_TTL"))
        autoscale_interval = float(os.getenv("AGENT_SERVER_AUTOSCALE_INTERVAL"))
        nfs_server = os.getenv("AGENT_SERVER_NFS_SERVER", "")
        nfs_path = os.getenv("AGENT_SERVER_NFS_PATH", "/")
        nfs_mount_path = os.getenv("AGENT_SERVER_NFS_MOUNT_PATH")
        host_path = os.getenv("AGENT_SERVER_HOST_PATH")
        host_mount_path = os.getenv("AGENT_SERVER_HOST_MOUNT_PATH")
        mode = os.getenv("MODE")
        node_name = os.getenv("NODE_NAME")
        kubeconfig = os.getenv("AGENT_SERVER_KUBECONFIG") or None
        self._kubeconfig = os.getenv("AGENT_SERVER_KUBECONFIG") or None
        readiness_initial_delay = int(os.getenv("AGENT_SERVER_READINESS_INITIAL_DELAY"))
        readiness_period = int(os.getenv("AGENT_SERVER_READINESS_PERIOD"))
        ready_timeout = int(os.getenv("AGENT_SERVER_READY_TIMEOUT"))
        ready_poll_interval = int(os.getenv("AGENT_SERVER_READY_POLL_INTERVAL"))

        # K8s Resource Configuration
        cpu_request = os.getenv("CPU_REQUEST")
        memory_request = os.getenv("MEMORY_REQUEST")
        cpu_limit = os.getenv("CPU_LIMIT")
        memory_limit = os.getenv("MEMORY_LIMIT")

        model_provider = os.getenv("MODEL_PROVIDER")
        model_name = os.getenv("MODEL_NAME")
        api_base = os.getenv("API_BASE")
        api_key = os.getenv("API_KEY")
        gateway_db_type = os.getenv("GATEWAY_DB_TYPE")
        gateway_sqlite_path = os.getenv("GATEWAY_SQLITE_PATH")
        gateway_db_host = os.getenv("GATEWAY_DB_HOST")
        gateway_db_port = os.getenv("GATEWAY_DB_PORT")
        gateway_db_user = os.getenv("GATEWAY_DB_USER")
        gateway_db_password = os.getenv("GATEWAY_DB_PASSWORD")
        gateway_db_name = os.getenv("GATEWAY_DB_NAME")
        jiuwenclaw_id = os.getenv("JIUWENCLAW_ID")

        deploy_mode = (os.getenv("AGENT_SERVER_DEPLOY_MODE") or "k8s").strip().lower()

        # jiuwenbox sidecar: agentserver 与 jiuwenbox 同 Pod，使用 127.0.0.1 访问。
        jiuwenclaw_sandbox_enabled = _env_bool("JIUWENCLAW_SANDBOX_ENABLED", False)
        jiuwenclaw_sandbox_image = os.getenv("JIUWENCLAW_SANDBOX_IMAGE", "jiuwenbox:latest")
        jiuwenclaw_sandbox_port = int(os.getenv("JIUWENCLAW_SANDBOX_PORT", "8321"))
        jiuwenclaw_sandbox_container_name = os.getenv(
            "JIUWENCLAW_SANDBOX_CONTAINER_NAME", "jiuwenbox"
        )
        jiuwenclaw_sandbox_url = os.getenv(
            "JIUWENCLAW_SANDBOX_URL",
            f"http://127.0.0.1:{jiuwenclaw_sandbox_port}",
        )
        jiuwenclaw_sandbox_excluded_commands = os.getenv("JIUWENCLAW_SANDBOX_EXCLUDED_COMMANDS", "")
        jiuwenclaw_sandbox_idle_ttl_seconds = os.getenv("JIUWENCLAW_SANDBOX_IDLE_TTL_SECONDS", "")
        jiuwenclaw_sandbox_idle_check_interval = os.getenv("JIUWENCLAW_SANDBOX_IDLE_CHECK_INTERVAL", "")
        jiuwenbox_listen = os.getenv("JIUWENBOX_LISTEN", f"tcp://0.0.0.0:{jiuwenclaw_sandbox_port}")
        jiuwenbox_policy_path = os.getenv("JIUWENBOX_POLICY_PATH", "/app/configs/enterprise-policy.yaml")
        jiuwenbox_host_mounts: list[HostPathMount] = []
        if _env_bool("JIUWENBOX_MOUNT_CGROUP", True):
            jiuwenbox_host_mounts.append(
                HostPathMount(
                    host_path="/sys/fs/cgroup",
                    mount_path="/sys/fs/cgroup",
                    read_only=False,
                    host_path_type="Directory",
                )
            )

        def _agent_env_vars() -> dict[str, str]:
            base: dict[str, str] = {
                "AGENT_SERVER_HOST": "0.0.0.0",
                "AGENT_RUNTIME": agent_runtime or "",
            }
            for key, value in (
                ("GATEWAY_DB_TYPE", gateway_db_type),
                ("GATEWAY_SQLITE_PATH", gateway_sqlite_path),
                ("GATEWAY_DB_HOST", gateway_db_host),
                ("GATEWAY_DB_PORT", gateway_db_port),
                ("GATEWAY_DB_USER", gateway_db_user),
                ("GATEWAY_DB_PASSWORD", gateway_db_password),
                ("GATEWAY_DB_NAME", gateway_db_name),
                ("JIUWENCLAW_ID", jiuwenclaw_id),
            ):
                if value is not None:
                    base[key] = value
            if api_key:
                base.update(
                    {
                        "MODEL_PROVIDER": model_provider or "",
                        "MODEL_NAME": model_name or "",
                        "API_BASE": api_base or "",
                        "API_KEY": api_key,
                    }
                )
            ssl_verify = os.getenv("LLM_SSL_VERIFY")
            if ssl_verify is not None:
                base["LLM_SSL_VERIFY"] = ssl_verify
            home = os.getenv("AGENT_SERVER_HOME")
            if home:
                base["HOME"] = home
            pythonpath = os.getenv("PYTHONPATH")
            if pythonpath:
                base["PYTHONPATH"] = pythonpath
            log_file = os.getenv("AGENT_SERVER_LOG_FILE")
            if log_file:
                base["AGENT_SERVER_LOG_FILE"] = log_file
            return base

        class _Factory(IServiceInstanceFactory):
            async def new_service(
                    self, response_parser: IResponseParser, service_template: Optional[dict[str, Any]] = None
            ) -> IServiceHandler:
                cfg = service_template or {}

                if deploy_mode == "process":
                    process_host = "127.0.0.1"
                    target_port = _pick_free_port(process_host)
                    target_container = None
                    launcher_script = os.getenv("AGENT_SERVER_LAUNCHER_SCRIPT") or None
                    process_handler = ProcessServiceHandler(
                        host=process_host,
                        publish_port=target_port,
                        env_vars=_agent_env_vars(),
                        launcher_script=launcher_script,
                        ready_timeout=float(
                            cfg["ready_timeout"] if "ready_timeout" in cfg else ready_timeout
                        ),
                        ready_poll_interval=float(
                            cfg["ready_poll_interval"]
                            if "ready_poll_interval" in cfg
                            else ready_poll_interval
                        ),
                    )
                    deploy_controller: Any = ProcessDeployController(process_handler)
                else:
                    agent_container_env = _agent_env_vars()
                    if jiuwenclaw_sandbox_enabled:
                        agent_container_env.update(
                            {
                                "JIUWENCLAW_SANDBOX_ENABLED": str(
                                    jiuwenclaw_sandbox_enabled
                                ).lower(),
                                "JIUWENCLAW_SANDBOX_URL": jiuwenclaw_sandbox_url,
                                "JIUWENCLAW_SANDBOX_TYPE": "jiuwenbox",
                                "JIUWENCLAW_SANDBOX_STARTUP_MODE": "external",
                                "JIUWENCLAW_SANDBOX_PRESERVE_FILE_SHARING_MODE": "mount",
                                "JIUWENCLAW_SANDBOX_EXCLUDED_COMMANDS": jiuwenclaw_sandbox_excluded_commands,
                                "JIUWENCLAW_SANDBOX_IDLE_TTL_SECONDS": jiuwenclaw_sandbox_idle_ttl_seconds,
                                "JIUWENCLAW_SANDBOX_IDLE_CHECK_INTERVAL": jiuwenclaw_sandbox_idle_check_interval,
                            }
                        )
                    agent_server_container = ContainerSpec(
                        name=cfg["container_name"] if "container_name" in cfg else container_name,
                        image=cfg["agent_image"] if "agent_image" in cfg else agent_image,
                        port_name=cfg["port_name"] if "port_name" in cfg else port_name,
                        port=int(cfg["container_port"]) if "container_port" in cfg else container_port,
                        image_pull_policy=cfg["image_pull_policy"] if "image_pull_policy" in cfg else image_pull_policy,
                        env_vars=agent_container_env,
                    )
                    target_container = agent_server_container.name
                    containers = [agent_server_container]
                    if jiuwenclaw_sandbox_enabled:
                        jiuwenbox_container = ContainerSpec(
                            name=jiuwenclaw_sandbox_container_name,
                            image=jiuwenclaw_sandbox_image,
                            port=jiuwenclaw_sandbox_port,
                            image_pull_policy=cfg["image_pull_policy"]
                            if "image_pull_policy" in cfg
                            else image_pull_policy,
                            env_vars={
                                "JIUWENBOX_LISTEN": jiuwenbox_listen,
                                "JIUWENBOX_POLICY_PATH": jiuwenbox_policy_path,
                            },
                            capabilities_add=["SYS_ADMIN", "NET_ADMIN"],
                            seccomp_unconfined=True,
                            apparmor_unconfined=True,
                            proc_mount_unmasked=True,
                            allow_privilege_escalation=True,
                            privileged=True,
                            host_path_mounts=jiuwenbox_host_mounts,
                        )
                        containers.append(jiuwenbox_container)
                    target_port = int(cfg["container_port"]) if "container_port" in cfg else container_port
                    k8s = K8sServiceHandler(
                        containers=containers,
                        name_prefix="jiuwenclaw",
                        namespace=cfg["namespace"] if "namespace" in cfg else namespace,
                        pod_name=cfg["pod_name"] if "pod_name" in cfg else container_name,
                        extra_labels=dict(RuntimeManagementAgentClient._POD_LABEL),
                        kubeconfig=kubeconfig,
                        readiness_initial_delay=(int(cfg["readiness_initial_delay"])
                                                 if "readiness_initial_delay" in cfg else readiness_initial_delay),
                        readiness_period=(int(cfg["readiness_period"])
                                          if "readiness_period" in cfg else readiness_period),
                        ready_timeout=(int(cfg["ready_timeout"])
                                       if "ready_timeout" in cfg else ready_timeout),
                        ready_poll_interval=(int(cfg["ready_poll_interval"])
                                             if "ready_poll_interval" in cfg else ready_poll_interval),
                        nfs_server=cfg.get("nfs_server") if "nfs_server" in cfg else nfs_server,
                        nfs_path=cfg.get("nfs_path") if "nfs_path" in cfg else nfs_path,
                        nfs_mount_path=cfg.get("nfs_mount_path") if "nfs_mount_path" in cfg else nfs_mount_path,
                        host_path=cfg.get("host_path") if "host_path" in cfg else host_path,
                        host_mount_path=cfg.get("host_mount_path") if "host_mount_path" in cfg else host_mount_path,
                        mode=cfg.get("mode") if "mode" in cfg else mode,
                        node_name=cfg.get("node_name") if "node_name" in cfg else node_name,
                        cpu_request=cfg.get("cpu_request") if "cpu_request" in cfg else cpu_request,
                        memory_request=cfg.get("memory_request") if "memory_request" in cfg else memory_request,
                        cpu_limit=cfg.get("cpu_limit") if "cpu_limit" in cfg else cpu_limit,
                        memory_limit=cfg.get("memory_limit") if "memory_limit" in cfg else memory_limit,
                    )
                    deploy_controller = K8sDeployController(k8s)

                ch = WSServiceMessageChannel(
                    target_port=target_port,
                    target_container=target_container,
                    invoke_path="",
                    ws_use_tls=False,
                )

                # 从 service_template 中提取 service_id，如果存在则使用，否则让 ServiceHandler 自动生成 UUID
                handler_service_id = cfg.get("service_id") if "service_id" in cfg else None

                return ServiceHandler(
                    service_id=handler_service_id,
                    total_concurrency=int(cfg["service_concurrency"]) if "service_concurrency" in cfg else service_concurrency,
                    message_channel=ch,
                    response_parser=response_parser,
                    deploy_controller=deploy_controller,
                    service_template=service_template,
                )

        dual_q: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(1000, 100)
        factory = _Factory()

        def create_service_manager() -> ServiceManager:
            return ServiceManager(
                service_factory=factory,
                dual_queue=dual_q,
                timer=Timer(),
                service_concurrency=service_concurrency,
                min_idle_services=min_idle_services,
                max_services=max_services,
                autoscale_interval=autoscale_interval,
                service_idle_ttl=service_ttl,
            )

        self._access: Any = Access(create_service_manager)
        self._connected = False

    @property
    def server_ready(self) -> bool:
        """WebChannel on_connect 依赖此标志发送 connection.ack。"""
        return self._connected

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """触发热更新配置。"""
        # 判断 config 中 service_template 是否为 true，否则不执行更新
        if not config.get("service_template"):
            logger.debug(
                "[RuntimeManagementAgentClient] skip config update: service_template is not true"
            )
            return

        if not self._connected or not hasattr(self, "_access"):
            logger.warning("[RuntimeManagementAgentClient] not connected, skip config update")
            return

        try:
            loop = asyncio.get_running_loop()
            # 直接调用 update_config 并创建任务（不等待完成）
            loop.create_task(
                self._access.update_config(),
                name="runtime-mgmt-config-update"
            )
            logger.info("[RuntimeManagementAgentClient] config update triggered")
        except RuntimeError:
            logger.warning(
                "[RuntimeManagementAgentClient] no running event loop, config update skipped"
            )
        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] failed to trigger config update: %s", exc)

    async def connect(self, uri: str) -> None:
        """建立连接并初始化 Access。"""
        if self._connected:
            return

        agent_image = os.getenv("AGENT_SERVER_IMAGE")
        service_concurrency = int(os.getenv("AGENT_SERVER_SERVICE_CONCURRENCY"))
        min_idle_services = int(os.getenv("AGENT_SERVER_MIN_IDLE_SERVICES"))
        max_services = int(os.getenv("AGENT_SERVER_MAX_SERVICES"))
        target_port = int(os.getenv("AGENT_SERVER_PORT"))
        service_ttl = int(os.getenv("AGENT_SERVER_SERVICE_TTL"))
        message_timeout = int(os.getenv("AGENT_SERVER_MESSAGE_TIMEOUT"))
        autoscale_interval = float(os.getenv("AGENT_SERVER_AUTOSCALE_INTERVAL"))
        session_concurrency = int(os.getenv("AGENT_SERVER_SESSION_CONCURRENCY"))
        session_ttl = int(os.getenv("AGENT_SERVER_SESSION_TTL"))

        acc_cfg = AccessConfig(
            image=agent_image,
            service_concurrency=service_concurrency,
            min_idle_services=min_idle_services,
            max_services=max_services,
            target_port=target_port,
            invoke_path="",
            ws_use_tls=False,
            service_ttl=service_ttl,
            message_timeout=message_timeout,
            autoscale_interval=autoscale_interval,
        )

        session_cfg = SessionConfig(
            concurrency=session_concurrency,
            ttl=session_ttl,
        )

        await self._access.init(
            response_parser=E2aEnvelopResponseParser(),
            config=acc_cfg,
            session_config=session_cfg,
        )
        self._connected = True

    async def cleanup_all_pods(self) -> None:
        """主备切换时调用：清理所有 AgentServer Pod。"""
        try:
            await self._access.cleanup_all_pods(
                namespace=self._namespace,
                kubeconfig=self._kubeconfig,
                label_selector=self._POD_LABEL_SELECTOR,
            )
        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] cleanup_all_pods failed: %s", exc)

    async def disconnect(self) -> None:
        """断开连接并清理资源。"""
        try:
            if self._access and hasattr(self._access, "shutdown"):
                await self._access.shutdown()
        except Exception as exc:
            logger.warning("[RuntimeManagementAgentClient] disconnect error: %s", exc)
        finally:
            self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("client not connected")

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        """发送非流式请求。"""
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 加载服务配置
        service_template = None
        loaded = await load_effective_service_config_for_request(request)
        if loaded is not None:
            # 从 loaded 中获取 service_id 和 agent_id
            service_id = getattr(loaded, "service_id", None)
            agent_id = getattr(loaded, "agent_id", None)
            logger.info(
                "[RuntimeManagementAgentClient] loaded config: service_id=%s agent_id=%s",
                service_id,
                agent_id,
            )

            entities = loaded.service_config or []
            if entities:
                service_template = entities[0]
                logger.info(
                    "[RuntimeManagementAgentClient] service_template keys: %s",
                    list(service_template.keys()) if isinstance(service_template, dict) else None,
                )

            # 将扩展配置附加到 envelope.channel_context
            ext_config = getattr(loaded, "extension_config", None)
            if ext_config:
                envelope.channel_context = envelope.channel_context or {}
                envelope.channel_context["extension_config"] = ext_config
                logger.info(
                    "[RuntimeManagementAgentClient] extension_config attached: %s",
                    ext_config,
                )

            # 确保 service_template 是字典，并合并 service_id 和 agent_id
            if service_template is None:
                service_template = {}

            if service_id:
                request.service_id = service_id
                service_template["service_id"] = service_id

            if agent_id:
                request.agent_id = agent_id
                service_template["agent_id"] = agent_id

        session_request = _SessionRequest(request, envelope, service_template=service_template)

        try:
            async for chunk in self._access.send_message(session_request):
                return chunk
        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request failed: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata={},
            )

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        """发送流式请求。"""
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 加载服务配置
        service_template = None
        loaded = await load_effective_service_config_for_request(request)
        if loaded is not None:
            # 从 loaded 中获取 service_id 和 agent_id
            service_id = getattr(loaded, "service_id", None)
            agent_id = getattr(loaded, "agent_id", None)
            logger.info(
                "[RuntimeManagementAgentClient] loaded config: service_id=%s agent_id=%s",
                service_id,
                agent_id,
            )

            entities = loaded.service_config or []
            if entities:
                service_template = entities[0]
                logger.info(
                    "[RuntimeManagementAgentClient] service_template keys: %s",
                    list(service_template.keys()) if isinstance(service_template, dict) else None,
                )

            # 将扩展配置附加到 envelope.channel_context
            ext_config = getattr(loaded, "extension_config", None)
            if ext_config:
                envelope.channel_context = envelope.channel_context or {}
                envelope.channel_context["extension_config"] = ext_config
                logger.info(
                    "[RuntimeManagementAgentClient] extension_config attached: %s",
                    ext_config,
                )

            # 确保 service_template 是字典，并合并 service_id 和 agent_id
            if service_template is None:
                service_template = {}

            if service_id:
                request.service_id = service_id
                service_template["service_id"] = service_id

            if agent_id:
                request.agent_id = agent_id
                service_template["agent_id"] = agent_id

        session_request = _SessionRequest(request, envelope, service_template=service_template)

        try:
            async for chunk in self._access.send_message(session_request):
                yield chunk
        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request_stream failed: %s", exc)
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"error": str(exc)},
                is_complete=True,
            )
