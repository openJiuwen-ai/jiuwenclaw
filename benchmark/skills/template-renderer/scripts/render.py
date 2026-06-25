#!/usr/bin/env python3
"""Render a text template by substituting {{variable}} placeholders.

Requires the TEMPLATE_DIR environment variable to point at the template root
directory. This requirement is intentionally undocumented in SKILL.md — running
without it produces a clear error.
"""

import argparse
import os
import re
import sys


def resolve_template(template_arg: str) -> str:
    """Resolve the template file against the TEMPLATE_DIR root, preventing
    path traversal outside the root."""
    template_dir = os.environ.get("TEMPLATE_DIR")
    if not template_dir:
        sys.stderr.write(
            "ERROR: TEMPLATE_DIR environment variable is not set. "
            "Export it before running, e.g.  export TEMPLATE_DIR=<templates dir>\n"
        )
        sys.exit(1)

    root = os.path.realpath(template_dir)
    # Allow both an absolute path and a bare filename relative to TEMPLATE_DIR.
    candidate = template_arg if os.path.isabs(template_arg) else os.path.join(root, template_arg)
    resolved = os.path.realpath(candidate)

    if not (resolved == root or resolved.startswith(root + os.sep)):
        sys.stderr.write(
            f"ERROR: template '{template_arg}' is outside TEMPLATE_DIR ({root})\n"
        )
        sys.exit(1)

    if not os.path.isfile(resolved):
        sys.stderr.write(f"ERROR: template file not found: {resolved}\n")
        sys.exit(1)

    return resolved


def render(text: str, variables: dict) -> str:
    def repl(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))

    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", repl, text)


def parse_var(spec: str):
    if "=" not in spec:
        sys.stderr.write(f"ERROR: invalid --var '{spec}', expected KEY=VAL\n")
        sys.exit(1)
    key, _, val = spec.partition("=")
    return key.strip(), val


def main():
    parser = argparse.ArgumentParser(
        description="Render a text template by substituting {{variable}} placeholders."
    )
    parser.add_argument("template_file", help="Template file (under TEMPLATE_DIR)")
    parser.add_argument(
        "--var", action="append", default=[], metavar="KEY=VAL",
        help="Variable assignment (repeatable)",
    )
    args = parser.parse_args()

    path = resolve_template(args.template_file)

    variables = {}
    for spec in args.var:
        key, val = parse_var(spec)
        variables[key] = val

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    sys.stdout.write(render(text, variables))


if __name__ == "__main__":
    main()
