# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for cron session-model reuse (cron 直接复用 chat-session 模型配置).

背景：agent 对话创建定时任务时，openjiuwen 的 cron 工具描述没有 ``model_name``
字段，job 落库 model_name=None、执行时回退到 config 默认模型（用户自配模型 key
失效时报 401）。产品语义是：通过 chat-session 创建的 cron，其模型**直接复用**该
会话当前使用的模型配置（metadata.model，如免费模型 mimo-v2.5-free），无需 LLM
参与模型选择，因此工具描述中不注入 model_name 字段。修复后：

- 工具描述保持 openjiuwen 原样（不含 model_name 入参）；
- 新版格式（schedule/payload/delivery）创建时显式传入的 model_name 仍被透传
  （会话无模型时兜底生效）；
- 创建 cron 时无条件用 chat-session 的模型配置覆盖 model_name，保证 cron 执行
  与创建它的会话使用同一模型配置。
"""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.harness.tools.cron import CronToolContext

from jiuwenswarm.agents.harness.common.tools.cron.cron_runtime import (
    CronRuntimeBridge,
    _CronToolsCronBackend,
    _extract_legacy_params,
)


class _DummyCronBackend:
    """满足 CronToolBackend 协议的最小桩。"""

    async def list_jobs(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def create_job(self, params: dict[str, Any], *, context: CronToolContext | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def update_job(self, job_id: str, patch: dict[str, Any], *, context: CronToolContext | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_job(self, job_id: str) -> bool:
        raise NotImplementedError

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        raise NotImplementedError

    async def preview_job(self, job_id: str, count: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def run_now(self, job_id: str) -> str:
        raise NotImplementedError

    async def status(self) -> dict[str, Any]:
        raise NotImplementedError

    async def get_runs(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError


def _build_tools() -> list[Any]:
    bridge = CronRuntimeBridge()
    bridge.set_backend(_DummyCronBackend())
    return bridge.build_tools(
        context=CronToolContext(channel_id="web", session_id="web_sess_1"),
        agent_id="test-agent",
        language="cn",
    )


def _tool_by_name(tools: list[Any], name: str) -> Any:
    for tool in tools:
        card = getattr(tool, "card", None)
        if card is not None and str(getattr(card, "name", "") or "") == name:
            return tool
    raise AssertionError(f"tool {name!r} not found in {[getattr(getattr(t, 'card', None), 'name', None) for t in tools]}")


# ---------------------------------------------------------------------------
# 工具描述不注入 model_name 字段（模型由 chat-session 直接复用）
# ---------------------------------------------------------------------------


def test_cron_tool_descriptions_have_no_model_name_field() -> None:
    tools = _build_tools()
    for name in ("cron", "cron_create_job", "cron_update_job"):
        tool = _tool_by_name(tools, name)
        properties = (tool.card.input_params or {}).get("properties", {})
        assert "model_name" not in properties, f"tool {name} 不应注入 model_name 字段"
        patch_schema = properties.get("patch") or {}
        patch_props = patch_schema.get("properties") or {}
        assert "model_name" not in patch_props, f"tool {name}.patch 不应注入 model_name 字段"


# ---------------------------------------------------------------------------
# _extract_legacy_params 透传 model_name（新版格式；会话无模型时兜底）
# ---------------------------------------------------------------------------


def _new_format_params(model_name: str | None = "mimo-v2.5-free") -> dict[str, Any]:
    params: dict[str, Any] = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"},
        "payload": {"kind": "agentTurn", "message": "每天早上提醒"},
        "delivery": {"channel": "web"},
    }
    if model_name is not None:
        params["model_name"] = model_name
    return params


def test_extract_legacy_params_passthrough_model_name() -> None:
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    out = _extract_legacy_params(_new_format_params(), context=context, require_schedule=True)
    assert out.get("model_name") == "mimo-v2.5-free"


def test_extract_legacy_params_model_name_from_payload_block() -> None:
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    params = _new_format_params(model_name=None)
    params["payload"]["model_name"] = "deepseek-v4-flash-free"
    out = _extract_legacy_params(params, context=context, require_schedule=True)
    assert out.get("model_name") == "deepseek-v4-flash-free"


# ---------------------------------------------------------------------------
# _inherit_session_model：直接复用 chat-session 的模型配置
# ---------------------------------------------------------------------------


def test_inherit_session_model_reuses_session_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {"model": "mimo-v2.5-free"},
    )
    # validate_cron_model 返回 canonical 值（这里模拟其解析成功）
    monkeypatch.setattr(
        "jiuwenswarm.gateway.cron.models.validate_cron_model",
        lambda raw: str(raw).strip() or None,
    )
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *"},
        context=context,
    )
    assert payload.get("model_name") == "mimo-v2.5-free"


def test_inherit_session_model_overrides_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接复用：即使显式传入 model_name，也被 chat-session 的模型配置覆盖。"""
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {"model": "mimo-v2.5-free"},
    )
    monkeypatch.setattr(
        "jiuwenswarm.gateway.cron.models.validate_cron_model",
        lambda raw: str(raw).strip() or None,
    )
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *", "model_name": "deepseek-v4-flash-free"},
        context=context,
    )
    assert payload.get("model_name") == "mimo-v2.5-free"


