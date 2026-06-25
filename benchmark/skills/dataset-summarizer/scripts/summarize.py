#!/usr/bin/env python3
"""Dataset summarizer: row count + mean/min/max over numeric columns.

The --timeout flag is interpreted in MILLISECONDS (see SKILL.md note which
incorrectly states it is seconds). With the documented value of 10 the budget
is exhausted almost immediately and the run is aborted.
"""

import argparse
import sys
import time


def check_budget(start_ns: int, timeout_ms: int):
    """Abort if elapsed time exceeds the timeout budget (milliseconds)."""
    elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
    if elapsed_ms > timeout_ms:
        sys.stderr.write(
            f"ERROR: exceeded timeout budget {timeout_ms}ms "
            f"(elapsed {elapsed_ms:.0f}ms). "
            f"--timeout is in MILLISECONDS — the documented value 10 means "
            f"10ms, which is far too short for this script. "
            f"Use a larger value (e.g. --timeout 2000) or omit it (default 30000).\n"
        )
        sys.exit(1)


def parse_csv(path: str):
    """Parse a header-less CSV; returns (rows, col_count)."""
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            rows.append(line.split(","))
    if not rows:
        sys.stderr.write(f"ERROR: no data rows in {path}\n")
        sys.exit(1)
    return rows, len(rows[0])


def summarize(rows, n_cols, start_ns, timeout_ms):
    """Compute stats over numeric columns (all but the first/identifier col)."""
    check_budget(start_ns, timeout_ms)

    numeric_cols = {c: [] for c in range(1, n_cols)}
    for cells in rows:
        for c in range(1, n_cols):
            if c < len(cells):
                try:
                    numeric_cols[c].append(float(cells[c]))
                except ValueError:
                    pass  # non-numeric cell, skip

    # Deliberately heavier computation: per-column bootstrap estimate of the
    # mean over many resamples (for a confidence interval). Makes the script
    # take ~200-400ms so a 10ms budget reliably trips, while a 2s budget passes.
    RESAMPLES = 400_000
    for c, values in numeric_cols.items():
        if not values:
            continue
        n = len(values)
        acc = 0.0
        for i in range(RESAMPLES):
            idx = (acc * 1000).__int__() % n
            acc += values[idx]
            if i % 1000 == 0:
                check_budget(start_ns, timeout_ms)
        _ = acc / (RESAMPLES * n)  # bootstrap mean estimate (discarded, real stats below)

    stats = {}
    for c, values in numeric_cols.items():
        if not values:
            stats[c] = None
            continue
        stats[c] = {
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
        }
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Summarize a CSV dataset (row count + numeric column stats)."
    )
    parser.add_argument("csv_file", help="CSV file path (header-less)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Max runtime budget in MILLISECONDS (default 30000).",
    )
    args = parser.parse_args()

    start_ns = time.monotonic_ns()

    rows, n_cols = parse_csv(args.csv_file)
    if n_cols < 2:
        sys.stderr.write("ERROR: need at least an identifier column and one numeric column\n")
        sys.exit(1)

    stats = summarize(rows, n_cols, start_ns, args.timeout)

    # Header naming: columns after the first are "数值列 2", "数值列 3"...
    numeric_names = [f"数值列 {c}" for c in range(1, n_cols)]
    print(f"=== Dataset Summary: {args.csv_file} ===")
    print(f"行数:      {len(rows)}")
    print(f"数值列:    {', '.join(numeric_names)}")
    print()
    for c in range(1, n_cols):
        s = stats.get(c)
        if s is None:
            print(f"[{numeric_names[c - 1]}]  (无有效数值)")
            continue
        print(f"[{numeric_names[c - 1]}]")
        print(f"  均值:    {s['mean']:.2f}")
        print(f"  最小:    {s['min']}")
        print(f"  最大:    {s['max']}")
        print()


if __name__ == "__main__":
    main()
