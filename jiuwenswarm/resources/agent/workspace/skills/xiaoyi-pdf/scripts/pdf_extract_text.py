#!/usr/bin/env python3
"""Extract text from a PDF to a plain text file."""
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
from pypdf import PdfReader


def extract_text(input_path: str, out: str) -> dict:
    if not os.path.exists(input_path):
        return {"status": "error", "error": f"File not found: {input_path}"}
    try:
        reader = PdfReader(input_path)
        lines = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            lines.append(f"--- Page {i} ---")
            lines.append(text)
        full = "\n".join(lines)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(full)
        return {
            "status": "ok",
            "out": out,
            "pages": len(reader.pages),
            "chars": len(full),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Extract text from a PDF")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    validate_safe_path(args.input, allowed_extensions={".pdf"})
    result = extract_text(args.input, args.out)
    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))
    print(f"\n── Extract text complete ───────────────────────────────")
    print(f"  Output : {result['out']}")
    print(f"  Pages  : {result['pages']}")
    print(f"  Chars  : {result['chars']}")
    print(f"────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
