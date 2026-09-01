"""RSI 服务域错误体系（错误码对齐 web 契约 §3.5 全集）。"""

from __future__ import annotations

from typing import Any


class RsiError(Exception):
    """RSI 服务域统一异常：携带对外错误码（web §3.5）。"""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

class RsiBadRequest(RsiError):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__("BAD_REQUEST", message, detail=detail)


class RsiScenarioNotSupported(RsiError):
    def __init__(self, message: str = "scenario 或 artifact_type 非法") -> None:
        super().__init__("SCENARIO_NOT_SUPPORTED", message)


class RsiDatasetInvalid(RsiError):
    def __init__(self, message: str, *, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__("DATASET_INVALID", message, detail=errors)
        self.errors = errors or []


class RsiPathInvalid(RsiError):
    def __init__(self, message: str = "本地路径不存在或后缀不符") -> None:
        super().__init__("PATH_INVALID", message)


class RsiTaskNotFound(RsiError):
    def __init__(self, task_id: str) -> None:
        super().__init__("TASK_NOT_FOUND", f"任务不存在: {task_id}")


class RsiTaskStateConflict(RsiError):
    def __init__(self, message: str) -> None:
        super().__init__("TASK_STATE_CONFLICT", message)


class RsiResumeMismatch(RsiError):
    def __init__(self, message: str = "resume fingerprint 校验失败") -> None:
        super().__init__("RESUME_MISMATCH", message)


class RsiArtifactNotFound(RsiError):
    def __init__(self, message: str = "artifact 不存在") -> None:
        super().__init__("ARTIFACT_NOT_FOUND", message)


class RsiNotReady(RsiError):
    """外部依赖未就绪的预留位（⚠️外部 / 中优先级接口骨架）。

    当前不可达或需外部(agent-core 事件化 / 引擎装配)后接线的路径，
    显式抛出而非静默假实现；前端收到 INTERNAL_ERROR 视作“暂不可用”。
    """

    def __init__(self, message: str) -> None:
        super().__init__("INTERNAL_ERROR", message)