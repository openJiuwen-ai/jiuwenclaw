from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentManager,
)
from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder

from jiuwenswarm.common import utils as _utils_mod
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.agents.harness.common.prompt.prompt_builder import (
    build_agent_identity_prompt,
)
from jiuwenswarm.agents.harness.common.rails import skill_retrieval_prompt_rail as _skill_retrieval_prompt_mod
from jiuwenswarm.agents.harness.common.rails.runtime_prompt_rail import RuntimePromptRail
from jiuwenswarm.agents.harness.common.rails.skill_retrieval_prompt_rail import SkillRetrievalPromptRail
from jiuwenswarm.agents.harness.common.rails.symphony_orchestration_prompt_rail import (
    SymphonyOrchestrationPromptRail,
)


class _TestableJiuWenSwarmDeepAdapter(JiuWenSwarmDeepAdapter):
    def set_workspace_dir(self, workspace_dir: str) -> None:
        self._workspace_dir = workspace_dir

    def build_configured_subagents(
        self,
        model: Model,
        config: dict,
        config_base: dict | None = None,
    ):
        return self._build_configured_subagents(model, config, config_base)


class _FakeSession:
    def get_session_id(self) -> str:
        return "sess1"


class _FakeAgent:
    def __init__(self, builder: SystemPromptBuilder) -> None:
        self.system_prompt_builder = builder
        self.prompt_attachment_manager = PromptAttachmentManager()


class _FakeAbilityManager:
    def __init__(self) -> None:
        self._items = {
            "list_skill": SimpleNamespace(name="list_skill"),
            "search_skill": SimpleNamespace(name="search_skill"),
        }
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_ability(self, card, tool=None):
        self._items[card.name] = card
        return SimpleNamespace(added=True)

    def remove_ability(self, name: str):
        return self._items.pop(name, None)

    def get(self, name: str):
        return self._items.get(name)

    def remove(self, name: str):
        self.removed.append(name)
        return self._items.pop(name, None)

    def add(self, ability):
        self.added.append(ability.name)
        self._items[ability.name] = ability


class _FakeToolAgent(_FakeAgent):
    def __init__(self, builder: SystemPromptBuilder) -> None:
        super().__init__(builder)
        self.ability_manager = _FakeAbilityManager()


class _FakeResourceManager:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_tool(self, tool: SimpleNamespace) -> None:
        self.added.append(tool.card.name)

    def remove_tool(self, tool_id: str) -> None:
        self.removed.append(tool_id)


class _FakeRuntimeInstance:
    def __init__(self) -> None:
        self.card = SimpleNamespace(id="jiuwenswarm")
        self.ability_manager = _FakeAbilityManager()


def test_build_agent_identity_prompt_contains_identity_section_only():
    prompt = build_agent_identity_prompt(language="zh")

    assert "# 你的家" in prompt
    assert "## Symphony Orchestration" not in prompt
    assert "`symphony_compose_score`" not in prompt
    assert "# 消息说明" not in prompt


@pytest.mark.asyncio
async def test_symphony_orchestration_prompt_rail_respects_config_snapshot():
    enabled_builder = SystemPromptBuilder(language="cn")
    enabled_agent = _FakeAgent(enabled_builder)
    enabled_ctx = AgentCallbackContext(
        agent=enabled_agent,
        inputs=SimpleNamespace(
            tools=[SimpleNamespace(name="symphony_compose_score")],
        ),
        session=_FakeSession(),
        extra={},
    )
    enabled_rail = SymphonyOrchestrationPromptRail(
        config_base={"symphony": {"enabled": True}},
    )
    enabled_rail.init(enabled_agent)
    await enabled_rail.before_model_call(enabled_ctx)

    disabled_builder = SystemPromptBuilder(language="cn")
    disabled_agent = _FakeAgent(disabled_builder)
    disabled_ctx = AgentCallbackContext(
        agent=disabled_agent,
        inputs=SimpleNamespace(
            tools=[SimpleNamespace(name="symphony_compose_score")],
        ),
        session=_FakeSession(),
        extra={},
    )
    disabled_rail = SymphonyOrchestrationPromptRail(
        config_base={"symphony": {"enabled": False}},
    )
    disabled_rail.init(disabled_agent)
    await disabled_rail.before_model_call(disabled_ctx)

    enabled_prompt = enabled_builder.build()
    disabled_prompt = disabled_builder.build()
    assert "## Symphony Orchestration" in enabled_prompt
    assert "`symphony_compose_score`" in enabled_prompt
    assert "## Symphony Orchestration" not in disabled_prompt
    assert "`symphony_compose_score`" not in disabled_prompt


