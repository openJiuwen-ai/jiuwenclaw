#!/usr/bin/env python3
"""Fetch release assets and download URLs for an openJiuwen repo tag."""

import argparse
import http.client
import json
import os
import socket
import sys
import urllib.parse
from datetime import datetime, timezone

socket.setdefaulttimeout(12)

# GitCode API owner slug is lowercase; display name stays openJiuwen.
API_HOST = "api.gitcode.com"
API_OWNER = "openjiuwen"
DISPLAY_ORG = "openJiuwen"


def _get(path: str, query: str = "") -> tuple[dict | None, str | None]:
    """GET GitCode API path; returns (json_dict, error_message)."""
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
        if not isinstance(data, dict):
            return None, "响应不是 JSON 对象"
        if data.get("error_code"):
            return None, (
                f"API {data.get('error_code')}: "
                f"{data.get('error_message') or data.get('error_code_name', '')}"
            )
        return data, None
    except Exception as exc:
        return None, str(exc)
    finally:
        conn.close()


def fetch_release(repo: str, tag: str) -> tuple[dict | None, str | None]:
    """获取指定 tag 的 Release 及附件下载地址。"""
    path = f"/api/v5/repos/{API_OWNER}/{repo}/releases/{urllib.parse.quote(tag, safe='')}"
    token = os.environ.get("GITCODE_ACCESS_TOKEN", "").strip()

    # Public release endpoint works without a token (GitCode API v5).
    data, err = _get(path)
    if data:
        return data, None

    if not token:
        return None, err

    query = urllib.parse.urlencode({
        "access_token": token,
        "temp_download_url": "true",
    })
    return _get(path, query)


def parse_release(data: dict) -> dict:
    author = data.get("author") or {}
    assets = []
    for asset in data.get("assets") or []:
        assets.append({
            "name": asset.get("name", ""),
            "type": asset.get("type", ""),
            "browser_download_url": asset.get("browser_download_url", ""),
            "temp_download_url": asset.get("temp_download_url", ""),
        })
    return {
        "tag_name": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "prerelease": data.get("prerelease", False),
        "created_at": data.get("created_at", ""),
        "release_status": data.get("release_status", ""),
        "target_commitish": data.get("target_commitish", ""),
        "author_login": author.get("login", ""),
        "body": data.get("body", ""),
        "assets": assets,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch release assets for an openJiuwen repo tag")
    parser.add_argument("repo", help="仓库名，如 agent-core")
    parser.add_argument("tag", help="tag 名称，如 v0.1.13")
    args = parser.parse_args()

    repo = args.repo.strip()
    tag = args.tag.strip()
    print(f"[正在查询 {DISPLAY_ORG}/{repo} release {tag}...]", file=sys.stderr)

    data, err = fetch_release(repo, tag)
    if not data:
        print(f"[错误] 无法获取 {DISPLAY_ORG}/{repo}@{tag} 的 release: {err}", file=sys.stderr)
        sys.exit(1)

    release = parse_release(data)
    output = {
        "queried_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "org": DISPLAY_ORG,
        "repo": repo,
        "tag": tag,
        "asset_count": len(release["assets"]),
        "release": release,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
