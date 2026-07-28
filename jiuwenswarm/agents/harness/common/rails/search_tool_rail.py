# coding: utf-8
"""JiuWenSwarm's ProgressiveToolRail subclass.

ALL jiuwenswarm-specific changes live here — agent-core stays upstream-clean.
Overrides via MRO: base class calls self._search_tools / self._init_visible_tools /
self._build_navigation_section → Python resolves to our subclass versions.

What's here (nothing in agent-core is modified):
- Dense retrieval (_dense_search, _ensure_embedding_model, _precompute_tool_embeddings)
  backed by the agent-core-free ``common.tool_retrieval`` lib.
- Executable-corpus filter (_build_executable_corpus) — drops ghost tools
  (card registered but no resource_mgr instance).
- Hidden tool summary (_build_hidden_tool_summary, _HIDDEN_CATEGORY_*)
- Prompt isolation (priority 80, before_model_call + remove_section("tools"))
- DenseSearchTool registration (init override)

Removed in v3 (vestigial under name-based direct call, where ``tools[]`` never
changes): LRU + cap + three-tier demotion (active/idle/hidden) and the
``load_tools`` meta tool. Search results now live in the ToolMessage only;
``session_visible`` is no longer mutated by search, so there is nothing to
cap or evict.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from openjiuwen.harness.rails.progressive_tool_rail import (
    ProgressiveToolRail,
    _VISIBLE_TOOLS_KEY,
    _DISCOVERY_TRACE_KEY,
)

from jiuwenswarm.common import tool_retrieval

logger = logging.getLogger("jiuwenswarm.harness.common.rails.search_tool_rail")

_HIDDEN_CATEGORY_CN: Dict[str, tuple[str, str]] = {
    "todo": ("待办管理", "创建、查看、修改、获取待办事项"),
    "memory": ("记忆系统", "搜索、读取、写入、编辑持久化记忆"),
    "skill": ("技能管理", "搜索、安装、卸载、查看技能"),
    "wiki": ("LLM Wiki", "导入文件、自然语言查询、健康检查"),
    "cron": ("定时任务", "创建、查看、修改定时任务"),
    "session": ("会话管理", "创建、取消、查看后台会话任务"),
    "acp": ("外部 Agent", "向 ACP 兼容 agent 发送消息"),
    "search": ("网络搜索", "免费/付费搜索"),
    "web": ("网页抓取", "抓取网页文本内容"),
    "audio": ("音频处理", "识别音频时长和歌曲信息"),
    "document": ("文档处理", "PDF 读取等"),
    "spreadsheet": ("表格处理", "Excel/XLSX 生成与处理"),
    "runtime": ("运行时", "执行命令、读写文件等"),
    "comm": ("用户交互", "询问用户、发送文件/消息"),
    "media": ("多媒体", "图像/视频理解与生成"),
    "other": ("其它", "专业工具"),
}
_HIDDEN_CATEGORY_EN: Dict[str, tuple[str, str]] = {
    "todo": ("Todo management", "create/view/modify/get todos"),
    "memory": ("Memory system", "search/read/write/edit persistent memory"),
    "skill": ("Skill management", "search/install/uninstall skills"),
    "wiki": ("LLM Wiki", "ingest files, query knowledge base"),
    "cron": ("Scheduled tasks", "create/view/modify cron jobs"),
    "session": ("Session management", "create/cancel/view background sessions"),
    "acp": ("External agent", "send messages to ACP-compatible agents"),
    "search": ("Web search", "free/paid search"),
    "web": ("Webpage fetch", "fetch webpage text content"),
    "audio": ("Audio processing", "detect audio duration and song info"),
    "document": ("Document processing", "PDF reading, etc."),
    "spreadsheet": ("Spreadsheet", "Excel/XLSX generation and processing"),
    "runtime": ("Runtime", "run commands, read/write files"),
    "comm": ("User interaction", "ask user, send files/messages"),
    "media": ("Multimedia", "image/video understanding and generation"),
    "other": ("Other", "specialized tools"),
}


class JiuWenProgressiveToolRail(ProgressiveToolRail):
    """All jiuwenswarm-specific overrides. agent-core base class stays untouched."""

    priority = 80
    _navigation_extra_tools: Set[str] = {"code"}

    _RULES_CN = (
        "## 工具使用规则\n"
        "你正在一个渐进式工具环境中工作。\n"
        "\n"
        "1. 当你需要某个工具但它不在当前可用列表中时，"
        "用 `search_tools` 搜索。导航中的隐藏工具类别提示了哪些工具可搜。\n"
        "\n"
        "2. `search_tools` 返回工具的完整定义（含参数 JSON Schema）。"
        "返回的工具不在你的 tools 列表中，但已注册，可直接按 name 调用——"
        "根据 parameters 构造参数后直接发起 tool call，"
        "不会改变 tools 列表。\n"
        "\n"
        "3. 优先使用专业工具（如 memory_search、cron_create_job）"
        "而非通用替代（如 bash、edit_file）。"
        "专业工具提供结构化数据和状态管理。\n"
        "\n"
        "4. 工作流程：查看导航 → 搜索需要的工具 → 直接按名称调用。\n"
    )

    _RULES_EN = (
        "## Tool Usage Rules\n"
        "You are operating in a progressive tool environment.\n"
        "\n"
        "1. When you need a tool that isn't in your current available list, "
        "use `search_tools` to find it. The hidden tool categories in the "
        "navigation indicate what's searchable.\n"
        "\n"
        "2. `search_tools` returns full tool definitions (including parameter "
        "JSON Schema). The returned tools are NOT in your tools list but are "
        "registered and directly callable by name — construct arguments from "
        "the parameters and call directly. The tools list stays unchanged.\n"
        "\n"
        "3. Prefer specialized tools (e.g. memory_search, cron_create_job) "
        "over general substitutes (e.g. bash, edit_file). "
        "Specialized tools provide structured data and state management.\n"
        "\n"
        "4. Workflow: check navigation → search for needed tools → call "
        "directly by name.\n"
    )

    def __init__(self, config):
        super().__init__(config)
        self._desc_cap = int(getattr(config, "tool_retrieval_desc_cap", 256))
        self._embedding_model_name = getattr(
            config, "tool_retrieval_embedding_model", "BAAI/bge-small-zh-v1.5"
        )
        self._top_k_max = int(getattr(config, "tool_retrieval_top_k_max", 3))
        self._embedding_model = None
        self._cached_tool_embeddings: Dict[str, Any] = {}
        self._cached_tool_sig: frozenset = frozenset()
        self._search_corpus: List = []
        self._executable_sig: frozenset = frozenset()
        self._dense_retrieval_enabled = True
        if self._dense_retrieval_enabled:
            self._ensure_embedding_model()

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx):
        await super().before_invoke(ctx)
        self._build_executable_corpus(ctx)
        self._precompute_tool_embeddings()

    async def before_model_call(self, ctx):
        await super().before_model_call(ctx)
        builder = self._get_prompt_builder(ctx)
        builder.remove_section("tools")
        session = getattr(ctx, "session", None)

        # Debug: log prefill + active set before each LLM call.
        if session is not None:
            active = self._get_visible_tools(session)
            inputs_obj = getattr(ctx, "inputs", None)
            tools_in_inputs = []
            if inputs_obj and hasattr(inputs_obj, "tools"):
                tools_in_inputs = sorted([
                    getattr(t, "name", "") or (t.get("function", {}).get("name", "") if isinstance(t, dict) else "?")
                    for t in (inputs_obj.tools or [])
                ])
            logger.info(
                "[JiuWenRail] CONTEXT DEBUG | inputs.tools=%d %s | active=%d %s",
                len(tools_in_inputs), tools_in_inputs,
                len(active), sorted(active),
            )

    # ------------------------------------------------------------------
    # init override — register DenseSearchTool only
    # ------------------------------------------------------------------

    def init(self, agent) -> None:
        language = getattr(self._config, "language", "cn") or "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        from jiuwenswarm.agents.harness.common.tools.search_tool import DenseSearchTool
        tools = [
            DenseSearchTool(
                search_fn=self._search_tools,
                append_trace=self._append_trace,
                language=language,
                agent_id=agent_id,
                top_k_max=self._top_k_max,
                # load_fn=None: search results stay in the ToolMessage only;
                # they never enter session_visible, so tools[] (prefill) stays
                # constant across turns for prompt-cache stability. The LLM
                # calls matched tools by name directly — ability_manager
                # resolves by name regardless of the tools[] parameter.
                load_fn=None,
            ),
        ]
        self._meta_tool_names = {tool.card.name for tool in tools}
        if hasattr(agent, "ability_manager"):
            for tool in tools:
                try:
                    result = agent.ability_manager.add_ability(tool.card, tool)
                    if result.added:
                        self._owned_tool_names.add(tool.card.name)
                except Exception as exc:
                    logger.warning("[JiuWenRail] failed to register '%s': %s", tool.card.name, exc)

    # ------------------------------------------------------------------
    # _init_visible_tools — baseline only, no LRU/idle/turn state
    # ------------------------------------------------------------------

    def _init_visible_tools(self, session, *, default_visible_tools=None):
        if session is None:
            return
        current = session.get_state(_VISIBLE_TOOLS_KEY)
        if isinstance(current, list):
            return
        initial = list(dict.fromkeys(list(default_visible_tools or [])))
        session.update_state({_VISIBLE_TOOLS_KEY: initial})
        session.update_state({_DISCOVERY_TRACE_KEY: []})

    # ------------------------------------------------------------------
    # _search_tools — Dense + keyword fallback + shadow A/B
    # ------------------------------------------------------------------

    async def _search_tools(self, query, limit=10, detail_level=1):
        query = (query or "").strip()
        if not query:
            return []
        if self._embedding_model is not None and self._cached_tool_embeddings:
            return self._dense_search(query, limit, detail_level)
        return await super()._search_tools(query, limit, detail_level)

    def _dense_search(self, query, limit, detail_level):
        return tool_retrieval.dense_search(
            query,
            self._search_corpus or self._cached_all_tool_infos,
            self._embedding_model,
            self._cached_tool_embeddings,
            limit=limit,
            detail_level=detail_level,
            desc_cap=self._desc_cap,
        )

    # ------------------------------------------------------------------
    # Embedding + corpus helpers
    # ------------------------------------------------------------------

    def _ensure_embedding_model(self):
        if self._embedding_model is not None:
            return
        self._embedding_model = tool_retrieval.ensure_embedding_model(self._embedding_model_name)
        if self._embedding_model is not None:
            logger.info("[JiuWenRail] embedding model loaded: %s", self._embedding_model_name)

    def _build_executable_corpus(self, ctx):
        try:
            from openjiuwen.core.runner import Runner
        except Exception:
            self._search_corpus = list(self._cached_all_tool_infos or [])
            return
        agent = getattr(ctx, "agent", None)
        am = getattr(agent, "ability_manager", None) if agent else None
        session = getattr(ctx, "session", None)
        sig = frozenset(str(getattr(t, "name", "") or "") for t in (self._cached_all_tool_infos or []))
        if sig == self._executable_sig and self._search_corpus:
            return
        self._executable_sig = sig

        def resolver(name):
            tool_id = name
            card = None
            if am is not None:
                try:
                    card = am.get(name)
                except Exception:
                    card = None
                cid = getattr(card, "id", None) if card else None
                if cid:
                    tool_id = cid
            try:
                if Runner.resource_mgr.get_tool(tool_id=tool_id, session=session) is not None:
                    return True
            except Exception:
                pass
            # Fallback: ability_manager resolves the card by name → the tool
            # is directly callable in the name-based direct-call model (e.g.
            # session/runtime tools registered via ability_manager.add(card),
            # such as send_file_to_user, whose resource_mgr probe key may not
            # match). Such tools are NOT ghosts and must stay searchable.
            return card is not None

        self._search_corpus = tool_retrieval.filter_executable(
            list(self._cached_all_tool_infos or []), resolver,
        )

    def _precompute_tool_embeddings(self):
        if self._embedding_model is None:
            return
        all_tools = self._search_corpus or self._cached_all_tool_infos
        if not all_tools:
            return
        sig = frozenset(str(getattr(t, "name", "") or "") for t in all_tools)
        if sig == self._cached_tool_sig and self._cached_tool_embeddings:
            return
        self._cached_tool_sig = sig
        tool_retrieval.precompute_embeddings(
            all_tools,
            self._embedding_model,
            self._cached_tool_embeddings,
            desc_cap=self._desc_cap,
        )

    # ------------------------------------------------------------------
    # Hidden tool summary
    # ------------------------------------------------------------------

    def _hidden_tool_category(self, tool_name, description=""):
        text = f"{tool_name} {description}".lower()
        name_tokens = tool_name.lower().split("_")
        if "send_file" in text or "发送文件" in text:
            return "comm"
        for cat, _ in _HIDDEN_CATEGORY_CN.items():
            if cat in name_tokens:
                return cat
        if "cron" in text:
            return "cron"
        if "memory" in text or "记忆" in text:
            return "memory"
        if "skill" in text or "技能" in text:
            return "skill"
        if "wiki" in text or "知识库" in text:
            return "wiki"
        if "session" in text or "会话" in text:
            return "session"
        if "search" in text or "搜索" in text:
            return "search"
        if "web" in text or "网页" in text or "fetch" in text:
            return "web"
        if "todo" in text or "待办" in text:
            return "todo"
        if "audio" in text or "音频" in text:
            return "audio"
        if "acp" in text:
            return "acp"
        return "other"

    async def _build_hidden_tool_summary(self, session, language="cn"):
        all_tools = await self._get_real_tool_infos()
        loaded = set(self._get_visible_tools(session))
        baseline = set(self.always_visible_tools) | set(self.default_visible_tools) | set(self._navigation_extra_tools)
        shown = baseline | loaded | set(self._meta_tool_names)
        hidden = [t for t in all_tools if str(getattr(t, "name", "") or "") not in shown]
        if not hidden:
            return []
        cats = {}
        for tool in hidden:
            name = str(getattr(tool, "name", "") or "")
            desc = str(getattr(tool, "description", "") or "")
            cat = self._hidden_tool_category(name, desc)
            cats.setdefault(cat, []).append(name)
        table = _HIDDEN_CATEGORY_CN if language != "en" else _HIDDEN_CATEGORY_EN
        entries = []
        for cat in sorted(cats.keys()):
            label, capability = table.get(cat, table["other"])
            count = len(cats[cat])
            names = ", ".join(sorted(cats[cat]))
            if language != "en":
                entries.append(f"- {label}（{count} 个）：{capability}。工具：{names}")
            else:
                entries.append(f"- {label} ({count}): {capability}. Tools: {names}")
        if entries:
            if language != "en":
                entries.insert(0, "### 隐藏工具（需 search_tools 发现）")
            else:
                entries.insert(0, "### Hidden Tools (use search_tools to discover)")
        return entries

    # ------------------------------------------------------------------
    # Navigation + rules section overrides
    # ------------------------------------------------------------------

    _NAV_HEADER_CN = (
        "## 工具导航\n"
        "以下条目用于帮助你理解当前 session 下的工具生态。\n"
        "请注意：这里展示的是「工具地图」，不是「全部可立即调用的工具清单」。\n"
        "工具分为两类：可直接调用、隐藏（需 search_tools 发现）。\n"
        "调用 search_tools 搜索后，匹配的工具会以完整定义（含参数 JSON Schema）"
        "返回在结果中，可直接按名称调用，不会改变当前 tools 列表。\n"
    )

    _NAV_HEADER_EN = (
        "## Tool Navigation\n"
        "The entries below help you understand the tool ecosystem available "
        "in the current session.\n"
        "Treat this section as a tool map, not as a full list of immediately "
        "callable tools.\n"
        "Tools fall into two categories: directly callable and hidden (use "
        "search_tools to discover).\n"
        "After calling search_tools, matched tools are returned with full "
        "definitions (including parameter JSON Schema) in the result and are "
        "directly callable by name. The tools list stays unchanged.\n"
    )

    async def _build_navigation_section(self, session):
        from openjiuwen.harness.prompts.builder import PromptSection
        from openjiuwen.harness.prompts.sections import SectionName

        entries_cn = await self._build_navigation_entries(session, language="cn")
        entries_en = await self._build_navigation_entries(session, language="en")

        hidden_cn = await self._build_hidden_tool_summary(session, language="cn")
        hidden_en = await self._build_hidden_tool_summary(session, language="en")
        if hidden_cn:
            entries_cn = [*entries_cn, *hidden_cn]
        if hidden_en:
            entries_en = [*entries_en, *hidden_en]

        cn_text = self._NAV_HEADER_CN + "\n" + "\n".join(entries_cn) if entries_cn else self._NAV_HEADER_CN
        en_text = self._NAV_HEADER_EN + "\n" + "\n".join(entries_en) if entries_en else self._NAV_HEADER_EN
        return PromptSection(
            name=SectionName.TOOL_NAVIGATION,
            content={"cn": cn_text, "en": en_text},
            priority=70,
        )

    def _build_progressive_tool_rules_section(self):
        from openjiuwen.harness.prompts.builder import PromptSection
        from openjiuwen.harness.prompts.sections import SectionName
        return PromptSection(
            name=SectionName.PROGRESSIVE_TOOL_RULES,
            content={"cn": self._RULES_CN, "en": self._RULES_EN},
            priority=75,
        )
