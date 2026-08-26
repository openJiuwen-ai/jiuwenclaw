"""Optional Huawei Ascend NPU skills for JiuwenSwarm.

When this package is installed, JiuwenSwarm lists the bundled skills in its
builtin skill catalog (see ``jiuwenswarm.common.utils.iter_builtin_skills_dirs``).
"""

from pathlib import Path


def get_skills_dir() -> Path:
    """Return the directory containing the bundled skill folders."""
    return Path(__file__).resolve().parent / "skills"
