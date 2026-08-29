"""Guard tests: keep the ui_e2e harness's environment assumptions valid.

These tests are intentionally playwright-free so they run in environments
without browsers installed. Do not import the case scripts here (they import
playwright at module level); run_suite only pulls in stdlib helpers.
"""
from pathlib import Path

import tomllib

try:
    from tests.ui_e2e import run_suite
except ImportError:
    import run_suite

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_web_dir_exists():
    assert run_suite.WEB_DIR.is_dir()
    assert (run_suite.WEB_DIR / "package.json").is_file()


def test_case_scripts_exist():
    for script in run_suite.CASE_SCRIPTS.values():
        assert script.is_file()


def test_app_web_exists():
    assert (REPO_ROOT / "jiuwenswarm" / "channels" / "web" / "app_web.py").is_file()


def test_playwright_is_core_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("playwright") for dep in deps)
