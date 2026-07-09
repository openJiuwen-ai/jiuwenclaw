#!/usr/bin/env python3
# coding: utf-8
"""配置加载器 - 支持多组织配置。

配置文件格式 (community-config.json):
{
  "gitcode_token": "",  // 可选，也可用环境变量 GITCODE_ACCESS_TOKEN
  "organizations": [
    {
      "name": "openJiuwen",
      "display_name": "openJiuwen 社区",
      "skip_repos": [".gitcode", "relay"]
    }
  ],
  "sync": {
    "interval_hours": 24,
    "retention_days": 90
  }
}
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_NAME = "community-config.json"
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent


class ConfigError(ValueError):
    """配置错误。"""
    pass


def find_config_path(config_path: str = "") -> str:
    """查找配置文件路径。"""
    if config_path:
        return config_path

    # 优先查找 skill 根目录
    for name in [DEFAULT_CONFIG_NAME, "gitcode-repo.json"]:
        candidate = SKILL_DIR / name
        if candidate.exists():
            return str(candidate)

    # 当前目录
    if Path(DEFAULT_CONFIG_NAME).exists():
        return DEFAULT_CONFIG_NAME

    return ""


def load_config(config_path: str = "") -> Dict[str, Any]:
    """加载配置文件。"""
    path = find_config_path(config_path)
    if not path or not os.path.exists(path):
        # 返回默认配置
        return {
            "organizations": [
                {
                    "name": "openJiuwen",
                    "display_name": "openJiuwen 社区",
                    "skip_repos": [
                        ".gitcode",
                        "relay",
                        "agent-builder",
                        "jiuwenclaw",
                        "jiuwencode",
                        "jiuwenbox",
                        "official-website",
                        "openJiuwen_template",
                    ],
                }
            ],
            "sync": {
                "interval_hours": 24,
                "retention_days": 90,
            },
        }

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")

    return data


def get_token(config: Optional[Dict[str, Any]] = None) -> str:
    """获取 GitCode Token。
    
    优先级：
    1. 环境变量 GITCODE_ACCESS_TOKEN
    2. 配置文件 gitcode_token
    """
    token = os.environ.get("GITCODE_ACCESS_TOKEN", "")
    if token:
        return token.strip()

    if config:
        token = config.get("gitcode_token", "")
        if token:
            return token.strip()

    return ""


def get_organization(
    config: Dict[str, Any],
    org_name: Optional[str] = None,
) -> Dict[str, Any]:
    """获取组织配置。"""
    orgs = config.get("organizations", [])
    
    if not orgs:
        # 默认 openJiuwen
        return {
            "name": "openJiuwen",
            "display_name": "openJiuwen 社区",
            "skip_repos": [],
        }

    if org_name:
        for org in orgs:
            if org.get("name") == org_name:
                return org
        available = [o.get("name", "") for o in orgs]
        raise ConfigError(f"未找到组织 {org_name!r}，可用: {available}")

    if len(orgs) == 1:
        return orgs[0]

    # 默认返回第一个
    return orgs[0]


def get_skip_repos(org_config: Dict[str, Any]) -> set:
    """获取要跳过的仓库集合。"""
    skip = org_config.get("skip_repos", [])
    return set(skip) if skip else set()
