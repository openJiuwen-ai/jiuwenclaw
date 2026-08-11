"""Web 组合模式解析与 TUI 历史模式直通的回归测试。"""

import pytest

from jiuwenswarm.common.mode_matrix import (
    base_mode_without_plan,
    base_mode_without_plan_new,
    deprecate_mode,
    DEPRECATION_MAP,
    is_code_profile_mode,
    is_new_canonical_mode,
    is_plan_mode,
    is_plan_mode_new,
    is_team_mode,
    is_team_plan_mode,
    NEW_AGENT_WORK_NORMAL,
    NEW_AGENT_WORK_PLAN,
    NEW_CANONICAL_MODES,
    resolve_request_mode,
)
from jiuwenswarm.server.agent_ws_server import resolve_agent_request_mode


def _resolve(params):
    return resolve_request_mode(params, resolve_agent_request_mode)


# ── Web 组合：只覆盖单 agent，work_mode 决定 profile，mode 决定是否 plan ─────


@pytest.mark.parametrize(
    ("mode", "work_mode", "expected"),
    [
        ("agent", "work", ("agent", None, "agent")),
        ("agent.plan", "work", ("agent", "plan", "agent.plan")),
        ("agent", "code", ("code", "normal", "code.normal")),
        ("agent.plan", "code", ("code", "plan", "code.plan")),
    ],
)
def test_web_composition_covers_all_single_agent_combinations(mode, work_mode, expected):
    resolved = _resolve({"mode": mode, "work_mode": work_mode})

    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == expected
    assert resolved.from_web_composition is True
    assert resolved.is_code_profile is (work_mode == "code")


@pytest.mark.parametrize(
    ("mode", "work_mode", "expected"),
    [
        # P6.4：新串直通 canonical——work 折叠进串，work_mode 不改变 canonical
        (NEW_AGENT_WORK_NORMAL, "work", ("agent", None, NEW_AGENT_WORK_NORMAL)),
        (NEW_AGENT_WORK_PLAN, "work", ("agent", "plan", NEW_AGENT_WORK_PLAN)),
        ("agent.code.normal", "work", ("code", None, "agent.code.normal")),
        ("agent.code.plan", "work", ("code", "plan", "agent.code.plan")),
        # work_mode=code 不改变新 work 串的 canonical（串优先于 work_mode）
        (NEW_AGENT_WORK_NORMAL, "code", ("agent", None, NEW_AGENT_WORK_NORMAL)),
        (NEW_AGENT_WORK_PLAN, "code", ("agent", "plan", NEW_AGENT_WORK_PLAN)),
    ],
)
def test_web_composition_new_canonical_passes_through(mode, work_mode, expected):
    """P6.4：新三段 canonical 经 Web 组合分支直通，canonical = mode_text 自身。"""
    resolved = _resolve({"mode": mode, "work_mode": work_mode})

    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == expected
    assert resolved.from_web_composition is True


@pytest.mark.parametrize(
    ("mode", "work_mode", "is_code"),
    [
        # P6.4：新串 profile 从串本身解析，不看 work_mode
        (NEW_AGENT_WORK_NORMAL, "work", False),
        (NEW_AGENT_WORK_NORMAL, "code", False),
        ("agent.code.normal", "work", True),
        ("agent.code.normal", "code", True),
        ("agent.code.plan", "work", True),
        (NEW_AGENT_WORK_PLAN, "code", False),
    ],
)
def test_web_composition_new_canonical_profile_from_string(mode, work_mode, is_code):
    """新串的 is_code_profile 由串本身决定，不因 work_mode 错判。"""
    resolved = _resolve({"mode": mode, "work_mode": work_mode})

    assert resolved.is_code_profile is is_code
    assert resolved.profile == ("code" if is_code else "normal")


@pytest.mark.parametrize(
    ("mode", "work_mode", "expected_plan"),
    [
        ("agent", "work", False),
        ("agent.plan", "work", True),
        ("agent.plan", "code", True),
    ],
)
def test_web_composition_plan_flag(mode, work_mode, expected_plan):
    resolved = _resolve({"mode": mode, "work_mode": work_mode})

    assert resolved.is_plan is expected_plan
    assert resolved.is_team is False


