# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small GitCode API client for auto-harness issue ingestion."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitCodeIssue:
    """Normalized GitCode issue metadata used by the issue runner."""

    number: int
    title: str
    body: str
    html_url: str
    labels: tuple[str, ...]
    raw: dict[str, Any]


class GitCodeIssueClient:
    """HTTP client for the GitCode repository issue and pull-request APIs."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.gitcode.com/api/v5",
        timeout: float = 20.0,
        trust_env: bool = False,
    ) -> None:
        self._token = token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._trust_env = trust_env

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "jiuwenswarm-auto-harness",
        }
        if self._token:
            # GitCode release code in this repo already uses PRIVATE-TOKEN.
            # Authorization is included for compatibility with bearer-style APIs.
            headers["PRIVATE-TOKEN"] = self._token
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        with httpx.Client(
            timeout=self._timeout,
            headers=self._headers(),
            trust_env=self._trust_env,
        ) as client:
            response = client.request(method, url, params=params, json=json_body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:500]
            logger.warning("[GitCodeIssueClient] %s %s failed: %s", method, path, detail)
            raise RuntimeError(
                f"GitCode API {method} {path} failed with {response.status_code}: {detail}"
            ) from exc
        if not response.content:
            return None
        return response.json()

    @staticmethod
    def _normalize_labels(raw_labels: Any) -> tuple[str, ...]:
        labels: list[str] = []
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, str):
                    labels.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("title")
                    if name:
                        labels.append(str(name))
        elif isinstance(raw_labels, str):
            labels.extend(part.strip() for part in raw_labels.split(",") if part.strip())
        return tuple(labels)

    @classmethod
    def _normalize_issue(cls, raw: dict[str, Any]) -> GitCodeIssue:
        number_raw = raw.get("number", raw.get("iid", raw.get("id", 0)))
        try:
            number = int(number_raw)
        except (TypeError, ValueError):
            number = 0
        return GitCodeIssue(
            number=number,
            title=str(raw.get("title") or ""),
            body=str(raw.get("body") or raw.get("description") or ""),
            html_url=str(raw.get("html_url") or raw.get("url") or ""),
            labels=cls._normalize_labels(raw.get("labels")),
            raw=raw,
        )

    def list_issues(
        self,
        *,
        owner: str,
        repo: str,
        state: str = "open",
        labels: Optional[list[str]] = None,
        sort: str = "updated",
        direction: str = "desc",
        page: int = 1,
        per_page: int = 20,
    ) -> list[GitCodeIssue]:
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "page": page,
            "per_page": per_page,
        }
        if labels:
            params["labels"] = ",".join(labels)
        data = self._request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        if not isinstance(data, list):
            return []
        return [self._normalize_issue(item) for item in data if isinstance(item, dict)]

    def get_issue(self, *, owner: str, repo: str, number: int) -> GitCodeIssue:
        data = self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")
        if not isinstance(data, dict):
            raise RuntimeError(f"GitCode issue #{number} response is not an object")
        return self._normalize_issue(data)

    def list_issue_pull_requests(self, *, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/repos/{owner}/{repo}/issues/{number}/pull_requests")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def list_pull_requests(
        self,
        *,
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": state,
                "page": page,
                "per_page": per_page,
            },
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def create_issue_comment(self, *, owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json_body={"body": body},
        )
        return data if isinstance(data, dict) else {}
