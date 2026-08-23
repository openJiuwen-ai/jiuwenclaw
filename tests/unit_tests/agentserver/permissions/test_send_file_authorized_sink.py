# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exact send grants activate stable verified delivery without changing routing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions.generated_artifact_delivery import (
    clear_send_file_execution_grant,
    create_send_file_execution_grant,
    current_send_file_execution_grant,
    publish_send_file_execution_grant,
)
from jiuwenswarm.agents.harness.common.tools import send_file_to_user as send_module
from jiuwenswarm.agents.harness.common.tools.send_file_to_user import SendFileToolkit
from jiuwenswarm.agents.harness.common.tools.verified_download_assets import (
    VerifiedDownloadAssetOwner,
)
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as adapter_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


@pytest.fixture(autouse=True)
def _clear_runtime_state() -> None:
    clear_send_file_execution_grant()
    send_module._SENT_FILE_PATHS_BY_SESSION.clear()
    yield
    clear_send_file_execution_grant()
    send_module._SENT_FILE_PATHS_BY_SESSION.clear()


def _toolkit(
    *,
    require_authorization: bool,
    asset_owner: VerifiedDownloadAssetOwner | None = None,
) -> SendFileToolkit:
    return SendFileToolkit(
        request_id="request-a",
        session_id="session-a",
        channel_id="web",
        user_id="user-a",
        require_execution_authorization=require_authorization,
        asset_owner=asset_owner,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_permission", [False, True])
async def test_adapter_activates_sink_authorization_only_for_auto_permission(
    auto_permission: bool,
) -> None:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._ensure_cron_tools_registered = MagicMock()
    adapter._resolve_prompt_channel = MagicMock(return_value="web")
    adapter._enable_auto_permission = auto_permission
    adapter._project_dir = None
    adapter._tool_owner_id = MagicMock(return_value="owner-a")
    adapter._register_agent_owned_tool = MagicMock()
    adapter._instance = SimpleNamespace(
        ability_manager=MagicMock(
            list=MagicMock(return_value=[]),
            add=MagicMock(),
        )
    )
    toolkit = MagicMock()
    toolkit.get_tools.return_value = []

    with patch.object(
        adapter_module,
        "get_config",
        return_value={"channels": {"web": {"send_file_allowed": True}}},
    ), patch.object(
        adapter_module,
        "SendFileToolkit",
        return_value=toolkit,
    ) as toolkit_type:
        await adapter._update_session_tools(
            session_id="session-a",
            request_id="request-a",
            channel_id="web",
        )

    assert (
        toolkit_type.call_args.kwargs["require_execution_authorization"]
        is auto_permission
    )


@pytest.mark.asyncio
async def test_auto_permission_send_consumes_exact_grant_and_stages_verified_download(
    tmp_path,
) -> None:
    source = tmp_path / "report.md"
    source.write_text("approved", encoding="utf-8")
    publish_send_file_execution_grant(
        create_send_file_execution_grant(
            (source,),
            target_channels=("web",),
        )
    )
    server = MagicMock()
    server.send_push = AsyncMock()
    owner = VerifiedDownloadAssetOwner(
        root=tmp_path / "assets",
        start_sweeper=False,
    )

    with patch(
        "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
        return_value=server,
    ), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ):
        result = await _toolkit(
            require_authorization=True,
            asset_owner=owner,
        ).send_file(
            source.as_posix(),
            target_channels=("web",),
        )

    assert result == "成功发送 1 个文件"
    assert current_send_file_execution_grant() is None
    payload = server.send_push.await_args.args[0]["payload"]["files"][0]
    assert payload["path"] != source.as_posix()
    assert payload["download_url"].endswith("&user_id=user-a")
    assert payload["download_token"]
    sidecar = next((tmp_path / "assets").glob("*.json"))
    claims = json.loads(sidecar.read_text(encoding="utf-8"))
    assert claims["state"] == "committed"
    assert claims["sealed_path"] == payload["path"]
    assert Path(payload["path"]).read_text(encoding="utf-8") == "approved"


@pytest.mark.asyncio
async def test_auto_permission_send_rejects_missing_or_mismatched_grant(
    tmp_path,
) -> None:
    source = tmp_path / "report.md"
    source.write_text("approved", encoding="utf-8")
    toolkit = _toolkit(require_authorization=True)

    missing = await toolkit.send_file(source.as_posix())
    publish_send_file_execution_grant(
        create_send_file_execution_grant(
            (source,),
            target_channels=("web",),
        )
    )
    mismatch = await toolkit.send_file(
        source.as_posix(),
        target_channels=("feishu",),
    )
    replay = await toolkit.send_file(
        source.as_posix(),
        target_channels=("web",),
    )

    assert "send_file_execution_grant_missing" in missing
    assert "send_file_execution_grant_mismatch" in mismatch
    assert "send_file_execution_grant_missing" in replay
    assert current_send_file_execution_grant() is None


@pytest.mark.asyncio
async def test_ordinary_send_does_not_require_auto_permission_grant(
    tmp_path,
) -> None:
    source = tmp_path / "report.md"
    source.write_text("ordinary", encoding="utf-8")
    server = MagicMock()
    server.send_push = AsyncMock()

    with patch(
        "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
        return_value=server,
    ), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), patch(
        "jiuwenswarm.agents.harness.common.tools.web_file_download.build_file_download_info",
        return_value={
            "size": 8,
            "mime_type": "text/markdown",
            "download_url": "/existing-download",
            "download_token": "existing-token",
        },
    ):
        result = await _toolkit(require_authorization=False).send_file(
            source.as_posix()
        )

    assert result == "成功发送 1 个文件"
    server.send_push.assert_awaited_once()


def test_runtime_context_tracks_auto_permission_activation_explicitly() -> None:
    toolkit = _toolkit(require_authorization=True)

    toolkit.update_runtime_context(
        request_id="request-b",
        session_id="session-b",
        channel_id="web",
    )
    assert toolkit._require_execution_authorization is True

    toolkit.update_runtime_context(
        request_id="request-c",
        session_id="session-c",
        channel_id="web",
        require_execution_authorization=False,
    )
    assert toolkit._require_execution_authorization is False
