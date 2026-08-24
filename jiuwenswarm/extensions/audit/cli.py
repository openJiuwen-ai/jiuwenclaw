# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计 CLI — 通过 python -m 方式运行，提供查询、导出、清理命令.

用法::

    python -m jiuwenswarm.extensions.audit.cli status
    python -m jiuwenswarm.extensions.audit.cli query --type chat_error --hours 24
    python -m jiuwenswarm.extensions.audit.cli errors --hours 12
    python -m jiuwenswarm.extensions.audit.cli tokens --hours 24
    python -m jiuwenswarm.extensions.audit.cli alerts --active
    python -m jiuwenswarm.extensions.audit.cli alerts --history --hours 48
    python -m jiuwenswarm.extensions.audit.cli export --format json --output /tmp/audit.json
    python -m jiuwenswarm.extensions.audit.cli cleanup --days 30
    python -m jiuwenswarm.extensions.audit.cli session-summary --session sess_xxx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_audit_config
from .log_store import LogStore

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="jiuwenswarm-audit",
        description="JiuwenSwarm Execution Audit & Alerting CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # status
    subparsers.add_parser("status", help="Show audit system status overview")

    # report
    sub_report = subparsers.add_parser("report", help="Show an operational audit report")
    sub_report.add_argument("--hours", type=int, default=24, help="Look back N hours")

    # timeline
    sub_timeline = subparsers.add_parser(
        "timeline",
        help="Show time-bucketed event, error and latency statistics",
    )
    sub_timeline.add_argument("--hours", type=int, default=24, help="Look back N hours")
    sub_timeline.add_argument(
        "--bucket-minutes",
        type=int,
        default=60,
        help="Bucket width in minutes (default: 60)",
    )

    # query
    sub_query = subparsers.add_parser("query", help="Query audit events")
    sub_query.add_argument("--type", dest="event_type", default=None, help="Filter by event type (e.g. chat_error)")
    sub_query.add_argument("--session", dest="session_id", default=None, help="Filter by session ID")
    sub_query.add_argument("--channel", dest="channel_id", default=None, help="Filter by channel ID")
    sub_query.add_argument("--request", dest="request_id", default=None, help="Filter by request ID")
    sub_query.add_argument("--agent", dest="agent_name", default=None, help="Filter by agent name")
    sub_query.add_argument("--error-type", default=None, help="Filter by error type")
    sub_query.add_argument("--only-errors", action="store_true", help="Only return failed events")
    sub_query.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")
    sub_query.add_argument("--limit", type=int, default=None, help="Max results (default: config query_limit)")
    sub_query.add_argument("--offset", type=int, default=0, help="Skip the first N results")
    sub_query.add_argument("--json", action="store_true", help="Print a JSON array")

    # errors
    sub_errors = subparsers.add_parser("errors", help="Show error statistics summary")
    sub_errors.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")

    # tokens
    sub_tokens = subparsers.add_parser("tokens", help="Show token usage statistics")
    sub_tokens.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")

    # alerts
    sub_alerts = subparsers.add_parser("alerts", help="Show alerts")
    sub_alerts.add_argument("--active", action="store_true", help="Show only active (unresolved) alerts")
    sub_alerts.add_argument("--history", action="store_true", help="Show alert history")
    sub_alerts.add_argument("--hours", type=int, default=48, help="Look back N hours (default: 48)")
    sub_alerts.add_argument("--severity", default=None, help="Filter by severity (info/warning/critical)")
    sub_alerts.add_argument("--rule", dest="rule_name", default=None, help="Filter by rule name")

    # alert lifecycle
    sub_resolve = subparsers.add_parser("resolve-alert", help="Mark an alert as resolved")
    sub_resolve.add_argument("alert_id", help="Alert ID")
    sub_suppress = subparsers.add_parser("suppress-alert", help="Mark an alert as suppressed")
    sub_suppress.add_argument("alert_id", help="Alert ID")

    # export
    sub_export = subparsers.add_parser("export", help="Export audit data to file")
    sub_export.add_argument("--format", choices=["csv", "json", "jsonl"], default="jsonl", help="Export format")
    sub_export.add_argument("--output", required=True, help="Output file path")
    sub_export.add_argument("--hours", type=int, default=24, help="Export last N hours (default: 24)")
    sub_export.add_argument("--type", dest="event_type", default=None, help="Filter by event type")

    # cleanup
    sub_cleanup = subparsers.add_parser("cleanup", help="Remove old audit data")
    sub_cleanup.add_argument("--days", type=int, default=30, help="Retention period in days (default: 30)")

    # session-summary
    sub_session = subparsers.add_parser("session-summary", help="Show audit summary for a session")
    sub_session.add_argument("--session", dest="session_id", required=True, help="Session ID")

    # sessions
    sub_sessions = subparsers.add_parser("sessions", help="List recent audited sessions")
    sub_sessions.add_argument("--hours", type=int, default=24, help="Look back N hours")
    sub_sessions.add_argument("--limit", type=int, default=100, help="Max sessions")
    sub_sessions.add_argument("--offset", type=int, default=0, help="Skip N sessions")

    return parser


