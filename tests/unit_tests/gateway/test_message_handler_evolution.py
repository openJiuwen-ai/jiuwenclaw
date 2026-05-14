from types import SimpleNamespace

import pytest

from jiuwenclaw.gateway.message_handler.message_handler import MessageHandler


class _FakeAgentClient:
    @staticmethod
    async def send_request(env):
        raise AssertionError("send_request should not be called in this unit test")

    @staticmethod
    async def send_request_stream(env):
        if False:
            yield env


class _TestMessageHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_TestMessageHandler":
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        return cls(_FakeAgentClient())

    def seed_pending_evolution_approval(
        self,
        session_id: str,
        request_id: str,
    ) -> None:
        marker = getattr(self, "_mark_pending_evolution_approval")
        marker(session_id, request_id)

    def seed_session_evolution_in_progress(self, session_id: str) -> None:
        marker = getattr(self, "_mark_session_evolution_in_progress")
        marker(session_id)

    def seed_queued_supplement_input(
        self,
        session_id: str,
        payload: dict[str, object],
    ) -> None:
        queued_inputs = getattr(self, "_queued_supplement_input")
        queued_inputs[session_id] = payload

    async def handle_evolution_chunk(
        self,
        chunk: SimpleNamespace,
        session_id: str,
        request_metadata: dict[str, object] | None = None,
    ) -> None:
        handler = getattr(self, "_handle_evolution_chunk")
        await handler(chunk, session_id, request_metadata)

    def finish_evolution_approval_if_current(
        self,
        session_id: str,
        answered_request_id: str,
    ) -> dict[str, object] | None:
        finisher = getattr(self, "_finish_evolution_approval_if_current")
        return finisher(session_id, answered_request_id)

    def pending_evolution_approval(self, session_id: str) -> str | None:
        approvals = getattr(self, "_pending_evolution_approval")
        return approvals.get(session_id)

    def has_session_evolution_in_progress(self, session_id: str) -> bool:
        checker = getattr(self, "_is_session_evolution_in_progress")
        return checker(session_id)

    def queued_supplement_input(self, session_id: str) -> dict[str, object] | None:
        queued_inputs = getattr(self, "_queued_supplement_input")
        return queued_inputs.get(session_id)

    def pop_user_message_nowait(self):
        user_messages = getattr(self, "_user_messages")
        return user_messages.get_nowait()


@pytest.mark.asyncio
async def test_handle_evolution_chunk_auto_accepts_previous_pending_approval():
    handler = _TestMessageHandler.create()
    handler.seed_pending_evolution_approval("sess-1", "team_skill_evolve_old")

    chunk = SimpleNamespace(
        channel_id="web",
        request_id="stream-1",
        payload={
            "event_type": "chat.ask_user_question",
            "request_id": "team_skill_evolve_new",
            "questions": [{"header": "x"}],
        },
    )

    await handler.handle_evolution_chunk(chunk, "sess-1", {"k": "v"})

    assert handler.pending_evolution_approval("sess-1") == "team_skill_evolve_new"
    auto_msg = handler.pop_user_message_nowait()
    assert auto_msg.session_id == "sess-1"
    assert auto_msg.channel_id == "web"
    assert auto_msg.params["request_id"] == "team_skill_evolve_old"
    assert auto_msg.params["answers"] == [{"selected_options": ["接收"]}]
    assert auto_msg.metadata == {"k": "v"}


def test_finish_evolution_approval_if_current_keeps_newer_pending_request():
    handler = _TestMessageHandler.create()
    handler.seed_pending_evolution_approval("sess-2", "team_skill_evolve_new")
    handler.seed_session_evolution_in_progress("sess-2")
    handler.seed_queued_supplement_input("sess-2", {"new_input": "follow up"})

    queued = handler.finish_evolution_approval_if_current(
        "sess-2",
        "team_skill_evolve_old",
    )

    assert queued is None
    assert handler.pending_evolution_approval("sess-2") == "team_skill_evolve_new"
    assert handler.has_session_evolution_in_progress("sess-2") is True
    assert handler.queued_supplement_input("sess-2") == {"new_input": "follow up"}
