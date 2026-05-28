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

_DANGEROUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("检测到危险命令：rm -rf /", re.compile(r"\brm\s+-rf\s+/\b")),
    ("检测到危险命令：chmod 777", re.compile(r"\bchmod\s+777\b")),
    ("检测到危险命令：curl | bash", re.compile(r"\bcurl\b[^\n|]*\|\s*\bbash\b")),
    ("检测到危险命令：wget | bash", re.compile(r"\bwget\b[^\n|]*\|\s*\bbash\b")),
    ("检测到危险命令：eval", re.compile(r"\beval\b")),
]

_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("疑似硬编码 OpenAI Key（sk-...）", re.compile(r"\bsk-[A-Za-z0-9]{8,}\b")),
    ("疑似硬编码 AWS Access Key（AKIA...）", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("疑似硬编码 token/api_key/password", re.compile(r"\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]", re.IGNORECASE)),
]

_PROMPT_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("疑似 Prompt Injection：ignore previous instructions", re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE)),
    ("疑似 Prompt Injection：覆盖系统/系统提示词", re.compile(r"(覆盖|忽略).{0,10}(系统|system).{0,10}(指令|prompt)", re.IGNORECASE)),
    ("疑似 Prompt Injection：你现在是/你必须", re.compile(r"(你现在是|你必须).{0,30}(系统|system)", re.IGNORECASE)),
]


def _iter_text_files(root: Path, *, include: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pat in include:
        files.extend(root.rglob(pat))
    return [p for p in files if p.is_file()]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return path.read_text(encoding="utf-8", errors="ignore")


def _scan_patterns(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    hits: list[str] = []
    for msg, pat in patterns:
        if pat.search(text):
            hits.append(msg)
    return hits


def _extract_declared_permissions(frontmatter: dict[str, str]) -> set[str]:
    raw = str(frontmatter.get("requestPermissions") or frontmatter.get("request_permissions") or "").strip()
    if not raw:
        return set()
    # support simple forms: "a,b" / "[a, b]" / "a"
    raw = raw.strip().strip("[]")
    parts = [p.strip().strip("'\"") for p in re.split(r"[,\n]", raw) if p.strip()]
    return {p for p in parts if p}


def _extract_required_permissions_from_body(body: str) -> set[str]:
    perms: set[str] = set()
    # Heuristic: find required_permissions: ["x","y"] or required_permissions=['x']
    for m in re.finditer(r"required_permissions\s*[:=]\s*\[([^\]]*)\]", body, flags=re.IGNORECASE):
        inner = m.group(1)
        for token in re.findall(r"['\"]([^'\"]+)['\"]", inner):
            perms.add(token.strip())
    return perms


def _contains_path_traversal(text: str) -> bool:
    return ("../" in text) or ("..\\" in text)


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

    # --- Static rule scan (must pass) ---
    scripts_dir = skill_root / "scripts"
    script_files = _iter_text_files(scripts_dir, include=("*.py", "*.sh", "*.ps1", "*.bat", "*.cmd")) if scripts_dir.is_dir() else []
    combined_script_text = "\n\n".join(_read_text(p) for p in script_files)
    combined_all_text = "\n\n".join([description, body, combined_script_text])

    errors.extend(_scan_patterns(combined_script_text, _DANGEROUS_PATTERNS))

    cred_hits = _scan_patterns(combined_all_text, _CREDENTIAL_PATTERNS)
    if cred_hits:
        errors.extend(cred_hits)

    if _contains_path_traversal(combined_script_text):
        errors.append("检测到路径越界（脚本内容包含 ../ 或 ..\\\\）")

    declared = _extract_declared_permissions(frontmatter)
    required = _extract_required_permissions_from_body(body)
    if required and not required.issubset(declared):
        missing = ", ".join(sorted(required - declared))
        errors.append(f"权限一致性不通过：requestPermissions 缺少 {missing}")

    # --- LLM semantic audit (must pass) ---
    errors.extend(_scan_patterns(body, _PROMPT_INJECTION_PATTERNS))
    if description and re.search(r"(万能|任何|all-in-one|anything|everything)", description, re.IGNORECASE):
        errors.append("疑似虚假/过度声明：description 可能包含过泛能力描述（需收敛）")

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