@pytest.mark.parametrize("work_mode", ["work", "code"])
def test_web_team_is_not_composable(work_mode):
    """集群不参与组合：``work_mode`` 不得改变集群的 Adapter 选型。

    Web 集群必须与改造前完全一致——``team`` 走历史解析，manager_mode 保持
    ``team``（即 DeepAdapter），不会因为 ``work_mode=code`` 变成 ``code.team``。
    """
    resolved = _resolve({"mode": "team", "work_mode": work_mode})

    assert resolved.from_web_composition is False
    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == (
        "team",
        None,
        "team",
    )
    assert resolved.is_team is True


@pytest.mark.parametrize(
    ("mode", "work_mode", "expected_normal"),
    [
        ("agent.plan", "work", "agent"),
        ("agent.plan", "code", "code.normal"),
    ],
)
def test_plan_exit_mode_is_profile_aware(mode, work_mode, expected_normal):
    assert _resolve({"mode": mode, "work_mode": work_mode}).normal_mode == expected_normal


@pytest.mark.parametrize("work_mode", ["work", "code"])
def test_web_team_plan_is_not_composable(work_mode):
    """Team Plan 不参与 Web 组合，正式别名始终选择 normal profile。"""
    resolved = _resolve({"mode": "team.plan", "work_mode": work_mode})

    assert resolved.from_web_composition is False
    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == (
        "team",
        "plan",
        "team.plan.normal",
    )
    assert resolved.profile == "normal"


# ── TUI / CLI / cron：不带 work_mode 时必须完全走历史解析 ───────────────────


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        ("agent", ("agent", None, "agent")),
        ("agent.plan", ("agent", None, "agent")),
        ("agent.fast", ("agent", None, "agent")),
        ("plan", ("agent", None, "agent")),
        ("code.normal", ("code", "normal", "code.normal")),
        ("code.plan", ("code", "plan", "code.plan")),
        ("code.team", ("code", "team", "code.team")),
        ("team", ("team", None, "team")),
        ("team.plan", ("team", "plan", "team.plan.normal")),
        ("team.plan.normal", ("team", "plan", "team.plan.normal")),
        ("team.plan.code", ("code", "team", "team.plan.code")),
    ],
)
def test_legacy_modes_are_untouched_without_work_mode(raw_mode, expected):
    resolved = _resolve({"mode": raw_mode})

    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == expected
    assert resolved.from_web_composition is False


@pytest.mark.parametrize(
    "raw_mode", ["code.plan", "code.team", "team.plan.normal", "team.plan.code", "agent.fast"]
)
def test_legacy_full_modes_ignore_work_mode(raw_mode):
    """即便某个客户端同时带了 work_mode，完整模式串仍按历史语义解析。

    ``code.normal`` 不在此列：它是 ``resolve_agent_request_mode`` 早就会按
    ``work_mode`` 改写的"可归属"模式，见
    :func:`test_legacy_neutral_modes_still_follow_work_mode`。
    """
    with_work = _resolve({"mode": raw_mode, "work_mode": "work"})
    without_work = _resolve({"mode": raw_mode})

    assert with_work.canonical_mode == without_work.canonical_mode
    assert with_work.manager_mode == without_work.manager_mode
    assert with_work.from_web_composition is False


@pytest.mark.parametrize(
    ("raw_mode", "work_mode", "expected"),
    [
        ("code.normal", "work", ("agent", None, "agent")),
        ("code.normal", "code", ("code", "normal", "code.normal")),
        ("code", "work", ("agent", None, "agent")),
        ("agent", "code", ("code", "normal", "code.normal")),
    ],
)
def test_legacy_neutral_modes_still_follow_work_mode(raw_mode, work_mode, expected):
    """``agent`` / ``code`` / ``code.normal`` 由 work_mode 决定归属（历史行为）。

    这三个取值只表达"普通单 agent"，不表达工作环境，因此 ``resolve_agent_request_mode``
    在 Web 组合模式引入之前就会用 ``work_mode``（通常来自会话 metadata）改写它们。
    组合分支不接管这些请求（``code.normal`` 等不是 Web 组合值，``agent`` 则由
    组合分支给出同样的结果），此处把这条历史约定钉住。
    """
    resolved = _resolve({"mode": raw_mode, "work_mode": work_mode})

    assert (resolved.manager_mode, resolved.sub_mode, resolved.canonical_mode) == expected


