# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""_apply_expert 装载/切换/卸载语义测试（真实 DeepAgent + 本地目录 source）。
"""

import json
import tempfile
from pathlib import Path

import pytest

# 真实 DeepAgent 的 Model 初始化会注册框架级共享 httpx connector pool，
# 其 socket 由 GC 回收的时机在全量跑动时不可控，会被 pytest unraisable 钩子
# 收集为 ResourceWarning 而误伤本文件用例（单跑/小组合均绿）。与断言无关，屏蔽。
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.filterwarnings(
        "ignore::pytest.PytestUnraisableExceptionWarning"
    ),
]

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.harness.factory import create_deep_agent

from jiuwenswarm.agents.harness.common.prompt import prompt_builder as pb
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)
from jiuwenswarm.server.runtime.expert import expert_store as es

HOST_PERSONA = pb.build_agent_persona_text("zh")

TOOL_FILE = '''
from openjiuwen.core.foundation.tool import Tool, ToolCard


class {class_name}(Tool):
    def __init__(self):
        super().__init__(ToolCard(
            id="{tool_name}",
            name="{tool_name}",
            description="测试工具",
            input_params={{}},
        ))

    async def invoke(self, inputs, **kwargs):
        return {{"echo": "ok"}}

    async def stream(self, inputs, **kwargs):
        if False:
            yield None
'''


def _make_model() -> Model:
    return Model(
        ModelClientConfig(
            client_id="test",
            client_provider="openai",
            api_key="dummy",
            api_base="https://example.com/v1",
        ),
        ModelRequestConfig(model_name="dummy-model"),
    )


def _make_package(root: Path, name: str, persona: str, tool_name: str | None = None) -> Path:
    pkg = root / name
    (pkg / "agents").mkdir(parents=True)
    (pkg / "agents" / "00-identity.md").write_text(persona, encoding="utf-8")
    manifest: dict = {
        "packageType": "agent_template",
        "agentCard": {"id": name, "name": name, "description": "测试包"},
        "persona": {"dir": "agents"},
    }
    if tool_name:
        (pkg / "tools").mkdir()
        class_name = "".join(part.title() for part in tool_name.split("_")) + "Tool"
        (pkg / "tools" / f"{tool_name}.py").write_text(
            TOOL_FILE.format(class_name=class_name, tool_name=tool_name),
            encoding="utf-8",
        )
        manifest["tools"] = [
            {"file": f"tools/{tool_name}.py", "class": class_name}
        ]
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return pkg


def _make_adapter() -> JiuWenSwarmDeepAdapter:
    """带真实 DeepAgent 的子适配器，接线与 create_instance 运行时一致：
    system_prompt 只传纯人设文本，conventions 以独立 section 后挂。"""
    adapter = JiuWenSwarmDeepAdapter()
    tmp = tempfile.mkdtemp()
    agent = create_deep_agent(
        model=_make_model(),
        system_prompt=pb.build_agent_persona_text("zh"),
        workspace=tmp,
    )
    agent.system_prompt_builder.add_section(pb.build_agent_conventions_section("zh"))
    adapter._instance = agent
    adapter.mark_as_session_scoped("sess-test")
    return adapter


def _identity_text(adapter: JiuWenSwarmDeepAdapter) -> str:
    section = adapter._instance.system_prompt_builder.get_section("identity")
    assert section is not None
    return section.content.get("cn") or next(iter(section.content.values()))


def _conventions_text(adapter: JiuWenSwarmDeepAdapter) -> str:
    section = adapter._instance.system_prompt_builder.get_section(
        "jiuwenswarm.conventions"
    )
    assert section is not None, "conventions section 应始终保留"
    return section.content.get("cn") or next(iter(section.content.values()))


def _tool_names(adapter: JiuWenSwarmDeepAdapter) -> set[str]:
    return {tool.name for tool in adapter._instance.ability_manager.list()}


@pytest.fixture
def experts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "experts"
    root.mkdir()
    source = es.LocalDirExpertPackageSource(experts_dir=root)
    monkeypatch.setattr(es, "get_expert_source", lambda: source)
    return root


@pytest.mark.asyncio
async def test_apply_replaces_identity_keeps_conventions(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()

    warnings = await adapter.apply_expert("expert-a")

    assert warnings == []
    assert adapter._current_expert_id == "expert-a"
    assert _identity_text(adapter) == "你是专家阿甲。"
    assert HOST_PERSONA not in _identity_text(adapter)
    assert "# JiuwenSwarm 内部数据" in _conventions_text(adapter)


@pytest.mark.asyncio
async def test_apply_registers_tools(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。", tool_name="echo_a")
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")

    assert "echo_a" in _tool_names(adapter)


@pytest.mark.asyncio
async def test_switch_replaces_without_residue(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。", tool_name="echo_a")
    _make_package(experts_dir, "expert-b", "你是专家阿乙。", tool_name="echo_b")
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")
    await adapter.apply_expert("expert-b")

    assert adapter._current_expert_id == "expert-b"
    assert _identity_text(adapter) == "你是专家阿乙。"
    tools = _tool_names(adapter)
    assert "echo_b" in tools
    assert "echo_a" not in tools, "切换后上一位专家的工具应被移除"


@pytest.mark.asyncio
async def test_unload_restores_identity_and_removes_tools(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。", tool_name="echo_a")
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")
    await adapter.apply_expert(None)

    assert adapter._current_expert_id is None
    assert adapter._expert_load_record is None
    assert HOST_PERSONA in _identity_text(adapter), (
        "卸载后 identity 应经 previous_snapshot 还原为宿主人设"
    )
    assert "echo_a" not in _tool_names(adapter)


@pytest.mark.asyncio
async def test_apply_same_expert_is_noop(
        experts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()
    fetch_calls = 0

    class _CountingSource(es.LocalDirExpertPackageSource):
        async def fetch(self, expert_id: str) -> Path:
            nonlocal fetch_calls
            fetch_calls += 1
            return await super().fetch(expert_id)

    monkeypatch.setattr(es, "get_expert_source", lambda: _CountingSource(experts_dir))

    await adapter.apply_expert("expert-a")
    await adapter.apply_expert("expert-a")

    assert fetch_calls == 1, "同专家重复 apply 应为 no-op"


@pytest.mark.asyncio
async def test_apply_failure_rolls_back_to_no_expert(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    broken = _make_package(experts_dir, "expert-b", "你是专家阿乙。")
    # 破坏 B：manifest 声明 rails（validate 拒载）
    manifest = json.loads((broken / "manifest.json").read_text(encoding="utf-8"))
    manifest["rails"] = [{"file": "rails/x.py", "class": "X"}]
    (broken / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")
    with pytest.raises(es.InvalidExpertPackage):
        await adapter.apply_expert("expert-b")

    assert adapter._current_expert_id is None
    assert adapter._expert_load_record is None
    assert HOST_PERSONA in _identity_text(adapter), (
        "装载失败后实例应回滚为无专家（宿主人设还原）"
    )
    notice = _notice_text(adapter)
    assert notice is not None and "无专家" in notice, (
        "装载失败后切换提示应同步更正为无专家，不得残留旧专家"
    )


@pytest.mark.asyncio
async def test_apply_requires_live_instance(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = JiuWenSwarmDeepAdapter()
    with pytest.raises(RuntimeError):
        await adapter.apply_expert("expert-a")


def _notice_text(adapter: JiuWenSwarmDeepAdapter) -> str | None:
    section = adapter._instance.system_prompt_builder.get_section(
        "expert.switch_notice"
    )
    if section is None:
        return None
    return section.content.get("cn") or next(iter(section.content.values()))


@pytest.mark.asyncio
async def test_switch_notice_written_on_load(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")

    notice = _notice_text(adapter)
    assert notice is not None, "装载后应有切换提示 section"
    assert "expert-a" in notice
    assert "默认助手" in notice, "首次装载应说明此前回复由默认助手给出"


@pytest.mark.asyncio
async def test_switch_notice_updated_on_switch_and_unload(experts_dir: Path) -> None:
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    _make_package(experts_dir, "expert-b", "你是专家阿乙。")
    adapter = _make_adapter()

    await adapter.apply_expert("expert-a")
    await adapter.apply_expert("expert-b")
    notice = _notice_text(adapter)
    assert notice is not None and "expert-b" in notice and "expert-a" in notice, (
        "切换后提示应同时含新旧专家"
    )

    await adapter.apply_expert(None)
    notice = _notice_text(adapter)
    assert notice is not None and "无专家" in notice and "expert-b" in notice, (
        "退出后提示应说明已切回默认助手、此前由哪位专家回复"
    )


@pytest.mark.asyncio
async def test_replay_does_not_write_notice(experts_dir: Path) -> None:
    """重放（驱逐重建）不是用户切换，不应产生切换提示。"""
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()

    await adapter._apply_expert("expert-a", notify=False)

    assert adapter._current_expert_id == "expert-a"
    assert _notice_text(adapter) is None


async def _identity_attachment(adapter: JiuWenSwarmDeepAdapter) -> str | None:
    """每轮尾部注入的身份附件内容（无附件返回 None）。"""
    manager = adapter._instance.prompt_attachment_manager
    items = await manager.collect_for_session("sess-test")
    for item in items:
        if item.section == "expert.current_identity":
            return item.content
    return None


@pytest.mark.asyncio
async def test_identity_attachment_synced_on_apply_and_unload(
        experts_dir: Path,
) -> None:
    """装载/切换/退出都要把当前身份同步到每轮尾部注入的 prompt attachment。"""
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    _make_package(experts_dir, "expert-b", "你是专家阿乙。")
    adapter = _make_adapter()

    assert await _identity_attachment(adapter) is None, "初始无附件"

    await adapter.apply_expert("expert-a")
    content = await _identity_attachment(adapter)
    assert content is not None and "expert-a" in content, "装载后附件应含当前专家"

    await adapter.apply_expert("expert-b")
    content = await _identity_attachment(adapter)
    assert content is not None and "expert-b" in content and "expert-a" not in content, (
        "切换后附件应只含新专家"
    )

    await adapter.apply_expert(None)
    assert await _identity_attachment(adapter) is None, "退出后附件应清除"


@pytest.mark.asyncio
async def test_identity_attachment_restored_on_replay_and_failed_apply(
        experts_dir: Path,
) -> None:
    """重放（notify=False）同样重建附件；装载失败回滚后附件不得残留。"""
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()

    await adapter._apply_expert("expert-a", notify=False)  # 重放路径
    content = await _identity_attachment(adapter)
    assert content is not None and "expert-a" in content, "重放也应重建身份附件"

    with pytest.raises(es.ExpertNotFound):  # fetch 即失败，同样走回滚路径
        await adapter.apply_expert("expert-missing")
    assert adapter._current_expert_id is None
    assert await _identity_attachment(adapter) is None, (
        "装载失败回滚为无专家后，附件不得残留旧专家"
    )


@pytest.mark.asyncio
async def test_identity_attachment_follows_builder_language(
        experts_dir: Path,
) -> None:
    """附件按 system_prompt_builder.language 写单语（cn/en 二值，运行时按请求同步）。"""
    _make_package(experts_dir, "expert-a", "你是专家阿甲。")
    adapter = _make_adapter()

    adapter._instance.system_prompt_builder.language = "en"
    await adapter.apply_expert("expert-a")
    content = await _identity_attachment(adapter)
    assert content is not None
    assert "Current expert" in content and "当前专家" not in content, (
        "en 场景附件应为纯英文"
    )

    adapter._instance.system_prompt_builder.language = "cn"
    await adapter._sync_expert_identity_attachment()  # 语言切换后重同步
    content = await _identity_attachment(adapter)
    assert content is not None
    assert "当前专家" in content and "Current expert" not in content, (
        "cn 场景附件应为纯中文"
    )


@pytest.mark.asyncio
async def test_prompt_rebuild_restores_conventions_and_expert(
        experts_dir: Path,
) -> None:
    """configure 的 system_prompt 重建会把 builder 打回 identity+attachments 两段，
    宿主必须补回 conventions 并重挂专家（否则专家人设静默丢失、规则被覆盖）。"""
    from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder

    _make_package(experts_dir, "expert-a", "你是专家阿甲。", tool_name="echo_a")
    adapter = _make_adapter()
    await adapter.apply_expert("expert-a")

    # 模拟 configure 的 builder 重建：只剩宿主 identity；专家工具也被热重载摘除
    rebuilt = SystemPromptBuilder(language="cn")
    rebuilt.add_section(
        PromptSection(
            name="identity",
            content={"cn": HOST_PERSONA},
            priority=10,
        )
    )
    adapter._instance.system_prompt_builder = rebuilt
    adapter._instance.ability_manager.remove_ability("echo_a")

    adapter._restore_dynamic_prompt_sections()
    await adapter._reapply_expert_after_prompt_rebuild()

    assert "# JiuwenSwarm 内部数据" in _conventions_text(adapter), (
        "重建后 conventions 应被补回"
    )
    assert _identity_text(adapter) == "你是专家阿甲。", "重建后专家应重挂"
    assert "echo_a" in _tool_names(adapter), "重建后专家工具应重挂"
    assert adapter._current_expert_id == "expert-a"


async def test_persona_text_and_conventions_section_helpers() -> None:
    """build_agent_persona_text 只含人设（喂 create_deep_agent 不夹带规则）。"""
    persona = pb.build_agent_persona_text("zh")
    assert "你是一个私人智能体" in persona
    assert "JiuwenSwarm 内部数据" not in persona
    section = pb.build_agent_conventions_section("zh")
    assert section.name == "jiuwenswarm.conventions"
    assert section.priority == 15


@pytest.mark.asyncio
async def test_factory_wiring_identity_is_persona_only() -> None:
    """create_deep_agent 会把 system_prompt 字符串整段塞进 identity section——
    确认传 persona 文本时 identity 不含 conventions（拆层在运行时真实生效）。"""
    agent = create_deep_agent(
        model=_make_model(),
        system_prompt=pb.build_agent_persona_text("zh"),
        workspace=tempfile.mkdtemp(),
    )
    identity = agent.system_prompt_builder.get_section("identity")
    text = identity.content.get("cn") or next(iter(identity.content.values()))
    assert "你是一个私人智能体" in text
    assert "JiuwenSwarm 内部数据" not in text
