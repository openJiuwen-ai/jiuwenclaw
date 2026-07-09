#!/usr/bin/env python3
"""Fetch contributor statistics for a single user in an openJiuwen repository."""

import argparse
import http.client
import json
import os
import socket
import sys
import urllib.parse
from datetime import datetime, timezone

socket.setdefaulttimeout(12)

API_HOST = "api.gitcode.com"
API_OWNER = "openjiuwen"
DISPLAY_ORG = "openJiuwen"


def _get(path: str, query: str = "") -> tuple[list | dict | None, str | None]:
    """GET GitCode API path; returns (json_body, error_message)."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "openjiuwen-stats/1.0",
    }
    full_path = path if not query else f"{path}?{query}"
    conn = http.client.HTTPSConnection(API_HOST, timeout=12)
    try:
        conn.request("GET", full_path, "", headers)
        res = conn.getresponse()
        body = res.read().decode()
        if res.status < 200 or res.status >= 300:
            return None, f"HTTP {res.status}: {body[:300]}"
        data = json.loads(body)
        if isinstance(data, dict) and data.get("error_code"):
            return None, (
                f"API {data.get('error_code')}: "
                f"{data.get('error_message') or data.get('error_code_name', '')}"
            )
        return data, None
    except Exception as exc:
        return None, str(exc)
    finally:
        conn.close()


def fetch_contributor(
    repo: str,
    author: str,
    *,
    since: str = "",
    until: str = "",
    ref_name: str = "",
) -> tuple[dict | None, str | None]:
    """获取单个贡献者统计。API: GET /repos/{owner}/{repo}/contributors/statistic"""
    path = (
        f"/api/v5/repos/{API_OWNER}/{urllib.parse.quote(repo, safe='')}"
        f"/contributors/statistic"
    )
    params: dict = {"author": author}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    if ref_name:
        params["ref_name"] = ref_name
    token = os.environ.get("GITCODE_ACCESS_TOKEN", "").strip()
    if token:
        params["access_token"] = token

    data, err = _get(path, urllib.parse.urlencode(params))
    if err:
        return None, err
    if not isinstance(data, list):
        return None, "响应不是 JSON 数组"
    if not data:
        return None, f"未找到贡献者 {author!r}（请确认 GitCode 用户名是否正确）"
    return parse_contributor(data[0]), None


def parse_contributor(raw: dict) -> dict:
    overview = raw.get("overview") or {}
    contributions = []
    for entry in raw.get("contributions") or []:
        contributions.append({
            "date": entry.get("date", ""),
            "additions": entry.get("additions", 0),
            "deletions": entry.get("deletions", 0),
            "total_changes": entry.get("total_changes", 0),
            "commit_count": entry.get("commit_count", 0),
        })
    return {
        "name": raw.get("name", ""),
        "email": raw.get("email", ""),
        "overview": {
            "additions": overview.get("additions", 0),
            "deletions": overview.get("deletions", 0),
            "total_changes": overview.get("total_changes", 0),
            "commit_count": overview.get("commit_count", 0),
        },
        "contribution_days": len(contributions),
        "contributions": contributions,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fetch contributor statistics for one user in an openJiuwen repo",
    )
    parser.add_argument("repo", help="仓库名，如 agent-core")
    parser.add_argument("author", help="贡献者 GitCode 用户名")
    parser.add_argument(
        "--since",
        default="",
        help="起始日期，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss",
    )
    parser.add_argument(
        "--until",
        default="",
        help="结束日期，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss",
    )
    parser.add_argument(
        "--ref-name",
        default="",
        help="分支名、commit ID 或 tag 名；省略则使用默认分支",
    )
    args = parser.parse_args()

    repo = args.repo.strip()
    author = args.author.strip()
    print(f"[正在查询 {DISPLAY_ORG}/{repo} 贡献者 {author}...]", file=sys.stderr)

    contributor, err = fetch_contributor(
        repo,
        author,
        since=args.since.strip(),
        until=args.until.strip(),
        ref_name=args.ref_name.strip(),
    )
    if not contributor:
        print(f"[错误] 无法获取贡献者统计: {err}", file=sys.stderr)
        sys.exit(1)

    output = {
        "queried_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "org": DISPLAY_ORG,
        "repo": repo,
        "author": author,
        "filters": {
            "since": args.since.strip() or None,
            "until": args.until.strip() or None,
            "ref_name": args.ref_name.strip() or None,
        },
        "contributor": contributor,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
