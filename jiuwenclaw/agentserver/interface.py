# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw Facade - 统一入口与 SDK 适配层.

此模块提供：
- 统一的 JiuWenClaw 公开 API
- SDK 工厂路由（通过环境变量选择）
- 公共编排逻辑（session 队列、Skills 路由、heartbeat、流式包装）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator, Tuple

from dotenv import load_dotenv

from jiuwenclaw.agentserver.agent_adapters import (
    AgentAdapter,
    create_adapter,
    resolve_sdk_choice,
)
from jiuwenclaw.agentserver.memory.config import get_memory_mode


logger = logging.getLogger(__name__)


def _truncate_for_log(text: str, max_len: int = 200) -> str:
    """截断文本用于日志输出.

    Args:
        text: 原始文本
        max_len: 最大长度，默认200字符

    Returns:
        截断后的文本，超过max_len时保留开头和结尾，中间显示截断信息
    """
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    # 保留开头150字符 + 截断标记 + 结尾50字符
    head_len = 150
    tail_len = 50
    truncated_count = len(text) - max_len
    return f"{text[:head_len]}...[truncated {truncated_count} chars]{text[-tail_len:]}"
from jiuwenclaw.agentserver.session_history import append_history_record
from jiuwenclaw.agentserver.session_manager import SessionManager
from jiuwenclaw.agentserver.session_metadata import (
    _safe_session_subdir,
    get_resolved_project_dir,
    update_session_metadata,
    validate_project_dir,
)
from jiuwenclaw.agentserver.skill_manager import SkillManager
from jiuwenclaw.config import get_config
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.schema.hooks_context import MemoryHookContext
from jiuwenclaw.schema.message import EventType, ReqMethod
from jiuwenclaw.utils import (
    get_agent_home_dir,
    get_agent_sessions_relative_dir,
    get_agent_workspace_dir,
    get_agent_workspace_relative_dir,
    get_env_file,
    get_user_workspace_dir,
)

load_dotenv(dotenv_path=get_env_file())

# _session_project_dir 进程内缓存上限（超出则淘汰最久未访问；磁盘 metadata 仍为权威来源）
_SESSION_PROJECT_DIR_CACHE_MAX = 4096

_SKILL_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.SKILLS_LIST: "handle_skills_list",
    ReqMethod.SKILLS_INSTALLED: "handle_skills_installed",
    ReqMethod.SKILLS_GET: "handle_skills_get",
    ReqMethod.SKILLS_MARKETPLACE_LIST: "handle_skills_marketplace_list",
    ReqMethod.SKILLS_INSTALL: "handle_skills_install",
    ReqMethod.SKILLS_UNINSTALL: "handle_skills_uninstall",
    ReqMethod.SKILLS_IMPORT_LOCAL: "handle_skills_import_local",
    ReqMethod.SKILLS_MARKETPLACE_ADD: "handle_skills_marketplace_add",
    ReqMethod.SKILLS_MARKETPLACE_REMOVE: "handle_skills_marketplace_remove",
    ReqMethod.SKILLS_MARKETPLACE_TOGGLE: "handle_skills_marketplace_toggle",
    ReqMethod.SKILLS_SKILLNET_SEARCH: "handle_skills_skillnet_search",
    ReqMethod.SKILLS_SKILLNET_INSTALL: "handle_skills_skillnet_install",
    ReqMethod.SKILLS_SKILLNET_INSTALL_STATUS: "handle_skills_skillnet_install_status",
    ReqMethod.SKILLS_SKILLNET_EVALUATE: "handle_skills_skillnet_evaluate",
    ReqMethod.SKILLS_CLAWHUB_GET_TOKEN: "handle_skills_clawhub_get_token",
    ReqMethod.SKILLS_CLAWHUB_SET_TOKEN: "handle_skills_clawhub_set_token",
    ReqMethod.SKILLS_CLAWHUB_SEARCH: "handle_skills_clawhub_search",
    ReqMethod.SKILLS_CLAWHUB_DOWNLOAD: "handle_skills_clawhub_download",
    ReqMethod.SKILLS_EVOLUTION_STATUS: "handle_skills_evolution_status",
    ReqMethod.SKILLS_EVOLUTION_GET: "handle_skills_evolution_get",
    ReqMethod.SKILLS_EVOLUTION_SAVE: "handle_skills_evolution_save",
}

# Tools 管理请求路由表
_TOOL_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.TOOLS_ADD: "handle_tools_add",
}

# SkillDev 请求方法集合（统一委托给 SkillDevService）
_SKILLDEV_METHODS: frozenset[ReqMethod] = frozenset(
    m for m in ReqMethod if m.value.startswith("skilldev.")
)

# Preserve cross-service context-size hints if they are present in request.params.
_CONTEXT_SIZE_HINT_KEYS: tuple[str, ...] = (
    "context_size",
    "context_window_size",
    "context_window",
    "max_context",
    "max_context_size",
    "max_context_message_num",
    "max_input_tokens",
    "max_prompt_tokens",
    "n_ctx",
    "ctx_len",
)


def build_user_prompt(
    content: str,
    files: dict | list,
    channel: str,
    language: str,
    *,
    supplementary_info: str | None = None,
) -> str:
    """Build user prompt for the agent."""
    if language == "zh":
        prompt = "你收到一条消息：\n"
    else:
        prompt = "You receive a new message:\n"
    if channel in ["cron", "heartbeat"]:
        return prompt + json.dumps(
            {
                "source": "system",
                "preferred_response_language": language,
                "content": content,
                "type": channel,
            },
            ensure_ascii=False,
        )

    # 提取 Agent 需要的文件信息（只保留本地路径和相关元数据，移除 uri）
    def extract_file_info(file_ref: dict) -> dict:
        """Extract file information that Agent needs."""
        info = {}
        if isinstance(file_ref, dict):
            # 只保留 Agent 需要的字段
            if "name" in file_ref:
                info["name"] = file_ref["name"]
            if "path" in file_ref:
                info["path"] = file_ref["path"]
            if "type" in file_ref:
                info["type"] = file_ref["type"]
            # 移除 uri、storage_path 等内部实现细节
        return info

    # 向后兼容：dict → list 自动转换
    if isinstance(files, dict):
        files = list(files.values())

    # 只处理 list 格式
    files_for_agent = [extract_file_info(f) for f in files if isinstance(f, dict)] if isinstance(files, list) else []

    # 空容器统一输出 "{}"（对 Agent 来说空 dict 和空 list 都表示无文件）
    files_json = json.dumps(files_for_agent, ensure_ascii=False) if files_for_agent else "{}"
    payload: dict[str, Any] = {
        "source": channel,
        "preferred_response_language": language,
        "content": content,
        "files_updated_by_user": files_json,
        "type": "user input",
    }
    if supplementary_info:
        payload["supplementary_info"] = supplementary_info
    return prompt + json.dumps(payload, ensure_ascii=False)


