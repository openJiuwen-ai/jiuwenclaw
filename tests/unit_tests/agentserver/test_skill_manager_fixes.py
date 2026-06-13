import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jiuwenclaw.agentserver.skill_manager import (
    SkillManager,
    _EVOLUTION_ENTRY_ID_PATTERN,
    _safe_child_path,
    _safe_path_name,
    _safe_rmtree,
)


class SkillManagerHarness(SkillManager):
    def set_mock_remote_import(self, mock_func):
        self._import_skill_from_remote_archive = mock_func

    def register_imported_skill(self, name: str, origin: str):
        self._add_local_skill({"name": name, "origin": origin, "source": "local"})
        self._refresh_agent_data_indexes()


# ---------------------------------------------------------------------------
# 1. _safe_rmtree 改进测试
# ---------------------------------------------------------------------------


def test_safe_rmtree_nonexistent_path(tmp_path):
    nonexistent = tmp_path / "does_not_exist"
    assert _safe_rmtree(nonexistent) is True


def test_safe_rmtree_normal_directory(tmp_path):
    target = tmp_path / "to_delete"
    target.mkdir()
    (target / "file.txt").write_text("hello", encoding="utf-8")
    assert _safe_rmtree(target) is True
    assert not target.exists()


def test_safe_rmtree_nested_directories(tmp_path):
    target = tmp_path / "nested"
    target.mkdir()
    sub = target / "sub" / "deep"
    sub.mkdir(parents=True)
    (sub / "data.json").write_text("{}", encoding="utf-8")
    assert _safe_rmtree(target) is True
    assert not target.exists()


@pytest.mark.skipif(
    not shutil.which("git"),
    reason="git not available",
)
def test_safe_rmtree_git_directory(tmp_path):
    import subprocess

    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env={**__import__("os").environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    assert _safe_rmtree(repo) is True
    assert not repo.exists()


# ---------------------------------------------------------------------------
# 2. _save_state 并发写入测试
# ---------------------------------------------------------------------------


def test_save_state_concurrent_writes(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    errors: list[Exception] = []

    def writer(iteration: int):
        try:
            for i in range(20):
                manager._add_local_skill({
                    "name": f"concurrent-skill-{iteration}-{i}",
                    "origin": f"test-{iteration}",
                    "source": "local",
                })
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(writer, idx) for idx in range(5)]
        for f in futures:
            f.result(timeout=30)

    assert not errors, f"Concurrent write errors: {errors}"
    state_file = manager._state_file
    assert state_file.exists()
    content = state_file.read_text(encoding="utf-8")
    parsed = json.loads(content)
    assert isinstance(parsed, dict)
    assert "local_skills" in parsed


# ---------------------------------------------------------------------------
# 3. Evolution entry ID 验证测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_id,valid",
    [
        ("abc123", True),
        ("entry-1", True),
        ("entry_2", True),
        ("entry:3", True),
        ("entry.4", True),
        ("../evil", False),
        ("entry with space", False),
        ("entry\nnewline", False),
        ("", False),
        ("entry|pipe", False),
        ("entry;semicolon", False),
    ],
)
def test_evolution_entry_id_pattern(entry_id, valid):
    match = _EVOLUTION_ENTRY_ID_PATTERN.match(entry_id)
    assert bool(match) == valid, f"entry_id={entry_id!r} expected valid={valid}"


@pytest.mark.asyncio
async def test_evolution_save_rejects_invalid_entry_id(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    skill_dir = tmp_path / "workspace" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\n---\nbody\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="非法字符"):
        await manager.handle_skills_evolution_save({
            "name": "test-skill",
            "entries": [{"id": "../evil", "change": {"content": "bad"}}],
        })


# ---------------------------------------------------------------------------
# 4. install_builtin force 参数测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_builtin_with_force(tmp_path, monkeypatch):
    builtin_dir = tmp_path / "builtin_skills"
    builtin_dir.mkdir()
    skill_src = builtin_dir / "my-builtin"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text(
        "---\nname: my-builtin\nversion: 1.0.0\n---\nbody\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.skill_manager.get_builtin_skills_dir",
        lambda: builtin_dir,
    )

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    result1 = await manager.handle_skills_install_builtin({"name": "my-builtin"})
    assert result1["success"] is True

    result2 = await manager.handle_skills_install_builtin({"name": "my-builtin"})
    assert result2["success"] is False

    result3 = await manager.handle_skills_install_builtin({
        "name": "my-builtin",
        "force": True,
    })
    assert result3["success"] is True


