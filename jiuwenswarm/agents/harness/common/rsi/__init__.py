"""RSI 服务域（agents/harness/common/rsi）—— Event Sink 主通道落地（内部接口 v3）。

六组件 + 派生薄服务 + 适配层 + 事件模型；对外统一组件拼装入口见
``RsiServiceContext``（组合根）。
"""

from __future__ import annotations

from jiuwenswarm.agents.harness.common.rsi.adapter import RsiEngineAdapter, RsiEventSink
from jiuwenswarm.agents.harness.common.rsi.artifact_adapter import ArtifactEngineAdapter
from jiuwenswarm.agents.harness.common.rsi.context import RsiServiceContext, build_rsi_service_context
from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiArtifactNotFound,
    RsiBadRequest,
    RsiDatasetInvalid,
    RsiError,
    RsiPathInvalid,
    RsiResumeMismatch,
    RsiScenarioNotSupported,
    RsiTaskNotFound,
    RsiTaskStateConflict,
)
from jiuwenswarm.agents.harness.common.rsi.events import EngineEvent
from jiuwenswarm.agents.harness.common.rsi.models import RsiTask, RsiTaskView, TaskStatus

__all__ = [
    "EngineEvent",
    "ArtifactEngineAdapter",
    "RsiArtifactNotFound",
    "RsiBadRequest",
    "RsiDatasetInvalid",
    "RsiEngineAdapter",
    "RsiError",
    "RsiEventSink",
    "RsiPathInvalid",
    "RsiResumeMismatch",
    "RsiScenarioNotSupported",
    "RsiServiceContext",
    "RsiTask",
    "RsiTaskNotFound",
    "RsiTaskStateConflict",
    "RsiTaskView",
    "TaskStatus",
    "build_rsi_service_context",
]
