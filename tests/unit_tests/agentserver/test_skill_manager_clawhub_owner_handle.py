import json

import pytest

from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager


class _FakeSearchResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    @staticmethod
    def raise_for_status():
        return None


class _FakeSearchClient:
    def __init__(self, results: list[dict]):
        self._results = results

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, params, headers):
        assert url == "https://clawhub.ai/api/v1/search"
        assert "q" in params
        return _FakeSearchResponse({"results": self._results})


class _FakeDownloadResponse:
    def __init__(self, status_code: int, text: str = "", content: bytes = b""):
        self.status_code = status_code
        self.text = text
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                message=f"Client error '{self.status_code}'",
                request=httpx.Request("GET", "https://clawhub.ai/api/v1/download"),
                response=self,
            )


class _FakeDownloadClient:
    def __init__(self, status_code: int = 200, text: str = "", content: bytes = b""):
        self._status_code = status_code
        self._text = text
        self._content = content
        self._captured_params: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, params, headers):
        assert url == "https://clawhub.ai/api/v1/download"
        self._captured_params = params
        return _FakeDownloadResponse(
            status_code=self._status_code,
            text=self._text,
            content=self._content,
        )

    def get_captured_params(self) -> dict | None:
        return self._captured_params


@pytest.mark.asyncio
async def test_clawhub_search_returns_owner_handle(tmp_path, monkeypatch):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    fake_results = [
        {
            "slug": "ppt-generator",
            "displayName": "PPT Generator",
            "summary": "Generate PPT",
            "version": "1.0.0",
            "updatedAt": 1000000,
            "ownerHandle": "kirkraman",
        },
        {
            "slug": "ppt-generator",
            "displayName": "PPT Gen 2",
            "summary": "Another PPT",
            "version": "2.0.0",
            "updatedAt": 2000000,
            "ownerHandle": "wwlyzzyorg",
        },
    ]

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: _FakeSearchClient(fake_results),
    )

    result = await manager.handle_skills_clawhub_search({"q": "ppt-generator", "limit": 50})

    assert result["success"] is True
    assert len(result["skills"]) == 2
    assert result["skills"][0]["owner_handle"] == "kirkraman"
    assert result["skills"][1]["owner_handle"] == "wwlyzzyorg"


@pytest.mark.asyncio
async def test_clawhub_search_owner_handle_missing_is_empty_string(tmp_path, monkeypatch):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    fake_results = [
        {
            "slug": "unique-skill",
            "displayName": "Unique Skill",
            "summary": "Only one publisher",
            "version": "1.0.0",
            "updatedAt": 1000000,
        },
    ]

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: _FakeSearchClient(fake_results),
    )

    result = await manager.handle_skills_clawhub_search({"q": "unique-skill"})

    assert result["success"] is True
    assert result["skills"][0]["owner_handle"] == ""


@pytest.mark.asyncio
async def test_clawhub_download_passes_owner_handle_to_api(tmp_path, monkeypatch):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    fake_client = _FakeDownloadClient(status_code=400, text="error")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: fake_client,
    )

    await manager.handle_skills_clawhub_download(
        {"slug": "ppt-generator", "owner_handle": "kirkraman"}
    )

    captured = fake_client.get_captured_params()
    assert captured is not None
    assert captured["slug"] == "ppt-generator"
    assert captured["ownerHandle"] == "kirkraman"


@pytest.mark.asyncio
async def test_clawhub_download_without_owner_handle_omits_param(tmp_path, monkeypatch):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    fake_client = _FakeDownloadClient(status_code=400, text="error")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: fake_client,
    )

    await manager.handle_skills_clawhub_download({"slug": "unique-skill"})

    captured = fake_client.get_captured_params()
    assert captured is not None
    assert captured["slug"] == "unique-skill"
    assert "ownerHandle" not in captured


@pytest.mark.asyncio
async def test_clawhub_download_empty_owner_handle_omits_param(tmp_path, monkeypatch):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    fake_client = _FakeDownloadClient(status_code=400, text="error")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: fake_client,
    )

    await manager.handle_skills_clawhub_download(
        {"slug": "unique-skill", "owner_handle": ""}
    )

    captured = fake_client.get_captured_params()
    assert captured is not None
    assert "ownerHandle" not in captured


@pytest.mark.asyncio
async def test_install_skill_parses_owner_handle_from_identifier(tmp_path, monkeypatch):
    from jiuwenswarm.agents.harness.common.tools.skill_toolkits import SkillToolkit

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    toolkit = SkillToolkit(manager=manager)

    fake_client = _FakeDownloadClient(status_code=400, text="error")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: fake_client,
    )

    result = await toolkit.install_skill(
        identifier="kirkraman/ppt-generator",
        source="clawhub",
    )

    captured = fake_client.get_captured_params()
    assert captured is not None
    assert captured["slug"] == "ppt-generator"
    assert captured["ownerHandle"] == "kirkraman"


@pytest.mark.asyncio
async def test_install_skill_plain_slug_no_owner_handle(tmp_path, monkeypatch):
    from jiuwenswarm.agents.harness.common.tools.skill_toolkits import SkillToolkit

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    await manager.handle_skills_clawhub_set_token({"token": "test-token"})

    toolkit = SkillToolkit(manager=manager)

    fake_client = _FakeDownloadClient(status_code=400, text="error")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.httpx.AsyncClient",
        lambda timeout: fake_client,
    )

    result = await toolkit.install_skill(
        identifier="unique-skill",
        source="clawhub",
    )

    captured = fake_client.get_captured_params()
    assert captured is not None
    assert captured["slug"] == "unique-skill"
    assert "ownerHandle" not in captured
