#!/usr/bin/env python3
"""Encrypt a PDF with a user password."""
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


def encrypt(input_path: str, out: str, password: str) -> dict:
    if not os.path.exists(input_path):
        return {"status": "error", "error": f"File not found: {input_path}"}
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "wb") as f:
            writer.write(f)
        size_kb = os.path.getsize(out) // 1024
        return {
            "status": "ok",
            "out": out,
            "pages": len(reader.pages),
            "size_kb": size_kb,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Encrypt a PDF")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    validate_safe_path(args.input, allowed_extensions={".pdf"})
    result = encrypt(args.input, args.out, args.password)
    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))
    print(f"\n── Encrypt complete ────────────────────────────────────")
    print(f"  Output : {result['out']}")
    print(f"  Pages  : {result['pages']}")
    print(f"  Size   : {result['size_kb']} KB")
    print(f"────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
