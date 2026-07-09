from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)

from jiuwenswarm.common.config import get_default_models
from jiuwenswarm.common.openrouter_attribution import inject_attribution_headers
from jiuwenswarm.common.reasoning_injector import (
    build_reasoning_model_request_kwargs,
)
from jiuwenswarm.common.utils import logger

from .alarm_tools import create_alarm, delete_alarm, modify_alarm, search_alarms
from .calendar_tools import create_calendar_event, search_calendar_event
from .contact_tools import search_contact
from .file_tools import search_file, upload_file
from .location_tool import get_user_location
from .message_tools import search_message, send_message
from .note_tools import create_note, modify_note, search_notes
from .phone_tools import call_phone
from .photo_tools import search_photo_gallery, upload_photo
from .save_tools import save_file_to_file_manager, save_media_to_gallery
from .xiaoyi_collection_tool import (
    add_collection,
    delete_collection,
    query_collection,
)


NO_DEVICE_TOOL = "NO_DEVICE_TOOL"
PLANNER_ATTEMPT_TIMEOUT_SECONDS = 30.0
PLANNER_TOTAL_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class DeviceToolRouteSpec:
    tool: Any
    intent_name: str

    @property
    def tool_name(self) -> str:
        return str(self.tool.card.name)

    def planner_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": str(self.tool.card.description or ""),
                "parameters": dict(self.tool.card.input_params or {}),
            },
        }


DEVICE_TOOL_ROUTE_SPECS: tuple[DeviceToolRouteSpec, ...] = (
    DeviceToolRouteSpec(get_user_location, "GetCurrentLocation"),
    DeviceToolRouteSpec(create_note, "CreateNote"),
    DeviceToolRouteSpec(search_notes, "SearchNote"),
    DeviceToolRouteSpec(modify_note, "ModifyNote"),
    DeviceToolRouteSpec(create_calendar_event, "CreateCalendarEvent"),
    DeviceToolRouteSpec(search_calendar_event, "SearchCalendarEvent"),
    DeviceToolRouteSpec(search_contact, "SearchContactLocal"),
    DeviceToolRouteSpec(search_photo_gallery, "SearchPhotoVideo"),
    DeviceToolRouteSpec(upload_photo, "ImageUploadForClaw"),
    DeviceToolRouteSpec(search_file, "SearchFile"),
    DeviceToolRouteSpec(upload_file, "FileUploadForClaw"),
    DeviceToolRouteSpec(call_phone, "StartCall"),
    DeviceToolRouteSpec(send_message, "SendShortMessage"),
    DeviceToolRouteSpec(search_message, "SearchMessage"),
    DeviceToolRouteSpec(create_alarm, "CreateAlarm"),
    DeviceToolRouteSpec(search_alarms, "SearchAlarm"),
    DeviceToolRouteSpec(modify_alarm, "ModifyAlarm"),
    DeviceToolRouteSpec(delete_alarm, "DeleteAlarm"),
    DeviceToolRouteSpec(query_collection, "QueryCollection"),
    DeviceToolRouteSpec(add_collection, "AddCollection"),
    DeviceToolRouteSpec(delete_collection, "DeleteCollection"),
    DeviceToolRouteSpec(save_media_to_gallery, "SaveMediaToGallery"),
    DeviceToolRouteSpec(save_file_to_file_manager, "SaveFileToFileManager"),
)


class DeviceToolRouteRegistry:
    def __init__(self, specs: tuple[DeviceToolRouteSpec, ...]) -> None:
        self.specs = specs
        self.by_name = {spec.tool_name: spec for spec in specs}
        if len(self.by_name) != len(specs):
            raise RuntimeError(
                "Duplicate Xiaoyi device tool names in route registry"
            )

    def planner_schemas(self) -> list[dict[str, Any]]:
        return [spec.planner_schema() for spec in self.specs]

    def map_tool_names(
        self,
        tool_names: list[str] | tuple[str, ...],
        *,
        privilege_intents: set[str] | frozenset[str],
    ) -> CronDeviceToolPlan:
        normalized_tools: list[str] = []
        allowed_intents: list[str] = []
        checked_intents: list[str] = []
        for raw_name in tool_names:
            tool_name = str(raw_name or "").strip()
            if not tool_name or tool_name in normalized_tools:
                continue
            spec = self.by_name.get(tool_name)
            if spec is None:
                raise ValueError(f"Unknown Xiaoyi device tool: {tool_name}")
            normalized_tools.append(tool_name)
            if spec.intent_name not in allowed_intents:
                allowed_intents.append(spec.intent_name)
            if (
                spec.intent_name in privilege_intents
                and spec.intent_name not in checked_intents
            ):
                checked_intents.append(spec.intent_name)
        return CronDeviceToolPlan(
            tool_names=tuple(normalized_tools),
            allowed_intents=tuple(allowed_intents),
            privilege_intents=tuple(checked_intents),
        )


@dataclass(frozen=True)
class CronDeviceToolPlan:
    tool_names: tuple[str, ...]
    allowed_intents: tuple[str, ...]
    privilege_intents: tuple[str, ...]

    @property
    def is_device_task(self) -> bool:
        return bool(self.tool_names)


