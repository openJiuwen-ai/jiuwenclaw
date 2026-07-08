# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""build_security_review_prompt 单元测试."""

import pytest

from jiuwenswarm.gateway.message_handler.prompts.security_review_prompt import (
    build_security_review_prompt,
)


@pytest.fixture(autouse=True)
def _english_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.gateway.message_handler.prompts.get_config",
        lambda: {"preferred_language": "en"},
    )


def test_build_security_review_prompt_runs_git_commands_in_english() -> None:
    prompt = build_security_review_prompt("")
    assert "`git status`" in prompt
    assert "`git diff --name-only origin/HEAD...`" in prompt
    assert "`git log --no-decorate origin/HEAD...`" in prompt
    assert "`git diff origin/HEAD...`" in prompt
    assert "Respond in English." in prompt
    assert "HIGH-CONFIDENCE security vulnerabilities" in prompt
    assert "FALSE POSITIVE FILTERING" in prompt
    assert "Additional instructions:" not in prompt
    assert "`gh pr" not in prompt


def test_build_security_review_prompt_passes_through_args() -> None:
    prompt = build_security_review_prompt("focus on auth bypass")
    assert prompt.rstrip().endswith("Additional instructions: focus on auth bypass")
    assert "Respond in English." in prompt


def test_build_security_review_prompt_chinese_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.gateway.message_handler.prompts.get_config",
        lambda: {"preferred_language": "zh"},
    )
    prompt = build_security_review_prompt("")
    assert "Respond in Chinese (simplified)." in prompt
