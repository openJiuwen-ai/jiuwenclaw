"""企业版 cron store 门面：在非企业 store 与 Gateway DB store 之间按门控切换。

仅当 ``is_enterprise_edition()``（``AGENT_RUNTIME`` 非空）时由工厂包一层；
非企业路径直接返回 file/Redis，**不**加载本模块。

Manager ``register.ack`` 后 ``jiuwenclaw_id`` 才绑定；门面在每次读写时
重新判断 ``enterprise_cron_enabled()``，避免启动期选错后端。

非企业 file/Redis store **保持原协议**（无 filters / 无三元组参数）；
企业扩展参数仅转发到 ``GatewayDbCronJobStore``。
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenclaw.gateway.cron.enterprise_gate import enterprise_cron_enabled
from jiuwenclaw.gateway.cron.models import CronJob
from jiuwenclaw.gateway.cron.store_base import CronJobStoreBackend

logger = logging.getLogger(__name__)

_NON_ENTERPRISE_CREATE_KEYS = frozenset(
    {
        "job_id",
        "name",
        "cron_expr",
        "timezone",
        "description",
        "targets",
        "enabled",
        "wake_offset_seconds",
        "session_id",
        "chat_type",
        "mode",
        "delete_after_run",
    }
)


class EnterpriseAwareCronJobStore:
    """非企业 backend 为兜底；企业就绪后切换到 Gateway DB。"""

    def __init__(self, non_enterprise_store: CronJobStoreBackend) -> None:
        self._non_enterprise = non_enterprise_store
        self._db_store: Any | None = None

    def _db(self) -> Any:
        if self._db_store is None:
            from jiuwenclaw.gateway.cron.db_store import GatewayDbCronJobStore

            self._db_store = GatewayDbCronJobStore()
            logger.info("[Cron] switched to Gateway DB cron_job store (enterprise ready)")
        return self._db_store

    @staticmethod
    def _use_enterprise() -> bool:
        return enterprise_cron_enabled()

    async def list_jobs(self, *, filters: dict[str, Any] | None = None) -> list[CronJob]:
        if self._use_enterprise():
            return await self._db().list_jobs(filters=filters)
        # 非企业 store 无 filters 参数；可选过滤在 Controller 层处理（默认全量）
        return await self._non_enterprise.list_jobs()

    async def get_job(self, job_id: str) -> CronJob | None:
        if self._use_enterprise():
            return await self._db().get_job(job_id)
        return await self._non_enterprise.get_job(job_id)

    async def create_job(self, **kwargs: Any) -> CronJob:
        if self._use_enterprise():
            return await self._db().create_job(**kwargs)
        non_enterprise_kwargs = {
            k: v for k, v in kwargs.items() if k in _NON_ENTERPRISE_CREATE_KEYS
        }
        return await self._non_enterprise.create_job(**non_enterprise_kwargs)

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        if self._use_enterprise():
            return await self._db().update_job(job_id, patch)
        return await self._non_enterprise.update_job(job_id, patch)

    async def delete_job(self, job_id: str) -> bool:
        if self._use_enterprise():
            return await self._db().delete_job(job_id)
        return await self._non_enterprise.delete_job(job_id)

    async def get_revision(self) -> int:
        if self._use_enterprise():
            return await self._db().get_revision()
        return await self._non_enterprise.get_revision()
