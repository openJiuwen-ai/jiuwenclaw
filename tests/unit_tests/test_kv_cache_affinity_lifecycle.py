from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.kv_cache import KVC_SESSION_EVICT_TIMEOUT_SECONDS

from jiuwenswarm.server.runtime.session.kv_cache import kv_cache_lifecycle as lifecycle


class FakeAffinityModel:
    def __init__(self, ok: bool = True, provider: str = "") -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []
        self.model_client_config = SimpleNamespace(client_provider=provider) if provider else None

    def supports_kv_cache_affinity(self) -> bool:
        return True

    async def prefetch_kvc(self, **kwargs):
        self.calls.append(("prefetch", kwargs))
        return self.ok

    async def offload_kvc(self, **kwargs):
        self.calls.append(("offload", kwargs))
        return self.ok

    async def evict_kvc(self, **kwargs):
        self.calls.append(("evict", kwargs))
        return self.ok


def _config(enabled: bool) -> dict:
    return {
        "react": {
            "kv_cache_affinity_config": {
                "enable_kv_cache_affinity": enabled,
                "enable_kv_cache_release": False,
            }
        }
    }


@pytest.mark.asyncio
async def test_lifecycle_skips_when_disabled(monkeypatch):
    model = FakeAffinityModel()
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(False))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.prefetch_session_kv_cache(
        session_id="sess_a",
        agent=object(),
    )

    assert result.status == "skipped"
    assert model.calls == []


