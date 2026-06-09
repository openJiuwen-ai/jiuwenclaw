# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_core_agent_adapter_uses_a2ui_integration_boundary():
    source = (ROOT / "jiuwenswarm/server/runtime/agent_adapter/interface.py").read_text(encoding="utf-8")

    assert "jiuwenswarm.server.runtime.a2ui.config" not in source
    assert "jiuwenswarm.server.runtime.a2ui.runtime.response_finalization" not in source
    assert "jiuwenswarm.server.runtime.a2ui.integration" in source


def test_backend_a2ui_lives_under_server_runtime():
    assert not (ROOT / "jiuwenswarm/a2ui").exists()
    assert (ROOT / "jiuwenswarm/server/runtime/a2ui").is_dir()


def test_websocket_hook_keeps_a2ui_feature_outside_generic_transport():
    source = (ROOT / "jiuwenswarm/channels/web/frontend/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")

    assert "../features/a2ui/" not in source
    assert "sendA2UIClientEvent" not in source
    assert "sendStructuredChatContent" in source
