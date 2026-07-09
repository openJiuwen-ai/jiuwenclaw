#!/usr/bin/env python3
"""Fetch open issues for openJiuwen org from GitCode API with age filtering.

Usage:
    python3 get_issues.py                      # all open issues
    python3 get_issues.py --days 30            # open issues older than 30 days
    python3 get_issues.py --repo agent-core    # only one repo
    python3 get_issues.py --days 7 --repo jiuwenswarm
    python3 get_issues.py --state closed       # closed issues
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# 导入本地模块
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from gitcode_client import GitCodeReadOnlyClient
from config_loader import load_config


def get_client_and_config(org: str) -> tuple:
    """初始化客户端和配置"""
    config = load_config()
    token = config.get("gitcode_token", "")
    client = GitCodeReadOnlyClient(token, org=org)
    return client, config


def fetch_all_repos(client: GitCodeReadOnlyClient, skip_repos: set) -> List[Dict]:
    """获取组织下所有仓库（使用 client 的自动分页）"""
    all_repos = client.get_all_org_repos()
    return [r for r in all_repos if r.get("name") not in skip_repos]


def fetch_issues(client: GitCodeReadOnlyClient, repo: str, state: str = "open") -> List[Dict]:
    """获取仓库的所有 issue（使用 client 的自动分页和重试机制）"""
    return client.get_all_issues(repo, state=state)


def parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string to UTC datetime."""
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser(description="Fetch openJiuwen issue details")
    parser.add_argument("--days", type=int, default=None,
                        help="Only show issues open/created more than N days ago")
    parser.add_argument("--repo", default=None,
                        help="Limit to a specific repo (e.g. agent-core)")
    parser.add_argument("--state", default="open", choices=["open", "closed", "all"],
                        help="Issue state (default: open)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max issues to return (0=无限制, default: 0)")
    parser.add_argument("--org", default="openJiuwen",
                        help="Organization name (default: openJiuwen)")
    args = parser.parse_args()

    org = args.org
    
    # 初始化客户端（传入 org）
    client, config = get_client_and_config(org)
    
    # 获取该组织的跳过仓库列表
    orgs_list = config.get("organizations", [])
    org_config = {}
    for o in orgs_list:
        if isinstance(o, dict) and o.get("name") == org:
            org_config = o
            break
    skip_repos = set(org_config.get("skip_repos", []))
    
    now = datetime.now(timezone.utc)
    queried_at = now.strftime("%Y-%m-%d %H:%M UTC")

    if args.repo:
        repos = [{"name": args.repo}]
    else:
        print("[正在获取仓库列表...]", file=sys.stderr)
        repos = fetch_all_repos(client, skip_repos)

    results = []
    total_fetched = 0
    total_matched = 0

    for repo_info in repos:
        repo = repo_info["name"]
        print(f"  [{repo}] 拉取 issue...", file=sys.stderr)
        issues = fetch_issues(client, repo, state=args.state)
        total_fetched += len(issues)
        print(f"  [{repo}] 获取到 {len(issues)} 条 issue", file=sys.stderr)

        matched = []
        for issue in issues:
            created_at = parse_dt(issue.get("created_at", ""))
            age_days = (now - created_at).days

            if args.days is not None and age_days < args.days:
                continue

            matched.append({
                "repo": repo,
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "age_days": age_days,
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "url": f"https://gitcode.com/{org}/{repo}/issues/{issue.get('number')}",
                "author": (issue.get("user") or {}).get("login", ""),
            })

        matched.sort(key=lambda x: x["age_days"], reverse=True)
        results.extend(matched)
        total_matched += len(matched)

    # Sort all results by age descending
    results.sort(key=lambda x: x["age_days"], reverse=True)
    
    # 应用 limit（在所有结果合并后）
    returned_count = len(results)
    if args.limit and args.limit > 0:
        results = results[:args.limit]
        returned_count = len(results)

    output = {
        "queried_at": queried_at,
        "org": org,
        "filter": {
            "state": args.state,
            "min_age_days": args.days,
            "repo": args.repo or "all",
            "limit": args.limit,
        },
        "total_fetched_from_api": total_fetched,
        "total_matched": total_matched,
        "returned_count": returned_count,
        "is_truncated": returned_count < total_matched,
        "issues": results,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
