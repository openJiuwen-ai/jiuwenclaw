# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import threading
import time
import warnings
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping

from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine
from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

from jiuwenclaw.agentserver.gateway_push import GatewayPushTransport, WebSocketGatewayPushTransport
from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.tools._deepresearch_tls import (
    TASK_MANAGER_TLS_ENV,
    iterate_with_scoped_tls_initialization,
)
from jiuwenclaw.agentserver.tools.deepresearch_plugin.report_bundle import build_report_bundle
from jiuwenclaw.local_env_config import (
    bind_agent_env_ns,
    bind_task_env_overlay,
    build_effective_env_overlay,
    get_task_env_overlay,
    read_default_headers_raw,
    get_local_config,
    reset_agent_env_ns,
    reset_task_env_overlay,
)
from jiuwenclaw.utils import get_logs_dir
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_effective_request_workspace_dir,
    set_effective_request_workspace_dir,
)

logger = logging.getLogger(__name__)
SAVE_REPORT_PATH = "workspace/reports"
INFERENCE_LINK_RE = re.compile(r"\[([^\]]+)\]\(#inference:(\d+)\)")


class TaskStatus(str, Enum):
    """任务状态枚举."""
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class DeepResearchTask:
    """DeepResearch 任务数据类."""
    task_id: str
    query: str
    file_name: str
    status: TaskStatus
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    result: str = ""
    error: str = ""
    session_id: str = ""
    channel_id: str = ""
    request_id: str = ""
    service_id: str = "default"
    agent_id: str = "default"
    env_snapshot: dict[str, str] = field(default_factory=dict)
    workspace_dir: str = ""
    # 协作取消事件
    cancel_event: threading.Event | None = None

    def __post_init__(self):
        """初始化取消事件."""
        if self.cancel_event is None:
            self.cancel_event = threading.Event()

    @staticmethod
    def format_timestamp(timestamp: float | None) -> str | None:
        """将时间戳转换为本地时区的格式化时间字符串（YYYY-MM-DD HH:MM:SS）."""
        if timestamp is None:
            return None
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DeepResearchTaskRequest:
    """Create/run DeepResearch task parameters (G.FNM.03)."""

    query: str
    file_name: str
    session_id: str = ""
    channel_id: str = ""
    request_id: str = ""
    service_id: str | None = None
    agent_id: str | None = None


