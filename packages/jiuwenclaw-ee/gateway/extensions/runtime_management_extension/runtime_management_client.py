# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import uuid as uuid_mod
import json
import logging
import os
import socket
import threading
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
from jiuwenclaw.gateway.session_map import load_session_map_scope
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
_session_id_lock = threading.Lock()
_session_id_to_service_pair: dict[str, tuple[str, str | None]] = {}


def _ensure_enterprise_config_loader() -> tuple[Any, Any, Any]:
    global _load_effective_enterprise_config, _service_config_slot, _extension_config_slot
    if (
        _load_effective_enterprise_config is not None
        and _service_config_slot is not None
        and _extension_config_slot is not None
    ):
        return _load_effective_enterprise_config, _service_config_slot, _extension_config_slot

    from jiuwenclaw.infrastructure.module_importer import (
        import_manager_ws_client_module,
    )

    loader_mod = import_manager_ws_client_module("core.enterprise_config.loader")
    schemas_mod = import_manager_ws_client_module("core.enterprise_config.schemas")
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


async def load_all_service_configs() -> list[dict[str, Any]]:
    """查询当前 ``jiuwenclaw_id`` 下全量 ``service_config_template``（enabled=True）。"""
    try:
        from jiuwenclaw.infrastructure.module_importer import (
            import_manager_ws_client_module,
        )

        gateway_db_mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
        jiuwenclaw_id = os.getenv("JIUWENCLAW_ID", "").strip() or None
        if not jiuwenclaw_id:
            logger.warning(
                "[RuntimeManagementAgentClient] load_all_service_configs skipped: "
                "jiuwenclaw_id not set"
            )
            return []
        db = gateway_db_mod.GatewayDb.bind(jiuwenclaw_id)
        rows = await db.list_records("service_config_template", filters={"enabled": True})
        logger.info(
            "[RuntimeManagementAgentClient] load_all_service_configs: "
            "jiuwenclaw_id=%s count=%s",
            jiuwenclaw_id,
            len(rows),
        )
        for row in rows:
            logger.info("[RuntimeManagementAgentClient] service_config: %s", row)
        return rows
    except Exception as exc:
        logger.warning("[RuntimeManagementAgentClient] load_all_service_configs failed: %s", exc)
        return []


def _resolve_invoke_ids_from_request(msg: AgentRequest) -> tuple[str, str | None]:
    """Resolve invoke ids, fallback to ``session_id`` mapping when service_id is missing."""
    svc = str(msg.service_id or "").strip()
    ag = str(msg.agent_id or "").strip()
    if svc:
        return svc, ag if ag else None

    sid = str(msg.session_id or "").strip()
    if sid:
        fallback_svc, fallback_ag = _session_id_to_invoke_ids(sid)
        if not ag and fallback_ag:
            ag = fallback_ag
        logger.info(
            "[RuntimeManagementAgentClient] service_id missing, fallback from session_id: session_id=%s service_id=%s",
            sid,
            fallback_svc,
        )
        return fallback_svc, ag if ag else None

    raise ValueError(
        "RuntimeManagementAgentClient requires AgentRequest.service_id "
        "(set gateway-side, e.g. SessionMap path)"
    )


def _md5_chat_bot_id(chat_id: str, bot_id: str) -> str:
    return hashlib.md5("::".join((chat_id, bot_id)).encode("utf-8")).hexdigest()


