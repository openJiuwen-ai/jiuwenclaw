# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RuntimePromptRail — Inject dynamic time/runtime info per model call.

Time and runtime state (model, mode, language, etc.) are injected fresh on
every model call by reading runtime_state.yaml in Python, so the LLM always
sees the current values without needing to call any tool.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import yaml

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)

from openjiuwen.harness.rails.base import DeepAgentRail
from jiuwenswarm.agents.harness.common.prompt.shell_environment import build_shell_environment_prompt
from jiuwenswarm.common.utils import get_runtime_state_path, logger

from jiuwenswarm.common.utils import get_agent_workspace_dir

_LANGUAGE_NAMES = {"cn": "Chinese", "zh": "Chinese", "en": "English"}

_LANGUAGE_NAMES = {"cn": "Chinese", "zh": "Chinese", "en": "English"}


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
        self.attachment_manager = None
        self._language = language
        self._channel = channel
        self._trusted_dirs: list[str] | None = None
        self._cwd: str | None = None
        self._project_dir: str | None = None
        self._model_name: str = ""
        self._mode: str = ""
        self._session_id: str | None = None
        self._force_english: bool = False

    def init(self, agent) -> None:
        """从 agent 获取 system_prompt_builder 引用。"""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent) -> None:
        """清理注入的 section 并释放引用。"""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("time")
            self.system_prompt_builder.remove_section("runtime.model_answer_policy")
            self.system_prompt_builder.remove_section("language_output")
            self.system_prompt_builder.remove_section("env")
            self.system_prompt_builder.remove_section("browser_tool_policy")
            self.system_prompt_builder.remove_section("tui_current_project_policy")
            self.system_prompt_builder.remove_section("trusted_dirs_policy")
        self.system_prompt_builder = None
        self.attachment_manager = None

    def set_language(self, language: str) -> None:
        """per-request 更新语言。"""
        self._language = language

    def set_channel(self, channel: str) -> None:
        """per-request 更新频道。"""
        self._channel = channel

    def set_trusted_dirs(self, trusted_dirs: list[str] | None) -> None:
        """per-request 更新可信目录。"""
        self._trusted_dirs = trusted_dirs

    def set_runtime_paths(self, *, cwd: str | None = None, project_dir: str | None = None) -> None:
        """Per-request stable project identity and dynamic cwd."""
        self._cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else None
        self._project_dir = (
            project_dir.strip()
            if isinstance(project_dir, str) and project_dir.strip()
            else None
        )

    def set_model_name(self, model_name: str) -> None:
        """per-request 更新模型名称，作为文件读取失败时的兜底。"""
        self._model_name = model_name or ""

    def set_mode(self, mode: str) -> None:
        """per-request 更新运行模式，作为文件读取失败时的兜底。"""
        self._mode = mode or ""

    def set_session_id(self, session_id: str | None) -> None:
        """per-request 更新 session id，用于读取按 session 隔离的 runtime_state 文件。"""
        self._session_id = (
            session_id.strip()
            if isinstance(session_id, str) and session_id.strip()
            else None
        )

    def set_force_english(self, force: bool) -> None:
        """Force English-only injected sections regardless of language (code mode)."""
        self._force_english = force

    @staticmethod
    def _existing_dirs(paths: list[str] | None) -> list[str]:
        """Return normalized existing directories, preserving order."""
        result: list[str] = []
        seen: set[str] = set()
        for item in paths or []:
            if not isinstance(item, str) or not item.strip():
                continue
            path = os.path.abspath(os.path.expanduser(item.strip()))
            key = os.path.normcase(path)
            if key in seen or not os.path.isdir(path):
                continue
            seen.add(key)
            result.append(path)
        return result

    @staticmethod
    def _existing_dir(path: str | None) -> str | None:
        if not isinstance(path, str) or not path.strip():
            return None
        resolved = os.path.abspath(os.path.expanduser(path.strip()))
        return resolved if os.path.isdir(resolved) else None

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.system_prompt_builder:
            return

        for name in (
            "time",
            "runtime.model_answer_policy",
            "language_output",
            "env",
            "browser_tool_policy",
            "tui_current_project_policy",
            "trusted_dirs_policy"):
            self.system_prompt_builder.remove_section(name)

        # ── time ──
        if not self._force_english and self._language == "cn":
            time_content = (
                f"# 时间说明\n\n"
                "- 当用户询问“最新、当前、今年、本年、实时、近期”等信息并需要搜索时，"
                "搜索 query 必须优先使用当前年份或日期"
            )
        else:
            time_content = (
                f"# Time Description\n\n"
                "- When the user asks for latest/current/this-year/recent information and search is needed, "
                "search queries must prefer the current year or date."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="time",
            content={"cn": time_content, "en": time_content},
            priority=92,
        ))

        # ── runtime ──
        runtime_state: dict[str, Any] = {}
        state_path = get_runtime_state_path(self._session_id)
        try:
            with open(state_path, encoding="utf-8") as f:
                runtime_state = yaml.safe_load(f) or {}
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to read runtime state file %s: %s", state_path, e)

        model = (runtime_state.get("model") or self._model_name or "unknown").strip()
        available_models: list[str] = runtime_state.get("available_models") or []
        available_models_str = ", ".join(available_models) if available_models else model
        mode = (runtime_state.get("mode") or self._mode or "unknown").strip()
        language_val = (
            "en"
            if self._force_english
            else self._language or runtime_state.get("language") or "unknown"
        ).strip()
        channel = (runtime_state.get("channel") or self._channel or "unknown").strip()

        if not self._force_english and self._language == "cn":
            runtime_content = (
                "# 运行时状态\n\n"
                f"- 当前模型：{model}\n"
                f"- 可用模型：{available_models_str}\n"
                f"- 当前模式：{mode}\n"
                f"- 当前语言：{language_val}\n"
                f"- 当前渠道：{channel}"
            )
            model_answer_policy = (
                "# 模型名称回答策略\n\n"
                "- 当用户询问「你是什么模型」「当前用的是哪个模型」等问题时，"
                "直接使用 `runtime.setting` 中的当前模型值回答，只说模型名称，不要介绍身份或列出能力。\n"
                "- 当用户询问「你支持/配置了哪些模型」「有哪些模型可用」等问题时，"
                "直接使用 `runtime.setting` 中的可用模型列表回答，列出所有已配置的模型名称。"
            )
        else:
            runtime_content = (
                "# Runtime State\n\n"
                f"- Current model: {model}\n"
                f"- Available models: {available_models_str}\n"
                f"- Current mode: {mode}\n"
                f"- Current language: {language_val}\n"
                f"- Current channel: {channel}"
            )
            model_answer_policy = (
                "# Model Name Answer Policy\n\n"
                "- When the user asks what model you are using, answer with only the current model "
                "value from `runtime.setting`. Do not introduce yourself or list capabilities.\n"
                "- When the user asks what models are available or configured, list all available "
                "models from the `runtime.setting` available_models list."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="runtime.model_answer_policy",
            content={"cn": model_answer_policy, "en": model_answer_policy},
            priority=95,
        ))

        await self._clear_prompt_attachment(ctx, section="runtime.setting")
        await self._upsert_prompt_attachment(
            ctx,
            section="runtime.setting",
            content=runtime_content,
            kind=PromptAttachmentKind.RUNTIME,
            priority=95,
        )

        # ── Language output constraint (injected near end) ──
        language_name = _LANGUAGE_NAMES.get(language_val, language_val)
        language_output_content = (
            "# Language\n\n"
            f"Always respond in {language_name}. "
            f"Use {language_name} for all explanations, comments, "
            f"and communications with the user. "
            f"Technical terms and code identifiers should remain "
            f"in their original form."
        )
        self.system_prompt_builder.add_section(PromptSection(
            name="language_output",
            content={"cn": language_output_content, "en": language_output_content},
            priority=93,
        ))

        # ── Language output constraint (injected near end) ──
        self.system_prompt_builder.remove_section("language_output")
        language_name = _LANGUAGE_NAMES.get(language_val, language_val)
        language_output_content = (
            "# Language\n\n"
            f"Always respond in {language_name}. "
            f"Use {language_name} for all explanations, comments, "
            f"and communications with the user. "
            f"Technical terms and code identifiers should remain "
            f"in their original form."
        )
        self.system_prompt_builder.add_section(PromptSection(
            name="language_output",
            content={"cn": language_output_content, "en": language_output_content},
            priority=93,
        ))

        # ── Platform / OS environment section ──
        os_type = sys.platform
        shell_path = os.environ.get("SHELL", "")
        shell_name = os.path.basename(shell_path) if shell_path else "unknown"
        import platform as plat
        os_version = f"{plat.system()} {plat.release()}"
        env_language = "cn" if not self._force_english and self._language == "cn" else "en"
        shell_env_prompt = build_shell_environment_prompt(env_language, os_type)

        if not self._force_english and self._language == "cn":
            env_content = (
                "# 运行环境\n\n"
                f"- 当前运行平台：`{os_type}`\n"
                f"- Shell：{shell_name}\n"
                f"- OS 版本：{os_version}\n\n"
                f"{shell_env_prompt}\n\n"
                "## 平台命令差异（仅在必须使用 shell 时参考）\n\n"
                "以下命令差异仅适用于测试、构建、git、包管理、运行脚本等必须调用 shell 的场景。"
                "文件读取、编辑、搜索仍应优先使用专用工具。\n\n"
                "| 操作 | Windows (`win32`/`win64`) | Linux/macOS (`linux`/`darwin`) |\n"
                "|------|---------------------------|-------------------------------|\n"
                "| 创建目录 | `mkdir folder` 或 PowerShell "
                "`New-Item -ItemType Directory -Path folder` "
                "| `mkdir -p folder` |\n"
                "| 删除文件 | `del file.txt` 或 PowerShell `Remove-Item file.txt` | `rm file.txt` |\n"
                "| 删除目录 | `rmdir folder` 或 PowerShell `Remove-Item -Recurse folder` | `rm -rf folder` |\n"
                "| 查找文件 | `dir /s pattern` 或 PowerShell "
                "`Get-ChildItem -Recurse -Filter pattern` "
                "| `find . -name pattern` |\n\n"
                "**特别注意**：Windows 的 cmd/PowerShell `mkdir` 不支持 `-p` 参数；"
                "只有在 Shell 能力显示 Git Bash/PATH bash 可用且实际使用 bash/Git Bash 时，"
                "`mkdir -p` 才是合适的。"
                "如需在 cmd/PowerShell 中创建嵌套目录，请使用 PowerShell "
                "`New-Item -ItemType Directory -Path \"parent/child\" -Force`，"
                "或使用 cmd 分步创建 `mkdir parent && mkdir parent\\child`。"
            )
        else:
            env_content = (
                "# Environment\n\n"
                f"- Current platform: `{os_type}`\n"
                f"- Shell: {shell_name}\n"
                f"- OS Version: {os_version}\n\n"
                f"{shell_env_prompt}\n\n"
                "## Platform Command Differences (only when shell is required)\n\n"
                "The following command differences apply only to scenarios where shell execution is required "
                "(testing, builds, git, package management, running scripts). "
                "File reading, editing, and searching should still prefer dedicated tools.\n\n"
                "| Operation | Windows (`win32`/`win64`) | Linux/macOS (`linux`/`darwin`) |\n"
                "|-----------|---------------------------|-------------------------------|\n"
                "| Create directory | `mkdir folder` or PowerShell "
                "`New-Item -ItemType Directory -Path folder` "
                "| `mkdir -p folder` |\n"
                "| Delete file | `del file.txt` or PowerShell `Remove-Item file.txt` | `rm file.txt` |\n"
                "| Delete directory | `rmdir folder` or PowerShell `Remove-Item -Recurse folder` | `rm -rf folder` |\n"
                "| Find file | `dir /s pattern` or PowerShell "
                "`Get-ChildItem -Recurse -Filter pattern` "
                "| `find . -name pattern` |\n\n"
                "**WARNING**: Windows cmd/PowerShell `mkdir` does NOT support the `-p` flag; "
                "`mkdir -p` is appropriate only when Shell capabilities show Git Bash/PATH bash "
                "is available and you are actually using bash/Git Bash. "
                "To create nested directories in cmd/PowerShell, use either PowerShell "
                "`New-Item -ItemType Directory -Path \"parent/child\" -Force` "
                "or cmd with step-by-step creation `mkdir parent && mkdir parent\\\\child`."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="env",
            content={"cn": env_content, "en": env_content},
            priority=89,
        ))

        # ── Git status section ──
        git_branch = str(runtime_state.get("git_branch") or "").strip()
        if git_branch and git_branch != "N/A":
            git_main_branch = str(runtime_state.get("git_main_branch") or "").strip()
            git_status_text = str(runtime_state.get("git_status") or "").strip()
            git_recent_commits = str(runtime_state.get("git_recent_commits") or "").strip()
            git_user = str(runtime_state.get("git_user") or "").strip()

            git_lines = [
                "This is the git status at the start of the conversation. "
                "Note that this status is a snapshot in time, and will not update during the conversation.",
                f"Current branch: {git_branch}",
            ]
            if git_main_branch:
                git_lines.append(
                    f"Main branch (you will usually use this for PRs): {git_main_branch}"
                )
            if git_user:
                git_lines.append(f"Git user: {git_user}")
            git_lines.append(f"Status:\n{git_status_text or '(clean)'}")
            git_lines.append(f"Recent commits:\n{git_recent_commits or '(none)'}")

            git_content = "\n\n".join(git_lines)

            await self._upsert_prompt_attachment(
                ctx,
                section="git_status",
                content=git_content,
                kind=PromptAttachmentKind.WORKSPACE_DELTA,
                priority=87,
            )
        else:
            await self._clear_prompt_attachment(
                ctx,
                section="git_status",
            )

        # ── Channel: browser_tool_policy or trusted_dirs_policy──
        if self._channel == "web":
            browser_tool_policy = (
                "# Browser Tool Policy\n\n"
                "- For browser tasks such as opening pages, navigation, clicking, typing, login, screenshots, "
                "page inspection, or extracting data from a live website, use `task_tool` with "
                '`subagent_type` set to `"browser_agent"` and put the full browser objective in '
                "`task_description`.\n"
                "- Before spawning `browser_agent` for booking, ticketing, purchasing, reservation, or "
                "form-filling tasks, check whether the user has supplied enough confirmed details. "
                "If required details are missing and A2UI is available, render a preflight A2UI form "
                "with action name `browser_preflight_submit` instead of starting browser automation. "
                "Do not use plain natural-language questions or `ask_user` for those missing "
                "browser-task details when A2UI is available on the Web channel.\n"
                "- Mandatory Web A2UI account-action gate: Gmail, email, mailbox cleanup, social "
                "media posting, comments, and other externally visible account actions MUST use A2UI "
                "when A2UI is available. Do not use `todo_create`, `todo_modify`, `memory_search`, "
                "`task_tool`, plain text, Markdown, or `ask_user` as a substitute for A2UI preflight, "
                "candidate selection, draft review, or final confirmation. For requests such as "
                "finding emails and replying to the ones that need a reply, first use A2UI preflight "
                "if filters or reply preferences are incomplete; after Gmail search, show the "
                "emails/threads as A2UI candidates before opening, summarizing multiple messages, "
                "drafting replies, or modifying mail; and show final A2UI confirmation before any "
                "send, archive, delete, unsubscribe, label, mark-read, post, publish, comment, like, "
                "follow, or delete action.\n"
                "- For hotel booking flows, after `browser_agent` returns candidate hotels, render the "
                "candidate list with A2UI selection actions named `hotel_option_select`. Include "
                "`next_action=\"continue_hotel_booking\"`, the selected hotel identity, and the "
                "confirmed city/date/guest context in each action context. When the user selects a "
                "hotel, call `browser_agent` to continue from the current browser state and selected "
                "candidate; do not restart the broad hotel search unless browser-state recovery is "
                "needed. At the payment/order summary page, render a final A2UI confirmation using "
                "`hotel_payment_confirm` and `hotel_payment_cancel` actions.\n"
                "- For Gmail search, summarization, reply drafting, and cleanup flows, render search "
                "results with `gmail_email_select` actions and cleanup candidates with "
                "`gmail_cleanup_select` actions. When the user selects an email, continue from the "
                "current Gmail browser state; do not repeat the broad Gmail search unless recovery is "
                "needed. Filling a reply draft must use `gmail_reply_draft_select` and must stop "
                "before sending. After `gmail_send_confirm`, send the email only if the visible "
                "Gmail compose state matches the confirmed context. Final cleanup requires "
                "`gmail_cleanup_confirm`. Respect `gmail_send_cancel` and `gmail_cleanup_cancel` "
                "by stopping without side effects.\n"
                "- For social media posting flows, render draft variants with "
                "`social_post_draft_select`. After draft selection, use `browser_agent` to fill the "
                "current platform compose UI but stop before any externally visible action. Final "
                "publishing requires `social_post_confirm`; after confirmation, publish only if "
                "the visible compose state matches the confirmed context. `social_post_cancel` "
                "stops without publishing.\n"
                "- Do not use bash, execute_code, subprocess, shell commands, or direct Chrome/Edge launches "
                "for browser automation.\n"
                "- If `task_tool` or `browser_agent` is unavailable, say that the browser "
                "subagent is unavailable before trying to start a browser through commands."
            )
            self.system_prompt_builder.add_section(PromptSection(
                name="browser_tool_policy",
                content={"cn": browser_tool_policy, "en": browser_tool_policy},
                priority=98,
            ))

        if self._channel == "tui":
            # Trusted directories policy for TUI mode
            trusted_dirs = self._existing_dirs(self._trusted_dirs)
            current_dir = (
                self._existing_dir(self._cwd)
                or self._existing_dir(self._project_dir)
                or (trusted_dirs[0] if trusted_dirs else None)
            )
            if current_dir:
                workspace_dir = str(get_agent_workspace_dir())
                project_dir = current_dir
                other_dirs = [
                    path for path in trusted_dirs
                    if not self._same_path(path, project_dir)
                ]
                cn_dirs_display = ", ".join(other_dirs) if other_dirs else "无"
                en_dirs_display = ", ".join(other_dirs) if other_dirs else "none"
                if not self._force_english and self._language == "cn":
                    current_project_policy = (
                        "# 当前项目工作空间\n\n"
                        f"- 当前项目目录：{project_dir}\n"
                        f"- 系统目录：{workspace_dir}\n\n"
                        "- 当用户询问“当前工作空间”“当前工作目录”“当前项目目录”“项目空间”或 workspace，"
                        "且没有明确限定 team workspace、系统目录或其他目录时，直接回答当前项目目录。\n"
                        "- 不要为了回答这类问题调用 `pwd`、`ls` 或读取内部 Team Leader workspace；"
                        "也不要把系统目录、team-workspace 或 Team Leader workspace 称为当前工作空间。\n"
                    )
                    trusted_dirs_content = (
                        "# 工作目录策略\n\n"
                        f"- 系统目录（不要在其中查找或运行项目文件）：{workspace_dir}\n"
                        f"- 当前项目目录（你正在工作的项目，查询文件、运行测试、执行命令等均应在此目录下进行）：{project_dir}\n"
                        f"- 其他可访问目录（可读写其中的资源，但不是当前项目目录）：{cn_dirs_display}\n\n"
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
                    current_project_policy = (
                        "# Current Project Workspace\n\n"
                        f"- Current project directory: {project_dir}\n"
                        f"- System directory: {workspace_dir}\n\n"
                        "- When the user asks for the current workspace, current working directory, "
                        "current project directory, project space, or workspace without explicitly "
                        "saying team workspace, "
                        "system directory, or another directory, answer directly with the current "
                        "project directory.\n"
                        "- Do not call `pwd`, `ls`, or inspect the internal Team Leader workspace "
                        "to answer this question, and do not call the system directory, "
                        "team-workspace, or Team Leader workspace the current workspace.\n"
                    )
                    trusted_dirs_content = (
                        "# Working Directory Policy\n\n"
                        f"- System directory (never search or run project files here): {workspace_dir}\n"
                        f"- Current project directory (the project you are working on; "
                        f"all file queries, test runs, command execution should happen here): {project_dir}\n"
                        f"- Other accessible directories (read/write allowed, but not the current project): "
                        f"{en_dirs_display}\n\n"
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
                    name="tui_current_project_policy",
                    content={"cn": current_project_policy, "en": current_project_policy},
                    priority=99,
                ))
                self.system_prompt_builder.add_section(PromptSection(
                    name="trusted_dirs_policy",
                    content={"cn": trusted_dirs_content, "en": trusted_dirs_content},
                    priority=90,
                ))

    async def _upsert_prompt_attachment(
        self,
        ctx: AgentCallbackContext,
        *,
        section: str,
        content: str,
        kind: PromptAttachmentKind,
        priority: int,
    ) -> None:
        if self.attachment_manager is None:
            logger.warning(
                "[RuntimePromptRail] prompt attachment manager unavailable; skip dynamic section=%s",
                section,
            )
            return
        try:
            writer = self.attachment_manager.bind_context(ctx)
            await writer.add_section(
                section,
                content,
                kind,
                "jiuwenswarm.runtime_prompt_rail",
                priority=priority,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[RuntimePromptRail] skip prompt attachment section=%s: %s", section, exc)

    async def _clear_prompt_attachment(
        self,
        ctx: AgentCallbackContext,
        *,
        section: str,
    ) -> None:
        if self.attachment_manager is None:
            return
        try:
            await self.attachment_manager.bind_context(ctx).clear_section(section)
        except ValueError as exc:
            logger.warning("[RuntimePromptRail] skip clearing prompt attachment section=%s: %s", section, exc)
