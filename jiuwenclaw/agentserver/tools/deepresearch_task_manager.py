# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DeepResearch 任务池管理器.

支持异步执行 DeepResearch 任务，不阻塞 Agent 响应。
提供任务创建、状态查询、取消、结果获取等功能。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

from dotenv import load_dotenv

from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

from jiuwenclaw.agentserver.tools.deepresearch_plugin.convert_docx_online import convert_md_to_docx
from jiuwenclaw.agentserver.tools.deepresearch_plugin.convert_html_online import convert_md_to_html
from jiuwenclaw.utils import get_env_file

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

    @staticmethod
    def format_timestamp(timestamp: float | None) -> str | None:
        """将时间戳转换为本地时区的格式化时间字符串（YYYY-MM-DD HH:MM:SS）."""
        if timestamp is None:
            return None
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")


class DeepResearchTaskManager:
    """DeepResearch 任务池管理器（全局单例）.

    管理所有 DeepResearch 任务的创建、执行、状态查询、取消等功能。
    """

    _instance: DeepResearchTaskManager | None = None
    _lock = asyncio.Lock()

    def __new__(cls):
        """实现单例模式."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化任务池管理器."""
        if hasattr(self, '_initialized'):
            return

        self._tasks: Dict[str, DeepResearchTask] = {}
        self._task_handles: Dict[str, asyncio.Task] = {}
        self._initialized = True
        logger.info("[DeepResearchTaskManager] 初始化完成（全局单例）")

    @classmethod
    async def get_instance(cls) -> DeepResearchTaskManager:
        """获取全局单例实例（线程安全）."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def _load_config() -> Dict[str, str]:
        """从环境变量加载 DeepSearch 配置."""
        # 加载环境变量，确保读取最新的配置
        env_file = get_env_file()
        if env_file.exists():
            # Reload the env file for each task so updated values take effect immediately.
            load_dotenv(dotenv_path=env_file, override=True)
        else:
            logger.warning(f"[DeepResearchTaskManager] 环境变量文件不存在: {env_file}")

        llm_model_type = (os.getenv("DEEPSEARCH_LLM_MODEL_TYPE") or "").strip().lower()
        if not llm_model_type:
            llm_model_type = (os.getenv("MODEL_PROVIDER") or "openai").strip().lower()

        config = {
            "LLM_MODEL_NAME": os.getenv("DEEPSEARCH_LLM_MODEL_NAME") or os.getenv("MODEL_NAME", "gpt-4o"),
            "LLM_MODEL_TYPE": llm_model_type,
            "LLM_BASE_URL": os.getenv("DEEPSEARCH_LLM_BASE_URL") or os.getenv("API_BASE", "https://api.openai.com/v1"),
            "LLM_API_KEY": os.getenv("DEEPSEARCH_LLM_API_KEY") or os.getenv("API_KEY", ""),
            "WEB_SEARCH_ENGINE_NAME": os.getenv("DEEPSEARCH_WEB_SEARCH_ENGINE_NAME", "tavily"),
            "WEB_SEARCH_API_KEY": os.getenv("DEEPSEARCH_WEB_SEARCH_API_KEY", ""),
            "WEB_SEARCH_URL": os.getenv("DEEPSEARCH_WEB_SEARCH_URL", "https://api.tavily.com"),
            "MAX_WEB_SEARCH_RESULTS": os.getenv("DEEPSEARCH_MAX_WEB_SEARCH_RESULTS", "5"),
            "EXECUTION_METHOD": os.getenv("DEEPSEARCH_EXECUTION_METHOD", "parallel"),
            "OUTLINER_MAX_SECTION_NUM": os.getenv("DEEPSEARCH_OUTLINER_MAX_SECTION_NUM", "10"),
            "WORKFLOW_HUMAN_IN_THE_LOOP": os.getenv("DEEPSEARCH_WORKFLOW_HUMAN_IN_THE_LOOP", "False"),
            "OUTLINE_INTERACTION_ENABLED": os.getenv("DEEPSEARCH_OUTLINE_INTERACTION_ENABLED", "False"),
            "SOURCE_TRACER_INFER_SWITCHES": os.getenv("DEEPSEARCH_SOURCE_TRACER_INFER_SWITCHES", "True"),
        }
        # logger.info("[DeepResearchTaskManager] 加载 DeepResearch 配置: %s", config)
        return config

    @staticmethod
    def _validate_config(config: Dict[str, str]) -> tuple[bool, str]:
        """验证 DeepResearch 配置."""
        if not config["LLM_API_KEY"]:
            return False, (
                "DeepResearch 缺少 LLM_API_KEY。\n"
                "请在 ~/.jiuwenclaw/config/.env 中设置以下任一环境变量：\n"
                "  - DEEPSEARCH_LLM_API_KEY（推荐，DeepResearch 专属配置）\n"
                "  - API_KEY（全局配置，可复用）"
            )

        if not config["WEB_SEARCH_API_KEY"]:
            return False, (
                "DeepResearch 缺少 WEB_SEARCH_API_KEY。\n"
                "请在 ~/.jiuwenclaw/config/.env 中设置 DEEPSEARCH_WEB_SEARCH_API_KEY"
            )

        return True, "配置验证通过"

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
        """解析 infer_messages，返回可写入文件的 HTML 映射."""
        if not isinstance(infer_messages, list):
            return {}

        html_map: dict[str, str] = {}
        for item in infer_messages:
            if not isinstance(item, dict):
                continue

            infer_id = str(item.get("id", "")).strip()
            html_base64 = item.get("html_base64", "")
            if not infer_id or not html_base64:
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
    def _write_inference_html_files(report_file: str, infer_messages: Any) -> str | None:
        """将溯源推理图写入独立 HTML 文件，返回目录路径."""
        html_map = DeepResearchTaskManager._collect_inference_html(infer_messages)
        if not html_map:
            return None

        infer_dir = f"{report_file}_infer"
        os.makedirs(infer_dir, exist_ok=True)
        for infer_id, html_content in html_map.items():
            infer_file = os.path.join(infer_dir, f"inference_{infer_id}.html")
            with open(infer_file, "w", encoding="utf-8") as f:
                f.write(html_content)
        return infer_dir

    @staticmethod
    def _replace_inference_links(report_content: str, infer_dir: str | None) -> str:
        """将 #inference:N 链接替换为本地 HTML 超链接."""
        infer_dir_name = os.path.basename(infer_dir) if infer_dir else ""

        def repl(match: re.Match[str]) -> str:
            label = match.group(1)
            infer_id = match.group(2)
            if not infer_dir_name or not infer_dir:
                return label

            infer_path = os.path.join(infer_dir, f"inference_{infer_id}.html")
            if not os.path.exists(infer_path):
                return label

            relative_link = os.path.join(infer_dir_name, f"inference_{infer_id}.html").replace("\\", "/")
            return f"[{label}]({relative_link})"

        return INFERENCE_LINK_RE.sub(repl, report_content)

    @staticmethod
    def _build_report_content(data: Any, report_file: str) -> tuple[str, str | None]:
        """根据 DeepResearch 结果构建最终落盘的报告内容."""
        infer_dir = None
        if isinstance(data, dict) and "response_content" in data:
            report_content = data.get("response_content", "")
            if report_content == "":
                raise ValueError("response_content is empty")

            try:
                infer_dir = DeepResearchTaskManager._write_inference_html_files(
                    report_file,
                    data.get("infer_messages", []),
                )
            except Exception as exc:
                logger.warning(
                    "[DeepResearchTaskManager] Failed to write inference html files. "
                    "report_file=%s error=%s",
                    report_file,
                    exc,
                )
                infer_dir = None
            report_content = DeepResearchTaskManager._replace_inference_links(report_content, infer_dir)
            return report_content, infer_dir

        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False, indent=2), infer_dir
        return str(data), infer_dir

    @staticmethod
    def _write_report_artifacts(
        data: Any,
        file_name: str,
        output_dir: str = SAVE_REPORT_PATH,
        *,
        task_id: str = "",
    ) -> dict[str, str]:
        """写出 markdown/html/docx 报告及推理图目录."""
        os.makedirs(output_dir, exist_ok=True)

        base_name = DeepResearchTaskManager._strip_known_suffix(file_name)
        report_file = os.path.join(output_dir, f"report_{base_name}")
        report_file_md = f"{report_file}.md"
        report_file_html = f"{report_file}.html"
        report_file_docx = f"{report_file}.docx"

        report_content, infer_dir = DeepResearchTaskManager._build_report_content(data, report_file)

        with open(report_file_md, "w", encoding="utf-8") as f:
            f.write(report_content)

        artifacts = {"md": report_file_md}
        if infer_dir:
            artifacts["infer_dir"] = infer_dir

        try:
            convert_md_to_html(report_file_md, report_file_html)
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] Optional html report generation failed. "
                "task_id=%s output=%s error=%s",
                task_id,
                report_file_html,
                exc,
            )
        else:
            artifacts["html"] = report_file_html

        try:
            convert_md_to_docx(report_file_md, report_file_docx)
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] Optional docx report generation failed. "
                "task_id=%s output=%s error=%s",
                task_id,
                report_file_docx,
                exc,
            )
        else:
            artifacts["docx"] = report_file_docx

        return artifacts

    @staticmethod
    def _format_report_result(report_paths: dict[str, str]) -> str:
        """鏍规嵁鎴愬姛鐢熸垚鐨勪骇鐗╃粍瑁呯粨鏋滄枃妗?"""
        parts = [f"markdown报告已保存到{report_paths['md']}"]
        if report_paths.get("html"):
            parts.append(f"html报告已保存到{report_paths['html']}")
        if report_paths.get("docx"):
            parts.append(f"docx报告已保存到{report_paths['docx']}")
        if report_paths.get("infer_dir"):
            parts.append(f"溯源推理图已保存到{report_paths['infer_dir']}")
        return "; ".join(parts)

    async def _run_jiuwen_workflow(self, query: str, agent_config: Dict, report_template: str) -> Any:
        """运行 openJiuwen-DeepResearch 工作流."""
        agent_factory = AgentFactory()
        agent = agent_factory.create_agent(agent_config)
        last_report = None
        async for chunk in agent.run(
            message=query,
            conversation_id=str(secrets.token_hex(16)),
            report_template=report_template,
            interrupt_feedback="",
            agent_config=agent_config
        ):
            logger.debug("[DeepResearchTaskManager] Stream message from node: %s", chunk)
            chunk_content = json.loads(chunk)
            report_result = parse_endnode_content(chunk_content)
            if report_result:
                last_report = report_result
                logger.debug("[DeepResearchTaskManager] Final Report is: %s", report_result)
        return last_report

    async def _execute_task(
        self,
        task_id: str,
        query: str,
        file_name: str,
        **kwargs,
    ) -> None:
        """执行 DeepResearch 任务（后台协程）."""
        task = self._tasks[task_id]
        task.started_at = time.time()
        task.status = TaskStatus.RUNNING

        logger.info(
            "[DeepResearchTaskManager] 开始执行任务 task_id=%s query=%s",
            task_id,
            query[:80] + "..." if len(query) > 80 else query,
            extra={'user_visible': 'critical'}
        )

        try:
            # 1. 加载配置
            config = DeepResearchTaskManager._load_config()
            
            # 2. 验证配置
            config_valid, config_msg = DeepResearchTaskManager._validate_config(config)
            if not config_valid:
                raise ValueError(config_msg)

            # 3. 设置 SSL 配置
            os.environ["LLM_SSL_VERIFY"] = "false"
            os.environ["LLM_SSL_CERT"] = ""
            os.environ["TOOL_SSL_VERIFY"] = "false"
            os.environ["TOOL_SSL_CERT"] = ""

            # 4. 解析 LLM 配置
            current_agent_config = Config().agent_config.model_dump()
            current_agent_config["llm_config"]["general"] = {}
            current_agent_config["llm_config"]["general"]["model_name"] = config["LLM_MODEL_NAME"]
            current_agent_config["llm_config"]["general"]["model_type"] = config["LLM_MODEL_TYPE"]
            current_agent_config["llm_config"]["general"]["base_url"] = config["LLM_BASE_URL"]
            current_agent_config["llm_config"]["general"]["api_key"] = bytearray(config["LLM_API_KEY"],
                                                                                 encoding="utf-8")
            current_agent_config["llm_config"]["general"]["verify_ssl"] = False

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
            if config["EXECUTION_METHOD"] == ExecutionMethod.DEPENDENCY_DRIVING.value:
                current_agent_config["execution_method"] = ExecutionMethod.DEPENDENCY_DRIVING.value
            else:
                current_agent_config["execution_method"] = ExecutionMethod.PARALLEL.value

            # 6. 执行工作流
            data = await self._run_jiuwen_workflow(query, current_agent_config, "")

            if data:
                report_paths = await asyncio.to_thread(
                    self._write_report_artifacts,
                    data,
                    file_name,
                    SAVE_REPORT_PATH,
                    task_id=task_id,
                )
                result = self._format_report_result(report_paths)

                """
                    result = (
                    f"markdown报告已保存到{report_paths['md']}; "
                    f"html报告已保存到{report_paths['html']}; "
                    f"docx报告已保存到{report_paths['docx']}"
                )
                    if report_paths["infer_dir"]:
                    result += f"; 溯源推理图已保存到{report_paths['infer_dir']}"

                """
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
            task.completed_at = time.time()
            self._task_handles.pop(task_id, None)

            # 发送 WebSocket 通知
           
            await self._notify_completion(task)

    async def _notify_completion(self, task: DeepResearchTask) -> None:
        """通过 WebSocket 发送任务完成通知."""
        try:
            from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
            server = AgentWebSocketServer.get_instance()
        except RuntimeError as e:
            logger.warning(
                "[DeepResearchTaskManager] WebSocketServer 未初始化，跳过通知: %s",
                e,
            )
            return

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
            await server.send_push(msg)
        except Exception as exc:
            logger.warning(
                "[DeepResearchTaskManager] 发送 WebSocket 通知失败 task_id=%s error=%s",
                task.task_id,
                exc,
            )

    async def create_task(
        self,
        query: str,
        file_name: str,
        session_id: str = "",
        channel_id: str = "",
        request_id: str = "",
    ) -> str:
        """创建并启动 DeepResearch 任务.

        Args:
            query: 研究查询
            file_name: 报告文件名，不带后缀
            session_id: 会话 ID（用于通知）
            channel_id: 渠道 ID（用于通知）
            request_id: 请求 ID（用于通知）

        Returns:
            任务 ID
        """
        task_id = f"dr_{time.monotonic_ns()}_{secrets.token_hex(4)}"

        task = DeepResearchTask(
            task_id=task_id,
            query=query,
            file_name=file_name,
            status=TaskStatus.RUNNING,
            created_at=time.time(),
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
        )

        self._tasks[task_id] = task

        # 创建后台协程
        coro = self._execute_task(
            task_id=task_id,
            query=query,
            file_name=file_name,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
        )
        task_handle = asyncio.create_task(coro)
        self._task_handles[task_id] = task_handle

        logger.info(
            "[DeepResearchTaskManager] 创建深度研究任务：%s task_id=%s query=%s",
            file_name,
            task_id,
            query[:80] + "..." if len(query) > 80 else query,
            extra={'user_visible': 'critical'}
        )

        return task_id

    async def get_task_status(self, task_id: str) -> Dict[str, Any] | None:
        """获取任务状态.

        Args:
            task_id: 任务 ID

        Returns:
            任务信息字典，如果任务不存在则返回 None
        """
        task = self._tasks.get(task_id)
        if task is None:
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

    async def list_tasks(self, status_filter: str | None = None) -> List[Dict[str, Any]]:
        """列出所有任务.

        Args:
            status_filter: 可选的状态过滤器（running/completed/cancelled/error）

        Returns:
            任务信息列表
        """
        tasks = []
        for task in self._tasks.values():
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

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务.

        Args:
            task_id: 任务 ID

        Returns:
            是否成功取消
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.warning(
                "[DeepResearchTaskManager] 取消任务失败：任务不存在 task_id=%s" % task_id
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

    async def get_task_result(self, task_id: str) -> str | None:
        """获取任务结果.

        Args:
            task_id: 任务 ID

        Returns:
            任务结果字符串，如果任务未完成则返回 None
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        if task.status != TaskStatus.COMPLETED:
            return None

        return task.result

    async def run_task_and_wait(
        self,
        query: str,
        file_name: str,
        session_id: str = "",
        channel_id: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        """创建任务并等待执行结束，适合 CLI 或脚本入口直接调用."""
        task_id = await self.create_task(
            query=query,
            file_name=file_name,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
        )

        task_handle = self._task_handles.get(task_id)
        if task_handle is not None:
            await asyncio.gather(task_handle, return_exceptions=True)

        task_info = await self.get_task_status(task_id)
        if task_info is None:
            raise RuntimeError(f"DeepResearch task not found after execution: {task_id}")
        return task_info





__all__ = [
    "DeepResearchTaskManager",
    "TaskStatus",
    "DeepResearchTask",
]
