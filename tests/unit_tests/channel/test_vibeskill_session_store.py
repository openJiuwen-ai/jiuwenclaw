import pytest

from jiuwenclaw.channel.vibeskill_session import VibeSkillSessionState, VibeSkillSessionStore


@pytest.mark.asyncio
async def test_vibeskill_session_store_keeps_user_id_in_memory():
    store = VibeSkillSessionStore()

    session = await store.get_or_create(external_id=None, internal_id="sess-1", mode="SkillCreate")
    await store.set_metadata(session.internal_id, {"user_id": "user-1"})
    await store.set_state(session.internal_id, VibeSkillSessionState.BUSY)

    assert store.get_user_id("sess-1") == "user-1"
    assert await store.get_state("sess-1") == VibeSkillSessionState.BUSY


@pytest.mark.asyncio
async def test_vibeskill_session_store_deletes_mapping():
    store = VibeSkillSessionStore()
    session = await store.get_or_create(external_id="external-1", internal_id="sess-1")
    await store.set_metadata(session.internal_id, {"user_id": "sess-1"})

    assert await store.delete_session("sess-1") is True
    assert await store.get_session("sess-1") is None
    assert await store.resolve_internal("external-1") is None
