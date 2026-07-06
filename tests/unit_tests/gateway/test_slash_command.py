# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""jiuwenswarm.gateway.slash_command 单元测试."""

import importlib.util
from pathlib import Path
import sys
import pytest

# 避免 `import jiuwenswarm.gateway.slash_command` 触发 `jiuwenswarm.gateway.__init__`
# 进而级联导入 channel/wecom/lark_oapi，在开启 warning->error 的 CI 中导致 collection 失败。
_MODULE_PATH = (
        Path(__file__).resolve().parents[
            3] / "jiuwenswarm" / "gateway" / "message_handler" / "command_parser" / "slash_command.py"
)
_SPEC = importlib.util.spec_from_file_location("ut_gateway_slash_command", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)

CONTROL_MESSAGE_TEXTS = _MOD.CONTROL_MESSAGE_TEXTS
FIRST_BATCH_REGISTRY = _MOD.FIRST_BATCH_REGISTRY
ParsedControlAction = _MOD.ParsedControlAction
VALID_MODE_LINES = _MOD.VALID_MODE_LINES
VALID_SWITCH_LINES = _MOD.VALID_SWITCH_LINES
format_skills_list_for_notice = _MOD.format_skills_list_for_notice
is_control_like_for_im_batching = _MOD.is_control_like_for_im_batching
parse_channel_control_text = _MOD.parse_channel_control_text


