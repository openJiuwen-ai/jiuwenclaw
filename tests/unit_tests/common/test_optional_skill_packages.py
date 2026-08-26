"""Tests for optional builtin-skill package discovery (workswarm-skills-ascend)."""

from __future__ import annotations

import importlib
import sys

from jiuwenswarm.common import utils as common_utils

ASCEND_SKILLS = (
    "akg-agents",
    "ascend-moe-optimizer-auto-trace",
    "ascend-moe-optimizer-trace-analyzer",
    "deepep-to-cam-converter",
)


def make_fake_skills_package(tmp_path, monkeypatch, skill_name="dummy-skill"):
    """Create an importable fake jiuwenswarm_skills_ascend package in tmp_path."""
    pkg_root = tmp_path / "fake-site-packages"
    pkg_dir = pkg_root / "jiuwenswarm_skills_ascend"
    skill_dir = pkg_dir / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: fake optional skill\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "jiuwenswarm_skills_ascend", raising=False)
    monkeypatch.syspath_prepend(str(pkg_root))
    importlib.invalidate_caches()
    # conftest blanks this for hermeticity; re-enable discovery for the fake package
    monkeypatch.setattr(common_utils, "_OPTIONAL_SKILL_PACKAGES", ("jiuwenswarm_skills_ascend",))
    return pkg_dir / "skills"


def test_extra_dirs_empty_when_module_absent(monkeypatch):
    monkeypatch.setattr(
        common_utils, "_OPTIONAL_SKILL_PACKAGES", ("jiuwenswarm_skills_test_absent",)
    )
    assert common_utils.get_extra_builtin_skills_dirs() == []
    assert common_utils.iter_builtin_skills_dirs() == [common_utils.get_builtin_skills_dir()]


def test_extra_dirs_discover_installed_package(tmp_path, monkeypatch):
    fake_skills_dir = make_fake_skills_package(tmp_path, monkeypatch)

    extra = common_utils.get_extra_builtin_skills_dirs()
    assert [d.resolve() for d in extra] == [fake_skills_dir.resolve()]

    dirs = common_utils.iter_builtin_skills_dirs()
    # Primary dir always first so it wins name collisions
    assert dirs[0] == common_utils.get_builtin_skills_dir()
    assert dirs[1].resolve() == fake_skills_dir.resolve()


def test_builtin_skill_names_include_optional_package(tmp_path, monkeypatch):
    make_fake_skills_package(tmp_path, monkeypatch)

    names = common_utils._get_builtin_skill_names()
    assert "dummy-skill" in names
    # Skills from the primary dir are still present
    assert "skill-creator" in names


def test_ascend_skills_removed_from_primary_builtin_dir():
    builtin_dir = common_utils.get_builtin_skills_dir()
    for name in ASCEND_SKILLS:
        assert not (builtin_dir / name).exists(), (
            f"{name} must live in packages/jiuwenswarm-skills-ascend, "
            "not in the default resources tree"
        )
