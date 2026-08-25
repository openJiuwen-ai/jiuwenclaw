"""Prompt boundary for Symphony-managed runtime Skill discovery."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails import SkillUseRail
from openjiuwen.harness.rails.base import DeepAgentRail

from openjiuwen.symphony.discovery import (
    DiscoverySettings,
    SkillFS,
    SkillPromptEntry,
    SkillPromptSnapshot,
    consume_incremental_skill_reminder,
    initialize_incremental_skill_notice_state,
)

from jiuwenswarm.agents.harness.common.tools.skill_retrieval_toolkits import (
    SkillRetrievalToolkit,
    build_discovery_settings,
    is_skill_retrieval_enabled,
)

_LEGACY_LIST_SKILL_TOOL_NAMES = frozenset({"list_skill", "list_skills"})
_SKILL_INDEX_TOOL_NAME = "skill_index"
_RUNTIME_SKILL_ATTACHMENT_SECTION = "skills.runtime_changes"
_SYMPHONY_RUNTIME_TOOL_NAMES = frozenset(
    {
        "skill_index",
        "symphony_compose_score",
        "symphony_read_score",
        "symphony_refresh_score",
        # JiuwenSwarm's current orchestration compatibility surface.
        "symphony_compose_graph",
        "symphony_read_graph",
        "symphony_refresh_graph",
    }
)
logger = logging.getLogger(__name__)


class SkillRetrievalPromptRail(DeepAgentRail):
    """Make Symphony the sole Skill-discovery surface while preserving SkillTool."""

    # Higher priorities run first. Hide the native section after SkillUseRail
    # refreshes it for the current model call.
    priority = SkillUseRail.priority - 1
    SECTION_NAME = "skill_retrieval"
    CANDIDATE_SECTION_NAME = "skill_retrieval.session_candidates"
    SECTION_PRIORITY = 41
    ATTACHMENT_SOURCE = "jiuwenswarm.skill_retrieval_prompt_rail"
    # Keep the inventory-dependent appendix after the reusable system/tool
    # prefix. Its content is frozen once in ``init``.
    CANDIDATE_SECTION_PRIORITY = 10_000

    def __init__(
        self,
        *,
        toolkit: SkillRetrievalToolkit | None = None,
        session_scope: str = "default",
        incremental_notice_max_chars: int | None = None,
        discovery_settings: DiscoverySettings | None = None,
        environment: SkillFS | None = None,
        config_base: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        settings = (
            discovery_settings
            or (toolkit.settings if toolkit is not None else None)
            or build_discovery_settings(config_base)
        )
        notice_max_chars = (
            incremental_notice_max_chars
            if incremental_notice_max_chars is not None
            else settings.incremental_notice_max_chars
        )
        if notice_max_chars <= 0:
            raise ValueError("incremental_notice_max_chars must be positive")
        self._session_scope = session_scope
        self._source_toolkit = toolkit
        self._incremental_notice_max_chars = notice_max_chars
        self._discovery_settings = settings
        self._prompt_skillfs: SkillFS | None = environment or (
            toolkit.environment if toolkit is not None else None
        )
        self._config_base = config_base
        self._session_enabled = is_skill_retrieval_enabled(config_base)
        self._agent = None
        self.system_prompt_builder = None
        self._hidden_legacy_abilities: dict[str, Any] = {}
        self._hidden_skills_section: PromptSection | None = None
        self.attachment_manager = None
        self._frozen_prompt_snapshot: SkillPromptSnapshot | None = None

    def init(self, agent: Any) -> None:
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(
            agent,
            "prompt_attachment_manager",
            None,
        )
        try:
            toolkit = self._toolkit()
            selection_cards = (
                toolkit.frozen_selection_cards
                if toolkit is not None
                else self._scan_selection_cards()
            )
            self._frozen_prompt_snapshot = (
                toolkit.frozen_prompt_snapshot
                if toolkit is not None
                else self._prompt_skillfs.prompt_snapshot()
                if self._prompt_skillfs is not None
                else self._empty_prompt_snapshot()
            )
            initialize_incremental_skill_notice_state(
                self._session_scope,
                selection_cards,
                discovery_tool_name=_SKILL_INDEX_TOOL_NAME,
            )
        except Exception:
            self._frozen_prompt_snapshot = self._empty_prompt_snapshot()
            logger.warning(
                "Unable to initialize Symphony Skill prompt/delta baseline",
                exc_info=True,
            )

    def uninit(self, agent: Any) -> None:
        self._restore_legacy_list_skill(agent)
        self._restore_native_skills_section()
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            self.system_prompt_builder.remove_section(self.CANDIDATE_SECTION_NAME)
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._prompt_skillfs = None
        self._source_toolkit = None
        self._frozen_prompt_snapshot = None
        self._agent = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Stage discovery guidance before the first admitted user turn."""
        await self._sync_prompt_attachment(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._sync_prompt_attachment(ctx)

    async def _sync_prompt_attachment(self, ctx: AgentCallbackContext) -> None:
        """Keep discovery guidance out of the cache-stable system prefix."""
        agent = getattr(ctx, "agent", None)
        if agent is not None:
            self._agent = agent
            if self.system_prompt_builder is None:
                self.system_prompt_builder = getattr(
                    agent, "system_prompt_builder", None
                )
            if self.attachment_manager is None:
                self.attachment_manager = getattr(
                    agent, "prompt_attachment_manager", None
                )

        if not self._session_enabled or not self._has_skill_index(ctx):
            await self._clear_prompt_attachments(ctx)
            self._disable_agentic_prompt(ctx)
            return

        if self.system_prompt_builder is None:
            return

        language = getattr(self.system_prompt_builder, "language", "cn") or "cn"
        self._hide_legacy_list_skill()
        self._filter_legacy_list_skill_from_model_inputs(ctx)
        self._hide_native_skills_section()
        await self._clear_runtime_skill_attachment(ctx)
        try:
            snapshot = self._prompt_snapshot()
        except Exception:
            logger.warning(
                "Unable to build the Symphony Skill prompt snapshot",
                exc_info=True,
            )
            snapshot = self._empty_prompt_snapshot()
        guidance = self._build_guidance(language)
        candidate_appendix = self._build_candidate_appendix(language, snapshot)
        manager = self.attachment_manager
        if manager is None:
            self._add_prompt_builder_sections(language, guidance, candidate_appendix)
            return

        self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder.remove_section(self.CANDIDATE_SECTION_NAME)
        writer = manager.bind_context(ctx)
        try:
            await writer.add_section(
                section=self.SECTION_NAME,
                content=guidance,
                kind=PromptAttachmentKind.SKILL,
                source=self.ATTACHMENT_SOURCE,
                priority=self.SECTION_PRIORITY,
                content_kind="text/markdown",
            )
            await writer.add_section(
                section=self.CANDIDATE_SECTION_NAME,
                content=candidate_appendix,
                kind=PromptAttachmentKind.SKILL,
                source=self.ATTACHMENT_SOURCE,
                priority=self.CANDIDATE_SECTION_PRIORITY,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning(
                "[SkillRetrievalPromptRail] attachment write failed: %s", exc
            )
            await self._clear_prompt_attachments(ctx)
            self._add_prompt_builder_sections(language, guidance, candidate_appendix)

    def _add_prompt_builder_sections(
        self,
        language: str,
        guidance: str,
        candidate_appendix: str,
    ) -> None:
        if self.system_prompt_builder is None:
            return
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={language: guidance},
                priority=self.SECTION_PRIORITY,
            )
        )
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.CANDIDATE_SECTION_NAME,
                content={language: candidate_appendix},
                priority=self.CANDIDATE_SECTION_PRIORITY,
            )
        )

    async def _clear_prompt_attachments(self, ctx: AgentCallbackContext) -> None:
        manager = self.attachment_manager
        if manager is None:
            return
        writer = manager.bind_context(ctx)
        try:
            await writer.clear_section(self.SECTION_NAME)
            await writer.clear_section(self.CANDIDATE_SECTION_NAME)
        except ValueError as exc:
            logger.warning(
                "[SkillRetrievalPromptRail] attachment clear failed: %s", exc
            )

    def _disable_agentic_prompt(self, ctx: AgentCallbackContext) -> None:
        self._restore_legacy_list_skill(getattr(ctx, "agent", None))
        self._restore_native_skills_section()
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            self.system_prompt_builder.remove_section(self.CANDIDATE_SECTION_NAME)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._session_enabled or getattr(ctx, "exception", None) is not None:
            return
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(
            getattr(inputs, "tool_name", None)
            or getattr(getattr(inputs, "tool_call", None), "name", None)
            or ""
        ).strip()
        if tool_name not in _SYMPHONY_RUNTIME_TOOL_NAMES:
            return
        # The session-owned command toolkit refreshes the SkillFS and consumes
        # the shared notice cursor as part of its result. Avoid a second scan.
        if tool_name == _SKILL_INDEX_TOOL_NAME:
            return
        try:
            reminder = consume_incremental_skill_reminder(
                self._session_scope,
                self._scan_selection_cards(),
                max_chars=self._incremental_notice_max_chars,
                discovery_tool_name=_SKILL_INDEX_TOOL_NAME,
            )
        except Exception:
            logger.warning(
                "Unable to consume Symphony Skill delta",
                exc_info=True,
            )
            return
        if reminder:
            self._append_tool_result_reminder(inputs, reminder)

    def _scan_selection_cards(self) -> dict[str, dict[str, str]]:
        if self._prompt_skillfs is None:
            return {}
        return self._prompt_skillfs.selection_cards()

    @staticmethod
    def _append_tool_result_reminder(
        inputs: Any,
        reminder: str,
    ) -> None:
        result = getattr(inputs, "tool_result", None)
        if isinstance(result, dict):
            updated = dict(result)
            for key in ("output", "result"):
                value = updated.get(key)
                if isinstance(value, str):
                    updated[key] = f"{value}\n\n{reminder}" if value else reminder
                    inputs.tool_result = updated
                    return
            updated["system_reminder"] = reminder
            inputs.tool_result = updated
            return
        if isinstance(result, str):
            inputs.tool_result = f"{result}\n\n{reminder}" if result else reminder

    async def _clear_runtime_skill_attachment(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        manager = self.attachment_manager
        if manager is None:
            return
        writer = manager.bind_context(ctx)
        if writer.session_id:
            await writer.clear_section(_RUNTIME_SKILL_ATTACHMENT_SECTION)

    def _hide_legacy_list_skill(self) -> None:
        ability_manager = getattr(self._agent, "ability_manager", None)
        if ability_manager is None:
            return
        get_ability = getattr(ability_manager, "get", None)
        remove_ability = getattr(ability_manager, "remove", None)
        if not callable(get_ability) or not callable(remove_ability):
            return

        for name in _LEGACY_LIST_SKILL_TOOL_NAMES:
            if name in self._hidden_legacy_abilities:
                continue
            card = get_ability(name)
            if card is None:
                continue
            removed = remove_ability(name)
            if removed is not None:
                self._hidden_legacy_abilities[name] = removed

    def _restore_legacy_list_skill(self, agent: Any | None = None) -> None:
        if agent is not None:
            self._agent = agent
        ability_manager = getattr(self._agent, "ability_manager", None)
        if ability_manager is None or not self._hidden_legacy_abilities:
            return
        get_ability = getattr(ability_manager, "get", None)
        add_ability = getattr(ability_manager, "add", None)
        if not callable(get_ability) or not callable(add_ability):
            return

        for name, card in list(self._hidden_legacy_abilities.items()):
            if get_ability(name) is None:
                add_ability(card)
            self._hidden_legacy_abilities.pop(name, None)

    def _hide_native_skills_section(self) -> None:
        if self.system_prompt_builder is None:
            return
        if self._hidden_skills_section is None:
            self._hidden_skills_section = self.system_prompt_builder.get_section(
                SectionName.SKILLS
            )
        self.system_prompt_builder.remove_section(SectionName.SKILLS)

    def _restore_native_skills_section(self) -> None:
        if self.system_prompt_builder is None or self._hidden_skills_section is None:
            return
        if not self.system_prompt_builder.has_section(SectionName.SKILLS):
            self.system_prompt_builder.add_section(self._hidden_skills_section)
        self._hidden_skills_section = None

    def _prompt_snapshot(self) -> SkillPromptSnapshot:
        if self._frozen_prompt_snapshot is None:
            toolkit = self._toolkit()
            self._frozen_prompt_snapshot = (
                toolkit.frozen_prompt_snapshot
                if toolkit is not None
                else self._prompt_skillfs.prompt_snapshot()
                if self._prompt_skillfs is not None
                else self._empty_prompt_snapshot()
            )
        return self._frozen_prompt_snapshot

    def _toolkit(self) -> SkillRetrievalToolkit | None:
        environment = self._prompt_skillfs
        if environment is None:
            return None
        # The rail intentionally keeps only the environment public; use the
        # constructor-owned toolkit when available without introducing a
        # second live inventory scan.
        toolkit = getattr(self, "_source_toolkit", None)
        return toolkit if isinstance(toolkit, SkillRetrievalToolkit) else None

    def _empty_prompt_snapshot(self) -> SkillPromptSnapshot:
        return SkillPromptSnapshot(
            mode="small",
            total_count=0,
            entries=(),
            estimated_candidate_tokens=0,
            candidate_budget_tokens=(self._discovery_settings.candidate_budget_tokens),
            index_state="missing",
        )

    def _build_guidance(
        self,
        language: str,
        _snapshot: SkillPromptSnapshot | None = None,
    ) -> str:
        if str(language).lower().startswith("en"):
            return self._build_english_guidance()
        return self._build_chinese_guidance()

    def _build_chinese_guidance(
        self,
        _snapshot: SkillPromptSnapshot | None = None,
    ) -> str:
        lines = [
            "## Skill 发现",
            "",
            "### 使用规则",
            (
                "- `skill_index` 是只读的已安装 Skill 目录，仅用于发现和选择 "
                "Skill。它只接受 `list`、`search`、`read` 三种结构化操作；"
                "其路径是虚拟目录资源，不是主机文件路径。"
            ),
            (
                "- 项目、工作区或系统文件使用文件工具或 Bash。不要用 "
                "`skill_index` 执行、修改、安装、删除、启用或禁用 Skill。"
                "用户要求执行已选 Skill 时，把工具实际返回的精确 `skill_id` "
                "交给 `skill_tool`。"
            ),
            (
                "- 任务明确匹配会话候选快照中的 Skill 时可直接选择其精确 `skill_id`；"
                "否则使用 `skill_index`。`list` 浏览当前目录或分类层级，"
                "`search` 按名称或能力证据检索，`read` 批量读取已观察到的"
                "元数据路径。参数必须使用工具的结构化字段，不得传入 "
                "Shell 命令、选项或管道文本。"
            ),
            (
                "- 检索前先提炼会改变 Skill 选择的约束。只有一个能力约束时，"
                "把已知的高信号格式、库、API、协议、方法名及同义词合并到 "
                "`query`；有两个或更多独立能力约束时，在第一次 `search` 中"
                "使用 `queries`，每项对应一个约束，并设置 `per_query_limit`，"
                "不要拆成连续多次搜索。`match=content` 时用 `|` 分隔同一约束的"
                "备选词（如 "
                "`youtube|subtitle|字幕|翻译`），不要用空格把它们拼成连续短语；"
                "已知多个目录时，也在同一次调用中传入多个 `paths`。已有"
                "充分候选后立即停止；只有具体任务约束仍未覆盖时才扩大"
                "或改写查询，不要机械地逐组尝试同义词。"
            ),
            (
                "- 候选名称和描述已是选择证据；足以判断时直接选择。只有"
                "多个候选需要比较或描述不足时，才用一次 `read` 批量读取"
                "已观察到的 metadata；发现和选择阶段不要读取完整 `SKILL.md`。"
                "只报告覆盖任务的最小充分候选集，不要为凑数加入弱相关项。"
            ),
            (
                "- `list` 或 `search` 可能返回多条结果时，在有序 `pipeline` "
                "末尾主动加入 `limit` 阶段：用户指定数量时使用该数量，"
                "否则限制为 10 行。只有用户明确要求全部、完整清单或一个"
                "不漏时才省略该阶段；“多一些”或“更广”不是穷举要求。"
                "只需总数时设置 `output_mode=count`；`count` 不是第四种操作。"
                "同时需要总数和有限样例时，在同一模型轮次分别发起一次 "
                "`output_mode=count` 调用和一次带 `limit` 的结果调用；有限结果"
                "本身不代表总数。"
            ),
            (
                "- 若工具说明内容因篇幅未显示，仍基于已返回内容完成当前"
                "回答，不要仅为扩展可见条目而重试。只有用户明确要求展示"
                "或列举，且省略影响请求完整性时，才在回答后说明继续可能"
                "占用较多上下文并询问是否继续。只有用户已明确允许不截断"
                "或不折叠输出时，才能设置 `disable_output_truncation=true`。"
            ),
            (
                "- 只能使用工具实际返回的 `skill_id` 和路径，不得猜测。"
                "只有完整目录根范围的检索无结果后，才能断言没有匹配的"
                "已安装 Skill；局部目录无命中只能说明该范围尚未找到。"
            ),
        ]
        return "\n".join(lines)

    def _build_english_guidance(
        self,
        _snapshot: SkillPromptSnapshot | None = None,
    ) -> str:
        lines = [
            "## Skill Discovery",
            "",
            "### Rules",
            (
                "- `skill_index` is the read-only installed-Skill directory. Use it "
                "only to discover and select Skills. It accepts exactly three structured "
                "operations: `list`, `search`, and `read`. Its paths name virtual "
                "directory resources, not host filesystem paths."
            ),
            (
                "- Use filesystem tools or Bash for project and system files. "
                "Do not use `skill_index` to execute, modify, install, remove, "
                "enable, or disable a Skill. When the user requests execution, pass an "
                "exact observed `skill_id` to `skill_tool`."
            ),
            (
                "- A clear match in the session candidate snapshot may be selected "
                "directly by exact `skill_id`; "
                "otherwise use `skill_index`. Use `list` to browse a directory or "
                "category hierarchy, `search` for name or capability evidence, and "
                "`read` to batch-read observed metadata paths. Always use the structured "
                "fields; never pass a shell command, option, or pipeline string."
            ),
            (
                "- Before searching, identify the constraints that could change Skill "
                "selection. For one capability constraint, combine known high-signal "
                "formats, libraries, APIs, protocols, method names, and synonyms in "
                "`query`. For two or more independent capability constraints, use "
                "`queries` in the first `search`, one item per constraint, and set "
                "`per_query_limit`; do not split them into sequential searches. For "
                "`match=content`, separate alternatives for the same constraint with `|` (for example, "
                "`youtube|subtitle|字幕|翻译`); spaces mean consecutive text. When "
                "several directories are known, include all of them in the same `paths` list. "
                "Stop as soon as the result contains sufficient candidates. Broaden or "
                "rewrite only when a concrete task constraint is still uncovered; do "
                "not mechanically try several synonym groups."
            ),
            (
                "- Candidate names and descriptions are selection evidence. Select "
                "directly when they are sufficient. Use one `read` call to batch-read "
                "observed metadata only when candidates genuinely need comparison or "
                "their descriptions are insufficient; do not read complete `SKILL.md` "
                "files during discovery and selection. Report the smallest sufficient "
                "shortlist and never add weak matches to fill a quota."
            ),
            (
                "- When `list` or `search` may return multiple rows, append a `limit` "
                "stage to the ordered `pipeline`: use the user's requested count, or "
                "10 rows when none is given. Omit it only for an explicit request for "
                "all entries, a complete inventory, or no omissions; requests for more "
                "or broader results are not exhaustive. For only a total, set "
                "`output_mode=count`; `count` is not a fourth operation. When both a "
                "total and a limited sample are needed, issue one `output_mode=count` "
                "call and one limited entries call in the same model turn; a limited "
                "result does not imply the total."
            ),
            (
                "- If the tool says content was omitted for length, still complete the "
                "current answer from returned content; do not retry merely to expand "
                "visible entries. Only when the user explicitly requested display or "
                "enumeration and omission makes that request incomplete, ask after "
                "answering whether to continue and briefly note the extra context cost. "
                "Set `disable_output_truncation=true` only after explicit permission "
                "for output without truncation or collapsing."
            ),
            (
                "- Use only `skill_id` values and paths actually returned by the tool; "
                "never guess them. Claim that no installed Skill matches only after a "
                "directory-root search. A miss within one branch proves only that the "
                "Skill was not found in that scope."
            ),
        ]
        return "\n".join(lines)

    def _build_candidate_appendix(
        self,
        language: str,
        snapshot: SkillPromptSnapshot,
    ) -> str:
        small = snapshot.all_candidates_included
        if str(language).lower().startswith("en"):
            heading = "## Session Skill Candidate Snapshot"
            description = (
                "This complete installed-Skill metadata snapshot was captured when "
                "the session was created."
                if small
                else "These preset/frequently used Skill references were captured "
                "when the session was created; use `skill_index` for the complete directory."
            )
            empty = (
                "No enabled Skill was present when the session was created."
                if small
                else "No preset Skill reference is fixed in this session."
            )
        else:
            heading = "## 会话 Skill 候选快照"
            description = (
                "以下是会话创建时获取的完整已安装 Skill 元数据快照。"
                if small
                else "以下是会话创建时固定的预置/常用 Skill 引用；完整目录请使用 `skill_index`。"
            )
            empty = (
                "会话创建时没有已启用 Skill。"
                if small
                else "当前会话没有固定的预置 Skill 引用。"
            )
        return "\n".join(
            [
                heading,
                "",
                description,
                *_render_prompt_entries(snapshot.entries, empty_text=empty),
            ]
        )

    @staticmethod
    def _filter_legacy_list_skill_from_model_inputs(ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not tools:
            return

        filtered = []
        for tool in tools:
            name = SkillRetrievalPromptRail._model_tool_name(tool)
            if name not in _LEGACY_LIST_SKILL_TOOL_NAMES:
                filtered.append(tool)
        if len(filtered) != len(tools):
            inputs.tools = filtered

    @staticmethod
    def _model_tool_name(tool: Any) -> str:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                return str(function.get("name", "") or "")
            return str(tool.get("name", "") or "")
        return str(getattr(tool, "name", "") or "")

    @classmethod
    def _has_skill_index(cls, ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not tools:
            return False
        return any(
            cls._model_tool_name(tool) == _SKILL_INDEX_TOOL_NAME for tool in tools
        )


def _render_prompt_entries(
    entries: tuple[SkillPromptEntry, ...],
    *,
    empty_text: str,
) -> list[str]:
    if not entries:
        return [empty_text]
    return [
        f"- `{_compact_prompt_text(entry.worker_id)}`: "
        f"{_compact_prompt_text(entry.description)}"
        for entry in entries
    ]


def _compact_prompt_text(value: str) -> str:
    return " ".join(str(value or "").replace("`", "\\`").split())


__all__ = ["SkillRetrievalPromptRail"]