@pytest.mark.asyncio
async def test_lifecycle_calls_model_with_session_target(monkeypatch):
    model = FakeAffinityModel()
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.prefetch_session_kv_cache(
        session_id="sess_child",
        parent_session_id="sess_parent",
        timeout=3.0,
    )

    assert result.ok
    assert model.calls == [
        (
            "prefetch",
            {
                "target": "session",
                "session_id": "sess_child",
                "parent_session_id": "sess_parent",
                "timeout": 3.0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_root_evict_uses_explicit_self_parent(monkeypatch):
    model = FakeAffinityModel()
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.evict_session_kv_cache(
        session_id="root_session",
        parent_session_id="root_session",
    )

    assert result.ok
    assert model.calls == [
        (
            "evict",
            {
                "target": "session",
                "session_id": "root_session",
                "parent_session_id": "root_session",
                "timeout": KVC_SESSION_EVICT_TIMEOUT_SECONDS,
            },
        )
    ]


@pytest.mark.asyncio
async def test_lifecycle_reports_failed_provider_result(monkeypatch):
    model = FakeAffinityModel(ok=False)
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.evict_session_kv_cache(session_id="sess_a")

    assert result.failed


@pytest.mark.asyncio
async def test_lifecycle_fails_when_enabled_with_non_ascend_provider(monkeypatch):
    model = FakeAffinityModel(provider="OpenAI")
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.prefetch_session_kv_cache(session_id="sess_a")

    assert result.failed
    assert model.calls == []


@pytest.mark.asyncio
async def test_lifecycle_skips_unsupported_model(monkeypatch):
    model = FakeAffinityModel()
    model.supports_kv_cache_affinity = lambda: False
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = await lifecycle.evict_session_kv_cache(session_id="sess_a")

    assert result.status == "skipped"
    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["config", "model", "capability", "arguments", "evict"])
async def test_lifecycle_contains_setup_and_evict_exceptions(monkeypatch, failure_point):
    model = FakeAffinityModel()
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    if failure_point == "config":
        monkeypatch.setattr(
            lifecycle,
            "get_config",
            lambda: (_ for _ in ()).throw(RuntimeError("config broken")),
        )
    elif failure_point == "model":
        monkeypatch.setattr(
            lifecycle,
            "resolve_kv_cache_affinity_model",
            lambda **_: (_ for _ in ()).throw(RuntimeError("model broken")),
        )
    elif failure_point == "capability":
        model.supports_kv_cache_affinity = lambda: (_ for _ in ()).throw(
            RuntimeError("capability broken")
        )
    elif failure_point == "arguments":
        model.evict_kvc = lambda **_: (_ for _ in ()).throw(RuntimeError("arguments broken"))
    else:
        async def broken_evict(**_):
            raise RuntimeError("evict broken")

        model.evict_kvc = broken_evict

    result = await lifecycle.evict_session_kv_cache(session_id="sess_a")

    assert result.failed


@pytest.mark.asyncio
async def test_lifecycle_forwards_explicit_timeout_to_client_owner(monkeypatch):
    model = FakeAffinityModel()
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    async def failed_evict(**kwargs):
        assert kwargs["timeout"] == 0.01
        return False

    model.evict_kvc = failed_evict

    result = await lifecycle.evict_session_kv_cache(session_id="sess_a", timeout=0.01)

    assert result.failed


@pytest.mark.asyncio
async def test_offload_dispatch_returns_before_provider_completion(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    model = FakeAffinityModel()

    async def slow_offload(**kwargs):
        model.calls.append(("offload", kwargs))
        started.set()
        await release.wait()
        return True

    model.offload_kvc = slow_offload
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    result = lifecycle.dispatch_offload_session_kv_cache(session_id="sess_signal")
    assert result.scheduled
    await asyncio.wait_for(started.wait(), timeout=0.5)
    assert len(lifecycle._BACKGROUND_ACTIONS) == 1

    release.set()
    await asyncio.sleep(0)
    await lifecycle.cancel_pending_kv_cache_lifecycle_tasks()


@pytest.mark.asyncio
async def test_same_root_session_offload_and_prefetch_are_ordered(monkeypatch):
    offload_started = asyncio.Event()
    offload_release = asyncio.Event()
    prefetch_done = asyncio.Event()
    calls: list[str] = []
    model = FakeAffinityModel()

    async def slow_offload(**_kwargs):
        calls.append("offload-start")
        offload_started.set()
        await offload_release.wait()
        calls.append("offload-end")
        return True

    async def prefetch(**_kwargs):
        calls.append("prefetch")
        prefetch_done.set()
        return True

    model.offload_kvc = slow_offload
    model.prefetch_kvc = prefetch
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    lifecycle.dispatch_offload_session_kv_cache(session_id="sess_a")
    await asyncio.wait_for(offload_started.wait(), timeout=0.5)
    lifecycle.dispatch_prefetch_session_kv_cache(session_id="sess_a")
    await asyncio.sleep(0)
    assert calls == ["offload-start"]

    offload_release.set()
    await asyncio.wait_for(prefetch_done.wait(), timeout=0.5)
    assert calls == ["offload-start", "offload-end", "prefetch"]
    await lifecycle.cancel_pending_kv_cache_lifecycle_tasks()


@pytest.mark.asyncio
async def test_signal_dispatched_during_root_evict_waits_for_evict(monkeypatch):
    evict_started = asyncio.Event()
    evict_release = asyncio.Event()
    prefetch_done = asyncio.Event()
    calls: list[str] = []
    model = FakeAffinityModel()

    async def slow_evict(**_kwargs):
        calls.append("evict-start")
        evict_started.set()
        await evict_release.wait()
        calls.append("evict-end")
        return True

    async def prefetch(**_kwargs):
        calls.append("prefetch")
        prefetch_done.set()
        return True

    model.evict_kvc = slow_evict
    model.prefetch_kvc = prefetch
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    evict_task = asyncio.create_task(
        lifecycle.evict_session_kv_cache(session_id="sess_a")
    )
    await asyncio.wait_for(evict_started.wait(), timeout=0.5)
    result = lifecycle.dispatch_prefetch_session_kv_cache(session_id="sess_a")
    assert result.scheduled
    await asyncio.sleep(0)
    assert calls == ["evict-start"]

    evict_release.set()
    assert (await evict_task).ok
    await asyncio.wait_for(prefetch_done.wait(), timeout=0.5)
    assert calls == ["evict-start", "evict-end", "prefetch"]
    await lifecycle.cancel_pending_kv_cache_lifecycle_tasks()


@pytest.mark.asyncio
async def test_different_root_sessions_do_not_share_management_lane(monkeypatch):
    release_a = asyncio.Event()
    started_b = asyncio.Event()
    model = FakeAffinityModel()

    async def offload(**kwargs):
        if kwargs["session_id"] == "sess_a":
            await release_a.wait()
        else:
            started_b.set()
        return True

    model.offload_kvc = offload
    monkeypatch.setattr(lifecycle, "get_config", lambda: _config(True))
    monkeypatch.setattr(lifecycle, "resolve_kv_cache_affinity_model", lambda **_: model)

    lifecycle.dispatch_offload_session_kv_cache(session_id="sess_a")
    lifecycle.dispatch_offload_session_kv_cache(session_id="sess_b")
    await asyncio.wait_for(started_b.wait(), timeout=0.5)

    release_a.set()
    await lifecycle.cancel_pending_kv_cache_lifecycle_tasks()
