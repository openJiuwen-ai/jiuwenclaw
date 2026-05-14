# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RuntimePromptRail — Inject dynamic time/runtime info per model call.

Time and runtime state (model, mode, language, etc.) are injected fresh on
every model call by reading runtime_state.yaml in Python, so the LLM always
sees the current values without needing to call any tool.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail
from jiuwenclaw.common.utils import get_config_dir

from jiuwenclaw.common.utils import get_agent_workspace_dir

_CN_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class RuntimePromptRail(DeepAgentRail):
    """在 before_model_call 中注入时间及运行时状态文件路径。"""

    priority = 5  # 高优先级，确保早于其他 rail 执行

    def __init__(
        self,
        language: str = "cn",
        channel: str = "web",
        timezone_offset: int = 8,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._language = language
        self._channel = channel
        self._tz = timezone(timedelta(hours=timezone_offset))
        self._trusted_dirs: list[str] | None = None
        self._model_name: str = ""
        self._mode: str = ""

    def init(self, agent) -> None:
        """从 agent 获取 system_prompt_builder 引用。"""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """清理注入的 section 并释放引用。"""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("time")
            self.system_prompt_builder.remove_section("runtime")
            self.system_prompt_builder.remove_section("browser_tool_policy")
            self.system_prompt_builder.remove_section("trusted_dirs_policy")
        self.system_prompt_builder = None

    def set_language(self, language: str) -> None:
        """per-request 更新语言。"""
        self._language = language

    def set_channel(self, channel: str) -> None:
        """per-request 更新频道。"""
        self._channel = channel

    def set_trusted_dirs(self, trusted_dirs: list[str] | None) -> None:
        """per-request 更新可信目录。"""
        self._trusted_dirs = trusted_dirs

    def set_model_name(self, model_name: str) -> None:
        """per-request 更新模型名称，作为文件读取失败时的兜底。"""
        self._model_name = model_name or ""

    def set_mode(self, mode: str) -> None:
        """per-request 更新运行模式，作为文件读取失败时的兜底。"""
        self._mode = mode or ""

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.system_prompt_builder:
            return

        now = datetime.now(tz=self._tz)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        current_year = now.strftime("%Y")
        weekday_cn = _CN_WEEKDAYS[now.weekday()]

        if self._language == "cn":
            time_content = (
                f"# 当前日期与时间\n\n"
                f"- 当前时间：{now_str}（{weekday_cn}）\n"
                f"- 当前年份：{current_year}\n"
                "- 当用户询问“最新、当前、今年、本年、实时、近期”等信息并需要搜索时，"
                "搜索 query 必须优先使用当前年份或日期"
            )
        else:
            time_content = (
                f"# Current Date & Time\n\n"
                f"- Current time: {now_str} ({now.strftime('%A')})\n"
                f"- Current year: {current_year}\n"
                "- When the user asks for latest/current/this-year/recent information and search is needed, "
                "search queries must prefer the current year or date."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="time",
            content={"cn": time_content, "en": time_content},
            priority=92,
        ))

        runtime_state: dict[str, Any] = {}
        try:
            with open(get_config_dir() / "runtime_state.yaml", encoding="utf-8") as f:
                runtime_state = yaml.safe_load(f) or {}
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to read runtime_state.yaml: %s", e)

        model = (runtime_state.get("model") or self._model_name or "unknown").strip()
        mode = (runtime_state.get("mode") or self._mode or "unknown").strip()
        language_val = (runtime_state.get("language") or self._language or "unknown").strip()
        channel = (runtime_state.get("channel") or self._channel or "unknown").strip()

        if self._language == "cn":
            runtime_content = (
                "# 运行时状态\n\n"
                f"- 当前模型：{model}\n"
                f"- 当前模式：{mode}\n"
                f"- 当前语言：{language_val}\n"
                f"- 当前渠道：{channel}\n"
                "- 当用户询问「你是什么模型」「当前用的是哪个模型」等问题时，"
                "直接用上方「当前模型」的值回答，只说模型名称，不要介绍身份或列出能力"
            )
        else:
            runtime_content = (
                "# Runtime State\n\n"
                f"- Current model: {model}\n"
                f"- Current mode: {mode}\n"
                f"- Current language: {language_val}\n"
                f"- Current channel: {channel}\n"
                "- When the user asks \"what model are you\" or similar questions, "
                "answer with only the model name above in one sentence — "
                "do NOT introduce yourself or list capabilities."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="runtime",
            content={"cn": runtime_content, "en": runtime_content},
            priority=95,
        ))

        self.system_prompt_builder.remove_section("browser_tool_policy")
        if self._channel == "web":
            browser_tool_policy = (
                "# Browser Tool Policy\n\n"
                "- For browser tasks such as opening pages, navigation, clicking, typing, login, screenshots, "
                "page inspection, or extracting data from a live website, use `task_tool` with "
                '`subagent_type` set to `"browser_agent"` and put the full browser objective in '
                "`task_description`.\n"
                "- Do not use bash, execute_code, subprocess, shell commands, or direct Chrome/Edge launches "
                "for browser automation.\n"
                "- If `task_tool` or `browser_agent` is unavailable, say that the browser subagent is unavailable "
                "before trying to start a browser through commands."
            )
            self.system_prompt_builder.add_section(PromptSection(
                name="browser_tool_policy",
                content={"cn": browser_tool_policy, "en": browser_tool_policy},
                priority=98,
            ))

        if self._channel == "tui":
            # Trusted directories policy for TUI mode
            if self._trusted_dirs and len(self._trusted_dirs) > 0:
                workspace_dir = str(get_agent_workspace_dir())
                project_dir = self._trusted_dirs[0]
                other_dirs = self._trusted_dirs[1:]
                other_dirs_display = ", ".join(other_dirs) if other_dirs else "无"
                if self._language == "cn":
                    trusted_dirs_content = (
                        "# 工作目录策略\n\n"
                        f"- 系统目录（不要在其中查找或运行项目文件）：{workspace_dir}\n"
                        f"- 当前项目目录（你正在工作的项目，查询文件、运行测试、执行命令等均应在此目录下进行）：{project_dir}\n"
                        f"- 其他可访问目录（可读写其中的资源，但不是当前项目目录）：{other_dirs_display}\n\n"
                        "重要规则：\n"
                        "- 命令执行工具（mcp_exec_command）默认的工作目录是系统目录，"
                        "如果你要在项目目录下执行命令，必须将工具的 workdir 参数设置为当前项目目录，"
                        f"即 workdir=\"{project_dir}\"，不要使用默认值或 cd 方式切换，"
                        "因为 cd 只在子shell中生效，不会改变工具本身的工作目录\n"
                        "- 查找项目文件、读取项目代码时，应在当前项目目录下搜索，不要在系统目录下查找\n"
                        "- 不要在系统目录下运行项目测试或构建，系统目录仅用于存放配置和状态文件\n"
                        "- 若用户请求的操作涉及超出上述目录范围的路径，必须先向用户确认是否允许此次操作\n"
                        "- 确认时需明确告知：操作的完整路径、操作类型（读取/编辑/执行）、潜在风险\n"
                    )
                else:
                    trusted_dirs_content = (
                        "# Working Directory Policy\n\n"
                        f"- System directory (never search or run project files here): {workspace_dir}\n"
                        f"- Current project directory (the project you are working on; "
                        f"all file queries, test runs, command execution should happen here): {project_dir}\n"
                        f"- Other accessible directories (read/write allowed, but not the current project): "
                        f"{other_dirs_display}\n\n"
                        "Important rules:\n"
                        "- The command execution tool (mcp_exec_command) defaults its working directory "
                        "to the system directory. When you need to execute commands in the project directory, "
                        "you MUST set the tool's workdir parameter to the current project directory, "
                        f"i.e. workdir=\"{project_dir}\". Do NOT rely on cd to switch directories, "
                        "because cd only takes effect inside a subshell and does not change the tool's "
                        "actual working directory\n"
                        "- When searching for project files or reading project code, search within the "
                        "current project directory, not the system directory\n"
                        "- Never run project tests or builds in the system directory; "
                        "the system directory is only for config and state files\n"
                        "- If the user requests an operation involving paths outside the above directories, "
                        "you must first ask the user to confirm whether to allow this operation\n"
                        "- When confirming, clearly state: the full path, operation type (read/edit/execute), "
                        "potential risks\n"
                    )
                self.system_prompt_builder.add_section(PromptSection(
                    name="trusted_dirs_policy",
                    content={"cn": trusted_dirs_content, "en": trusted_dirs_content},
                    priority=90,
                ))
            else:
                self.system_prompt_builder.remove_section("trusted_dirs_policy")
