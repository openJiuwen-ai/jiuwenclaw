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
    sub_status = subparsers.add_parser("status", help="Show audit system status overview")

    # query
    sub_query = subparsers.add_parser("query", help="Query audit events")
    sub_query.add_argument("--type", dest="event_type", default=None, help="Filter by event type (e.g. chat_error)")
    sub_query.add_argument("--session", dest="session_id", default=None, help="Filter by session ID")
    sub_query.add_argument("--channel", dest="channel_id", default=None, help="Filter by channel ID")
    sub_query.add_argument("--hours", type=int, default=24, help="Look back N hours (default: 24)")
    sub_query.add_argument("--limit", type=int, default=500, help="Max results (default: 500)")

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

    # export
    sub_export = subparsers.add_parser("export", help="Export audit data to file")
    sub_export.add_argument("--format", choices=["json", "jsonl"], default="jsonl", help="Export format")
    sub_export.add_argument("--output", required=True, help="Output file path")
    sub_export.add_argument("--hours", type=int, default=24, help="Export last N hours (default: 24)")
    sub_export.add_argument("--type", dest="event_type", default=None, help="Filter by event type")

    # cleanup
    sub_cleanup = subparsers.add_parser("cleanup", help="Remove old audit data")
    sub_cleanup.add_argument("--days", type=int, default=30, help="Retention period in days (default: 30)")

    # session-summary
    sub_session = subparsers.add_parser("session-summary", help="Show audit summary for a session")
    sub_session.add_argument("--session", dest="session_id", required=True, help="Session ID")

    return parser


async def _run_command(command: str, args: argparse.Namespace) -> int:
    """执行 CLI 命令."""
    config = load_audit_config()
    audit_dir = config.resolve_audit_dir()

    if command == "status":
        return await _cmd_status(audit_dir)

    if command == "query":
        return await _cmd_query(audit_dir, args)

    if command == "errors":
        return await _cmd_errors(audit_dir, args)

    if command == "tokens":
        return await _cmd_tokens(audit_dir, args)

    if command == "alerts":
        return await _cmd_alerts(audit_dir, args)

    if command == "export":
        return await _cmd_export(audit_dir, args)

    if command == "cleanup":
        return await _cmd_cleanup(audit_dir, config, args)

    if command == "session-summary":
        return await _cmd_session_summary(audit_dir, args)

    print(f"Unknown command: {command}")
    return 1


async def _cmd_status(audit_dir: Path) -> int:
    """审计系统状态概览."""
    store = LogStore(audit_dir)
    await store.initialize()
    status = await store.get_status()
    await store.close()

    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


async def _cmd_query(audit_dir: Path, args: argparse.Namespace) -> int:
    """查询审计事件."""
    store = LogStore(audit_dir)
    await store.initialize()

    filters: dict[str, Any] = {"hours": args.hours, "limit": args.limit}
    if args.event_type:
        filters["event_type"] = args.event_type
    if args.session_id:
        filters["session_id"] = args.session_id
    if args.channel_id:
        filters["channel_id"] = args.channel_id

    events = await store.query_events(filters)
    await store.close()

    for event in events:
        d = event.to_dict()
        ts = d.get("timestamp", 0)
        from datetime import datetime, timezone
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts_str}] {d['event_type']} session={d.get('session_id') or '-'} "
              f"channel={d.get('channel_id') or '-'} "
              f"error={d.get('error_type') or '-'}")

    print(f"\nTotal: {len(events)} events")
    return 0


async def _cmd_errors(audit_dir: Path, args: argparse.Namespace) -> int:
    """错误统计摘要."""
    store = LogStore(audit_dir)
    await store.initialize()
    summary = await store.get_error_summary(args.hours)
    await store.close()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


async def _cmd_tokens(audit_dir: Path, args: argparse.Namespace) -> int:
    """Token 消耗统计."""
    store = LogStore(audit_dir)
    await store.initialize()
    summary = await store.get_token_usage_summary(args.hours)
    await store.close()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


async def _cmd_alerts(audit_dir: Path, args: argparse.Namespace) -> int:
    """查看告警."""
    store = LogStore(audit_dir)
    await store.initialize()

    filters: dict[str, Any] = {"hours": args.hours, "limit": 500}
    if args.active:
        filters["status"] = "active"
    if args.severity:
        filters["severity"] = args.severity

    alerts = await store.query_alerts(filters)
    await store.close()

    for alert in alerts:
        d = alert.to_dict()
        print(f"[{d['severity']}] {d['rule_name']}: {d['message']} "
              f"(status={d['status']}, id={d['alert_id'][:16]}...)")

    print(f"\nTotal: {len(alerts)} alerts")
    return 0


async def _cmd_export(audit_dir: Path, args: argparse.Namespace) -> int:
    """导出审计数据."""
    store = LogStore(audit_dir)
    await store.initialize()

    output_path = Path(args.output)
    filters: dict[str, Any] = {"hours": args.hours, "limit": 10000}
    if args.event_type:
        filters["event_type"] = args.event_type

    if args.format == "jsonl":
        count = await store.export_to_jsonl(output_path, filters)
    else:
        # JSON 格式：导出为 JSON 数组
        events = await store.query_events(filters)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in events], f, indent=2, ensure_ascii=False)
        count = len(events)

    await store.close()

    print(f"Exported {count} events to {output_path}")
    return 0


async def _cmd_cleanup(audit_dir: Path, config: Any, args: argparse.Namespace) -> int:
    """清理过期审计数据."""
    store = LogStore(audit_dir)
    await store.initialize()
    deleted = await store.cleanup_old_events(args.days)
    await store.close()

    print(f"Cleaned up {deleted} records older than {args.days} days")
    return 0


async def _cmd_session_summary(audit_dir: Path, args: argparse.Namespace) -> int:
    """会话审计摘要."""
    store = LogStore(audit_dir)
    await store.initialize()
    summary = await store.get_session_summary(args.session_id)
    await store.close()

    if summary is None:
        print(f"No audit data found for session: {args.session_id}")
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=False))
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
