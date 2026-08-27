"""Compact inbound logging for large catalog frames."""

from __future__ import annotations

import json

from jiuwenswarm.server import wire_parse as wp


def test_parse_inbound_catalog_logs_summary_not_full_payload(monkeypatch) -> None:
    messages: list[str] = []

    def _capture(msg, *args, **_kwargs):
        messages.append(msg % args if args else str(msg))

    monkeypatch.setattr(wp.logger, "info", _capture)
    payload = {
        "request_id": "sync_agents_test",
        "channel_id": "officeclaw",
        "req_method": "sync_agents_configs",
        "params": {
            "revision": "abc",
            "agents": [
                {"agent_id": "office", "config": {"k": "v" * 100}, "env": {}, "runtime": {}},
                {"agent_id": "agentteam", "config": {"k": "v" * 100}, "env": {}, "runtime": {}},
            ],
        },
        "is_stream": False,
    }
    wp.parse_inbound(json.dumps(payload))
    text = "\n".join(messages)
    assert "request_id=sync_agents_test" in text
    assert "method=sync_agents_configs" in text
    assert "agents=2" in text
    assert "'config':" not in text


def test_parse_inbound_small_chat_still_logs_masked_dict(monkeypatch) -> None:
    messages: list[str] = []

    def _capture(msg, *args, **_kwargs):
        messages.append(msg % args if args else str(msg))

    monkeypatch.setattr(wp.logger, "info", _capture)
    payload = {
        "request_id": "chat-1",
        "channel_id": "officeclaw",
        "req_method": "chat.send",
        "params": {"query": "hello-world-query"},
        "is_stream": True,
    }
    wp.parse_inbound(json.dumps(payload))
    text = "\n".join(messages)
    assert "Inbound raw payload:" in text
    assert "hello-world-query" not in text
    assert "******" in text
