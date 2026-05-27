"""Self-contained skill validation and packaging for skill-standardizer."""

from __future__ import annotations

import fnmatch
import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DESCRIPTION_MAX_TOKENS = 300
BODY_MAX_TOKENS = 5000
BODY_MAX_LINES = 500
DESCRIPTION_MAX_CHARS_CJK = 256
DESCRIPTION_MAX_CHARS_EN = 512

EXCLUDE_DIRS = {"__pycache__", "node_modules"}
EXCLUDE_GLOBS = {"*.pyc", "*.swp"}
EXCLUDE_FILES = {".DS_Store"}
ROOT_EXCLUDE_DIRS = {"evals", "output"}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token)."""
    if not text:
        return 0
    return len(text) // 4


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple single-line YAML frontmatter key: value pairs."""
    result: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        key_match = re.match(r"^([a-zA-Z_-]+):\s*(.*)", line)
        if key_match:
            if current_key:
                result[current_key] = "\n".join(current_lines).strip()
            current_key = key_match.group(1)
            value = key_match.group(2)
            current_lines = [] if value in ("|", ">") else [value]
        elif current_key:
            current_lines.append(line)

    if current_key:
        result[current_key] = "\n".join(current_lines).strip()

    return result


def normalize_skill_description(text: str) -> str:
    """Normalize description to a single plain-text line."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^[>|]\-?\s*", "", cleaned)
    while True:
        prev = cleaned
        cleaned = re.sub(r"^[>#*•]\s*", "", cleaned)
        cleaned = re.sub(r"^-\s+", "", cleaned)
        cleaned = cleaned.lstrip()
        if cleaned == prev:
            break
    return cleaned


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


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _parse_skill_md(skill_md: Path) -> tuple[dict[str, str], str]:
    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise ValueError("SKILL.md 缺少 YAML frontmatter（应以 --- 开头）")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", content, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md frontmatter 格式无效")
    frontmatter = parse_frontmatter(match.group(1))
    body = match.group(2)
    return frontmatter, body


def validate_direct_import_skill(skill_root: Path) -> tuple[bool, str]:
    """Validate SKILL.md against listing rules; collect all violations."""
    errors: list[str] = []
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        return False, "解压后未找到 SKILL.md"

    try:
        frontmatter, body = _parse_skill_md(skill_md)
    except ValueError as exc:
        return False, str(exc)

    name = str(frontmatter.get("name") or "").strip()
    if not name:
        errors.append("frontmatter 缺少必填字段 name（skill-name）")
    else:
        if not SKILL_NAME_PATTERN.match(name):
            errors.append(
                f"skill-name '{name}' 不符合规范：仅允许 [a-zA-Z0-9_-]，长度 1-64"
            )
        if name.startswith("-") or name.endswith("-") or "--" in name:
            errors.append(
                f"skill-name '{name}' 不能以 '-' 开头/结尾或包含连续 '--'"
            )
        if name != skill_root.name:
            errors.append(
                f"skill-name '{name}' 必须与父目录名 '{skill_root.name}' 一致"
            )

    description = normalize_skill_description(str(frontmatter.get("description") or ""))
    if not description:
        errors.append("description 不能为空")
    else:
        max_chars = (
            DESCRIPTION_MAX_CHARS_CJK if _contains_cjk(description) else DESCRIPTION_MAX_CHARS_EN
        )
        if len(description) > max_chars:
            errors.append(
                f"description 字符数超限（{len(description)} > {max_chars}）"
            )
        desc_tokens = estimate_tokens(description)
        if desc_tokens > DESCRIPTION_MAX_TOKENS:
            errors.append(
                f"description token 数超限（约 {desc_tokens} > {DESCRIPTION_MAX_TOKENS}）"
            )

    body_lines = body.splitlines()
    if not body.strip():
        errors.append("SKILL.md 正文不能为空")
    else:
        if len(body_lines) > BODY_MAX_LINES:
            errors.append(f"正文行数超限（{len(body_lines)} > {BODY_MAX_LINES}）")
        body_tokens = estimate_tokens(body)
        if body_tokens > BODY_MAX_TOKENS:
            errors.append(
                f"正文 token 数超限（约 {body_tokens} > {BODY_MAX_TOKENS}）"
            )

    if errors:
        return False, "\n".join(f"- {item}" for item in errors)
    return True, "SKILL.md 校验通过"


def _should_exclude(rel_path: Path) -> bool:
    parts = rel_path.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    if len(parts) > 1 and parts[1] in ROOT_EXCLUDE_DIRS:
        return True
    name = rel_path.name
    if name in EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_GLOBS)


def package_validated_skill(skill_root: Path, output_dir: Path) -> Path | None:
    """Package a skill directory into a zip under output_dir."""
    skill_root = skill_root.resolve()
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        logger.error("package failed: SKILL.md missing in %s", skill_root)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    skill_name = skill_root.name
    skill_filename = output_dir / f"{skill_name}.zip"

    files_to_package: list[tuple[Path, Path]] = []
    for file_path in skill_root.rglob("*"):
        if not file_path.is_file():
            continue
        arcname = Path(skill_name) / file_path.relative_to(skill_root)
        if _should_exclude(arcname):
            continue
        files_to_package.append((file_path, arcname))

    try:
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path, arcname in files_to_package:
                zipf.write(file_path, arcname)
        logger.info("packaged skill to %s", skill_filename)
        return skill_filename
    except Exception as exc:
        logger.exception("package failed: %s", exc)
        return None
