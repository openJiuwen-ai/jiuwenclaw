"""Shared utility helpers for SkillDev."""

from __future__ import annotations

import zipfile
from pathlib import Path


def safe_extract_zip(
    zip_path: Path,
    dest_dir: Path,
    *,
    extract_to_stem_dir: bool = True,
) -> Path:
    """Extract a zip archive safely and return extraction directory.

    - Validates zip format.
    - Skips macOS metadata entries.
    - Prevents zip-slip path traversal.
    """
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"文件不是合法的 zip: {zip_path.name}")

    extract_dir = dest_dir / zip_path.stem if extract_to_stem_dir else dest_dir
    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            name = member.filename
            if name.startswith("__MACOSX/") or name.startswith("._"):
                continue
            target = (extract_dir / name).resolve()
            if not str(target).startswith(str(root)):
                continue
            zf.extract(member, extract_dir)

    return extract_dir
