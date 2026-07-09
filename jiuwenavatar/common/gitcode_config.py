# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""GitCode token 与 gitcode-repo skill 运行时配置同步."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GITCODE_REPO_CONFIG_NAME = "gitcode-repo.json"


def sync_gitcode_token_to_skill_config(token: str) -> bool:
    """将 GITCODE_TOKEN 写入已安装的 gitcode-repo skill 配置（若存在）.

    gitcode-repo 脚本优先读环境变量 GITCODE_TOKEN；同步到 JSON 作为兜底。
    """
    from jiuwenavatar.common.utils import get_agent_skills_dir

    skill_dir = get_agent_skills_dir() / "gitcode-repo"
    config_path = skill_dir / _GITCODE_REPO_CONFIG_NAME
    if not config_path.is_file():
        return False

    try:
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read %s: %s", config_path, exc)
        return False

    raw["gitcode_token"] = token or ""
    try:
        config_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Synced GITCODE_TOKEN to %s", config_path)
        return True
    except OSError as exc:
        logger.warning("Failed to write %s: %s", config_path, exc)
        return False