def test_invalid_work_mode_falls_back_to_legacy():
    resolved = _resolve({"mode": "agent.plan", "work_mode": "nonsense"})

    assert resolved.from_web_composition is False
    assert resolved.canonical_mode == "agent"


def test_missing_mode_defaults_to_agent():
    assert _resolve({}).canonical_mode == "agent"


# ── 纯函数 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("agent.plan", True),
        ("code.plan", True),
        ("team.plan", True),
        ("team.plan.normal", True),
        ("team.plan.code", True),
        ("agent", False),
        ("team", False),
        ("code.normal", False),
        ("code.team", False),
    ],
)
def test_is_plan_mode(mode, expected):
    assert is_plan_mode(mode) is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("team", True),
        ("team.plan", True),
        ("team.plan.normal", True),
        ("team.plan.code", True),
        ("code.team", True),
        ("agent", False),
        ("agent.plan", False),
        ("code.plan", False),
    ],
)
def test_is_team_mode(mode, expected):
    assert is_team_mode(mode) is expected


def test_base_mode_without_plan_is_identity_for_normal_modes():
    assert base_mode_without_plan("agent") == "agent"
    assert base_mode_without_plan("code.team") == "code.team"


@pytest.mark.parametrize(
    "mode", ["team", "team.plan", "team.plan.normal", "team.plan.code", "code.team"]
)
def test_team_modes_are_team_params(mode):
    from jiuwenswarm.server.utils.utils import is_team_params

    assert is_team_params({"mode": mode})


# ── P1：新三段命名 canonical + 旧→新静默映射（纯加法）──────────────────────
#
# 五条兼容铁律：旧输入→新输出 / 新输入直通 / 未知值不动 / 持久化旧数据可读 / 写回不丢失。
# 详见 PLAN_mode_refactor_phased.md P1 与「历史兼容性横切约束」。


def test_deprecation_map_covers_all_legacy_canonical():
    """铁律 1 前置：9 个旧 canonical 全部在 DEPRECATION_MAP 里。"""
    legacy = {
        "agent",
        "agent.plan",
        "agent.fast",
        "code.normal",
        "code.plan",
        "code.team",
        "team",
        "team.plan.normal",
        "team.plan.code",
    }
    assert legacy <= DEPRECATION_MAP.keys()


@pytest.mark.parametrize(("old", "new"), sorted(DEPRECATION_MAP.items()))
def test_deprecate_mode_silently_maps(old, new):
    """铁律 1：旧输入 → 新输出，且目标是新 canonical。"""
    assert deprecate_mode(old) == new
    assert is_new_canonical_mode(new)


@pytest.mark.parametrize("mode", sorted(NEW_CANONICAL_MODES))
def test_new_mode_is_idempotent(mode):
    """铁律 2：新输入直通，不被二次转译。"""
    assert deprecate_mode(mode) == mode


def test_unknown_mode_passes_through():
    """铁律 3：未识别串原样返回，不破坏未知值。

    注意空值/None 不在此列——``normalize_mode_text`` 既有行为把空值回落到
    ``agent``，再经 ``DEPRECATION_MAP`` 映射成 ``agent.work.normal``。这是
    归一化在映射之前的正确顺序，不是"未知值不动"的违反。
    """
    assert deprecate_mode("unknown_mode") == "unknown_mode"
    assert deprecate_mode("custom.profile") == "custom.profile"


def test_empty_and_none_normalize_before_deprecate():
    """空值/None 经 normalize_mode_text 回落 agent，再映射成新 canonical。

    钉住"归一化先于弃用映射"的既有顺序，避免有人改成空串原样返回破坏默认值语义。
    """
    assert deprecate_mode("") == NEW_AGENT_WORK_NORMAL
    assert deprecate_mode(None) == NEW_AGENT_WORK_NORMAL


@pytest.mark.parametrize(
    ("mode", "plan"),
    [
        ("agent.work.normal", False),
        ("agent.work.plan", True),
        ("agent.code.normal", False),
        ("agent.code.plan", True),
        ("team.work.normal", False),
        ("team.work.plan", True),
        ("team.code.normal", False),
        ("team.code.plan", True),
    ],
)
def test_new_plan_detection(mode, plan):
    """新命名下第三段为 plan 即 plan 模式。"""
    assert is_plan_mode_new(mode) is plan


