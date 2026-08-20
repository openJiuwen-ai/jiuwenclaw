# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""专家卸载后 session baseline 不再残留（ReconcilingSkillUseRail）。
"""

import json
import tempfile
from pathlib import Path

import pytest

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
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.factory import create_deep_agent

from jiuwenswarm.agents.harness.common.prompt import prompt_builder as pb
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)
from jiuwenswarm.server.runtime.agent_adapter.skill_rail_reconcile import (
    ReconcilingSkillUseRail,
)
from jiuwenswarm.server.runtime.expert import expert_store as es

HOST_PERSONA = pb.build_agent_persona_text("zh")

_SKILL_MD = (
    "---\nname: {name}\n"
    "description: {name} 测试技能概述，供 PM-18 第二层回归。\n---\n# {name}\n"
)


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


def _make_package(root: Path, name: str, skill_name: str) -> Path:
    pkg = root / name
    (pkg / "agents").mkdir(parents=True)
    (pkg / "agents" / "00-identity.md").write_text(
        f"你是专家{skill_name}-持有人。", encoding="utf-8"
    )
    skill_dir = pkg / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        _SKILL_MD.format(name=skill_name), encoding="utf-8"
    )
    manifest = {
        "packageType": "agent_template",
        "agentCard": {"id": name, "name": name, "description": "带 skill 测试包"},
        "persona": {"dir": "agents"},
        "skills": [{"dir": f"skills/{skill_name}"}],
    }
    (pkg / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return pkg


class _FakeSession:
    """Minimal session backing get_state/update_state for SkillUseRail baseline.

    SkillUseRail._load_session_state（skill_use_rail.py:553）调 session.get_state(key)；
    _save_session_baseline（:563）调 session.update_state({key: {...}})。本 fake 用一
    个 dict 当 state 存储，满足 baseline 读写即可，不依赖真实 Session 装配。
    """

    def __init__(self, session_id: str = "sess-baseline") -> None:
        self._id = session_id
        self._state: dict = {}

    def get_session_id(self) -> str:
        return self._id

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict) -> None:
        for k, v in data.items():
            if v is None:
                self._state.pop(k, None)
            else:
                self._state[k] = v


async def _make_adapter_with_reconciling_rail() -> JiuWenSwarmDeepAdapter:
    """同 _make_adapter，但挂 ReconcilingSkillUseRail（而非基类 SkillUseRail）。

    生产环境 interface_deep._build_skill_rail 即返回 ReconcilingSkillUseRail（:4138），
    本工厂对齐生产接线：空 skills_dir（_bind_skill 装载时并入），include_tools=False。
    """
    adapter = JiuWenSwarmDeepAdapter()
    tmp = tempfile.mkdtemp()
    agent = create_deep_agent(
        model=_make_model(),
        system_prompt=pb.build_agent_persona_text("zh"),
        workspace=tmp,
    )
    agent.system_prompt_builder.add_section(pb.build_agent_conventions_section("zh"))
    adapter._instance = agent
    adapter.mark_as_session_scoped("sess-baseline")

    skills_root = Path(tempfile.mkdtemp())  # 或复用 tmp_path 风格的空目录
    rail = ReconcilingSkillUseRail(
        skills_dir=str(skills_root), skill_mode="all", include_tools=False
    )
    await adapter._instance.register_rail(rail)
    return adapter


def _rail(adapter: JiuWenSwarmDeepAdapter) -> ReconcilingSkillUseRail:
    rails = adapter._instance.find_rails_by_type((ReconcilingSkillUseRail,))
    assert rails, "ReconcilingSkillUseRail 应已注册"
    return rails[0]


def _ctx(session: _FakeSession) -> AgentCallbackContext:
    return AgentCallbackContext(agent=None, inputs=None, session=session)


def _baseline_skill_names(rail: ReconcilingSkillUseRail, session) -> set[str]:
    state = rail._load_session_state(session)
    if state is None:
        return set()
    baseline = state.get("baseline_skills") or []
    return {item.get("name") for item in baseline if isinstance(item, dict)}


def _skills_section_text(adapter: JiuWenSwarmDeepAdapter) -> str:
    section = adapter._instance.system_prompt_builder.get_section("skills")
    if section is None:
        return ""
    return section.content.get("cn") or next(iter(section.content.values()), "")


async def _skill_attachment_content(adapter, session_id="sess-baseline"):
    """Skill runtime attachment（<prompt-attachment type="skill">）内容，无则 None。"""
    manager = adapter._instance.prompt_attachment_manager
    items = await manager.collect_for_session(session_id)
    for item in items:
        if getattr(item, "kind", None) and str(item.kind).lower() == "skill":
            return item.content
    return None


@pytest.fixture
def experts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "experts"
    root.mkdir()
    source = es.LocalDirExpertPackageSource(experts_dir=root)
    monkeypatch.setattr(es, "get_expert_source", lambda: source)
    return root


@pytest.mark.asyncio
async def test_unload_purges_session_baseline(experts_dir: Path) -> None:
    """卸载专家后再跑一轮 before_model_call：baseline/主 system/attachment 三处不残留。"""
    _make_package(experts_dir, "expert-a", "threat-modeling")
    adapter = await _make_adapter_with_reconciling_rail()
    session = _FakeSession()
    rail = _rail(adapter)

    await adapter.apply_expert("expert-a")
    await rail.before_model_call(_ctx(session))  # 建含专家 skill 的 baseline
    assert "threat-modeling" in _baseline_skill_names(rail, session), (
        "装载后 baseline 应含专家 skill"
    )
    assert "threat-modeling" in _skills_section_text(adapter), (
        "装载后主 system # 技能 段应含专家 skill 概述"
    )

    await adapter.apply_expert(None)  # 卸载：第一层 purge 清 self.skills/skills_dir
    # 第一层后但第二层前：baseline 仍旧（agent-core 只增不删）
    assert "threat-modeling" in _baseline_skill_names(rail, session), (
        "卸载后、子类 reconcile 前，baseline 仍含专家 skill（复现 PM-18 第二层残留）"
    )

    await rail.before_model_call(_ctx(session))  # 子类 _reconcile 刷 baseline
    assert "threat-modeling" not in _baseline_skill_names(rail, session), (
        "reconcile 后 baseline 应剔除已卸载专家 skill"
    )
    assert "threat-modeling" not in _skills_section_text(adapter), (
        "reconcile 后主 system # 技能 段不应残留已卸载专家 skill 概述"
    )
    attachment = await _skill_attachment_content(adapter)
    assert attachment is None or "threat-modeling" not in attachment, (
        "reconcile 后不应再注入含已移除 threat-modeling 的 skill attachment diff"
    )


@pytest.mark.asyncio
async def test_baseline_not_purged_when_skill_still_mounted(experts_dir: Path) -> None:
    """skill 目录仍在 skills_dir 时 baseline 不被误删（守卫：避免 reload 时序误删）。"""
    _make_package(experts_dir, "expert-a", "threat-modeling")
    adapter = await _make_adapter_with_reconciling_rail()
    session = _FakeSession()
    rail = _rail(adapter)

    await adapter.apply_expert("expert-a")
    await rail.before_model_call(_ctx(session))  # baseline 含 threat-modeling
    names_before = _baseline_skill_names(rail, session)
    assert "threat-modeling" in names_before

    # 不卸载，再跑一轮 before_model_call：skill 仍在 skills_dir，baseline 应保持
    await rail.before_model_call(_ctx(session))
    names_after = _baseline_skill_names(rail, session)
    assert names_after == names_before, (
        "skill 仍挂载时 reconcile 不应改动 baseline"
    )
    assert "threat-modeling" in _skills_section_text(adapter)


@pytest.mark.asyncio
async def test_load_into_empty_baseline_reconciles_up(experts_dir: Path) -> None:
    """baseline 首建于无专家时为空，装载后 reconcile 补齐。

    复现用户现象：会话先空跑一轮（建空 baseline）→ 装载 senior-developer → 下一轮
    before_model_call 前 baseline 仍空（agent-core 只建首份不刷新）→ 主 system # 技能
    段显示 NO_SKILL、attachment 把专家技能当 additions 注入「新增可用」。子类 _reconcile
    的增方向应把 baseline 补齐为 self.skills，使主 system 段列出技能、attachment 不再注入。
    """
    _make_package(experts_dir, "expert-a", "threat-modeling")
    adapter = await _make_adapter_with_reconciling_rail()
    session = _FakeSession()
    rail = _rail(adapter)

    # 会话首启（无专家）：先跑一轮 before_model_call 建 baseline=self.skills=空
    await rail.before_model_call(_ctx(session))
    assert _baseline_skill_names(rail, session) == set(), (
        "无专家时首份 baseline 应为空"
    )
    assert "当前任务没有选择任何技能" in _skills_section_text(adapter), (
        "无专家时主 system # 技能 段应为 NO_SKILL fallback"
    )

    # 装载专家：self.skills 变 3…（此处单技 threat-modeling），但 baseline 仍旧空
    await adapter.apply_expert("expert-a")
    assert "threat-modeling" in {s.name for s in rail.skills}, (
        "装载后 self.skills 应含专家 skill"
    )
    assert _baseline_skill_names(rail, session) == set(), (
        "装载后、子类 reconcile 前，baseline 仍空（复现 PM-18 第二层增方向残留）"
    )

    # 下一轮 before_model_call：子类 _reconcile 增方向把 baseline 补齐为 self.skills
    await rail.before_model_call(_ctx(session))
    assert "threat-modeling" in _baseline_skill_names(rail, session), (
        "reconcile 后 baseline 应补齐含装载的专家 skill"
    )
    assert "threat-modeling" in _skills_section_text(adapter), (
        "reconcile 后主 system # 技能 段应列出专家 skill，而非 NO_SKILL"
    )
    assert "当前任务没有选择任何技能" not in _skills_section_text(adapter), (
        "reconcile 后主 system # 技能 段不应再是 NO_SKILL fallback"
    )
    attachment = await _skill_attachment_content(adapter)
    assert attachment is None or "新增可用" not in attachment, (
        "reconcile 后不应再注入「新增可用」skill attachment diff"
    )
