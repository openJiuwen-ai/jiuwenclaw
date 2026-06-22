# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64

from jiuwenswarm.gateway.media_attachments import normalize_chat_media_attachments


def test_normalize_chat_media_attachments_persists_images(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.media_attachments.get_agent_sessions_dir",
        lambda: tmp_path / "sessions",
    )
    image_bytes = b"\x89PNG\r\n\x1a\nfake"
    params = {
        "content": "这是什么？",
        "media_items": [
            {
                "type": "image",
                "mimeType": "image/png",
                "filename": "../screen shot.png",
                "base64Data": base64.b64encode(image_bytes).decode("ascii"),
            }
        ],
    }

    normalize_chat_media_attachments(params, "sess-win-mac")

    content = params.get("content")
    media_items = params.get("media_items")
    files = params.get("files")
    uploaded_images = files.get("uploaded_images") if isinstance(files, dict) else None

    assert isinstance(content, str)
    assert isinstance(media_items, list)
    assert isinstance(uploaded_images, list)
    assert params.get("query") == content
    assert "visual_question_answering" in content
    assert media_items[0].get("filename") == "screen_shot.png"
    stored_path = tmp_path / "sessions" / "sess-win-mac" / "uploads" / "screen_shot.png"
    assert stored_path.read_bytes() == image_bytes
    assert uploaded_images[0].get("path") == str(stored_path)


def test_normalize_chat_media_attachments_drops_invalid_images(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.gateway.media_attachments.get_agent_sessions_dir",
        lambda: tmp_path / "sessions",
    )
    params = {
        "content": "hello",
        "media_items": [
            {
                "type": "image",
                "mimeType": "image/png",
                "filename": "bad.png",
                "base64Data": "not-base64",
            }
        ],
    }

    normalize_chat_media_attachments(params, "sess-1")

    assert params == {"content": "hello"}