def test_inherit_session_model_no_session_model_keeps_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话无模型记录时，显式传入的 model_name 保留（兜底生效）。"""
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {},
    )
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *", "model_name": "deepseek-v4-flash-free"},
        context=context,
    )
    assert payload.get("model_name") == "deepseek-v4-flash-free"


def test_inherit_session_model_no_session_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {},
    )
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *"},
        context=context,
    )
    assert "model_name" not in payload


def test_inherit_session_model_validation_failure_keeps_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {"model": "no-such-model"},
    )
    # validate_cron_model 对未知模型抛错 → 复用失败不应阻断创建
    def _reject(raw: Any) -> Any:
        raise ValueError(f"Unknown model {raw!r}")

    monkeypatch.setattr(
        "jiuwenswarm.gateway.cron.models.validate_cron_model",
        _reject,
    )
    context = CronToolContext(channel_id="web", session_id="web_sess_1")
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *"},
        context=context,
    )
    assert "model_name" not in payload


def test_inherit_session_model_without_context() -> None:
    payload = _CronToolsCronBackend._inherit_session_model(
        {"name": "x", "cron_expr": "0 9 * * *"},
        context=None,
    )
    assert "model_name" not in payload


# ---------------------------------------------------------------------------
# update_job：不继承会话模型，保持 cron 已落库的模型配置
# ---------------------------------------------------------------------------


class _FakeCronTools:
    """满足 _CronToolsCronBackend 所需路由/更新接口的最小桩。"""

    def __init__(self) -> None:
        self.update_payloads: list[tuple[str, dict[str, Any]]] = []
        self._token_seq = 0

    def push_cron_route(self, route: Any) -> int:
        self._token_seq += 1
        return self._token_seq

    def reset_cron_route(self, token: int) -> None:
        pass

    async def update_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.update_payloads.append((job_id, dict(payload)))
        return {"id": job_id, "name": str(payload.get("name") or "x")}


@pytest.mark.asyncio
async def test_update_job_does_not_inherit_session_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """对话内 update 不改 cron 的模型配置：即使会话有模型也不继承/覆盖。"""
    # 会话有模型配置（模拟创建该 cron 后用户换了模型再更新），update 不应继承它
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda sid, cache_bust=False: {"model": "mimo-v2.5-free"},
    )
    fake_tools = _FakeCronTools()
    backend = _CronToolsCronBackend(fake_tools)
    context = CronToolContext(channel_id="web", session_id="web_sess_1")

    await backend.update_job(
        "job-1",
        {"name": "renamed", "cron_expr": "0 10 * * *"},
        context=context,
    )

    assert fake_tools.update_payloads, "update_job must reach CronTools.update_job"
    _job_id, payload = fake_tools.update_payloads[0]
    # patch 未显式传 model_name → payload 不应出现 model_name，cron 落库的模型保持不变
    assert "model_name" not in payload, "update 不应继承/覆盖 cron 的模型配置"
