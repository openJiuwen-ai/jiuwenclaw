# -*- coding: utf-8 -*-
"""Executable-corpus filtering. No agent-core dependency.

The ``resolver`` callable (``name -> bool``) is injected by the rail adapter;
it encapsulates agent-core's ``ability_manager.get`` + ``Runner.resource_mgr.get_tool``
probe. Keeping the probe out of this module keeps it ``openjiuwen``-free.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.corpus")


def filter_executable(tools: List[Any], resolver: Callable[[str], bool]) -> List[Any]:
    corpus: List[Any] = []
    filtered = 0
    for t in tools or []:
        name = str(getattr(t, "name", "") or "")
        if not name:
            continue
        if resolver(name):
            corpus.append(t)
        else:
            filtered += 1
            logger.info("[tool_retrieval] ghost tool filtered: %s", name)
    logger.info(
        "[tool_retrieval] executable corpus: %d/%d (filtered %d ghosts)",
        len(corpus),
        len(tools or []),
        filtered,
    )
    return corpus
