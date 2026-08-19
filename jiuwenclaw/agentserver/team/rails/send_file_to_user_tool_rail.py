# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register ``send_file_to_user`` on the team leader (leader-only).

Team mode bypasses ``_update_session_tools`` (interface_deep.py:8885-8919
returns before :9023), so the leader never received ``send_file_to_user``.
This rail mirrors ``_update_session_tools`` (interface_deep.py:5366-5381) and
``AskUserQuestionToolRail`` to register the tool on the leader's
``ability_manager`` at rail init. The leader then delivers files mid-stream
via the authoritative ``send_file_to_user -> _emit_chat_file -> chat.file``
path, BEFORE any stall/pause can suppress the stream-end soft-fallback.

The ``_registered`` flag is consumed by ``TeamWorkspaceReportPathRail`` to
gate the "call send_file_to_user" guidance on actual registration (so the
prompt never instructs the leader to call a tool it does not have).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.tools import SendFileToolkit

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


class SendFileToUserToolRail(DeepAgentRail):
    """Mount ``send_file_to_user`` on the team leader (leader-only)."""

    priority = 93  # just below AskUserQuestionToolRail(94)

    def __init__(
        self,
        *,
        request_id: str | None,
        session_id: str,
        channel: str,
        config: Any,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._session_id = session_id
        self._channel = str(channel or "web").strip() or "web"
        self._config = config
        self._tools: list[Any] = []
        self._registered = False  # consumed by TeamWorkspaceReportPathRail guidance gate

    def init(self, agent: "DeepAgent") -> None:
        if self._tools:
            return
        channel = self._channel
        channels = (self._config or {}).get("channels", {}) if isinstance(self._config, dict) else {}
        ch_cfg = channels.get(channel, {}) if isinstance(channels, dict) else {}
        send_file_enabled = bool(ch_cfg.get("send_file_allowed", False))
        # Mirror interface_deep.py:5366 exactly.
        send_file_channel_allowed = send_file_enabled or channel == "officeclaw"
        # Mirror interface_deep.py:5367 exactly.
        has_send_file_request_context = bool(self._request_id and self._session_id)
        if not (send_file_channel_allowed and has_send_file_request_context):
            logger.info(
                "[SendFileToUserToolRail] skip: gate not met channel=%s "
                "send_file_enabled=%s has_ctx=%s",
                channel,
                send_file_enabled,
                has_send_file_request_context,
            )
            return  # NOT registered -> guidance rail must NOT inject the prompt
        try:
            toolkit = SendFileToolkit(
                request_id=self._request_id,
                session_id=self._session_id,
                channel_id=channel,
                metadata=None,
            )
            for sf_tool in toolkit.get_tools():
                if not Runner.resource_mgr.get_tool(sf_tool.card.id):
                    Runner.resource_mgr.add_tool(sf_tool)
                agent.ability_manager.add(sf_tool.card)
                self._tools.append(sf_tool)
            self._registered = True
            logger.info(
                "[SendFileToUserToolRail] registered send_file_to_user for leader "
                "session_id=%s tools=%d",
                self._session_id,
                len(self._tools),
            )
        except Exception as exc:
            logger.warning("[SendFileToUserToolRail] register failed: %s", exc, exc_info=True)
            self._registered = False
            self._tools = []

    def uninit(self, agent: "DeepAgent") -> None:
        for sf_tool in self._tools:
            name = getattr(getattr(sf_tool, "card", None), "name", None)
            if name and hasattr(agent.ability_manager, "remove"):
                try:
                    agent.ability_manager.remove(name)
                except Exception:
                    logger.debug(
                        "[SendFileToUserToolRail] remove ability failed name=%s",
                        name,
                        exc_info=True,
                    )
        self._tools = []
        self._registered = False


__all__ = ["SendFileToUserToolRail"]
