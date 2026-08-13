"""Lock that process_team_message_stream threads request_id/channel_id
into its get_swarm_enriched_team_spec call (regression: team_helpers.py:1584
omitted both kwargs -> SwarmBuildContext request_id=None/channel='default'
-> SendFileToUserToolRail gate has_ctx=False -> send_file_to_user never
registered -> no authoritative chat.file -> no frontend file card)."""
from __future__ import annotations
import ast
import inspect

import jiuwenclaw.agentserver.deep_agent.team_helpers as th


def _get_swarm_enriched_calls() -> list[ast.Call]:
    """Return every get_swarm_enriched_team_spec(...) Call node inside
    process_team_message_stream's own source (targeted, not whole-module)."""
    src = inspect.getsource(th.process_team_message_stream)
    tree = ast.parse(src)  # parse the function source (async def ...)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        # await team_manager.get_swarm_enriched_team_spec(...) -> ast.Await(ast.Call(...))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get_swarm_enriched_team_spec":
                calls.append(node)
    return calls


def test_1584_threads_request_id_and_channel_id():
    """正路径：:1584 调用必须带 request_id= / channel_id= 关键字。"""
    calls = _get_swarm_enriched_calls()
    assert calls, (
        "expected a get_swarm_enriched_team_spec call in "
        "process_team_message_stream (team_helpers.py:~1584)")
    kw_args = {kw.arg for kw in calls[0].keywords}
    assert "request_id" in kw_args, (
        "team_helpers.py:1584 漏传 request_id=rid -> SwarmBuildContext.request_id=None "
        "-> RuntimeInfo.request_id=None -> SendFileToUserToolRail has_ctx=False")
    assert "channel_id" in kw_args, (
        "team_helpers.py:1584 漏传 channel_id=channel_id -> SwarmBuildContext.channel='default' "
        "-> gate channel != 'officeclaw' 且 send_file_enabled=False")


def test_1584_passes_in_scope_rid_and_channel_id_names():
    """失败/边界：值必须是已在作用域的 rid / channel_id 变量（:1427-1428），
    而非字面 None 或其它符号——锁定"接上已有值"而非"新造值"。"""
    calls = _get_swarm_enriched_calls()
    assert calls
    rid_kw = next(kw for kw in calls[0].keywords if kw.arg == "request_id")
    assert isinstance(rid_kw.value, ast.Name) and rid_kw.value.id == "rid", (
        "request_id= 必须传 :1427 提取的 rid，而非字面/其它")
    ch_kw = next(kw for kw in calls[0].keywords if kw.arg == "channel_id")
    assert isinstance(ch_kw.value, ast.Name) and ch_kw.value.id == "channel_id", (
        "channel_id= 必须传 :1428 提取的 channel_id，而非字面/其它")


def test_1584_is_the_only_get_swarm_enriched_call_site():
    """同源检查：process_team_message_stream 内该调用点唯一（无第二个漏传点）。"""
    calls = _get_swarm_enriched_calls()
    assert len(calls) == 1, (
        f"expected exactly 1 get_swarm_enriched_team_spec call, got {len(calls)}")
