from __future__ import annotations

from types import SimpleNamespace

from jiuwenclaw.agentserver.tools.image_gen_tools import (
    _iter_response_image_items,
    _save_generated_images,
)


def test_iter_response_image_items_openjiuwen_style() -> None:
    response = SimpleNamespace(
        images=["https://example.com/a.png", "https://example.com/b.png"],
        images_base64=[],
    )
    items = _iter_response_image_items(response)
    assert len(items) == 2
    assert items[0]["url"] == "https://example.com/a.png"


def test_iter_response_image_items_dashscope_nested() -> None:
    response = {
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"image": "https://dashscope.example/out.png"},
                        ]
                    }
                }
            ]
        }
    }
    items = _iter_response_image_items(response)
    assert len(items) == 1
    assert items[0]["url"] == "https://dashscope.example/out.png"


def test_save_generated_images_from_url_strings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.image_gen_tools.resolve_tenant_agent_workspace_dir",
        lambda *args, **kwargs: tmp_path,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.image_gen_tools.get_effective_request_workspace_dir",
        lambda: None,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.image_gen_tools._download_image",
        lambda url, dest, timeout=120: dest.write_bytes(b"png-bytes"),
    )
    response = SimpleNamespace(images=["https://example.com/x.png"], images_base64=[])
    paths = _save_generated_images(response, prompt="test prompt")
    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].read_bytes() == b"png-bytes"
    assert paths[0].parent == (tmp_path / "generated_images").resolve()


def test_save_generated_images_uses_effective_project_dir(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "workspace" / "20260525143022"
    expected_dir = project_dir / "generated_images"
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.image_gen_tools.get_effective_request_workspace_dir",
        lambda: str(project_dir),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.image_gen_tools._download_image",
        lambda url, dest, timeout=120: dest.write_bytes(b"png-bytes"),
    )
    response = SimpleNamespace(images=["https://example.com/x.png"], images_base64=[])
    paths = _save_generated_images(response, prompt="hero cat")
    assert len(paths) == 1
    assert paths[0].parent == expected_dir.resolve()
    assert paths[0].exists()
