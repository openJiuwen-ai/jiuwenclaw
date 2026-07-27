# coding: utf-8
"""JiuWenSwarm's ProgressiveToolRail subclass.

ALL jiuwenswarm-specific changes live here — agent-core stays upstream-clean.
Overrides via MRO: base class calls self._search_tools / self._init_visible_tools /
self._build_navigation_section → Python resolves to our subclass versions.

What's here (nothing in agent-core is modified):
- LRU + cap (_add_loaded_tools, _select_lru_eviction, _touch_tool, after_tool_call)
- Dense retrieval (_dense_search, _ensure_embedding_model, _precompute_tool_embeddings)
- Hidden tool summary (_build_hidden_tool_summary, _HIDDEN_CATEGORY_*)
- Prompt isolation (priority 80, before_model_call + remove_section)
- New rules (_build_progressive_tool_rules_section override)
- DenseSearchTool registration (init override)
"""
from __future__ import annotations

import logging
import inspect
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel

from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.harness.rails.progressive_tool_rail import (
    ProgressiveToolRail,
    _VISIBLE_TOOLS_KEY,
    _DISCOVERY_TRACE_KEY,
)
from openjiuwen.harness.prompts.sections.progressive_tool_rail import (
    build_multilingual_navigation_section,
)

logger = logging.getLogger("jiuwenswarm.harness.common.rails.progressive_tool_rail_jiuwen")

