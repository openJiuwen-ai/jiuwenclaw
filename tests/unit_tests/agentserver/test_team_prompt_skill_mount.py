from __future__ import annotations

from pathlib import Path

import jiuwenclaw.agentserver.team.prompt_skill_mount as prompt_mount

from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    build_member_rails,
)


def _make_skill(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(marker, encoding="utf-8")
    return skill_dir


def _metadata_stubs(monkeypatch):
    metadata: dict[str, object] = {}

    monkeypatch.setattr(prompt_mount, "get_session_metadata", lambda _session_id: dict(metadata))

    def persist(_session_id: str, value: dict[str, object]) -> None:
        metadata.clear()
        metadata.update(value)

    monkeypatch.setattr(prompt_mount, "_enqueue_write", persist)
    return metadata


def test_extracts_multiple_prompt_skills_and_rejects_path_segments() -> None:
    query = (
        "使用 docx-craft 技能 使用 多引擎搜索 技能；"
        "使用 ../escape 技能，使用 DOCX-CRAFT 技能"
    )

    assert prompt_mount.extract_prompt_skill_names(query) == ["docx-craft", "多引擎搜索"]


def test_resolve_roots_promotes_official_before_user(tmp_path: Path, monkeypatch) -> None:
    official = tmp_path / "official"
    user = tmp_path / "user"
    official.mkdir()
    user.mkdir()
    (official / "BOOTSTRAP.md").write_text("official", encoding="utf-8")

    monkeypatch.setattr(prompt_mount, "get_shared_agent_skills_dirs", lambda: [user, official])
    monkeypatch.setattr(prompt_mount, "get_agent_skills_dir", lambda: user)

    assert prompt_mount.resolve_prompt_skill_roots() == [official.resolve(), user.resolve()]


def test_mount_prefers_official_root_and_accumulates_for_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _metadata_stubs(monkeypatch)
    official = tmp_path / "official"
    user = tmp_path / "user"
    target = tmp_path / "leader-skills"
    official.mkdir()
    user.mkdir()
    (official / "BOOTSTRAP.md").write_text("official", encoding="utf-8")
    official_docx = _make_skill(official, "docx-craft", "official")
    _make_skill(user, "docx-craft", "user")
    user_search = _make_skill(user, "multi-search", "user")

    linked: list[tuple[Path, Path]] = []

    def fake_link(source: Path, destination: Path) -> None:
        linked.append((source, destination))
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(
            (source / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    monkeypatch.setattr(prompt_mount, "link_skill_dir", fake_link)

    first = prompt_mount.mount_leader_prompt_skills(
        session_id="session-1",
        query="使用 docx-craft 技能 写一份文档",
        target_dir=target,
        skill_roots=[official, user],
    )
    second = prompt_mount.mount_leader_prompt_skills(
        session_id="session-1",
        query="使用 multi-search 技能 继续处理",
        target_dir=target,
        skill_roots=[official, user],
    )

    assert first.selected_names == ("docx-craft",)
    assert second.selected_names == ("docx-craft", "multi-search")
    assert second.mounted_names == ("docx-craft", "multi-search")
    assert linked[0][0] == official_docx
    assert linked[1][0] == user_search


def test_missing_skill_is_persisted_and_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    metadata = _metadata_stubs(monkeypatch)

    result = prompt_mount.mount_leader_prompt_skills(
        session_id="session-2",
        query="使用 unavailable 技能 完成任务",
        target_dir=tmp_path / "leader-skills",
        skill_roots=[tmp_path / "missing-root"],
    )

    assert result.selected_names == ("unavailable",)
    assert result.mounted_names == ()
    assert result.missing_names == ("unavailable",)
    assert metadata["team_leader_prompt_skills"] == ["unavailable"]


def test_follow_up_without_phrase_keeps_prior_selection(tmp_path: Path, monkeypatch) -> None:
    _metadata_stubs(monkeypatch)
    root = tmp_path / "official"
    root.mkdir()
    _make_skill(root, "docx-craft", "official")

    monkeypatch.setattr(
        prompt_mount,
        "link_skill_dir",
        lambda _source, destination: destination.mkdir(parents=True),
    )

    prompt_mount.mount_leader_prompt_skills(
        session_id="session-3",
        query="使用 docx-craft 技能 创建文档",
        target_dir=tmp_path / "leader-skills",
        skill_roots=[root],
    )
    follow_up = prompt_mount.mount_leader_prompt_skills(
        session_id="session-3",
        query="继续补充第三章",
        target_dir=tmp_path / "leader-skills",
        skill_roots=[root],
    )

    assert follow_up.selected_names == ("docx-craft",)
    assert follow_up.mounted_names == ("docx-craft",)


def test_leader_uses_dedicated_prompt_skill_view(monkeypatch) -> None:
    from openjiuwen.harness.rails.skill_use_rail import SkillUseRail

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.HeartbeatRail",
        None,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.get_context_engine_enabled",
        lambda _config: False,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_skill_rails",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_disabled_tools_rail",
        lambda *_args, **_kwargs: None,
    )

    rails = build_member_rails(
        member_info=MemberInfo(agent_name="leader", role="leader"),
        runtime=RuntimeInfo(channel="web", language="cn"),
        team_workspace=TeamWorkspaceInfo(
            root_dir="team-root",
            skills_dir="team-root/skills",
            leader_skills_dir="team-root/leader-skills",
            config={},
        ),
    )

    skill_rails = [rail for rail in rails if isinstance(rail, SkillUseRail)]
    assert len(skill_rails) == 1
    assert skill_rails[0].skills_dir == ["team-root/leader-skills"]
