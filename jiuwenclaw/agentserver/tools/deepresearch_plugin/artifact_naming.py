"""Pure versioned filename allocation for DeepResearch artifacts.

Artifact revision identity belongs to provenance.  Filenames are only a
human-facing allocation concern, so this module neither writes nor publishes
artifact payloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .conversion_utils import make_safe_filename_component


_ERROR_CODE = "ARTIFACT_NAMING_INVALID"
_DEFAULT_BASE_STEM = "深度研究报告"
_MAX_BASE_STEM_LENGTH = 120
_VERSION_SUFFIX_RE = re.compile(r"-v[1-9]\d*$")
_ATX_H1_RE = re.compile(r"^[ \t]{0,3}#[ \t]+(?P<text>.*?)[ \t]*$")
_SETEXT_H1_RE = re.compile(r"^[ \t]{0,3}=+[ \t]*$")


class ArtifactNamingError(ValueError):
    """Raised when artifact version metadata cannot be interpreted safely."""

    code = _ERROR_CODE

    def __init__(self, message: str):
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    base_stem: str
    version_number: int


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    version: ArtifactVersion
    markdown_path: Path
    provenance_path: Path
    final_result_path: Path


def _invalid(message: str) -> ArtifactNamingError:
    return ArtifactNamingError(message)


def _strip_terminal_version(value: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", value)


def _cap_base_stem(value: str) -> str:
    return value[:_MAX_BASE_STEM_LENGTH].rstrip("._-")


def _normalize_base_stem(value: str) -> str:
    if not isinstance(value, str):
        raise _invalid("artifact base stem must be a string")
    normalized = make_safe_filename_component(
        _strip_terminal_version(value), default=_DEFAULT_BASE_STEM
    )
    normalized = _cap_base_stem(normalized)
    return normalized or _DEFAULT_BASE_STEM


def _validate_explicit_base_stem(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid("version_base_stem must be a non-empty string")
    if _VERSION_SUFFIX_RE.search(value):
        raise _invalid("version_base_stem must not end in a version suffix")
    if make_safe_filename_component(value) != value:
        raise _invalid("version_base_stem is not a safe filename component")
    if _cap_base_stem(value) != value:
        raise _invalid("version_base_stem exceeds the filename allocation limit")
    return value


def _first_h1(markdown: str) -> str | None:
    if not isinstance(markdown, str):
        raise _invalid("markdown must be a string")

    lines = markdown.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.lstrip(" \t")
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        atx_match = _ATX_H1_RE.match(line)
        if atx_match:
            heading = re.sub(r"[ \t]+#+[ \t]*$", "", atx_match.group("text"))
            if heading:
                return heading
        if index and _SETEXT_H1_RE.match(line):
            heading = lines[index - 1].strip()
            if heading:
                return heading
    return None


def _legacy_base_stem(report_path: Path, markdown: str) -> str:
    heading = _first_h1(markdown)
    if heading:
        return _normalize_base_stem(heading)

    fallback_stem = _strip_terminal_version(report_path.stem)
    if fallback_stem:
        return _normalize_base_stem(fallback_stem)
    return _DEFAULT_BASE_STEM


def initial_version(requested_name: str) -> ArtifactVersion:
    """Return the logical initial version for a user-requested report name."""

    if not isinstance(requested_name, str):
        raise _invalid("requested_name must be a string")
    requested_stem = (
        requested_name[:-3] if requested_name.lower().endswith(".md") else requested_name
    )
    return ArtifactVersion(_normalize_base_stem(requested_stem), 1)


def resolve_artifact_version(
    provenance: dict, report_path: Path, markdown: str
) -> ArtifactVersion:
    """Resolve explicit provenance metadata or a safe legacy logical version."""

    if not isinstance(provenance, dict):
        raise _invalid("provenance must be a dictionary")
    if not isinstance(report_path, Path):
        raise _invalid("report_path must be a Path")

    has_number = "version_number" in provenance
    has_base = "version_base_stem" in provenance
    if has_number != has_base:
        raise _invalid("version metadata must include both fields")
    if has_number:
        version_number = provenance["version_number"]
        if type(version_number) is not int or version_number < 1:
            raise _invalid("version_number must be a positive integer")
        return ArtifactVersion(
            _validate_explicit_base_stem(provenance["version_base_stem"]), version_number
        )

    history = provenance.get("rewrite_history", [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise _invalid("legacy rewrite_history must be a list of mappings")
    return ArtifactVersion(_legacy_base_stem(report_path, markdown), len(history) + 1)


def _paths(output_dir: Path, version: ArtifactVersion) -> ArtifactPaths:
    markdown_path = output_dir / f"{version.base_stem}-v{version.version_number}.md"
    return ArtifactPaths(
        version=version,
        markdown_path=markdown_path,
        provenance_path=markdown_path.with_suffix(".provenance.json"),
        final_result_path=markdown_path.with_suffix(".final-result.json"),
    )


def _is_available(paths: ArtifactPaths) -> bool:
    return not any(
        path.exists()
        for path in (paths.markdown_path, paths.provenance_path, paths.final_result_path)
    )


def allocate_initial_paths(output_dir: Path, requested_name: str) -> ArtifactPaths:
    """Allocate a collision-free initial artifact path without creating it."""

    if not isinstance(output_dir, Path):
        raise _invalid("output_dir must be a Path")
    version = initial_version(requested_name)
    suffix = 1
    while True:
        base_stem = version.base_stem if suffix == 1 else f"{version.base_stem}-{suffix}"
        candidate = _paths(output_dir, ArtifactVersion(base_stem, 1))
        if _is_available(candidate):
            return candidate
        suffix += 1


def _require_document_id(provenance: dict) -> str:
    document_id = provenance.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise _invalid("document_id must be a non-empty string")
    return document_id


def _sidecar_markdown_path(sidecar: Path) -> Path:
    suffix = ".provenance.json"
    return sidecar.with_name(f"{sidecar.name.removesuffix(suffix)}.md")


def _sibling_versions(parent_path: Path, document_id: str) -> list[ArtifactVersion]:
    versions: list[ArtifactVersion] = []
    for sidecar in sorted(parent_path.parent.glob("*.provenance.json")):
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("document_id") != document_id:
            continue
        markdown_path = _sidecar_markdown_path(sidecar)
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            markdown = ""
        versions.append(resolve_artifact_version(payload, markdown_path, markdown))
    return versions


def allocate_next_paths(
    parent_path: Path, parent_provenance: dict, parent_markdown: str
) -> ArtifactPaths:
    """Allocate the next document-global version without writing any artifacts."""

    if not isinstance(parent_path, Path):
        raise _invalid("parent_path must be a Path")
    document_id = _require_document_id(parent_provenance)
    parent_version = resolve_artifact_version(
        parent_provenance, parent_path, parent_markdown
    )
    max_version = max(
        [parent_version.version_number]
        + [version.version_number for version in _sibling_versions(parent_path, document_id)]
    )
    next_number = max_version + 1
    while True:
        candidate = _paths(
            parent_path.parent,
            ArtifactVersion(parent_version.base_stem, next_number),
        )
        if _is_available(candidate):
            return candidate
        next_number += 1
