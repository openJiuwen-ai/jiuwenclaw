# coding: utf-8
"""JiuWenSwarm's self-contained on-demand tool-retrieval rail.

Decoupled from openjiuwen's ProgressiveToolRail: extends DeepAgentRail directly
and inlines the subset of base helpers we use (see the class docstring). ALL
jiuwenswarm-specific changes live here — agent-core stays upstream-clean.

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

import asyncio
import logging
from typing import Any, Dict, List, Set

from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common import tool_retrieval

logger = logging.getLogger("jiuwenswarm.harness.common.rails.search_tool_rail")

# Session-state keys (previously imported from openjiuwen's ProgressiveToolRail;
# inlined here so this rail no longer depends on that base class, which may be
# removed during swarm slimming).
_VISIBLE_TOOLS_KEY = "__progressive_visible_tool_names__"
_DISCOVERY_TRACE_KEY = "__progressive_tool_discovery_trace__"

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


class JiuWenProgressiveToolRail(DeepAgentRail):
    """Self-contained on-demand tool-retrieval rail.

    Decoupled from openjiuwen's ProgressiveToolRail: extends DeepAgentRail
    directly and inlines the subset of base helpers we actually use (tool
    inventory, visible-tools session state, prompt-builder access, navigation
    entries). Drops the v1 baggage (load_tools / LRU / three-tier demotion)
    we never used, and removes the dependency on the upstream ProgressiveToolRail
    base (which may be deleted during swarm slimming).
    """

    priority = 80
    _navigation_extra_tools: Set[str] = {"code"}

    # Legacy single-entry tools that have a flat-fields replacement.
    # Excluded from the search index so the LLM picks the specific tool
    # (e.g. cron_create_job) instead of the generic wrapper (cron).
    _EXCLUDED_FROM_SEARCH: Set[str] = {"cron"}

    _RULES_CN = (
        "## 工具使用规则\n"
        "你正在一个渐进式工具环境中工作。\n"
        "\n"
        "1. 当你需要某个工具但它不在当前可用列表中时，"
        "用 `search_tools` 搜索。导航中的隐藏工具已按类列出工具名和描述，"
        "供你了解有哪些工具可用。\n"
        "\n"
        "2. 导航中列出的专用工具必须优先使用。"
        "只有当导航中没有对应专用工具时，才用 bash、write_file 等通用工具。"
        "例如：查 wiki 用 wiki_query 而非 bash，创建定时任务用 cron_create_job 而非 bash。\n"
        "\n"
        "3. `search_tools` 返回工具的完整定义（含参数 JSON Schema）。"
        "返回的工具不在你的 tools 列表中，但已注册，可直接按 name 调用——"
        "根据 parameters 构造参数后直接发起 tool call，"
        "不会改变 tools 列表。\n"
        "\n"
        "4. search_tools 的 query 推荐用「工具名 + 简短中文描述」的组合"
        "（如 `cron_create_job 创建定时任务`、`free_search 免费网页搜索`）。"
        "这种组合最准：工具名走精确匹配，描述提供语义，系统按混合信号找到最佳工具。"
        "若不确定工具名，仅用描述亦可。\n"
        "\n"
        "5. 如果当前对话历史中已有某工具的 schema（来自之前的 "
        "`search_tools` 返回），切勿再次搜索同一工具，"
        "直接根据已有 schema 构造参数并调用。"
        "确定了要用哪个工具就别犹豫，直接调；调失败再看错误信息调整。\n"
    )

    _RULES_EN = (
        "## Tool Usage Rules\n"
        "You are operating in a progressive tool environment.\n"
        "\n"
        "1. When you need a tool that isn't in your current available list, "
        "use `search_tools` to find it. Hidden tools in the navigation are "
        "listed by category with names and descriptions, so you know what's "
        "available.\n"
        "\n"
        "2. Specialized tools listed in the navigation MUST be used first. "
        "Only use bash, write_file, or other general tools when no specialized "
        "tool exists for the task. Example: use wiki_query not bash for wiki, "
        "use cron_create_job not bash for cron.\n"
        "\n"
        "3. `search_tools` returns full tool definitions (including parameter "
        "JSON Schema). The returned tools are NOT in your tools list but are "
        "registered and directly callable by name — construct arguments from "
        "the parameters and call directly. The tools list stays unchanged.\n"
        "\n"
        "4. Prefer a `search_tools` query that pairs a tool name with a short "
        "description (e.g. `cron_create_job create scheduled task`, "
        "`free_search free web search`). This is most accurate: the name "
        "enables exact matching and the description adds semantic signal. "
        "If unsure of the name, a description alone also works.\n"
        "\n"
        "5. If a tool's schema already appears in conversation history "
        "(from a previous `search_tools` result), do NOT search for it "
        "again — reuse the existing schema and call directly. Once you've "
        "decided which tool to use, don't hesitate — call it; if it fails, "
        "adjust based on the error.\n"
    )

    def __init__(self, config):
        super().__init__()
        # Base scaffolding (previously inherited from ProgressiveToolRail).
        self._config = config
        self.default_visible_tools = set(getattr(config, "progressive_tool_default_visible_tools", []) or [])
        self.always_visible_tools = set(getattr(config, "progressive_tool_always_visible_tools", []) or [])
        self._meta_tool_names: Set[str] = set()
        self._owned_tool_names: Set[str] = set()
        self._cached_all_tool_infos: List[ToolInfo] = []
        self._deep_agent = None
        self._runtime_agent = None
        # JiuWen retrieval knobs.
        self._desc_cap = int(getattr(config, "tool_retrieval_desc_cap", 256))
        self._embedding_model_name = getattr(
            config, "tool_retrieval_embedding_model", "BAAI/bge-small-zh-v1.5"
        )
        self._top_k_max = int(getattr(config, "tool_retrieval_top_k_max", 3))
        self._min_sim = float(getattr(config, "tool_retrieval_min_sim", 0.35))
        self._method = str(getattr(config, "tool_retrieval_method", "bm25")).lower()
        # v3: dense disabled by default — BM25 + CJK n-gram covers pure-Chinese
        # queries without a 90M model / fastembed / network. Set
        # tool_retrieval_dense_enabled: true to opt back into hybrid (model is
        # then loaded eagerly in __init__).
        self._dense_enabled = bool(getattr(config, "tool_retrieval_dense_enabled", False))
        self._embedding_model = None
        self._cached_tool_embeddings: Dict[str, Any] = {}
        self._cached_tool_sig: frozenset = frozenset()
        # BM25 index + sig cache (pure-text, ghost-probe-free → sig is safe).
        self._bm25_index = None
        self._cached_bm25_sig: frozenset = frozenset()
        self._search_corpus: List = []
        if self._dense_enabled:
            self._ensure_embedding_model()

    # ------------------------------------------------------------------
    # Lifecycle (inlined from base; no longer inherits ProgressiveToolRail)
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx):
        # Refresh the full tool inventory from ability_manager (each turn).
        self._runtime_agent = ctx.agent
        self._cached_all_tool_infos = await self._list_tool_infos(ctx.agent)
        # DEBUG: log all registered tool names
        all_names = sorted(str(getattr(t, "name", "") or "") for t in self._cached_all_tool_infos)
        logger.info("[JiuWenRail] registered tools (%d): %s", len(all_names), all_names)
        session = getattr(ctx, "session", None)
        self._init_visible_tools(
            session, default_visible_tools=list(self.default_visible_tools)
        )
        await asyncio.to_thread(self._build_executable_corpus, ctx)
        if self._dense_enabled:
            await asyncio.to_thread(self._precompute_tool_embeddings)
        await asyncio.to_thread(self._build_bm25_index)

    async def before_model_call(self, ctx):
        session = getattr(ctx, "session", None)
        builder = self._get_prompt_builder(ctx)

        # Inject navigation + rules sections (our overrides).
        navigation_section = await self._build_navigation_section(session)
        rules_section = self._build_progressive_tool_rules_section()
        builder.add_section(navigation_section)
        builder.add_section(rules_section)

        # Filter inputs.tools: keep only meta + baseline(always-visible) +
        # session-visible. search never mutates session_visible (v3 name-based
        # direct call; results stay in the ToolMessage only), so tools[] stays
        # constant across turns → prompt-cache stable.
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if isinstance(tools, list):
            keep = (
                set(self._meta_tool_names)
                | set(self.always_visible_tools)
                | set(self._get_visible_tools(session))
            )

            def _tool_name(t):
                n = getattr(t, "name", "")
                if n:
                    return str(n)
                if isinstance(t, dict):
                    return str((t.get("function", {}) or {}).get("name", "") or "")
                return ""

            inputs.tools = [t for t in tools if _tool_name(t) in keep]

        # Remove the static "tools" section (nav replaces it).
        builder.remove_section("tools")

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
        self._deep_agent = agent
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
    # Tool inventory + session state + prompt builder
    # (ported from openjiuwen ProgressiveToolRail; only what we use)
    # ------------------------------------------------------------------

    def uninit(self, agent) -> None:
        """Remove meta tools registered by this rail (teardown)."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove_ability(tool_name)
                except Exception as exc:
                    logger.warning("[JiuWenRail] failed to remove '%s': %s", tool_name, exc)
        self._owned_tool_names.clear()
        self._meta_tool_names.clear()
        self._cached_all_tool_infos = []

    async def _list_tool_infos(self, agent) -> List[ToolInfo]:
        """List all tool infos currently registered on the agent."""
        if not hasattr(agent, "ability_manager"):
            return []
        try:
            tool_infos = await agent.ability_manager.list_tool_info()
            return list(tool_infos or [])
        except Exception as exc:
            logger.warning("[JiuWenRail] failed to list tool infos: %s", exc)
            return []

    async def _list_all_tool_infos(self) -> List[ToolInfo]:
        """Return cached full tool inventory."""
        return list(self._cached_all_tool_infos or [])

    async def _get_real_tool_infos(self) -> List[ToolInfo]:
        """Return non-meta tools from the cached inventory."""
        infos = await self._list_all_tool_infos()
        return [
            tool
            for tool in infos
            if getattr(tool, "name", "") not in self._meta_tool_names
        ]

    def _get_visible_tools(self, session) -> List[str]:
        """Read current session-visible tool names."""
        if session is None:
            return []
        state = session.get_state(_VISIBLE_TOOLS_KEY)
        if isinstance(state, list):
            return [str(item).strip() for item in state if str(item).strip()]
        return []

    def _append_trace(self, session, event) -> None:
        """Append progressive-tool discovery trace into session state."""
        if session is None:
            return
        trace = session.get_state(_DISCOVERY_TRACE_KEY)
        if not isinstance(trace, list):
            trace = []
        trace.append(event)
        session.update_state({_DISCOVERY_TRACE_KEY: trace})

    @staticmethod
    def _get_prompt_builder(ctx: AgentCallbackContext) -> SystemPromptBuilder:
        """Fetch persistent SystemPromptBuilder from agent."""
        agent = getattr(ctx, "agent", None)
        if agent is None:
            raise RuntimeError("JiuWenProgressiveToolRail requires ctx.agent to exist.")
        builder = getattr(agent, "system_prompt_builder", None)
        if not isinstance(builder, SystemPromptBuilder):
            raise RuntimeError(
                "JiuWenProgressiveToolRail requires agent.system_prompt_builder "
                "to be a SystemPromptBuilder instance."
            )
        return builder

    # ------------------------------------------------------------------
    # Navigation entries (ported from base; build_navigation_entry inlined)
    # ------------------------------------------------------------------

    async def _build_navigation_entries(self, session, language: str = "cn") -> List[str]:
        all_tools = await self._get_real_tool_infos()
        loaded = set(self._get_visible_tools(session))
        baseline = (
            set(self.always_visible_tools)
            | set(self.default_visible_tools)
            | set(self._navigation_extra_tools)
        )
        entries: List[str] = []
        seen: Set[str] = set()

        def include_tool(name: str) -> bool:
            if name in seen:
                return False
            if name in baseline:
                return True
            if name in loaded:
                return True
            if name in {"code", "read_file", "bash", "list_skill", "pdf", "xlsx"}:
                return True
            return False

        sorted_tools = sorted(
            all_tools,
            key=lambda t: (
                self._tool_group_rank(t),
                str(getattr(t, "name", "") or ""),
            ),
        )

        for tool in sorted_tools:
            name = str(getattr(tool, "name", "") or "")
            if not name or not include_tool(name):
                continue
            seen.add(name)
            summary = self._tool_summary_for_navigation(tool)
            group = self._tool_group_for_navigation(tool)
            if language == "en":
                status = (
                    "callable"
                    if name in loaded or name in self.always_visible_tools
                    else "navigation-only"
                )
                group_label = group
            else:
                status = (
                    "可调用"
                    if name in loaded or name in self.always_visible_tools
                    else "仅导航"
                )
                group_label = self._tool_group_to_cn(group)
            entries.append(
                self._format_nav_entry(
                    name=name,
                    group=group_label,
                    status=status,
                    summary=summary,
                    language=language,
                )
            )
        return entries

    @staticmethod
    def _format_nav_entry(*, name, group, status, summary, language="cn") -> str:
        if language == "en":
            return f"- {name} [{group}, {status}]: {summary}"
        return f"- {name} [{group}, {status}]：{summary}"

    @staticmethod
    def _tool_summary_for_navigation(tool) -> str:
        description = str(getattr(tool, "description", "") or "").strip()
        if not description:
            return "No summary available."
        line = description.splitlines()[0].strip()
        return line[:160]

    @staticmethod
    def _tool_group_for_navigation(tool) -> str:
        name = str(getattr(tool, "name", "") or "").lower()
        description = str(getattr(tool, "description", "") or "").lower()
        if any(k in name for k in ["read", "write", "edit", "file", "bash", "code"]):
            return "runtime"
        if any(k in name for k in ["pdf", "invoice", "document"]):
            return "document"
        if any(k in name for k in ["xlsx", "excel", "sheet", "spreadsheet"]):
            return "spreadsheet"
        if "skill" in name:
            return "skill"
        if any(k in description for k in ["pdf", "invoice", "document"]):
            return "document"
        if any(k in description for k in ["xlsx", "excel", "spreadsheet"]):
            return "spreadsheet"
        return "general"

    @staticmethod
    def _tool_group_to_cn(group: str) -> str:
        return {
            "skill": "技能",
            "runtime": "运行时",
            "document": "文档",
            "spreadsheet": "表格",
            "general": "通用",
        }.get(group, "通用")

    @staticmethod
    def _tool_group_rank(tool) -> int:
        group = JiuWenProgressiveToolRail._tool_group_for_navigation(tool)
        return {"skill": 0, "runtime": 1, "document": 2, "spreadsheet": 3, "general": 9}.get(group, 99)

    # ------------------------------------------------------------------
    # _search_tools — dispatch by ``method`` (hybrid/dense/bm25), name as
    # the final fallback. The hand-rolled dense→bm25→name chain was replaced
    # so that ``method`` config actually drives retrieval (hybrid RRF was
    # never reached before; dense-unavailable → BM25 degradation lives in
    # ``tool_retrieval.search``). Name lookup is the last resort: it catches
    # exact tool-name queries that semantic search missed (e.g. the LLM
    # searched by ``send_file_to_user`` but dense returned write_file) and
    # runtime-registered tools absent from the search corpus.
    # ------------------------------------------------------------------

    async def _search_tools(self, query, limit=10, detail_level=1):
        query = (query or "").strip()
        if not query:
            return []
        # Main path: dispatch by self._method. The embedding cache / BM25
        # index are built lazily inside _search's callees if missing.
        results = await asyncio.to_thread(self._search, query, limit, detail_level)
        if results:
            return results

        # Fallback: exact name lookup from the full inventory (also recovers
        # runtime-registered tools missing from the search corpus).
        name_hits = self._lookup_tool_by_name(query, detail_level=detail_level)
        if name_hits:
            return name_hits[:limit]

        # Last resort: the corpus may be stale (tools registered after the
        # last before_invoke refresh). Rebuild + retry once.
        retried = await self._force_refresh_and_retry(query, limit, detail_level)
        if retried:
            return retried

        return []

    async def _force_refresh_and_retry(self, query, limit, detail_level):
        agent = self._runtime_agent or self._deep_agent
        if agent is None:
            return []
        live_infos = await self._list_tool_infos(agent)
        # Compare the name set, not just the count: a tool swap (one
        # unregistered, another registered) leaves the count unchanged while
        # the corpus is genuinely stale.
        live_sig = frozenset(
            str(getattr(t, "name", "") or "") for t in live_infos
        )
        cached_sig = frozenset(
            str(getattr(t, "name", "") or "") for t in (self._cached_all_tool_infos or [])
        )
        if live_sig == cached_sig:
            return []
        logger.info(
            "[JiuWenRail] force-refresh: corpus stale (%d -> %d tools), rebuilding + retrying",
            len(self._cached_all_tool_infos), len(live_infos),
        )
        self._cached_all_tool_infos = live_infos
        self._search_corpus = list(live_infos)
        self._cached_tool_sig = frozenset()
        self._cached_bm25_sig = frozenset()
        if self._dense_enabled:
            await asyncio.to_thread(self._precompute_tool_embeddings)
        await asyncio.to_thread(self._build_bm25_index)
        return await asyncio.to_thread(self._search, query, limit, detail_level)

    def _search(self, query, limit, detail_level):
        """Dispatch to dense / bm25 / hybrid by ``self._method``.

        Degradation is handled inside ``tool_retrieval.search``: embedding
        unavailable → BM25 (never []). The old ``_dense_search`` is kept
        below for reference/tests but is no longer the call path.

        Lazy-init guard: if the embedding cache / BM25 index weren't built
        yet (e.g. first search of the session, or model finished loading
        after before_invoke), build them now in bulk. Without this,
        ``dense_search`` would fall back to per-tool ``embed_single`` calls
        (44 model invocations instead of one batched embed).
        """
        if self._embedding_model is not None and not self._cached_tool_embeddings:
            self._precompute_tool_embeddings()
        if self._bm25_index is None and self._method != "semantic":
            self._build_bm25_index()
        return tool_retrieval.dispatch_search(
            query,
            self._search_corpus or self._cached_all_tool_infos,
            method=self._method,
            embedding_model=self._embedding_model,
            embedding_cache=self._cached_tool_embeddings,
            bm25_index=self._bm25_index,
            limit=limit,
            detail_level=detail_level,
            desc_cap=self._desc_cap,
            min_sim=self._min_sim,
        )

    def _dense_search(self, query, limit, detail_level):
        """Legacy dense-only path. Kept for tests; production uses _search."""
        return tool_retrieval.dense_search(
            query,
            self._search_corpus or self._cached_all_tool_infos,
            self._embedding_model,
            self._cached_tool_embeddings,
            limit=limit,
            detail_level=detail_level,
            desc_cap=self._desc_cap,
            min_sim=self._min_sim,
        )

    def _bm25_search_fallback(self, query, limit, detail_level):
        """BM25-only search (fallback when dense is unavailable or empty)."""
        from jiuwenswarm.common.tool_retrieval import bm25_search
        return bm25_search(
            query,
            self._search_corpus or self._cached_all_tool_infos,
            self._bm25_index,
            limit=limit,
            detail_level=detail_level,
            desc_cap=self._desc_cap,
            min_sim=0.0,
        )

    def _lookup_tool_by_name(self, query, *, detail_level=1):
        """Resolve a query that already looks like a tool name directly from
        the full inventory, bypassing ``_search_corpus``.

        A runtime-registered tool (e.g. ``send_file_to_user``) can be missing
        from ``_search_corpus`` due to ghost-filter timing while still being
        listed in ``_cached_all_tool_infos`` (which navigation and the hidden
        summary read from). When the model searches by the tool's own name,
        this fast-path recovers it instead of returning no match — the exact
        scenario where dense retrieval consistently failed in practice.

        If the cache misses, fall back to a live ``ability_manager`` lookup
        so tools registered after the last ``before_invoke`` refresh are
        still found by name.
        """
        ql = query.strip().lower()
        # "looks like a tool name": contains '_' and is alphanumeric once
        # underscores are stripped — same heuristic as the retry guard below.
        if not ql or "_" not in ql or not ql.replace("_", "").isalnum():
            return []
        ql_norm = "_".join(ql.split())
        hits = []
        for tool in (self._cached_all_tool_infos or []):
            name = str(getattr(tool, "name", "") or "").lower()
            if name and (name == ql or name == ql_norm):
                hits.append(
                    tool_retrieval.build_tool_summary(tool, detail_level=detail_level)
                )
        if hits:
            return hits
        # Cache miss — try a live lookup from ability_manager so that
        # runtime-registered tools (e.g. send_file_to_user added after the
        # last before_invoke refresh) are still found by exact name.
        agent = self._runtime_agent or self._deep_agent
        am = getattr(agent, "ability_manager", None) if agent else None
        if am is not None:
            try:
                card = am.get(query)
            except Exception:
                card = None
            if card is None and query != ql_norm:
                try:
                    card = am.get(ql_norm)
                except Exception:
                    card = None
            if card is not None:
                logger.info(
                    "[JiuWenRail] name lookup cache miss, live hit: %s",
                    query,
                )
                hits.append(
                    tool_retrieval.build_tool_summary(card, detail_level=detail_level)
                )
        return hits

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
        # Re-filter every turn (no sig cache): a runtime-registered tool
        # (e.g. send_file_to_user) can be momentarily misclassified as a
        # ghost when am.get / resource_mgr.get_tool misses due to registration
        # timing. Caching the sig would lock that misclassification in —
        # _search_corpus would stay missing the tool while navigation (which
        # reads _cached_all_tool_infos directly) still lists it, so search
        # never recovers it. am.get and resource_mgr probes are local dict
        # lookups; the embedding cache (_cached_tool_sig in
        # _precompute_tool_embeddings) is independent and still short-circuits.
        def resolver(name):
            tool_id = name
            card = None
            if am is not None:
                try:
                    card = am.get(name)
                except Exception as exc:
                    logger.debug(
                        "[JiuWenRail] ability_manager.get failed for %s: %s", name, exc
                    )
                    card = None
                cid = getattr(card, "id", None) if card else None
                if cid:
                    tool_id = cid
            try:
                if Runner.resource_mgr.get_tool(tool_id=tool_id, session=session) is not None:
                    return True
            except Exception as exc:
                logger.debug(
                    "[JiuWenRail] resource_mgr.get_tool probe failed for %s: %s", name, exc
                )
            # Fallback: ability_manager resolves the card by name → the tool
            # is directly callable in the name-based direct-call model (e.g.
            # session/runtime tools registered via ability_manager.add(card),
            # such as send_file_to_user, whose resource_mgr probe key may not
            # match). Such tools are NOT ghosts and must stay searchable.
            return card is not None

        self._search_corpus = tool_retrieval.filter_executable(
            list(self._cached_all_tool_infos or []), resolver,
        )
        if self._EXCLUDED_FROM_SEARCH:
            self._search_corpus = [
                t for t in self._search_corpus
                if str(getattr(t, "name", "") or "") not in self._EXCLUDED_FROM_SEARCH
            ]

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

    def _build_bm25_index(self):
        """Build/refresh the BM25 index over the search corpus.

        Sig cache is safe here: BM25 is a pure-text index over haystacks (no
        ghost-probe timing like the executable-corpus filter), so a name-set
        sig correctly identifies an unchanged corpus. Kept separate from
        ``_cached_tool_sig`` (dense) because their invalidation conditions
        differ — BM25 only depends on text, dense depends on model + text.
        """
        all_tools = self._search_corpus or self._cached_all_tool_infos
        if not all_tools:
            self._bm25_index = None
            return
        sig = frozenset(str(getattr(t, "name", "") or "") for t in all_tools)
        if sig == self._cached_bm25_sig and self._bm25_index is not None:
            return
        self._cached_bm25_sig = sig
        self._bm25_index = tool_retrieval.build_bm25_index(
            all_tools, desc_cap=self._desc_cap,
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
            cats.setdefault(cat, []).append(tool)
        table = _HIDDEN_CATEGORY_CN if language != "en" else _HIDDEN_CATEGORY_EN
        entries = []
        for cat in sorted(cats.keys()):
            label, capability = table.get(cat, table["other"])
            tools_in_cat = sorted(cats[cat], key=lambda t: str(getattr(t, "name", "") or ""))
            count = len(tools_in_cat)
            if language != "en":
                entries.append(f"- {label}（{count} 个）：{capability}")
                for tool in tools_in_cat:
                    name = str(getattr(tool, "name", "") or "")
                    summary = self._tool_summary_for_navigation(tool)
                    entries.append(f"  - {name}：{summary}")
            else:
                entries.append(f"- {label} ({count}): {capability}")
                for tool in tools_in_cat:
                    name = str(getattr(tool, "name", "") or "")
                    summary = self._tool_summary_for_navigation(tool)
                    entries.append(f"  - {name}: {summary}")
        if entries:
            if language != "en":
                entries.insert(0, "### 隐藏工具（按类列出名字与描述，用工具名当 query 调 search_tools 即可发现并直接调用）")
            else:
                entries.insert(0, "### Hidden Tools (names + descriptions listed by category; use a tool name as the query to search_tools to discover and call directly)")
        return entries

    # ------------------------------------------------------------------
    # Navigation + rules section overrides
    # ------------------------------------------------------------------

    _NAV_HEADER_CN = (
        "## 工具导航\n"
        "以下条目用于帮助你理解当前 session 下的工具生态。\n"
        "请注意：这里展示的是「工具地图」，不是「全部可立即调用的工具清单」。\n"
        "工具分为两类：可直接调用、隐藏（已按类列出名字与描述，"
        "用工具名当 query 调 search_tools 即可发现并直接调用）。\n"
        "调用 search_tools 搜索后，匹配的工具会以完整定义（含参数 JSON Schema）"
        "返回在结果中，可直接按名称调用，不会改变当前 tools 列表。\n"
    )

    _NAV_HEADER_EN = (
        "## Tool Navigation\n"
        "The entries below help you understand the tool ecosystem available "
        "in the current session.\n"
        "Treat this section as a tool map, not as a full list of immediately "
        "callable tools.\n"
        "Tools fall into two categories: directly callable and hidden (names "
        "and descriptions listed by category; use a tool name as the query to "
        "search_tools to discover and call directly).\n"
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
