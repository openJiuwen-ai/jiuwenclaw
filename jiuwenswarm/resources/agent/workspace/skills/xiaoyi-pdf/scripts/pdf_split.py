#!/usr/bin/env python3
"""Split a PDF into single pages or by page ranges."""
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


def parse_ranges(ranges_str: str, max_page: int) -> tuple[list[tuple[int, int]], str, list[str]]:
    ranges = []
    warnings = []
    for part in ranges_str.split(","):
        part = part.strip()
        try:
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                original_s = int(start_str)
                original_e = int(end_str)
            else:
                original_s = original_e = int(part)
        except ValueError:
            return [], f"Invalid range string: {ranges_str!r}", []

        s = max(1, original_s)
        e = min(original_e, max_page)

        if s > max_page or e < 1:
            warnings.append(f"Range {original_s}-{original_e} skipped (outside valid page bounds)")
            continue

        if original_s < 1 or original_e > max_page:
            warnings.append(f"Range {original_s}-{original_e} clamped to valid page bounds")

        ranges.append((s, e))
    return ranges, "", warnings


def split(input_path: str, outdir: str, ranges_str: str = "") -> dict:
    if not os.path.exists(input_path):
        return {"status": "error", "error": f"File not found: {input_path}"}
    try:
        reader = PdfReader(input_path)
    except Exception as e:
        return {"status": "error", "error": f"Failed to read PDF: {e}"}
    total_pages = len(reader.pages)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]
    files_created = 0
    warnings = []

    if ranges_str:
        ranges, error_msg, range_warnings = parse_ranges(ranges_str, total_pages)
        if error_msg:
            return {"status": "error", "error": error_msg}
        warnings.extend(range_warnings)
        for idx, (s, e) in enumerate(ranges, 1):
            writer = PdfWriter()
            for i in range(s - 1, e):
                writer.add_page(reader.pages[i])
            out_path = os.path.join(outdir, f"{base}_part{idx}_pages{s}-{e}.pdf")
            with open(out_path, "wb") as f:
                writer.write(f)
            files_created += 1
    else:
        for i in range(total_pages):
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            out_path = os.path.join(outdir, f"{base}_page{i+1}.pdf")
            with open(out_path, "wb") as f:
                writer.write(f)
            files_created += 1

    return {
        "status": "ok",
        "outdir": outdir,
        "total_pages": total_pages,
        "files_created": files_created,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Split a PDF")
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--ranges", default="", help="e.g. 1-3,5")
    args = parser.parse_args()
    validate_safe_path(args.input, allowed_extensions={".pdf"})
    result = split(args.input, args.outdir, args.ranges)
    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))
    print(f"\n── Split complete ──────────────────────────────────────")
    print(f"  Out dir      : {result['outdir']}")
    print(f"  Total pages  : {result['total_pages']}")
    print(f"  Files created: {result['files_created']}")
    print(f"────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
