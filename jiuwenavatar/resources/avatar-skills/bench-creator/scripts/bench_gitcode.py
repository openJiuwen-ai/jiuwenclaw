#!/usr/bin/env python3
# coding: utf-8
"""bench-creator 专用：gitcode-repo.json 加载与 GitCode API 薄封装。

bench 流程约定：经本客户端访问 upstream 时仅用于只读拉取（PR/commits）；
创建分支、推送、创建 Issue 必须在 fork 上完成，勿在本模块扩展 upstream 写 API。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_NAME = "gitcode-repo.json"
LEGACY_CONFIG_NAME = "issue-resolver.json"

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


class ConfigError(ValueError):
    """配置文件或工作区选择错误。"""


class GitCodeClientError(Exception):
    """GitCode API 调用异常。"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        redact_token: str = "",
    ):
        safe_message = (
            _redact_secrets(message, redact_token)
            if redact_token
            else message
        )
        safe_body = (
            _redact_secrets(response_body, redact_token)
            if response_body and redact_token
            else response_body
        )
        super().__init__(safe_message)
        self.status_code = status_code
        self.response_body = safe_body


def _redact_secrets(text: str, token: str) -> str:
    if not text:
        return text
    out = text
    if token and len(token) >= 8:
        out = out.replace(token, "<redacted>")
    out = re.sub(
        r"(?i)access_token=[^&\s#]+",
        "access_token=<redacted>",
        out,
    )
    return out


def find_config_path(config_path: str = "") -> str:
    """解析配置文件路径；未指定时在常见位置自动查找。"""
    if config_path:
        return config_path

    candidates: List[str] = []
    for name in (DEFAULT_CONFIG_NAME, LEGACY_CONFIG_NAME):
        candidates.append(name)
        candidates.append(os.path.normpath(os.path.join(_SCRIPT_DIR, "..", name)))
        candidates.append(
            os.path.normpath(
                os.path.join(_SCRIPT_DIR, "..", "..", "gitcode-repo", name)
            )
        )

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""


def load_raw_config(config_path: str) -> Dict[str, Any]:
    if not config_path:
        return {}
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")
    return data


def _workspace_names(workspaces: List[Dict[str, Any]]) -> List[str]:
    return [
        str(w.get("name") or f"<unnamed-{i}>")
        for i, w in enumerate(workspaces)
    ]


def resolve_workspace_config(
    raw: Dict[str, Any],
    workspace_name: Optional[str] = None,
) -> Dict[str, Any]:
    workspaces = raw.get("workspaces") or []
    if workspaces:
        if workspace_name:
            matched = [
                w for w in workspaces
                if w.get("name") == workspace_name
            ]
            if not matched:
                available = _workspace_names(workspaces)
                raise ConfigError(
                    f"未找到工作区 {workspace_name!r}，"
                    f"可用: {available}"
                )
            ws = matched[0]
        elif len(workspaces) == 1:
            ws = workspaces[0]
        else:
            available = _workspace_names(workspaces)
            raise ConfigError(
                f"配置文件含 {len(workspaces)} 个工作区，"
                f"请使用 --workspace 指定其一: {available}"
            )

        effective: Dict[str, Any] = {
            "gitcode_token": raw.get("gitcode_token", ""),
            "upstream": ws.get("upstream", {}),
            "fork": ws.get("fork", {}),
            "local_repo": ws.get("local_repo", {}),
            "_workspace_name": ws.get("name", ""),
        }
        if "poller" in ws:
            effective["poller"] = ws["poller"]
        return effective

    upstream = raw.get("upstream") or {}
    if upstream.get("owner") and upstream.get("repo"):
        return raw

    raise ConfigError(
        "配置无效：请填写 workspaces[] 或顶层 upstream.owner/upstream.repo"
    )


def exit_on_config_error(exc: ConfigError) -> None:
    print(
        json.dumps({"error": str(exc)}, ensure_ascii=False),
        file=sys.stderr,
    )
    sys.exit(1)


class GitCodeClient:
    """GitCode API v5 薄客户端（仅 bench_from_pr 所需能力）。"""

    BASE_URL = "https://api.gitcode.com/api/v5"
    MAX_RETRIES = 3
    RETRY_WAIT_SECONDS = 15

    def __init__(
        self,
        token: str,
        upstream_owner: str,
        upstream_repo: str,
        fork_owner: str = "",
        fork_repo: str = "",
        base_branch: str = "main",
    ):
        self.token = (token or "").strip()
        uo = (upstream_owner or "").strip()
        ur = (upstream_repo or "").strip()
        if not uo or not ur:
            raise ValueError(
                "upstream.owner 与 upstream.repo 不能为空，"
                "请检查 gitcode-repo.json"
            )
        self.upstream_owner = uo
        self.upstream_repo = ur
        self.fork_owner = (fork_owner or uo).strip()
        self.fork_repo = (fork_repo or ur).strip()
        self.base_branch = (base_branch or "main").strip()
        if requests is None:
            raise GitCodeClientError(
                "缺少 requests 库，请执行: pip install requests"
            )
        self._session = requests.Session()
        if not self.token:
            raise ValueError("GitCode access token 不能为空")

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Any = None,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"
        if params is None:
            params = {}
        else:
            params = dict(params)
        params["access_token"] = self.token

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_WAIT_SECONDS)
                    continue
                raise GitCodeClientError(
                    f"请求失败: {_redact_secrets(str(exc), self.token)}",
                ) from exc

            if resp.status_code == 429:
                if attempt < self.MAX_RETRIES - 1:
                    wait = self.RETRY_WAIT_SECONDS * (attempt + 1)
                    print(
                        f"触发限流，等待 {wait}s 后重试...",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue
                raise GitCodeClientError(
                    "API 限流，已达最大重试次数",
                    status_code=429,
                    redact_token=self.token,
                )

            if resp.status_code >= 400:
                safe_text = _redact_secrets(resp.text[:500], self.token)
                raise GitCodeClientError(
                    f"API 错误 {resp.status_code}: {safe_text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    redact_token=self.token,
                )

            if resp.status_code == 204:
                return {}
            try:
                return resp.json()
            except ValueError:
                safe_text = _redact_secrets(resp.text[:500], self.token)
                raise GitCodeClientError(
                    f"API 返回非 JSON 响应（{resp.status_code}）：{safe_text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    redact_token=self.token,
                )

        raise GitCodeClientError(
            "已达最大重试次数",
            redact_token=self.token,
        )

    @classmethod
    def from_config(
        cls,
        config_path: Optional[str] = None,
        workspace_name: Optional[str] = None,
    ) -> "GitCodeClient":
        resolved_path = find_config_path(config_path or "")
        if not resolved_path:
            raise ConfigError(
                "未找到 gitcode-repo.json，请用 --config 指定路径"
            )
        raw = load_raw_config(resolved_path)
        config = resolve_workspace_config(raw, workspace_name)

        token = os.environ.get("GITCODE_TOKEN", "")
        if not token:
            token = config.get("gitcode_token", "")
        if not token:
            token = input("请输入 GitCode Token: ").strip()
        if not token:
            raise ConfigError("未提供 GitCode Token")

        upstream = config.get("upstream", {})
        fork = config.get("fork", {})
        return cls(
            token=token,
            upstream_owner=upstream.get("owner", config.get("owner", "")),
            upstream_repo=upstream.get("repo", config.get("repo", "")),
            fork_owner=fork.get("owner", ""),
            fork_repo=fork.get(
                "repo",
                upstream.get("repo", config.get("repo", "")),
            ),
            base_branch=upstream.get(
                "base_branch",
                config.get("base_branch", "main"),
            ),
        )
