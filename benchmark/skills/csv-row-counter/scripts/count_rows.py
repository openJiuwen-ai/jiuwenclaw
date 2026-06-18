#!/usr/bin/env python3
"""Count data rows in a CSV file. Skips header row and empty lines."""

import csv
import sys
import os


def detect_encoding(filepath):
    """Detect file encoding: UTF-8 or GBK."""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                f.read(4096)
            return enc
        except (UnicodeDecodeError, ValueError):
            continue
    return "utf-8"


def count_rows(filepath, delimiter=",", filter_expr=None):
    """Count data rows in CSV, always skipping the header row."""
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    encoding = detect_encoding(filepath)

    with open(filepath, "r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)

        # BUG: Always skip first row as header, even when file has no header
        header = next(reader, None)
        if header is None:
            print("文件为空")
            return

        data_rows = []
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # skip empty rows
            data_rows.append(row)

        # Apply filter if specified
        if filter_expr and "=" in filter_expr:
            col_name, value = filter_expr.split("=", 1)
            if col_name in header:
                col_idx = header.index(col_name)
                data_rows = [r for r in data_rows if len(r) > col_idx and r[col_idx] == value]

        print(f"文件: {os.path.basename(filepath)}")
        print(f"表头: {','.join(header)}")
        print(f"数据行数: {len(data_rows)}")


def main():
    if len(sys.argv) < 2:
        print("Usage: count_rows.py <csv_file> [--delimiter <delim>] [--filter <col>=<val>]")
        sys.exit(1)

    filepath = sys.argv[1]
    delimiter = ","
    filter_expr = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--delimiter" and i + 1 < len(sys.argv):
            delimiter = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--filter" and i + 1 < len(sys.argv):
            filter_expr = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    count_rows(filepath, delimiter, filter_expr)


if __name__ == "__main__":
    main()