@pytest.mark.asyncio
async def test_symphony_orchestration_prompt_rail_injects_when_tool_visible(
    monkeypatch,
):
    monkeypatch.setattr(
        "jiuwenswarm.symphony.config.load_symphony_config",
        lambda: SimpleNamespace(enabled=True),
    )
    builder = SystemPromptBuilder(language="cn")
    agent = _FakeAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(
            tools=[SimpleNamespace(name="symphony_compose_score")],
        ),
        session=_FakeSession(),
        extra={},
    )

    rail = SymphonyOrchestrationPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    prompt = builder.build()
    assert "## Symphony Orchestration" in prompt
    assert "`symphony_compose_score`" in prompt


@pytest.mark.asyncio
async def test_symphony_orchestration_prompt_rail_clears_when_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "jiuwenswarm.symphony.config.load_symphony_config",
        lambda: SimpleNamespace(enabled=True),
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(
        PromptSection(
            name="symphony_orchestration",
            content={"cn": "stale orchestration prompt"},
            priority=42,
        )
    )
    agent = _FakeAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(tools=[SimpleNamespace(name="other_tool")]),
        session=_FakeSession(),
        extra={},
    )

    rail = SymphonyOrchestrationPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    assert "stale orchestration prompt" not in builder.build()


@pytest.mark.asyncio
async def test_symphony_orchestration_prompt_rail_clears_when_disabled(
    monkeypatch,
):
    monkeypatch.setattr(
        "jiuwenswarm.symphony.config.load_symphony_config",
        lambda: SimpleNamespace(enabled=False),
    )
    builder = SystemPromptBuilder(language="cn")
    agent = _FakeAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(
            tools=[SimpleNamespace(name="symphony_compose_score")],
        ),
        session=_FakeSession(),
        extra={},
    )

    rail = SymphonyOrchestrationPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    assert "## Symphony Orchestration" not in builder.build()


def test_deep_adapter_syncs_symphony_tools_from_config_snapshot(monkeypatch):
    fake_resource = _FakeResourceManager()
    fake_instance = _FakeRuntimeInstance()
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = fake_instance
    adapter._tool_cards = []
    adapter._symphony_tools = []
    adapter._symphony_tools_registered = False
    seen_configs: list[dict] = []

    tools = [
        SimpleNamespace(card=SimpleNamespace(id=name, name=name))
        for name in (
            "symphony_read_score",
            "symphony_refresh_score",
            "symphony_compose_score",
        )
    ]

    class FakeSymphonyToolkit:
        def get_tools(self, config_base=None):
            seen_configs.append(config_base)
            return tools

    monkeypatch.setattr(interface_module.Runner, "resource_mgr", fake_resource)
    monkeypatch.setattr(interface_module, "SymphonyToolkit", FakeSymphonyToolkit)

    adapter._sync_symphony_tools_for_runtime({"symphony": {"enabled": True}})

    assert seen_configs == [{"symphony": {"enabled": True}}]
    assert adapter._symphony_tools_registered is True
    assert [card.name for card in adapter._tool_cards] == [
        "symphony_read_score",
        "symphony_refresh_score",
        "symphony_compose_score",
    ]
    assert fake_resource.added == [
        "symphony_read_score",
        "symphony_refresh_score",
        "symphony_compose_score",
    ]
    assert fake_instance.ability_manager.added == fake_resource.added

    adapter._sync_symphony_tools_for_runtime({"symphony": {"enabled": False}})

    assert adapter._symphony_tools == []
    assert adapter._symphony_tools_registered is False
    assert adapter._tool_cards == []
    assert fake_resource.removed == [
        "symphony_read_score",
        "symphony_refresh_score",
        "symphony_compose_score",
    ]
    assert fake_instance.ability_manager.removed == fake_resource.removed


