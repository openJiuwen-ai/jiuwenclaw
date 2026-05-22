#!/usr/bin/env python3
"""
Quick validation script for skills - minimal version
"""

import logging
import re
import sys
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)

DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s+-[^\n]*r[^\n]*f\s+/"), "rm -rf /"),
    (re.compile(r"\bchmod\s+777\b"), "chmod 777"),
    (re.compile(r"\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b"), "curl | sh/bash"),
    (re.compile(r"\beval\s*\("), "eval(...)"),
]
CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]
def validate_skill(skill_path):
    """Basic validation of a skill"""
    skill_path = Path(skill_path).resolve()

    # Check SKILL.md exists
    skill_md = skill_path / 'SKILL.md'
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and validate frontmatter
    content = skill_md.read_text(encoding='utf-8')
    if not content.startswith('---'):
        return False, "No YAML frontmatter found"

    # Extract frontmatter
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)
    body = content[match.end():].lstrip("\n")

    duplicate_key = find_duplicate_frontmatter_key(frontmatter_text)
    if duplicate_key:
        return False, f"Duplicate key in SKILL.md frontmatter: {duplicate_key}"

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    # Define allowed properties
    ALLOWED_PROPERTIES = {'name', 'description', 'license', 'allowed-tools', 'metadata', 'compatibility'}

    # Check for unexpected properties (excluding nested keys under metadata)
    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_PROPERTIES))}"
        )

    # Check required fields
    if 'name' not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if 'description' not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    # Extract name for validation
    name = frontmatter.get('name', '')
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if not name:
        return False, "Name cannot be empty"
    # Check naming convention (kebab-case: lowercase with hyphens)
    if not re.match(r'^[a-z0-9-]+$', name):
        return False, f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
    if name.startswith('-') or name.endswith('-') or '--' in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    # Check name length
    if len(name) > 64:
        return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters."
    if name != skill_path.name:
        return False, f"Name '{name}' must match directory name '{skill_path.name}'"

    # Extract and validate description
    description = frontmatter.get('description', '')
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if not description:
        return False, "Description cannot be empty"
    # Check for angle brackets
    if '<' in description or '>' in description:
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


def find_duplicate_frontmatter_key(frontmatter_text):
    """Return the first duplicate top-level YAML key, if any."""
    seen = set()
    for line in frontmatter_text.splitlines():
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            return key
        seen.add(key)
    return None


def contains_cjk(text):
    """Detect CJK characters for the stricter Chinese description limit."""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def validate_static_security(skill_path, skill_content):
    """Run lightweight static security checks before packaging."""
    credential_files = [(skill_path / "SKILL.md", skill_content)]
    script_files = []
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) != 2:
        logger.error("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    if valid:
        logger.info(message)
    else:
        logger.error(message)
    sys.exit(0 if valid else 1)