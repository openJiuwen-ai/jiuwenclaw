#!/usr/bin/env python3
"""Extract tables from a PDF to Excel or CSV."""
import argparse
import importlib.util
import json
import os
import sys

from security import validate_safe_path


def ensure_deps():
    missing = []
    for pkg in ("pdfplumber", "pandas", "openpyxl"):
        if importlib.util.find_spec(pkg) is None:
            missing.append(pkg)
    if missing:
        import subprocess

        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", "-q"] + missing
        )


import pdfplumber
import pandas as pd


def extract_tables(input_path: str, out: str, fmt: str = "xlsx") -> dict:
    if fmt not in ("csv", "xlsx"):
        raise ValueError(f"Unsupported format: {fmt}")
    if not os.path.exists(input_path):
        return {"status": "error", "error": f"File not found: {input_path}"}
    try:
        all_tables = []
        with pdfplumber.open(input_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        df = pd.DataFrame(table[1:], columns=table[0])
                        all_tables.append(df)
        if not all_tables:
            combined = pd.DataFrame()
        else:
            combined = pd.concat(all_tables, ignore_index=True)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        if fmt == "csv":
            combined.to_csv(out, index=False, encoding="utf-8-sig")
        else:
            combined.to_excel(out, index=False)
        return {
            "status": "ok",
            "out": out,
            "tables_found": len(all_tables),
            "rows": len(combined),
            "format": fmt,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    ensure_deps()
    parser = argparse.ArgumentParser(description="Extract tables from a PDF")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    args = parser.parse_args()
    validate_safe_path(args.input, allowed_extensions={".pdf"})
    result = extract_tables(args.input, args.out, args.format)
    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))
    print(f"\n── Extract tables complete ─────────────────────────────")
    print(f"  Output       : {result['out']}")
    print(f"  Tables found : {result['tables_found']}")
    print(f"  Total rows   : {result['rows']}")
    print(f"  Format       : {result['format']}")
    print(f"────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
