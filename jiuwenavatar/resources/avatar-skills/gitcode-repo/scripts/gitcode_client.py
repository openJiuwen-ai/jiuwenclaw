#!/usr/bin/env python3
# coding: utf-8
"""
GitCode API v5 客户端。

封装 GitCode REST API 交互，支持 upstream + fork 双仓库上下文。
- Issue 获取/列表/评论/标签/创建 → 均支持 fork 或 upstream（CLI 默认 fork）
- PR/MR 获取/列表/评论/标签/创建 → 默认 fork 仓库，也可显式指定 upstream 仓库
- 认证优先级：环境变量 GITCODE_TOKEN → 配置文件 → 交互提示
- API v5 认证方式：access_token query 参数
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from config_loader import (
    ConfigError,
    find_config_path,
    load_raw_config,
    resolve_workspace_config,
)


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


def _looks_like_dev_review_comment(body: str) -> bool:
    markers = (
        "<!-- dev-reviewer:",
        "[CR-",
        "[Must Fix]",
        "[Should Fix]",
        "][Must Fix][",
        "][Should Fix][",
        "[严重][Must Fix]",
        "[建议][Should Fix]",
    )
    return any(marker in body for marker in markers)


def _validate_dev_review_comment_body(body: str) -> str:
    if "<!-- dev-reviewer:" not in body:
        return "dev-reviewer 检视意见必须使用 render-comments 生成的评论文件，正文缺少隐藏签名"
    visible_body = re.sub(r"<!--\s*dev-reviewer:[^>]+-->", "", body).strip()
    first_visible_line = next(
        (line.strip() for line in visible_body.splitlines() if line.strip()),
        "",
    )
    if first_visible_line.startswith("[CR-") and "|" in first_visible_line:
        return "dev-reviewer 检视意见疑似只提交了报告表格摘要，请使用 render-comments 生成的评论文件"
    paragraph_count = len([part for part in re.split(r"\n\s*\n", visible_body) if part.strip()])
    if paragraph_count < 4:
        return "dev-reviewer 检视意见正文结构过短，至少应包含标题、问题场景、影响和修复/验证建议"
    return ""

try:
    import requests
except ImportError:
    print(
        "错误: 缺少 requests 库，请执行: "
        "pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)


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


class GitCodeClient:
    """GitCode API v5 客户端。

    支持 upstream（主仓）和 fork（个人仓）双仓库上下文。
    Issue 获取/创建/评论/标签均支持 fork 或 upstream（CLI 默认 fork）；
    MR 默认创建到 fork；需要时可显式指定 upstream。

    支持 Dry Run 模式：在初始化时设置 dry_run=True，
    所有创建/修改操作只会打印日志而不会实际调用 API。
    """

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
        dry_run: bool = False,
    ):
        """初始化客户端。

        Args:
            token: GitCode access token。
            upstream_owner: 主仓 owner。
            upstream_repo: 主仓 repo 名称。
            fork_owner: 个人 fork 的 owner。
            fork_repo: 个人 fork 的 repo 名称。
            base_branch: 主仓默认分支。
            dry_run: 是否为 Dry Run 模式（只打印不执行）。

        Raises:
            ValueError: upstream owner/repo 未配置。
        """
        self.token = (token or "").strip()
        uo = (upstream_owner or "").strip()
        ur = (upstream_repo or "").strip()
        if not uo or not ur:
            raise ValueError(
                "upstream.owner 与 upstream.repo 不能为空，"
                "请检查 gitcode-repo.json（或环境配置）"
            )
        self.upstream_owner = uo
        self.upstream_repo = ur
        self.has_explicit_fork_owner = bool(
            (fork_owner or "").strip()
        )
        self.fork_owner = (fork_owner or uo).strip()
        self.fork_repo = (fork_repo or ur).strip()
        self.base_branch = (base_branch or "main").strip()
        self.dry_run = dry_run
        self._session = requests.Session()
        if not self.token:
            raise ValueError("GitCode access token 不能为空")

    def _dry_run_log(
        self, action: str, details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """记录 Dry Run 操作并返回模拟结果。

        Args:
            action: 操作名称。
            details: 操作详情。

        Returns:
            Dry Run 结果字典。
        """
        print(f"[DRY RUN] {action}")
        for key, value in details.items():
            if isinstance(value, str) and len(value) > 100:
                value = value[:100] + "..."
            print(f"  {key}: {value}")
        return {
            "dry_run": True,
            "action": action,
            "details": details,
        }

    # ── Issue 操作（支持 upstream / fork） ──────────────

    def _issue_repo(
        self, target_project: str = "fork"
    ) -> Tuple[str, str]:
        """解析 Issue 操作使用的仓库。"""
        target = (target_project or "fork").strip().lower()
        if target == "upstream":
            return self.upstream_owner, self.upstream_repo
        if target == "fork":
            if not self.has_explicit_fork_owner:
                raise ValueError(
                    "target_project=fork 须在 gitcode-repo.json 中配置 "
                    "fork.owner；未配置时会退化为 upstream 同一仓库。"
                    "主仓 Issue 请使用 --source upstream。"
                )
            return self.fork_owner, self.fork_repo
        raise ValueError(
            "target_project 仅支持 'upstream' 或 'fork'"
        )

    def get_issue(
        self, number: int, target_project: str = "fork"
    ) -> Dict[str, Any]:
        """获取指定仓库的某个 Issue。

        Args:
            number: Issue 编号。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            Issue 详情字典。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/issues/{number}"
        )
        return self._request("GET", path)

    def list_issues(
        self,
        state: str = "open",
        labels: str = "",
        page: int = 1,
        per_page: int = 20,
        assignee: str = "",
        search: str = "",
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取指定仓库的 Issue 列表。

        Args:
            state: Issue 状态（open/closed/all）。
            labels: 逗号分隔的标签过滤。
            page: 页码。
            per_page: 每页数量。
            assignee: 指派人用户名过滤。
            search: 关键词搜索（搜索标题和内容）。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            Issue 列表。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/issues"
        )
        params: Dict[str, Any] = {
            "state": state,
            "page": page,
            "per_page": per_page,
        }
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        if search:
            params["q"] = search
        return self._request("GET", path, params=params)

    def get_issue_comments(
        self,
        number: int,
        page: int = 1,
        per_page: int = 100,
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取 Issue 的所有评论。

        Args:
            number: Issue 编号。
            page: 页码。
            per_page: 每页数量。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            评论列表。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}"
            f"/issues/{number}/comments"
        )
        params = {"page": page, "per_page": per_page}
        return self._request("GET", path, params=params)

    def get_all_issue_comments(
        self,
        number: int,
        per_page: int = 100,
        target_project: str = "fork",
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """分页获取 Issue 的全部评论。"""
        all_comments: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            batch = self.get_issue_comments(
                number,
                page=page,
                per_page=per_page,
                target_project=target_project,
            )
            if not isinstance(batch, list) or not batch:
                break
            all_comments.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return all_comments

    def create_comment(
        self,
        number: int,
        body: str,
        target_project: str = "fork",
    ) -> Dict[str, Any]:
        """在 Issue 中创建评论。

        Args:
            number: Issue 编号。
            body: 评论内容（支持 Markdown）。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            创建的评论详情。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}"
            f"/issues/{number}/comments"
        )
        return self._request(
            "POST", path, json_data={"body": body}
        )

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignee: str = "",
        target_project: str = "fork",
    ) -> Dict[str, Any]:
        """在指定仓库创建新 Issue。

        Args:
            title: Issue 标题。
            body: Issue 描述（支持 Markdown）。
            labels: 标签名称列表。为提高成功率，创建请求不直接
                携带标签；调用方应在 Issue 创建成功后单独添加标签。
            assignee: 指派人用户名。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            创建的 Issue 详情。
        """
        title_clean = (title or "").strip()
        body_clean = (body or "").strip()
        assignee_clean = (assignee or "").strip()
        if not self.dry_run and not title_clean:
            raise ValueError("Issue 标题不能为空")

        if self.dry_run:
            return self._dry_run_log(
                "create_issue",
                {
                    "title": title_clean,
                    "body": body_clean,
                    "labels": labels,
                    "assignee": assignee_clean,
                    "target_project": target_project,
                },
            )

        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/issues"
        )
        data: Dict[str, Any] = {
            "repo": repo,
            "title": title_clean,
        }
        if body_clean:
            data["body"] = body_clean
        if assignee_clean:
            data["assignee"] = assignee_clean
        return self._request("POST", path, json_data=data)


    def update_issue(
        self,
        number: int,
        target_project: str = "fork",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """更新 Issue 信息。

        Args:
            number: Issue 编号。
            target_project: Issue 所属仓库，支持 upstream 或 fork。
            **kwargs: 可更新字段（title, body, state,
                assignee 等）。

        Returns:
            更新后的 Issue 详情。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/issues/{number}"
        )
        data = {
            "repo": repo,
        }
        data.update(kwargs)
        return self._request("PATCH", path, json_data=data)

    def list_labels(
        self,
        page: int = 1,
        per_page: int = 20,
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取仓库的标签列表。

        在创建 Issue 前应先调用此方法获取可用标签列表，
        避免使用不存在的标签导致 403 Forbidden 错误。

        Args:
            page: 页码。
            per_page: 每页数量。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            标签列表。
        """
        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/labels"
        )
        params: Dict[str, Any] = {
            "page": page,
            "per_page": per_page,
        }
        return self._request("GET", path, params=params)

    def add_labels(
        self,
        number: int,
        labels: List[str],
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """为 Issue 添加标签。

        Args:
            number: Issue 编号。
            labels: 标签名称列表。
            target_project: Issue 所属仓库，支持 upstream 或 fork。

        Returns:
            标签列表。
        """
        if self.dry_run:
            result = self._dry_run_log(
                "add_labels",
                {
                    "number": number,
                    "labels": labels,
                    "target_project": target_project,
                },
            )
            return [result]

        owner, repo = self._issue_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}"
            f"/issues/{number}/labels"
        )
        return self._request(
            "POST", path, json_data=labels
        )

    # ── PR/MR 操作（默认 fork，可显式使用 upstream） ──

    def _pull_request_repo(
        self, target_project: str = "fork"
    ) -> Tuple[str, str]:
        """解析 PR/MR 操作使用的仓库。"""
        target = (target_project or "fork").strip().lower()
        if target == "upstream":
            return self.upstream_owner, self.upstream_repo
        if target == "fork":
            return self.fork_owner, self.fork_repo
        raise ValueError(
            "target_project 仅支持 'upstream' 或 'fork'"
        )

    def pull_request_repo(
        self, target_project: str = "fork"
    ) -> Tuple[str, str]:
        """解析 PR/MR 操作使用的仓库（公开方法）。"""
        return self._pull_request_repo(target_project)

    def fork_path(self) -> str:
        """Fork 项目路径 ``owner/repo``（来自配置 fork.owner / fork.repo）。"""
        owner = (self.fork_owner or "").strip()
        repo = (self.fork_repo or "").strip()
        if not owner or not repo:
            raise ValueError(
                "跨仓 PR 需要配置 fork.owner 与 fork.repo"
            )
        return f"{owner}/{repo}"

    def fork_path_for_upstream(self, head: str) -> str:
        """解析跨仓 PR 的 ``fork_path``（须配合 ``username:branch`` 格式的 head）。

        ``head`` 已含 ``owner:branch`` 时，``fork_path`` 取 head 中的 owner
        与配置中的 fork.repo（缺省为 upstream.repo）；否则使用配置中的
        fork.owner / fork.repo。
        """
        head_clean = (head or "").strip()
        fork_repo = (self.fork_repo or self.upstream_repo).strip()
        if not fork_repo:
            raise ValueError(
                "跨仓 PR 需要 fork.repo 或 upstream.repo"
            )
        if ":" in head_clean:
            head_user, branch = head_clean.split(":", 1)
            head_user = head_user.strip()
            if not head_user or not branch.strip():
                raise ValueError(
                    "跨仓 PR 的 head 须为 username:branch 格式"
                )
            return f"{head_user}/{fork_repo}"
        return self.fork_path()

    def _validate_issue_number(
        self, issue: Optional[int]
    ) -> Optional[int]:
        """校验 Issue 编号（须为正整数）。"""
        if issue is None:
            return None
        if issue <= 0:
            raise ValueError("Issue 编号须为正整数")
        return issue

    def resolve_pull_request_head(
        self, head: str, target_project: str = "fork"
    ) -> Tuple[str, str]:
        """解析 PR 创建时实际发送的 head 与 fork_path（不调用 API）。"""
        head_clean = (head or "").strip()
        target_clean = (target_project or "fork").strip().lower()
        fork_path = ""
        if target_clean == "upstream":
            if ":" not in head_clean:
                if not self.has_explicit_fork_owner:
                    raise ValueError(
                        "跨仓 PR 的 head 须为 username:branch 格式；"
                        "仅传分支名时需配置 fork.owner"
                    )
                head_clean = f"{self.fork_owner}:{head_clean}"
            fork_path = self.fork_path_for_upstream(head_clean)
        return head_clean, fork_path

    def get_pull_request(
        self,
        number: int,
        target_project: str = "fork",
    ) -> Dict[str, Any]:
        """获取单个 Pull Request。

        Args:
            number: PR 编号。
            target_project: PR/MR 所属仓库，支持 upstream 或 fork。

        Returns:
            PR 详情字典。
        """
        owner, repo = self._pull_request_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}"
        )
        return self._request("GET", path)

    def list_pull_requests(
        self,
        state: str = "open",
        head: str = "",
        base: str = "",
        search: str = "",
        page: int = 1,
        per_page: int = 20,
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取 Pull Request 列表。

        Args:
            state: PR 状态（open/closed/all）。
            head: 源分支过滤。同仓 MR 可用分支名；跨仓到 upstream
                用 ``fork_owner:branch``。
            base: 目标分支过滤。
            search: 关键词搜索（传 ``q`` 查询参数；无结果时由调用方兜底）。
            page: 页码。
            per_page: 每页数量。
            target_project: PR/MR 所属仓库，支持 upstream 或 fork。

        Returns:
            PR 列表。
        """
        owner, repo = self._pull_request_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls"
        )
        params: Dict[str, Any] = {
            "state": state,
            "page": page,
            "per_page": per_page,
        }
        if head:
            params["head"] = head
        if base:
            params["base"] = base
        if search:
            params["q"] = search
        return self._request("GET", path, params=params)

    def get_pull_request_comments(
        self,
        number: int,
        page: int = 1,
        per_page: int = 100,
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取 Pull Request 的评论列表。"""
        owner, repo = self._pull_request_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}/comments"
        )
        params = {"page": page, "per_page": per_page}
        return self._request("GET", path, params=params)

    def get_all_pull_request_comments(
        self,
        number: int,
        per_page: int = 100,
        target_project: str = "fork",
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """分页获取 Pull Request 的全部评论。"""
        all_comments: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            batch = self.get_pull_request_comments(
                number,
                page=page,
                per_page=per_page,
                target_project=target_project,
            )
            if not isinstance(batch, list) or not batch:
                break
            all_comments.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return all_comments

    def create_pull_comment(
        self,
        number: int,
        body: str,
        path: str = "",
        position: Optional[int] = None,
        position_type: str = "",
        target_project: str = "fork",
        need_to_resolve: Optional[bool] = None,
        allow_review_discussion_comment: bool = False,
    ) -> Dict[str, Any]:
        """在 Pull Request 中提交评论。

        Args:
            number: PR 编号。
            body: 评论正文（必填）。
            path: 文件相对路径（行评可选）。
            position: 行号（行评可选）。
            position_type: ``text``（默认行评）或 ``binary``（文件级，忽略 position）。
            target_project: PR/MR 所属仓库，支持 upstream 或 fork。
            need_to_resolve: 是否标记为「需解决」的检视意见。``True`` 时该评论
                会成为待闭环的 discussion（可被合并门禁拦截），后续闭环后用
                :meth:`resolve_pull_comment` 置为已解决；``None`` 则不传该字段。
        """
        body_clean = (body or "").strip()
        if not body_clean:
            raise ValueError("PR 评论正文不能为空")
        path_clean = (path or "").strip()
        if _looks_like_dev_review_comment(body_clean):
            review_body_error = _validate_dev_review_comment_body(body_clean)
            if review_body_error:
                raise ValueError(review_body_error)
            if (
                not allow_review_discussion_comment
                and not (path_clean and position is not None)
            ):
                raise ValueError(
                    "dev-reviewer 检视意见必须作为行评提交：请同时传 path 与 position；"
                    "仅架构/文档类例外才可显式允许讨论区评论"
                )
        if self.dry_run:
            return self._dry_run_log(
                "create_pull_comment",
                {
                    "number": number,
                    "body": body_clean,
                    "path": path,
                    "position": position,
                    "position_type": position_type,
                    "target_project": target_project,
                    "need_to_resolve": need_to_resolve,
                    "allow_review_discussion_comment": allow_review_discussion_comment,
                },
            )

        owner, repo = self._pull_request_repo(target_project)
        api_path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}/comments"
        )
        data: Dict[str, Any] = {"body": body_clean}
        if path_clean:
            data["path"] = path_clean
        if position is not None:
            data["position"] = position
        pos_type = (position_type or "").strip()
        if pos_type:
            data["position_type"] = pos_type
        if need_to_resolve is not None:
            data["need_to_resolve"] = bool(need_to_resolve)
        return self._request("POST", api_path, json_data=data)

    def resolve_pull_comment(
        self,
        number: int,
        discussion_id: str,
        resolved: bool = True,
        target_project: str = "fork",
    ) -> Dict[str, Any]:
        """修改检视意见（discussion）的解决状态。

        对应 GitCode API ``PUT /repos/:owner/:repo/pulls/:number/comments/
        :discussion_id``，请求体字段 ``resolved``（boolean，是否已解决）：
        ``true`` = 标记为「已解决 / 闭环」，``false`` = 标记为「未解决 / 重开」。

        Args:
            number: PR 编号。
            discussion_id: 检视意见的 discussion id，即提交行评/评论时返回的
                ``id``（哈希串），也可从「获取 PR 全部评论」结果的 ``id`` 取得。
            resolved: ``True``（默认）标记为已解决；``False`` 标记为未解决（重开）。
            target_project: PR/MR 所属仓库，支持 upstream 或 fork。
        """
        discussion_clean = (discussion_id or "").strip()
        resolved_flag = bool(resolved)
        if self.dry_run:
            return self._dry_run_log(
                "resolve_pull_comment",
                {
                    "number": number,
                    "discussion_id": discussion_clean,
                    "resolved": resolved_flag,
                    "target_project": target_project,
                },
            )
        if not discussion_clean:
            raise ValueError("discussion_id 不能为空")

        owner, repo = self._pull_request_repo(target_project)
        api_path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}/comments/{discussion_clean}"
        )
        result = self._request(
            "PUT",
            api_path,
            json_data={"resolved": resolved_flag},
        )
        if isinstance(result, dict):
            return result
        return {"resolved": resolved_flag}

    def get_pull_labels(
        self,
        number: int,
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """获取 Pull Request 的全部标签。"""
        owner, repo = self._pull_request_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}/labels"
        )
        result = self._request("GET", path)
        if isinstance(result, list):
            return result
        return []

    def add_pull_labels(
        self,
        number: int,
        labels: List[str],
        target_project: str = "fork",
    ) -> List[Dict[str, Any]]:
        """为 Pull Request 添加标签（Body 为标签名 JSON 数组）。"""
        if self.dry_run:
            result = self._dry_run_log(
                "add_pull_labels",
                {
                    "number": number,
                    "labels": labels,
                    "target_project": target_project,
                },
            )
            return [result]

        owner, repo = self._pull_request_repo(target_project)
        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls/{number}/labels"
        )
        return self._request("POST", path, json_data=labels)

    # ── Webhook 操作（默认 upstream 仓）──────────────

    def list_hooks(self, target_project: str = "upstream") -> List[Dict[str, Any]]:
        """列出仓库 Webhook。"""
        owner, repo = self._pull_repo(target_project)
        path = f"/repos/{owner}/{repo}/hooks"
        return self._request("GET", path)

    def create_hook(
        self,
        url: str,
        password: str = "",
        events: Optional[List[str]] = None,
        target_project: str = "upstream",
    ) -> Dict[str, Any]:
        """创建仓库 Webhook。"""
        owner, repo = self._pull_repo(target_project)
        data: Dict[str, Any] = {"url": url}
        if password:
            data["password"] = password
        if events:
            data["events"] = events
        if self.dry_run:
            return self._dry_run_log(
                "create_hook",
                {
                    "owner": owner,
                    "repo": repo,
                    "url": url,
                    "password": "<set>" if password else "",
                    "events": events or [],
                },
            )
        path = f"/repos/{owner}/{repo}/hooks"
        return self._request("POST", path, json_data=data)

    def delete_hook(self, hook_id: int | str, target_project: str = "upstream") -> Dict[str, Any]:
        """删除仓库 Webhook。"""
        owner, repo = self._pull_repo(target_project)
        if self.dry_run:
            return self._dry_run_log(
                "delete_hook",
                {"owner": owner, "repo": repo, "hook_id": hook_id},
            )
        path = f"/repos/{owner}/{repo}/hooks/{hook_id}"
        return self._request("DELETE", path)

    def create_pull_request(
        self,
        title: str,
        head: str,
        base: str = "",
        body: str = "",
        check_open_duplicate: bool = True,
        target_project: str = "fork",
        issue: Optional[int] = None,
        resolved_head: Optional[str] = None,
        resolved_fork_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建 Pull Request。

        创建前可先检查该分支是否已有未关闭的 MR，
        避免重复创建导致 409 Conflict 错误。

        Args:
            title: PR 标题。
            head: 源分支。同仓 MR 可用分支名；跨仓到 upstream
                须 ``fork_owner:branch``。仅传分支名时需已配置
                ``fork.owner``（会自动补全前缀并附带 ``fork_path``）。
            base: 目标分支，默认 base_branch。
            body: PR 描述。
            check_open_duplicate: 为 True 时，创建前先查询是否已有
                相同 head 的 open PR（本地预检查，仍应以服务端为准）。
            target_project: PR/MR 所属仓库，支持 upstream 或 fork。
            issue: 关联的 Issue 编号（可选；API 以 string 发送）。
            resolved_head: 已解析的 head（与 ``resolved_fork_path`` 成对传入，
                避免重复计算）。
            resolved_fork_path: 已解析的 fork_path（跨仓 PR 时使用）。

        Returns:
            创建的 PR 详情；dry_run 时 ``details`` 含 ``head`` / ``fork_path``。
        """
        title_clean = (title or "").strip()
        issue_valid = self._validate_issue_number(issue)
        target_clean = (target_project or "fork").strip().lower()
        owner, repo = self._pull_request_repo(target_clean)

        if resolved_head is not None:
            head_clean = resolved_head.strip()
            fork_path = resolved_fork_path or ""
        else:
            head_clean, fork_path = self.resolve_pull_request_head(
                head, target_clean
            )

        base_clean = (base or self.base_branch).strip()
        body_clean = body or ""

        if not self.dry_run:
            if not title_clean:
                raise ValueError("PR 标题不能为空")
            if not head_clean:
                raise ValueError("PR head 不能为空")

        if self.dry_run:
            dry_details: Dict[str, Any] = {
                "title": title_clean,
                "head": head_clean,
                "base": base_clean,
                "body": body_clean,
                "target_project": target_clean,
            }
            if fork_path:
                dry_details["fork_path"] = fork_path
            if issue_valid is not None:
                dry_details["issue"] = str(issue_valid)
            return self._dry_run_log(
                "create_pull_request",
                dry_details,
            )

        if check_open_duplicate:
            existing = self.list_pull_requests(
                state="open",
                head=head_clean,
                target_project=target_clean,
            )
            if isinstance(existing, list) and existing:
                first = existing[0]
                num = first.get("number", "?")
                raise GitCodeClientError(
                    "已存在指向该 head 的 open PR（#%s），"
                    "请先关闭或合并后再创建，或传入 "
                    "check_open_duplicate=False。"
                    % num,
                    status_code=409,
                    redact_token=self.token,
                )

        path = (
            f"/repos/{owner}"
            f"/{repo}/pulls"
        )
        data: Dict[str, Any] = {
            "title": title_clean,
            "head": head_clean,
            "base": base_clean,
            "body": body_clean,
        }
        if fork_path:
            data["fork_path"] = fork_path
        if issue_valid is not None:
            data["issue"] = str(issue_valid)
        return self._request("POST", path, json_data=data)

    # ── 内部方法 ────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Any = None,
    ) -> Any:
        """发送 API 请求，处理认证和限流。

        Args:
            method: HTTP 方法。
            path: API 路径（不含 BASE_URL）。
            params: 查询参数。
            json_data: 请求体 JSON 数据。

        Returns:
            响应 JSON。

        Raises:
            GitCodeClientError: API 调用失败。
        """
        url = f"{self.BASE_URL}{path}"
        if params is None:
            params = {}
        else:
            params = dict(params)

        params["access_token"] = self.token

        for attempt in range(self.MAX_RETRIES):
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data, headers=headers,
                    timeout=30,
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_WAIT_SECONDS)
                    continue
                msg = _redact_secrets(str(exc), self.token)
                raise GitCodeClientError(
                    f"请求失败: {msg}",
                    redact_token="",
                ) from exc

            if resp.status_code == 429:
                if attempt < self.MAX_RETRIES - 1:
                    wait = self.RETRY_WAIT_SECONDS * (
                        attempt + 1
                    )
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
                safe_text = _redact_secrets(
                    resp.text[:500], self.token
                )
                raise GitCodeClientError(
                    f"API 错误 {resp.status_code}: {safe_text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    redact_token=self.token,
                )

            # 204 无内容，或 2xx 空响应体（如 PUT 修改解决状态成功）
            # 一律按成功处理，避免对空 body 调 .json() 误报错误。
            if resp.status_code == 204 or not (resp.text or "").strip():
                return {}
            try:
                return resp.json()
            except ValueError:
                safe_text = _redact_secrets(
                    resp.text[:500], self.token
                )
                raise GitCodeClientError(
                    f"API 返回非 JSON 响应（{resp.status_code}）："
                    f"{safe_text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    redact_token=self.token,
                )

        raise GitCodeClientError(
            "已达最大重试次数",
            redact_token=self.token,
        )

    # ── 工厂方法 ────────────────────────────

    @classmethod
    def from_config(
        cls,
        config_path: Optional[str] = None,
        workspace_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> "GitCodeClient":
        """从配置文件和环境变量创建客户端。

        认证优先级：
        1. 环境变量 GITCODE_TOKEN
        2. 配置文件中的 gitcode_token 字段
        3. 交互式提示输入

        配置文件查找顺序（当 config_path 为 None 时）：
        1. 当前目录下的 gitcode-repo.json
        2. 脚本所在目录的父目录下的 gitcode-repo.json

        多工作区：配置含 ``workspaces[]`` 时，用 ``workspace_name`` 或
        ``--workspace`` 指定；仅一条时可省略。

        Args:
            config_path: 配置文件路径。
            workspace_name: 工作区名称（对应 workspaces[].name）。
            dry_run: 是否为 Dry Run 模式（只打印不执行）。

        Returns:
            GitCodeClient 实例。
        """
        resolved_path = find_config_path(config_path or "")
        raw = load_raw_config(resolved_path)
        try:
            config = resolve_workspace_config(raw, workspace_name)
        except ConfigError as exc:
            print(
                json.dumps({"error": str(exc)}, ensure_ascii=False)
            )
            sys.exit(1)

        upstream = config.get("upstream", {})
        fork = config.get("fork", {})

        token = os.environ.get("GITCODE_TOKEN", "")
        if not token:
            token = config.get("gitcode_token", "")
        if not token:
            token = input("请输入 GitCode Token: ").strip()
        if not token:
            print(
                "错误: 未提供 GitCode Token",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            return cls(
                token=token,
                upstream_owner=upstream.get(
                    "owner", config.get("owner", "")
                ),
                upstream_repo=upstream.get(
                    "repo", config.get("repo", "")
                ),
                fork_owner=fork.get("owner", ""),
                fork_repo=fork.get(
                    "repo",
                    upstream.get(
                        "repo", config.get("repo", "")
                    ),
                ),
                base_branch=upstream.get(
                    "base_branch",
                    config.get("base_branch", "main"),
                ),
                dry_run=dry_run,
            )
        except ValueError as exc:
            print(
                json.dumps(
                    {"error": str(exc)},
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