@pytest.mark.parametrize(
    ("text", "action", "subcommand", "branch_name", "rewind_turn"),
    [
        ("", ParsedControlAction.NONE, None, None, None),
        ("hello", ParsedControlAction.NONE, None, None, None),
        ("/new_session", ParsedControlAction.NEW_SESSION_OK, None, None, None),
        ("/new_session x", ParsedControlAction.NEW_SESSION_BAD, None, None, None),
        ("/mode agent", ParsedControlAction.MODE_OK, ("agent", None), None, None),
        ("/mode code", ParsedControlAction.MODE_OK, ("code", None), None, None),
        ("/mode team", ParsedControlAction.MODE_OK, ("team", None), None, None),
        ("/mode agent.plan", ParsedControlAction.MODE_OK, ("agent.plan", None), None, None),
        ("/mode agent.fast", ParsedControlAction.MODE_OK, ("agent.fast", None), None, None),
        ("/mode code.plan", ParsedControlAction.MODE_OK, ("code.plan", None), None, None),
        ("/mode code.normal", ParsedControlAction.MODE_OK, ("code.normal", None), None, None),
        ("/mode code.team", ParsedControlAction.MODE_OK, ("code.team", None), None, None),
        ("/mode plan", ParsedControlAction.MODE_BAD, (None, None), None, None),
        ("/mode", ParsedControlAction.MODE_BAD, (None, None), None, None),
        ("/switch plan", ParsedControlAction.SWITCH_OK, (None, "plan"), None, None),
        ("/switch fast", ParsedControlAction.SWITCH_OK, (None, "fast"), None, None),
        ("/switch normal", ParsedControlAction.SWITCH_OK, (None, "normal"), None, None),
        ("/switch team", ParsedControlAction.SWITCH_OK, (None, "team"), None, None),
        ("/switch code", ParsedControlAction.SWITCH_BAD, (None, None), None, None),
        ("/switch", ParsedControlAction.SWITCH_BAD, (None, None), None, None),
        ("/skills", ParsedControlAction.NONE, None, None, None),
        ("/skills list", ParsedControlAction.SKILLS_OK, None, None, None),
        ("/skills   list", ParsedControlAction.SKILLS_OK, None, None, None),
        ("/skills extra", ParsedControlAction.NONE, None, None, None),
        ("line1\nline2", ParsedControlAction.NONE, None, None, None),
        ("/branch", ParsedControlAction.BRANCH_OK, None, "", None),
        ("/branch fix-login", ParsedControlAction.BRANCH_OK, None, "fix-login", None),
        ("/branch  multi word name", ParsedControlAction.BRANCH_OK, None, "multi word name", None),
        ("/rewind 3", ParsedControlAction.REWIND_OK, None, None, 3),
        ("/rewind 1", ParsedControlAction.REWIND_OK, None, None, 1),
        ("/rewind", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind abc", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind 0", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind -1", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind confirm 3", ParsedControlAction.REWIND_CONFIRM, None, None, 3),
        ("/rewind confirm 1", ParsedControlAction.REWIND_CONFIRM, None, None, 1),
        ("/rewind confirm 0", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind confirm abc", ParsedControlAction.REWIND_BAD, None, None, None),
        ("/rewind cancel", ParsedControlAction.REWIND_CANCEL, None, None, None),
        ("/review", ParsedControlAction.REVIEW_OK, None, None, None),
        ("/review 123", ParsedControlAction.REVIEW_OK, None, None, None),
        (
            "/review https://github.com/org/repo/pull/123",
            ParsedControlAction.REVIEW_OK,
            None,
            None,
            None,
        ),
        ("/review abc", ParsedControlAction.REVIEW_OK, None, None, None),
        ("/review 123 focus on security", ParsedControlAction.REVIEW_OK, None, None, None),
        ("/review bb37e71c33b87199", ParsedControlAction.REVIEW_OK, None, None, None),
        ("/review bad-arg", ParsedControlAction.REVIEW_OK, None, None, None),
        ("/security-review", ParsedControlAction.SECURITY_REVIEW_OK, None, None, None),
        (
            "/security-review focus on auth",
            ParsedControlAction.SECURITY_REVIEW_OK,
            None,
            None,
            None,
        ),
    ],
)
def test_parse_channel_control_text(
    text: str,
    action: ParsedControlAction,
    subcommand: tuple[str | None, str | None] | None,
    branch_name: str | None,
    rewind_turn: int | None,
) -> None:
    p = parse_channel_control_text(text)
    assert p.action is action
    if subcommand:
        assert p.mode_subcommand == subcommand[0]
        assert p.switch_subcommand == subcommand[1]
    else:
        assert p.mode_subcommand is None
        assert p.switch_subcommand is None
    assert p.branch_name == branch_name
    assert p.rewind_turn == rewind_turn
    if action is ParsedControlAction.REVIEW_OK:
        if text == "/review":
            assert p.pr_arg == ""
        elif text.startswith("/review "):
            assert p.pr_arg == text[len("/review "):].strip()
    elif action is ParsedControlAction.REVIEW_BAD:
        assert p.pr_arg is None
    elif action is ParsedControlAction.SECURITY_REVIEW_OK:
        if text == "/security-review":
            assert p.security_review_arg == ""
        elif text.startswith("/security-review "):
            assert p.security_review_arg == text[len("/security-review "):].strip()
    elif action is ParsedControlAction.SECURITY_REVIEW_BAD:
        assert p.security_review_arg is None


def test_parse_channel_control_text_review_rejects_unsafe_args() -> None:
    too_long = "x" * 2049
    p = parse_channel_control_text(f"/review {too_long}")
    assert p.action is ParsedControlAction.REVIEW_BAD

    p = parse_channel_control_text("/review bad\x00arg")
    assert p.action is ParsedControlAction.REVIEW_BAD


def test_parse_channel_control_text_security_review_rejects_unsafe_args() -> None:
    too_long = "x" * 2049
    p = parse_channel_control_text(f"/security-review {too_long}")
    assert p.action is ParsedControlAction.SECURITY_REVIEW_BAD

    p = parse_channel_control_text("/security-review bad\x00arg")
    assert p.action is ParsedControlAction.SECURITY_REVIEW_BAD


def test_control_message_texts_contains_mode_variants_and_skills() -> None:
    assert "/new_session" in CONTROL_MESSAGE_TEXTS
    assert "/skills list" in CONTROL_MESSAGE_TEXTS
    assert VALID_MODE_LINES <= CONTROL_MESSAGE_TEXTS
    assert VALID_SWITCH_LINES <= CONTROL_MESSAGE_TEXTS
    assert "/mode team" in CONTROL_MESSAGE_TEXTS
    assert "/mode code" in CONTROL_MESSAGE_TEXTS
    assert "/mode agent.plan" in CONTROL_MESSAGE_TEXTS
    assert "/mode code.normal" in CONTROL_MESSAGE_TEXTS
    assert "/mode code.team" in CONTROL_MESSAGE_TEXTS
    assert "/switch normal" in CONTROL_MESSAGE_TEXTS
    assert "/switch team" in CONTROL_MESSAGE_TEXTS
    assert "/branch" in CONTROL_MESSAGE_TEXTS
    assert "/rewind" in CONTROL_MESSAGE_TEXTS


def test_is_control_like_for_im_batching() -> None:
    assert is_control_like_for_im_batching("/new_session")
    assert is_control_like_for_im_batching("/mode agent")
    assert is_control_like_for_im_batching("/mode agent.plan")
    assert is_control_like_for_im_batching("/mode foo")
    assert is_control_like_for_im_batching("/switch plan")
    assert is_control_like_for_im_batching("/switch foo")
    assert is_control_like_for_im_batching("/new_sessionoops")
    assert is_control_like_for_im_batching("/skills list")
    assert is_control_like_for_im_batching("/skills   list")
    assert is_control_like_for_im_batching("/branch")
    assert is_control_like_for_im_batching("/branch fix-login")
    assert is_control_like_for_im_batching("/rewind 3")
    assert is_control_like_for_im_batching("/rewind")
    assert is_control_like_for_im_batching("/review")
    assert is_control_like_for_im_batching("/review 123")
    assert is_control_like_for_im_batching("/review bad-arg")
    assert is_control_like_for_im_batching("/security-review")
    assert is_control_like_for_im_batching("/security-review focus on auth")
    assert not is_control_like_for_im_batching("/skills")
    assert not is_control_like_for_im_batching("/skills extra")
    assert not is_control_like_for_im_batching("")
    assert not is_control_like_for_im_batching("a\nb")


def test_format_skills_list_for_notice() -> None:
    out = format_skills_list_for_notice(
        {
            "skills": [
                {"name": "a", "description": "d1", "source": "local"},
                {"name": "b"},
            ]
        }
    )
    assert "【技能列表】" in out
    assert "a" in out
    assert "b" in out


def test_format_skills_list_for_notice_im_invariants() -> None:
    """IM 通道（微信等）仅从 payload.content 取文本，skills.list 载荷必须能渲染出非空 content。

    /skills list 在 IM 端无返回的根因：skills.list 响应 ``{"skills": [...]}`` 不含
    ``content``，被通道当作空消息丢弃。这里锁定 _skills_slash_notice 依赖的渲染入口
    在成功/空/错误三态下都产出可下发文本。
    """
    # 成功：有技能
    ok_text = format_skills_list_for_notice(
        {"skills": [{"name": "a", "description": "d", "source": "local"}]}
    )
    assert ok_text and ok_text.strip()
    assert "【技能列表】" in ok_text

    # 空：无技能
    empty_text = format_skills_list_for_notice({"skills": []})
    assert empty_text and empty_text.strip()

    # 错误：上游返回 error 字段
    err_text = format_skills_list_for_notice({"error": "boom"})
    assert err_text and err_text.strip()
    assert "boom" in err_text

    # 异常：载荷为 None / 非 dict
    assert format_skills_list_for_notice(None).strip()
    assert format_skills_list_for_notice({}).strip()


def test_first_batch_registry_ids() -> None:
    ids = {e.id for e in FIRST_BATCH_REGISTRY}
    expected = {
        "new_session", "mode", "switch", "skills", "resume",
        "workspace_dir", "branch", "rewind", "recap", "agents", "review", "security-review",
    }
    assert ids == expected
