#!/usr/bin/env python3
"""Query trend data from local database.

Usage:
    python3 query_trends.py                         # org-wide trends (7d, 30d)
    python3 query_trends.py --repo agent-core       # single repo trends
    python3 query_trends.py --days 90               # custom time window
    python3 query_trends.py --metric stars          # specific metric
    python3 query_trends.py --daily                 # daily breakdown
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Dict

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR.parent / "data" / "community.db"


def check_db():
    """Check if database exists and has data."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not found. Run sync_data.py first.",
            "hint": f"python3 {SCRIPT_DIR}/sync_data.py"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


def get_latest_snapshot_date(conn: sqlite3.Connection) -> Optional[str]:
    """Get the most recent snapshot date."""
    cur = conn.execute("SELECT MAX(snapshot_date) FROM repos_daily")
    row = cur.fetchone()
    return row[0] if row else None


def get_org_trends(conn: sqlite3.Connection, days: int) -> dict:
    """Calculate org-wide trends for the given time window."""
    today = date.today().isoformat()
    past = (date.today() - timedelta(days=days)).isoformat()
    
    cur = conn.execute("""
        SELECT 
            snapshot_date,
            SUM(stars) as total_stars,
            SUM(forks) as total_forks,
            SUM(open_issues) as total_open_issues,
            SUM(downloads_total) as total_downloads,
            SUM(downloads_30d) as total_downloads_30d,
            SUM(contributors) as total_contributors
        FROM repos_daily
        WHERE snapshot_date IN (
            (SELECT MAX(snapshot_date) FROM repos_daily),
            (SELECT MAX(snapshot_date) FROM repos_daily WHERE snapshot_date <= ?)
        )
        GROUP BY snapshot_date
        ORDER BY snapshot_date
    """, (past,))
    
    rows = cur.fetchall()
    if len(rows) < 2:
        return {"error": f"Not enough historical data for {days}-day trend. Need at least 2 snapshots."}
    
    old, new = rows[0], rows[1]
    
    return {
        "period_days": days,
        "from_date": old[0],
        "to_date": new[0],
        "stars": {"current": new[1], "previous": old[1], "change": new[1] - old[1]},
        "forks": {"current": new[2], "previous": old[2], "change": new[2] - old[2]},
        "open_issues": {"current": new[3], "previous": old[3], "change": new[3] - old[3]},
        "downloads_total": {"current": new[4], "previous": old[4], "change": (new[4] or 0) - (old[4] or 0)},
        "contributors": {"current": new[6], "previous": old[6], "change": (new[6] or 0) - (old[6] or 0)},
    }


def get_repo_trends(conn: sqlite3.Connection, repo: str, days: int) -> dict:
    """Calculate trends for a specific repo."""
    today = date.today().isoformat()
    past = (date.today() - timedelta(days=days)).isoformat()
    
    cur = conn.execute("""
        SELECT 
            snapshot_date, stars, forks, open_issues, 
            downloads_total, downloads_30d, contributors
        FROM repos_daily
        WHERE LOWER(repo) = LOWER(?) AND snapshot_date IN (
            (SELECT MAX(snapshot_date) FROM repos_daily WHERE LOWER(repo) = LOWER(?)),
            (SELECT MAX(snapshot_date) FROM repos_daily WHERE LOWER(repo) = LOWER(?) AND snapshot_date <= ?)
        )
        ORDER BY snapshot_date
    """, (repo, repo, repo, past))
    
    rows = cur.fetchall()
    if len(rows) < 1:
        return {"error": f"No data found for repo '{repo}'"}
    if len(rows) < 2:
        return {"error": f"Not enough historical data for {days}-day trend. Only 1 snapshot found."}
    
    old, new = rows[0], rows[1]
    
    return {
        "repo": repo,
        "period_days": days,
        "from_date": old[0],
        "to_date": new[0],
        "stars": {"current": new[1], "previous": old[1], "change": new[1] - old[1]},
        "forks": {"current": new[2], "previous": old[2], "change": new[2] - old[2]},
        "open_issues": {"current": new[3], "previous": old[3], "change": new[3] - old[3]},
        "downloads_total": {"current": new[4], "previous": old[4], "change": (new[4] or 0) - (old[4] or 0)},
        "contributors": {"current": new[6], "previous": old[6], "change": (new[6] or 0) - (old[6] or 0)},
    }


