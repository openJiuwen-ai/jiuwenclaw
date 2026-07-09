from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.0.8"


def _read_dist_info_version(base: Path) -> str | None:
    for _d in sorted(base.glob("jiuwenavatar-*.dist-info"), reverse=True):
        _meta = _d / "METADATA"
        if _meta.is_file():
            for _line in _meta.read_text(encoding="utf-8").splitlines():
                if _line.startswith("Version:"):
                    return _line.split(":", 1)[1].strip()
    return None


def _read_pyproject_version() -> str | None:
    try:
        import tomllib

        _ver_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if _ver_path.is_file():
            return tomllib.loads(_ver_path.read_text(encoding="utf-8"))["project"]["version"]
    except Exception:
        return None
    return None


if getattr(sys, "frozen", False):
    # PyInstaller: read from dist-info/METADATA (guaranteed by copy_metadata in spec)
    __version__ = _read_dist_info_version(Path(sys.executable).parent / "_internal") or __version__
else:
    try:
        from importlib.metadata import version as _meta_version
        __version__ = _meta_version("jiuwenavatar")
    except Exception:
        __version__ = _read_pyproject_version() or __version__
