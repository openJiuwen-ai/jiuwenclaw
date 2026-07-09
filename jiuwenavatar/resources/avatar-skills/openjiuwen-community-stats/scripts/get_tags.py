#!/usr/bin/env python3
"""Fetch all tags for an openJiuwen repository from GitCode API."""

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


def fetch_all_tags(repo: str) -> tuple[list[dict] | None, str | None]:
    """列出仓库全部 tags，自动翻页。API: GET /repos/{owner}/{repo}/tags"""
    path = f"/api/v5/repos/{API_OWNER}/{urllib.parse.quote(repo, safe='')}/tags"
    token = os.environ.get("GITCODE_ACCESS_TOKEN", "").strip()
    tags, page = [], 1

    while True:
        params: dict = {"per_page": 100, "page": page}
        if token:
            params["access_token"] = token
        query = urllib.parse.urlencode(params)
        data, err = _get(path, query)
        if err:
            return (None, err) if page == 1 else (tags, None)
        if not isinstance(data, list) or not data:
            break
        for tag in data:
            commit = tag.get("commit") or {}
            tagger = tag.get("tagger") or {}
            tags.append({
                "name": tag.get("name", ""),
                "message": tag.get("message", ""),
                "commit_sha": commit.get("sha", ""),
                "commit_date": commit.get("date", ""),
                "tagger_name": tagger.get("name", ""),
                "tagger_email": tagger.get("email", ""),
                "tagger_date": tagger.get("date", ""),
            })
        if len(data) < 100:
            break
        page += 1
    return tags, None


def main():
    parser = argparse.ArgumentParser(description="Fetch all tags for an openJiuwen repo")
    parser.add_argument("repo", help="仓库名，如 agent-core")
    args = parser.parse_args()

    repo = args.repo.strip()
    print(f"[正在查询 {DISPLAY_ORG}/{repo} 的 tags...]", file=sys.stderr)

    tags, err = fetch_all_tags(repo)
    if tags is None:
        print(f"[错误] 无法获取 {DISPLAY_ORG}/{repo} 的 tags: {err}", file=sys.stderr)
        sys.exit(1)

    output = {
        "queried_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "org": DISPLAY_ORG,
        "repo": repo,
        "tag_count": len(tags),
        "tags": tags,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