# ---------------------------------------------------------------------------
# 5. install_jobs 清理测试
# ---------------------------------------------------------------------------


def test_cleanup_skillnet_install_jobs_removes_old(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    from datetime import datetime, timezone, timedelta

    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    recent_time = datetime.now(timezone.utc).isoformat()

    manager._skillnet_install_jobs = {
        "old-done": {"status": "done", "created_at": old_time, "skill": {"name": "old"}},
        "recent-done": {"status": "done", "created_at": recent_time, "skill": {"name": "recent"}},
        "pending-job": {"status": "pending", "created_at": old_time},
    }

    manager._cleanup_skillnet_install_jobs()

    assert "old-done" not in manager._skillnet_install_jobs
    assert "recent-done" in manager._skillnet_install_jobs
    assert "pending-job" in manager._skillnet_install_jobs


def test_cleanup_skillnet_install_jobs_max_count(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    from jiuwenclaw.agentserver.skill_manager import _SKILLNET_INSTALL_JOBS_MAX_COUNT
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for i in range(_SKILLNET_INSTALL_JOBS_MAX_COUNT + 10):
        manager._skillnet_install_jobs[f"job-{i}"] = {
            "status": "done",
            "created_at": now,
            "skill": {"name": f"skill-{i}"},
        }

    manager._cleanup_skillnet_install_jobs()
    assert len(manager._skillnet_install_jobs) <= _SKILLNET_INSTALL_JOBS_MAX_COUNT


# ---------------------------------------------------------------------------
# 6. handle_skills_get 路径安全测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_get_rejects_path_traversal_name(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    with pytest.raises(ValueError, match="无效的 skill name"):
        await manager.handle_skills_get({"name": "../etc/passwd"})


@pytest.mark.asyncio
async def test_skills_get_rejects_empty_name(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    with pytest.raises(ValueError, match="缺少参数"):
        await manager.handle_skills_get({"name": ""})


# ---------------------------------------------------------------------------
# 7. Zip Slip 防护测试 (ClawHub download 使用 _safe_extract_zip_to_dir)
# ---------------------------------------------------------------------------


def test_safe_extract_zip_rejects_path_traversal(tmp_path):
    import io
    import zipfile

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("../../etc/evil.txt", "malicious content")
    zip_buf.seek(0)

    zip_path = tmp_path / "evil.zip"
    zip_path.write_bytes(zip_buf.read())

    with pytest.raises(RuntimeError, match="非法路径|路径越界"):
        SkillManager._safe_extract_zip_to_dir(zip_path, tmp_path / "dest")


def test_safe_extract_zip_rejects_absolute_path(tmp_path):
    import io
    import zipfile

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("/etc/passwd", "malicious content")
    zip_buf.seek(0)

    zip_path = tmp_path / "evil_abs.zip"
    zip_path.write_bytes(zip_buf.read())

    dest = tmp_path / "dest"
    SkillManager._safe_extract_zip_to_dir(zip_path, dest)
    assert not (dest / "etc" / "passwd").exists(), "absolute path file should be skipped"


def test_safe_extract_zip_normal_files(tmp_path):
    import io
    import zipfile

    dest = tmp_path / "dest"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: test\n---\nbody\n")
        zf.writestr("subdir/helper.py", "print('hello')")
    zip_buf.seek(0)

    zip_path = tmp_path / "good.zip"
    zip_path.write_bytes(zip_buf.read())

    SkillManager._safe_extract_zip_to_dir(zip_path, dest)
    assert (dest / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (dest / "subdir" / "helper.py").exists()


# ---------------------------------------------------------------------------
# 8. ClawHub token 掩码测试
# ---------------------------------------------------------------------------


def test_mask_clawhub_token_short():
    assert SkillManager._mask_clawhub_token("abcd") == "****"


def test_mask_clawhub_token_long():
    masked = SkillManager._mask_clawhub_token("abcdefghijklmnop")
    assert masked.startswith("abcd")
    assert masked.endswith("mnop")
    assert "*" in masked


def test_mask_clawhub_token_empty():
    assert SkillManager._mask_clawhub_token("") == ""


# ---------------------------------------------------------------------------
# 9. _save_state 原子写入测试
# ---------------------------------------------------------------------------


def test_save_state_atomic_write(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    manager._add_local_skill({"name": "test-skill", "origin": "test", "source": "local"})

    state_file = manager._state_file
    assert state_file.exists()
    tmp_file = state_file.with_suffix(".tmp")
    assert not tmp_file.exists(), "temp file should be cleaned up after atomic write"


# ---------------------------------------------------------------------------
# 10. _safe_path_name 和 _safe_child_path 额外边界测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a" * 300, "skill\x00name", "skill\nname"])
def test_safe_path_name_rejects_unusual_names(name):
    if not name.strip():
        with pytest.raises(ValueError):
            _safe_path_name(name, "skill")
    elif "/" in name or "\\" in name or name in (".", ".."):
        with pytest.raises(ValueError):
            _safe_path_name(name, "skill")


def test_safe_child_path_normal(tmp_path):
    child = _safe_child_path(tmp_path, "good-skill", "skill")
    assert child == (tmp_path / "good-skill").resolve()


# ---------------------------------------------------------------------------
# 11. _scan_local_skills 不暴露 body 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_local_skills_no_body_leak(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    skill_dir = tmp_path / "workspace" / "skills" / "leak-test"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: leak-test\nversion: 1.0.0\n---\nsecret body content\n",
        encoding="utf-8",
    )

    skills = manager._scan_local_skills()
    for skill in skills:
        assert "body" not in skill, f"skill {skill.get('name')} leaked body field"


# ---------------------------------------------------------------------------
# 12. _skillnet_install_jobs_lock 线程安全测试
# ---------------------------------------------------------------------------


def test_install_jobs_lock_thread_safety(tmp_path):
    from datetime import datetime, timezone

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    errors: list[Exception] = []

    def writer(iteration: int):
        try:
            install_id = f"test-{iteration}"
            with manager._install_jobs_lock:
                manager._skillnet_install_jobs[install_id] = {
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            time.sleep(0.001)
            with manager._install_jobs_lock:
                manager._skillnet_install_jobs[install_id] = {
                    "status": "done",
                    "created_at": manager._skillnet_install_jobs[install_id]["created_at"],
                    "skill": {"name": f"skill-{iteration}"},
                }
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(writer, idx) for idx in range(50)]
        for f in futures:
            f.result(timeout=30)

    assert not errors, f"Thread safety errors: {errors}"
    assert len(manager._skillnet_install_jobs) == 50


# ---------------------------------------------------------------------------
# 13. normalize_marketplaces 过滤测试
# ---------------------------------------------------------------------------


def test_normalize_marketplaces_filters_invalid():
    raw = [
        {"name": "valid", "url": "https://example.com/repo.git"},
        {"name": "", "url": "https://example.com/repo.git"},
        {"name": "no-url", "url": ""},
        "not-a-dict",
        {"name": "also-valid", "url": "https://example.com/other.git", "enabled": False},
    ]
    result = SkillManager.normalize_marketplaces(raw)
    assert len(result) == 2
    assert result[0]["name"] == "valid"
    assert result[1]["name"] == "also-valid"
    assert result[0]["enabled"] is True
    assert result[1]["enabled"] is False


# ---------------------------------------------------------------------------
# 14. _detect_archive_format 测试
# ---------------------------------------------------------------------------


def test_detect_archive_format_zip():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.txt", "hello")
    body = buf.getvalue()
    assert SkillManager._detect_archive_format(body) == "zip"


def test_detect_archive_format_invalid():
    assert SkillManager._detect_archive_format(b"not an archive") == ""


# ---------------------------------------------------------------------------
# 15. _openjiuwen_host_matches_rule 测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,rule,expected",
    [
        ("openjiuwen-market.obs.cn-north-4.myhuaweicloud.com",
         "openjiuwen-market.obs.*.myhuaweicloud.com", True),
        ("evil.com",
         "openjiuwen-market.obs.*.myhuaweicloud.com", False),
        ("a.b.c.com", "*.b.c.com", True),
        ("a.x.c.com", "*.b.c.com", False),
        ("short.com", "a.b.c.com", False),
    ],
)
def test_openjiuwen_host_matches_rule(host, rule, expected):
    result = SkillManager._openjiuwen_host_matches_rule(host, rule)
    assert result is expected


# ---------------------------------------------------------------------------
# 16. _coerce_str_list 测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val,expected",
    [
        (None, []),
        ([], []),
        (["a", "b"], ["a", "b"]),
        ("a, b, c", ["a", "b", "c"]),
        ("single", ["single"]),
        ("", []),
        (123, ["123"]),
    ],
)
def test_coerce_str_list(val, expected):
    result = SkillManager._coerce_str_list(val)
    assert result == expected


# ---------------------------------------------------------------------------
# 17. _parse_skill_md 无 frontmatter 测试
# ---------------------------------------------------------------------------


def test_parse_skill_md_no_frontmatter(tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("Just plain text body\n", encoding="utf-8")
    result = SkillManager._parse_skill_md(md)
    assert result is not None
    assert result["name"] == "SKILL"
    assert result["body"].strip() == "Just plain text body"


# ---------------------------------------------------------------------------
# 18. _is_valid_http_mirror_url 测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://mirror.example.com", True),
        ("http://mirror.example.com", True),
        ("ftp://mirror.example.com", False),
        ("", False),
        ("not-a-url", False),
        ("a" * 2049, False),
    ],
)
def test_is_valid_http_mirror_url(url, expected):
    from jiuwenclaw.agentserver.skill_manager import _is_valid_http_mirror_url
    assert _is_valid_http_mirror_url(url) is expected


# ---------------------------------------------------------------------------
# 19. _env_bool 测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,default,expected",
    [
        ("1", False, True),
        ("true", False, True),
        ("false", True, False),
        ("", True, True),
        ("", False, False),
    ],
)
def test_env_bool(monkeypatch, value, default, expected):
    from jiuwenclaw.agentserver.skill_manager import _env_bool
    monkeypatch.setenv("TEST_ENV_BOOL", value)
    assert _env_bool("TEST_ENV_BOOL", default) is expected


