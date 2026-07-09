#!/usr/bin/env python3
"""Query overdue/stale issues from local database.

Usage:
    python3 query_overdue.py                        # issues open > 30 days (default)
    python3 query_overdue.py --days 7               # issues open > 7 days  
    python3 query_overdue.py --days 60 --repo agent-core
    python3 query_overdue.py --label bug            # filter by label
    python3 query_overdue.py --author zhangsan      # filter by author
    python3 query_overdue.py --summary              # summary by repo
    python3 query_overdue.py --no-update 14         # no update in 14 days

This script queries the local database (much faster than API).
Run sync_data.py first to populate the database.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional, Dict

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR.parent / "data" / "community.db"


def check_db():
    """Check if database exists."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not found. Run sync_data.py first.",
            "hint": f"python3 {SCRIPT_DIR}/sync_data.py"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


def parse_date(s: str) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def query_overdue_issues(conn: sqlite3.Connection, args) -> dict:
    """Query overdue issues with filters."""
    now = datetime.now(timezone.utc)
    cutoff_created = (now - timedelta(days=args.days)).isoformat()
    
    # 构建基础查询条件
    where_clause = "WHERE state = 'open' AND created_at <= ?"
    params = [cutoff_created]
    
    if args.repo:
        where_clause += " AND LOWER(repo) = LOWER(?)"
        params.append(args.repo)
    
    if args.label:
        where_clause += " AND labels LIKE ?"
        params.append(f'%"{args.label}"%')
    
    if args.author:
        where_clause += " AND author = ?"
        params.append(args.author)
    
    if args.no_update:
        cutoff_updated = (now - timedelta(days=args.no_update)).isoformat()
        where_clause += " AND updated_at <= ?"
        params.append(cutoff_updated)
    
    # 先查询总数（不受 limit 影响）
    count_sql = f"SELECT COUNT(*) FROM issues {where_clause}"
    total_count = conn.execute(count_sql, params).fetchone()[0]
    
    # 再查询详情
    sql = f"""
        SELECT repo, issue_number, title, state, author, 
               created_at, updated_at, closed_at, labels, milestone, comments_count
        FROM issues
        {where_clause}
        ORDER BY created_at ASC
    """
    
    if args.limit and args.limit > 0:
        sql += f" LIMIT {args.limit}"
    
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    
    issues = []
    for row in rows:
        created = parse_date(row[5])
        updated = parse_date(row[6])
        age_days = (now - created).days if created else 0
        stale_days = (now - updated).days if updated else 0
        
        issues.append({
            "repo": row[0],
            "number": row[1],
            "title": row[2],
            "author": row[4],
            "age_days": age_days,
            "stale_days": stale_days,
            "created_at": row[5],
            "updated_at": row[6],
            "labels": json.loads(row[8]) if row[8] else [],
            "milestone": row[9],
            "comments": row[10],
            "url": f"https://gitcode.com/openJiuwen/{row[0]}/issues/{row[1]}",
        })
    
    returned_count = len(issues)
    is_truncated = returned_count < total_count
    
    return {
        "filter": {
            "min_age_days": args.days,
            "repo": args.repo or "all",
            "label": args.label,
            "author": args.author,
            "no_update_days": args.no_update,
            "limit": args.limit,
        },
        "total_count": total_count,
        "returned_count": returned_count,
        "is_truncated": is_truncated,
        "truncated_hint": f"结果已截断，共 {total_count} 条，仅返回 {returned_count} 条。如需全部结果请使用 --limit 0" if is_truncated else None,
        "issues": issues,
    }


def query_summary_by_repo(conn: sqlite3.Connection, args) -> dict:
    """Get summary of overdue issues grouped by repo."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=args.days)).isoformat()
    
    sql = """
        SELECT repo, COUNT(*) as count,
               MIN(created_at) as oldest_created,
               AVG(julianday('now') - julianday(created_at)) as avg_age_days
        FROM issues
        WHERE state = 'open'
          AND created_at <= ?
    """
    params = [cutoff]
    
    if args.label:
        sql += " AND labels LIKE ?"
        params.append(f'%"{args.label}"%')
    
    sql += " GROUP BY repo ORDER BY count DESC"
    
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    
    summary = []
    total = 0
    for row in rows:
        oldest = parse_date(row[2])
        oldest_age = (now - oldest).days if oldest else 0
        summary.append({
            "repo": row[0],
            "overdue_count": row[1],
            "oldest_age_days": oldest_age,
            "avg_age_days": round(row[3]) if row[3] else 0,
        })
        total += row[1]
    
    return {
        "filter": {"min_age_days": args.days, "label": args.label},
        "total_overdue": total,
        "by_repo": summary,
    }


def query_summary_by_label(conn: sqlite3.Connection, args) -> dict:
    """Get summary of overdue issues grouped by label."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=args.days)).isoformat()
    
    cur = conn.execute("""
        SELECT labels FROM issues
        WHERE state = 'open' AND created_at <= ?
    """, (cutoff,))
    
    label_counts = {}
    for (labels_json,) in cur.fetchall():
        labels = json.loads(labels_json) if labels_json else []
        for label in labels:
            label_counts[label] = label_counts.get(label, 0) + 1
    
    sorted_labels = sorted(label_counts.items(), key=lambda x: -x[1])
    
    return {
        "filter": {"min_age_days": args.days},
        "by_label": [{"label": k, "count": v} for k, v in sorted_labels[:20]],
    }


def get_db_stats(conn: sqlite3.Connection) -> dict:
    """Get database statistics."""
    cur = conn.execute("SELECT MAX(synced_at) FROM issues")
    last_sync = cur.fetchone()[0]
    
    cur = conn.execute("SELECT COUNT(*) FROM issues WHERE state = 'open'")
    open_count = cur.fetchone()[0]
    
    cur = conn.execute("SELECT COUNT(*) FROM issues")
    total_count = cur.fetchone()[0]
    
    cur = conn.execute("SELECT COUNT(DISTINCT repo) FROM issues")
    repo_count = cur.fetchone()[0]
    
    return {
        "last_sync": last_sync,
        "total_issues": total_count,
        "open_issues": open_count,
        "repos": repo_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Query overdue issues from local database")
    parser.add_argument("--days", type=int, default=30,
                        help="Issues open longer than N days (default: 30)")
    parser.add_argument("--repo", help="Filter by repo name")
    parser.add_argument("--label", help="Filter by label (substring match)")
    parser.add_argument("--author", help="Filter by author")
    parser.add_argument("--no-update", type=int, dest="no_update",
                        help="Issues with no update in N days")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max issues to return (0=无限制, default: 0)")
    parser.add_argument("--summary", action="store_true",
                        help="Show summary grouped by repo")
    parser.add_argument("--by-label", action="store_true", dest="by_label",
                        help="Show summary grouped by label")
    args = parser.parse_args()
    
    check_db()
    conn = sqlite3.connect(DB_PATH)
    
    db_stats = get_db_stats(conn)
    
    if args.summary:
        result = query_summary_by_repo(conn, args)
    elif args.by_label:
        result = query_summary_by_label(conn, args)
    else:
        result = query_overdue_issues(conn, args)
    
    result["database"] = {
        "path": str(DB_PATH),
        **db_stats,
    }
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