DEVICE_TOOL_ROUTE_REGISTRY = DeviceToolRouteRegistry(
    DEVICE_TOOL_ROUTE_SPECS
)
DEVICE_TOOL_ROUTE_BY_NAME = DEVICE_TOOL_ROUTE_REGISTRY.by_name


def map_device_tool_names(
    tool_names: list[str] | tuple[str, ...],
    *,
    privilege_intents: set[str] | frozenset[str],
) -> CronDeviceToolPlan:
    return DEVICE_TOOL_ROUTE_REGISTRY.map_tool_names(
        tool_names,
        privilege_intents=privilege_intents,
    )


def _build_default_model() -> Model:
    entries = get_default_models()
    if not entries:
        raise RuntimeError("No default model is configured for Cron device planning")
    entry = next(
        (item for item in entries if item.get("is_default") is True),
        entries[0],
    )
    model_client_config = dict(entry.get("model_client_config") or {})
    model_config_obj = dict(entry.get("model_config_obj") or {})
    model_name = str(
        model_config_obj.get("model")
        or model_client_config.get("model_name")
        or ""
    ).strip()
    if not model_name:
        raise RuntimeError("Default model name is missing for Cron device planning")

    inject_attribution_headers(model_client_config)
    request_config = ModelRequestConfig(
        **build_reasoning_model_request_kwargs(
            model_client_config=model_client_config,
            model_config_obj=model_config_obj,
            model_name=model_name,
        )
    )
    return Model(
        model_client_config=ModelClientConfig(**model_client_config),
        model_config=request_config,
    )


def _response_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    return "".join(chunks).strip()


class CronDeviceToolPlanner:
    def __init__(
        self,
        *,
        model_factory: Callable[[], Any] = _build_default_model,
    ) -> None:
        self._model_factory = model_factory
        self._model: Any | None = None
        self._tool_schemas = DEVICE_TOOL_ROUTE_REGISTRY.planner_schemas()

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = self._model_factory()
        return self._model

    async def plan(
        self,
        *,
        name: str,
        description: str,
        privilege_intents: set[str] | frozenset[str],
        request_id: str = "",
        job_key: str = "",
    ) -> CronDeviceToolPlan:
        logger.info(
            "[CRON_DEVICE] phase=DEVICE_TOOL_PLAN_BEGIN request_id=%s "
            "job_key=%s",
            request_id,
            job_key,
        )
        started_at = time.monotonic()
        last_error: BaseException | None = None
        for attempt in range(2):
            remaining = PLANNER_TOTAL_TIMEOUT_SECONDS - (
                time.monotonic() - started_at
            )
            if remaining <= 0:
                last_error = asyncio.TimeoutError()
                break
            try:
                response = await asyncio.wait_for(
                    self._get_model().invoke(
                        [
                            SystemMessage(
                                content=(
                                    "You plan Xiaoyi device tools for a scheduled task. "
                                    "Return every device tool that may be needed as tool "
                                    "calls in one response, but do not execute any tool. "
                                    f"If no listed device tool is needed, reply exactly "
                                    f"{NO_DEVICE_TOOL}. Do not provide other text."
                                )
                            ),
                            UserMessage(
                                content=(
                                    f"Task name: {name}\n"
                                    f"Task to execute at trigger time: {description}"
                                )
                            ),
                        ],
                        tools=self._tool_schemas,
                        temperature=0.0,
                        timeout=min(
                            PLANNER_ATTEMPT_TIMEOUT_SECONDS,
                            remaining,
                        ),
                    ),
                    timeout=min(
                        PLANNER_ATTEMPT_TIMEOUT_SECONDS,
                        remaining,
                    ),
                )
                tool_calls = getattr(response, "tool_calls", None) or []
                if tool_calls:
                    tool_names = [
                        str(getattr(call, "name", "") or "").strip()
                        for call in tool_calls
                    ]
                    plan = map_device_tool_names(
                        tool_names,
                        privilege_intents=privilege_intents,
                    )
                    if not plan.tool_names:
                        raise ValueError("Planner returned empty device tool calls")
                    logger.info(
                        "[CRON_DEVICE] phase=DEVICE_TOOL_PLAN_DONE "
                        "request_id=%s job_key=%s tool_names=%s",
                        request_id,
                        job_key,
                        list(plan.tool_names),
                    )
                    return plan
                if _response_text(response) == NO_DEVICE_TOOL:
                    logger.info(
                        "[CRON_DEVICE] phase=DEVICE_TOOL_PLAN_DONE "
                        "request_id=%s job_key=%s tool_names=[]",
                        request_id,
                        job_key,
                    )
                    return CronDeviceToolPlan((), (), ())
                raise ValueError("Planner returned neither tool calls nor marker")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "[CRON_DEVICE] phase=DEVICE_TOOL_PLAN_RETRY "
                        "request_id=%s job_key=%s error_type=%s",
                        request_id,
                        job_key,
                        type(exc).__name__,
                    )

        error_type = type(last_error).__name__ if last_error else "UnknownError"
        logger.error(
            "[CRON_DEVICE] phase=DEVICE_TOOL_PLAN_FAILED request_id=%s "
            "job_key=%s error_type=%s",
            request_id,
            job_key,
            error_type,
        )
        raise RuntimeError(
            f"Cron device tool planning failed: {error_type}"
        ) from last_error