# ---------------------------------------------------------------------------
# 20. enabled_skills_from_environ 测试
# ---------------------------------------------------------------------------


def test_enabled_skills_from_environ_none(monkeypatch):
    from jiuwenclaw.agentserver.skill_manager import enabled_skills_from_environ
    monkeypatch.delenv("ENABLED_SKILLS", raising=False)
    assert enabled_skills_from_environ() is None


def test_enabled_skills_from_environ_empty(monkeypatch):
    from jiuwenclaw.agentserver.skill_manager import enabled_skills_from_environ
    monkeypatch.setenv("ENABLED_SKILLS", "  ")
    assert enabled_skills_from_environ() is None


def test_enabled_skills_from_environ_set(monkeypatch):
    from jiuwenclaw.agentserver.skill_manager import enabled_skills_from_environ
    monkeypatch.setenv("ENABLED_SKILLS", "bash,python")
    assert enabled_skills_from_environ() == "bash,python"


# ---------------------------------------------------------------------------
# 21. get_installed_plugins 返回拷贝测试
# ---------------------------------------------------------------------------


def test_get_installed_plugins_returns_copy(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    manager._add_installed_plugin({"name": "test", "marketplace": "test"})
    plugins = manager.get_installed_plugins()
    assert isinstance(plugins, list)
    assert len(plugins) == 1
    plugins.clear()
    assert len(manager.get_installed_plugins()) == 1


# ---------------------------------------------------------------------------
# 22. _parse_comma_separated_string 测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("", []),
        ("  ", []),
        ("a", ["a"]),
        ("a,b,c", ["a", "b", "c"]),
        ("a; b; c", ["a", "b", "c"]),
        ("a,,b", ["a", "b"]),
    ],
)
def test_parse_comma_separated_string(raw, expected):
    from jiuwenclaw.agentserver.skill_manager import _parse_comma_separated_string
    assert _parse_comma_separated_string(raw) == expected