async def _run_command(command: str, args: argparse.Namespace) -> int:
    """执行 CLI 命令."""
    config = load_audit_config()
    audit_dir = config.resolve_audit_dir()

    if command == "status":
        return await _cmd_status(audit_dir)

    if command == "report":
        return await _cmd_report(audit_dir, args)

    if command == "timeline":
        return await _cmd_timeline(audit_dir, args)

    if command == "query":
        if args.limit is None:
            args.limit = config.query_limit
        return await _cmd_query(audit_dir, args)

    if command == "errors":
        return await _cmd_errors(audit_dir, args)

    if command == "tokens":
        return await _cmd_tokens(audit_dir, args)

    if command == "alerts":
        return await _cmd_alerts(audit_dir, args)

    if command in {"resolve-alert", "suppress-alert"}:
        return await _cmd_alert_state(audit_dir, args.alert_id, command)

    if command == "export":
        return await _cmd_export(audit_dir, args)

    if command == "cleanup":
        return await _cmd_cleanup(audit_dir, args)

    if command == "session-summary":
        return await _cmd_session_summary(audit_dir, args)

    if command == "sessions":
        return await _cmd_sessions(audit_dir, args)

    print(f"Unknown command: {command}")
    return 1


async def _cmd_status(audit_dir: Path) -> int:
    """审计系统状态概览."""
    async with LogStore(audit_dir) as store:
        status = await store.get_status()

    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


async def _cmd_report(audit_dir: Path, args: argparse.Namespace) -> int:
    async with LogStore(audit_dir) as store:
        report = await store.get_overview(args.hours)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


async def _cmd_timeline(audit_dir: Path, args: argparse.Namespace) -> int:
    async with LogStore(audit_dir) as store:
        timeline = await store.get_event_timeseries(
            hours=args.hours,
            bucket_minutes=args.bucket_minutes,
        )
    print(json.dumps(timeline, indent=2, ensure_ascii=False))
    return 0


async def _cmd_query(audit_dir: Path, args: argparse.Namespace) -> int:
    """查询审计事件."""
    filters: dict[str, Any] = {
        "hours": args.hours,
        "limit": args.limit,
        "offset": args.offset,
    }
    if args.event_type:
        filters["event_type"] = args.event_type
    if args.session_id:
        filters["session_id"] = args.session_id
    if args.channel_id:
        filters["channel_id"] = args.channel_id
    if args.request_id:
        filters["request_id"] = args.request_id
    if args.agent_name:
        filters["agent_name"] = args.agent_name
    if args.error_type:
        filters["error_type"] = args.error_type
    if args.only_errors:
        filters["has_error"] = True

    async with LogStore(audit_dir) as store:
        events = await store.query_events(filters)

    if args.json:
        print(json.dumps([event.to_dict() for event in events], indent=2, ensure_ascii=False))
        return 0

    for event in events:
        d = event.to_dict()
        ts = d.get("timestamp", 0)
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts_str}] {d['event_type']} session={d.get('session_id') or '-'} "
              f"channel={d.get('channel_id') or '-'} "
              f"error={d.get('error_type') or '-'}")

    print(f"\nTotal: {len(events)} events")
    return 0


