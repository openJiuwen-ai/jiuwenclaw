#!/usr/bin/env python3
"""Validate an imported skill under <workspace>/skill/ for listing."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s+-[^\n]*r[^\n]*f\s+/"), "forced recursive root deletion"),
    (re.compile(r"\bchmod\s+777\b"), "world-writable permissions"),
    (re.compile(r"\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b"), "piped remote shell execution"),
    (re.compile(r"\beval\s*\("), "dynamic eval execution"),
]
CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Basic validation of a skill (aligned with skill-creator quick_validate)."""
    skill_path = Path(skill_path).resolve()

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)
    body = content[match.end() :].lstrip("\n")

    duplicate_key = find_duplicate_frontmatter_key(frontmatter_text)
    if duplicate_key:
        return False, f"Duplicate key in SKILL.md frontmatter: {duplicate_key}"

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    allowed_properties = {
        "name",
        "description",
        "license",
        "allowed-tools",
        "metadata",
        "compatibility",
    }
    unexpected_keys = set(frontmatter.keys()) - allowed_properties
    if unexpected_keys:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}. "
            f"Allowed properties are: {', '.join(sorted(allowed_properties))}"
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if not name:
        return False, "Name cannot be empty"
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, (
            f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
        )
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    if len(name) > 64:
        return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters."
    if name != skill_path.name:
        return False, f"Name '{name}' must match directory name '{skill_path.name}'"

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if not description:
        return False, "Description cannot be empty"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)"
    max_description_chars = 512 if contains_cjk(description) else 1024
    if len(description) > max_description_chars:
        return False, (
            f"Description is too long ({len(description)} characters). "
            f"Maximum is {max_description_chars} characters."
        )

    body_lines = body.splitlines()
    if not body.strip():
        return False, "SKILL.md body cannot be empty"
    if len(body_lines) > 500:
        return False, f"SKILL.md body is too long ({len(body_lines)} lines). Maximum is 500 lines."

    security_valid, security_message = validate_static_security(skill_path, content)
    if not security_valid:
        return False, security_message

    return True, "Skill is valid!"


def find_duplicate_frontmatter_key(frontmatter_text: str) -> str | None:
    """Return the first duplicate top-level YAML key, if any."""
    seen: set[str] = set()
    for line in frontmatter_text.splitlines():
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            return key
        seen.add(key)
    return None


def contains_cjk(text: str) -> bool:
    """Detect CJK characters for the stricter Chinese description limit."""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def validate_static_security(skill_path: Path, skill_content: str) -> tuple[bool, str]:
    """Run lightweight static security checks before packaging."""
    credential_files: list[tuple[Path, str]] = [(skill_path / "SKILL.md", skill_content)]
    script_files: list[tuple[Path, str]] = []
    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for script_path in scripts_dir.rglob("*"):
            if script_path.is_file():
                try:
                    text = script_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                script_files.append((script_path, text))
                credential_files.append((script_path, text))

    for file_path, _ in credential_files:
        rel_path = file_path.relative_to(skill_path)
        if ".." in rel_path.parts:
            return False, f"Path traversal detected: {rel_path}"

    for file_path, text in script_files:
        rel_path = file_path.relative_to(skill_path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern, label in DANGEROUS_PATTERNS:
                if pattern.search(line):
                    return False, (
                        f"Security check failed in {rel_path}:{line_number}: "
                        f"prohibited command pattern `{label}`"
                    )

    for file_path, text in credential_files:
        rel_path = file_path.relative_to(skill_path)
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                return False, f"Security check failed in {rel_path}: possible hardcoded credential"

    return True, "Static security checks passed"


def find_skill_root(skill_dir: Path) -> Path | None:
    """Locate the skill root directory containing SKILL.md under skill/."""
    if not skill_dir.is_dir():
        return None
    if (skill_dir / "SKILL.md").is_file():
        return skill_dir

    subdirs = [
        child
        for child in skill_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    if len(subdirs) == 1:
        return subdirs[0]
    if len(subdirs) > 1:
        names = ", ".join(sorted(d.name for d in subdirs))
        logger.warning("multiple skill roots under %s: %s", skill_dir, names)
        return None

    for skill_md in skill_dir.rglob("SKILL.md"):
        return skill_md.parent
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python3 -m scripts.validate <workspace>")
        return 2

    workspace = Path(argv[1]).resolve()
    skill_root = find_skill_root(workspace / "skill")
    if skill_root is None:
        print("Validation failed: cannot find skill root under <workspace>/skill/")
        return 1

    valid, message = validate_skill(skill_root)
    if not valid:
        print("Validation failed:")
        print(message)
        return 1

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