@pytest.mark.asyncio
async def test_runtime_time_section_participates_in_priority_order():
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="identity", content={"cn": "identity"}, priority=10))
    builder.add_section(PromptSection(name="tools", content={"cn": "# 可用工具"}, priority=30))
    builder.add_section(PromptSection(name="workspace", content={"cn": "# 工作空间"}, priority=70))

    agent = _FakeAgent(builder)
    runtime_rail = RuntimePromptRail(
        language="cn",
        channel="web"
    )
    runtime_rail.init(agent)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=_FakeSession(),
        extra={},
    )
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    ordered_markers = [
        "identity",
        "# 可用工具",
        "# 工作空间",
        "# 时间说明",
    ]
    positions = [prompt.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert builder.has_section("runtime.model_answer_policy")
    assert not builder.has_section("runtime")
    assert "# 运行时状态" not in prompt


@pytest.mark.asyncio
async def test_runtime_dynamic_sections_go_to_prompt_attachment_when_manager_available(tmp_path, monkeypatch):
    monkeypatch.setattr(_utils_mod, "get_config_dir", lambda: tmp_path)
    builder = SystemPromptBuilder(language="en")
    agent = _FakeAgent(builder)
    runtime_rail = RuntimePromptRail(language="en", channel="web")
    runtime_rail.init(agent)
    runtime_rail.set_model_name("model-x")
    runtime_rail.set_mode("agent.plan")

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=_FakeSession(),
        extra={},
    )
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    assert "# Time Description" in prompt
    assert "# Runtime State" not in prompt
    assert "# Language" in prompt
    assert "# Browser Tool Policy" in prompt
    assert "browser_preflight_submit" in prompt
    assert "hotel_option_select" in prompt
    assert "hotel_payment_confirm" in prompt
    assert "gmail_email_select" in prompt
    assert "gmail_cleanup_confirm" in prompt
    assert "social_post_draft_select" in prompt
    assert "social_post_confirm" in prompt
    assert "Do not use plain natural-language questions or `ask_user`" in prompt
    assert "Mandatory Web A2UI account-action gate" in prompt
    assert "`todo_create`, `todo_modify`, `memory_search`" in prompt
    assert "`task_tool`, plain text, Markdown, or `ask_user`" in prompt
    assert "show final A2UI confirmation before any" in prompt
    assert "# Environment" in prompt

    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert [item.id for item in items] == ["session.sess1.runtime.setting"]
    rendered = agent.prompt_attachment_manager.render(items)
    assert "model-x" in rendered
    assert "Always respond in English" in prompt
    assert "# Browser Tool Policy" in prompt


@pytest.mark.asyncio
async def test_runtime_git_status_attachment_clears_when_git_context_disappears(tmp_path, monkeypatch):
    monkeypatch.setattr(_utils_mod, "get_config_dir", lambda: tmp_path)
    runtime_state = tmp_path / "runtime_state" / "default.yaml"
    runtime_state.parent.mkdir(parents=True, exist_ok=True)
    runtime_state.write_text(
        "git_branch: feature/test\n"
        "git_status: M file.py\n"
        "git_recent_commits: abc init\n",
        encoding="utf-8",
    )
    builder = SystemPromptBuilder(language="en")
    agent = _FakeAgent(builder)
    runtime_rail = RuntimePromptRail(language="en", channel="web")
    runtime_rail.init(agent)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=_FakeSession(),
        extra={},
    )

    await runtime_rail.before_model_call(ctx)
    session_items = await agent.prompt_attachment_manager.list_by_filter(session_id="sess1")
    assert [item.id for item in session_items if item.id.endswith(".git_status")] == ["session.sess1.git_status"]

    runtime_state.write_text("git_branch: ''\n", encoding="utf-8")
    await runtime_rail.before_model_call(ctx)
    session_items = await agent.prompt_attachment_manager.list_by_filter(session_id="sess1")
    assert [item.id for item in session_items if item.id.endswith(".git_status")] == []


