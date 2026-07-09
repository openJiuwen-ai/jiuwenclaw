#!/usr/bin/env python3
"""Sync openJiuwen data from GitCode API to local SQLite database.

Usage:
    python3 sync_data.py                    # full sync
    python3 sync_data.py --issues-only      # only sync issues
    python3 sync_data.py --repos-only       # only sync repo stats
    python3 sync_data.py --org openJiuwen   # specify organization
    
This script should be run periodically (e.g., daily via cron) to maintain
historical data for trend analysis.

NOTE: This script only uses READ operations (GET requests).
      No data is written to GitCode (no POST/PATCH/DELETE).
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, List, Dict, Any

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DB_PATH = DATA_DIR / "community.db"

# Import local modules
sys.path.insert(0, str(SCRIPT_DIR))
from gitcode_client import GitCodeReadOnlyClient, GitCodeClientError
from config_loader import load_config, get_token, get_organization, get_skip_repos


def init_db(conn: sqlite3.Connection):
    """Initialize database schema."""
    conn.executescript("""
        -- Repo daily snapshots for trend analysis
        CREATE TABLE IF NOT EXISTS repos_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org TEXT NOT NULL DEFAULT 'openJiuwen',
            repo TEXT NOT NULL,
            snapshot_date DATE NOT NULL,
            stars INTEGER,
            forks INTEGER,
            open_issues INTEGER,
            watchers INTEGER,
            downloads_total INTEGER,
            downloads_30d INTEGER,
            contributors INTEGER,
            pr_count_approx INTEGER,
            issue_count_approx INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org, repo, snapshot_date)
        );
        
        -- Full issue details
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org TEXT NOT NULL DEFAULT 'openJiuwen',
            repo TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            title TEXT,
            state TEXT,
            author TEXT,
            created_at DATETIME,
            updated_at DATETIME,
            closed_at DATETIME,
            labels TEXT,
            milestone TEXT,
            assignees TEXT,
            comments_count INTEGER DEFAULT 0,
            synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org, repo, issue_number)
        );
        
        -- Sync log
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            org TEXT,
            sync_type TEXT,
            repos_synced INTEGER,
            issues_synced INTEGER,
            duration_seconds REAL,
            status TEXT,
            error_message TEXT
        );
        
        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_repos_daily_date ON repos_daily(snapshot_date);
        CREATE INDEX IF NOT EXISTS idx_repos_daily_repo ON repos_daily(repo);
        CREATE INDEX IF NOT EXISTS idx_repos_daily_org ON repos_daily(org);
        CREATE INDEX IF NOT EXISTS idx_issues_state ON issues(state);
        CREATE INDEX IF NOT EXISTS idx_issues_created ON issues(created_at);
        CREATE INDEX IF NOT EXISTS idx_issues_repo ON issues(repo);
        CREATE INDEX IF NOT EXISTS idx_issues_org ON issues(org);
    """)
    conn.commit()


def sync_repos(
    conn: sqlite3.Connection,
    client: GitCodeReadOnlyClient,
    skip_repos: set,
    org: str,
) -> int:
    """Sync repo statistics to database (READ-ONLY from API)."""
    today = date.today().isoformat()
    
    print("[Fetching repo list...]", file=sys.stderr)
    all_repos = client.get_all_org_repos()
    repos = [r for r in all_repos if r.get("name") not in skip_repos]
    print(f"[Found {len(repos)} repos (skipped {len(all_repos) - len(repos)})]", file=sys.stderr)
    
    count = 0
    for r in repos:
        repo_name = r["name"]
        print(f"  [{repo_name}] syncing stats...", file=sys.stderr)
        
        # Get download stats
        dl_data = client.get_download_stats(repo_name)
        dl_total = None
        dl_30d = None
        if dl_data:
            dl_total = dl_data.get("download_statistics_history_total", 0)
            details = dl_data.get("download_statistics_detail", [])
            dl_30d = sum(d.get("today_dl_cnt", 0) for d in details[:30])
        
        # Get contributors count
        try:
            contributors = len(client.get_repo_contributors(repo_name))
        except GitCodeClientError:
            contributors = 0
        
        # Get PR count (approximate from latest number)
        pr_approx = 0
        try:
            prs = client.list_pulls(repo_name, state="all", per_page=1)
            if prs:
                pr_approx = prs[0].get("number", 0)
        except GitCodeClientError:
            pass
        
        # Get issue count (approximate from latest number)
        issue_approx = 0
        try:
            issues = client.list_issues(repo_name, state="all", per_page=1)
            if issues:
                issue_approx = issues[0].get("number", 0)
        except GitCodeClientError:
            pass
        
        conn.execute("""
            INSERT OR REPLACE INTO repos_daily 
            (org, repo, snapshot_date, stars, forks, open_issues, watchers, 
             downloads_total, downloads_30d, contributors, pr_count_approx, issue_count_approx)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org,
            repo_name,
            today,
            r.get("stargazers_count", 0),
            r.get("forks_count", 0),
            r.get("open_issues_count", 0),
            r.get("watchers_count", 0),
            dl_total,
            dl_30d,
            contributors,
            pr_approx,
            issue_approx,
        ))
        count += 1
        time.sleep(0.15)
    
    conn.commit()
    return count


