#!/usr/bin/env python3
# coding: utf-8
"""
GitCode API v5 只读客户端。

仅支持 GET 请求，用于数据查询和统计分析。
不支持任何写操作（POST/PATCH/DELETE），确保安全。

Features:
- 自动重试和限流处理
- Token 脱敏（防止日志泄露）
- 多工作区配置支持
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import urllib.request
    import urllib.error
    import urllib.parse
except ImportError:
    pass


def _redact_secrets(text: str, token: str) -> str:
    """避免在异常信息中泄露 access_token。"""
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


class GitCodeReadOnlyClient:
    """GitCode API v5 只读客户端。
    
    仅支持 GET 请求，用于查询和统计。
    不支持任何写操作，确保数据安全。
    """

    BASE_URL = "https://api.gitcode.com/api/v5"
    MAX_RETRIES = 3
    RETRY_WAIT_SECONDS = 15
    TIMEOUT = 15

    def __init__(
        self,
        token: str = "",
        org: str = "openJiuwen",
    ):
        """初始化只读客户端。

        Args:
            token: GitCode access token（可选，公开接口无需 token）。
            org: 组织名称，默认 openJiuwen。
        """
        self.token = (token or "").strip()
        self.org = org

    def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """发送 GET 请求，处理重试和限流。

        Args:
            path: API 路径（不含 BASE_URL）。
            params: 查询参数。

        Returns:
            响应 JSON。

        Raises:
            GitCodeClientError: API 调用失败。
        """
        if params is None:
            params = {}
        else:
            params = dict(params)

        if self.token:
            params["access_token"] = self.token

        query = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "openjiuwen-community-stats/2.0",
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)

            except urllib.error.HTTPError as exc:
                status = exc.code
                body = ""
                try:
                    body = exc.read().decode("utf-8")
                except Exception:
                    pass

                if status == 429:
                    if attempt < self.MAX_RETRIES - 1:
                        wait = self.RETRY_WAIT_SECONDS * (attempt + 1)
                        print(
                            f"[WARN] 触发限流，等待 {wait}s 后重试...",
                            file=sys.stderr,
                        )
                        time.sleep(wait)
                        continue
                    raise GitCodeClientError(
                        "API 限流，已达最大重试次数",
                        status_code=429,
                        redact_token=self.token,
                    )

                safe_body = _redact_secrets(body[:500], self.token)
                raise GitCodeClientError(
                    f"API 错误 {status}: {safe_body}",
                    status_code=status,
                    response_body=body,
                    redact_token=self.token,
                )

            except urllib.error.URLError as exc:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_WAIT_SECONDS)
                    continue
                msg = _redact_secrets(str(exc), self.token)
                raise GitCodeClientError(
                    f"请求失败: {msg}",
                    redact_token=self.token,
                ) from exc

            except Exception as exc:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
                    continue
                msg = _redact_secrets(str(exc), self.token)
                raise GitCodeClientError(
                    f"请求异常: {msg}",
                    redact_token=self.token,
                ) from exc

        raise GitCodeClientError(
            "已达最大重试次数",
            redact_token=self.token,
        )

    # ── 组织级查询 ──────────────────────────────

    def list_org_repos(
        self,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取组织的仓库列表。"""
        path = f"/orgs/{self.org}/repos"
        return self._request(path, {"page": page, "per_page": per_page})

    def get_all_org_repos(self) -> List[Dict[str, Any]]:
        """获取组织的全部仓库（自动翻页）。"""
        repos = []
        page = 1
        while True:
            batch = self.list_org_repos(page=page, per_page=100)
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return repos

    # ── 仓库级查询 ──────────────────────────────

    def get_repo(self, repo: str) -> Dict[str, Any]:
        """获取仓库详情。"""
        path = f"/repos/{self.org}/{repo}"
        return self._request(path)

    def get_repo_contributors(
        self,
        repo: str,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取仓库贡献者列表。"""
        path = f"/repos/{self.org}/{repo}/contributors"
        return self._request(path, {"page": page, "per_page": per_page})

    def get_download_stats(self, repo: str) -> Optional[Dict[str, Any]]:
        """获取仓库下载统计。"""
        path = f"/repos/{self.org}/{repo}/download_statistics"
        try:
            return self._request(path)
        except GitCodeClientError:
            return None

    # ── Issue 查询（只读）──────────────────────

    def list_issues(
        self,
        repo: str,
        state: str = "open",
        labels: str = "",
        assignee: str = "",
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取 Issue 列表（只读）。"""
        path = f"/repos/{self.org}/{repo}/issues"
        params = {
            "state": state,
            "page": page,
            "per_page": per_page,
        }
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        result = self._request(path, params)
        return result if isinstance(result, list) else []

    def get_all_issues(
        self,
        repo: str,
        state: str = "all",
    ) -> List[Dict[str, Any]]:
        """获取仓库全部 Issue（自动翻页，只读）。"""
        issues = []
        page = 1
        while True:
            batch = self.list_issues(repo, state=state, page=page, per_page=100)
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(0.1)
        return issues

    def get_issue(self, repo: str, number: int) -> Dict[str, Any]:
        """获取单个 Issue 详情（只读）。"""
        path = f"/repos/{self.org}/{repo}/issues/{number}"
        return self._request(path)

    def get_issue_comments(
        self,
        repo: str,
        number: int,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取 Issue 评论列表（只读）。"""
        path = f"/repos/{self.org}/{repo}/issues/{number}/comments"
        result = self._request(path, {"page": page, "per_page": per_page})
        return result if isinstance(result, list) else []

    # ── PR 查询（只读）─────────────────────────

    def list_pulls(
        self,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取 PR 列表（只读）。"""
        path = f"/repos/{self.org}/{repo}/pulls"
        params = {
            "state": state,
            "page": page,
            "per_page": per_page,
        }
        result = self._request(path, params)
        return result if isinstance(result, list) else []

    def get_pull(self, repo: str, number: int) -> Dict[str, Any]:
        """获取单个 PR 详情（只读）。"""
        path = f"/repos/{self.org}/{repo}/pulls/{number}"
        return self._request(path)

    # ── Tag/Release 查询（只读）────────────────

    def list_tags(
        self,
        repo: str,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取仓库 Tag 列表（只读）。"""
        path = f"/repos/{self.org}/{repo}/tags"
        result = self._request(path, {"page": page, "per_page": per_page})
        return result if isinstance(result, list) else []

    def get_release(self, repo: str, tag: str) -> Dict[str, Any]:
        """获取指定 Tag 的 Release 详情（只读）。"""
        path = f"/repos/{self.org}/{repo}/releases/tags/{tag}"
        return self._request(path)

    # ── 贡献者统计（只读）──────────────────────

    def get_contributor_stats(
        self,
        repo: str,
        author: str,
        since: str = "",
        until: str = "",
        ref_name: str = "",
    ) -> Dict[str, Any]:
        """获取单个贡献者统计（只读）。"""
        path = f"/repos/{self.org}/{repo}/contributors/statistic"
        params = {"author": author}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if ref_name:
            params["ref_name"] = ref_name
        return self._request(path, params)

    # ── 工厂方法 ────────────────────────────────

    @classmethod
    def from_env(cls, org: str = "openJiuwen") -> "GitCodeReadOnlyClient":
        """从环境变量创建客户端。"""
        token = os.environ.get("GITCODE_ACCESS_TOKEN", "")
        return cls(token=token, org=org)
