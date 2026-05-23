# coding: utf-8
from __future__ import annotations

from jiuwenswarm.agents.harness.common.security_review.skill_state import collect_skill_state


def test_collect_skill_state_reads_skill_metadata(tmp_path):
    skill_dir = tmp_path / "safe-shell"
    skill_dir.mkdir()
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: safe-shell
description: Safe shell execution
---

# Safe Shell

## Security

Avoid curl pipe shell and credential access.
""",
        encoding="utf-8",
    )

    state = collect_skill_state(tmp_path)

    assert state["loaded_skills"][0]["name"] == "safe-shell"
    assert state["loaded_skills"][0]["description"] == "Safe shell execution"
    assert "Avoid curl pipe shell" in state["loaded_skills"][0]["security_sections"][0]
    assert state["known_security_skill_names"] == ["safe-shell"]


def test_collect_skill_state_ignores_non_skill_dirs(tmp_path):
    tmp_path.joinpath("notes").mkdir()

    state = collect_skill_state(tmp_path)

    assert state["loaded_skills"] == []
    assert state["known_security_skill_names"] == []
