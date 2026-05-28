#!/usr/bin/env python3
"""
Quick validation script for imported skills (skill-standardizer).

This file is moved from skill-creator and adapted to the directImport listing rules.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    # Rough heuristic: ~4 chars per token
    if not text:
        return 0
    return len(text) // 4


SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DESCRIPTION_MAX_TOKENS = 300
BODY_MAX_TOKENS = 5000
BODY_MAX_LINES = 500
DESCRIPTION_MAX_CHARS_CJK = 256
DESCRIPTION_MAX_CHARS_EN = 512

DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-[^\n]*r[^\n]*f\s+/"), "forced recursive root deletion"),
    (re.compile(r"\bchmod\s+777\b"), "world-writable permissions"),
    (re.compile(r"\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b"), "piped remote shell execution"),
    (re.compile(r"\bwget\b[^\n|]*\|\s*(?:sh|bash)\b"), "piped remote shell execution"),
    (re.compile(r"\beval\b"), "dynamic eval execution"),
]

CREDENTIAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

SEMANTIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE), "prompt injection: ignore previous instructions"),
    (re.compile(r"(覆盖|忽略).{0,10}(系统|system).{0,10}(指令|prompt)", re.IGNORECASE), "prompt injection: override system prompt"),
    (re.compile(r"(你现在是|你必须).{0,30}(系统|system)", re.IGNORECASE), "prompt injection: role override"),
]


def find_duplicate_frontmatter_key(frontmatter_text: str) -> str | None:
    """Return the first duplicate top-level YAML key, if any."""
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


def contains_cjk(text: str) -> bool:
    """Detect CJK characters for the stricter Chinese description limit."""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _extract_permissions(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw = value.strip().strip("[]")
        parts = [p.strip().strip("'\"") for p in re.split(r"[,\n]", raw) if p.strip()]
        return {p for p in parts if p}
    if isinstance(value, list):
        return {str(v).strip() for v in value if str(v).strip()}
    return set()


def _extract_required_permissions_from_body(body: str) -> set[str]:
    perms: set[str] = set()
    for m in re.finditer(r"required_permissions\s*[:=]\s*\[([^\]]*)\]", body, flags=re.IGNORECASE):
        inner = m.group(1)
        for token in re.findall(r"['\"]([^'\"]+)['\"]", inner):
            perms.add(token.strip())
    return perms


def validate_static_security(skill_path: Path, skill_content: str, *, frontmatter: dict) -> tuple[bool, str]:
    """Run lightweight static security checks before packaging."""
    credential_files: list[tuple[Path, str]] = [(skill_path / "SKILL.md", skill_content)]
    script_files: list[tuple[Path, str]] = []

    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for script_path in scripts_dir.rglob("*"):
            if not script_path.is_file():
                continue
            try:
                text = script_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            script_files.append((script_path, text))
            credential_files.append((script_path, text))

    # Path traversal in file paths (should not happen, but check)
    for file_path, _ in credential_files:
        rel_path = file_path.relative_to(skill_path)
        if ".." in rel_path.parts:
            return False, f"Path traversal detected: {rel_path}"

    # Path traversal in script contents
    for file_path, text in script_files:
        rel_path = file_path.relative_to(skill_path)
        if ("../" in text) or ("..\\" in text):
            return False, f"Path traversal detected in script content: {rel_path}"

    # Dangerous command patterns in scripts
    for file_path, text in script_files:
        rel_path = file_path.relative_to(skill_path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern, label in DANGEROUS_PATTERNS:
                if pattern.search(line):
                    return False, (
                        f"Security check failed in {rel_path}:{line_number}: "
                        f"prohibited command pattern `{label}`"
                    )

    # Credential patterns in body / scripts
    for file_path, text in credential_files:
        rel_path = file_path.relative_to(skill_path)
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                return False, f"Security check failed in {rel_path}: possible hardcoded credential"

    # Permission consistency: requestPermissions vs required_permissions in body
    declared = _extract_permissions(frontmatter.get("requestPermissions") or frontmatter.get("request_permissions"))
    required = _extract_required_permissions_from_body(skill_content)
    if required and not required.issubset(declared):
        missing = ", ".join(sorted(required - declared))
        return False, f"Permission consistency failed: requestPermissions missing {missing}"

    return True, "Static security checks passed"


def validate_semantic_audit(description: str, body: str) -> tuple[bool, str]:
    """LLM semantic audit: must pass."""
    for pattern, label in SEMANTIC_PATTERNS:
        if pattern.search(body):
            return False, f"Semantic audit failed: {label}"

    if description and re.search(r"(万能|任何|all-in-one|anything|everything)", description, re.IGNORECASE):
        return False, "Semantic audit failed: suspicious over-claim in description"

    return True, "Semantic audit passed"


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Validation of an imported skill for listing."""
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
    body = content[match.end():].lstrip("\n")

    duplicate_key = find_duplicate_frontmatter_key(frontmatter_text)
    if duplicate_key:
        return False, f"Duplicate key in SKILL.md frontmatter: {duplicate_key}"

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"

    # Allowed top-level frontmatter keys (listing)
    allowed_properties = {
        "name",
        "description",
        "license",
        "allowed-tools",
        "metadata",
        "compatibility",
        "requestPermissions",
        "request_permissions",
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
    if not SKILL_NAME_PATTERN.match(name):
        return False, f"Name '{name}' must match [a-zA-Z0-9_-]{{1,64}}"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    if name != skill_path.name:
        return False, f"Name '{name}' must match directory name '{skill_path.name}'"

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if not description:
        return False, "Description cannot be empty"
    max_description_chars = DESCRIPTION_MAX_CHARS_CJK if contains_cjk(description) else DESCRIPTION_MAX_CHARS_EN
    if len(description) > max_description_chars:
        return False, (
            f"Description is too long ({len(description)} characters). "
            f"Maximum is {max_description_chars} characters."
        )
    desc_tokens = estimate_tokens(description)
    if desc_tokens > DESCRIPTION_MAX_TOKENS:
        return False, f"Description token count too large (~{desc_tokens} > {DESCRIPTION_MAX_TOKENS})"

    body_lines = body.splitlines()
    if not body.strip():
        return False, "SKILL.md body cannot be empty"
    if len(body_lines) > BODY_MAX_LINES:
        return False, f"SKILL.md body is too long ({len(body_lines)} lines). Maximum is {BODY_MAX_LINES} lines."
    body_tokens = estimate_tokens(body)
    if body_tokens > BODY_MAX_TOKENS:
        return False, f"SKILL.md body token count too large (~{body_tokens} > {BODY_MAX_TOKENS})"

    security_valid, security_message = validate_static_security(skill_path, content, frontmatter=frontmatter)
    if not security_valid:
        return False, security_message

    semantic_valid, semantic_message = validate_semantic_audit(description, body)
    if not semantic_valid:
        return False, semantic_message

    return True, "Skill is valid!"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) != 2:
        logger.error("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    ok, msg = validate_skill(sys.argv[1])
    if ok:
        logger.info(msg)
    else:
        logger.error(msg)
    sys.exit(0 if ok else 1)

