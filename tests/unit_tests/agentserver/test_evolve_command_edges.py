from types import SimpleNamespace

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter


@pytest.mark.anyio
async def test_agent_evolve_missing_skill_md_fails_before_sdk_call(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return False

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            pytest.fail("missing SKILL.md must be rejected before calling SDK evolution")

    adapter = JiuWenClawDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "_collect_messages_for_evolve",
        lambda _session_id: [{"role": "user", "content": "please evolve"}],
    )

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve demo-skill improve review flow",
        "sess-agent-evolve",
    )

    assert result["result_type"] == "error"
    assert "SKILL.md" in result["output"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "message", "expected_type", "expected_output"),
    [
        ("generation_failed", "llm unavailable", "error", "llm unavailable"),
        (
            "no_evolution_no_records",
            "",
            "answer",
            "已请求演进，但本次未生成可保存经验。",
        ),
    ],
)
async def test_agent_evolve_maps_sdk_result_status(
    monkeypatch,
    status: str,
    message: str,
    expected_type: str,
    expected_output: str,
):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            return SimpleNamespace(
                status=status,
                message=message,
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenClawDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve demo-skill improve review flow",
        "sess-agent-evolve",
    )

    assert result == {"output": expected_output, "result_type": expected_type}


@pytest.mark.anyio
async def test_agent_evolve_without_local_signal_still_maps_sdk_generation_failure(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["code-runner"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

    recorded_intents: list[str] = []

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(skill_name: str, evolution_intent: str, **_kwargs):
            recorded_intents.append(evolution_intent)
            return SimpleNamespace(
                status="generation_failed",
                message="llm unavailable",
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenClawDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve code-runner",
        "sess-agent-evolve",
    )

    assert result == {"output": "llm unavailable", "result_type": "error"}
    assert recorded_intents == ["用户显式请求演进 Skill 'code-runner'。"]


@pytest.mark.anyio
async def test_agent_evolve_hides_internal_toolchain_generation_error(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["code-runner"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            return SimpleNamespace(
                status="generation_failed",
                message=(
                    "[170001] toolchain optimizer_backword execution error, "
                    "reason: [174031] toolchain optimizer tool_call lim_call "
                    "execution error, reason: invoke_failed"
                ),
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenClawDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve code-runner",
        "sess-agent-evolve",
    )

    assert result == {
        "output": "LLM 服务调用失败，请检查模型配置或稍后重试",
        "result_type": "error",
    }


@pytest.mark.anyio
async def test_agent_evolve_list_allows_skill_without_skill_md():
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return False

        @staticmethod
        async def get_records_by_score(skill_name: str) -> list[object]:
            return []

    adapter = JiuWenClawDeepAdapter()
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        store=_FakeStore()
    )

    result = await adapter._handle_evolve_list_command(  # pylint: disable=protected-access
        "/evolve_list demo-skill",
    )

    assert result == {
        "output": "Skill 'demo-skill' 暂无演进经验。",
        "result_type": "answer",
    }
