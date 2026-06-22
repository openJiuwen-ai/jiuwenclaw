#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""CLI tool for querying SQLite trace database."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from jiuwenswarm.telemetry.sqlite_exporter import (
    query_spans,
    get_trace_tree,
    get_span_statistics,
)


def deep_decode_json_strings(data):
    """Recursively decode any JSON-encoded string values in the data structure.

    Handles double-encoding cases where attributes/events/links/resource
    are stored as JSON strings inside an already-JSON structure.
    Only parses strings that decode to dicts or lists; scalar strings are kept.
    """
    if isinstance(data, list):
        return [deep_decode_json_strings(item) for item in data]
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, (dict, list)):
                        result[key] = deep_decode_json_strings(parsed)
                    else:
                        # Scalar value (int, bool, null, plain string) — keep original
                        result[key] = value
                except (json.JSONDecodeError, ValueError):
                    result[key] = value
            elif isinstance(value, dict):
                result[key] = deep_decode_json_strings(value)
            elif isinstance(value, list):
                result[key] = deep_decode_json_strings(value)
            else:
                result[key] = value
        return result
    return data


def cmd_stats(args):
    """Show database statistics."""
    stats = get_span_statistics(args.db_path)

    print("=== Trace Database Statistics ===")
    print(f"Total spans: {stats['total_spans']}")
    print(f"Total traces: {stats['total_traces']}")

    if stats['time_range']['start']:
        print(f"Time range: {stats['time_range']['start']} to {stats['time_range']['end']}")

    print("\n--- Span Types ---")
    for item in stats['span_names']:
        avg_ms = item['avg_duration_ms'] or 0
        print(f"  {item['name']}: {item['count']} spans, avg {avg_ms:.2f}ms")


def cmd_traces(args):
    """List recent traces."""
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            trace_id,
            COUNT(*) as span_count,
            MIN(datetime(start_time_ns / 1e9, 'unixepoch')) as start_time,
            MAX(duration_ns) / 1e6 as max_duration_ms
        FROM spans
        GROUP BY trace_id
        ORDER BY start_time DESC
        LIMIT ?
    """, (args.limit,))

    rows = cursor.fetchall()
    conn.close()

    print(f"=== Recent Traces (limit {args.limit}) ===\n")

    for row in rows:
        trace_id_short = row['trace_id'][:16] + "..."
        duration = row['max_duration_ms'] or 0
        print(f"{trace_id_short}")
        print(f"  Spans: {row['span_count']}, Duration: {duration:.2f}ms")
        print(f"  Time: {row['start_time']}")
        print()


def cmd_trace(args):
    """Show single trace tree."""
    tree = get_trace_tree(args.db_path, args.trace_id)
    tree = deep_decode_json_strings(tree)

    trace_id_short = args.trace_id[:16] + "..."
    print(f"=== Trace Tree: {trace_id_short} ===\n")

    def print_node(node: dict, indent: str = ""):
        duration_ms = (node['duration_ns'] or 0) / 1e6

        # Status indicator
        status = "o"
        if node['status_code'] == 'ERROR':
            status = "X"
        elif node['status_code'] == 'OK':
            status = "v"

        print(f"{indent}{status} {node['name']} [{duration_ms:.2f}ms]")

        # Print attributes (top 3)
        if node['attributes']:
            attrs = node['attributes']
            for key in list(attrs.keys())[:3]:
                print(f"{indent}  - {key}: {attrs[key]}")

        # Print children
        for child in node['children']:
            print_node(child, indent + "  ")

    for root in tree:
        print_node(root)


def cmd_search(args):
    """Search spans by name."""
    spans = query_spans(args.db_path, span_name=args.name, limit=args.limit)

    print(f"=== Search Results: '{args.name}' (limit {args.limit}) ===\n")

    for span in spans:
        trace_id_short = span['trace_id'][:16] + "..."
        duration_ms = (span['duration_ns'] or 0) / 1e6

        print(f"{span['name']} [{duration_ms:.2f}ms]")
        print(f"  Trace: {trace_id_short}")
        print(f"  Time: {span['created_at']}")
        print()


def cmd_slow(args):
    """Show slowest spans."""
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            name,
            duration_ns / 1e6 as duration_ms,
            trace_id,
            created_at
        FROM spans
        WHERE duration_ns IS NOT NULL
        ORDER BY duration_ns DESC
        LIMIT ?
    """, (args.limit,))

    rows = cursor.fetchall()
    conn.close()

    print(f"=== Slowest Spans (limit {args.limit}) ===\n")

    for i, row in enumerate(rows, 1):
        trace_id_short = row['trace_id'][:16] + "..."
        print(f"{i}. {row['name']} [{row['duration_ms']:.2f}ms]")
        print(f"   Trace: {trace_id_short} at {row['created_at']}")
        print()


def cmd_export(args):
    """Export to JSON."""
    data = []

    if args.type == "trace":
        tree = get_trace_tree(args.db_path, args.id)
        data = tree
    else:  # all
        spans = query_spans(args.db_path, limit=args.limit)
        data = spans

    # Recursively decode any nested JSON strings (handles double-encoding)
    data = deep_decode_json_strings(data)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Exported {len(data)} items to {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Query SQLite trace database")
    parser.add_argument("--db", dest="db_path", default="traces.db", help="Database path")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # stats
    subparsers.add_parser("stats", help="Show database statistics")

    # traces
    traces_parser = subparsers.add_parser("traces", help="List recent traces")
    traces_parser.add_argument("--limit", type=int, default=10, help="Number of traces")

    # trace
    trace_parser = subparsers.add_parser("trace", help="Show trace tree")
    trace_parser.add_argument("trace_id", help="Trace ID")

    # search
    search_parser = subparsers.add_parser("search", help="Search spans")
    search_parser.add_argument("name", help="Span name pattern")
    search_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # slow
    slow_parser = subparsers.add_parser("slow", help="Show slowest spans")
    slow_parser.add_argument("--limit", type=int, default=10, help="Number of spans")

    # export
    export_parser = subparsers.add_parser("export", help="Export to JSON")
    export_parser.add_argument("type", choices=["trace", "all"], help="Export type")
    export_parser.add_argument("id", nargs="?", help="Trace ID (for trace export)")
    export_parser.add_argument("-o", "--output", required=True, help="Output file")
    export_parser.add_argument("--limit", type=int, default=1000, help="Limit (for all)")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "traces":
        cmd_traces(args)
    elif args.command == "trace":
        cmd_trace(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "slow":
        cmd_slow(args)
    elif args.command == "export":
        cmd_export(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