# ---------------------------------------------------------------------------
# 23. resolve_string_or_list_config 测试
# ---------------------------------------------------------------------------


def test_resolve_string_or_list_config_list():
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config
    assert resolve_string_or_list_config(["a", "b"]) == ["a", "b"]


def test_resolve_string_or_list_config_string():
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config
    assert resolve_string_or_list_config("a,b") == ["a", "b"]


def test_resolve_string_or_list_config_none():
    from jiuwenclaw.agentserver.skill_manager import resolve_string_or_list_config
    assert resolve_string_or_list_config(None) == []


# ---------------------------------------------------------------------------
# 24. _skillnet_proxy_mapping 测试
# ---------------------------------------------------------------------------


def test_skillnet_proxy_mapping_no_proxy(monkeypatch):
    from jiuwenclaw.agentserver.skill_manager import _skillnet_proxy_mapping
    monkeypatch.delenv("FREE_SEARCH_PROXY_URL", raising=False)
    assert _skillnet_proxy_mapping() == {}


def test_skillnet_proxy_mapping_with_proxy(monkeypatch):
    from jiuwenclaw.agentserver.skill_manager import _skillnet_proxy_mapping
    monkeypatch.setenv("FREE_SEARCH_PROXY_URL", "http://proxy:8080")
    result = _skillnet_proxy_mapping()
    assert result == {"http": "http://proxy:8080", "https": "http://proxy:8080"}