class DeepResearchTaskManager:
    """DeepResearch 任务池管理器（按租户 ``(service_id, agent_id)`` 分桶）.

    管理本租户 DeepResearch 任务的创建、执行、状态查询、取消等功能。
    """

    # 资源上限配置（按租户实例计）
    MAX_ACTIVE_TASKS = 10  # 最大活跃任务数
    MAX_TOTAL_TASKS = 100  # 最大保留任务数（包括已完成）
    MAX_TASKS_PER_SESSION = 5  # 每个会话最大任务数
    MAX_RESPONSE_CONTENT_BYTES = 10 * 1024 * 1024  # 最大报告内容大小（10MB）
    MAX_INFER_MESSAGES = 20  # 最大推理图数量
    MAX_SINGLE_HTML_BASE64_BYTES = 5 * 1024 * 1024  # 单个推理图最大大小（5MB）

    # LogManager 会改类级全局状态；跨任务并发时需串行化整段 capture 生命周期
    _log_capture_async_lock: ClassVar[asyncio.Lock | None] = None

    @classmethod
    def _get_log_capture_lock(cls) -> asyncio.Lock:
        if cls._log_capture_async_lock is None:
            cls._log_capture_async_lock = asyncio.Lock()
        return cls._log_capture_async_lock

    # 节点显示名称与功能描述映射（用于进度推送）
    # 格式: (中文名称, 功能描述)
    NODE_DISPLAY_INFO: ClassVar[Dict[str, tuple[str, str]]] = {
        # 主图节点
        "start": ("系统就绪", "初始化研究任务上下文"),
        "intent_recognition": ("意图识别", "分析研究主题与报告类型策略"),
        "generate_questions": ("澄清问题", "生成澄清问题供用户确认研究方向"),
        "feedback_handler": ("处理反馈", "根据用户反馈调整研究意图"),
        "outline": ("生成大纲", "基于研究主题规划报告章节结构"),
        "outline_interaction": ("大纲确认", "等待用户审阅或修订大纲"),
        "editor_team": ("编排章节", "管理各章节的并行调研与撰写"),
        "reporter": ("汇聚报告", "合成所有章节为完整研究报告"),
        "vlm_chart_generator": ("图表生成", "在报告中插入数据分析图表"),
        "source_tracer": ("溯源校验", "验证整份报告的引用来源准确性"),
        "source_tracer_infer": ("推理图谱", "生成关键结论的可视化溯源推理图"),
        "user_feedback_processor": ("局部改写", "根据反馈对特定章节进行定向修改"),
        "end": ("流程完成", "研究报告生成完毕"),
        # 章节子图节点
        "plan_reasoning": ("规划调研", "为当前章节制定分步信息采集计划"),
        "info_collector": ("信息采集", "执行多源检索、抓取并评估文档质量"),
        "sub_reporter": ("撰写章节", "基于采集信息撰写当前章节内容"),
        "sub_source_tracer": ("引用标注", "为章节内容添加引文来源标注"),
        # 信息采集子图节点
        "collector_query_generation": ("生成搜索词", "根据调研步骤生成检索关键词"),
        "collector_info_retrieval": ("执行检索", "使用搜索引擎获取相关文档"),
        "collector_supervisor": ("评估信息", "判断已采集信息是否充分并补充检索"),
        "collector_summary": ("总结采集", "汇总采集结果并生成评估摘要"),
        # 搜索模式节点
        "search_info_collector": ("搜索采集", "执行快速搜索获取信息"),
        "search_plan_reasoning": ("搜索规划", "为搜索任务规划检索步骤"),
        # 框架层
        "framework": ("框架层", "框架级事件或异常信号"),
    }

    # 仅发单次 summary_response 的终态节点：首次收到 chunk 时推 start + 立即推 done
    # 这些节点不调用 custom_stream_output（不发 event=start/done），
    # 只通过 write_custom_stream 发一次 event=summary_response。
    # 从消费者角度，chunk 到达即表示节点已完成（尽管节点内部可能有 LLM 调用耗时）。
    INSTANT_COMPLETE_NODES: ClassVar[set[str]] = {"collector_summary"}

    def __init__(self, *, service_id: str = "default", agent_id: str = "default"):
        """初始化本租户任务池管理器."""
        self.service_id = (service_id or "default").strip() or "default"
        self.agent_id = (agent_id or "default").strip() or "default"
        self._tasks: Dict[str, DeepResearchTask] = {}
        self._task_handles: Dict[str, asyncio.Task] = {}
        self._task_semaphore = asyncio.Semaphore(DeepResearchTaskManager.MAX_ACTIVE_TASKS)
        self._gateway_push: GatewayPushTransport = WebSocketGatewayPushTransport()
        logger.info(
            "[DeepResearchTaskManager] 初始化完成 tenant=(%s, %s)",
            self.service_id,
            self.agent_id,
        )

    @staticmethod
    def _capture_env_snapshot(service_id: str, agent_id: str) -> dict[str, str]:
        """Snapshot effective tip (+ current task overlay) for background execution."""
        extra = get_task_env_overlay()
        merged = build_effective_env_overlay(
            extra,
            service_id=service_id,
            agent_id=agent_id,
        )
        return {str(k): str(v) for k, v in merged.items()}

    @staticmethod
    def _bind_task_runtime(task: DeepResearchTask) -> dict[str, Any]:
        """Bind tenant env + workspace for a background/blocking DR task."""
        tokens: dict[str, Any] = {
            "ns": bind_agent_env_ns(task.service_id, task.agent_id),
            "overlay": bind_task_env_overlay(task.env_snapshot or {}),
            "tenant": None,
        }
        workspace = (task.workspace_dir or "").strip()
        if workspace:
            set_effective_request_workspace_dir(workspace)
            try:
                from jiuwenclaw.agentserver.tenant_context import bind_tenant_workspace_dirs

                ws_path = Path(workspace)
                tokens["tenant"] = bind_tenant_workspace_dirs(
                    jiuwenclaw_workspace=workspace,
                    agent_root=str(ws_path.parent),
                    tenant_root=str(ws_path.parent.parent),
                )
            except Exception as exc:
                logger.warning(
                    "[DeepResearchTaskManager] bind tenant workspace failed task_id=%s: %s",
                    task.task_id,
                    exc,
                )
        return tokens

    @staticmethod
    def _reset_task_runtime(tokens: dict[str, Any]) -> None:
        tenant = tokens.get("tenant")
        if tenant is not None:
            try:
                from jiuwenclaw.agentserver.tenant_context import reset_tenant_workspace_dirs

                reset_tenant_workspace_dirs(tenant)
            except Exception:
                logger.debug(
                    "[DeepResearchTaskManager] reset_tenant_workspace_dirs failed",
                    exc_info=True,
                )
        overlay = tokens.get("overlay")
        if overlay is not None:
            reset_task_env_overlay(overlay)
        ns = tokens.get("ns")
        if ns is not None:
            reset_agent_env_ns(ns)

    @classmethod
    async def get_instance(cls) -> DeepResearchTaskManager:
        """Legacy: explicitly return default/default tenant manager."""
        warnings.warn(
            "DeepResearchTaskManager.get_instance() is deprecated; "
            "use get_deepresearch_manager(scope) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return await get_deepresearch_manager(RuntimeScopeKey.from_ids("default", "default"))

    @staticmethod
    def _read_config_value(
        name: str,
        default: str = "",
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Read an explicit snapshot, or the active tenant-aware configuration."""
        if env is not None:
            return str(env.get(name, default) or default)
        return str(get_local_config(name, default) or default)

    @staticmethod
    def _resolve_petal_search_url(env: Mapping[str, str] | None = None) -> str:
        """Build Petal Search URL from LLM API_BASE: strip trailing /v2, append /v1/ai-tools/web-search."""
        read = lambda name, default="": DeepResearchTaskManager._read_config_value(
            name, default, env
        )
        petal_api_url = read("PETAL_API_URL").strip()
        if petal_api_url:
            return petal_api_url
        api_base = (
            read("API_BASE")
            or read("OPENAI_BASE_URL")
            or read("OPENAI_API_BASE")
            or ""
        )
        if isinstance(api_base, str):
            api_base = api_base.strip()
        else:
            api_base = str(api_base or "").strip()
        if not api_base:
            return ""
        trimmed = api_base.rstrip("/")
        if trimmed.lower().endswith("/v2"):
            trimmed = trimmed[:-3]
        trimmed = trimmed.rstrip("/")
        return f"{trimmed}/v1/ai-tools/web-search"

    @staticmethod
    def _detect_configured_search_engines(env: Mapping[str, str] | None = None) -> Dict[str, str]:
        """自动识别环境变量中已配置的检索引擎.

        返回：
            Dict[str, str]: 引擎名字 -> API key 的映射，例如 {"jina": "sk-xxx", "bocha": "sk-yyy"}
        """
        read = lambda name, default="": DeepResearchTaskManager._read_config_value(
            name, default, env
        )
        configured_engines = {}

        # SerpAPI 搜索引擎
        serper_api_key = read("SERPER_API_KEY").strip()
        if serper_api_key:
            configured_engines[SearchEngine.SERPER.value] = serper_api_key

        # JINA 搜索引擎
        jina_api_key = read("JINA_API_KEY").strip()
        if jina_api_key:
            configured_engines[SearchEngine.JINA.value] = jina_api_key

        # 博查搜索引擎
        bocha_api_key = read("BOCHA_API_KEY").strip()
        if bocha_api_key:
            configured_engines[SearchEngine.BOCHA.value] = bocha_api_key

        # Perplexity 搜索引擎
        perplexity_api_key = read("PERPLEXITY_API_KEY").strip()
        if perplexity_api_key:
            configured_engines[SearchEngine.PERPLEXITY.value] = perplexity_api_key

        return configured_engines

    @staticmethod
    def _build_general_llm_configs(
        config: Dict[str, str],
        extension: dict,
    ) -> tuple[dict, dict]:
        """Build isolated LLM configs for the workflow and report styling."""

        def build_config(model_type: str) -> dict:
            return {
                "model_name": config["LLM_MODEL_NAME"],
                "model_type": model_type,
                "base_url": config["LLM_BASE_URL"],
                "extension": extension,
                "api_key": bytearray(config["LLM_API_KEY"], encoding="utf-8"),
            }

        workflow_model_type = config["LLM_MODEL_TYPE"]
        report_style_model_type = workflow_model_type.strip().lower()
        if report_style_model_type not in ("openai", "siliconflow"):
            report_style_model_type = "openai"

        return build_config(workflow_model_type), build_config(report_style_model_type)

    @staticmethod
    def _load_config(env: Mapping[str, str] | None = None) -> Dict[str, str]:
        """从环境变量加载 DeepSearch 配置.

        策略：
        - 大模型相关配置：先从 DeepSearch 专属环境变量获取，为空则 fallback 到项目全局环境变量
        - 其他配置：直接从 DeepSearch 专属环境变量获取

        环境变量映射（与 app_web_handlers.py _CONFIG_SET_ENV_MAP 保持一致）：
        - DeepSearch 专属：LLM_MODEL_NAME, LLM_MODEL_TYPE, LLM_BASE_URL, LLM_API_KEY,
          WEB_SEARCH_ENGINE_NAME, WEB_SEARCH_API_KEY, WEB_SEARCH_URL, EXECUTION_METHOD
        - 项目全局：MODEL_NAME, MODEL_PROVIDER, API_BASE, API_KEY
        """
        read = lambda name, default="": DeepResearchTaskManager._read_config_value(
            name, default, env
        )

        # 大模型相关配置：DeepSearch 专属优先，fallback 到项目全局
        llm_model_name = (
            read("LLM_MODEL_NAME").strip()
            or read("MODEL_NAME").strip()
        )
        llm_model_type = (
            read("LLM_MODEL_TYPE").strip().lower()
            or read("MODEL_PROVIDER").strip().lower()
        )
        llm_base_url = (
            read("LLM_BASE_URL").strip()
            or read("API_BASE").strip()
        )
        llm_api_key = (
            read("LLM_API_KEY").strip()
            or read("API_KEY").strip()
        )

        # 自动识别已配置的检索引擎
        configured_engines = DeepResearchTaskManager._detect_configured_search_engines(env)

        # 确定搜索引擎名称：WEB_SEARCH_ENGINE_NAME > 已配置搜索引擎 > petal
        web_search_engine_name = read("WEB_SEARCH_ENGINE_NAME").strip().lower()
        if not web_search_engine_name and configured_engines:
            # 使用第一个已配置的搜索引擎
            web_search_engine_name = next(iter(configured_engines.keys()))
        if not web_search_engine_name:
            web_search_engine_name = SearchEngine.PETAL.value

        # 确定搜索引擎 API Key：WEB_SEARCH_API_KEY > 已配置搜索引擎的 API Key > OPENAI_DEFAULT_HEADERS/default_headers
        web_search_api_key = read("WEB_SEARCH_API_KEY").strip()
        if not web_search_api_key and web_search_engine_name:
            web_search_api_key = read(f"{web_search_engine_name.upper()}_API_KEY").strip()
        if not web_search_api_key and configured_engines and web_search_engine_name in configured_engines:
            # 使用对应引擎的 API Key
            web_search_api_key = configured_engines[web_search_engine_name]
        if not web_search_api_key:
            for header_name in ("default_headers", "DEFAULT_HEADERS", "OPENAI_DEFAULT_HEADERS"):
                web_search_api_key = read(header_name).strip()
                if web_search_api_key:
                    break
        if not web_search_api_key and env is None:
            web_search_api_key = read_default_headers_raw()

        web_search_url = read("WEB_SEARCH_URL").strip()
        if not web_search_url and web_search_engine_name == SearchEngine.PETAL.value:
            web_search_url = DeepResearchTaskManager._resolve_petal_search_url(env)

        execution_method = read("EXECUTION_METHOD", "parallel").strip()

        # 检查 VISION 相关配置
        vision_api_key = read("VISION_API_KEY").strip()
        vision_api_base = read("VISION_API_BASE").strip()
        vision_provider = read("VISION_PROVIDER").strip().lower()
        vision_model_name = read("VISION_MODEL_NAME").strip()

        # 如果 VISION 相关环境变量已配置，启用 VLM 图表生成器
        vlm_chart_generator_enable = "False"
        has_valid_vision_config = all(
            [
                vision_api_key,
                vision_api_base,
                vision_provider,
                vision_model_name,
            ]
        )
        if has_valid_vision_config:
            vlm_chart_generator_enable = "True"
        config = {
            "LLM_MODEL_NAME": llm_model_name,
            "LLM_MODEL_TYPE": llm_model_type,
            "LLM_BASE_URL": llm_base_url,
            "LLM_API_KEY": llm_api_key,
            "WEB_SEARCH_ENGINE_NAME": web_search_engine_name,
            "WEB_SEARCH_API_KEY": web_search_api_key,
            "WEB_SEARCH_URL": web_search_url,
            "MAX_WEB_SEARCH_RESULTS": "5",
            "EXECUTION_METHOD": execution_method,
            "OUTLINER_MAX_SECTION_NUM": "5",
            "WORKFLOW_HUMAN_IN_THE_LOOP": "False",
            "OUTLINE_INTERACTION_ENABLED": "False",
            "SOURCE_TRACER_INFER_SWITCHES": "True",
            "VLM_CHART_GENERATOR_ENABLE": "False",
            "VLM_CHART_GENERATOR_MAX_ITERATIONS": 3,
            "VISION_API_KEY": vision_api_key,
            "VISION_API_URL": vision_api_base,
            "VISION_PROVIDER": vision_provider,
            "VISION_MODEL_NAME": vision_model_name,
        }
        return config

    @staticmethod
    def _validate_config(config: Dict[str, str]) -> tuple[bool, str]:
        """验证 DeepResearch 配置."""
        if not config["LLM_API_KEY"]:
            return False, (
                "DeepResearch 缺少 LLM_API_KEY。\n"
                "请在 ~/.jiuwenclaw/config/.env 中设置以下任一环境变量：\n"
                "  - LLM_API_KEY（DeepSearch 专属配置，优先使用）\n"
                "  - API_KEY（项目全局配置，作为 fallback）"
            )

        if not config["WEB_SEARCH_API_KEY"]:
            return False, (
                "DeepResearch 缺少 WEB_SEARCH_API_KEY。\n"
                "请在 ~/.jiuwenclaw/config/.env 中设置 WEB_SEARCH_API_KEY"
            )

        return True, "配置验证通过"

    @staticmethod
    def _validate_and_sanitize_filename(file_name: str) -> str:
        """校验并净化文件名，防止路径穿越.

        Args:
            file_name: 原始文件名

        Returns:
            安全的文件名组件

        Raises:
            ValueError: 如果文件名包含非法字符或路径穿越模式
        """
        # 移除已知后缀
        base_name = DeepResearchTaskManager._strip_known_suffix(file_name)

        # 检查空文件名
        if not base_name or not base_name.strip():
            raise ValueError("file_name 不能为空")

        # 检查路径分隔符和路径穿越模式
        dangerous_patterns = ['/', '\\', '..', '~', '\x00']
        for pattern in dangerous_patterns:
            if pattern in base_name:
                raise ValueError(f"file_name 包含非法字符或路径穿越模式: '{pattern}'")

        # 检查绝对路径（Windows 和 Unix）
        if base_name.startswith('/') or (len(base_name) > 1 and base_name[1] == ':'):
            raise ValueError("file_name 不能是绝对路径")

        # 只允许安全字符：字母、数字、下划线、短横线、中文、空格（替换为下划线）
        import re as filename_re
        safe_name = filename_re.sub(r'[^\w\u4e00-\u9fff\-]', '_', base_name)
        safe_name = filename_re.sub(r'_+', '_', safe_name).strip('_')

        if not safe_name:
            raise ValueError("file_name 经净化后为空，请使用有效的文件名")

        # 限制文件名长度
        max_length = 200
        if len(safe_name) > max_length:
            safe_name = safe_name[:max_length]

        return safe_name

    @staticmethod
    def _verify_path_containment(output_dir: str, target_path: str) -> bool:
        """校验目标路径是否在输出目录范围内.

        Args:
            output_dir: 输出目录路径
            target_path: 目标文件路径

        Returns:
            目标路径是否在输出目录内
        """
        try:
            output_dir_resolved = Path(output_dir).resolve()
            target_resolved = Path(target_path).resolve()
            # 检查目标路径是否在输出目录下
            return str(target_resolved).startswith(str(output_dir_resolved))
        except Exception:
            return False

    @staticmethod
    def _strip_known_suffix(file_name: str) -> str:
        """移除已知后缀，避免重复拼接扩展名."""
        base_name = file_name
        for suffix in [".md", ".html", ".docx", ".txt"]:
            if base_name.lower().endswith(suffix.lower()):
                return base_name[:-len(suffix)]
        return base_name

    @staticmethod
    def _collect_inference_html(infer_messages: Any) -> dict[str, str]:
        """解析 infer_messages，返回可写入文件的 HTML 映射（带资源边界检查）."""
        if not isinstance(infer_messages, list):
            return {}

        html_map: dict[str, str] = {}
        for item in infer_messages:
            # 检查推理图数量上限
            if len(html_map) >= DeepResearchTaskManager.MAX_INFER_MESSAGES:
                logger.warning(
                    "[DeepResearchTaskManager] 推理图数量达到上限 (%d)，跳过后续推理图",
                    DeepResearchTaskManager.MAX_INFER_MESSAGES,
                )
                break

            if not isinstance(item, dict):
                continue

            infer_id = str(item.get("id", "")).strip()
            html_base64 = item.get("html_base64", "")
            if not infer_id or not html_base64:
                continue

            # 检查单个推理图大小上限
            if len(html_base64) > DeepResearchTaskManager.MAX_SINGLE_HTML_BASE64_BYTES:
                logger.warning(
                    "[DeepResearchTaskManager] 推理图 base64 超过大小上限 (%d bytes)，跳过 infer_id=%s",
                    DeepResearchTaskManager.MAX_SINGLE_HTML_BASE64_BYTES,
                    infer_id,
                )
                continue

            try:
                html_content = base64.b64decode(html_base64).decode("utf-8")
            except Exception as exc:
                logger.warning(
                    "[DeepResearchTaskManager] Failed to decode inference html. infer_id=%s error=%s",
                    infer_id,
                    exc,
                )
                continue

            if html_content.strip():
                html_map[infer_id] = html_content
        return html_map

    @staticmethod
    def _sanitize_html(html_content: str) -> str:
        """净化HTML内容，移除危险元素和属性.

        Args:
            html_content: 原始HTML内容

        Returns:
            安全的HTML内容
        """
        import re as html_re

        # 移除 script 标签及其内容
        html_content = html_re.sub(
            r'<script[^>]*>.*?</script>',
            '',
            html_content,
            flags=html_re.IGNORECASE | html_re.DOTALL
        )

        # 移除危险标签: iframe, object, embed, applet, form
        dangerous_tags = ['iframe', 'object', 'embed', 'applet', 'form', 'meta', 'link', 'base']
        for tag in dangerous_tags:
            html_content = html_re.sub(
                rf'<{tag}[^>]*>.*?</{tag}>',
                '',
                html_content,
                flags=html_re.IGNORECASE | html_re.DOTALL
            )
            html_content = html_re.sub(
                rf'<{tag}[^>]*>',
                '',
                html_content,
                flags=html_re.IGNORECASE
            )

        html_content = html_re.sub(
            r'\s+on[a-z]+\s*=\s*["\'][^"\']*["\']',
            '',
            html_content,
            flags=html_re.IGNORECASE
        )
        html_content = html_re.sub(
            r'\s+on[a-z]+\s*=\s*[^\s>]+',
            '',
            html_content,
            flags=html_re.IGNORECASE
        )

        dangerous_schemes = ['javascript:', 'vbscript:', 'data:text/html']
        url_attrs_pattern = 'href|src|action|formaction|data|poster'
        for scheme in dangerous_schemes:
            # 带引号的 URL
            html_content = html_re.sub(
                rf'({url_attrs_pattern})\s*=\s*["\']\s*{scheme}[^"\']*["\']',
                '',
                html_content,
                flags=html_re.IGNORECASE
            )
            # 不带引号的 URL
            html_content = html_re.sub(
                rf'({url_attrs_pattern})\s*=\s*{scheme}[^\s>]*',
                '',
                html_content,
                flags=html_re.IGNORECASE
            )

        return html_content

    @staticmethod
    def _build_report_content(data: Any, report_file: str) -> tuple[str, str | None, str | None]:
        """根据 DeepResearch 结果构建最终落盘的报告内容（带资源边界检查）."""
        infer_dir = None
        chart_dir = None
        if isinstance(data, dict) and "response_content" in data:
            report_content = data.get("response_content", "")
            if report_content == "":
                raise ValueError("response_content is empty")

            # 检查报告内容大小上限
            data_for_bundle = data
            content_bytes = len(report_content.encode("utf-8"))
            if content_bytes > DeepResearchTaskManager.MAX_RESPONSE_CONTENT_BYTES:
                logger.warning(
                    "[DeepResearchTaskManager] 报告内容超过大小上限 (%d bytes > %d bytes)，将截断",
                    content_bytes,
                    DeepResearchTaskManager.MAX_RESPONSE_CONTENT_BYTES,
                )
                # 截断而不是拒绝，避免整个任务失败
                data_for_bundle = dict(data)
                data_for_bundle["response_content"] = report_content[
                    : DeepResearchTaskManager.MAX_RESPONSE_CONTENT_BYTES // 2
                ]

            bundle = build_report_bundle(
                data_for_bundle,
                report_file,
                max_infer_messages=DeepResearchTaskManager.MAX_INFER_MESSAGES,
                max_single_html_base64_bytes=DeepResearchTaskManager.MAX_SINGLE_HTML_BASE64_BYTES,
            )
            return bundle.markdown_text, bundle.infer_dir, bundle.chart_dir

        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False, indent=2), infer_dir, chart_dir
        return str(data), infer_dir, chart_dir

    @staticmethod
    async def _write_report_artifacts(
        data: Any,
        file_name: str,
        output_dir: str = SAVE_REPORT_PATH,
        *,
        task_id: str = "",
        cancel_event: threading.Event | None = None,
        llm_config: dict,
    ) -> dict[str, str]:
        """写出 Markdown/HTML 报告及推理图目录（支持协作取消）."""
        # 检查取消状态
        if cancel_event and cancel_event.is_set():
            logger.info("[DeepResearchTaskManager] 任务已取消，跳过报告写出 task_id=%s", task_id)
            return {}

        os.makedirs(output_dir, exist_ok=True)

        # 文件名安全校验和净化
        try:
            safe_base_name = DeepResearchTaskManager._validate_and_sanitize_filename(file_name)
        except ValueError as exc:
            logger.warning(
                "[DeepResearchTaskManager] 文件名校验失败，使用默认文件名。file_name=%s error=%s",
                file_name,
                exc,
            )
            safe_base_name = task_id or "default"

        report_file = os.path.join(
            output_dir,
            f"{safe_base_name}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        )

        # 路径 containment 校验
        if not DeepResearchTaskManager._verify_path_containment(output_dir, report_file):
            raise ValueError(f"生成的报告路径逃逸出输出目录: {report_file}")

        report_file_md = f"{report_file}.md"
        report_file_html = f"{report_file}.html"

        report_content, infer_dir, chart_dir = await asyncio.to_thread(
            DeepResearchTaskManager._build_report_content,
            data,
            report_file,
        )

        # 再次检查取消状态
        if cancel_event and cancel_event.is_set():
            logger.info("[DeepResearchTaskManager] 任务已取消，跳过报告文件写入 task_id=%s", task_id)
            return {}

        await asyncio.to_thread(
            Path(report_file_md).write_text,
            report_content,
            encoding="utf-8",
        )

        artifacts = {"md": report_file_md}
        if infer_dir:
            artifacts["infer_dir"] = infer_dir
        if chart_dir:
            artifacts["chart_dir"] = chart_dir

        # HTML转换前检查取消状态
        if cancel_event and cancel_event.is_set():
            logger.info("[DeepResearchTaskManager] 任务已取消，跳过HTML转换 task_id=%s", task_id)
            return artifacts

        html_infer_dir = f"{report_file}_infer"
        html_chart_dir = f"{report_file}_charts"
        html_export_started_at = time.monotonic()
        logger.info(
            "[DeepResearchTaskManager] Styled html report export started "
            "task_id=%s output=%s",
            task_id,
            report_file_html,
        )
        try:
            from jiuwenclaw.agentserver.tools.deepresearch_plugin.styled_html_export import (
                export_styled_html,
            )

            styled_result = await export_styled_html(
                data,
                llm_config,
                html_path=report_file_html,
            )
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] Styled html report generation failed. "
                "task_id=%s elapsed_seconds=%.3f output=%s error=%s: %s",
                task_id,
                time.monotonic() - html_export_started_at,
                report_file_html,
                type(exc).__name__,
                exc,
            )
        else:
            artifacts["html"] = report_file_html
            if Path(html_infer_dir).is_dir() and any(Path(html_infer_dir).iterdir()):
                artifacts["infer_dir"] = html_infer_dir.replace("\\", "/")
            if Path(html_chart_dir).is_dir() and any(Path(html_chart_dir).iterdir()):
                artifacts["chart_dir"] = html_chart_dir.replace("\\", "/")
            logger.info(
                "[DeepResearchTaskManager] Styled html report exported "
                "task_id=%s style_status=%s style_applied=%s "
                "elapsed_seconds=%.3f output=%s",
                task_id,
                styled_result.style_status,
                styled_result.style_applied,
                time.monotonic() - html_export_started_at,
                report_file_html,
            )

        return artifacts

    @staticmethod
    def _extract_section_titles(outline_content: str) -> dict[str, str]:
        """从大纲内容中提取章节标题，映射到 section_idx.

        支持两种内容格式：
        1. JSON 格式：尝试 json.loads 解析，从 "sections"/"outline" 等结构中提取
        2. Markdown 格式：解析多种标题样式（第N章、N. 标题等）

        注意：一级标题 # 通常用于报告总标题（如 "# 研究报告：XXX"），
        此类标题会被跳过，仅提取二级/子级标题作为章节标题。

        Args:
            outline_content: 大纲内容（JSON 或 markdown 文本）

        Returns:
            dict[str, str]：section_idx -> 章节标题 的映射
        """
        section_titles: dict[str, str] = {}
        if not outline_content or not outline_content.strip():
            return section_titles

        # 尝试 JSON 格式解析
        stripped_content = outline_content.strip()
        if stripped_content.startswith('{') or stripped_content.startswith('['):
            try:
                data = json.loads(stripped_content)
                json_titles = DeepResearchTaskManager._extract_titles_from_json(data)
                if json_titles:
                    return json_titles
            except (json.JSONDecodeError, TypeError):
                pass  # JSON 解析失败，回退到 markdown 解析

        lines = outline_content.split('\n')
        title_index = 0

        for line in lines:
            stripped = line.strip()
            # 提取标题内容：支持 #、##、### 级别
            heading = ""
            if stripped.startswith('## '):
                heading = stripped[3:].strip()
            elif stripped.startswith('### '):
                heading = stripped[4:].strip()
            elif stripped.startswith('# '):
                # 一级标题通常是报告总标题，不作为章节标题
                # 但如果格式如 "# 第1章节：XXX" 则仍提取
                heading = stripped[2:].strip()
                # 检查是否为报告总标题（不含章节编号关键词）
                if not re.match(r'第\d+[章节篇部]', heading) and not re.match(r'\d+[.、)]\s', heading):
                    # 跳过报告总标题（如 "# 深度研究报告：XXX"）
                    continue
            else:
                continue

            if not heading:
                continue

            title_index += 1
            idx = str(title_index)

            # 模式1：第N章/章节/篇/部 标题
            m = re.match(r'第(\d+)[章节篇部]\s*[：:]*\s*(.+)', heading)
            if m:
                section_titles[m.group(1)] = m.group(2).strip()
                continue

            # 模式2：N. 标题 或 N、标题 或 N) 标题
            m = re.match(r'(\d+)[.、)]\s*(.+)', heading)
            if m:
                section_titles[m.group(1)] = m.group(2).strip()
                continue

            # 模式3：按出现顺序映射
            section_titles[idx] = heading

        return section_titles

    @staticmethod
    def _extract_titles_from_json(data: Any) -> dict[str, str]:
        """从 JSON 结构中提取章节标题.

        支持的结构：
        - {"sections": [{"title": "...", "name": "..."}, ...]}
        - {"outline": [{"title": "...", "name": "..."}, ...]}
        - [{"title": "...", "name": "..."}, ...]（顶层列表）

        Args:
            data: 解析后的 JSON 数据

        Returns:
            dict[str, str]：section_idx -> 章节标题 的映射
        """
        section_titles: dict[str, str] = {}
        sections: list = []

        if isinstance(data, dict):
            for key in ("sections", "outline", "chapters", "章节"):
                if key in data and isinstance(data[key], list):
                    sections = data[key]
                    break
            if not sections:
                for v in data.values():
                    if isinstance(v, list):
                        sections = v
                        break
        elif isinstance(data, list):
            sections = data

        for i, item in enumerate(sections, start=1):
            title = ""
            if isinstance(item, dict):
                title = item.get("title") or item.get("name") or item.get("heading") or ""
            elif isinstance(item, str):
                title = item
            if title:
                section_titles[str(i)] = title.strip()

        return section_titles

    @staticmethod
    def _build_progress_entry(
        agent_name: str,
        display_name: str,
        description: str,
        section_idx: str,
        section_titles: dict[str, str],
        content_preview: str,
        query: str,
    ) -> str | None:
        """构建进度条目字符串（用于工具返回值）.

        Args:
            agent_name: 节点名称（如 "outline", "plan_reasoning"）
            display_name: 中文显示名（如 "生成大纲", "规划调研"）
            description: 功能描述
            section_idx: Section 编号
            section_titles: 章节标题映射
            content_preview: 内容预览（前50字符）
            query: 用户查询

        Returns:
            进度条目字符串，或 None（无需条目的节点）
        """
        # 过滤低价值节点：start/end/framework/entry/outline_interaction 不向用户展示进度
        _SKIP_PROGRESS_NODES = {"start", "end", "framework", "entry", "outline_interaction"}
        if agent_name in _SKIP_PROGRESS_NODES:
            return None

        # outline 节点：注入研究主题
        if agent_name == "outline":
            short_query = query[:80] + "..." if len(query) > 80 else query
            enhanced_desc = description.replace("研究主题", f"「{short_query}」")
            return f"{display_name} - {enhanced_desc}"

        # 章节级节点（有 section_idx）：注入章节标题
        if section_idx != "0":
            title = section_titles.get(section_idx, "")
            if title:
                return f"{display_name} - {description}（第{section_idx}章节：{title}）"
            elif content_preview:
                return f"{display_name} - {description}（第{section_idx}章节：{content_preview}）"
            else:
                return f"{display_name} - {description}（第{section_idx}章节）"

        # 主图节点：注入动态内容信息
        # editor_team / reporter：注入章节数量和大纲概要
        if agent_name in ("editor_team", "reporter"):
            section_count = len(section_titles)
            if section_count:
                titles_preview = "、".join(list(section_titles.values())[:3])
                if section_count > 3:
                    titles_preview += f"等{section_count}个章节"
                return f"{display_name} - {description}（{titles_preview}）"
            # section_titles 尚未解析时，使用 content_preview 或 query
            if content_preview:
                return f"{display_name} - {description}（{content_preview[:50]}）"
            short_query = query[:50] + "..." if len(query) > 50 else query
            return f"{display_name} - 基于「{short_query}」{description}"

        # source_tracer / source_tracer_infer：注入校验范围
        if agent_name in ("source_tracer", "source_tracer_infer"):
            section_count = len(section_titles)
            if section_count:
                return f"{display_name} - {description}（覆盖{section_count}个章节）"
            return f"{display_name} - {description}"

        # 其他主图节点：默认格式（不再返回 None）
        return f"{display_name} - {description}"

    @staticmethod
    def _build_push_preview(
        agent_name: str,
        section_idx: str,
        section_titles: dict[str, str],
        content_preview: str,
        query: str,
    ) -> str:
        """构建 WebSocket 推送的 content_preview 字段.

        Args:
            agent_name: 节点名称
            section_idx: Section 编号
            section_titles: 章节标题映射
            content_preview: 原始内容预览
            query: 用户查询

        Returns:
            用于 _send_progress_push 的 content_preview 值
        """
        # outline 节点：截断后的研究主题
        if agent_name == "outline":
            return query[:80] + "..." if len(query) > 80 else query

        # 章节级节点：优先 section_titles，其次 content_preview
        if section_idx != "0":
            title = section_titles.get(section_idx, "")
            if title:
                return title
            # content_preview 来自 start chunk 可能为空，
            # 但仍返回原值以便 _send_progress_push 尝试使用
            return content_preview

        # 主图节点：为关键节点构建有意义的 preview
        if agent_name in ("editor_team", "reporter"):
            section_count = len(section_titles)
            if section_count:
                titles_preview = "、".join(list(section_titles.values())[:3])
                if section_count > 3:
                    titles_preview += f"等{section_count}个章节"
                return titles_preview
            # section_titles 尚未解析时 fallback 到 content_preview 或 query
            if content_preview:
                return content_preview
            short_query = query[:50] + "..." if len(query) > 50 else query
            return short_query

        if agent_name in ("source_tracer", "source_tracer_infer"):
            section_count = len(section_titles)
            if section_count:
                return f"覆盖{section_count}个章节"
            return content_preview

        # 其他节点：保持原值
        return content_preview

    @staticmethod
    def _format_report_result(report_paths: dict[str, str]) -> str:
        """生成格式化后的报告结果路径字符串."""
        parts = []
        if report_paths.get("html"):
            parts.append(f"html报告已保存到{report_paths['html']}\n")
        parts.append(f"markdown报告已保存到{report_paths['md']}\n")
        if report_paths.get("infer_dir"):
            parts.append(f"溯源推理图已保存到{report_paths['infer_dir']}\n")
        if report_paths.get("chart_dir"):
            parts.append(f"图表图片已保存到{report_paths['chart_dir']}\n")
        return "".join(parts)

    @staticmethod
    def _format_progress_result(
        progress_entries: list[str],
        report_paths: dict[str, str],
    ) -> str:
        """生成包含进度条目和报告路径的结果字符串.

        Args:
            progress_entries: 进度条目列表（如 "生成大纲 - 基于XX规划报告章节结构"）
            report_paths: 报告文件路径字典

        Returns:
            格式化的结果字符串
        """
        parts = []
        for entry in progress_entries:
            parts.append(f"{entry}\n")
        parts.append("\n")
        parts.append(DeepResearchTaskManager._format_report_result(report_paths))
        return "".join(parts)

    async def _run_jiuwen_workflow(
            self,
            query: str,
            agent_config: Dict,
            report_template: str,
            # 新增路由参数
            session_id: str = "",
            channel_id: str = "",
            request_id: str = "",
            collect_progress: bool = False,
    ) -> Any:
        """运行 openJiuwen-DeepResearch 工作流，捕获执行日志到任务目录.

        Args:
            query: 用户查询字符串
            agent_config: Agent 配置字典
            report_template: 报告模板
            collect_progress: 是否收集进度条目（用于工具输出）。
                为 True 时返回 (report_content, progress_entries)，否则返回 report_content

        Returns:
            collect_progress=False: 最终研究报告内容
            collect_progress=True: (最终研究报告内容, 进度条目列表)
        """
        try:
            last_report = None
            chunk_count = 0

            # === 进度追踪变量 ===
            # 使用字典追踪所有活跃节点的状态，支持并行执行的多个章节。
            # 每个节点独立管理生命周期，避免并行节点相互干扰。
            # node_key -> {"started": bool, "done": bool, "agent_name": str, "section_idx": str}
            active_nodes: dict[str, dict[str, Any]] = {}

            # === 内容收集变量 ===
            outline_content_parts: list[str] = []
            section_titles: dict[str, str] = {}
            progress_entries: list[str] = []

            def create_agent_stream():
                agent_factory = AgentFactory()
                agent = agent_factory.create_agent(agent_config)
                return agent.run(
                    message=query,
                    conversation_id=str(secrets.token_hex(16)),
                    report_template=report_template,
                    interrupt_feedback="",
                    agent_config=agent_config
                )

            stream = iterate_with_scoped_tls_initialization(
                create_agent_stream,
                TASK_MANAGER_TLS_ENV,
            )
            async with aclosing(stream):
                async for chunk in stream:
                    chunk_count += 1
                    logger.debug("[DeepResearchTaskManager] Stream chunk #%d from node", chunk_count)
                    chunk_content = json.loads(chunk)

                    # === 解析节点进度信息 ===
                    agent_name = chunk_content.get("agent", "")
                    event = chunk_content.get("event", "")
                    section_idx = chunk_content.get("section_idx", "0")
                    content_preview = chunk_content.get("content", "")[:50] if chunk_content.get("content") else ""

                    # 构建唯一节点标识（包含 section 编号）
                    node_key = f"{agent_name}_{section_idx}" if section_idx != "0" else agent_name

                    # === 收集大纲内容并持续解析章节标题 ===
                    # （不受 collect_progress 限制，因为 WebSocket 推送也需要 section_titles）
                    if agent_name == "outline":
                        chunk_text = chunk_content.get("content", "")
                        if chunk_text:
                            outline_content_parts.append(chunk_text)
                            # 每次收到 outline 内容都重新解析章节标题
                            # （大纲是流式到达的，每次可能有新的标题出现）
                            full_outline = "".join(outline_content_parts)
                            parsed = self._extract_section_titles(full_outline)
                            if parsed and parsed != section_titles:
                                new_count = len(parsed)
                                old_count = len(section_titles)
                                section_titles = parsed
                                if old_count == 0:
                                    logger.info(
                                        "[DeepResearchTaskManager] 从大纲中提取到 %d 个章节标题: %s",
                                        new_count,
                                        section_titles,
                                    )
                                else:
                                    logger.info(
                                        "[DeepResearchTaskManager] 大纲更新，章节标题从 %d 个增至 %d 个: %s",
                                        old_count, new_count,
                                        section_titles,
                                    )

                    # === 节点生命周期管理（支持并行执行） ===
                    # DeepSearch 引擎的多数节点不发送 event=start CustomSchema chunk，
                    # 但每个 chunk 都携带 agent 字段。当首次收到某节点的 chunk 时，
                    # 视为该节点开始执行；收到显式 event=done 时，视为节点完成。
                    # 使用 active_nodes 字典独立追踪每个节点的状态，支持并行章节。
                    if agent_name and node_key not in active_nodes:
                        display_info = self.NODE_DISPLAY_INFO.get(agent_name)
                        if display_info:
                            # 首次收到该节点的 chunk，发送开始推送
                            display_name = display_info[0]
                            description = display_info[1]
                            push_preview = self._build_push_preview(
                                agent_name, section_idx, section_titles, content_preview, query,
                            )
                            # 进度条目（工具返回值）
                            if collect_progress:
                                entry = self._build_progress_entry(
                                    agent_name, display_name, description,
                                    section_idx, section_titles, content_preview, query,
                                )
                                if entry:
                                    progress_entries.append(entry)
                            # 修改1：推同一 section 内未完成节点的 done
                            # 同一 section 内节点串行执行，新节点出现意味着上一节点已完成。
                            # 必须按 section_idx 过滤，避免并行 section 的误推。
                            for other_key, other_state in active_nodes.items():
                                if (other_state["started"] and not other_state["done"]
                                        and other_state["section_idx"] == section_idx
                                        and other_key != node_key):
                                    await self._send_progress_push(
                                        session_id, channel_id, request_id,
                                        other_state["agent_name"], "done", other_state["section_idx"],
                                        section_titles=section_titles,
                                    )
                                    other_state["done"] = True
                            # WebSocket 推送（前端实时进度）
                            await self._send_progress_push(
                                session_id, channel_id, request_id,
                                agent_name, "start", section_idx, push_preview,
                                section_titles=section_titles,
                            )
                            # 记录节点状态
                            active_nodes[node_key] = {
                                "started": True,
                                "done": False,
                                "agent_name": agent_name,
                                "section_idx": section_idx,
                            }
                            # 修改2：终态节点立即推 done
                            if agent_name in self.INSTANT_COMPLETE_NODES:
                                await self._send_progress_push(
                                    session_id, channel_id, request_id,
                                    agent_name, "done", section_idx,
                                    section_titles=section_titles,
                                )
                                active_nodes[node_key]["done"] = True

                    # === 处理显式 event=done 事件 ===
                    # 部分节点（outline、plan_reasoning）通过 custom_stream_output
                    # 显式发送 event=done，这些事件可能携带更精确的信息
                    # （如 outline 的 done 触发 section_titles 最终解析）。
                    if agent_name and event == "done":
                        # outline 完成时：对累积大纲做最终一次 section_titles 解析
                        if agent_name == "outline" and outline_content_parts:
                            full_outline = "".join(outline_content_parts)
                            final_parsed = self._extract_section_titles(full_outline)
                            if final_parsed and final_parsed != section_titles:
                                logger.info(
                                    "[DeepResearchTaskManager] outline done: 最终解析章节标题 "
                                    "从 %d 个增至 %d 个: %s",
                                    len(section_titles), len(final_parsed),
                                    final_parsed,
                                )
                                section_titles = final_parsed

                        # 节点完成：仅在已开始且未完成时推送
                        node_state = active_nodes.get(node_key)
                        if node_state and node_state["started"] and not node_state["done"]:
                            await self._send_progress_push(
                                session_id, channel_id, request_id,
                                agent_name, "done", section_idx,
                                section_titles=section_titles,
                            )
                            node_state["done"] = True

                    # 现有逻辑：解析最终报告
                    report_result = parse_endnode_content(chunk_content)
                    if report_result:
                        last_report = report_result
                        logger.info(
                            "[DeepResearchTaskManager] Final report received at chunk #%d",
                            chunk_count,
                            extra={'user_visible': 'critical'}
                        )

            # === 发送所有活跃节点的完成通知 ===
            # 遍历所有已开始但未完成的节点，发送完成推送。
            # 这确保即使 DeepSearch 引擎没有显式发送 event=done，
            # 前端也能收到所有节点的完成通知。
            for node_key, node_state in active_nodes.items():
                if node_state["started"] and not node_state["done"]:
                    await self._send_progress_push(
                        session_id, channel_id, request_id,
                        node_state["agent_name"], "done", node_state["section_idx"],
                        section_titles=section_titles,
                    )
                    node_state["done"] = True

            logger.info(
                "[DeepResearchTaskManager] Workflow completed. Total chunks: %d",
                chunk_count
            )

            if collect_progress:
                return last_report, progress_entries
            return last_report

        except Exception as e:
            logger.error(
                "[DeepResearchTaskManager] Workflow execution failed: %s",
                str(e),
                exc_info=True,
                extra={'user_visible': 'critical'}
            )
            raise

    @contextmanager
    def _log_capture_scope(self, task_id: str, log_output_dir: str = ""):
        """让工作流执行、结果记录和报告导出共享同一日志生命周期。"""
        context = self._setup_log_capture(task_id, log_output_dir)
        try:
            yield
        finally:
            self._teardown_log_capture(context)

    @staticmethod
    def _setup_log_capture(
            task_id: str,
            log_output_dir: str = "",
    ) -> Dict[str, Any]:
        """设置日志捕获机制，使用 openjiuwen_deepsearch 内部 LogManager 初始化日志系统.

        Args:
            task_id: 任务 ID（用于关联日志）
            log_output_dir: 日志输出目录路径（默认为项目日志目录下的 DeepResearch 子文件夹）

        Returns:
            清理上下文字典，包含：
            - session_id_token: session_id_ctx.reset 所需的 token
            - original_safe_base: 原 LogManager._SAFE_BASE 值
            - log_dir: 日志输出目录

        日志输出结构（由 LogManager.init() 创建）：
            {log_output_dir}/
            ├── common/
            │   ├── common.log          # 通用日志
            │   └── common_warning.log  # 警告及以上日志
            ├── metrics/
            │   └── metrics.log         # 性能打点日志
            └── interface/
            │   └── deepsearch_interface.log  # 接口日志
        """
        context: Dict[str, Any] = {
            "session_id_token": None,
            "original_safe_base": None,
            "log_dir": None,
        }

        # 默认使用项目日志目录下的 DeepResearch 子文件夹
        if not log_output_dir:
            log_output_dir = str(get_logs_dir() / "DeepResearch")
        if not task_id:
            task_id = f"anonymous_{secrets.token_hex(4)}"

        # 1. 设置 session_id_ctx（让 openjiuwen_deepsearch 内部日志关联任务）
        session_id_token = session_id_ctx.set(task_id)
        context["session_id_token"] = session_id_token

        logger.info(
            "[DeepResearchTaskManager] Task execution session_id: %s",
            task_id,
            extra={'user_visible': 'critical'}
        )

        # 2. 创建任务专属日志目录
        log_dir = Path(log_output_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        context["log_dir"] = str(log_dir)

        # 3. 处理 LogManager 单例和路径限制（调用方已持有 asyncio 锁）
        # 保存原始 _SAFE_BASE 值以便恢复
        context["original_safe_base"] = LogManager._SAFE_BASE

        # 动态修改 _SAFE_BASE 为任务目录（绕过路径安全限制）
        LogManager._SAFE_BASE = str(log_dir)

        # 重置 _initialized 以允许重新初始化
        LogManager._initialized = False

        # 4. 清理之前配置的 handlers（避免重复）
        # LogManager.init() 会清理 root logger handlers，但不会清理 metrics/interface
        metrics_logger = logging.getLogger("metrics")
        interface_logger = logging.getLogger("deepsearch_interface")

        for handler in list(metrics_logger.handlers):
            handler.flush()
            handler.close()
        metrics_logger.handlers.clear()

        for handler in list(interface_logger.handlers):
            handler.flush()
            handler.close()
        interface_logger.handlers.clear()

        # 5. 使用 LogManager.init() 初始化完整日志系统
        LogManager.init(
            log_dir=str(log_dir),
            level="INFO",
            max_bytes=10 * 1024 * 1024,  # 10MB
            backup_count=5,
            is_sensitive=False,
        )

        logger.info(
            "[DeepResearchTaskManager] LogManager initialized with log_dir=%s level=INFO",
            log_dir
        )

        return context

    @staticmethod
    def _teardown_log_capture(context: Dict[str, Any]) -> None:
        """清理日志捕获机制，恢复原日志配置.

        Args:
            context: _setup_log_capture 返回的清理上下文
        """
        # 1. 重置 session_id_ctx
        session_id_token = context.get("session_id_token")
        if session_id_token is not None:
            session_id_ctx.reset(session_id_token)
            logger.debug("[DeepResearchTaskManager] Reset session_id_ctx")

        # 2. 重置 LogManager 状态（允许下次任务重新初始化）
        LogManager._initialized = False

        # 3. 恢复原始 _SAFE_BASE
        original_safe_base = context.get("original_safe_base")
        if original_safe_base is not None:
            LogManager._SAFE_BASE = original_safe_base

        # 4. 清理所有 handlers（root logger、metrics、interface）
        # 清理 root logger handlers（由 setup_common_logger 创建）
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            handler.flush()
            handler.close()
        root_logger.handlers.clear()
        root_logger.setLevel(logging.WARNING)

        # 清理 metrics_logger handlers（由 setup_metrics_logger 创建）
        metrics_logger = logging.getLogger("metrics")
        for handler in list(metrics_logger.handlers):
            handler.flush()
            handler.close()
        metrics_logger.handlers.clear()

        # 清理 interface_logger handlers（由 setup_interface_logger 创建）
        interface_logger = logging.getLogger("deepsearch_interface")
        for handler in list(interface_logger.handlers):
            handler.flush()
            handler.close()
        interface_logger.handlers.clear()

        logger.debug(
            "[DeepResearchTaskManager] Cleaned up all LogManager handlers"
        )

    async def _execute_task(
        self,
        task_id: str,
        query: str,
        file_name: str,
    ) -> None:
        """执行 DeepResearch 任务（后台协程）."""
        task = self._tasks[task_id]
        task.started_at = time.time()
        task.status = TaskStatus.RUNNING
        runtime_tokens = self._bind_task_runtime(task)

        logger.debug(
            "[DeepResearchTaskManager] 任务路由信息 task_id=%s channel_id=%s session_id=%s request_id=%s "
            "tenant=(%s,%s)",
            task_id,
            task.channel_id,
            task.session_id,
            task.request_id,
            task.service_id,
            task.agent_id,
        )

        logger.info(
            "[DeepResearchTaskManager] 开始执行任务 task_id=%s query=%s",
            task_id,
            query[:80] + "..." if len(query) > 80 else query,
            extra={'user_visible': 'critical'}
        )

        try:
            # 1. 加载配置
            config = self._load_config()

            # 2. 验证配置
            config_valid, config_msg = self._validate_config(config)
            if not config_valid:
                raise ValueError(config_msg)

            config_extension = {
                "extra_body": {
                    "thinking": {
                        "type": "disabled"
                    }
                }
            }

            # 4. 解析 LLM 配置
            current_agent_config = Config().agent_config.model_dump()
            workflow_llm_config, report_style_llm_config = self._build_general_llm_configs(
                config,
                config_extension,
            )
            current_agent_config["llm_config"]["general"] = workflow_llm_config

            # 5. 解析搜索引擎配置
            current_agent_config["web_search_engine_config"]["search_engine_name"] = config["WEB_SEARCH_ENGINE_NAME"]
            current_agent_config["web_search_engine_config"]["search_api_key"] = bytearray(
                config["WEB_SEARCH_API_KEY"], encoding="utf-8"
            )
            current_agent_config["web_search_engine_config"]["search_url"] = config["WEB_SEARCH_URL"]
            current_agent_config["web_search_engine_config"]["max_web_search_results"] = (
                config["MAX_WEB_SEARCH_RESULTS"]
            )
            current_agent_config["outliner_max_section_num"] = int(config["OUTLINER_MAX_SECTION_NUM"])

            current_agent_config["workflow_human_in_the_loop"] = config["WORKFLOW_HUMAN_IN_THE_LOOP"]
            current_agent_config["outline_interaction_enabled"] = config["OUTLINE_INTERACTION_ENABLED"]
            current_agent_config["source_tracer_infer_switch"] = config["SOURCE_TRACER_INFER_SWITCHES"]
            current_agent_config["vlm_chart_generator_enable"] = config["VLM_CHART_GENERATOR_ENABLE"]
            current_agent_config["vlm_chart_generator_max_iterations"] = config["VLM_CHART_GENERATOR_MAX_ITERATIONS"]
            if config["EXECUTION_METHOD"] == ExecutionMethod.DEPENDENCY_DRIVING.value:
                current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
            else:
                current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value

            # 6. 执行工作流（带日志捕获）
            # 报告目录：用于保存报告文件
            workspace = task.workspace_dir or get_effective_request_workspace_dir() or ""
            report_dir = os.path.join(workspace, "reports") if workspace else os.path.join(
                get_effective_request_workspace_dir() or ".", "reports"
            )
            # 日志目录：使用项目日志目录下的 DeepResearch 子文件夹（默认行为）
            # 进程合一：LogManager 全局状态需串行化整段 capture 生命周期
            async with self._get_log_capture_lock():
                with self._log_capture_scope(task_id):
                    data = await self._run_jiuwen_workflow(
                        query,
                        current_agent_config,
                        "",
                    )

                    if data:
                        report_paths = await self._write_report_artifacts(
                            data,
                            file_name,
                            report_dir,
                            task_id=task_id,
                            cancel_event=task.cancel_event,
                            llm_config=report_style_llm_config,
                        )
                        result = self._format_report_result(report_paths)

                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        logger.info(
                            "[DeepResearchTaskManager] 任务完成 task_id=%s result=%s",
                            task_id,
                            result,
                            extra={'user_visible': 'critical'}
                        )
                    else:
                        raise ValueError("DeepResearch 返回空结果")

        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "任务已取消"
            logger.info(
                "[DeepResearchTaskManager] 任务已取消 task_id=%s",
                task_id,
                extra={'user_visible': 'critical'}
            )

        except Exception as exc:
            task.status = TaskStatus.ERROR
            task.error = str(exc)
            logger.exception(
                "[DeepResearchTaskManager] 任务异常 task_id=%s error=%s",
                task_id,
                exc,
                extra={'user_visible': 'critical'}
            )

        finally:
            self._reset_task_runtime(runtime_tokens)
            task.completed_at = time.time()
            self._task_handles.pop(task_id, None)

            # 发送 WebSocket 通知

            await self._notify_completion(task)

    async def _send_progress_push(
        self,
        session_id: str,
        channel_id: str,
        request_id: str,
        node_name: str,
        node_status: str,
        section_idx: str = "0",
        content_preview: str = "",
        section_titles: dict[str, str] | None = None,
    ) -> None:
        """发送节点进度推送消息到前端.

        使用 NODE_DISPLAY_INFO 映射将英文节点名转换为中文显示名，
        并附带功能描述，让用户了解每个步骤的具体进展。

        Args:
            session_id: 会话 ID
            channel_id: 渠道 ID
            request_id: 请求 ID
            node_name: 节点名称（agent 原值，如 "outline", "plan_reasoning"）
            node_status: 节点状态（"start", "done"）
            section_idx: Section 编号（默认 "0"）
            content_preview: 内容预览（可选）
            section_titles: 章节标题映射（可选，用于 payload 中的 section_title 字段）
        """
        if not session_id or not channel_id:
            logger.debug(
                "[DeepResearchTaskManager] 跳过进度推送: 无路由信息 node=%s",
                node_name,
            )
            return

        # 过滤低价值节点：与 _build_progress_entry 保持一致
        _SKIP_PROGRESS_NODES = {"start", "end", "framework", "entry", "outline_interaction"}
        if node_name in _SKIP_PROGRESS_NODES:
            return

        # 查找节点显示信息
        display_info = self.NODE_DISPLAY_INFO.get(node_name)
        display_name = display_info[0] if display_info else node_name
        description = display_info[1] if display_info else ""

        # 构建章节编号上下文
        section_context = ""
        if section_idx != "0":
            section_context = f"第{section_idx}章节"

        # 构建任务内容（中文显示名 + 功能描述）
        if node_status == "start":
            # 开始状态：显示节点名称和功能描述
            task_content = f"{display_name}"
            if description:
                task_content += f" - {description}"
            # 根据节点类型添加内容信息
            if node_name == "outline" and content_preview:
                # description 是 "基于研究主题规划报告章节结构"
                # 替换 "研究主题" 为实际的 content_preview（截断后的 query）
                enhanced_desc = description.replace("研究主题", f"「{content_preview}」")
                task_content = f"{display_name} - {enhanced_desc}"
            elif section_context:
                # 所有章节级节点（plan_reasoning, sub_reporter, info_collector 等）
                # content_preview 来自 _build_push_preview：优先 section_titles，其次原始 preview
                if content_preview:
                    task_content += f"（{section_context}：{content_preview}）"
                else:
                    task_content += f"（{section_context}）"
            elif node_name in ("editor_team", "reporter") and content_preview:
                # 主图节点：editor_team 和 reporter 注入章节概要
                task_content += f"（{content_preview[:60]}）"
            elif node_name in ("source_tracer", "source_tracer_infer") and content_preview:
                # 溯源节点：注入校验范围
                task_content += f"（{content_preview[:40]}）"
        elif node_status == "done":
            # 完成状态：简要显示完成信息
            task_content = f"{display_name} 完成"
            if section_context:
                task_content += f"（{section_context}）"
            if content_preview:
                task_content += f": {content_preview[:30]}..."
        else:
            logger.debug(
                "[DeepResearchTaskManager] 跳过进度推送: 未知状态 node=%s status=%s",
                node_name,
                node_status,
            )
            return

        # 使用 task.start/task.complete 事件类型
        event_type = "task.start" if node_status == "start" else "task.complete"
        task_id = f"dr_{section_idx}_{node_name}" if section_idx != "0" else f"dr_{node_name}"

        payload = {
            "event_type": event_type,
            "task_id": task_id,
            "task_content": task_content,
            # 保留原始节点名和中文显示名，供前端灵活渲染
            "node_name": node_name,
            "section_idx": section_idx,
            "display_name": display_name,
            "description": description,
            # 章节标题：即使 task_content 未嵌入标题，前端也可直接使用
            "section_title": (section_titles or {}).get(section_idx, "") if section_idx != "0" else "",
        }

        msg = {
            "request_id": request_id or "",
            "channel_id": channel_id,
            "session_id": session_id,
            "payload": payload,
            "is_complete": False,
        }

        try:
            await self._gateway_push.send_push(msg)
            logger.info(
                "[DeepResearchTaskManager] 进度推送: %s",
                task_content,
                extra={'user_visible': 'progress'}
            )
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] 进度推送失败 node=%s error=%s",
                node_name,
                exc,
            )

    async def _notify_completion(self, task: DeepResearchTask) -> None:
        """通过 WebSocket 发送任务完成通知."""
        if task.status == TaskStatus.COMPLETED:
            payload_status = "completed"
            message = task.result
        elif task.status == TaskStatus.CANCELLED:
            payload_status = "cancelled"
            message = "任务已取消"
        else:
            payload_status = "error"
            message = task.error

        payload = {
            "event_type": "deepresearch.task_completed",
            "task_id": task.task_id,
            "query": task.query,
            "status": payload_status,
            "result": message,
            "created_at": DeepResearchTask.format_timestamp(task.created_at) if task.created_at else None,
            "started_at": DeepResearchTask.format_timestamp(task.started_at) if task.started_at else None,
            "completed_at": DeepResearchTask.format_timestamp(task.completed_at) if task.completed_at else None,
        }

        msg = {
            "request_id": task.request_id,
            "channel_id": task.channel_id,
            "session_id": task.session_id,
            "payload": payload,
            "is_complete": False,
        }

        logger.debug(
            "[DeepResearchTaskManager] 发送任务完成通知 task_id=%s status=%s",
            task.task_id,
            payload_status,
        )

        try:
            await self._gateway_push.send_push(msg)
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] 发送 WebSocket 通知失败 task_id=%s error=%s",
                task.task_id,
                exc,
            )

    async def create_task(self, request: DeepResearchTaskRequest) -> str:
        """创建并启动 DeepResearch 任务.

        Args:
            request: 任务查询、文件名、路由与租户参数

        Returns:
            任务 ID

        Raises:
            RuntimeError: 当达到任务数量上限时
        """
        query = request.query
        file_name = request.file_name
        session_id = request.session_id
        channel_id = request.channel_id
        request_id = request.request_id
        service_id = request.service_id
        agent_id = request.agent_id

        # 检查全局任务数量上限
        running_count = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
        if running_count >= DeepResearchTaskManager.MAX_ACTIVE_TASKS:
            raise RuntimeError(
                f"活跃任务数已达上限 ({DeepResearchTaskManager.MAX_ACTIVE_TASKS})，"
                f"请等待现有任务完成后再创建新任务"
            )

        # 检查保留任务数量上限
        if len(self._tasks) >= DeepResearchTaskManager.MAX_TOTAL_TASKS:
            # 清理已完成的旧任务
            completed_tasks = [
                tid for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR)
            ]
            for tid in completed_tasks[: len(self._tasks) - DeepResearchTaskManager.MAX_TOTAL_TASKS + 1]:
                self._tasks.pop(tid, None)
                logger.info("[DeepResearchTaskManager] 清理已完成旧任务 task_id=%s", tid)

        effective_session_id = session_id or "__anonymous__"
        session_tasks = sum(
            1 for t in self._tasks.values()
            if
            (t.session_id == effective_session_id or (t.session_id == "" and effective_session_id == "__anonymous__"))
            and t.status == TaskStatus.RUNNING
        )
        if session_tasks >= DeepResearchTaskManager.MAX_TASKS_PER_SESSION:
            raise RuntimeError(
                f"当前会话 ({session_id or '匿名'}) 活跃任务数已达上限 "
                f"({DeepResearchTaskManager.MAX_TASKS_PER_SESSION})"
            )

        task_id = f"dr_{time.monotonic_ns()}_{secrets.token_hex(4)}"
        sid = (
            service_id or getattr(self, "service_id", "default") or "default"
        ).strip() or "default"
        aid = (
            agent_id or getattr(self, "agent_id", "default") or "default"
        ).strip() or "default"
        env_snapshot = self._capture_env_snapshot(sid, aid)
        workspace_dir = get_effective_request_workspace_dir() or ""

        task = DeepResearchTask(
            task_id=task_id,
            query=query,
            file_name=file_name,
            status=TaskStatus.RUNNING,
            created_at=time.time(),
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            service_id=sid,
            agent_id=aid,
            env_snapshot=env_snapshot,
            workspace_dir=workspace_dir,
        )

        self._tasks[task_id] = task

        # 创建后台协程，显式传递路由参数
        coro = self._execute_task(
            task_id=task_id,
            query=query,
            file_name=file_name,
        )
        task_handle = asyncio.create_task(coro)
        self._task_handles[task_id] = task_handle

        logger.info(
            "[DeepResearchTaskManager] 创建深度研究任务：%s task_id=%s query=%s channel_id=%s session_id=%s "
            "tenant=(%s,%s) running_count=%d/%d",
            file_name,
            task_id,
            query[:80] + "..." if len(query) > 80 else query,
            channel_id,
            session_id,
            sid,
            aid,
            running_count + 1,
            DeepResearchTaskManager.MAX_ACTIVE_TASKS,
            extra={'user_visible': 'critical'}
        )

        return task_id

    async def get_task_status(
        self,
        task_id: str,
        caller_session_id: str = "",
        caller_channel_id: str = "",
    ) -> Dict[str, Any] | None:
        """获取任务状态.

        Args:
            task_id: 任务 ID
            caller_session_id: 调用者会话 ID（用于授权校验）
            caller_channel_id: 调用者渠道 ID（用于授权校验）

        Returns:
            任务信息字典，如果任务不存在或无权访问则返回 None
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        # 授权校验：只允许任务所有者访问
        if not self._check_task_ownership(task, caller_session_id, caller_channel_id):
            return None

        return {
            "task_id": task.task_id,
            "query": task.query,
            "file_name": task.file_name,
            "status": task.status.value,
            "created_at": DeepResearchTask.format_timestamp(task.created_at) if task.created_at else None,
            "started_at": DeepResearchTask.format_timestamp(task.started_at) if task.started_at else None,
            "completed_at": DeepResearchTask.format_timestamp(task.completed_at) if task.completed_at else None,
            "result": task.result if task.status == TaskStatus.COMPLETED else "",
            "error": task.error if task.status == TaskStatus.ERROR else "",
        }

    @staticmethod
    def _check_task_ownership(
            task: DeepResearchTask,
            caller_session_id: str,
            caller_channel_id: str,
    ) -> bool:
        """校验任务所有权（仅基于 session_id）.

        安全修复：移除 channel_id 独立授权逻辑，防止同频道跨会话越权访问。
        channel_id 仅用于日志记录和审计，不参与授权决策。

        Args:
            task: 任务对象
            caller_session_id: 调用者会话 ID（唯一授权依据）
            caller_channel_id: 调用者渠道 ID（仅用于日志审计）

        Returns:
            是否有权限访问该任务
        """
        # 安全策略：仅以 session_id 作为授权依据
        # 1. 任务必须有明确的 session_id（匿名任务拒绝访问）
        if not task.session_id:
            logger.warning(
                "[DeepResearchTaskManager] 拒绝访问无归属任务 task_id=%s "
                "(安全策略：匿名任务不允许访问)",
                task.task_id,
            )
            return False

        # 2. 调用者必须提供 session_id
        if not caller_session_id:
            logger.warning(
                "[DeepResearchTaskManager] 拒绝访问：调用者未提供 session_id task_id=%s",
                task.task_id,
            )
            return False

        # 3. 仅匹配 session_id（移除 channel_id 独立授权）
        if task.session_id == caller_session_id:
            return True

        # 4. session_id 不匹配，记录审计日志（包含 channel_id 信息）
        logger.warning(
            "[DeepResearchTaskManager] 越权访问被阻止 task_id=%s "
            "task_session=%s caller_session=%s caller_channel=%s",
            task.task_id,
            task.session_id,
            caller_session_id,
            caller_channel_id,
        )
        return False

    async def list_tasks(
        self,
        status_filter: str | None = None,
        caller_session_id: str = "",
        caller_channel_id: str = "",
    ) -> List[Dict[str, Any]]:
        """列出任务（仅返回调用者拥有的任务）.

        Args:
            status_filter: 可选的状态过滤器（running/completed/cancelled/error）
            caller_session_id: 调用者会话 ID（用于授权校验）
            caller_channel_id: 调用者渠道 ID（用于授权校验）

        Returns:
            任务信息列表
        """
        tasks = []
        for task in self._tasks.values():
            # 授权校验：只返回调用者拥有的任务
            if not self._check_task_ownership(task, caller_session_id, caller_channel_id):
                continue

            if status_filter and task.status.value != status_filter:
                continue

            tasks.append({
                "task_id": task.task_id,
                "query": task.query,
                "file_name": task.file_name,
                "status": task.status.value,
                "created_at": DeepResearchTask.format_timestamp(task.created_at) if task.created_at else None,
                "started_at": DeepResearchTask.format_timestamp(task.started_at) if task.started_at else None,
                "completed_at": DeepResearchTask.format_timestamp(task.completed_at) if task.completed_at else None,
                "result": task.result if task.status == TaskStatus.COMPLETED else "",
                "error": task.error if task.status == TaskStatus.ERROR else "",
            })

        # 按创建时间倒序排列
        tasks.sort(key=lambda x: x["created_at"], reverse=True)
        return tasks

    async def cancel_task(
        self,
        task_id: str,
        caller_session_id: str = "",
        caller_channel_id: str = "",
    ) -> bool:
        """取消任务（仅允许任务所有者取消）.

        Args:
            task_id: 任务 ID
            caller_session_id: 调用者会话 ID（用于授权校验）
            caller_channel_id: 调用者渠道 ID（用于授权校验）

        Returns:
            是否成功取消
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.warning(
                "[DeepResearchTaskManager] 取消任务失败：任务不存在 task_id=%s" % task_id
            )
            return False

        # 授权校验：只允许任务所有者取消
        if not self._check_task_ownership(task, caller_session_id, caller_channel_id):
            logger.warning(
                "[DeepResearchTaskManager] 取消任务失败：无权访问 task_id=%s" % task_id
            )
            return False

        task_handle = self._task_handles.get(task_id)
        if task_handle is None:
            logger.info(
                "[DeepResearchTaskManager] 任务已结束，无需取消 task_id=%s" % task_id
            )
            return False

        if task_handle.done():
            logger.info(
                "[DeepResearchTaskManager] 任务已完成，无需取消 task_id=%s" % task_id
            )
            return False

        # 设置取消事件，通知线程中的报告转换停止
        if task.cancel_event:
            task.cancel_event.set()
            logger.info(
                "[DeepResearchTaskManager] 已设置取消事件 task_id=%s",
                task_id,
            )

        task_handle.cancel()
        try:
            await asyncio.gather(task_handle, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        logger.info(
            "[DeepResearchTaskManager] 已取消任务 task_id=%s",
            task_id,
            extra={'user_visible': 'critical'}
        )
        return True

    async def shutdown(self) -> None:
        """Cancel all in-flight tasks and drop tenant manager state (tenant eviction)."""
        handles = list(self._task_handles.items())
        for task_id, task_handle in handles:
            task = self._tasks.get(task_id)
            if task is not None and task.cancel_event is not None:
                task.cancel_event.set()
            if task_handle is not None and not task_handle.done():
                task_handle.cancel()
        if handles:
            await asyncio.gather(
                *(h for _, h in handles if h is not None),
                return_exceptions=True,
            )
        self._task_handles.clear()
        self._tasks.clear()
        logger.info(
            "[DeepResearchTaskManager] shutdown complete tenant=(%s, %s)",
            self.service_id,
            self.agent_id,
        )

    async def get_task_result(
        self,
        task_id: str,
        caller_session_id: str = "",
        caller_channel_id: str = "",
    ) -> str | None:
        """获取任务结果（仅允许任务所有者获取）.

        Args:
            task_id: 任务 ID
            caller_session_id: 调用者会话 ID（用于授权校验）
            caller_channel_id: 调用者渠道 ID（用于授权校验）

        Returns:
            任务结果字符串，如果任务未完成或无权访问则返回 None
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        # 授权校验：只允许任务所有者获取结果
        if not self._check_task_ownership(task, caller_session_id, caller_channel_id):
            return None

        if task.status != TaskStatus.COMPLETED:
            return None

        return task.result

    async def run_task_and_wait(self, request: DeepResearchTaskRequest) -> Dict[str, Any]:
        """创建任务并等待执行结束，适合 CLI 或脚本入口直接调用."""
        task_id = await self.create_task(request)

        task_handle = self._task_handles.get(task_id)
        if task_handle is not None:
            await asyncio.gather(task_handle, return_exceptions=True)

        task_info = await self.get_task_status(task_id)
        if task_info is None:
            raise RuntimeError(f"DeepResearch task not found after execution: {task_id}")
        return task_info

    async def run_task_direct(
        self,
        request: DeepResearchTaskRequest | str,
        file_name: str | None = None,
    ) -> str:
        """直接执行深度研究任务并阻塞等待完成，不提交到任务池.

        与 create_task 的区别：
        - 不创建 DeepResearchTask 对象
        - 不占用任务池资源（_tasks、_task_handles）
        - 不受 MAX_ACTIVE_TASKS、MAX_TASKS_PER_SESSION 等限制
        - 不发送 WebSocket 完成通知
        - 直接在当前协程中执行，阻塞等待完成

        适用场景：
        - Agent 会话中需要即时获取结果
        - 不需要异步后台执行的场景

        Args:
            request: 任务查询、文件名、路由与租户参数；也兼容旧版 query 字符串
            file_name: 旧版调用方式中的报告文件名

        Returns:
            报告保存路径信息字符串

        Raises:
            ValueError: 配置验证失败或执行结果为空
            Exception: 工作流执行过程中的其他异常
        """
        if isinstance(request, str):
            if file_name is None:
                raise TypeError("file_name is required when request is a query string")
            request = DeepResearchTaskRequest(query=request, file_name=file_name)

        query = request.query
        file_name = request.file_name
        session_id = request.session_id
        channel_id = request.channel_id
        request_id = request.request_id
        service_id = request.service_id
        agent_id = request.agent_id

        # 生成临时任务 ID（仅用于日志和报告文件命名）
        temp_task_id = f"dr_blocking_{time.monotonic_ns()}_{secrets.token_hex(4)}"
        sid = (
            service_id or getattr(self, "service_id", "default") or "default"
        ).strip() or "default"
        aid = (
            agent_id or getattr(self, "agent_id", "default") or "default"
        ).strip() or "default"
        ephemeral = DeepResearchTask(
            task_id=temp_task_id,
            query=query,
            file_name=file_name,
            status=TaskStatus.RUNNING,
            created_at=time.time(),
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            service_id=sid,
            agent_id=aid,
            env_snapshot=self._capture_env_snapshot(sid, aid),
            workspace_dir=get_effective_request_workspace_dir() or "",
        )
        runtime_tokens = self._bind_task_runtime(ephemeral)

        logger.info(
            "[DeepResearchTaskManager] 开始阻塞执行深度研究任务 temp_task_id=%s query=%s "
            "session_id=%s channel_id=%s tenant=(%s,%s)",
            temp_task_id,
            query[:80] + "..." if len(query) > 80 else query,
            session_id,
            channel_id,
            sid,
            aid,
            extra={'user_visible': 'critical'}
        )

        try:
            # 1. 加载配置
            config = self._load_config()

            # 2. 验证配置
            config_valid, config_msg = self._validate_config(config)
            if not config_valid:
                raise ValueError(config_msg)

            config_extension = {
                "extra_body": {
                    "thinking": {
                        "type": "disabled"
                    }
                }
            }

            # 4. 解析 LLM 配置
            current_agent_config = Config().agent_config.model_dump()
            workflow_llm_config, report_style_llm_config = self._build_general_llm_configs(
                config,
                config_extension,
            )
            current_agent_config["llm_config"]["general"] = workflow_llm_config

            # 5. 解析搜索引擎配置
            current_agent_config["web_search_engine_config"]["search_engine_name"] = config["WEB_SEARCH_ENGINE_NAME"]
            current_agent_config["web_search_engine_config"]["search_api_key"] = bytearray(
                config["WEB_SEARCH_API_KEY"], encoding="utf-8"
            )
            current_agent_config["web_search_engine_config"]["search_url"] = config["WEB_SEARCH_URL"]
            current_agent_config["web_search_engine_config"]["max_web_search_results"] = (
                config["MAX_WEB_SEARCH_RESULTS"]
            )
            current_agent_config["outliner_max_section_num"] = int(config["OUTLINER_MAX_SECTION_NUM"])

            current_agent_config["workflow_human_in_the_loop"] = config["WORKFLOW_HUMAN_IN_THE_LOOP"]
            current_agent_config["outline_interaction_enabled"] = config["OUTLINE_INTERACTION_ENABLED"]
            current_agent_config["source_tracer_infer_switch"] = config["SOURCE_TRACER_INFER_SWITCHES"]
            current_agent_config["vlm_chart_generator_enable"] = config["VLM_CHART_GENERATOR_ENABLE"]
            current_agent_config["vlm_chart_generator_max_iterations"] = config["VLM_CHART_GENERATOR_MAX_ITERATIONS"]

            # 配置 VLM 图表生成器相关参数
            if config["VLM_CHART_GENERATOR_ENABLE"] == "True":
                current_agent_config["llm_config"]["vlm_chart_generating"] = {}
                current_agent_config["llm_config"]["vlm_chart_generating"]["model_name"] = config["VISION_MODEL_NAME"]
                current_agent_config["llm_config"]["vlm_chart_generating"]["model_type"] = config["VISION_PROVIDER"]
                current_agent_config["llm_config"]["vlm_chart_generating"]["base_url"] = config["VISION_API_URL"]
                current_agent_config["llm_config"]["vlm_chart_generating"]["api_key"] = bytearray(
                    config["VISION_API_KEY"], encoding="utf-8"
                )
                current_agent_config["llm_config"]["vlm_chart_generating"]["verify_ssl"] = False
            if config["EXECUTION_METHOD"] == ExecutionMethod.DEPENDENCY_DRIVING.value:
                current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
            else:
                current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value

            # 6. 直接执行工作流（阻塞等待，同时收集进度信息）
            workspace = ephemeral.workspace_dir or get_effective_request_workspace_dir() or "."
            report_dir = os.path.join(workspace, "reports")
            async with self._get_log_capture_lock():
                with self._log_capture_scope(temp_task_id):
                    data, progress_entries = await self._run_jiuwen_workflow(
                        query,
                        current_agent_config,
                        "",
                        # 传递路由参数用于进度推送
                        session_id=session_id,
                        channel_id=channel_id,
                        request_id=request_id,
                        collect_progress=True,
                    )

                    if not data:
                        raise ValueError("DeepResearch 返回空结果")

                    # 7. 写出报告文件
                    report_paths = await self._write_report_artifacts(
                        data,
                        file_name,
                        report_dir,
                        task_id=temp_task_id,
                        cancel_event=None,  # 阻塞执行不支持取消
                        llm_config=report_style_llm_config,
                    )

                    result = self._format_progress_result(progress_entries, report_paths)

                    logger.info(
                        "[DeepResearchTaskManager] 阻塞执行任务完成 temp_task_id=%s result=%s",
                        temp_task_id,
                        result,
                        extra={'user_visible': 'critical'}
                    )

            return result
        finally:
            self._reset_task_runtime(runtime_tokens)


class DeepResearchTaskManagerPool:
    """Process-level pool of per-tenant DeepResearchTaskManager instances."""

    _lock = asyncio.Lock()
    _managers: dict[tuple[str, str], DeepResearchTaskManager] = {}

    @classmethod
    async def get_or_create(cls, tenant: tuple[str, str]) -> DeepResearchTaskManager:
        key = (
            (tenant[0] or "default").strip() or "default",
            (tenant[1] or "default").strip() or "default",
        )
        async with cls._lock:
            mgr = cls._managers.get(key)
            if mgr is None:
                mgr = DeepResearchTaskManager(service_id=key[0], agent_id=key[1])
                cls._managers[key] = mgr
            return mgr

    @classmethod
    async def remove(cls, service_id: str, agent_id: str) -> bool:
        """Evict one tenant DeepResearch manager and shut down its tasks."""
        key = (
            (str(service_id or "default").strip() or "default"),
            (str(agent_id or "default").strip() or "default"),
        )
        async with cls._lock:
            mgr = cls._managers.pop(key, None)
        if mgr is None:
            return False
        try:
            await mgr.shutdown()
        except Exception:
            logger.warning(
                "[DeepResearchTaskManagerPool] shutdown failed tenant=(%s, %s)",
                key[0],
                key[1],
                exc_info=True,
            )
        return True

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._managers.clear()


async def get_deepresearch_manager(scope: RuntimeScopeKey) -> DeepResearchTaskManager:
    """Return the DeepResearch manager for ``scope.tenant()``.

    ``scope`` is required. For the default tenant pass
    ``RuntimeScopeKey.from_ids("default", "default")`` (or ``RuntimeScopeKey()``).
    """
    if scope is None:
        raise TypeError(
            "get_deepresearch_manager(scope) requires a non-None scope; "
            "for default tenant pass RuntimeScopeKey.from_ids('default', 'default')"
        )
    return await DeepResearchTaskManagerPool.get_or_create(scope.tenant())



__all__ = [
    "DeepResearchTaskManager",
    "DeepResearchTaskManagerPool",
    "get_deepresearch_manager",
    "TaskStatus",
    "DeepResearchTask",
]