async def _cmd_errors(audit_dir: Path, args: argparse.Namespace) -> int:
    """错误统计摘要."""
    async with LogStore(audit_dir) as store:
        summary = await store.get_error_summary(args.hours)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


async def _cmd_tokens(audit_dir: Path, args: argparse.Namespace) -> int:
    """Token 消耗统计."""
    async with LogStore(audit_dir) as store:
        summary = await store.get_token_usage_summary(args.hours)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


async def _cmd_alerts(audit_dir: Path, args: argparse.Namespace) -> int:
    """查看告警."""
    filters: dict[str, Any] = {"hours": args.hours, "limit": 500}
    if args.active:
        filters["status"] = "active"
    if args.severity:
        filters["severity"] = args.severity
    if args.rule_name:
        filters["rule_name"] = args.rule_name

    async with LogStore(audit_dir) as store:
        alerts = await store.query_alerts(filters)

    for alert in alerts:
        d = alert.to_dict()
        print(f"[{d['severity']}] {d['rule_name']}: {d['message']} "
              f"(status={d['status']}, id={d['alert_id'][:16]}...)")

    print(f"\nTotal: {len(alerts)} alerts")
    return 0


async def _cmd_alert_state(audit_dir: Path, alert_id: str, command: str) -> int:
    async with LogStore(audit_dir) as store:
        if command == "resolve-alert":
            changed = await store.resolve_alert(alert_id)
            state = "resolved"
        else:
            changed = await store.suppress_alert(alert_id)
            state = "suppressed"
    if not changed:
        print(f"Alert not found: {alert_id}", file=sys.stderr)
        return 1
    print(f"Alert {alert_id} marked as {state}")
    return 0


async def _cmd_export(audit_dir: Path, args: argparse.Namespace) -> int:
    """导出审计数据."""
    output_path = Path(args.output)
    filters: dict[str, Any] = {"hours": args.hours, "limit": 10000}
    if args.event_type:
        filters["event_type"] = args.event_type

    async with LogStore(audit_dir) as store:
        if args.format == "jsonl":
            count = await store.export_to_jsonl(output_path, filters)
        elif args.format == "csv":
            count = await store.export_to_csv(output_path, filters)
        else:
            # JSON 格式：导出为 JSON 数组
            events = await store.query_events(filters)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as file:
                json.dump([e.to_dict() for e in events], file, indent=2, ensure_ascii=False)
            count = len(events)

    print(f"Exported {count} events to {output_path}")
    return 0


async def _cmd_cleanup(audit_dir: Path, args: argparse.Namespace) -> int:
    """清理过期审计数据."""
    async with LogStore(audit_dir) as store:
        deleted = await store.cleanup_old_events(args.days)

    print(f"Cleaned up {deleted} records older than {args.days} days")
    return 0


async def _cmd_session_summary(audit_dir: Path, args: argparse.Namespace) -> int:
    """会话审计摘要."""
    async with LogStore(audit_dir) as store:
        summary = await store.get_session_summary(args.session_id)

    if summary is None:
        print(f"No audit data found for session: {args.session_id}")
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


async def _cmd_sessions(audit_dir: Path, args: argparse.Namespace) -> int:
    async with LogStore(audit_dir) as store:
        sessions = await store.list_sessions(args.hours, args.limit, args.offset)
    print(json.dumps(sessions, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    """CLI 入口函数."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        exit_code = asyncio.run(_run_command(args.command, args))
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as exc:
        logger.error("Audit CLI error: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