@pytest.mark.asyncio
async def test_runtime_prompt_uses_runtime_cwd_over_stale_trusted_dir(tmp_path):
    builder = SystemPromptBuilder(language="en")
    agent = _FakeAgent(builder)
    stale_dir = tmp_path / "missing-worktree"
    project_dir = tmp_path / "project"
    current_dir = project_dir / "current"
    extra_dir = tmp_path / "extra"
    current_dir.mkdir(parents=True)
    extra_dir.mkdir()

    runtime_rail = RuntimePromptRail(language="en", channel="tui")
    runtime_rail.init(agent)
    runtime_rail.set_trusted_dirs([str(stale_dir), str(current_dir), str(extra_dir)])
    runtime_rail.set_runtime_paths(cwd=str(current_dir), project_dir=str(project_dir))

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=_FakeSession(),
        extra={},
    )
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    assert "# Current Project Workspace" in prompt
    assert "Current project directory" in prompt
    assert "Do not call `pwd`, `ls`" in prompt
    assert "# Working Directory Policy" in prompt
    assert str(current_dir) in prompt
    assert str(stale_dir) not in prompt
    assert str(extra_dir) in prompt

    items = await agent.prompt_attachment_manager.list_by_filter(session_id="sess1")
    assert [item.id for item in items if item.id.endswith(".trusted_dirs_policy")] == []


@pytest.mark.asyncio
async def test_runtime_prompt_language_output_prefers_rail_language_over_runtime_state(
    monkeypatch,
    tmp_path,
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    state_dir = config_dir / "runtime_state"
    state_dir.mkdir()
    (state_dir / "default.yaml").write_text(
        "model: test-model\nmode: team.plan\nlanguage: en\nchannel: tui\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_utils_mod, "get_config_dir", lambda: config_dir)

    builder = SystemPromptBuilder(language="cn")
    agent = _FakeAgent(builder)
    runtime_rail = RuntimePromptRail(language="cn", channel="tui")
    runtime_rail.init(agent)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=_FakeSession(),
        extra={},
    )
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    assert "Always respond in Chinese." in prompt
    rendered = agent.prompt_attachment_manager.render(
        await agent.prompt_attachment_manager.list_by_filter(session_id="sess1")
    )
    assert "Always respond in Chinese." not in rendered
    assert "Always respond in English." not in rendered
    assert "Always respond in English." not in prompt
    assert "当前语言：cn" in rendered


@pytest.mark.asyncio
async def test_skill_retrieval_prompt_hides_legacy_list_skill(monkeypatch):
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "is_agentic_retrieval_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "render_skill_retrieval_prompt_for_visible_skills",
        lambda manager, language, visible_skill_names=None: "# Agentic 技能检索\n使用 skill_branch_explore。",
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="skills", content={"cn": "旧 list_skill 提示"}, priority=40))
    agent = _FakeToolAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(
            tools=[
                SimpleNamespace(name="list_skill"),
                SimpleNamespace(name="list_skills"),
                SimpleNamespace(name="skill_branch_explore"),
            ],
        ),
        session=_FakeSession(),
        extra={},
    )

    rail = SkillRetrievalPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    assert [tool.name for tool in ctx.inputs.tools] == ["skill_branch_explore"]
    assert agent.ability_manager.get("list_skill") is None
    prompt = builder.build()
    assert "旧 list_skill 提示" not in prompt
    assert "Agentic 技能检索" in prompt

    await rail.after_model_call(ctx)

    assert agent.ability_manager.get("list_skill") is not None
    assert "旧 list_skill 提示" in builder.build()