def sync_issues(
    conn: sqlite3.Connection,
    client: GitCodeReadOnlyClient,
    skip_repos: set,
    org: str,
) -> int:
    """Sync all issues to database (READ-ONLY from API)."""
    all_repos = client.get_all_org_repos()
    repos = [r for r in all_repos if r.get("name") not in skip_repos]
    
    count = 0
    for r in repos:
        repo_name = r["name"]
        print(f"  [{repo_name}] syncing issues...", file=sys.stderr)
        
        try:
            issues = client.get_all_issues(repo_name, state="all")
        except GitCodeClientError as e:
            print(f"    [WARN] Failed to fetch issues: {e}", file=sys.stderr)
            continue
        
        for issue in issues:
            labels = json.dumps([l.get("name", "") for l in issue.get("labels", [])])
            assignees = json.dumps([a.get("login", "") for a in issue.get("assignees", [])])
            milestone = issue.get("milestone", {}).get("title") if issue.get("milestone") else None
            
            conn.execute("""
                INSERT OR REPLACE INTO issues
                (org, repo, issue_number, title, state, author, created_at, updated_at, 
                 closed_at, labels, milestone, assignees, comments_count, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                org,
                repo_name,
                issue.get("number"),
                issue.get("title", ""),
                issue.get("state", ""),
                (issue.get("user") or {}).get("login", ""),
                issue.get("created_at"),
                issue.get("updated_at"),
                issue.get("closed_at"),
                labels,
                milestone,
                assignees,
                issue.get("comments", 0),
            ))
            count += 1
        
        conn.commit()
        time.sleep(0.1)
    
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Sync GitCode data to local database (READ-ONLY)"
    )
    parser.add_argument("--issues-only", action="store_true", help="Only sync issues")
    parser.add_argument("--repos-only", action="store_true", help="Only sync repo stats")
    parser.add_argument("--org", default="openJiuwen", help="Organization name (default: openJiuwen)")
    parser.add_argument("--config", default="", help="Config file path")
    args = parser.parse_args()
    
    # Load config
    config = load_config(args.config)
    token = get_token(config)
    org_config = get_organization(config, args.org)
    org_name = org_config.get("name", args.org)
    skip_repos = get_skip_repos(org_config)
    
    # Create client (READ-ONLY)
    client = GitCodeReadOnlyClient(token=token, org=org_name)
    
    # Initialize database
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    
    start_time = time.time()
    repos_synced = 0
    issues_synced = 0
    error_msg = None
    
    try:
        if not args.issues_only:
            print(f"[Syncing repo statistics for {org_name}...]", file=sys.stderr)
            repos_synced = sync_repos(conn, client, skip_repos, org_name)
        
        if not args.repos_only:
            print(f"[Syncing issues for {org_name}...]", file=sys.stderr)
            issues_synced = sync_issues(conn, client, skip_repos, org_name)
        
        status = "success"
    except Exception as e:
        status = "error"
        error_msg = str(e)
        print(f"[ERROR] {e}", file=sys.stderr)
    
    duration = time.time() - start_time
    
    conn.execute("""
        INSERT INTO sync_log (org, sync_type, repos_synced, issues_synced, duration_seconds, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        org_name,
        "repos" if args.repos_only else ("issues" if args.issues_only else "full"),
        repos_synced,
        issues_synced,
        duration,
        status,
        error_msg,
    ))
    conn.commit()
    
    result = {
        "status": status,
        "org": org_name,
        "repos_synced": repos_synced,
        "issues_synced": issues_synced,
        "duration_seconds": round(duration, 1),
        "database": str(DB_PATH),
        "note": "READ-ONLY: No data written to GitCode",
    }
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