_VISIBLE_TOOLS_LRU_KEY = "__progressive_visible_tool_lru__"

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
        "2. `search_tools` 找到的工具会自动加载，下一轮可直接调用。\n"
        "\n"
        "3. 优先使用专业工具（如 memory_search、cron_create_job）"
        "而非通用替代（如 bash、edit_file）。"
        "专业工具提供结构化数据和状态管理。\n"
        "\n"
        "4. 工作流程：查看导航 → 搜索需要的工具 → 直接调用。\n"
    )

    _RULES_EN = (
        "## Tool Usage Rules\n"
        "You are operating in a progressive tool environment.\n"
        "\n"
        "1. When you need a tool that isn't in your current available list, "
        "use `search_tools` to find it. The hidden tool categories in the "
        "navigation indicate what's searchable.\n"
        "\n"
        "2. Tools found by `search_tools` are auto-loaded and callable in "
        "the next turn.\n"
        "\n"
        "3. Prefer specialized tools (e.g. memory_search, cron_create_job) "
        "over general substitutes (e.g. bash, edit_file). "
        "Specialized tools provide structured data and state management.\n"
        "\n"
        "4. Workflow: check navigation → search for needed tools → call directly.\n"
    )

    def __init__(self, config):
        super().__init__(config)
        self.max_loaded_tools = 16
        self._embedding_model = None
        self._cached_tool_embeddings: Dict[str, Any] = {}
        self._cached_tool_sig: frozenset = frozenset()
        self._dense_retrieval_enabled = True
        self._ability_snapshot_done = False
        if self._dense_retrieval_enabled:
            self._ensure_embedding_model()

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx):
        await super().before_invoke(ctx)
        self._precompute_tool_embeddings()
        if not self._ability_snapshot_done:
            self._ability_snapshot_done = True
            try:
                from jiuwenswarm.common.prompt_capture import get_capture as _get_capture
                cap = _get_capture()
                if cap is not None:
                    cap.snapshot_ability_manager(getattr(ctx, "agent", None))
            except Exception as exc:
                logger.warning("[JiuWenRail] ability snapshot failed: %s", exc)

    async def before_model_call(self, ctx):
        await super().before_model_call(ctx)
        builder = self._get_prompt_builder(ctx)
        builder.remove_section("tools")

    async def after_tool_call(self, ctx):
        """Touch the just-called tool so LRU keeps it."""
        tool_name = getattr(ctx, "tool_name", None) or getattr(
            getattr(ctx, "inputs", None), "tool_name", None
        )
        if tool_name:
            self._touch_tool(getattr(ctx, "session", None), str(tool_name))

    # ------------------------------------------------------------------
    # init override — register DenseSearchTool
    # ------------------------------------------------------------------

    def init(self, agent) -> None:
        language = getattr(self._config, "language", "cn") or "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        from jiuwenswarm.agents.harness.common.tools.search_tool import DenseSearchTool
        from openjiuwen.harness.tools import LoadToolsTool
        tools = [
            DenseSearchTool(
                search_fn=self._search_tools,
                load_fn=self._add_loaded_tools,
                append_trace=self._append_trace,
                language=language,
                agent_id=agent_id,
            ),
            LoadToolsTool(
                load_tools=self._load_tools,
                language=language,
                agent_id=agent_id,
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
    # _init_visible_tools — don't put always_visible in session_visible
    # ------------------------------------------------------------------

    def _init_visible_tools(self, session, *, default_visible_tools=None):
        if session is None:
            return
        current = session.get_state(_VISIBLE_TOOLS_KEY)
        if isinstance(current, list):
            return
        initial = list(dict.fromkeys(list(default_visible_tools or [])))
        session.update_state({_VISIBLE_TOOLS_KEY: initial})
        session.update_state({_VISIBLE_TOOLS_LRU_KEY: {}})
        session.update_state({_DISCOVERY_TRACE_KEY: []})

    # ------------------------------------------------------------------
    # _search_tools — Dense + keyword fallback + shadow A/B
    # ------------------------------------------------------------------

    async def _search_tools(self, query, limit=10, detail_level=1):
        query = (query or "").strip()
        if not query:
            return []
        if self._embedding_model is not None and self._cached_tool_embeddings:
            results = self._dense_search(query, limit, detail_level)
        else:
            results = await super()._search_tools(query, limit, detail_level)
        if self._embedding_model is not None and self._cached_tool_embeddings:
            shadow = await super()._search_tools(query, limit, detail_level)
            d3 = [r.get("name", "") for r in results[:3]]
            k3 = [r.get("name", "") for r in shadow[:3]]
            if d3 != k3:
                logger.info("[JiuWenRail] shadow A/B query=%r dense=%s vs keyword=%s", query, d3, k3)
        return results

    def _dense_search(self, query, limit, detail_level):
        import numpy as np
        ql = query.strip().lower()
        qv = list(self._embedding_model.embed([ql]))[0]
        qn = float(np.linalg.norm(qv))
        scored = []
        for tool in self._cached_all_tool_infos:
            name = str(getattr(tool, "name", "") or "")
            nl = name.lower()
            tv = self._cached_tool_embeddings.get(name)
            if tv is None:
                tv = self._embed_single_tool(tool)
                if tv is not None:
                    self._cached_tool_embeddings[name] = tv
            if tv is None:
                continue
            tn = float(np.linalg.norm(tv))
            if tn == 0 or qn == 0:
                continue
            sim = float(np.dot(qv, tv) / (qn * tn))
            if ql == nl:
                sim += 1.0
            elif nl.startswith(ql):
                sim += 0.3
            scored.append((sim, tool))
        scored.sort(key=lambda item: (-item[0], getattr(item[1], "name", "")))
        matched = [tool for _, tool in scored[:max(1, limit)]]
        return [self._build_tool_summary(tool, detail_level=detail_level) for tool in matched]

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _ensure_embedding_model(self):
        if self._embedding_model is not None:
            return
        try:
            import os
            if not os.environ.get("HF_ENDPOINT"):
                os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            from fastembed import TextEmbedding
            self._embedding_model = TextEmbedding("BAAI/bge-small-zh-v1.5")
            logger.info("[JiuWenRail] embedding model loaded (bge-small-zh)")
        except Exception as exc:
            logger.warning("[JiuWenRail] embedding load failed, fallback to keyword: %s", exc)
            self._embedding_model = None

    def _precompute_tool_embeddings(self):
        if self._embedding_model is None:
            return
        all_tools = self._cached_all_tool_infos
        if not all_tools:
            return
        sig = frozenset(str(getattr(t, "name", "") or "") for t in all_tools)
        if sig == self._cached_tool_sig and self._cached_tool_embeddings:
            return
        self._cached_tool_sig = sig
        haystacks = [
            f"{getattr(t,'name','') or ''} {getattr(t,'description','') or ''} "
            f"{self._parameters_to_text(getattr(t,'parameters',None))}"
            for t in all_tools
        ]
        try:
            embs = list(self._embedding_model.embed(haystacks))
            self._cached_tool_embeddings = {
                str(getattr(all_tools[i], "name", "")): embs[i]
                for i in range(len(all_tools))
            }
            logger.info("[JiuWenRail] pre-computed %d embeddings", len(self._cached_tool_embeddings))
        except Exception as exc:
            logger.warning("[JiuWenRail] pre-compute failed: %s", exc)
            self._cached_tool_embeddings = {}

    def _embed_single_tool(self, tool):
        if self._embedding_model is None:
            return None
        try:
            h = (f"{getattr(tool,'name','')} {getattr(tool,'description','')} "
                 f"{self._parameters_to_text(getattr(tool,'parameters',None))}")
            return list(self._embedding_model.embed([h]))[0]
        except Exception:
            return None

    # ------------------------------------------------------------------
    # LRU + cap
    # ------------------------------------------------------------------

    def _get_lru_map(self, session):
        if session is None:
            return {}
        s = session.get_state(_VISIBLE_TOOLS_LRU_KEY)
        return {str(k): int(v) for k, v in s.items() if str(k)} if isinstance(s, dict) else {}

    def _set_lru_map(self, session, m):
        if session is None:
            return
        session.update_state({_VISIBLE_TOOLS_LRU_KEY: dict(m)})

    def _next_lru_rank(self, lru):
        return (max(lru.values()) + 1) if lru else 1

    def _select_lru_eviction(self, session, candidates, cap):
        overflow = len(candidates) - cap
        if overflow <= 0:
            return []
        lru = self._get_lru_map(session)
        indexed = list(enumerate(candidates))
        indexed.sort(key=lambda p: (lru.get(p[1], -1), p[0]))
        return [name for _, name in indexed[:overflow]]

    def _touch_tool(self, session, name):
        if not name or session is None:
            return
        if name not in set(self._get_visible_tools(session)):
            return
        lru = self._get_lru_map(session)
        lru[name] = self._next_lru_rank(lru)
        self._set_lru_map(session, lru)

    def _add_loaded_tools(self, session, names, *, replace=False):
        names = [n for n in names if n not in self.always_visible_tools]
        current = self._get_visible_tools(session)
        base = [] if replace else list(current)
        merged = list(dict.fromkeys([*base, *names]))
        cset = set(current)
        newly = [n for n in names if n not in cset]
        lru = self._get_lru_map(session)
        rank = self._next_lru_rank(lru)
        for n in newly:
            if n not in lru:
                lru[n] = rank
                rank += 1
        self._set_lru_map(session, lru)
        evicted = []
        if len(merged) > self.max_loaded_tools:
            evicted = self._select_lru_eviction(session, merged, self.max_loaded_tools)
            es = set(evicted)
            merged = [n for n in merged if n not in es]
            lru = self._get_lru_map(session)
            for n in evicted:
                lru.pop(n, None)
            self._set_lru_map(session, lru)
        self._set_visible_tools(session, merged)
        return merged, newly, evicted

    # ------------------------------------------------------------------
    # _load_tools — use _add_loaded_tools instead of truncation
    # ------------------------------------------------------------------

    async def _load_tools(self, session, tool_names, replace=False):
        if session is None:
            return {"loaded_tools": [], "visible_tools": [], "skipped_tools": list(tool_names or []),
                    "message": "session is required for load_tools"}
        all_tools = await self._get_real_tool_infos()
        avail = {str(getattr(t, "name", "") or "") for t in all_tools}
        requested = [str(n).strip() for n in tool_names if str(n).strip()]
        valid, skipped = [], []
        for name in requested:
            if name in self.always_visible_tools or name in avail:
                valid.append(name)
            else:
                skipped.append(name)
        cur = self._get_visible_tools(session)
        next_vis, _added, evicted = self._add_loaded_tools(session, valid, replace=replace)
        skipped.extend(evicted)
        self._append_trace(session, {
            "action": "load_tools", "requested": requested, "loaded": valid,
            "visible_before": list(cur), "visible_after": next_vis,
            "skipped": skipped, "evicted_by_lru": evicted, "replace": replace,
        })
        msg = (f"loaded {len(valid)} tool(s), visible now: {', '.join(next_vis) if next_vis else '(none)'}"
               + (f", evicted {len(evicted)} by LRU cap({self.max_loaded_tools})" if evicted else ""))
        return {"loaded_tools": valid, "visible_tools": next_vis, "skipped_tools": skipped, "message": msg}

    # ------------------------------------------------------------------
    # Hidden tool summary
    # ------------------------------------------------------------------

    def _hidden_tool_category(self, tool_name, description=""):
        text = f"{tool_name} {description}".lower()
        for cat, _ in _HIDDEN_CATEGORY_CN.items():
            if cat in text or cat.replace("_", "") in text:
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
        "工具分为三类：可直接调用、仅导航、隐藏（需 search_tools 发现）。\n"
        "调用 search_tools 搜索后，匹配的工具会自动加载，下一轮可直接调用，无需再调用 load_tools。\n"
    )

    _NAV_HEADER_EN = (
        "## Tool Navigation\n"
        "The entries below help you understand the tool ecosystem available "
        "in the current session.\n"
        "Treat this section as a tool map, not as a full list of immediately "
        "callable tools.\n"
        "Tools fall into three categories: directly callable, navigation-only, "
        "and hidden (use search_tools to discover).\n"
        "After calling search_tools, matched tools are auto-loaded and callable "
        "in the next turn. No need to call load_tools separately.\n"
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