@pytest.mark.asyncio
async def test_skill_retrieval_prompt_hides_native_skill_prompt_after_skill_use_rail(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "is_agentic_retrieval_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "render_skill_retrieval_prompt_for_visible_skills",
        lambda manager, language, visible_skill_names=None: "# Agentic 技能检索\n使用 skill_branch_explore。",
    )
    builder = SystemPromptBuilder(language="cn")
    agent = _FakeToolAgent(builder)
    agent.card = SimpleNamespace(id="test-agent")
    agent.deep_config = SimpleNamespace(enable_read_image_multimodal=False)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(
            tools=[
                SimpleNamespace(name="list_skill"),
                SimpleNamespace(name="skill_branch_explore"),
            ],
        ),
        session=_FakeSession(),
        extra={},
    )
    skill_rail = SkillUseRail(
        str(tmp_path),
        skill_mode=SkillUseRail.SKILL_MODE_AUTO_LIST,
        include_tools=False,
    )
    retrieval_rail = SkillRetrievalPromptRail()
    skill_rail.init(agent)
    retrieval_rail.init(agent)

    rails = sorted([skill_rail, retrieval_rail], key=lambda rail: rail.priority, reverse=True)
    await rails[0].before_model_call(ctx)
    await rails[1].before_model_call(ctx)

    prompt = builder.build()
    assert "需要时先调用 list_skill 查看可用技能" not in prompt
    assert "# 技能" not in prompt
    assert "Agentic 技能检索" in prompt
    assert [tool.name for tool in ctx.inputs.tools] == ["skill_branch_explore"]


@pytest.mark.asyncio
async def test_skill_retrieval_prompt_clears_section_when_disabled(monkeypatch):
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "is_agentic_retrieval_enabled",
        lambda: False,
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="skill_retrieval", content={"cn": "残留 Agentic 技能检索"}, priority=41))
    agent = _FakeToolAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(tools=[SimpleNamespace(name="list_skill")]),
        session=_FakeSession(),
        extra={},
    )

    rail = SkillRetrievalPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    assert [tool.name for tool in ctx.inputs.tools] == ["list_skill"]
    assert "残留 Agentic 技能检索" not in builder.build()
    assert agent.ability_manager.get("list_skill") is not None


@pytest.mark.asyncio
async def test_skill_retrieval_prompt_disabled_restores_hidden_skills_section(monkeypatch):
    enabled = True
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "is_agentic_retrieval_enabled",
        lambda: enabled,
    )
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "render_skill_retrieval_prompt_for_visible_skills",
        lambda manager, language, visible_skill_names=None: "# Agentic 技能检索\n使用 skill_branch_explore。",
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="skills", content={"cn": "原生技能提示"}, priority=40))
    agent = _FakeToolAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(tools=[SimpleNamespace(name="skill_branch_explore")]),
        session=_FakeSession(),
        extra={},
    )

    rail = SkillRetrievalPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)
    assert "原生技能提示" not in builder.build()

    enabled = False
    await rail.before_model_call(ctx)

    prompt = builder.build()
    assert "Agentic 技能检索" not in prompt
    assert "原生技能提示" in prompt


@pytest.mark.asyncio
async def test_skill_retrieval_prompt_render_empty_restores_native_skills(monkeypatch):
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "is_agentic_retrieval_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        _skill_retrieval_prompt_mod,
        "render_skill_retrieval_prompt_for_visible_skills",
        lambda manager, language, visible_skill_names=None: "",
    )
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="skills", content={"cn": "原生技能提示"}, priority=40))
    agent = _FakeToolAgent(builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(tools=[SimpleNamespace(name="list_skill")]),
        session=_FakeSession(),
        extra={},
    )

    rail = SkillRetrievalPromptRail()
    rail.init(agent)
    await rail.before_model_call(ctx)

    assert "原生技能提示" in builder.build()
    assert agent.ability_manager.get("list_skill") is not None
    assert [tool.name for tool in ctx.inputs.tools] == ["list_skill"]