def _session_id_to_invoke_ids(session_id: str) -> tuple[str, str | None]:
    """Map gateway session_id into invoke ``(service_id, agent_id)`` pair."""
    with _session_id_lock:
        cached = _session_id_to_service_pair.get(session_id)
        if cached:
            return cached

    _ = load_session_map_scope()
    parts = session_id.split("::")
    if len(parts) == 6:
        _provider, chat_id, bot_id, user_id, _ts, _suffix = parts
        pair = (_md5_chat_bot_id(chat_id, bot_id), user_id)
    elif len(parts) == 5:
        _provider, chat_id, bot_id, _ts, _suffix = parts
        pair = (_md5_chat_bot_id(chat_id, bot_id), None)
    else:
        pair = (hashlib.md5(session_id.encode("utf-8")).hexdigest(), None)

    with _session_id_lock:
        _session_id_to_service_pair.setdefault(session_id, pair)
        return _session_id_to_service_pair[session_id]


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
        return int(os.getenv("AGENT_SERVER_SESSION_CONCURRENCY", "3"))

    @property
    def session_ttl(self) -> int:
        return int(os.getenv("AGENT_SERVER_SESSION_TTL", "60"))

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

    POD_LABEL_KEY = "jiuwenclaw-component"
    POD_LABEL_VALUE = "agentserver"
    POD_LABEL = {POD_LABEL_KEY: POD_LABEL_VALUE}
    GATEWAY_ID_LABEL_KEY = "jiuwenclaw-gateway-id"
    POD_LABEL_SELECTOR = f"{POD_LABEL_KEY}={POD_LABEL_VALUE}"

    def __init__(self) -> None:
        """初始化 Runtime Management 客户端。"""
        self.gateway_id = uuid_mod.uuid4().hex[:8]
        self.namespace = os.getenv("NAMESPACE")
        self.kubeconfig = os.getenv("AGENT_SERVER_KUBECONFIG") or None

        agent_image = os.getenv("AGENT_SERVER_IMAGE")
        agent_runtime = os.getenv("AGENT_RUNTIME")
        agent_cpu_request = os.getenv("AGENT_SERVER_CPU_REQUEST")
        agent_memory_request = os.getenv("AGENT_SERVER_MEMORY_REQUEST")
        agent_cpu_limit = os.getenv("AGENT_SERVER_CPU_LIMIT")
        agent_memory_limit = os.getenv("AGENT_SERVER_MEMORY_LIMIT")
        agent_readiness_initial_delay = int(os.getenv("AGENT_SERVER_READINESS_INITIAL_DELAY", "10"))
        agent_readiness_period = int(os.getenv("AGENT_SERVER_READINESS_PERIOD", "5"))
        agent_custom_envs = os.getenv("AGENT_SERVER_CUSTOM_ENVS")

        container_name = os.getenv("AGENT_SERVER_CONTAINER_NAME", "agentserver")
        container_port = int(os.getenv("AGENT_SERVER_PORT", "8080"))
        port_name = os.getenv("AGENT_SERVER_PORT_NAME", "http")
        image_pull_policy = os.getenv("AGENT_SERVER_IMAGE_PULL_POLICY", "IfNotPresent")
        min_idle_services = int(os.getenv("AGENT_SERVER_MIN_IDLE_SERVICES", "1"))
        max_services = int(os.getenv("AGENT_SERVER_MAX_SERVICES", "20"))
        service_concurrency = int(os.getenv("AGENT_SERVER_SERVICE_CONCURRENCY", "30"))
        service_ttl = int(os.getenv("AGENT_SERVER_SERVICE_TTL", "180"))
        autoscale_interval = float(os.getenv("AGENT_SERVER_AUTOSCALE_INTERVAL", "5"))
        nfs_server = os.getenv("AGENT_SERVER_NFS_SERVER", "")
        nfs_path = os.getenv("AGENT_SERVER_NFS_PATH", "/")
        nfs_mount_path = os.getenv("AGENT_SERVER_NFS_MOUNT_PATH")
        host_path = os.getenv("AGENT_SERVER_HOST_PATH")
        host_mount_path = os.getenv("AGENT_SERVER_HOST_MOUNT_PATH")

        agent_host_mounts: list[HostPathMount] = []
        # 只有当 host_path 和 host_mount_path 都配置时，才添加挂载
        if host_path and host_mount_path:
            agent_host_mounts.append(
                HostPathMount(
                    host_path=host_path,
                    mount_path=host_mount_path,
                    read_only=False,
                    host_path_type="Directory"
                )
            )

        mode = os.getenv("MODE")
        node_name = os.getenv("NODE_NAME")
        ready_timeout = int(os.getenv("AGENT_SERVER_READY_TIMEOUT", "300"))
        ready_poll_interval = int(os.getenv("AGENT_SERVER_READY_POLL_INTERVAL", "5"))

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

        deploy_mode = (os.getenv("AGENT_SERVER_DEPLOY_MODE") or "k8s").strip().lower()
        self._deploy_mode = deploy_mode

        # jiuwenbox sidecar: agentserver 与 jiuwenbox 同 Pod，使用 127.0.0.1 访问。
        jiuwenbox_enabled = _env_bool("JIUWENBOX_ENABLED", False)
        jiuwenbox_image = os.getenv("JIUWENBOX_IMAGE", "jiuwenbox:latest")
        jiuwenbox_port = int(os.getenv("JIUWENBOX_PORT", "8321"))
        jiuwenbox_cpu_request = os.getenv("JIUWENBOX_CPU_REQUEST")
        jiuwenbox_memory_request = os.getenv("JIUWENBOX_MEMORY_REQUEST")
        jiuwenbox_cpu_limit = os.getenv("JIUWENBOX_CPU_LIMIT")
        jiuwenbox_memory_limit = os.getenv("JIUWENBOX_MEMORY_LIMIT")
        jiuwenbox_container_name = os.getenv("JIUWENBOX_CONTAINER_NAME", "jiuwenbox" )
        jiuwenbox_url = os.getenv("JIUWENBOX_URL",f"http://127.0.0.1:{jiuwenbox_port}")
        jiuwenbox_readiness_initial_delay = int(os.getenv("JIUWENBOX_READINESS_INITIAL_DELAY", "10"))
        jiuwenbox_readiness_period = int(os.getenv("JIUWENBOX_READINESS_PERIOD", "5"))
        jiuwenbox_excluded_commands = os.getenv("JIUWENBOX_EXCLUDED_COMMANDS", "")
        jiuwenbox_idle_ttl_seconds = os.getenv("JIUWENBOX_IDLE_TTL_SECONDS", "")
        jiuwenbox_idle_check_interval = os.getenv("JIUWENBOX_IDLE_CHECK_INTERVAL", "")
        jiuwenbox_listen = os.getenv("JIUWENBOX_LISTEN", f"tcp://0.0.0.0:{jiuwenbox_port}")
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
                ("JIUWENCLAW_ID", os.getenv("JIUWENCLAW_ID")),
                ("LLM_SSL_VERIFY", os.getenv("LLM_SSL_VERIFY")),
                ("HOME", os.getenv("AGENT_SERVER_HOME")),
                ("PYTHONPATH", os.getenv("PYTHONPATH")),
                ("AGENT_SERVER_LOG_FILE", os.getenv("AGENT_SERVER_LOG_FILE")),
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

            if mode == "dev":
                base["LOG_ROOT_PATH"] = "/root/.logs"
            else:
                base["LOG_ROOT_PATH"] = "/home/app/.logs"

            try:
                custom_env_dict = json.loads(agent_custom_envs)
                if isinstance(custom_env_dict, dict):
                    base.update(custom_env_dict)
                else:
                    # JSON不是字典类型，打印警告
                    logger.warning(
                        f"AGENT_SERVER_CUSTOM_ENVS 解析成功但非字典类型，类型：{type(custom_env_dict)}，内容忽略"
                    )
            except json.JSONDecodeError as e:
                # JSON格式非法时静默跳过，不阻断启动
                # JSON格式非法，打印详细错误+原始文本，方便排障
                logger.warning(
                    f"解析 AGENT_SERVER_CUSTOM_ENVS JSON 格式失败，错误信息：{str(e)}，原始配置字符串：{agent_custom_envs}"
            )
                pass
            return base

        _client = self  # 捕获外层 RuntimeManagementAgentClient 实例，供内部类使用

        class \
                _Factory(IServiceInstanceFactory):
            async def new_service(
                    self, response_parser: IResponseParser, service_template: Optional[dict[str, Any]] = None
            ) -> IServiceHandler:
                cfg = service_template or {}

                # 如果配置了 template，打印关键字段
                if service_template:
                    logger.info(
                        "[RuntimeManagementAgentClient] new_service called with service_template, "
                        "template_id=%s, service_id=%s, agent_id=%s",
                        service_template.get("template_id"),
                        service_template.get("service_id"),
                        service_template.get("agent_id"),
                    )

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
                    if jiuwenbox_enabled:
                        agent_container_env.update(
                            {
                                "JIUWENCLAW_SANDBOX_ENABLED": str(
                                    jiuwenbox_enabled
                                ).lower(),
                                "JIUWENCLAW_SANDBOX_URL": jiuwenbox_url,
                                "JIUWENCLAW_SANDBOX_TYPE": "jiuwenbox",
                                "JIUWENCLAW_SANDBOX_STARTUP_MODE": "external",
                                "JIUWENCLAW_SANDBOX_PRESERVE_FILE_SHARING_MODE": "mount",
                                "JIUWENCLAW_SANDBOX_EXCLUDED_COMMANDS": jiuwenbox_excluded_commands,
                                "JIUWENCLAW_SANDBOX_IDLE_TTL_SECONDS": jiuwenbox_idle_ttl_seconds,
                                "JIUWENCLAW_SANDBOX_IDLE_CHECK_INTERVAL": jiuwenbox_idle_check_interval,
                            }
                        )

                    agent_server_container = ContainerSpec(
                        name=cfg.get("container_name") or container_name,
                        image=cfg.get("agent_image") or agent_image,
                        port_name=cfg.get("port_name") or port_name,
                        port=int(cfg["container_port"]) if cfg.get("container_port") is not None else container_port,
                        image_pull_policy=cfg.get("image_pull_policy") or image_pull_policy,
                        env_vars=agent_container_env,
                        host_path_mounts=agent_host_mounts,
                        allow_privilege_escalation=True if mode == "dev" else None,
                        run_as_non_root=False if mode == "dev" else None,
                        run_as_user=0 if mode == "dev" else None,
                        run_as_group=0 if mode == "dev" else None,
                        cpu_request=cfg.get("agent_cpu_request") or agent_cpu_request,
                        memory_request=cfg.get("agent_memory_request") or agent_memory_request,
                        cpu_limit=cfg.get("agent_cpu_limit") or agent_cpu_limit,
                        memory_limit=cfg.get("agent_memory_limit") or agent_memory_limit,
                        readiness_probe_type="websockets",
                        readiness_initial_delay=agent_readiness_initial_delay,
                        readiness_period=agent_readiness_period,
                    )
                    target_container = agent_server_container.name
                    containers = [agent_server_container]
                    if jiuwenbox_enabled:
                        jiuwenbox_container = ContainerSpec(
                            name=jiuwenbox_container_name,
                            image=jiuwenbox_image,
                            port=jiuwenbox_port,
                            image_pull_policy=cfg.get("image_pull_policy") or image_pull_policy,
                            env_vars={
                                "JIUWENBOX_LISTEN": jiuwenbox_listen,
                                "JIUWENBOX_POLICY_PATH": jiuwenbox_policy_path,
                            },
                            capabilities_add=["SYS_ADMIN", "NET_ADMIN"],
                            seccomp_unconfined=True,
                            apparmor_unconfined=True,
                            allow_privilege_escalation=True,
                            privileged=True,
                            host_path_mounts=jiuwenbox_host_mounts,
                            cpu_request=jiuwenbox_cpu_request,
                            memory_request=jiuwenbox_memory_request,
                            cpu_limit=jiuwenbox_cpu_limit,
                            memory_limit=jiuwenbox_memory_limit,
                            readiness_probe_type="tcp",
                            readiness_initial_delay=jiuwenbox_readiness_initial_delay,
                            readiness_period=jiuwenbox_readiness_period,
                        )
                        containers.append(jiuwenbox_container)
                    target_port = (
                        int(cfg["container_port"])
                        if cfg.get("container_port") is not None
                        else container_port
                    )
                    k8s = K8sServiceHandler(
                        containers=containers,
                        name_prefix="jiuwenclaw",
                        namespace=_client.namespace,
                        pod_name=cfg.get("pod_name") or container_name,
                        extra_labels={
                            **RuntimeManagementAgentClient.POD_LABEL,
                            RuntimeManagementAgentClient.GATEWAY_ID_LABEL_KEY: _client.gateway_id,
                        },
                        kubeconfig=cfg.get("kubeconfig") or _client.kubeconfig,
                        ready_timeout=(int(cfg["ready_timeout"])
                                       if cfg.get("ready_timeout") is not None else ready_timeout),
                        ready_poll_interval=(int(cfg["ready_poll_interval"])
                                             if cfg.get("ready_poll_interval") is not None else ready_poll_interval),
                        nfs_server=cfg.get("nfs_server") or nfs_server,
                        nfs_path=cfg.get("nfs_path") or nfs_path,
                        nfs_mount_path=cfg.get("nfs_mount_path") or nfs_mount_path,
                        host_path=cfg.get("host_path") or host_path,
                        host_mount_path=cfg.get("host_mount_path") or host_mount_path,
                        mode=cfg.get("mode") or mode,
                        node_name=cfg.get("node_name") or node_name,
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
                    total_concurrency=(
                        int(cfg["service_concurrency"])
                        if cfg.get("service_concurrency") is not None
                        else service_concurrency
                    ),
                    message_channel=ch,
                    response_parser=response_parser,
                    deploy_controller=deploy_controller,
                    service_template=service_template,
                )

        dual_q: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(1000, 100)
        factory = _Factory()

        async def create_service_manager() -> ServiceManager:
            service_templates = await load_all_service_configs()
            # 每次创建 ServiceManager 时使用新的队列实例，避免热更新时队列被关闭导致后续请求失败
            new_dual_q = PriorityDualAsyncQueues(1000, 100)
            return ServiceManager(
                service_factory=factory,
                dual_queue=new_dual_q,
                timer=Timer(),
                service_concurrency=service_concurrency,
                min_idle_services=min_idle_services,
                max_services=max_services,
                autoscale_interval=autoscale_interval,
                service_idle_ttl=service_ttl,
                service_templates=service_templates,
                namespace=_client.namespace or "default",
                kubeconfig=_client.kubeconfig,
            )

        self._create_service_manager = create_service_manager
        self._access: Any = Access(create_service_manager)
        self._connected = False

    @property
    def server_ready(self) -> bool:
        """WebChannel on_connect 依赖此标志发送 connection.ack。"""
        return self._connected

    async def collect_pod_status(self, include_metrics: bool = False) -> dict[str, Any]:
        """采集当前 Gateway 创建的 AgentServer Pod 状态。"""
        namespace = self.namespace or "default"
        label_selector = (
            f"{self.POD_LABEL_SELECTOR},"
            f"{self.GATEWAY_ID_LABEL_KEY}={self.gateway_id}"
        )
        if self._deploy_mode != "k8s":
            return {
                "runtime_gateway_id": self.gateway_id,
                "namespace": namespace,
                "label_selector": label_selector,
                "deploy_mode": self._deploy_mode,
                "total": 0,
                "running": 0,
                "failed": 0,
                "pods": [],
            }
        pods = await K8sServiceHandler.monitor_pods_status(
            namespace=namespace,
            label_selector=label_selector,
            kubeconfig=self.kubeconfig,
            include_metrics=include_metrics,
        )
        failed_statuses = {
            "Terminating",
            "Failed",
            "Error",
            "Terminated",
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "ErrImagePull",
        }
        return {
            "runtime_gateway_id": self.gateway_id,
            "namespace": namespace,
            "label_selector": label_selector,
            "total": len(pods),
            "running": sum(1 for pod in pods if pod.status == "Running"),
            "failed": sum(1 for p in pods if p.status in failed_statuses),
            "pods": [
                {
                    "pod_name": pod.pod_name,
                    "namespace": pod.namespace,
                    "status": pod.status,
                    "phase": pod.phase,
                    "pod_ip": pod.pod_ip,
                    "host_ip": pod.host_ip,
                    "node_name": pod.node_name,
                    "reason": pod.reason,
                    "message": pod.message,
                    "restart_count": pod.restart_count,
                    "deletion_timestamp": pod.deletion_timestamp,
                    "cpu_usage_cores": pod.cpu_usage_cores,
                    "memory_usage_bytes": pod.memory_usage_bytes,
                }
                for pod in pods
            ],
        }

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """触发热更新配置。"""
        # 判断 config 中 enterprise_config_update 是否为 true，否则不执行更新
        if not config.get("enterprise_config_update"):
            logger.debug(
                "[RuntimeManagementAgentClient] skip config update: enterprise_config_update is not true"
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

        await self._init_access()

    async def _init_access(self) -> None:
        """初始化（或重新初始化）Access：读取环境变量构建配置，调用 Access.init。"""
        agent_image = os.getenv("AGENT_SERVER_IMAGE")
        service_concurrency = int(os.getenv("AGENT_SERVER_SERVICE_CONCURRENCY", "30"))
        min_idle_services = int(os.getenv("AGENT_SERVER_MIN_IDLE_SERVICES", "1"))
        max_services = int(os.getenv("AGENT_SERVER_MAX_SERVICES", "20"))
        target_port = int(os.getenv("AGENT_SERVER_PORT", "8080"))
        service_ttl = int(os.getenv("AGENT_SERVER_SERVICE_TTL", "180"))
        message_timeout = int(os.getenv("AGENT_SERVER_MESSAGE_TIMEOUT", "60"))
        autoscale_interval = float(os.getenv("AGENT_SERVER_AUTOSCALE_INTERVAL", "5"))
        session_concurrency = int(os.getenv("AGENT_SERVER_SESSION_CONCURRENCY", "3"))
        session_ttl = int(os.getenv("AGENT_SERVER_SESSION_TTL", "60"))

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

        service_configs = await load_all_service_configs()
        await self._access.init(
            response_parser=E2aEnvelopResponseParser(),
            config=acc_cfg,
            session_config=session_cfg,
            service_configs=service_configs or None,
        )
        self._connected = True

    async def reinit_access(self) -> None:
        """主备切换为 PRIMARY 时调用：shutdown 旧 Access 并创建新实例重新初始化。"""
        logger.info("[RuntimeManagementAgentClient] reinit_access: shutting down old Access")
        try:
            if self._access and hasattr(self._access, "shutdown"):
                await self._access.shutdown()
        except Exception as exc:
            logger.warning("[RuntimeManagementAgentClient] reinit_access shutdown error: %s", exc)
        self._connected = False
        self._access = Access(self._create_service_manager)
        await self._init_access()
        logger.info("[RuntimeManagementAgentClient] reinit_access: done")

    async def cleanup_all_pods(self) -> None:
        """主备切换时调用：清理其他 Gateway 遗留的 AgentServer Pod，保留自身创建的。"""
        try:
            label_selector = (
                f"{self.POD_LABEL_KEY}={self.POD_LABEL_VALUE}"
                f",{self.GATEWAY_ID_LABEL_KEY}!={self.gateway_id}"
            )
            logger.info(
                "[RuntimeManagementAgentClient] cleanup_all_pods: gateway_id=%s selector=%s",
                self.gateway_id, label_selector,
            )
            await self._access.cleanup_all_pods(
                namespace=self.namespace,
                kubeconfig=self.kubeconfig,
                label_selector=label_selector,
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