class JiuWenClaw:
    """JiuWenClaw 统一门面.

    提供：
    - SDK 工厂路由
    - 统一对外 API（create_instance, reload_agent_config, process_message, process_message_stream）
    - 公共编排（session 队列、Skills / Tools 路由、heartbeat、流式包装）
    """

    def __init__(
        self,
        user_workspace_dir: str | None = None,
        agent_id: str | None = None,
        service_id: str | None = None,
    ) -> None:
        self._adapter: AgentAdapter | None = None
        self._sdk_name: str | None = None
        self._agent_id = agent_id
        self._service_id = service_id
        user_workspace_path = Path(user_workspace_dir) if user_workspace_dir else get_user_workspace_dir()
        self._workspace_dir = str(user_workspace_path / get_agent_workspace_relative_dir())
        self._sessions_dir = user_workspace_path / get_agent_sessions_relative_dir()
        self._skill_manager = SkillManager(workspace_dir=self._workspace_dir)
        self._session_manager = SessionManager()
        # session_id -> 已解析的 project_dir（与 metadata 绑定，见 _effective_project_dir_for_session）
        self._session_project_dir: OrderedDict[str, str] = OrderedDict()
        # SkillDev 模式：懒初始化，首次 skilldev.* 请求时构造
        self._skilldev_service = None
        # Storage 服务：懒初始化，首次文件操作时构造
        self._storage = None
        self._tool_manager = None
        self._session_id: str | None = None

    def _get_skilldev_service(self):
        """懒初始化并返回 SkillDevService 实例.

        SkillDevService 是无状态的，单实例即可服务所有请求。
        首次调用时从当前 JiuWenClaw 配置中提取最小依赖并构造。
        """
        if self._skilldev_service is not None:
            return self._skilldev_service

        from jiuwenclaw.agentserver.skilldev import SkillDevDeps, SkillDevService, StateStore, WorkspaceProvider
        from jiuwenclaw.utils import get_workspace_dir

        skilldev_base = get_workspace_dir() / "skilldev"
        state_store = StateStore(skilldev_base)
        workspace_provider = WorkspaceProvider(skilldev_base)

        config = get_config()
        model_configs = config.get("models", {})
        default_model = model_configs.get("default", {})

        deps = SkillDevDeps(
            model_name=default_model.get("model_client_config", {}).get("model_name", ""),
            model_client_config=default_model.get("model_client_config", {}),
            model_config_obj=default_model.get("model_config_obj", {}),
            sysop_config=None,
            state_store=state_store,
            workspace_provider=workspace_provider,
        )
        self._skilldev_service = SkillDevService(deps)
        logger.info("[JiuWenClaw] SkillDevService 初始化完成")
        return self._skilldev_service

    async def _get_storage(self):
        """懒初始化并返回 StorageService 实例.

        StorageService 是单例，首次调用时根据配置创建后端实例。
        """
        if self._storage is not None:
            return self._storage

        from jiuwenclaw.storage import StorageService

        self._storage = await StorageService.get_instance()
        logger.info("[JiuWenClaw] StorageService 初始化完成")
        return self._storage

    async def _ensure_adapter(self) -> AgentAdapter:
        """确保 adapter 已初始化，如果未初始化则根据环境变量创建."""
        if self._adapter is None:
            self._sdk_name = resolve_sdk_choice()
            self._adapter = await create_adapter(
                self._sdk_name,
                workspace_dir=self._workspace_dir,
                agent_id=self._agent_id,
                service_id=self._service_id,
            )
            if hasattr(self._adapter, "set_skill_manager"):
                self._adapter.set_skill_manager(self._skill_manager)
            self._skill_manager.set_skillnet_install_complete_hook(
                self.create_instance
            )
            logger.info("[JiuWenClaw] Initialized adapter: sdk=%s, workspace_dir=%s",
                        self._sdk_name, self._workspace_dir)
        return self._adapter

    def _get_tool_manager(self):
        """懒初始化 ``ToolManager``（依赖 ``get_instance`` 上的底层 Agent）。"""
        from jiuwenclaw.agentserver.tool_manager import ToolManager

        if self._tool_manager is None:
            self._tool_manager = ToolManager(
                get_agent=lambda: self.get_instance(),
                get_tools_dir=lambda: Path(self._workspace_dir) / "tools",
            )
        return self._tool_manager

    def _session_project_dir_get(self, session_id: str) -> str | None:
        d = self._session_project_dir
        if session_id not in d:
            return None
        d.move_to_end(session_id)
        return d[session_id]

    def _session_project_dir_set(self, session_id: str, value: str) -> None:
        d = self._session_project_dir
        d.pop(session_id, None)
        d[session_id] = value
        while len(d) > _SESSION_PROJECT_DIR_CACHE_MAX:
            d.popitem(last=False)

    def _effective_project_dir_for_session(
        self,
        session_id: str,
        param_project_dir: str | None,
    ) -> str:
        """解析本会话工程目录：首次非空 params.project_dir 绑定并落 metadata；冲突保留首次。"""
        raw = (param_project_dir or "").strip()
        sessions_root = str(self._sessions_dir)
        default = str(get_agent_workspace_dir().resolve())
        can_persist = _safe_session_subdir(session_id, sessions_root) is not None
        if not raw:
            existing = self._session_project_dir_get(session_id)
            if existing is not None:
                return existing
            if not can_persist:
                logger.warning(
                    "[JiuWenClaw] 跳过 metadata 读取 project_dir：非法 session_id=%r",
                    session_id,
                )
                return default
            loaded = get_resolved_project_dir(session_id, sessions_root)
            self._session_project_dir_set(session_id, loaded)
            return loaded

        resolved = str(validate_project_dir(raw, default=Path(default)))
        existing = self._session_project_dir_get(session_id)
        if existing is None:
            self._session_project_dir_set(session_id, resolved)
            if can_persist:
                update_session_metadata(
                    session_id=session_id,
                    project_dir=resolved,
                    sessions_root=self._sessions_dir,
                )
            else:
                logger.warning(
                    "[JiuWenClaw] 跳过 metadata 写入 project_dir：非法 session_id=%r",
                    session_id,
                )
            return resolved
        if existing == resolved:
            if can_persist:
                update_session_metadata(
                    session_id=session_id,
                    project_dir=existing,
                    sessions_root=self._sessions_dir,
                )
            return existing
        logger.warning(
            "[JiuWenClaw] 忽略冲突的 project_dir：session_id=%s 保留 %r，收到 %r",
            session_id,
            existing,
            resolved,
        )
        return existing

    def _apply_effective_project_dir_to_request(
        self,
        request: AgentRequest,
        session_id: str,
        inputs: dict[str, Any],
    ) -> None:
        """写入 effective_project_dir 到 request.metadata 与 inputs。"""
        param = None
        if isinstance(request.params, dict):
            pd = request.params.get("project_dir")
            if isinstance(pd, str):
                param = pd
        effective = self._effective_project_dir_for_session(session_id, param)
        md = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        md["effective_project_dir"] = effective
        request.metadata = md
        inputs["effective_project_dir"] = effective

    async def create_instance(
        self,
        config: dict[str, Any] | None = None,
        *,
        mode: str = "agent",
        session_id: str | None = None,
    ) -> None:
        """初始化 Agent 实例.

        Args:
            config: 可选配置，透传给底层 adapter.
            mode: 实例化模式，"claw"（默认）或 "code"，透传给底层 adapter.
            session_id: 会话 ID，用于隔离各 session 的 outer rail 回调命名空间。
        """
        if session_id is not None:
            self._session_id = session_id.strip() or None
        adapter = await self._ensure_adapter()
        await adapter.create_instance(
            config,
            mode=mode,
            session_id=session_id or getattr(self, "_session_id", None),
        )
        logger.info("[JiuWenClaw] Agent instance created: sdk=%s", self._sdk_name)

        project_mcp_names: set[str] = set()
        host_project_mcp_path = self._get_tool_manager().find_host_project_mcp_json()
        try:
            if host_project_mcp_path is None:
                logger.info(
                    "[JiuWenClaw] 未找到宿主项目 .mcp.json，跳过 MCP 工具导入: CAT_CAFE_MCP_CWD=%s",
                    os.getenv("CAT_CAFE_MCP_CWD", ""),
                )
            else:
                project_mcp_payload = await self._get_tool_manager().load_project_mcp_json(host_project_mcp_path)
                project_mcp_names = {
                    item["name"]
                    for item in project_mcp_payload.get("saved", [])
                    if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
                }
                if not project_mcp_payload.get("skipped"):
                    logger.info(
                        "[JiuWenClaw] 已从宿主项目 .mcp.json 导入 MCP 工具: count=%s source=%s",
                        len(project_mcp_names),
                        project_mcp_payload.get("source", str(host_project_mcp_path)),
                    )
        except Exception as exc:
            logger.warning("[JiuWenClaw] 从宿主项目 .mcp.json 导入 MCP 工具失败: %s", exc)

        try:
            await self._get_tool_manager().load_tools_from_disk(skip_server_names=project_mcp_names)
        except Exception as exc:
            logger.warning("[JiuWenClaw] 从 agent/tools 加载落盘 MCP 工具失败: %s", exc)
        self._register_extension_tools()

    def _register_extension_tools(self) -> None:
        """将 ExtensionRegistry 登记的扩展本地工具挂到 Runner 与 ability_manager。"""
        instance = self.get_instance()
        if instance is None:
            return
        try:
            from openjiuwen.core.foundation.tool import ToolCard
            from openjiuwen.core.runner import Runner

            from jiuwenclaw.extensions.sdk.local_tool_builder import make_extension_tools

            ext_reg = ExtensionRegistry.get_instance()
            existing_ext_tool_names: set[str] = {
                ab.name
                for ab in instance.ability_manager.list()
                if isinstance(ab, ToolCard)
            }
            for ext_tool in make_extension_tools(ext_reg.extension_local_tool_entries):
                tname = ext_tool.card.name
                tid = ext_tool.card.id
                if tname in existing_ext_tool_names:
                    logger.warning(
                        "[JiuWenClaw] extension tool name conflicts with existing tool, skip: %s",
                        tname,
                    )
                    continue
                try:
                    if Runner.resource_mgr.get_tool(tid):
                        logger.warning(
                            "[JiuWenClaw] extension tool id already in resource_mgr, skip: %s",
                            tid,
                        )
                        continue
                except Exception as exc:
                    logger.debug(
                        "[JiuWenClaw] extension tool get_tool probe failed for %s, will try add: %s",
                        tid,
                        exc,
                    )
                try:
                    Runner.resource_mgr.add_tool(ext_tool)
                    instance.ability_manager.add(ext_tool.card)
                    existing_ext_tool_names.add(tname)
                    logger.info(
                        "[JiuWenClaw] extension tool add success, name : %s", tname,
                    )
                except Exception as exc:
                    logger.warning(
                        "[JiuWenClaw] extension tool register failed (%s): %s",
                        tname,
                        exc,
                    )
        except RuntimeError:
            logger.warning("[JiuWenClaw] ExtensionRegistry unavailable, skip extension tools")

    async def reload_agent_config(
            self,
            config_base: dict[str, Any] | None = None,
            env_overrides: dict[str, Any] | None = None,
    ) -> None:
        """从配置重新加载.

        Args:
            config_base: 可选的完整配置快照；传入时优先使用它而不是读取本地 config.yaml。
            env_overrides: 可选的环境变量增量；仅覆盖请求中出现的 key。
        """
        adapter = await self._ensure_adapter()
        await adapter.reload_agent_config(config_base, env_overrides)
        logger.info("[JiuWenClaw] Agent config reloaded: sdk=%s", self._sdk_name)

    async def prepare_files_for_agent(self, request: AgentRequest) -> None:
        """预处理文件：从对象存储下载文件到本地 workspace.

        从 request.params.files 中提取文件引用，下载到本地 workspace，
        并更新 files 字段添加 path 字段供 Agent 访问。
        同时创建 input_dir 和 output_dir 目录，并将路径写入 request.metadata。

        Args:
            request: AgentRequest，会被原地修改
        """

        files = request.params.get("files")
        if not files:
            # 即使没有文件，也需要创建 input/output 目录
            # 统一提取 chat_id 和 channel_type
            cleaned_chat_id, channel_type = self._extract_chat_id(request)

            # 提取用户 ID
            user_id = request.session_id or "default"
            if request.metadata and isinstance(request.metadata, dict):
                user_id = request.metadata.get("user_id", user_id)

            # 检测路径重复异常：user_id == chat_id 可能导致重复目录结构
            if user_id == cleaned_chat_id:
                logger.warning(
                    f"[JiuWenClaw] Path duplication detected: user_id == chat_id ({user_id}). "
                    f"This may cause duplicate directory structure. "
                    f"Please check frontend data source for user_id and chat_id."
                )

            # 计算 base_dir（检测对话模式 vs 项目模式）
            base_dir = Path(self._workspace_dir)  # 对话模式默认值
            effective_project_dir = None
            if request.metadata and isinstance(request.metadata, dict):
                effective_project_dir = request.metadata.get("effective_project_dir")

            if effective_project_dir and effective_project_dir != self._workspace_dir:
                # 项目模式：使用项目目录作为 base_dir
                base_dir = Path(effective_project_dir)
                logger.info(
                    f"[JiuWenClaw] 项目模式: base_dir={base_dir} "
                    f"(effective_project_dir={effective_project_dir})"
                )
            else:
                logger.info(f"[JiuWenClaw] 对话模式: base_dir={base_dir}")

            # 创建 input_dir 和 output_dir 目录
            input_dir = base_dir / "files" / user_id / cleaned_chat_id / "input"
            output_dir = base_dir / "files" / user_id / cleaned_chat_id / "output"

            try:
                input_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)

                # 将 input_dir 和 output_dir 写入 request.metadata
                md = dict(request.metadata) if isinstance(request.metadata, dict) else {}
                md["input_dir"] = str(input_dir)
                md["output_dir"] = str(output_dir)
                request.metadata = md

                logger.info(
                    f"[JiuWenClaw] 目录创建完成: input_dir={input_dir} output_dir={output_dir}"
                )

                #  诊断日志：观察 output_dir 写入 metadata 的实际值
                logger.info(
                    f"[JiuWenClaw]  DIAGNOSTIC: output_dir 已写入 request.metadata "
                    f"| request_id={request.request_id} "
                    f"| metadata.output_dir={request.metadata.get('output_dir')} "
                    f"| user_id={user_id} chat_id={cleaned_chat_id}"
                )
            except Exception as e:
                logger.error(
                    f"[JiuWenClaw] 目录创建失败: input_dir={input_dir} output_dir={output_dir} error={e}"
                )
            return

        # 向后兼容：dict → list 自动转换
        if isinstance(files, dict):
            files = [
                {"uri": v.get("uri"), "name": v.get("name"), **v}
                for k, v in files.items() if isinstance(v, dict) and v.get("uri")
            ]
            request.params["files"] = files
            logger.info(f"[JiuWenClaw] 文件格式转换: dict → list (count={len(files)})")

        # 只处理 list 格式
        if not isinstance(files, list):
            logger.warning(f"[JiuWenClaw] files 格式不支持: {type(files)}")
            return

        # 获取存储服务
        storage = await self._get_storage()

        # 统一提取 chat_id 和 channel_type
        cleaned_chat_id, channel_type = self._extract_chat_id(request)

        # 提取用户 ID
        user_id = request.session_id or "default"
        if request.metadata and isinstance(request.metadata, dict):
            user_id = request.metadata.get("user_id", user_id)

        # 检测路径重复异常：user_id == chat_id 可能导致重复目录结构
        if user_id == cleaned_chat_id:
            logger.warning(
                f"[JiuWenClaw] Path duplication detected: user_id == chat_id ({user_id}). "
                f"This may cause duplicate directory structure. "
                f"Please check frontend data source for user_id and chat_id."
            )

        # 计算 base_dir（检测对话模式 vs 项目模式）
        base_dir = Path(self._workspace_dir)  # 对话模式默认值
        effective_project_dir = None
        if request.metadata and isinstance(request.metadata, dict):
            effective_project_dir = request.metadata.get("effective_project_dir")

        if effective_project_dir and effective_project_dir != self._workspace_dir:
            # 项目模式：使用项目目录作为 base_dir
            base_dir = Path(effective_project_dir)
            logger.info(
                f"[JiuWenClaw] 项目模式: base_dir={base_dir} "
                f"(effective_project_dir={effective_project_dir})"
            )
        else:
            logger.info(f"[JiuWenClaw] 对话模式: base_dir={base_dir}")

        # 创建 input_dir 和 output_dir 目录
        input_dir = base_dir / "files" / user_id / cleaned_chat_id / "input"
        output_dir = base_dir / "files" / user_id / cleaned_chat_id / "output"

        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            # 将 input_dir 和 output_dir 写入 request.metadata
            md = dict(request.metadata) if isinstance(request.metadata, dict) else {}
            md["input_dir"] = str(input_dir)
            md["output_dir"] = str(output_dir)
            request.metadata = md

            logger.info(
                f"[JiuWenClaw] 目录创建完成: input_dir={input_dir} output_dir={output_dir}"
            )
        except Exception as e:
            logger.error(
                f"[JiuWenClaw] 目录创建失败: input_dir={input_dir} output_dir={output_dir} error={e}"
            )
            return

        # 处理每个文件（统一为 list 格式）
        for file_ref in files:
            if not isinstance(file_ref, dict):
                continue

            uri = file_ref.get("uri")
            if not uri:
                continue

            filename = file_ref.get("name", "file")
            local_path = str(input_dir / filename)

            try:
                await storage.download_file(uri, local_path)
                file_ref["path"] = local_path
                logger.info(f"[JiuWenClaw] 文件已下载: {uri} -> {local_path}")
            except Exception as e:
                logger.error(f"[JiuWenClaw] 文件下载失败: {uri}: {e}")
                continue

    async def upload_agent_files(
        self,
        response: AgentResponse,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> None:
        """后处理文件：上传 Agent 生成的文件到对象存储.

        从 response.payload 中提取文件路径，上传到对象存储，
        并更新 response 添加 uri 字段供前端访问。

        Args:
            response: AgentResponse，会被原地修改
            user_id: 用户 ID（用于对象存储路径隔离）
            chat_id: 会话 ID（用于会话级别文件隔离）
            channel_type: 渠道类型
        """

        if not response.payload or not isinstance(response.payload, dict):
            return

        # 检查 payload.files 字段（统一结构）
        files_data = response.payload.get("files")

        if not files_data:
            return

        # 获取存储服务
        storage = await self._get_storage()

        # 只处理 list 格式
        if not isinstance(files_data, list):
            return

        #  诊断日志：观察 Agent 生成的文件路径
        logger.info(
            "[JiuWenClaw]  DIAGNOSTIC: Agent 生成的文件列表 "
            "| request_id=%s "
            "| files_count=%d "
            "| file_paths=[%s]",
            response.request_id,
            len(files_data),
            ", ".join(f.get("path", "?") for f in files_data if isinstance(f, dict)),
        )

        # 上传每个文件，标准化 file_info 结构为 {path, name, uri}
        for file_info in files_data:
            if not isinstance(file_info, dict):
                continue

            # 标准化结构：提取 path（必需字段）
            local_path = file_info.get("path")
            if not local_path:
                continue

            # 检查文件是否存在
            if not Path(local_path).exists():
                logger.warning(f"[JiuWenClaw] 文件不存在，跳过上传: {local_path}")
                continue

            try:
                # 上传文件
                uri = await storage.upload_file(local_path, user_id, chat_id, channel_type)

                # 标准化 file_info 结构
                file_info["uri"] = uri
                # 确保 name 字段存在（如果缺失，从 path 中提取）
                if "name" not in file_info:
                    file_info["name"] = Path(local_path).name

                logger.info(
                    f"[JiuWenClaw] 文件已上传: {local_path} -> {uri} "
                    f"(user={user_id}, channel={channel_type}, chat={chat_id})"
                )

            except Exception as e:
                logger.error(
                    f"[JiuWenClaw] 文件上传失败: {local_path}: {e} "
                    f"(user={user_id}, channel={channel_type}, chat={chat_id})"
                )

    @staticmethod
    def _extract_chat_id(request: AgentRequest) -> tuple[str, str]:
        """从请求中统一提取 chat_id 和 channel_type.

        根据 channel 类型提取对应的 chat_id，并进行清理。

        Args:
            request: AgentRequest 请求对象

        Returns:
            tuple[str, str]: (cleaned_chat_id, channel_type)
        """
        from jiuwenclaw.storage.utils import sanitize_chat_id

        # 提取 channel_type
        channel = request.channel_id
        if not channel:  # 处理 None 和空字符串
            channel = "unknown"

        # 根据不同 Channel 类型提取 chat_id
        if channel == "web":
            # WebChannel 使用 session_id
            raw_chat_id = request.chat_id or request.session_id or request.params.get("session_id", "default")

        elif channel == "dingtalk":
            # DingTalk 使用 chat_id 或 conversation_id
            raw_chat_id = request.params.get("chat_id") or request.params.get("conversation_id", "")
            if not raw_chat_id:
                raw_chat_id = request.session_id or "default"

        elif channel == "xiaoyi":
            # XiaoYi 使用 chat_id 或 session_id
            raw_chat_id = request.params.get("chat_id") or request.session_id or "default"

        elif channel == "wecom":
            # Wecom 优先从 metadata 获取
            if request.metadata and isinstance(request.metadata, dict):
                raw_chat_id = request.metadata.get("wecom_chat_id", "")
                if not raw_chat_id:
                    raw_chat_id = request.chat_id or request.session_id or "default"
            else:
                raw_chat_id = request.chat_id or request.session_id or "default"
        elif channel == "officeclaw":
            raw_chat_id = request.chat_id or request.session_id or request.params.get("session_id", "default")
        else:
            # 其他 channel 默认使用 session_id
            raw_chat_id = request.session_id or "default"

        # 清理 chat_id 特殊字符
        cleaned_chat_id = sanitize_chat_id(raw_chat_id, channel)

        return cleaned_chat_id, channel

    def _build_inputs(self, request: AgentRequest) -> Tuple[dict[str, Any], str]:
        """构建 adapter 所需的 inputs 字典."""
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        config_base = get_config()
        memory_mode = get_memory_mode(config_base)
        query = request.params.get("query", "")
        channel = request.session_id.split('_')[0] if request.session_id else "web"
        language = config_base.get("preferred_language", "zh")

        if isinstance(query, InteractiveInput):
            final_query = query
        else:
            _supp_raw = request.params.get("supplementary_info")
            supplementary: str | None = None
            if isinstance(_supp_raw, str):
                stripped = _supp_raw.strip()
                if stripped:
                    supplementary = stripped
            answers = request.params.get("answers", [])
            if answers:
                request_id = request.params.get("request_id", "")
                interactive_input = self._build_interactive_input_from_answers(request_id, answers)
                final_query = interactive_input if interactive_input is not None else build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language,
                    supplementary_info=supplementary,
                )
            else:
                final_query = build_user_prompt(
                    query,
                    files=request.params.get("files", {}),
                    channel=channel,
                    language=language,
                    supplementary_info=supplementary,
                )

        inputs: dict[str, Any] = {
            "conversation_id": request.session_id,
            "query": final_query,
            "channel": channel,
            "language": language,
        }

        # 传递 enable_memory 参数
        enable_memory = request.metadata.get("enable_memory", True) if request.metadata else True
        inputs["enable_memory"] = enable_memory

        # 传递 files 参数（直接传递，供 Agent 直接访问）
        inputs["files"] = request.params.get("files", [])

        run = request.params.get("run")
        if run:
            inputs["run"] = run

        for key in _CONTEXT_SIZE_HINT_KEYS:
            value = request.params.get(key)
            if value is not None:
                inputs[key] = value

        # 返回原始 query（未经 build_user_prompt 包装）
        # Team 模式需要使用原始 query，而不是 JSON 包装后的 prompt
        return inputs, memory_mode, query

    def _build_interactive_input_from_answers(
            self, request_id: str, answers: list[dict]
    ) -> Any:
        """从用户答案构建 InteractiveInput.

        Args:
            request_id: 工具调用 ID
            answers: 用户答案列表，每个答案对应一个问题

        Returns:
            InteractiveInput 实例
        """
        from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

        interactive_input = InteractiveInput()

        answer = answers[0] if answers else {}
        selected_options = answer.get("selected_options", []) if isinstance(answer, dict) else []
        custom_input = answer.get("custom_input", "") if isinstance(answer, dict) else ""

        value = selected_options[0] if selected_options else ""

        if value in ("approve", "本次允许", "Approve"):
            confirm_payload = {"approved": True, "auto_confirm": False, "feedback": ""}
        elif value in ("always_allow", "总是允许", "Always Allow"):
            confirm_payload = {
                "approved": True,
                "auto_confirm": True,
                "persist_allow": True,
                "feedback": "",
            }
        elif value in ("reject", "拒绝", "Reject"):
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": custom_input or "用户拒绝"}
        else:
            confirm_payload = {"approved": False, "auto_confirm": False, "feedback": f"未知选项: {value}"}

        interactive_input.update(request_id, confirm_payload)
        logger.info(
            "[JiuWenClaw] InteractiveInput.update: request_id=%s payload=%s",
            request_id, confirm_payload
        )

        return interactive_input

    async def _handle_skilldev_request(self, request: AgentRequest) -> AgentResponse | None:
        """处理 SkillDev 相关请求，返回 None 表示不是 SkillDev 请求."""
        if request.req_method not in _SKILLDEV_METHODS:
            return None

        service = self._get_skilldev_service()
        try:
            chunks = []
            async for chunk in service.handle(request):
                chunks.append(chunk)
            final = chunks[-1] if chunks else None
            payload = final.payload if final else {}
        except Exception as exc:
            logger.error("[JiuWenClaw] skilldev 请求处理失败: %s", exc, extra={'user_visible': 'critical'})
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _handle_skills_request(self, request: AgentRequest) -> AgentResponse | None:
        """处理 Skills 相关请求，返回 None 表示不是 Skills 请求."""
        if request.req_method not in _SKILL_ROUTES:
            return None

        handler_name = _SKILL_ROUTES[request.req_method]
        handler = getattr(self._skill_manager, handler_name)
        try:
            payload = await handler(request.params)
            _reload_after_skills = handler_name in [
                "handle_skills_install",
                "handle_skills_uninstall",
                "handle_skills_import_local",
                "handle_skills_skillnet_install",
                "handle_skills_clawhub_download",
            ]
            if handler_name == "handle_skills_skillnet_install" and payload.get("pending"):
                _reload_after_skills = False
            if _reload_after_skills:
                # Rebuild agent after skill changes; session_id is omitted here and
                # create_instance() reuses cached self._session_id set at first init
                # (AgentManager always passes session_id on the initial create_instance).
                await self.create_instance()
        except Exception as exc:
            logger.error("[JiuWenClaw] skills 请求处理失败: %s", exc, extra={'user_visible': 'critical'})
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _handle_tools_request(self, request: AgentRequest) -> AgentResponse | None:
        """处理 tools.* 相关请求，返回 None 表示不是 Tools 管理请求."""
        if request.req_method not in _TOOL_ROUTES:
            return None

        handler_name = _TOOL_ROUTES[request.req_method]
        tm = self._get_tool_manager()
        handler = getattr(tm, handler_name)
        try:
            payload = await handler(request.params)
        except Exception as exc:
            logger.error("[JiuWenClaw] tools 请求处理失败: %s", exc, extra={'user_visible': 'critical'})
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    async def _process_interrupt(self, request: AgentRequest) -> AgentResponse:
        """处理 interrupt 请求.

        根据 intent 分流：
        - pause: 暂停 ReAct 循环（不取消任务）
        - resume: 恢复已暂停的 ReAct 循环
        - cancel: 取消所有运行中的任务
        - supplement: 取消当前任务但保留 todo

        Args:
            request: AgentRequest，params 中可包含：
                - intent: 中断意图 ('pause' | 'cancel' | 'resume' | 'supplement')
                - new_input: 新的用户输入（用于切换任务）

        Returns:
            AgentResponse 包含 interrupt_result 事件数据
        """
        adapter = await self._ensure_adapter()
        # 调用 adapter 的 process_interrupt 处理 SDK 特定逻辑（如 pause/resume、todo 标记等）
        response = await adapter.process_interrupt(request)
        intent = request.params.get("intent", "cancel")

        if intent == "pause":
            # 暂停：不取消任务，只暂停 ReAct 循环
            return response

        if intent == "resume":
            # 恢复：恢复 ReAct 循环
            return response

        if intent == "supplement":
            # 取消当前 session 的任务
            session_id = self._session_manager.get_session_id(request.session_id)
            await self._session_manager.cancel_session_task(session_id, "interrupt(supplement): ")
            return response

        # cancel: 取消所有 session 的任务
        await self._session_manager.cancel_all_session_tasks(f"interrupt(intent={intent}): ")
        return response

    async def process_message(self, request: AgentRequest) -> AgentResponse:
        """处理非流式请求.

        支持多 session 并发执行，同 session 内任务按先进后出顺序执行.
        """
        adapter = await self._ensure_adapter()

        if request.req_method == ReqMethod.CHAT_CANCEL:
            return await self._process_interrupt(request)

        if request.req_method == ReqMethod.CHAT_ANSWER:
            return await adapter.handle_user_answer(request)

        heartbeat_response = await adapter.handle_heartbeat(request)
        if heartbeat_response is not None:
            return heartbeat_response

        skilldev_response = await self._handle_skilldev_request(request)
        if skilldev_response is not None:
            return skilldev_response

        skills_response = await self._handle_skills_request(request)
        if skills_response is not None:
            return skills_response

        tools_response = await self._handle_tools_request(request)
        if tools_response is not None:
            return tools_response

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")
        # 记录用户问题到日志
        logger.info(
            "[JiuWenClaw] 用户问题: %s",
            _truncate_for_log(query),
            extra={'user_visible': 'critical'}
        )
        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
            channel_metadata=request.metadata,
            mode=request.params.get("mode", "unknown"),
            sessions_root=self._sessions_dir,
        )

        logger.info(
            "[JiuWenClaw] 处理请求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
            extra={'user_visible': 'progress'}
        )

        prepare_plan_pause = getattr(adapter, "prepare_plan_pause_for_request", None)
        if callable(prepare_plan_pause):
            await prepare_plan_pause(request)

        inputs, memory_mode, raw_query = self._build_inputs(request)
        self._apply_effective_project_dir_to_request(request, session_id, inputs)

        # 文件预处理：从对象存储下载输入文件到本地 workspace
        await self.prepare_files_for_agent(request)

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            logger.info(
                f"[JiuWenClaw] Memory hook执行完成: request_id={request.request_id}",
                extra={'user_visible': 'progress'}
            )
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        async def run_agent_task():
            # 注册 Office Claw MCP（请求级环境变量）
            office_claw_mcp = request.params.get("office_claw_mcp")
            if isinstance(office_claw_mcp, dict):
                try:
                    await self._get_tool_manager().register_request_scoped_office_claw_mcp(office_claw_mcp)
                except Exception as exc:
                    logger.warning("[JiuWenClaw] office_claw_mcp 注册失败: %s", exc, extra={'user_visible': 'progress'})
            return await adapter.process_message_impl(request, inputs)

        result = await self._session_manager.submit_and_wait(session_id, run_agent_task)

        if result.ok and result.payload.get("content"):
            content = result.payload["content"]
            content_str = content if isinstance(content, str) else str(content)
            # 记录Agent回答到日志
            logger.info(
                "[JiuWenClaw] Agent回答: %s",
                _truncate_for_log(content_str),
                extra={'user_visible': 'critical'}
            )
            append_history_record(
                session_id=session_id,
                request_id=request.request_id,
                channel_id=request.channel_id,
                role="assistant",
                event_type="chat.final",
                content=content_str,
                timestamp=time.time(),
                mode=request.params.get("mode", "unknown"),
                sessions_root=self._sessions_dir,
            )

            # cloud memory: after chat hook
            if memory_mode == "cloud":
                after_ctx = MemoryHookContext(
                    session_id=request.session_id or "default",
                    request_id=request.request_id or "",
                    channel_id=request.channel_id,
                    agent_name="main_agent",
                    workspace_dir=str(get_agent_home_dir()),
                    assistant_message=content_str,
                    extra=request.params,
                )
                await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        # 文件后处理：上传 Agent 生成的输出文件到对象存储
        if result.ok:
            # 从 session_id 或 metadata 中提取用户 ID
            user_id = request.session_id or "default"
            if request.metadata and isinstance(request.metadata, dict):
                user_id = request.metadata.get("user_id", user_id)

            # 统一提取 chat_id 和 channel_type
            cleaned_chat_id, channel_type = self._extract_chat_id(request)

            logger.info(
                f"[JiuWenClaw] 开始上传文件: request_id={request.request_id} "
                f"files_count={len(result.payload.get('files'))} user_id={user_id} chat_id={cleaned_chat_id}",
                extra={'user_visible': 'critical'}
            )
            await self.upload_agent_files(result, user_id, cleaned_chat_id, channel_type)
        return result

    async def process_message_stream(
            self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        """处理流式请求.

        支持多 session 并发执行，同 session 内任务按先进后出顺序执行.
        """
        #  诊断日志：确认 JiuWenClaw.process_message_stream 是否被调用
        logger.info(
            "[JiuWenClaw]  DIAGNOSTIC: process_message_stream 入口 "
            "| request_id=%s | channel=%s | session=%s | has_metadata=%s",
            request.request_id,
            request.channel_id,
            request.session_id,
            bool(request.metadata),
        )
        # SkillDev 流式请求：直接委托给 SkillDevService，绕过 ReActAgent
        if request.req_method in _SKILLDEV_METHODS:
            service = self._get_skilldev_service()
            try:
                async for chunk in service.handle(request):
                    yield chunk
            except Exception as exc:
                logger.error("[JiuWenClaw] skilldev 流式请求处理失败: %s", exc, extra={'user_visible': 'progress'})
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"event_type": "skilldev.error", "error": str(exc)},
                    is_complete=True,
                )
            return

        adapter = await self._ensure_adapter()
        logger.info(
            f"[JiuWenClaw] Adapter初始化成功: request_id={request.request_id}",
            extra={'user_visible': 'progress'}
        )

        session_id = self._session_manager.get_session_id(request.session_id)
        query = request.params.get("query", "")
        # 记录用户问题到日志
        logger.info(
            "[JiuWenClaw] 用户问题: %s",
            _truncate_for_log(query),
            extra={'user_visible': 'critical'}
        )

        mode = request.params.get("mode", "") if isinstance(request.params, dict) else ""
        team_flag = request.params.get("team", False) if isinstance(request.params, dict) else False
        is_team_mode = team_flag or (isinstance(mode, str) and mode.strip().lower() == "team")

        append_history_record(
            session_id=session_id,
            request_id=request.request_id,
            channel_id=request.channel_id,
            role="user",
            content=query,
            timestamp=time.time(),
            channel_metadata=request.metadata,
            mode=request.params.get("mode", "unknown"),
            sessions_root=self._sessions_dir,
        )

        logger.info(
            "[JiuWenClaw] 处理流式请求: request_id=%s channel_id=%s session_id=%s sdk=%s",
            request.request_id, request.channel_id, session_id, self._sdk_name,
        )

        prepare_plan_pause = getattr(adapter, "prepare_plan_pause_for_request", None)
        if callable(prepare_plan_pause):
            await prepare_plan_pause(request)

        inputs, memory_mode, raw_query = self._build_inputs(request)
        logger.info(
            f"[JiuWenClaw] Inputs构建完成: request_id={request.request_id}",
            extra={'user_visible': 'progress'}
        )
        self._apply_effective_project_dir_to_request(request, session_id, inputs)

        # 文件预处理：从对象存储下载输入文件到本地 workspace
        await self.prepare_files_for_agent(request)
        rid = request.request_id
        cid = request.channel_id

        # Team 模式：使用原始 query，而不是 build_user_prompt 包装后的内容
        if is_team_mode:
            inputs["query"] = raw_query
            logger.info(
                "[JiuWenClaw] Team模式使用原始query: %s",
                raw_query[:100] if raw_query else "",
                extra={'user_visible': 'progress'}
            )

        # cloud memory: before chat hook
        if memory_mode == "cloud":
            mem_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_BEFORE_CHAT, mem_ctx)
            logger.info(
                f"[JiuWenClaw] Memory hook执行完成: request_id={request.request_id}",
                extra={'user_visible': 'progress'}
            )
            memory_block = "\n\n".join(b for b in mem_ctx.memory_blocks if b)
            inputs["memory_block"] = memory_block

        # Team 模式: 检查是否是后续请求（需要绕过 Session Manager）
        is_team_first_request = True
        if is_team_mode:
            from jiuwenclaw.agentserver.team import get_team_manager
            team_manager = get_team_manager()
            is_team_first_request = not team_manager.has_stream_task(session_id)
            logger.info(
                "[JiuWenClaw] Team模式: session_id=%s is_first=%s",
                session_id, is_team_first_request
            )
            if is_team_first_request:
                logger.info(
                    f"[JiuWenClaw] Team模式启用: session_id={session_id}",
                    extra={'user_visible': 'critical'}
                )

        stream_queue = asyncio.Queue()
        stream_done = asyncio.Event()
        final_answer_content = ""
        final_answer_chunks: list[str] = []
        adapter_emitted_terminal_chunk = False
        collected_files: list[dict] = []  # 收集 Agent 生成的文件信息

        async def run_stream_task():
            try:
                # 注册 Office Claw MCP（请求级环境变量）
                office_claw_mcp = request.params.get("office_claw_mcp")
                if isinstance(office_claw_mcp, dict):
                    try:
                        await self._get_tool_manager().register_request_scoped_office_claw_mcp(office_claw_mcp)
                    except Exception as exc:
                        logger.error(
                            f"[JiuWenClaw] office_claw_mcp注册失败: request_id={request.request_id} error={str(exc)}",
                            extra={'user_visible': 'critical'}
                        )
                        logger.warning("[JiuWenClaw] office_claw_mcp 注册失败: %s", exc)
                async for chunk in adapter.process_message_stream_impl(request, inputs):
                    await stream_queue.put(("chunk", chunk))
            except asyncio.CancelledError:
                logger.info("[JiuWenClaw] 流式任务被取消: request_id=%s session_id=%s", rid, session_id, 
                            extra={'user_visible': 'progress'})
                await stream_queue.put(("error", asyncio.CancelledError()))
            except Exception as exc:
                logger.exception("[JiuWenClaw] 流式任务异常: %s", exc, extra={'user_visible': 'progress'})
                await stream_queue.put(("error", exc))
            finally:
                stream_done.set()

        # Team 模式: 后续请求直接执行，绕过 Session Manager 队列
        # 因为 Team 是长期运行的(persistent)，interact 调用不需要等待前一个任务完成
        # 且 team_helpers 内部已有请求锁保证同一 session 的请求串行执行
        if is_team_mode and not is_team_first_request:
            logger.info(
                "[JiuWenClaw] Team模式后续请求，直接执行: request_id=%s session_id=%s",
                rid, session_id, extra={'user_visible': 'progress'}
            )
            # Team模式后续请求：直接异步执行，不排队。Team 模式支持并发，不需要排队。
            asyncio.create_task(run_stream_task())
        else:
            # 其他情况：通过 SessionManager 排队执行。同 session 内按 FIFO 顺序执行。
            await self._session_manager.submit_task(session_id, run_stream_task)
        try:
            # 1.生产者-消费者模式：run_stream_task 生产 → 循环消费 → yield 转发
            # 2.历史记录异步写入：不阻塞流式转发
            # 3.事件类型过滤：只记录 chat.*、task.*、team.message 类型事件
            while not stream_done.is_set() or not stream_queue.empty():
                try:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                event_type, data = item

                if event_type == "error":
                    if isinstance(data, asyncio.CancelledError):
                        logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid, extra={'user_visible': 'progress'})
                        raise data
                    append_history_record(
                        session_id=session_id,
                        request_id=rid,
                        channel_id=cid,
                        role="assistant",
                        event_type="chat.error",
                        content=str(data),
                        timestamp=time.time(),
                        mode=request.params.get("mode", "unknown"),
                        sessions_root=self._sessions_dir,
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.error", "error": str(data)},
                        is_complete=False,
                    )
                else:
                    if isinstance(data, AgentResponseChunk):
                        if data.is_complete:
                            adapter_emitted_terminal_chunk = True
                        if isinstance(data.payload, dict) and isinstance(data.payload.get("event_type"), str):
                            et = str(data.payload.get("event_type"))
                            should_record = et.startswith("chat.") or et.startswith("task.")
                            if not should_record and et == EventType.TEAM_MESSAGE.value:
                                should_record = True

                            if should_record:
                                payload_dict = dict(data.payload)
                                extra_fields = {k: v for k, v in payload_dict.items() if
                                                k not in ("event_type", "content", "task_id")}
                                if et == EventType.TEAM_MESSAGE.value and "event" in payload_dict:
                                    event_data = payload_dict.get("event", {})
                                    if isinstance(event_data, dict):
                                        for k, v in event_data.items():
                                            if k not in ("type", "timestamp", "content"):
                                                extra_fields[k] = v
                                append_history_record(
                                    session_id=session_id,
                                    request_id=rid,
                                    channel_id=cid,
                                    role="assistant",
                                    event_type=et,
                                    content=data.payload.get("content") or data.payload.get("error") or "",
                                    timestamp=time.time(),
                                    extra=extra_fields if extra_fields else None,
                                    mode=request.params.get("mode", "unknown"),
                                    sessions_root=self._sessions_dir,
                                    task_id=payload_dict.get("task_id"),
                                )
                            if et == "chat.final":
                                final_answer_content = str(data.payload.get("content", ""))
                            elif et == "chat.delta":
                                final_answer_chunks.append(str(data.payload.get("content", "")))
                            elif et == "chat.file":
                                # 收集 Agent 生成的文件信息
                                files_list = data.payload.get("files", [])
                                if isinstance(files_list, list):
                                    collected_files.extend(files_list)
                                    logger.info(
                                        f"[JiuWenClaw] 收集到文件: request_id={rid} files_count={len(files_list)}",
                                        extra={'user_visible': 'critical'}
                                    )
                        yield data
                    elif isinstance(data, dict) and isinstance(data.get("event_type"), str):
                        et = str(data.get("event_type"))
                        should_record = et.startswith("chat.") or et.startswith("task.")
                        if not should_record and et == EventType.TEAM_MESSAGE.value:
                            should_record = True

                        if should_record:
                            extra_fields = {
                                k: v
                                for k, v in data.items()
                                if k not in ("event_type", "content", "task_id")
                            }
                            if et == EventType.TEAM_MESSAGE.value and "event" in data:
                                event_data = data.get("event", {})
                                if isinstance(event_data, dict):
                                    for k, v in event_data.items():
                                        if k not in ("type", "timestamp", "content"):
                                            extra_fields[k] = v
                            append_history_record(
                                session_id=session_id,
                                request_id=rid,
                                channel_id=cid,
                                role="assistant",
                                event_type=et,
                                content=data.get("content") or data.get("error") or "",
                                timestamp=time.time(),
                                extra=extra_fields if extra_fields else None,
                                mode=request.params.get("mode", "unknown"),
                                sessions_root=self._sessions_dir,
                                task_id=data.get("task_id"),
                            )
                        if et == "chat.final":
                            final_answer_content = str(data.get("content", ""))
                        elif et == "chat.delta":
                            final_answer_chunks.append(str(data.get("content", "")))
                        elif et == "chat.file":
                            # 收集 Agent 生成的文件信息
                            files_list = data.get("files", [])
                            if isinstance(files_list, list):
                                collected_files.extend(files_list)
                                logger.info(
                                    f"[JiuWenClaw] 收集到文件: request_id={rid} files_count={len(files_list)}",
                                    extra={'user_visible': 'critical'}
                                )
                        yield AgentResponseChunk(
                            request_id=rid,
                            channel_id=cid,
                            payload=data,
                            is_complete=False,
                        )
        except asyncio.CancelledError:
            logger.info("[JiuWenClaw] 流式处理被中断: request_id=%s", rid, extra={'user_visible': 'progress'})
            raise

        # 记录Agent回答到日志（流式delta合并，仅在无chat.final事件时）
        if not final_answer_content and final_answer_chunks:
            delta_content = "".join(final_answer_chunks)
            if delta_content:
                logger.info(
                    "[JiuWenClaw] Agent流式回答汇总: %s",
                    _truncate_for_log(delta_content),
                    extra={'user_visible': 'critical'}
                )

        # cloud memory: after chat hook
        if memory_mode == "cloud":
            assistant_message = final_answer_content or "".join(final_answer_chunks)
            after_ctx = MemoryHookContext(
                session_id=request.session_id or "default",
                request_id=request.request_id or "",
                channel_id=request.channel_id,
                agent_name="main_agent",
                workspace_dir=str(get_agent_home_dir()),
                assistant_message=assistant_message,
                extra=request.params,
            )
            await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.MEMORY_AFTER_CHAT, after_ctx)

        # 文件后处理：上传 Agent 生成的输出文件到对象存储
        # 从 session_id 或 metadata 中提取用户 ID
        user_id = request.session_id or "default"
        if request.metadata and isinstance(request.metadata, dict):
            user_id = request.metadata.get("user_id", user_id)

        # 统一提取 chat_id 和 channel_type
        cleaned_chat_id, channel_type = self._extract_chat_id(request)

        # 文件后处理：上传 Agent 生成的输出文件到对象存储
        if collected_files:
            virtual_response = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"files": collected_files}
            )

            logger.info(
                f"[JiuWenClaw] 开始上传文件: request_id={request.request_id} "
                f"files_count={len(collected_files)} user_id={user_id} chat_id={cleaned_chat_id}",
                extra={'user_visible': 'critical'}
            )

            await self.upload_agent_files(virtual_response, user_id, cleaned_chat_id, channel_type)

            # 上传完成后，发送 chat.file_uploaded 事件（包含 URI）
            # virtual_response.payload["files"] 现在已包含 uri 字段
            if virtual_response.payload and virtual_response.payload.get("files"):
                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={
                        "event_type": "chat.file_uploaded",
                        "files": virtual_response.payload["files"]
                    },
                    is_complete=False,
                )
                logger.info(
                    f"[JiuWenClaw] 文件上传完成事件已发送: request_id={request.request_id} "
                    f"files_count={len(virtual_response.payload['files'])}",
                    extra={'user_visible': 'critical'}
                )

        if not adapter_emitted_terminal_chunk:
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"is_complete": True},
                is_complete=True,
            )

    # ---------- 实例获取 ----------

    def get_instance(self):
        return self._adapter._instance

    def get_registered_tools_catalog(self) -> list[dict[str, str]]:
        """枚举当前实例已注册工具（name / description / short_description）。"""
        from jiuwenclaw.agentserver.tool_catalog import get_registered_tools_catalog

        instance = self.get_instance()
        if instance is None:
            return []
        ability_manager = getattr(instance, "ability_manager", None)
        if ability_manager is None and callable(getattr(instance, "list", None)):
            ability_manager = instance
        return get_registered_tools_catalog(ability_manager)

    # ---------- 资源清理 ----------

    async def cancel_inflight_work(self, log_prefix: str = "[gateway disconnect] ") -> None:
        """Gateway 与 AgentServer 的 WebSocket 断开时调用：取消 session 流式任务并中止 adapter 内层循环。"""
        await self._session_manager.cancel_all_session_tasks(log_prefix)
        adapter = self._adapter
        if adapter is None:
            return
        abort_fn = getattr(adapter, "abort_on_gateway_disconnect", None)
        if not callable(abort_fn):
            return
        try:
            await abort_fn()
        except Exception:
            logger.exception("[JiuWenClaw] adapter.abort_on_gateway_disconnect failed")

    async def cleanup(self) -> None:
        """清理资源，准备销毁实例.

        每次 initialize 重建 agent 时调用。
        不清理记忆数据（记忆数据保留在文件系统中）。
        """
        logger.info("[JiuWenClaw] cleanup: 清理资源")

        if self._adapter is not None:
            try:
                if hasattr(self._adapter, "cleanup"):
                    await self._adapter.cleanup()
            except Exception as e:
                logger.warning("[JiuWenClaw] Adapter cleanup failed: %s", e)
            self._adapter = None

        self._tool_manager = None

        logger.info("[JiuWenClaw] cleanup: 完成")

    def is_working(self) -> bool:
        """返回 Agent 是否正在工作."""
        task = self._session_manager.get_session_tasks()
        queue = self._session_manager.get_session_queues()
        return self._adapter.is_working(task, queue)