def test_resolve_skill_mode_accepts_all_and_auto_list(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.is_skill_retrieval_enabled",
        lambda: False,
    )
    assert JiuWenSwarmDeepAdapter._resolve_skill_mode({"skill_mode": "all"}) == "all"
    assert JiuWenSwarmDeepAdapter._resolve_skill_mode({"skill_mode": "auto_list"}) == "auto_list"
    assert JiuWenSwarmDeepAdapter._resolve_skill_mode({"skill_mode": "invalid"}) == "all"

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.is_skill_retrieval_enabled",
        lambda: True,
    )
    assert JiuWenSwarmDeepAdapter._resolve_skill_mode({"skill_mode": "all"}) == "auto_list"


def test_deep_adapter_visible_skill_names_match_list_skill(monkeypatch, tmp_path):
    for name in ("alpha", "beta", "_internal", ".hidden"):
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (tmp_path / "not-a-skill").mkdir()

    adapter = _TestableJiuWenSwarmDeepAdapter()
    adapter.set_skill_manager(
        SimpleNamespace(list_execution_disabled_skills=lambda: ["beta"])
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.get_agent_skills_dir",
        lambda: tmp_path,
    )

    assert adapter._visible_skill_names_for_list_skill() == {"alpha"}


def test_deep_adapter_skill_retrieval_prompt_uses_visible_skill_provider(monkeypatch):
    captured: dict[str, object] = {}

    class FakeRail:
        def __init__(self, *, manager, visible_skill_names):
            captured["manager"] = manager
            captured["visible_skill_names"] = visible_skill_names

    manager = SimpleNamespace(list_execution_disabled_skills=lambda: [])
    adapter = _TestableJiuWenSwarmDeepAdapter()
    adapter.set_skill_manager(manager)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.is_skill_retrieval_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.SkillRetrievalPromptRail",
        FakeRail,
    )

    rail = adapter._build_skill_retrieval_prompt_rail()

    assert isinstance(rail, FakeRail)
    assert captured["manager"] is manager
    assert captured["visible_skill_names"] == adapter._visible_skill_names_for_list_skill


@pytest.mark.asyncio
async def test_deep_adapter_skill_retrieval_prompt_rail_sync_hot_toggles(monkeypatch):
    registered: list[object] = []
    unregistered: list[object] = []

    class FakeDeepAgent:
        async def register_rail(self, rail):
            registered.append(rail)

        async def unregister_rail(self, rail):
            unregistered.append(rail)

    adapter = _TestableJiuWenSwarmDeepAdapter()
    adapter._instance = FakeDeepAgent()
    rail = SimpleNamespace(name="skill_retrieval_prompt")
    monkeypatch.setattr(adapter, "_build_skill_retrieval_prompt_rail", lambda: rail)
    monkeypatch.setattr(
        adapter,
        "_skill_retrieval_tools_enabled_for_runtime",
        lambda config_base=None: True,
    )

    await adapter._sync_skill_retrieval_prompt_rail_for_runtime()
    await adapter._sync_skill_retrieval_prompt_rail_for_runtime()

    assert adapter._skill_retrieval_prompt_rail is rail
    assert registered == [rail]
    assert unregistered == []

    monkeypatch.setattr(
        adapter,
        "_skill_retrieval_tools_enabled_for_runtime",
        lambda config_base=None: False,
    )

    await adapter._sync_skill_retrieval_prompt_rail_for_runtime()

    assert adapter._skill_retrieval_prompt_rail is None
    assert unregistered == [rail]


