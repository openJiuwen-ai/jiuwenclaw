#!/usr/bin/env python3
"""Merge multiple PDFs into one."""
import argparse
import importlib.util
import json
import os
import sys

from security import validate_safe_path


def ensure_deps():
    if importlib.util.find_spec("pypdf") is None:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", "-q", "pypdf"]
        )


ensure_deps()
from pypdf import PdfReader, PdfWriter


def merge(inputs: list[str], out: str) -> dict:
    """Merge multiple PDFs into one.

    Args:
        inputs: List of input PDF file paths.
        out: Output path for the merged PDF.

    Returns:
        A dict with keys: status ("ok" or "error"), out, total_pages,
        input_count, size_kb. On error, includes an "error" message.
    """
    if not inputs:
        return {"status": "error", "error": "No input files provided"}
    writer = PdfWriter()
    total_pages = 0
    for path in inputs:
        if not os.path.exists(path):
            return {"status": "error", "error": f"File not found: {path}"}
        try:
            reader = PdfReader(path)
        except Exception as e:
            return {"status": "error", "error": f"Failed to read PDF {path}: {e}"}
        for page in reader.pages:
            writer.add_page(page)
        total_pages += len(reader.pages)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    size_kb = os.path.getsize(out) // 1024
    return {
        "status": "ok",
        "out": out,
        "total_pages": total_pages,
        "input_count": len(inputs),
        "size_kb": size_kb,
    }


def main():
    parser = argparse.ArgumentParser(description="Merge multiple PDFs")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input PDF files")
    parser.add_argument("--out", required=True, help="Output PDF path")
    args = parser.parse_args()
    for p in args.inputs:
        validate_safe_path(p, allowed_extensions={".pdf"})
    result = merge(args.inputs, args.out)
    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))
    print(f"\n── Merge complete ──────────────────────────────────────")
    print(f"  Output      : {result['out']}")
    print(f"  Total pages : {result['total_pages']}")
    print(f"  Inputs      : {result['input_count']}")
    print(f"  Size        : {result['size_kb']} KB")
    print(f"────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
