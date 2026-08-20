# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ExpertPackageSource 抽象 / HTTP 实现 / 本地 override / 包校验 / metadata expert_id 测试。

mock 仓库契约即未来正式仓库的替换验收依据。
"""

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

import jiuwenswarm.server.runtime.session.session_metadata as sm
from jiuwenswarm.server.runtime.expert import expert_store as es


def _make_package(
        root: Path,
        name: str,
        *,
        rails: bool = False,
        subagents: bool = False,
        model: bool = False,
        persona: bool = True,
        tools: list[str] | None = None,
        card_id: str | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    manifest: dict = {
        "packageType": "agent_template",
        "agentCard": {
            "id": card_id if card_id is not None else name,
            "name": f"{name} 专家",
            "description": "描述",
        },
        "persona": {"dir": "agents"},
        "metadata": {"tags": ["test"], "profession": "头衔"},
    }
    if rails:
        manifest["rails"] = [{"file": "rails/x.py", "class": "X"}]
    if subagents:
        manifest["subagents"] = [{"dir": "subagents/x"}]
    if model:
        manifest["model"] = {"name": "ignored"}
    if persona:
        (pkg / "agents").mkdir()
        (pkg / "agents" / "00-identity.md").write_text("# 人设", encoding="utf-8")
    if tools:
        for tool_file in tools:
            tool_path = pkg / tool_file
            tool_path.parent.mkdir(parents=True, exist_ok=True)
            tool_path.write_text("# tool", encoding="utf-8")
        manifest["tools"] = [{"file": f} for f in tools]
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return pkg


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test-repo"
    )


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "experts_cache"
    monkeypatch.setattr(es, "get_expert_cache_dir", lambda: target)
    return target


@pytest.mark.asyncio
async def test_list_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/packages"
        return httpx.Response(
            200,
            json={
                "experts": [
                    {
                        "id": "security-reviewer",
                        "name": "安全评审专家",
                        "description": "描述",
                        "available": True,
                        "unavailable_reason": "",
                        "tags": ["security"],
                        "type": "agent",
                        "metadata": {"profession": "安全架构师"},
                        "avatar_url": "http://repo/api/v1/packages/security-reviewer/avatar",
                    }
                ]
            },
        )

    source = es.HttpRepoExpertPackageSource(client=_mock_client(handler))
    (summary,) = await source.list()
    assert summary.id == "security-reviewer"
    assert summary.source == "repo"
    assert summary.available is True
    assert summary.metadata["profession"] == "安全架构师"
    assert summary.avatar_url.endswith("/security-reviewer/avatar")


@pytest.mark.asyncio
async def test_list_repo_down_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    source = es.HttpRepoExpertPackageSource(client=_mock_client(handler))
    with pytest.raises(es.ExpertRepoUnavailable):
        await source.list()


@pytest.mark.asyncio
async def test_list_non_200_raises_unavailable() -> None:
    source = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(500))
    )
    with pytest.raises(es.ExpertRepoUnavailable):
        await source.list()


@pytest.mark.asyncio
async def test_fetch_ok_extracts_to_cache(cache_dir: Path) -> None:
    payload = _zip_bytes(
        {"manifest.json": "{}", "agents/00-identity.md": "# 人设"}
    )
    source = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(200, content=payload))
    )
    package_dir = await source.fetch("security-reviewer")
    assert package_dir == cache_dir / "security-reviewer"
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "agents" / "00-identity.md").read_text(
        encoding="utf-8"
    ) == "# 人设"


@pytest.mark.asyncio
async def test_fetch_404_raises_not_found() -> None:
    source = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(404))
    )
    with pytest.raises(es.ExpertNotFound):
        await source.fetch("nope")


@pytest.mark.asyncio
async def test_fetch_down_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    source = es.HttpRepoExpertPackageSource(client=_mock_client(handler))
    with pytest.raises(es.ExpertRepoUnavailable):
        await source.fetch("security-reviewer")


@pytest.mark.asyncio
async def test_fetch_rejects_zip_slip(cache_dir: Path) -> None:
    payload = _zip_bytes({"../evil.txt": "x"})
    source = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(200, content=payload))
    )
    with pytest.raises(es.ExpertRepoUnavailable):
        await source.fetch("security-reviewer")
    assert not (cache_dir.parent / "evil.txt").exists()


@pytest.mark.asyncio
async def test_fetch_replaces_stale_cache(cache_dir: Path) -> None:
    stale = cache_dir / "security-reviewer"
    (stale / "old").mkdir(parents=True)
    (stale / "old" / "stale.txt").write_text("stale", encoding="utf-8")
    payload = _zip_bytes({"manifest.json": "{}"})
    source = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(200, content=payload))
    )
    package_dir = await source.fetch("security-reviewer")
    assert not (package_dir / "old").exists(), "旧缓存目录应先清空再解压"
    assert (package_dir / "manifest.json").is_file()


@pytest.mark.asyncio
async def test_local_override_wins_on_fetch(tmp_path: Path) -> None:
    local_dir = tmp_path / "experts"
    _make_package(local_dir, "security-reviewer")
    local = es.LocalDirExpertPackageSource(experts_dir=local_dir)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("local 命中时不应访问仓库")

    repo = es.HttpRepoExpertPackageSource(client=_mock_client(handler))
    chain = es.ChainExpertPackageSource([local, repo])
    assert await chain.fetch("security-reviewer") == local_dir / "security-reviewer"


@pytest.mark.asyncio
async def test_chain_fetch_falls_back_to_repo(
        tmp_path: Path, cache_dir: Path
) -> None:
    local = es.LocalDirExpertPackageSource(experts_dir=tmp_path / "experts")
    payload = _zip_bytes({"manifest.json": "{}"})
    repo = es.HttpRepoExpertPackageSource(
        client=_mock_client(lambda request: httpx.Response(200, content=payload))
    )
    chain = es.ChainExpertPackageSource([local, repo])
    assert await chain.fetch("security-reviewer") == cache_dir / "security-reviewer"


@pytest.mark.asyncio
async def test_chain_list_merges_local_over_repo(tmp_path: Path) -> None:
    local_dir = tmp_path / "experts"
    _make_package(local_dir, "security-reviewer")
    local = es.LocalDirExpertPackageSource(experts_dir=local_dir)
    repo = es.HttpRepoExpertPackageSource(
        client=_mock_client(
            lambda request: httpx.Response(
                200,
                json={
                    "experts": [
                        {"id": "security-reviewer", "name": "仓库版", "available": True},
                        {"id": "other", "name": "另一个", "available": True},
                    ]
                },
            )
        )
    )
    chain = es.ChainExpertPackageSource([local, repo])
    summaries = {s.id: s for s in await chain.list()}
    assert summaries["security-reviewer"].source == "local", "同名时 local 覆盖 repo"
    assert summaries["other"].source == "repo"


@pytest.mark.asyncio
async def test_chain_list_raises_when_all_empty_and_repo_down(tmp_path: Path) -> None:
    local = es.LocalDirExpertPackageSource(experts_dir=tmp_path / "experts")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    repo = es.HttpRepoExpertPackageSource(client=_mock_client(handler))
    chain = es.ChainExpertPackageSource([local, repo])
    with pytest.raises(es.ExpertRepoUnavailable):
        await chain.list()


def test_validate_ok_with_tools_warning_free(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path, "security-reviewer", tools=["tools/scan.py"])
    assert es.validate_expert_package(pkg) == []


def test_validate_model_field_warns(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path, "security-reviewer", model=True)
    warnings = es.validate_expert_package(pkg)
    assert any("model" in w for w in warnings)


@pytest.mark.parametrize(
    "kwargs, reason_part",
    [
        ({"rails": True}, "rails"),
        ({"subagents": True}, "subagents"),
        ({"persona": False}, "persona"),
        ({"card_id": "other-id"}, "不一致"),
    ],
)
def test_validate_rejects_invalid(tmp_path: Path, kwargs: dict, reason_part: str) -> None:
    pkg = _make_package(tmp_path, "security-reviewer", **kwargs)
    with pytest.raises(es.InvalidExpertPackage, match=reason_part):
        es.validate_expert_package(pkg)


def test_validate_rejects_missing_tool_file(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path, "security-reviewer")
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    manifest["tools"] = [{"file": "tools/missing.py"}]
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(es.InvalidExpertPackage, match="tools"):
        es.validate_expert_package(pkg)


def test_validate_rejects_missing_manifest(tmp_path: Path) -> None:
    (tmp_path / "empty-pkg").mkdir()
    with pytest.raises(es.InvalidExpertPackage, match="manifest"):
        es.validate_expert_package(tmp_path / "empty-pkg")


def test_validate_avatar_declared_but_missing(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path, "security-reviewer")
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    manifest["metadata"]["avatar"] = "avatars/missing.png"
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(es.InvalidExpertPackage, match="头像"):
        es.validate_expert_package(pkg)


def test_validate_avatar_ok(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path, "security-reviewer")
    (pkg / "avatars").mkdir()
    (pkg / "avatars" / "expert.png").write_bytes(b"\x89PNG")
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    manifest["metadata"]["avatar"] = "avatars/expert.png"
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    assert es.validate_expert_package(pkg) == []


@pytest.fixture
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "sessions"
    target.mkdir()
    monkeypatch.setattr(sm, "get_agent_sessions_dir", lambda: target)
    sm._METADATA_CACHE.clear()
    yield target
    sm._METADATA_CACHE.clear()


def test_metadata_init_defaults_expert_id_empty(sessions_dir: Path) -> None:
    sm.init_session_metadata(session_id="s1", channel_id="desktop")
    assert sm.get_session_metadata("s1", cache_bust=True)["expert_id"] == ""


def test_metadata_init_with_expert_id(sessions_dir: Path) -> None:
    sm.init_session_metadata(
        session_id="s2", channel_id="desktop", expert_id="security-reviewer"
    )
    assert (
            sm.get_session_metadata("s2", cache_bust=True)["expert_id"]
            == "security-reviewer"
    )


def test_metadata_update_and_clear_expert_id(sessions_dir: Path) -> None:
    sm.init_session_metadata(session_id="s3", channel_id="desktop")
    sm.update_session_metadata(
        session_id="s3", expert_id="security-reviewer", sync_write=True
    )
    assert (
            sm.get_session_metadata("s3", cache_bust=True)["expert_id"]
            == "security-reviewer"
    )
    sm.update_session_metadata(session_id="s3", expert_id="", sync_write=True)
    assert sm.get_session_metadata("s3", cache_bust=True)["expert_id"] == ""


def test_metadata_legacy_session_without_key(sessions_dir: Path) -> None:
    """旧会话 metadata.json 无 expert_id 键时读出默认 ""（无需迁移）。"""
    session_dir = sessions_dir / "legacy"
    session_dir.mkdir()
    (session_dir / "metadata.json").write_text(
        json.dumps({"session_id": "legacy", "title": "旧会话"}), encoding="utf-8"
    )
    assert sm.get_session_metadata("legacy", cache_bust=True)["expert_id"] == ""
