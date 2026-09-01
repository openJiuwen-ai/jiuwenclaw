# 基于 MiniMax-AI/skills/minimax-pdf 修改，原项目协议：MIT License
# 原版权：Copyright (c) 2025 MiniMax AI

#!/usr/bin/env python3
"""
render_cover.py — Render cover.html → cover.pdf via Playwright (Python).

Usage:
    python3 render_cover.py --input cover.html --out cover.pdf
    python3 render_cover.py --input cover.html --out cover.pdf --wait 1200

Exit codes: 0 success, 1 bad args, 2 dependency missing, 3 render error
"""

import argparse
import json
import os
import sys
from pathlib import Path


def usage():
    print("Usage: python3 render_cover.py --input <file.html> --out <file.pdf> [--wait <ms>]", file=sys.stderr)
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description="Render cover HTML to PDF via Playwright")
    parser.add_argument("--input", required=True, help="Input HTML file path")
    parser.add_argument("--out", required=True, help="Output PDF file path")
    parser.add_argument("--wait", type=int, default=800, help="Wait time in ms for CSS/JS to settle")
    return parser.parse_args()


def resolve_chrome_path() -> str:
    """Resolve Chrome executable path from env var or platform default."""
    env_path = os.environ.get("CHROME_PATH", "").strip()
    if env_path:
        return env_path

    if sys.platform == "win32":
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    return "/opt/chrome-linux/chrome"


def render_cover(input_file: str, output_file: str, wait_ms: int) -> dict:
    """Render HTML cover to PDF using Playwright."""
    input_path = Path(input_file)
    output_path = Path(output_file)

    # Validate input file exists
    if not input_path.exists():
        return {
            "status": "error",
            "error": f"File not found: {input_file}"
        }

    # Resolve Chrome path
    chrome_path = resolve_chrome_path()
    if not Path(chrome_path).exists():
        return {
            "status": "error",
            "error": f"Chrome not found at: {chrome_path}",
            "hint": "Install Google Chrome or set CHROME_PATH environment variable"
        }

    # Import playwright (may fail if not installed)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "status": "error",
            "error": "playwright not found",
            "hint": "Run: pip install playwright"
        }

    # Render using Playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome_path)
            page = browser.new_page()
            file_url = "file://" + str(input_path.resolve())
            page.goto(file_url)
            page.wait_for_timeout(wait_ms)

            page.pdf(
                path=str(output_path),
                width="794px",
                height="1123px",
                print_background=True
            )
            browser.close()
    except Exception as e:
        return {
            "status": "error",
            "error": f"Failed to render: {e}"
        }

    # Validate output
    stat = output_path.stat()
    if stat.st_size < 5000:
        return {
            "status": "error",
            "error": "Output PDF is suspiciously small — cover may be blank",
            "hint": "Check cover.html for render errors"
        }

    return {
        "status": "ok",
        "out": str(output_file),
        "size_kb": round(stat.st_size / 1024)
    }


def main():
    args = parse_args()
    result = render_cover(args.input, args.out, args.wait)

    # Print result as JSON
    print(json.dumps(result))

    # Exit with appropriate code
    if result["status"] == "ok":
        sys.exit(0)
    else:
        sys.exit(2 if "not found" in result.get("error", "").lower() else 3)


if __name__ == "__main__":
    main()
