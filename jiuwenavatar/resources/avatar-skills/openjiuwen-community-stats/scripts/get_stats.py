#!/usr/bin/env python3
"""Fetch openJiuwen full organization statistics from GitCode API."""

import http.client
import json
import os
import socket
import sys
import time
import urllib.parse
from datetime import datetime, timezone

socket.setdefaulttimeout(12)

API_HOST = "api.gitcode.com"
API_OWNER = "openjiuwen"
DISPLAY_ORG = "openJiuwen"

# 跳过无实质内容的仓库
SKIP_REPOS = {
    ".gitcode",
}


def _encode_params(params: dict) -> str:
    token = os.environ.get("GITCODE_ACCESS_TOKEN", "").strip()
    if token:
        params = {**params, "access_token": token}
    return urllib.parse.urlencode(params)


def _get(path: str, params: dict | None = None) -> tuple[object | None, str | None]:
    """GET GitCode API path; returns (json_body, error_message)."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "openjiuwen-stats/1.0",
    }
    query = _encode_params(params) if params else ""
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


def fetch_all_repos() -> tuple[list[dict], str | None]:
    repos, page = [], 1
    path = f"/api/v5/orgs/{API_OWNER}/repos"
    while True:
        data, err = _get(path, {"per_page": 100, "page": page})
        if err:
            return repos, err if page == 1 else None
        if not isinstance(data, list) or not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos, None


def fetch_contributors_count(repo: str) -> int:
    path = f"/api/v5/repos/{API_OWNER}/{repo}/contributors"
    data, _ = _get(path, {"per_page": 100})
    if isinstance(data, list):
        return len(data)
    return 0


def fetch_download_stats(repo: str) -> dict | None:
    path = f"/api/v5/repos/{API_OWNER}/{repo}/download_statistics"
    data, _ = _get(path)
    if not isinstance(data, dict):
        return None
    total = data.get("download_statistics_history_total", 0)
    if not total:
        return None
    details = data.get("download_statistics_detail", [])
    recent_30d = sum(d.get("today_dl_cnt", 0) for d in details[:30])
    yesterday = details[0].get("today_dl_cnt", 0) if details else 0
    latest_date = details[0].get("pdate", "") if details else ""
    return {
        "total": total,
        "recent_30d": recent_30d,
        "yesterday": yesterday,
        "latest_date": latest_date,
    }


def fetch_pr_count_approx(repo: str) -> int:
    """取最新 PR 编号作为历史 PR 总数近似。"""
    path = f"/api/v5/repos/{API_OWNER}/{repo}/pulls"
    data, _ = _get(path, {"state": "all", "per_page": 1, "page": 1})
    if isinstance(data, list) and data:
        return int(data[0].get("number", 0))
    return 0


def fetch_issue_count_approx(repo: str) -> int:
    """取最新 Issue 编号作为历史 Issue 总数近似。"""
    path = f"/api/v5/repos/{API_OWNER}/{repo}/issues"
    data, _ = _get(path, {"state": "all", "per_page": 1, "page": 1})
    if isinstance(data, list) and data:
        return int(data[0].get("number", 0))
    return 0


def main():
    queried_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("[正在查询组织仓库列表...]", file=sys.stderr)

    all_repos, err = fetch_all_repos()
    if err:
        print(f"[错误] 无法获取组织仓库列表: {err}", file=sys.stderr)
        sys.exit(1)
    if not all_repos:
        print("[错误] 组织仓库列表为空，请检查 API owner 或网络", file=sys.stderr)
        sys.exit(1)

    active_repos = [r for r in all_repos if r["name"] not in SKIP_REPOS]
    active_repos.sort(key=lambda r: r["stargazers_count"], reverse=True)
    print(f"[共 {len(all_repos)} 个仓库，过滤后 {len(active_repos)} 个，开始逐仓统计]", file=sys.stderr)

    org_totals = {
        "total_stars": sum(r["stargazers_count"] for r in active_repos),
        "total_forks": sum(r["forks_count"] for r in active_repos),
        "total_open_issues": sum(r["open_issues_count"] for r in active_repos),
    }

    repo_details = []
    total_downloads = 0
    total_downloads_30d = 0
    total_contributors = 0
    total_pr_approx = 0
    total_issue_approx = 0

    for ri in active_repos:
        repo = ri["name"]
        print(f"  [{repo}] ...", file=sys.stderr)

        contributors = fetch_contributors_count(repo)
        dl = fetch_download_stats(repo)
        pr_approx = fetch_pr_count_approx(repo)
        issue_approx = fetch_issue_count_approx(repo)

        if dl:
            total_downloads += dl["total"]
            total_downloads_30d += dl["recent_30d"]
        total_contributors += contributors
        total_pr_approx += pr_approx
        total_issue_approx += issue_approx

        repo_details.append({
            "repo": repo,
            "stars": ri["stargazers_count"],
            "forks": ri["forks_count"],
            "open_issues": ri["open_issues_count"],
            "contributors": contributors,
            "pr_count_approx": pr_approx,
            "issue_count_approx": issue_approx,
            "downloads": dl,
        })
        time.sleep(0.15)

    org_totals.update({
        "total_contributors_sum": total_contributors,
        "total_downloads": total_downloads,
        "total_downloads_30d": total_downloads_30d,
        "total_pr_approx": total_pr_approx,
        "total_issue_approx": total_issue_approx,
    })

    output = {
        "queried_at": queried_at,
        "org": DISPLAY_ORG,
        "repo_count": len(active_repos),
        "org_totals": org_totals,
        "repos": repo_details,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
