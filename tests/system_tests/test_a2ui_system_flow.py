# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System-level coverage for the A2UI response flow."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.system]


VALID_A2UI_RESPONSE = """<a2ui-json>
[
  {
    "beginRendering": {
      "surfaceId": "system-test",
      "root": "root"
    }
  },
  {
    "surfaceUpdate": {
      "surfaceId": "system-test",
      "components": [
        {
          "id": "root",
          "component": {
            "Text": {
              "text": {
                "literalString": "System test A2UI content"
              }
            }
          }
        }
      ]
    }
  }
]
</a2ui-json>"""


@pytest.mark.asyncio
async def test_a2ui_system_flow_accepts_event_and_valid_response(monkeypatch):
    """Verify the A2UI host flow across config, prompt, event, and response."""
    from jiuwenswarm.server.runtime.a2ui.config import get_a2ui_config
    from jiuwenswarm.server.runtime.a2ui.integration import build_user_prompt_if_a2ui_event
    from jiuwenswarm.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer
    from jiuwenswarm.server.runtime.a2ui.runtime.prompt import build_a2ui_prompt_section
    from jiuwenswarm.server.runtime.a2ui.protocol import get_protocol_spec

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    config = get_a2ui_config({"a2ui": {"enabled": True}})
    prompt_section = build_a2ui_prompt_section("en")
    client_event = {
        "type": "a2ui.client_event",
        "event": {
            "userAction": {
                "name": "submit_selection",
                "surfaceId": "system-test",
                "sourceComponentId": "submit",
                "context": {"selected": "alpha"},
            }
        },
    }

    client_prompt = build_user_prompt_if_a2ui_event(
        client_event,
        channel="web",
        language="en",
    )
    finalized = await A2UIResponseFinalizer().finalize(
        VALID_A2UI_RESPONSE,
        user_query="show system test result",
        request_id="system-a2ui-valid-response",
        repair_call=lambda _: pytest.fail("valid A2UI response must not be repaired"),
    )
    validation = get_protocol_spec().validate_response(finalized)

    assert config.enabled is True
    assert "<a2ui-json>" in prompt_section
    assert "browser_preflight_submit" in prompt_section
    assert "hotel_option_select" in prompt_section
    assert "hotel_payment_confirm" in prompt_section
    assert "Do not ask for those missing browser-task details" in prompt_section
    assert "ask_user tool" in prompt_section
    assert client_prompt is not None
    assert "submit_selection" in client_prompt
    assert "alpha" in client_prompt
    assert finalized == VALID_A2UI_RESPONSE
    assert validation.valid is True


def test_a2ui_browser_preflight_event_prompts_browser_subagent(monkeypatch):
    """Browser preflight submissions should continue into the browser subagent path."""
    from jiuwenswarm.server.runtime.a2ui.integration import build_user_prompt_if_a2ui_event

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    client_event = {
        "type": "a2ui.client_event",
        "event": {
            "userAction": {
                "name": "browser_preflight_submit",
                "surfaceId": "browser-preflight",
                "sourceComponentId": "submit",
                "context": {
                    "original_query": "Book a hotel in Shanghai",
                    "task_type": "hotel",
                    "next_action": "run_browser_agent",
                    "city": "Shanghai",
                    "check_in": "2026-07-01",
                    "check_out": "2026-07-03",
                    "must_confirm_before_payment": True,
                },
            }
        },
    }

    client_prompt = build_user_prompt_if_a2ui_event(
        client_event,
        channel="web",
        language="en",
    )

    assert client_prompt is not None
    assert "browser task preflight submission" in client_prompt
    assert "spawn_sub_agent" in client_prompt
    assert "browser_agent" in client_prompt
    assert "Book a hotel in Shanghai" in client_prompt
    assert "must_confirm_before_payment" in client_prompt


def test_a2ui_hotel_option_select_continues_current_browser_state(monkeypatch):
    """Hotel candidate selection should not be interpreted as a fresh hotel search."""
    from jiuwenswarm.server.runtime.a2ui.integration import build_user_prompt_if_a2ui_event

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    client_event = {
        "type": "a2ui.client_event",
        "event": {
            "userAction": {
                "name": "hotel_option_select",
                "surfaceId": "hotel-candidates",
                "sourceComponentId": "hotel-2-select",
                "context": {
                    "original_query": "Book a hotel in Shanghai",
                    "task_type": "hotel",
                    "next_action": "continue_hotel_booking",
                    "city": "Shanghai",
                    "check_in": "2026-07-01",
                    "check_out": "2026-07-03",
                    "guest_count": 2,
                    "hotel_name": "Example Riverside Hotel",
                    "candidate_index": 2,
                    "detail_url": "https://example.test/hotel/2",
                },
            }
        },
    }

    client_prompt = build_user_prompt_if_a2ui_event(
        client_event,
        channel="web",
        language="en",
    )

    assert client_prompt is not None
    assert "hotel candidate selection" in client_prompt
    assert "spawn_sub_agent" in client_prompt
    assert "browser_agent" in client_prompt
    assert "current browser state/session" in client_prompt
    assert "Do not repeat the broad hotel search" in client_prompt
    assert "hotel_payment_confirm" in client_prompt
    assert "Example Riverside Hotel" in client_prompt


def test_a2ui_hotel_payment_confirmation_is_guarded(monkeypatch):
    """Final payment confirmation should verify the visible order before proceeding."""
    from jiuwenswarm.server.runtime.a2ui.integration import build_user_prompt_if_a2ui_event

    monkeypatch.setenv("JIUWENSWARM_A2UI_ENABLED", "true")

    client_event = {
        "type": "a2ui.client_event",
        "event": {
            "userAction": {
                "name": "hotel_payment_confirm",
                "surfaceId": "hotel-payment",
                "sourceComponentId": "confirm",
                "context": {
                    "task_type": "hotel",
                    "next_action": "confirm_hotel_payment",
                    "hotel_name": "Example Riverside Hotel",
                    "total_price": "CNY 1288",
                },
            }
        },
    }

    client_prompt = build_user_prompt_if_a2ui_event(
        client_event,
        channel="web",
        language="en",
    )

    assert client_prompt is not None
    assert "final hotel payment confirmation" in client_prompt
    assert "verify that the selected hotel" in client_prompt
    assert "current browser state match the context" in client_prompt
    assert "Example Riverside Hotel" in client_prompt
