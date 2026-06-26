# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""PipelineInitNode 辅助逻辑单元测试。"""

from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt import pipeline_init as pi
from jiuwenclaw.agentserver.replan_agent.environment import (
    _compute_dir_checksum,
    _verify_skill_checksum,
)


@pytest.mark.unit
def test_needs_npm_install_from_check_env_output() -> None:
    output = "❌ npm 依赖未安装\n   → 安装: cd /tmp/pptx && npm install"
    assert pi._needs_npm_install(output) is True


@pytest.mark.unit
def test_needs_playwright_install_from_check_env_output() -> None:
    output = "❌ Chromium 未安装\n   → 安装: npx playwright install chromium"
    assert pi._needs_playwright_install(output) is True


@pytest.mark.unit
def test_needs_playwright_install_false_when_ready() -> None:
    output = "✅ 环境就绪，可以开始制作 PPT"
    assert pi._needs_playwright_install(output) is False


@pytest.mark.unit
def test_parse_cli_path_from_last_line() -> None:
    stdout = "Installing...\n/workspace/20260317_143052_000\n"
    assert pi._parse_cli_path(stdout).endswith("20260317_143052_000")


@pytest.mark.unit
def test_resolve_explicit_output_dir_from_inputs() -> None:
    inputs = {"output_dir": "D:/decks/my-demo"}
    resolved = pi._resolve_explicit_output_dir(inputs)
    assert resolved is not None
    assert resolved.replace("\\", "/").endswith("D:/decks/my-demo")


@pytest.mark.unit
def test_resolve_explicit_output_dir_missing() -> None:
    assert pi._resolve_explicit_output_dir({}) is None


@pytest.mark.unit
def test_resolve_timestamp_parent_dir_from_project() -> None:
    inputs = {
        "effective_project_dir": "D:/officeclaw/workspace/20260528231753",
        "conversation_id": "officeclaw_fdfbd2709b81c2dc11beb2d1",
    }
    resolved = pi._resolve_timestamp_parent_dir(inputs)
    assert resolved.replace("\\", "/").endswith(
        "officeclaw_fdfbd2709b81c2dc11beb2d1/output"
    )


@pytest.mark.unit
def test_parse_bash_payload_json() -> None:
    raw = '{"exit_code": 0, "stdout": "/tmp/out\\n", "stderr": ""}'
    result = pi._parse_bash_payload(raw)
    assert result.exit_code == 0
    assert "/tmp/out" in result.stdout


# --- [TEMP-EXTERNAL-SKILL] 新增测试 ---


@pytest.mark.unit
def test_resolve_pptx_root_from_pptx_root_input() -> None:
    """显式 pptx_root 优先级最高。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        inputs = {"pptx_root": tmpdir}
        assert pi._resolve_pptx_root(inputs) == str(
            os.path.realpath(tmpdir)
        )


@pytest.mark.unit
def test_resolve_pptx_root_from_skill_root_with_skill_name() -> None:
    """skill_root + skill_name 拼接。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "pptx-craft")
        os.makedirs(skill_dir, exist_ok=True)
        inputs = {"skill_root": tmpdir, "skill_name": "pptx-craft"}
        assert pi._resolve_pptx_root(inputs) == str(
            os.path.realpath(skill_dir)
        )


@pytest.mark.unit
def test_resolve_pptx_root_skill_root_is_skill_dir() -> None:
    """skill_root 本身就是 skill 目录（name == skill_name）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "pptx-craft")
        os.makedirs(skill_dir, exist_ok=True)
        inputs = {"skill_root": skill_dir, "skill_name": "pptx-craft"}
        assert pi._resolve_pptx_root(inputs) == str(
            os.path.realpath(skill_dir)
        )


@pytest.mark.unit
def test_resolve_pptx_root_fallback_pptx_craft() -> None:
    """skill_root 下没找到 skill_name 但有 pptx-craft 时 fallback。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_dir = os.path.join(tmpdir, "pptx-craft")
        os.makedirs(old_dir, exist_ok=True)
        inputs = {"skill_root": tmpdir, "skill_name": "pptx-craft-replan"}
        result = pi._resolve_pptx_root(inputs)
        assert result == str(os.path.realpath(old_dir))


@pytest.mark.unit
def test_resolve_pptx_root_raises_on_missing() -> None:
    """没有 pptx_root 且 skill_root 下找不到 skill_name → raise。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        inputs = {"skill_root": tmpdir}
        with pytest.raises(pi.PipelineInitError, match="pptx-craft"):
            pi._resolve_pptx_root(inputs)


@pytest.mark.unit
def test_resolve_pptx_root_raises_on_empty_inputs() -> None:
    """inputs 为空 → raise。"""
    with pytest.raises(pi.PipelineInitError):
        pi._resolve_pptx_root({})


@pytest.mark.unit
def test_compute_dir_checksum_empty_dir() -> None:
    """空目录的 checksum 是确定性的。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checksum = _compute_dir_checksum(tmpdir)
        assert isinstance(checksum, str)
        assert len(checksum) == 64  # SHA256 hex digest


@pytest.mark.unit
def test_compute_dir_checksum_deterministic() -> None:
    """相同内容产生相同 checksum。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = os.path.join(tmpdir, "scripts")
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, "cli.js"), "w", encoding="utf-8") as f:
            f.write("// test")
        checksum1 = _compute_dir_checksum(tmpdir)
        checksum2 = _compute_dir_checksum(tmpdir)
        assert checksum1 == checksum2


@pytest.mark.unit
def test_compute_dir_checksum_excludes_node_modules() -> None:
    """node_modules 目录不参与 checksum。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "node_modules"), exist_ok=True)
        with open(os.path.join(tmpdir, "scripts", "cli.js"), "w", encoding="utf-8") as f:
            f.write("// test")
        checksum_without_nm = _compute_dir_checksum(tmpdir)
        # 加 node_modules 内容不影响 checksum
        with open(
            os.path.join(tmpdir, "node_modules", "extra.js"), "w", encoding="utf-8"
        ) as f:
            f.write("// extra")
        checksum_with_nm = _compute_dir_checksum(tmpdir)
        assert checksum_without_nm == checksum_with_nm


@pytest.mark.unit
def test_verify_skill_checksum_skip_on_empty() -> None:
    """skill_checksum 为空时跳过校验（返回 True）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        assert _verify_skill_checksum(tmpdir, "") is True


@pytest.mark.unit
def test_verify_skill_checksum_match() -> None:
    """checksum 匹配时返回 True。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "scripts"), exist_ok=True)
        with open(os.path.join(tmpdir, "scripts", "cli.js"), "w", encoding="utf-8") as f:
            f.write("// test")
        actual = _compute_dir_checksum(tmpdir)
        assert _verify_skill_checksum(tmpdir, actual) is True


@pytest.mark.unit
def test_verify_skill_checksum_mismatch() -> None:
    """checksum 不匹配时返回 False。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        assert _verify_skill_checksum(tmpdir, "wrong_sha256_value") is False