def get_daily_breakdown(conn: sqlite3.Connection, repo: Optional[str], days: int, metric: str) -> list:
    """Get daily breakdown for a metric."""
    past = (date.today() - timedelta(days=days)).isoformat()
    
    if repo:
        cur = conn.execute(f"""
            SELECT snapshot_date, {metric}
            FROM repos_daily
            WHERE LOWER(repo) = LOWER(?) AND snapshot_date >= ?
            ORDER BY snapshot_date
        """, (repo, past))
    else:
        cur = conn.execute(f"""
            SELECT snapshot_date, SUM({metric}) as total
            FROM repos_daily
            WHERE snapshot_date >= ?
            GROUP BY snapshot_date
            ORDER BY snapshot_date
        """, (past,))
    
    rows = cur.fetchall()
    result = []
    prev_value = None
    
    for row in rows:
        value = row[1] or 0
        change = value - prev_value if prev_value is not None else 0
        result.append({
            "date": row[0],
            "value": value,
            "change": change,
        })
        prev_value = value
    
    return result


def get_top_repos_by_growth(conn: sqlite3.Connection, days: int, metric: str, limit: int = 10) -> list:
    """Get top repos by growth in a metric."""
    past = (date.today() - timedelta(days=days)).isoformat()
    
    cur = conn.execute(f"""
        WITH latest AS (
            SELECT repo, {metric} as current_value
            FROM repos_daily
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM repos_daily)
        ),
        past AS (
            SELECT repo, {metric} as past_value
            FROM repos_daily
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM repos_daily WHERE snapshot_date <= ?)
        )
        SELECT l.repo, l.current_value, p.past_value, 
               COALESCE(l.current_value, 0) - COALESCE(p.past_value, 0) as growth
        FROM latest l
        LEFT JOIN past p ON l.repo = p.repo
        WHERE growth > 0
        ORDER BY growth DESC
        LIMIT ?
    """, (past, limit))
    
    return [{"repo": r[0], "current": r[1], "previous": r[2], "growth": r[3]} for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description="Query trend data")
    parser.add_argument("--repo", help="Specific repo (default: org-wide)")
    parser.add_argument("--days", type=int, default=30, help="Time window in days (default: 30)")
    parser.add_argument("--metric", choices=["stars", "forks", "open_issues", "downloads_total", "contributors"],
                        help="Specific metric for detailed view")
    parser.add_argument("--daily", action="store_true", help="Show daily breakdown")
    parser.add_argument("--top", type=int, help="Show top N repos by growth")
    args = parser.parse_args()
    
    check_db()
    conn = sqlite3.connect(DB_PATH)
    
    latest_date = get_latest_snapshot_date(conn)
    
    result = {
        "query_time": date.today().isoformat(),
        "latest_snapshot": latest_date,
        "database": str(DB_PATH),
    }
    
    if args.top and args.metric:
        result["top_repos_by_growth"] = get_top_repos_by_growth(conn, args.days, args.metric, args.top)
    elif args.daily and args.metric:
        result["daily_breakdown"] = get_daily_breakdown(conn, args.repo, args.days, args.metric)
    elif args.repo:
        result["trends"] = get_repo_trends(conn, args.repo, args.days)
    else:
        result["trends"] = {
            "7d": get_org_trends(conn, 7),
            "30d": get_org_trends(conn, 30),
        }
        if args.days not in [7, 30]:
            result["trends"][f"{args.days}d"] = get_org_trends(conn, args.days)
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
