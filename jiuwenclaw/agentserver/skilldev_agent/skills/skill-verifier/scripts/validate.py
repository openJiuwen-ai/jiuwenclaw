#!/usr/bin/env python3
"""Unified skill validator — single source of truth for spec + security checks.

Replaces skill-creator/quick_validate.py, skill-standardizer/validate.py,
and the inline validation in direct_import.py.

Enforces both character limits and token limits (dual-limit policy).
"""

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

ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}

DESCRIPTION_MAX_CHARS_CJK = 512
DESCRIPTION_MAX_CHARS_EN = 1024
DESCRIPTION_MAX_TOKENS = 300
BODY_MAX_LINES = 500
BODY_MAX_TOKENS = 5000


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // 4


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _find_duplicate_frontmatter_key(frontmatter_text: str) -> str | None:
    seen: set[str] = set()
    for line in frontmatter_text.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if not m:
            continue
        key = m.group(1)
        if key in seen:
            return key
        seen.add(key)
    return None


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Validate a skill directory against all spec + security rules.

    Collects ALL errors before returning so the caller can fix them in one pass.
    Returns (True, "Skill is valid!") or (False, "<bullet list of all errors>").
    """
    skill_path = Path(skill_path).resolve()
    errors: list[str] = []

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
    body = content[match.end():].lstrip("\n")

    dup = _find_duplicate_frontmatter_key(frontmatter_text)
    if dup:
        errors.append(f"Duplicate key in SKILL.md frontmatter: {dup}")

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    unexpected = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        errors.append(
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_FRONTMATTER_KEYS))}"
        )

    if "name" not in frontmatter:
        errors.append("Missing 'name' in frontmatter")
    if "description" not in frontmatter:
        errors.append("Missing 'description' in frontmatter")

    # --- name ---
    name = frontmatter.get("name", "")
    if isinstance(name, str):
        name = name.strip()
        if not name:
            errors.append("Name cannot be empty")
        else:
            if not re.match(r"^[a-z0-9-]+$", name):
                errors.append(
                    f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
                )
            if name.startswith("-") or name.endswith("-") or "--" in name:
                errors.append(
                    f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
                )
            if len(name) > 64:
                errors.append(f"Name is too long ({len(name)} characters). Maximum is 64 characters.")
            if name != skill_path.name:
                errors.append(f"Name '{name}' must match directory name '{skill_path.name}'")
    elif name is not None and "name" in frontmatter:
        errors.append(f"Name must be a string, got {type(name).__name__}")

    # --- description (char + token dual limit) ---
    description = frontmatter.get("description", "")
    if isinstance(description, str):
        description = description.strip()
        if not description:
            errors.append("Description cannot be empty")
        else:
            if "<" in description or ">" in description:
                errors.append("Description cannot contain angle brackets (< or >)")
            max_chars = DESCRIPTION_MAX_CHARS_CJK if _contains_cjk(description) else DESCRIPTION_MAX_CHARS_EN
            if len(description) > max_chars:
                errors.append(
                    f"Description is too long ({len(description)} characters). "
                    f"Maximum is {max_chars} characters."
                )
            desc_tokens = _estimate_tokens(description)
            if desc_tokens > DESCRIPTION_MAX_TOKENS:
                errors.append(
                    f"Description token count too high (~{desc_tokens} tokens). "
                    f"Maximum is {DESCRIPTION_MAX_TOKENS} tokens."
                )
    elif description is not None and "description" in frontmatter:
        errors.append(f"Description must be a string, got {type(description).__name__}")

    # --- body (line + token dual limit) ---
    if not body.strip():
        errors.append("SKILL.md body cannot be empty")
    else:
        body_lines = body.splitlines()
        if len(body_lines) > BODY_MAX_LINES:
            errors.append(
                f"SKILL.md body is too long ({len(body_lines)} lines). "
                f"Maximum is {BODY_MAX_LINES} lines."
            )
        body_tokens = _estimate_tokens(body)
        if body_tokens > BODY_MAX_TOKENS:
            errors.append(
                f"SKILL.md body token count too high (~{body_tokens} tokens). "
                f"Maximum is {BODY_MAX_TOKENS} tokens."
            )

    # --- static security ---
    sec_errors = _validate_static_security_all(skill_path, content)
    errors.extend(sec_errors)

    if errors:
        return False, "\n".join(f"- {e}" for e in errors)
    return True, "Skill is valid!"


def _validate_static_security_all(skill_path: Path, skill_content: str) -> list[str]:
    """Collect all static security errors instead of stopping at the first."""
    errors: list[str] = []
    credential_files: list[tuple[Path, str]] = [(skill_path / "SKILL.md", skill_content)]
    script_files: list[tuple[Path, str]] = []
    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for sp in scripts_dir.rglob("*"):
            if sp.is_file():
                try:
                    text = sp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                script_files.append((sp, text))
                credential_files.append((sp, text))

    for file_path, _ in credential_files:
        rel = file_path.relative_to(skill_path)
        if ".." in rel.parts:
            errors.append(f"Path traversal detected: {rel}")

    for file_path, text in script_files:
        rel = file_path.relative_to(skill_path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern, label in DANGEROUS_PATTERNS:
                if pattern.search(line):
                    errors.append(
                        f"Security check failed in {rel}:{line_no}: "
                        f"prohibited command pattern `{label}`"
                    )

    for file_path, text in credential_files:
        rel = file_path.relative_to(skill_path)
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                errors.append(f"Security check failed in {rel}: possible hardcoded credential")

    return errors


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
