"""Static system-prompt instructions for the Celia memory rail."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files

logger = logging.getLogger(__name__)

_PROMPT_RESOURCE = ("resources", "memory", "celia", "AGENTS.md")


@lru_cache(maxsize=1)
def load_celia_agent_prompt() -> str:
    """Load the packaged Celia instructions, failing open when unavailable."""

    try:
        resource = files("jiuwenswarm")
        for part in _PROMPT_RESOURCE:
            resource = resource.joinpath(part)
        return resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        logger.warning("[CeliaMemoryPrompt] failed to load packaged instructions: %s", exc)
        return ""


__all__ = ["load_celia_agent_prompt"]
