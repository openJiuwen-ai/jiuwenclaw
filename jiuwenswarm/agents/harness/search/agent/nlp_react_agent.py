"""
NLP ReAct Agent 模块

本模块实现了一个基于 ReAct（Reasoning + Acting）模式的纯文本/多模态搜索 Agent。
Agent 通过多轮"LLM 推理 → 工具调用 → 结果反馈"循环，自主完成复杂的搜索任务。

与 mm_react_agent.py 的区别：
1. 按需注册 python_code_interpreter（沙箱代码解释器，仅当 tool_names 包含时）和 check_confidence_gate（置信度门控）工具
2. 到达最大迭代轮次时，强制注入提示消息要求模型给出最终答案
3. 使用 create_llm_client() 工厂函数创建客户端（支持 Qwen/Gemini/OpenAI）

核心流程：
    用户输入 → 构建 system prompt + tools schema + query prompt
    → ReAct 循环（最多 max_iterations 轮）：
        1. 上下文管理（重复检测 + token 统计 + 超限截断）
        2. LLM 调用（含全局限流和重试）
        3. 结果解析：
           - finish_reason != "tool_calls" → 提取最终回答，返回 ReactLoopResult
           - finish_reason == "tool_calls" → 并行执行工具调用，结果追加到对话历史
    → 返回 ReactLoopResult（轨迹列表 + 成功/失败状态 + token 用量）
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import logging
import traceback

from openai.types.chat import ChatCompletionFunctionToolParam, ChatCompletionMessageToolCallUnion

from jiuwenswarm.agents.harness.search.llm_client.openai_client import OpenAIClient
from jiuwenswarm.agents.harness.search.tools.tool_registry import ToolRegistry
from jiuwenswarm.agents.harness.search.tools.web_fetch_summary import WebSearcherAndFetchTool
from jiuwenswarm.agents.harness.search.tools.answer_check import check_answer
from jiuwenswarm.agents.harness.search.agent.trace import TraceItem, ReactLoopResult
from jiuwenswarm.agents.harness.search.agent.context_manager import ContextManager
from jiuwenswarm.agents.harness.search import prompts
from jiuwenswarm.agents.harness.search.exception_type import ContextExhaustedError
from jiuwenswarm.agents.harness.search.util.image_process import resize_base64_image
from jiuwenswarm.agents.harness.search.llm_client.create_client import create_llm_client


@dataclass
class AgentConfig:
    """
    Agent 配置数据类，包含 LLM 调用参数、工具配置、上下文管理参数等。

    属性说明：
        model_name: LLM 模型名称，用于 create_llm_client() 自动选择客户端类型
        api_key: LLM API 密钥
        base_url: LLM API 基础地址
        system_prompt_name: 系统 prompt 模板名称（对应 prompts.SYSTEM_TEMPLATES 中的 key）
        query_prompt_name: 用户 query 模板名称（对应 prompts.QUERY_TEMPLATES 中的 key）
    """
    model_name: str
    api_key: str
    base_url: str
    system_prompt_name: str
    query_prompt_name: str

    # ---- 工具调用设置 ----
    # 默认启用的工具名列表，可通过 NLPRunner 覆盖为仅 web_search + web_fetch_and_summary 等
    tool_names: List[str] = field(default_factory=lambda: ["web_search",
                                                           "web_fetch_and_summary",
                                                           "text_to_image_web_search",
                                                           "image_to_image_web_search",
                                                           "image_crop",
                                                           ])
    # 工具调用的全局最大并发数，控制各工具的 Semaphore 上限
    tool_max_concurrency: int = 200

    # ---- 调用 LLM 的参数 ----
    # 思考模式：disabled=禁用思考，enabled=启用思考输出
    thinking_mode: str = "disabled"
    # 以下参数均为 None 时使用模型默认值
    temperature: float = None
    top_p: float = None
    max_completion_tokens: int = None
    presence_penalty: float = None
    # 额外参数字典，会作为 extra_body 传给 LLM API（如 preserve_thinking、enable_thinking 等）
    extra_body: Dict[str, Any] = field(default_factory=dict)
    # LLM 单次调用超时时间（秒），None 表示不超时（NLP Agent 中被 NLPRunner 设为 None）
    timeout: Optional[int] = 120
    # LLM 全局最大并发数，所有 OpenAIClient 实例共享的 Semaphore 上限
    llm_max_concurrency: int = 20

    # ---- Agent 调用设置 ----
    # Runner 层最大重试次数（遇到 LLM 超时等非流程控制错误时重试）
    max_runner_retries: int = 3
    # Agent ReAct 循环最大迭代轮次，超过此轮次会强制注入终止提示
    max_iterations: int = 30
    # 输出目录，用于保存 sys_tool_dict.json 等调试文件
    output_dir: str = "."
    # 是否将 system prompt / tools schema / query prompt 落盘到 output_dir/sys_tool_dict.json
    # 供调试追溯。默认 False：生产路径不向 cwd 写调试文件。调试时置 True（并按需设 output_dir）。
    save_debug_artifacts: bool = False

    # ---- 工具返回图片的输出模式 ----
    # "embed": OpenAI 官方多模态工具输出规范，图片直接内嵌在 tool role 的 content 数组中
    # "user_message": 工具返回纯文本摘要（含 HTTPS 网址），图片 base64 通过额外的 user role 消息注入对话历史
    tool_image_output_mode: str = "embed"
    # 输入图片最长边缩放目标像素大小，超过此值的图片会被等比缩放
    image_max_long_side: int = 1024
    # 每次工具返回结果中提取的最大图片数量，防止图片过多导致上下文过长
    max_images_per_tool_result: int = 5

    # ---- 上下文管理器配置 ----
    # 上下文窗口最大 token 数，用于 ContextManager 判断是否超限
    max_context_tokens: int = 262144
    # Qwen tokenizer 本地路径，用于精确计算 token 数量；不可用时降级为字符数估算
    tokenizer_name: str = "/kos_ulan/yangzhihan/origin_model/qwen/Qwen3.5-27B"
    # 连续重复检测阈值：连续 N 轮 LLM 输出指纹完全一致时触发截断（丢弃历史仅保留 system+首条 user）
    repeat_threshold: int = 3
    # token 超限截断阈值：对话历史 token 总数超过此值时截断；0 表示不启用
    compact_threshold: int = 0

    def __post_init__(self):
        """参数合法性校验：tool_image_output_mode 必须为 embed 或 user_message"""
        if self.tool_image_output_mode not in ("embed", "user_message"):
            raise ValueError(f"tool_image_output_mode 必须为 'embed' 或 'user_message'，当前值: {self.tool_image_output_mode!r}")


@dataclass
class ImageInput:
    """
    图片输入数据类，支持三种图片来源：
    1. HTTP URL：img_url 为 http:// 或 https:// 开头的链接
    2. 本地文件路径：img_url 为本地路径，Agent 会尝试上传到搜索服务
    3. Base64 编码：img_base64 为 data:image/...;base64,... 格式的字符串
    """
    img_url: Optional[str] = None
    img_base64: Optional[str] = None
    img_width: int = None
    img_height: int = None


@dataclass
class MMSearchInput:
    """
    Agent 搜索输入数据类：
    - question: 用户问题文本
    - images: 可选的图片输入列表（纯文本场景可为空列表）
    """
    question: str
    images: List[ImageInput] = field(default_factory=list)


class MMSearchAgent:
    """
    基于 ReAct 模式的搜索 Agent。

    工作流程：
    1. 初始化：注册工具集、创建 LLM 客户端、初始化上下文管理器
    2. react_loop()：执行 ReAct 循环
       - 构建 system prompt + tools schema + query prompt
       - 每轮：上下文管理 → LLM 调用 → 结果解析 → 工具执行（如有）
       - 终止条件：LLM 不再调用工具（finish_reason != "tool_calls"）或达到最大轮次
    3. 返回 ReactLoopResult：包含完整轨迹、成功状态、token 用量等

    与 mm_react_agent.py 中 MMSearchAgent 的差异：
    - 按需注册 python_code_interpreter（仅当 tool_names 包含时）和 check_confidence_gate 工具
    - 最大迭代轮次时强制注入终止提示（而非仅标记为 other_info 警告）
    - 使用 create_llm_client() 工厂函数（支持 Qwen/Gemini/OpenAI 自动选择）
    """

    def __init__(self,
                 agent_config: AgentConfig,
                 logger: logging.Logger) -> None:
        """
        初始化 Agent：创建 LLM 客户端、注册工具集、初始化上下文管理器。

        Args:
            agent_config: Agent 配置对象
            logger: 日志记录器
        """
        self.config = agent_config
        self.logger = logger

        # ---- 创建 LLM 客户端 ----
        # 使用工厂函数根据 model_name 自动选择 QwenClient / GeminiClient / OpenAIClient
        self.llm = create_llm_client(model=self.config.model_name,
                                     api_key=self.config.api_key,
                                     base_url=self.config.base_url,
                                     logger=logger,
                                     temperature=self.config.temperature,
                                     top_p=self.config.top_p,
                                     presence_penalty=self.config.presence_penalty,
                                     timeout=self.config.timeout,
                                     max_parallel=self.config.llm_max_concurrency,
                                     max_retries=self.config.max_runner_retries,
                                     max_completion_tokens=self.config.max_completion_tokens
                                     )

        # ---- 创建工具注册中心 ----
        self.tool_registry = ToolRegistry()

        # 注册网页搜索工具集（web_search / web_fetch / web_fetch_and_summary）
        self.web_searcher_tool = WebSearcherAndFetchTool(
            web_search_url=os.getenv("WEB_SEARCH_URL", ""),
            web_fetch_url=os.getenv("WEB_FETCH_URL", ""),
            web_fetch_and_summary_url=os.getenv("WEB_FETCH_AND_SUMMARY_URL", ""),
            max_retry=int(os.getenv("WEB_TOOL_MAX_RETRY", "2")),
            max_concurrency=self.config.tool_max_concurrency,
            logger=self.logger,
            use_cache=False,
            retry_interval=float(os.getenv("WEB_TOOL_RETRY_INTERVAL", "1.0"))
        )
        self.tool_registry.register_plugin(self.web_searcher_tool)

        # 注册视觉搜索工具集（可选：纯文本搜索场景不需要；text_image_search 未 vendor 时跳过）
        self.vl_web_searcher_tool = None
        try:
            from jiuwenswarm.agents.harness.search.tools.text_image_search import VLWebSearcherAndFetchTool
            self.vl_web_searcher_tool = VLWebSearcherAndFetchTool(
                text_to_image_web_search_url=os.getenv("TEXT_TO_IMAGE_SEARCH_URL", ""),
                image_to_image_web_search_url=os.getenv("IMAGE_TO_IMAGE_SEARCH_URL", ""),
                image_crop_url=os.getenv("IMAGE_CROP_SEARCH_URL", ""),
                upload_image_url=os.getenv("IMAGE_UPLOAD_URL", ""),
                download_image_url=os.getenv("IMAGE_DOWNLOAD_URL", ""),
                max_retry=3,
                max_concurrency=200,
                logger=self.logger,
                use_cache=False,
                retry_interval=3.0
            )
            self.tool_registry.register_plugin(self.vl_web_searcher_tool)
        except ImportError:
            self.logger.info("text_image_search not vendored; skipping image search tools.")
            # VL 工具未 vendor 时，同步从 tool_names 移除，避免 _build_tools_schema
            # 因“指定但未注册”抛 ValueError。
            _vl_names = {"text_to_image_web_search", "image_to_image_web_search", "image_crop"}
            self.config.tool_names = [t for t in self.config.tool_names if t not in _vl_names]

        # 注册沙箱 Python 代码解释器（可选）。仅当 tool_names 显式包含
        # python_code_interpreter 时才构造并注册；默认不启用，避免无谓地
        # 导入 opensandbox 与构造空 domain 的 ConnectionConfig。
        self.python_code_interpreter = None
        if "python_code_interpreter" in self.config.tool_names:
            try:
                from jiuwenswarm.agents.harness.search.tools.python_code_interpreter import PythonCodeInterpreter
                self.python_code_interpreter = PythonCodeInterpreter(logger=self.logger)
                self.tool_registry.register_plugin(self.python_code_interpreter)
            except ImportError:
                # opensandbox 未装：不仅要跳过注册，还要从 tool_names 移除，否则
                # _build_tools_schema 会因“tool_names 指定但 tool_registry 未注册”抛 ValueError。
                self.logger.info("opensandbox not installed; python_code_interpreter skipped.")
                self.config.tool_names = [t for t in self.config.tool_names if t != "python_code_interpreter"]

        # 注册置信度门控工具（NLP Agent 特有，mm_react_agent 中未注册）
        # check_answer 是一个独立函数（非类方法），使用 register_function 注册
        self.tool_registry.register_function(check_answer)

        # ---- 初始化上下文管理器 ----
        # 负责 token 统计、重复检测、超限截断
        self.context_manager = ContextManager(
            max_context_tokens=self.config.max_context_tokens,
            compact_threshold=self.config.compact_threshold,
            tokenizer_name=self.config.tokenizer_name,
            logger=self.logger,
        )

    async def close(self):
        """关闭所有工具的 httpx 客户端，释放连接池资源"""
        await self.web_searcher_tool.close()
        if self.vl_web_searcher_tool is not None:
            await self.vl_web_searcher_tool.close()

    @staticmethod
    def _build_system_prompt(system_prompt_name):
        """
        根据模板名称获取系统 prompt 内容。

        如果 system_prompt_name 是预定义模板名称（如 SYSTEM_TEMPLATE_XIAOHAN0319），
        返回模板内容；否则直接返回原字符串作为自定义 prompt。

        Args:
            system_prompt_name: 模板名称或自定义 prompt 字符串

        Returns:
            系统 prompt 内容字符串
        """
        return prompts.get_system_prompt(system_prompt_name)

    def _build_tools_schema(self) -> Optional[List[ChatCompletionFunctionToolParam | Dict[str, Any]]]:
        """
        构建传给 LLM 的 tools schema 列表，并根据 tool_names 过滤。

        流程：
        1. 从 tool_registry 获取所有已注册工具的 schema
        2. 如果 config.tool_names 非空，仅保留指定名称的工具
        3. 校验 tool_names 中指定的工具是否都已注册，未注册则抛出 ValueError

        Returns:
            过滤后的 tools schema 列表，供 OpenAI chat.completions.create 的 tools 参数使用
        """
        tools_schema = self.tool_registry.list_tools()
        if self.config.tool_names:
            # 构建允许的工具名集合
            allowed = set(self.config.tool_names)
            # 获取当前已注册的所有工具名
            available_names = {
                t.get("function", {}).get("name") or t.get("name")
                for t in tools_schema
            }
            # 检查是否有指定但未注册的工具
            missing = allowed - available_names
            if missing:
                raise ValueError(f"tool_names 中指定的工具未在 tool_registry 中注册: {sorted(missing)}")
            # 过滤 schema，仅保留允许的工具
            tools_schema = [
                t for t in tools_schema
                if t.get("function", {}).get("name") in allowed
                   or t.get("name") in allowed
            ]
        return tools_schema

    def _build_question_prompt(self, search_input: MMSearchInput):
        """
        根据用户问题和 query 模板构建用户 prompt。

        使用 prompts.format_query() 将问题文本填入指定模板。
        例如 QUERY_TEMPLATE 会生成 "Question: {question}\nExplanation: ...\nExact Answer: ..." 格式。

        Args:
            search_input: 搜索输入对象，包含 question 和 images

        Returns:
            格式化后的用户 prompt 字符串
        """
        query_prompt = prompts.format_query(search_input.question, query_template=self.config.query_prompt_name)
        return query_prompt

    async def react_loop(self, search_input: MMSearchInput) -> ReactLoopResult:
        """
        ReAct 推理主循环：反复调用 LLM 和工具，直到获得最终答案或达到最大轮次。

        核心流程：
        1. 构建 system prompt、tools schema、用户 query
        2. 处理用户输入图片（上传/缩放/拼入对话历史）
        3. 进入 ReAct 循环（最多 max_iterations 轮）：
           a. 到达最大轮次时，强制注入终止提示（NLP Agent 特有逻辑）
           b. 上下文管理：检测重复/超限，必要时截断对话历史
           c. 调用 LLM，获取回复
           d. 解析回复：
              - finish_reason != "tool_calls"：LLM 不再调用工具，提取最终回答并返回
              - finish_reason == "tool_calls"：并行执行所有工具调用，结果追加到对话历史
        4. 异常处理：
           - ContextExhaustedError：上下文超限，视为成功（保留已有轨迹）
           - 其他异常：视为失败

        Args:
            search_input: 搜索输入对象，包含 question 和 images

        Returns:
            ReactLoopResult: 包含完整轨迹、成功状态、token 用量等
        """
        # ---- 第一步：构建 prompt 和 schema ----
        system_prompt = self._build_system_prompt(system_prompt_name=self.config.system_prompt_name)
        tools_schema = self._build_tools_schema()
        query_prompt = self._build_question_prompt(search_input)

        # 将 system prompt、tools schema、query prompt 保存到文件，便于调试和追溯。
        # 默认关闭（save_debug_artifacts=False），避免生产路径向 cwd 落盘调试文件；
        # 调试时置 AgentConfig.save_debug_artifacts=True（并按需设 output_dir 指向非 cwd 路径）。
        if self.config.save_debug_artifacts:
            sys_tool_dict = {
                "system_prompt": system_prompt,
                "tools_schema": tools_schema,
                "query_prompt": query_prompt,
            }
            save_path = os.path.join(self.config.output_dir, "sys_tool_dict.json")
            os.makedirs(self.config.output_dir, exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(sys_tool_dict, f, ensure_ascii=False, indent=2)

        # ---- 第二步：构建初始用户消息，处理图片输入 ----
        # 初始 user content 包含文本问题 + 可选的图片信息
        init_user_content = [{"type": "text", "text": query_prompt}]
        init_image_base64 = []

        # 遍历所有输入图片，依次处理
        for image in search_input.images:
            images_url_str = ""
            # 图片必须有 base64 编码（纯文本场景 images 为空列表，不会进入此循环）
            if not image.img_base64:
                raise ValueError("image_base64 is empty")

            # 将图片缩放到最长边 image_max_long_side 像素，减少 token 消耗
            resized_base64 = resize_base64_image(image.img_base64, max_long_side=self.config.image_max_long_side)

            # 尝试将图片上传到搜索服务，获取可访问的 image_url
            try:
                image_url_resp = await self.vl_web_searcher_tool.upload_image(image_base64=resized_base64)
                image_url = image_url_resp.get("image_url", None)
            except Exception as E:
                # 上传失败时的降级策略：
                # 1. 如果 img_url 是本地文件路径，尝试从本地上传
                # 2. 如果 img_url 是 HTTP URL，直接使用
                # 3. 否则报错
                if os.path.exists(image.img_url):
                    image_url_resp = await self.vl_web_searcher_tool.upload_image(local_image_file_path=image.img_url)
                    image_url = image_url_resp.get("image_url", None)
                    if image_url:
                        images_url_str += "-" + image.img_url + "\n"
                        self.logger.info(f"input image upload success,image_url:{image_url}")
                    else:
                        self.logger.error(f"image upload failed,image_url:{image.img_url},error:{image_url_resp.get('error')}")
                        raise ValueError(f"image upload failed,image_url:{image.img_url},error:{image_url_resp.get('error')}")
                elif image.img_url.startswith(("http://", "https://")):
                    # img_url 本身就是 HTTP URL，无需上传
                    image_url = image.img_url
                else:
                    self.logger.warning(f"image_url is illegal:{image.img_url}")
                    raise ValueError(f"image_url is illegal:{image.img_url}")

            # 将图片 URL 和 base64 数据追加到初始用户消息中
            init_user_content.append({"type": "text", "text": f"\n图片的image_url链接是：{image_url}"})
            init_user_content.append({"type": "image_url", "image_url": {"url": resized_base64}})
            init_image_base64.append(resized_base64)

        # ---- 第三步：初始化对话历史和轨迹记录 ----
        conversation_history = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": init_user_content}
        ]

        # 归一化轨迹列表：记录每一步的输入/输出，用于保存推理轨迹 JSON
        normalized_results: list[TraceItem] = [TraceItem(type="user",
                                                         output=search_input.question + "\n".join(
                                                             _ for _ in [input.get("text", "") for input in init_user_content if "image_url" in input.get("text", "")]),
                                                         init_image_base64=init_image_base64,
                                                         )]

        # ---- 第四步：进入 ReAct 循环 ----
        last_ctx_status = None  # 上一轮上下文管理状态
        has_repeated_truncation = False  # 是否发生过重复截断
        has_compacted_truncation = False  # 是否发生过 token 超限截断
        # 累计 token 用量统计
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "prompt_tokens_details": {"cached_tokens": 0}}
        finish_reason = ""  # LLM 返回的 finish_reason

        for cur_loop_step in range(self.config.max_iterations):
            try:
                # ---- 4a. 到达最大迭代轮次时，强制注入终止提示 ----
                # NLP Agent 特有逻辑：要求模型停止工具调用，基于已有信息给出最终答案
                # 这与 mm_react_agent 不同：mm_react_agent 仅标记 other_info 警告，不强制注入提示
                if cur_loop_step + 1 == self.config.max_iterations:
                    force_answer_message = {
                        "role": "user", "content": "You have reached the maximum search/context budget. This is the final response turn."
                                                   "You must stop all tool usage now. Do not call search tools, browsing tools, confidence gate tools, answer-checking tools, or any other tools. The confidence gate is no longer applicable in this final-budget state."
                                                   "Based only on the information already gathered in the conversation, re-evaluate all evidence, resolve the most plausible candidate, and provide the best final answer you can. Do not answer \u201cunknown,\u201d \u201cunable to determine,\u201d or \u201cinsufficient evidence.\u201d Do not continue investigating. You must output a final answer now."
                                                   "If the evidence is imperfect, still choose the most likely answer from the gathered evidence and briefly explain the reasoning and remaining uncertainty."}

                    # 将强制回答提示追加到对话历史和归一化结果轨迹中
                    conversation_history.append(force_answer_message)
                    normalized_results.append(TraceItem(type="user",
                                                        output=force_answer_message["content"]))

                # ---- 4b. 上下文管理：重复检测 + token 统计 + 超限截断 ----
                # check_and_compact 会原地修改 conversation_history：
                #   - 检测到连续重复时：截断对话历史，仅保留 system prompt + 首条 user 消息
                #   - token 超过 compact_threshold 时：同样截断
                # 通过返回值的 repeated / compacted 字段判断是否发生了截断
                ctx_status = self.context_manager.check_and_compact(
                    conversation_history,
                    repeat_threshold=self.config.repeat_threshold,
                    cur_loop_step=cur_loop_step,
                )

                # 记录重复截断事件
                if ctx_status.get("repeated", False):
                    has_repeated_truncation = True
                    self.logger.warning(
                        f"对话历史已在 check_and_compact 中被截断，"
                        f"重复指纹: {ctx_status.get('repeated_fingerprint', '')[:200]}"
                    )
                    normalized_results.append(TraceItem(type="user",
                                                        output="之前的回答存在连续重复调用，将丢掉历史上下文。"))

                # 记录 token 超限截断事件
                if ctx_status.get("compacted", False):
                    has_compacted_truncation = True
                    normalized_results.append(TraceItem(type="user",
                                                        output=f"之前的回答超过压缩阈值了：{self.config.repeat_threshold}，将丢掉历史上下文。"))

                last_ctx_status = ctx_status

                # ---- 4c. 构建 LLM 调用额外参数 ----
                llm_kwargs = {}
                if self.config.extra_body:
                    if not isinstance(self.config.extra_body, dict):
                        raise TypeError("extra_body must be a dict")
                    llm_kwargs["extra_body"] = self.config.extra_body

                # ---- 4d. 调用 LLM ----
                # LLM 客户端内部已实现全局限流（Semaphore + 请求间隔）和重试（指数退避）
                response = await self.llm.invoke(messages=conversation_history, tools=tools_schema, **llm_kwargs)
                choice = response.choices[0]

                # ---- 4e. 解析 LLM 返回结果 ----
                finish_reason = choice.finish_reason  # "stop" / "tool_calls" / "length" 等

                # 累加 token 用量统计
                usage = getattr(response, "usage", None)
                if usage:
                    total_usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                    total_usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
                    total_usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
                    prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
                    # 将当前轮的 prompt_tokens 和 completion_tokens 记录到上下文状态中
                    last_ctx_status["prompt_token"] = getattr(usage, "prompt_tokens", 0) or 0
                    last_ctx_status["completion_tokens"] = getattr(usage, "completion_tokens", 0) or 0
                    if prompt_tokens_details:
                        total_usage["prompt_tokens_details"]["cached_tokens"] += getattr(prompt_tokens_details, "cached_tokens", 0) or 0

                # 提取 LLM 返回的消息内容
                message = choice.message
                tool_calls = message.tool_calls  # 工具调用列表（finish_reason == "tool_calls" 时非空）
                reasoning_content = getattr(message, "reasoning_content", None)  # 思考过程（Qwen 模型特有）
                content = message.content  # 文本回复内容

                # 将 LLM 返回消息转为 dict 格式，用于追加到对话历史
                message_dict = message.model_dump()

                # 记录思考过程到轨迹（如果有）
                if reasoning_content:
                    normalized_results.append(TraceItem(type="reasoning", output=reasoning_content, ctx_status=ctx_status))

                # 将 LLM 返回消息追加到对话历史（OpenAI API 要求将 assistant 回复加入历史）
                conversation_history.append(message_dict)

                # 记录文本回复到轨迹（如果有）
                if content:
                    normalized_results.append(TraceItem(type="output_text", output=content, ctx_status=ctx_status))

                # ---- 4f. 判断是否终止循环 ----
                # finish_reason != "tool_calls" 表示 LLM 不再调用工具，认为任务完成
                if finish_reason != "tool_calls":
                    if not content:
                        # LLM 停止了工具调用但没有返回文本内容，属于异常情况
                        self.logger.error(f"finish_reason != tool_calls but no content returned:{finish_reason}")
                        failure_reason = f"no final answer content (finish_reason={finish_reason})"
                        if finish_reason == "length":
                            failure_reason += " - output truncated by max_completion_tokens; raise the cap"
                    else:
                        self.logger.info(f"finish_reason != tool_calls,will return;finish_reason:{finish_reason}")
                        failure_reason = None
                    # 返回成功结果
                    return ReactLoopResult(success=True,
                                           turn_num=cur_loop_step,
                                           finish_reason=finish_reason,
                                           failure_reason=failure_reason,
                                           trace_list=normalized_results,
                                           ctx_status=self._build_final_ctx_status(last_ctx_status, has_repeated_truncation, has_compacted_truncation),
                                           total_usage=total_usage)

                # ---- 4g. 并行执行工具调用 ----
                if tool_calls:
                    await self.execute_tools(tool_calls, conversation_history, normalized_results)
                    continue

            except ContextExhaustedError as E:
                # 上下文超限错误：LLM 返回 context length exceeded 类错误
                # 视为成功（保留已有轨迹，因为之前可能已收集到有用信息）
                self.logger.error("context exhausted in react loop")
                return ReactLoopResult(success=True,
                                       trace_list=normalized_results,
                                       turn_num=cur_loop_step,
                                       finish_reason=finish_reason,
                                       failure_reason=f"{type(E).__name__}: {E}",
                                       ctx_status=self._build_final_ctx_status(last_ctx_status, has_repeated_truncation, has_compacted_truncation),
                                       total_usage=total_usage)
            except Exception as E:
                # 其他异常：视为失败
                self.logger.error(f'Error in loop {cur_loop_step}: {E} | traceback:{traceback.format_exc()}')
                return ReactLoopResult(success=False,
                                       trace_list=normalized_results,
                                       turn_num=cur_loop_step,
                                       finish_reason=finish_reason,
                                       failure_reason=f"{type(E).__name__}: {E} | traceback:{traceback.format_exc()}",
                                       ctx_status=self._build_final_ctx_status(last_ctx_status, has_repeated_truncation, has_compacted_truncation),
                                       total_usage=total_usage)

        # ---- 第五步：超过最大轮次，返回结果 ----
        # 注意：由于在最后一轮已强制注入了终止提示，理论上 LLM 应该已经给出了最终答案
        # 如果走到这里，说明 LLM 在收到终止提示后仍然调用了工具（不应该发生）
        warning_message = f"react loop hit max iterations limit:{self.config.max_iterations} without final response"
        self.logger.warning(warning_message)
        return ReactLoopResult(success=True,
                               trace_list=normalized_results,
                               turn_num=self.config.max_iterations,
                               finish_reason=finish_reason,
                               other_info=warning_message,
                               ctx_status=self._build_final_ctx_status(last_ctx_status, has_repeated_truncation, has_compacted_truncation),
                               total_usage=total_usage)

    @staticmethod
    def _build_final_ctx_status(ctx_status: Optional[Dict[str, Any]], has_repeated_truncation: bool, has_compacted_truncation: bool) -> Optional[Dict[str, Any]]:
        """
        构建最终的上下文状态字典，附加重复截断和 token 超限截断的标记。

        Args:
            ctx_status: 最后一轮的上下文管理状态（包含 total_tokens、remaining_tokens 等）
            has_repeated_truncation: 是否发生过重复截断
            has_compacted_truncation: 是否发生过 token 超限截断

        Returns:
            附加截断标记后的上下文状态字典
        """
        if ctx_status is None:
            result = {}
            if has_repeated_truncation:
                result["has_repeated_truncation"] = True
            if has_compacted_truncation:
                result["has_compacted_truncation"] = True
            return result if result else None
        final = dict(ctx_status)
        final["has_repeated_truncation"] = has_repeated_truncation
        final["has_compacted_truncation"] = has_compacted_truncation
        return final

    @staticmethod
    def _extract_images_from_data(data: Any, max_images: int = 5) -> List[Dict[str, Any]]:
        """
        从工具返回的 data 中递归提取图片信息。

        支持的 data 结构：
          - dict 含有 "base64" / "image_base_64"（base64 编码）
          - dict 含有 "image_url" / "imgurl"（HTTP URL）
          - ImageResult.dict() 对象
          - list[dict]
          - 嵌套 list/dict

        每条提取结果包含：
          - url: 图片的 HTTP URL（可能为 None）
          - base64: 图片的 base64 编码（可能为 None，会自动补全 data URI 前缀）
          - source_url: 图片所在网页的 URL
          - title: 图片标题
          - content: 图片描述

        Args:
            data: 工具返回的原始数据
            max_images: 最大提取图片数量，防止图片过多导致上下文过长

        Returns:
            图片信息列表，每项为 {"url": str|None, "base64": str|None, "source_url": str|None, "title": str|None, "content": str|None}
        """
        image_infos = []

        def _extract(item: Any):
            """递归提取单个 item 中的图片信息"""
            if isinstance(item, dict):
                # 提取 base64 编码（支持 "base64" 和 "image_base_64" 两种 key）
                raw_base64 = item.get("base64") or item.get("image_base_64")
                # 提取图片 URL（支持 "image_url" 和 "imgurl" 两种 key）
                raw_url = item.get("image_url") or item.get("imgurl")
                # 提取图片所在网页的 URL
                source_url = item.get("url") or item.get("link") or item.get("source_url")

                # 处理 base64：确保有 data URI 前缀
                base64 = None
                if raw_base64 and isinstance(raw_base64, str) and raw_base64.strip():
                    base64 = raw_base64.strip()
                    if not base64.startswith("data:"):
                        base64 = f"data:image/jpeg;base64,{base64}"

                # 处理 URL：确保是有效的字符串
                url = None
                if raw_url and isinstance(raw_url, str) and raw_url.strip():
                    url = raw_url.strip()

                # 只要有 base64 或 URL 就认为是一条有效的图片信息
                if base64 or url:
                    title = item.get("title")
                    content = item.get("content") or item.get("description")
                    image_infos.append({
                        "url": url,
                        "base64": base64,
                        "source_url": source_url,
                        "title": title if isinstance(title, str) and title.strip() else None,
                        "content": content if isinstance(content, str) and content.strip() else None,
                    })
            elif isinstance(item, list):
                # 递归处理嵌套列表
                for sub in item:
                    if len(image_infos) >= max_images:
                        return
                    _extract(sub)

        # 从顶层 data 开始提取
        if isinstance(data, list):
            for item in data:
                if len(image_infos) >= max_images:
                    break
                _extract(item)
        else:
            _extract(data)

        return image_infos[:max_images]

    @staticmethod
    def _build_multimodal_tool_content(data: Any, image_infos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Mode='embed'：构建多模态 tool content（文本 + 图片数组）。

        将工具返回的原始数据和提取出的图片信息组合为 OpenAI 多模态 content 格式：
        [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:image/..."}}]

        优先使用 base64 编码的图片数据，附带 title/content/source_url 元数据。

        Args:
            data: 工具返回的原始数据（用于生成文本摘要）
            image_infos: 提取出的图片信息列表

        Returns:
            OpenAI 多模态 content 格式的列表
        """
        # 第一部分：工具返回的原始数据作为文本
        content = [{"type": "text", "text": str(data)}]
        # 后续部分：每张图片的元数据文本 + 图片 URL（base64 或 HTTP URL）
        for info in image_infos:
            # 优先使用 base64，其次使用 HTTP URL
            src = info["base64"] or info["url"]
            if src:
                # 拼接元数据文本（标题、描述、来源 URL）
                meta_parts = []
                if info.get("title"):
                    meta_parts.append(f"标题: {info['title']}")
                if info.get("content"):
                    meta_parts.append(f"描述: {info['content']}")
                if info.get("source_url"):
                    meta_parts.append(f"来源url: {info['source_url']}")
                if meta_parts:
                    content.append({"type": "text", "text": "；".join(meta_parts)})
                # 追加图片
                content.append({
                    "type": "image_url",
                    "image_url": {"url": src},
                })
        return content

    @staticmethod
    def _build_image_summary_text(image_infos: List[Dict[str, Any]]) -> str:
        """
        Mode='user_message'：用 HTTPS 网址生成工具返回的图片摘要文本。

        不包含 base64 数据，仅包含图片 URL 和元数据，作为 tool role 的 content。
        图片的 base64 数据会通过额外的 user 消息注入（见 _build_user_image_message）。

        Args:
            image_infos: 提取出的图片信息列表

        Returns:
            图片摘要文本，格式如："图片获取成功，图片信息：\n- 图片url: xxx；标题: xxx"
        """
        lines = ["图片获取成功，图片信息："]
        for info in image_infos:
            u = info["url"]
            if u:
                parts = [f"图片url: {u}"]
                if info.get("title"):
                    parts.append(f"标题: {info['title']}")
                if info.get("content"):
                    parts.append(f"描述: {info['content']}")
                lines.append("- " + "；".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _build_user_image_message(image_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Mode='user_message'：构建包含图片 base64 的 user 消息。

        每张图片生成一个文本块（含 URL 和元数据）+ 一个 image_url 块（含 base64），
        组合为一条 user role 的多模态消息，追加到对话历史中。

        这种模式适用于不支持 tool role 多模态输出的 LLM API，
        通过 user 消息注入图片，让 LLM 能"看到"工具返回的图片。

        Args:
            image_infos: 提取出的图片信息列表

        Returns:
            OpenAI 多模态 user 消息格式：{"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", ...}]}
        """
        content = []
        for idx, info in enumerate(image_infos):
            u = info["url"]
            b64 = info["base64"]
            # 第一张图片前加引导文本
            if idx == 0:
                prefix = "以下是工具返回的图片，请结合图片内容继续分析。\n"
            else:
                prefix = ""
            # 拼接图片信息文本
            if u:
                parts = [f"图片url: {u}"]
                if info.get("title"):
                    parts.append(f"标题: {info['title']}")
                if info.get("content"):
                    parts.append(f"描述: {info['content']}")
                text = f"{prefix}{'；'.join(parts)}"
            else:
                # 没有 URL 时仅展示元数据
                meta_parts = []
                if info.get("title"):
                    meta_parts.append(f"标题: {info['title']}")
                if info.get("content"):
                    meta_parts.append(f"描述: {info['content']}")
                text = f"{prefix}图片"
                if meta_parts:
                    text += "（" + "；".join(meta_parts) + "）"
            # 追加文本块
            content.append({"type": "text", "text": text})
            # 追加图片块（base64 编码）
            if b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": b64},
                })
        # 兜底：如果没有提取到任何内容，添加一条默认文本
        if not content:
            content.append({"type": "text", "text": "以下是工具返回的图片，请结合图片内容继续分析。"})
        return {"role": "user", "content": content}

    async def execute_tools(self, tool_calls: Optional[List[ChatCompletionMessageToolCallUnion]], conversation_history, normalized_results: List[TraceItem]):
        """
        并行执行 LLM 返回的所有工具调用。

        流程：
        1. 为每个 tool_call 创建异步任务
        2. 每个任务内部：
           a. 解析工具名和参数
           b. 调用 tool_registry.call() 执行工具
           c. 从工具返回值中提取图片信息
           d. 根据图片信息和 tool_image_output_mode 构建对话消息：
              - 无图片：纯文本 tool 消息
              - 有图片 + embed 模式：多模态 tool 消息（图片内嵌在 tool content 中）
              - 有图片 + user_message 模式：纯文本 tool 消息 + 额外的 user 消息（含图片 base64）
        3. 使用 asyncio.gather 并行执行所有任务

        Args:
            tool_calls: LLM 返回的工具调用列表
            conversation_history: 对话历史列表（会被原地修改，追加工具调用结果）
            normalized_results: 归一化轨迹列表（会被原地修改，记录工具调用轨迹）
        """
        if tool_calls is None:
            return None

        async def execute_tool(tool_call: ChatCompletionMessageToolCallUnion):
            """执行单个工具调用，处理结果并追加到对话历史和轨迹"""
            try:
                function = tool_call.function
                name = function.name  # 工具名
                arguments = function.arguments  # 工具参数（JSON 字符串）
                call_id = tool_call.id  # 工具调用 ID（OpenAI API 要求 tool 消息必须携带）
                self.logger.debug(f"execute_tool | name:{name} | arguments:{arguments}")

                # 解析工具参数 JSON
                arguments_parser = json.loads(arguments)

                # 调用工具注册中心执行工具
                tool_result = await self.tool_registry.call(name=name, arguments=arguments_parser)

                # 尝试从工具返回值中提取图片信息
                if tool_result.ok and tool_result.data is not None:
                    image_infos = self._extract_images_from_data(
                        tool_result.data,
                        max_images=self.config.max_images_per_tool_result,
                    )
                else:
                    image_infos = []

                if not image_infos:
                    # ---- 无图片：保持原有纯文本逻辑 ----
                    # 将工具返回数据转为字符串，作为 tool role 消息
                    if not isinstance(tool_result.data, str):
                        content = json.dumps(tool_result.data, ensure_ascii=False, indent=2)
                    else:
                        content = str(tool_result.data)

                    # 追加 tool 消息到对话历史（OpenAI API 要求 tool_call_id 匹配）
                    conversation_history.append({
                        "role": "tool",
                        "content": content,
                        "tool_call_id": call_id,
                    })
                    # 记录工具调用轨迹
                    normalized_results.append(TraceItem(type="tool_call",
                                                        tool_name=name,
                                                        arguments=arguments,
                                                        output=tool_result.data))
                else:
                    # ---- 有图片：根据 tool_image_output_mode 构建消息 ----
                    mode = self.config.tool_image_output_mode
                    if mode == "embed":
                        # Mode 1: OpenAI 官方多模态工具输出规范
                        # 图片直接内嵌在 tool role 的 content 数组中
                        multimodal_content = self._build_multimodal_tool_content(
                            tool_result.data, image_infos,
                        )
                        conversation_history.append({
                            "role": "tool",
                            "content": multimodal_content,
                            "tool_call_id": call_id,
                        })
                        normalized_results.append(TraceItem(type="tool_call",
                                                            tool_name=name,
                                                            arguments=arguments,
                                                            output=str(multimodal_content)))

                    elif mode == "user_message":
                        # Mode 2: 工具返回纯文本（仅 HTTPS 网址），图片 base64 通过额外 user 消息注入
                        # 步骤 1：追加纯文本 tool 消息（含图片 URL 和元数据）
                        summary_text = self._build_image_summary_text(image_infos)
                        conversation_history.append({
                            "role": "tool",
                            "content": summary_text,
                            "tool_call_id": call_id,
                        })
                        normalized_results.append(TraceItem(type="tool_call",
                                                            tool_name=name,
                                                            arguments=arguments,
                                                            output=str(summary_text)))
                        # 步骤 2：追加额外的 user 消息（含图片 base64），让 LLM 能"看到"图片
                        user_image_msg = self._build_user_image_message(image_infos)
                        conversation_history.append(user_image_msg)
                        normalized_results.append(TraceItem(type="user",
                                                            output="\n".join(item.get("text", "") for item in user_image_msg.get("content", []) if item.get("type") == "text")))
                    else:
                        raise ValueError(
                            f"不支持的 tool_image_output_mode: {mode}，"
                            f"请使用 'embed' 或 'user_message'"
                        )

                return tool_result
            except Exception as E:
                self.logger.error(f'Error in execute_tool: {E} | traceback:{traceback.format_exc()}')
                return None

        # 并行执行所有工具调用
        tool_results = await asyncio.gather(*[execute_tool(tool_call=tool_call) for tool_call in tool_calls])
        return tool_results