# ---------------------------------------------------------------------------
# 25. _safe_extract_tar_to_dir 安全测试
# ---------------------------------------------------------------------------


def test_safe_extract_tar_rejects_symlink(tmp_path):
    import io
    import tarfile

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    tar_buf.seek(0)

    tar_path = tmp_path / "evil.tar.gz"
    tar_path.write_bytes(tar_buf.read())

    with pytest.raises(RuntimeError, match="链接文件|归档包含非法"):
        SkillManager._safe_extract_tar_to_dir(tar_path, tmp_path / "dest")


# ---------------------------------------------------------------------------
# 26. handle_skills_list 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_skills_list_basic(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    skill_dir = tmp_path / "workspace" / "skills" / "list-test"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: list-test\nversion: 1.0.0\n---\nbody\n", encoding="utf-8"
    )

    result = await manager.handle_skills_list({})
    assert "skills" in result
    names = [s.get("name") for s in result["skills"]]
    assert "list-test" in names


# ---------------------------------------------------------------------------
# 27. handle_skills_installed 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_skills_installed(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    manager._add_installed_plugin({
        "name": "test-plugin",
        "marketplace": "test-market",
        "version": "1.0.0",
        "commit": "abc123",
    })

    result = await manager.handle_skills_installed({})
    assert "plugins" in result
    assert len(result["plugins"]) == 1
    plugin = result["plugins"][0]
    assert plugin["plugin_name"] == "test-plugin"
    assert plugin["spec"] == "test-plugin@test-market"
    assert plugin["marketplace"] == "test-market"


# ---------------------------------------------------------------------------
# 28. handle_skills_marketplace_list 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_skills_marketplace_list(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    manager._add_marketplace({
        "name": "test-market",
        "url": "https://example.com/repo.git",
        "enabled": True,
    })

    result = await manager.handle_skills_marketplace_list({})
    assert "marketplaces" in result
    assert len(result["marketplaces"]) == 1
    assert result["marketplaces"][0]["name"] == "test-market"


# ---------------------------------------------------------------------------
# 29. handle_skills_marketplace_remove 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_skills_marketplace_remove(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    manager._add_marketplace({
        "name": "test-market",
        "url": "https://example.com/repo.git",
    })

    result = await manager.handle_skills_marketplace_remove({"name": "test-market"})
    assert result["success"] is True

    result2 = await manager.handle_skills_marketplace_remove({"name": "test-market"})
    assert result2["success"] is False


# ---------------------------------------------------------------------------
# 30. handle_skills_uninstall 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_skills_uninstall_not_found(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    result = await manager.handle_skills_uninstall({"name": "nonexistent"})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_handle_skills_uninstall_rejects_traversal(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    result = await manager.handle_skills_uninstall({"name": "../etc"})
    assert result["success"] is False