def test_code_adapter_skill_retrieval_sync_respects_configured_tools(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter.interface_code import JiuwenSwarmCodeAdapter

    adapter = JiuwenSwarmCodeAdapter()
    monkeypatch.setattr(
        interface_module,
        "is_skill_retrieval_enabled",
        lambda: True,
    )

    assert (
        adapter._skill_retrieval_tools_enabled_for_runtime(
            {"modes": {"code": {"tools": ["skill_toolkit"]}}}
        )
        is False
    )
    assert (
        adapter._skill_retrieval_tools_enabled_for_runtime(
            {"modes": {"code": {"tools": ["skill_toolkit", "skill_retrieval"]}}}
        )
        is True
    )


def test_resolve_enable_task_loop_forces_true_when_skill_create_enabled(monkeypatch):
    monkeypatch.delenv("SKILL_CREATE", raising=False)
    assert (
        JiuWenSwarmDeepAdapter._resolve_enable_task_loop(
            {"enable_task_loop": False},
            {"evolution": {"skill_create": True}},
        )
        is True
    )


def test_resolve_enable_task_loop_forces_true_when_auto_scan_enabled(monkeypatch):
    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    assert (
        JiuWenSwarmDeepAdapter._resolve_enable_task_loop(
            {"enable_task_loop": False},
            {"evolution": {"auto_scan": True}},
        )
        is True
    )


def test_resolve_enable_task_loop_preserves_false_when_only_evolution_enabled(monkeypatch):
    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.delenv("SKILL_CREATE", raising=False)
    assert (
        JiuWenSwarmDeepAdapter._resolve_enable_task_loop(
            {"enable_task_loop": False},
            {"evolution": {"enabled": True, "auto_scan": False, "skill_create": False}},
        )
        is False
    )


def test_resolve_enable_task_loop_preserves_false_without_enforcers(monkeypatch):
    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.delenv("SKILL_CREATE", raising=False)
    assert (
        JiuWenSwarmDeepAdapter._resolve_enable_task_loop(
            {"enable_task_loop": False},
            {"evolution": {"enabled": False, "skill_create": False}},
        )
        is False
    )


# DeepAdapter only builds research_agent + browser_agent (agent mode).
# code_agent / explore_agent belong to CodeAdapter.

def test_deep_adapter_subagents_includes_optional_browser_and_configured_research():
    adapter = _TestableJiuWenSwarmDeepAdapter()
    adapter.set_workspace_dir("/tmp/jiuwenswarm-workspace")
    model = object()
    config = {
        "max_iterations": 9,
        "subagents": {
            "research_agent": {"enabled": True},
            "browser_agent": {"max_iterations": 7},
        },
    }

    with (
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_browser_runtime_enabled", return_value=True),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.build_research_agent_config",
            return_value="research_spec",
        ) as mock_research,
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.build_browser_agent_config",
            return_value="browser_spec",
        ) as mock_browser,
    ):
        subagents, _ = adapter.build_configured_subagents(model, config)

    assert subagents == ["research_spec", "browser_spec"]
    mock_research.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenswarm-workspace",
        language="cn",
        max_iterations=9,
    )
    mock_browser.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenswarm-workspace",
        language="cn",
        max_iterations=7,
    )


def test_deep_adapter_subagents_omits_research_without_explicit_enable():
    adapter = _TestableJiuWenSwarmDeepAdapter()
    adapter.set_workspace_dir("/tmp/jiuwenswarm-workspace")
    model = object()
    config = {"max_iterations": 9}

    with (
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_browser_runtime_enabled", return_value=True),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.build_research_agent_config",
            return_value="research_spec",
        ) as mock_research,
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.build_browser_agent_config",
            return_value="browser_spec",
        ) as mock_browser,
    ):
        subagents, _ = adapter.build_configured_subagents(model, config)

    # DeepAdapter: no research_agent configured, browser enabled
    assert subagents == ["browser_spec"]
    mock_research.assert_not_called()
    mock_browser.assert_called_once()
