# -*- coding: utf-8 -*-
"""Dense retrieval + boost logic. No agent-core dependency, fully self-contained.

Operates on duck-typed tool objects (``getattr(name/description/parameters)``).
Haystack building and output-dict shaping use the locally-ported ``summary``
helpers (verbatim from agent-core's base class) — no injected callables, so the
module runs standalone (can be unit-tested / reused outside agent-core).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from .summary import build_tool_summary, parameters_to_text

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.search")

VERB_INTENT = (
    (("create", "add"), ("创建", "新增", "新建", "添加", "建立", "生成", "create", "add")),
    (("list",), ("列出", "列表", "list")),
    (("delete", "remove"), ("删除", "移除", "remove", "delete")),
    (("update",), ("修改", "更新", "update")),
    (("preview",), ("预览", "preview")),
    (("get",), ("获取", "get")),
    (("toggle",), ("切换", "开关", "toggle")),
    (("run",), ("运行", "执行", "run", "runs")),
)


def verb_intent_boost(ql: str, nl: str) -> float:
    name_tokens = nl.split("_")
    for en_tokens, cn_words in VERB_INTENT:
        if any(w in ql for w in cn_words) and any(t in name_tokens for t in en_tokens):
            return 0.5
    return 0.0


def haystack_for(tool: Any, desc_cap: int) -> str:
    name = str(getattr(tool, "name", "") or "")
    desc = str(getattr(tool, "description", "") or "")
    if len(desc) > desc_cap:
        desc = desc[:desc_cap]
    return f"{name} {desc} {parameters_to_text(getattr(tool, 'parameters', None))}"


def dense_search(
    query: str,
    tools: List[Any],
    model: Any,
    embedding_cache: Dict[str, Any],
    *,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
) -> List[dict]:
    if model is None or not (tools or []):
        return []
    import numpy as np

    ql = (query or "").strip().lower()
    ql_norm = "_".join(ql.split())
    emb_results = list(model.embed([ql]))
    if not emb_results:
        return []
    qv = emb_results[0]
    qn = float(np.linalg.norm(qv))
    scored = []
    for tool in (tools or []):
        name = str(getattr(tool, "name", "") or "")
        nl = name.lower()
        tv = embedding_cache.get(name)
        if tv is None:
            tv = embed_single(tool, model, desc_cap=desc_cap)
            if tv is not None:
                embedding_cache[name] = tv
        if tv is None:
            continue
        tn = float(np.linalg.norm(tv))
        if tn == 0 or qn == 0:
            continue
        sim = float(np.dot(qv, tv) / (qn * tn))
        if ql == nl or ql_norm == nl:
            sim += 1.0
        elif len(nl) >= 5 and (nl in ql_norm or nl in ql):
            sim += 0.8
        elif len(ql) >= 3 and (nl.startswith(ql) or nl.startswith(ql_norm)):
            sim += 0.3
        else:
            name_tokens = [t for t in nl.split("_") if len(t) >= 3]
            if name_tokens:
                hit = sum(1 for t in name_tokens if t in ql or t in ql_norm or t.rstrip("s") in ql)
                if hit:
                    ratio = hit / len(name_tokens)
                    sim += 0.5 if hit == len(name_tokens) else 0.25 * ratio
            elif len(ql) >= 4 and (ql in nl or ql_norm in nl):
                sim += 0.2
        sim += verb_intent_boost(ql, nl)
        scored.append((sim, tool))
    scored.sort(key=lambda item: (-item[0], getattr(item[1], "name", "")))
    matched = [tool for _, tool in scored[:max(1, limit)]]
    return [build_tool_summary(tool, detail_level=detail_level) for tool in matched]


def precompute_embeddings(
    tools: List[Any],
    model: Any,
    embedding_cache: Dict[str, Any],
    *,
    desc_cap: int = 256,
) -> None:
    if model is None or not tools:
        return
    haystacks = [haystack_for(t, desc_cap) for t in tools]
    try:
        embs = list(model.embed(haystacks))
        embedding_cache.clear()
        embedding_cache.update(
            {str(getattr(tools[i], "name", "")): embs[i] for i in range(len(tools))}
        )
        logger.info("[tool_retrieval] pre-computed %d embeddings", len(embedding_cache))
    except Exception as exc:
        logger.warning("[tool_retrieval] pre-compute failed: %s", exc)
        embedding_cache.clear()


def embed_single(tool: Any, model: Any, *, desc_cap: int = 256):
    if model is None:
        return None
    try:
        h = haystack_for(tool, desc_cap)
        return list(model.embed([h]))[0]
    except Exception:
        return None