def test_new_plan_exit():
    """新命名 plan 退出后回到对应 normal 变体。"""
    assert base_mode_without_plan_new("agent.work.plan") == "agent.work.normal"
    assert base_mode_without_plan_new("team.code.plan") == "team.code.normal"
    assert base_mode_without_plan_new("agent.code.plan") == "agent.code.normal"
    assert base_mode_without_plan_new("team.work.plan") == "team.work.normal"


def test_new_plan_exit_identity_for_non_plan():
    """非新 plan 模式原样返回。"""
    assert base_mode_without_plan_new("agent.work.normal") == "agent.work.normal"
    assert base_mode_without_plan_new("agent") == "agent"
    assert base_mode_without_plan_new("unknown") == "unknown"


# ── P1.5：新串接入旧判断集合（现有用例只断言旧串，此处补新串断言）────────────


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # 新串也必须是 team（P1.5 扩 TEAM_CANONICAL_MODES）
        ("team.work.normal", True),
        ("team.work.plan", True),
        ("team.code.normal", True),
        ("team.code.plan", True),
        # 旧串仍在集合里（回归）
        ("team", True),
        ("team.plan.normal", True),
        ("team.plan.code", True),
        ("code.team", True),
        # 非 team 新串
        ("agent.work.normal", False),
        ("agent.code.plan", False),
    ],
)
def test_is_team_mode_covers_new_canonical(mode, expected):
    assert is_team_mode(mode) is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # 新 plan 变体（P1.5 扩 PLAN_CANONICAL_MODES）
        ("agent.work.plan", True),
        ("agent.code.plan", True),
        ("team.work.plan", True),
        ("team.code.plan", True),
        # 新 normal 变体非 plan
        ("agent.work.normal", False),
        ("agent.code.normal", False),
        ("team.work.normal", False),
        ("team.code.normal", False),
        # 旧串仍在（回归）
        ("agent.plan", True),
        ("code.plan", True),
        ("team.plan.normal", True),
        ("agent", False),
    ],
)
def test_is_plan_mode_covers_new_canonical(mode, expected):
    assert is_plan_mode(mode) is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # P1.5 扩 is_team_plan_mode：新 team plan 变体返 True
        ("team.work.plan", True),
        ("team.code.plan", True),
        # 旧 team plan 仍在
        ("team.plan.normal", True),
        ("team.plan.code", True),
        # 非 team plan
        ("agent.work.plan", False),
        ("agent.code.plan", False),
        ("team.work.normal", False),
        ("team.code.normal", False),
    ],
)
def test_is_team_plan_mode_covers_new_canonical(mode, expected):
    """P4 rail 路由依赖：新 team plan 串必须返 True，否则 leader 分支被跳过。"""
    assert is_team_plan_mode(mode) is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # P1.5 扩 is_code_profile_mode：新 code 变体返 True
        ("agent.code.normal", True),
        ("agent.code.plan", True),
        ("team.code.normal", True),
        ("team.code.plan", True),
        # 旧 code 仍在
        ("code.normal", True),
        ("code.plan", True),
        ("code.team", True),
        ("team.plan.code", True),
        # 非 code profile
        ("agent.work.normal", False),
        ("agent.work.plan", False),
        ("team.work.normal", False),
        ("team.work.plan", False),
    ],
)
def test_is_code_profile_mode_covers_new_canonical(mode, expected):
    """P4 rail 路由依赖：新 code 串必须返 True，否则 rail 分流错。"""
    assert is_code_profile_mode(mode) is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # 新 plan 变体退出映射（P1.5 扩 _PLAN_EXIT_MODES）
        ("agent.work.plan", "agent.work.normal"),
        ("agent.code.plan", "agent.code.normal"),
        ("team.work.plan", "team.work.normal"),
        ("team.code.plan", "team.code.normal"),
        # 旧映射仍在（回归）
        ("agent.plan", "agent"),
        ("code.plan", "code.normal"),
        ("team.plan.normal", "team"),
        ("team.plan.code", "code.team"),
        # 非 plan 原样返回
        ("agent.work.normal", "agent.work.normal"),
        ("agent", "agent"),
    ],
)
def test_base_mode_without_plan_covers_new_canonical(mode, expected):
    """base_mode_without_plan 查 _PLAN_EXIT_MODES，P1.5 扩后对新串自动生效。"""
    assert base_mode_without_plan(mode) == expected
