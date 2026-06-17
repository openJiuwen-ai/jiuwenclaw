# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager REST API 单元测试夹具：内存 SQLite + mock Gateway push。"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from sqlalchemy.exc import SAWarning

from jiuwenclaw_manager.infrastructure.db import get_db_handler
from jiuwenclaw_manager.models.table_init import init_all_tables
from jiuwenclaw_manager.routers.register import router_register

from demo_payloads import instance_create_body

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")


class _GatewayAckSimulator:
    """模拟 Gateway config.ack，为策略/映射 create 分配自增 id。"""

    def __init__(self) -> None:
        self._service_policy_id = 0
        self._agent_policy_id = 0
        self._global_policy_id = 0
        self._mapping_id = 0

    async def push_config_op(
        self,
        jiuwenclaw_id: str,
        config: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        _ = jiuwenclaw_id
        if "config_effective_service_policies" in config:
            payload = config["config_effective_service_policies"]
            if payload.get("op") == "create":
                self._service_policy_id += 1
                return {
                    "result": {"id": self._service_policy_id},
                    "revision": "rev-ut",
                    "success_flag": True,
                }
        if "config_effective_agent_policies" in config:
            payload = config["config_effective_agent_policies"]
            if payload.get("op") == "create":
                self._agent_policy_id += 1
                return {
                    "result": {"id": self._agent_policy_id},
                    "revision": "rev-ut",
                    "success_flag": True,
                }
        if "config_effective_global_policies" in config:
            payload = config["config_effective_global_policies"]
            if payload.get("op") == "create":
                self._global_policy_id += 1
                return {
                    "result": {"id": self._global_policy_id},
                    "revision": "rev-ut",
                    "success_flag": True,
                }
        if "config_default_template_mappings" in config:
            payload = config["config_default_template_mappings"]
            if payload.get("op") == "create":
                self._mapping_id += 1
                return {
                    "result": {"id": self._mapping_id},
                    "revision": "rev-ut",
                    "success_flag": True,
                }
        return {"revision": "rev-ut", "success_flag": True}

    async def push_config_op_to_all(
        self,
        config: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        _ = config
        return {"revision": "rev-ut", "success_flag": True}


async def _open_sqlite(path: Path) -> SQLiteHandler:
    handler = SQLiteHandler(str(path))
    await handler.init_database()
    await handler.connect()
    return handler


def _require_http_ok(resp: Response) -> None:
    if resp.status_code != 200:
        raise RuntimeError(
            f"expected HTTP 200, got {resp.status_code}: {resp.text}"
        )


@dataclass
class ManagerApiHarness:
    """Manager FastAPI + SQLite 测试环境。"""

    http: AsyncClient
    handler: DBHandler
    jiuwenclaw_id: str = field(default="")
    gateway_sim: _GatewayAckSimulator = field(default_factory=_GatewayAckSimulator)

    @staticmethod
    def templates_url(path: str) -> str:
        return f"/api/v1{path}"

    @staticmethod
    def instances_url(suffix: str = "") -> str:
        return f"/api/v1/instances{suffix}"

    def scoped_url(self, path: str) -> str:
        if not self.jiuwenclaw_id:
            raise ValueError("jiuwenclaw_id required for instance-scoped API")
        return f"/api/v1/instances/{self.jiuwenclaw_id}{path}"

    async def create_instance(self, *, name: str = "ut-demo-instance") -> str:
        resp = await self.http.post(
            self.instances_url(),
            json=instance_create_body(jiuwenclaw_name=name),
        )
        _require_http_ok(resp)
        data = resp.json()["data"]
        self.jiuwenclaw_id = data["jiuwenclaw_id"]
        return self.jiuwenclaw_id

    async def post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.post(url, json=body)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def get_json(self, path: str, **params: Any) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.get(url, params=params or None)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def patch_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.patch(url, json=body)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def delete_ok(self, path: str) -> None:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.delete(url)
        _require_http_ok(resp)


_PUSH_CONFIG_OP_MODULES = (
    "jiuwenclaw_manager.manager_ws_server.server",
    "jiuwenclaw_manager.core.template.model_template",
    "jiuwenclaw_manager.core.template.extension_config_template",
    "jiuwenclaw_manager.core.template.skill_whitelist_template",
    "jiuwenclaw_manager.core.template.service_config_template",
    "jiuwenclaw_manager.core.application_config.channel_config",
    "jiuwenclaw_manager.core.application_config.log_masking_rule",
    "jiuwenclaw_manager.core.config_effective_policy.config_effective_service_policy",
    "jiuwenclaw_manager.core.config_effective_policy.config_effective_global_policy",
    "jiuwenclaw_manager.core.config_effective_policy.config_effective_agent_policy",
    "jiuwenclaw_manager.core.config_effective_policy.config_default_template_mapping",
)

_PUSH_CONFIG_OP_TO_ALL_MODULES = (
    "jiuwenclaw_manager.manager_ws_server.server",
)


def _install_push_mocks(
    monkeypatch: pytest.MonkeyPatch,
    sim: _GatewayAckSimulator,
) -> None:
    for mod in _PUSH_CONFIG_OP_MODULES:
        monkeypatch.setattr(f"{mod}.push_config_op", sim.push_config_op, raising=False)
    for mod in _PUSH_CONFIG_OP_TO_ALL_MODULES:
        monkeypatch.setattr(
            f"{mod}.push_config_op_to_all",
            sim.push_config_op_to_all,
            raising=False,
        )


@pytest_asyncio.fixture
async def manager_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """可写 SQLite + mock push 的 Manager API 客户端。"""
    sim = _GatewayAckSimulator()
    _install_push_mocks(monkeypatch, sim)

    handler = await _open_sqlite(tmp_path / "manager_ut.db")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        await init_all_tables(handler)

    app = FastAPI()
    router_register(app)

    def _override_get_db_handler() -> DBHandler:
        return handler

    app.dependency_overrides[get_db_handler] = _override_get_db_handler

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        harness = ManagerApiHarness(http=client, handler=handler, gateway_sim=sim)
        yield harness

    app.dependency_overrides.clear()
    await handler.disconnect()
