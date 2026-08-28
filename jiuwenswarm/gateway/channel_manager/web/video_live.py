"""Jiuwen Web RPC for short-window realtime audio-video Q&A."""

from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
import uuid

from jiuwenswarm.gateway.channel_manager.web import (
    joyai_provider,
    video_search,
    video_voice,
)
from jiuwenswarm.gateway.channel_manager.web.qwen_omni_gateway import (
    QWEN_OMNI_PROXY_PATH,
    QwenOmniRealtimeConfig,
    qwen_omni_realtime_enabled,
)
from jiuwenswarm.gateway.channel_manager.web.qwen_omni_tools import (
    parse_qwen_omni_tool_call,
    qwen_omni_tools,
)
_ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_LOG_WRITE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


_AGENT_ROUTER_SYSTEM_PROMPT = (
    "你是视频直播会话的意图路由器。输入包含用户原话、Realtime模型已经对用户说出的自然语言回答和已有任务。"
    "你只判断是否需要执行后台控制动作，必须返回JSON对象，不要重复回答用户。"
    "action只能是none、set_current_task、stop_current_task、jiuwen_research。"
    "用户原话用于判断用户要求执行什么动作；Realtime回答代表视觉模型结合当前画面得到的事实，二者必须联合理解。"
    "Realtime回答不能凭空创造用户没有要求的任务或搜索。即使Realtime已经用白话确认操作，仍要输出对应后台动作。"
    "判断set_current_task的核心标准不是任务领域，而是完成请求是否需要继续接收后续画面、跨多个时刻观察，"
    "或维护随时间变化的状态。只要符合这一标准就返回set_current_task。"
    "这类任务包括但不限于持续识别或转换内容、跟踪对象或状态、比较变化、记录过程、累计统计、周期汇总、"
    "检查规则、等待条件、在适当时机输出；不得仅因任务不是翻译、提醒或计数而返回none。"
    "用户没有明确说‘持续’时，如果请求自然地作用于不断变化的直播画面，例如‘帮我翻译画面里的文字’、"
    "‘看着这个过程’、‘记录他在做什么’，也应理解为持续任务；但明确询问当前这一帧、当前物体或要求一次性回答时返回none。"
    "返回set_current_task时必须提供task：用简洁、完整、领域无关的执行规则保留用户目标，并写清观察对象、"
    "需要执行的处理、触发或输出时机，以及确有必要跨调用保留的状态。不得虚构用户未要求的条件、频率或结果。"
    "已有任务时，用户修改目标、规则、频率或输出方式，返回set_current_task，并在task中给出合并修改后的完整任务；"
    "不要只返回修改片段。"
    "已有任务时，用户明确要求停止、暂停或取消监控或当前任务，返回stop_current_task。"
    "凡用户问题需要当前画面、用户陈述和近期对话之外的外部事实或时效信息，返回jiuwen_research并提供query；"
    "包括但不限于天气、新闻、价格、公司或品牌背景、人物资料、地点信息。"
    "不要因为Realtime已经给出猜测性答案或只说‘我帮你查一下’而返回none。生成query时，"
    "必须优先采用Realtime回答中从画面识别出的具体品牌、人物、地点、物品或文字，"
    "用它补全‘这个’、‘这个牌子’、‘这家公司’、‘它’等指代，并纠正用户原话中明显的ASR同音误识别。"
    "不得在Realtime回答已经给出明确实体时仍搜索未解析的指代或错误ASR词。"
    "普通的一次性视觉问答、闲聊和不需要后续画面的指令返回none。"
    "持续任务只根据用户原话判断，画面回答不能把一次性问题变成持续任务。已有任务的迟到语音分片或简单复述返回none。"
)
_REALTIME_TELEMETRY_EVENTS = {
    "barge_in_candidate",
    "barge_in_confirmed",
    "barge_in_rejected",
    "current_task_applied",
    "realtime_start_clicked",
    "realtime_start_blocked_no_source",
    "realtime_first_frame_waiting",
    "realtime_first_frame_ready",
    "realtime_start_blocked_no_frames",
    "realtime_start_unsupported_browser",
    "realtime_config_requested",
    "realtime_config_received",
    "realtime_microphone_request_started",
    "realtime_microphone_ready",
    "realtime_websocket_connecting",
    "realtime_websocket_open",
    "realtime_session_ready",
    "realtime_websocket_error",
    "realtime_websocket_closed",
    "realtime_context_updated",
    "active_task_reminder_sent",
    "joyai_monitor_started",
    "realtime_answer_final",
    "search_result_received",
    "search_result_duplicate_ignored",
    "search_result_queued",
    "search_result_queue_failed",
    "search_result_dispatched",
    "search_result_answered",
    "search_result_response_interrupted",
    "search_result_response_empty",
    "joyai_tool_context_buffered",
    "joyai_tool_context_attached",
    "qwen_tool_call_received",
    "qwen_tool_call_invalid",
    "qwen_tool_result_returned",
    "qwen_text_input_dispatched",
    "qwen_native_tool_router_selected",
    "qwen_tool_call_forwarding",
    "video_agent_route_failed",
}
_REALTIME_TELEMETRY_FIELDS = {
    "event", "client_time", "level", "peak_level", "threshold",
    "noise_floor", "speech_ms", "assistant_playing", "response_active", "reason",
    "previous_task", "current_task",
    "source", "frame_count", "model", "provider", "dialect", "url", "code", "message",
    "client_build", "task", "job_id", "search_session_id", "question",
    "query", "result", "realtime_answer", "turn_id", "attempt",
    "decision", "response_chars", "context_job_count", "context_chars",
    "name", "call_id",
}


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    # Search completion and router responses can finish on different worker
    # threads. Serialize each append so one JSON object always occupies one line.
    with _LOG_WRITE_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)


def _append_realtime_telemetry(event: dict[str, Any]) -> None:
    path = Path.home() / ".jiuwenswarm" / "logs" / "realtime-interrupt.jsonl"
    _append_jsonl(path, event)


def _append_realtime_session_log(event: dict[str, Any]) -> None:
    path = Path.home() / ".jiuwenswarm" / "logs" / "realtime-session.jsonl"
    event = {"server_time": datetime.now(timezone.utc).isoformat(), **event}
    _append_jsonl(path, event)


def _append_video_task_log(event: dict[str, Any]) -> None:
    path = Path.home() / ".jiuwenswarm" / "logs" / "video-task-routing.jsonl"
    event = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    _append_jsonl(path, event)


def _append_asr_log(event: dict[str, Any]) -> None:
    path = Path.home() / ".jiuwenswarm" / "logs" / "asr-results.jsonl"
    record = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    _append_jsonl(path, record)


def _append_joyai_log(event: dict[str, Any]) -> None:
    path = Path.home() / ".jiuwenswarm" / "logs" / "joyai-video.jsonl"
    record = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    _append_jsonl(path, record)


def _is_allowed_image_data_url(value: str) -> bool:
    header, separator, _ = value.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return False
    parts = header[5:].lower().split(";")
    return parts[0] in _ALLOWED_IMAGE_MIME_TYPES and "base64" in parts[1:]


def _video_model_config() -> tuple[str, str, str]:
    """Prefer Jiuwen's models.video config, then dedicated video env vars."""
    configured: dict[str, Any] = {}
    try:
        from jiuwenswarm.common.config import get_config

        config = get_config()
        models = config.get("models") if isinstance(config, dict) else None
        video = models.get("video") if isinstance(models, dict) else None
        client = video.get("model_client_config") if isinstance(video, dict) else None
        if isinstance(client, dict):
            configured = client
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug(
            "Unable to load the configured video model; using VIDEO_* environment variables",
            exc_info=exc,
        )
    api_base = str(configured.get("api_base") or os.environ.get("VIDEO_API_BASE") or "").strip()
    api_key = str(configured.get("api_key") or os.environ.get("VIDEO_API_KEY") or "").strip()
    model = str(configured.get("model_name") or os.environ.get("VIDEO_MODEL_NAME") or "").strip()
    return api_base.rstrip("/"), api_key, model


def _video_live_mode() -> str:
    mode = os.environ.get("VIDEO_LIVE_MODE", "realtime").strip().casefold()
    return "joyai" if mode == "joyai" else "realtime"


def _realtime_provider() -> str:
    return "qwen_omni" if qwen_omni_realtime_enabled() else "minicpm"


def _uses_joyai_voice_channel() -> bool:
    """Return whether JoyAI mode should use its native ASR/TTS WebSockets."""
    return joyai_provider.uses_native_voice_channel(_video_live_mode())


def _realtime_public_url() -> str:
    explicit = os.environ.get("VIDEO_REALTIME_PUBLIC_URL", "").strip()
    if explicit:
        return explicit
    api_base, _, _ = _video_model_config()
    if api_base.startswith("https://"):
        api_base = "wss://" + api_base[8:]
    elif api_base.startswith("http://"):
        api_base = "ws://" + api_base[7:]
    return urljoin(f"{api_base.rstrip('/')}/", "realtime") if api_base else ""


def _realtime_ref_audio() -> str:
    path = os.environ.get("VIDEO_REALTIME_REF_AUDIO_PATH", "").strip()
    if not path:
        return ""
    with open(path, "rb") as stream:
        return base64.b64encode(stream.read()).decode("ascii")


def _model_config(prefix: str) -> tuple[str, str, str]:
    return (
        os.environ.get(f"{prefix}API_BASE", "").strip().rstrip("/"),
        *(os.environ.get(f"{prefix}{key}", "").strip() for key in ("API_KEY", "MODEL_NAME")),
    )


def _task_text(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:500]
    return str(value or "").strip()[:500]


def _requests_task_stop(question: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?]", "", question).lower()
    if not normalized or re.search(r"(?:不要|别|无需)(?:停止|暂停|取消)", normalized):
        return False
    if normalized in {"停止监控", "暂停监控", "取消监控", "停止当前任务", "暂停当前任务", "取消当前任务", "先停一下", "停一下"}:
        return True
    return bool(re.search(
        r"(?:停止|暂停|取消|结束|关闭|别再|不用再).{0,8}(?:监控|监测|观察|当前任务|这个任务|该任务)",
        normalized,
    ))


async def _agent_answer(
    question: str,
    realtime_answer: str,
    current_task: str = "",
    recent_chat: str = "",
    trace_context: dict[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    from openai import AsyncOpenAI

    if current_task and _requests_task_stop(question):
        return "好的，已暂停当前任务。", "", ["stop_current_task"]

    # JoyAI emits its own delegation decision; do not run a second intent router.
    if _video_live_mode() == "joyai":
        return realtime_answer, "", []

    api_base, api_key, model = _model_config("")
    if not api_base or not api_key or not model:
        return realtime_answer, "", []
    client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=45.0)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _AGENT_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"已有任务：{current_task or '无'}\n"
                    f"近期对话：{recent_chat or '无'}\n"
                    f"用户原话：{question}\n"
                    f"Realtime自然回答：{realtime_answer or '无'}"
                )},
            ],
            response_format={"type": "json_object"},
            max_tokens=240,
            temperature=0,
            extra_body={"enable_thinking": False},
        )
        raw_response = response.choices[0].message.content or "{}"
        if trace_context is not None:
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "router_model_response",
                **trace_context,
                "model": model,
                "raw_response": raw_response,
            })
        decision = json.loads(raw_response)
        action = str(decision.get("action") or "none")
        if action == "stop_current_task" and current_task:
            return "好的，已暂停当前任务。", "", [action]
        if action == "set_current_task":
            task = _task_text(decision.get("task") or decision.get("task_rule"))
            if task:
                return f"好的，已设为当前任务：{task}", task, [action]
        if action != "jiuwen_research":
            return realtime_answer, "", []
        query = str(decision.get("query") or question).strip()[:500]
        return query or question, "", [action]
    finally:
        await client.close()


def register_video_live_handler(channel: Any, *, agent_client: Any = None) -> None:
    search_manager = video_search.VideoSearchManager(
        channel,
        agent_client,
        # Resolve the logger at execution time so runtime/test overrides remain effective.
        log_event=lambda event: _append_video_task_log(event),
    )
    tts_stream_tasks: dict[tuple[int, str], asyncio.Task] = {}

    async def _realtime_config(ws, req_id, params, session_id):
        del params, session_id
        if _video_live_mode() == "joyai":
            api_base, _, model = joyai_provider.model_config()
            if not api_base or not model:
                await channel.send_response(
                    ws, req_id, ok=False,
                    error="请配置 JOYAI_API_BASE 和 JOYAI_MODEL_NAME",
                    code="VIDEO_CONFIG_ERROR",
                )
                return
            await channel.send_response(
                ws, req_id, ok=True,
                payload={"provider": "joyai", "model": model},
            )
            return
        if _realtime_provider() == "qwen_omni":
            config = QwenOmniRealtimeConfig.from_environment()
            try:
                config.validate()
            except ValueError as exc:
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=str(exc),
                    code="VIDEO_CONFIG_ERROR",
                )
                return
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={
                    "provider": "qwen_omni",
                    "dialect": "qwen_omni",
                    "url": QWEN_OMNI_PROXY_PATH,
                    "model": config.model,
                    "voice": config.voice,
                    "tools": qwen_omni_tools(),
                },
            )
            return
        url = _realtime_public_url()
        _, _, model = _video_model_config()
        if not url or not model:
            await channel.send_response(
                ws, req_id, ok=False,
                error="请配置视频模型 Realtime 地址和模型名",
                code="VIDEO_CONFIG_ERROR",
            )
            return
        try:
            ref_audio = _realtime_ref_audio()
        except OSError as exc:
            await channel.send_response(
                ws, req_id, ok=False,
                error=f"无法读取 Realtime 参考音频：{exc}",
                code="VIDEO_CONFIG_ERROR",
            )
            return
        await channel.send_response(
            ws, req_id, ok=True,
            payload={
                "provider": "realtime",
                "dialect": "minicpm",
                "url": url,
                "model": model,
                "ref_audio_base64": ref_audio,
            },
        )

    async def _joyai_frame(ws, req_id, params, session_id):
        params = params if isinstance(params, dict) else {}
        frame_data_url = str(params.get("frame_data_url") or "").strip()
        instruction = str(params.get("instruction") or "").strip()
        requested_session_id = str(params.get("joyai_session_id") or "").strip()
        search_session_id = str(params.get("search_session_id") or "").strip()
        question = str(params.get("question") or "").strip()
        tool_context = str(params.get("tool_context") or "").strip()
        frame_time_range = str(params.get("frame_time_range") or "").strip()
        request_kind = str(params.get("request_kind") or "monitor").strip().casefold()
        if not _is_allowed_image_data_url(frame_data_url):
            await channel.send_response(
                ws, req_id, ok=False,
                error="frame_data_url must be a base64 JPEG, PNG, or WebP data URL",
                code="BAD_REQUEST",
            )
            return
        if len(frame_data_url) > joyai_provider.MAX_FRAME_CHARS:
            await channel.send_response(
                ws, req_id, ok=False, error="frame_data_url is too large", code="BAD_REQUEST"
            )
            return
        if request_kind not in {"user", "monitor", "tool"}:
            await channel.send_response(
                ws, req_id, ok=False, error="request_kind is invalid", code="BAD_REQUEST"
            )
            return
        if (
            len(instruction) > joyai_provider.MAX_INSTRUCTION_CHARS
            or (not instruction and request_kind != "monitor")
        ):
            await channel.send_response(
                ws, req_id, ok=False,
                error=(
                    "instruction may be empty only for frame-only monitor requests; "
                    "otherwise it must contain 1-"
                    f"{joyai_provider.MAX_INSTRUCTION_CHARS} characters"
                ),
                code="BAD_REQUEST",
            )
            return
        if len(tool_context) > joyai_provider.MAX_TOOL_CONTEXT_CHARS:
            await channel.send_response(
                ws, req_id, ok=False,
                error=(
                    "tool_context must not exceed "
                    f"{joyai_provider.MAX_TOOL_CONTEXT_CHARS} characters"
                ),
                code="BAD_REQUEST",
            )
            return
        if tool_context and request_kind != "user":
            await channel.send_response(
                ws, req_id, ok=False,
                error="tool_context is allowed only for user requests",
                code="BAD_REQUEST",
            )
            return
        if len(requested_session_id) > 200:
            await channel.send_response(
                ws, req_id, ok=False, error="joyai_session_id is too long", code="BAD_REQUEST"
            )
            return
        if len(frame_time_range) > 100 or (
            frame_time_range
            and not re.fullmatch(
                r"\d+(?:\.\d+)? seconds ~ \d+(?:\.\d+)? seconds",
                frame_time_range,
            )
        ):
            await channel.send_response(
                ws, req_id, ok=False, error="frame_time_range is invalid", code="BAD_REQUEST"
            )
            return
        upstream_session_id = requested_session_id or str(session_id or f"joyai-{uuid.uuid4().hex}")
        search_session_id = search_session_id or upstream_session_id
        request_log = {
            "request_id": str(req_id),
            "joyai_session_id": upstream_session_id,
            "instruction": instruction,
            "request_kind": request_kind,
            "frame_only": not bool(instruction),
            "frame_chars": len(frame_data_url),
            "frame_time_range": frame_time_range,
            "tool_context_chars": len(tool_context),
        }
        await asyncio.to_thread(_append_joyai_log, {**request_log, "stage": "requested"})
        try:
            model_instruction = (
                joyai_provider.ground_user_instruction(instruction, tool_context)
                if request_kind == "user"
                else instruction
            )
            request_args = [frame_data_url, model_instruction, upstream_session_id]
            if frame_time_range:
                request_args.append(frame_time_range)
            if request_kind == "tool":
                result = await joyai_provider.request_frame(
                    *request_args,
                    system_prompt_key=joyai_provider.TOOL_SYSTEM_PROMPT_KEY,
                )
            else:
                result = await joyai_provider.request_frame(*request_args)
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or "JoyAI frame request failed"
            error_code = (
                "JOYAI_RATE_LIMIT"
                if isinstance(exc, joyai_provider.JoyAIRateLimitError)
                else "JOYAI_ERROR"
            )
            await asyncio.to_thread(_append_joyai_log, {
                **request_log,
                "stage": "failed",
                "error": error,
                "error_code": error_code,
            })
            await channel.send_response(
                ws, req_id, ok=False, error=error, code=error_code
            )
            return

        search_job = None
        tools_used: list[str] = []
        delegation = str(result.get("delegation") or "").strip()[:500]
        if (
            request_kind != "tool"
            and result.get("decision") == "delegation"
            and delegation
        ):
            search_question = question[:500] or delegation
            search_job = search_manager.find_running(
                query=delegation,
                search_session_id=search_session_id,
            )
            if search_job is None:
                search_job = search_manager.start(
                    ws,
                    question=search_question,
                    query=delegation,
                    search_session_id=search_session_id,
                    visual_context=str(result.get("response") or ""),
                    frame_data_url=frame_data_url,
                )
            tools_used.append("jiuwen_research")
        result["tools_used"] = tools_used
        result["search_job"] = search_job
        await asyncio.to_thread(_append_joyai_log, {
            **request_log,
            "stage": "completed",
            "decision": result["decision"],
            "response": result["response"],
            "delegation": result["delegation"],
            "raw_content": result["raw_content"],
            "latency_ms": result["latency_ms"],
            "timing": result["timing"],
            "tools_used": tools_used,
            "search_job": search_job,
        })
        await channel.send_response(ws, req_id, ok=True, payload=result)

    async def _realtime_telemetry(ws, req_id, params, session_id):
        del session_id
        event_name = str(params.get("event") or "").strip() if isinstance(params, dict) else ""
        if event_name not in _REALTIME_TELEMETRY_EVENTS:
            await channel.send_response(
                ws, req_id, ok=False, error="unsupported telemetry event", code="BAD_REQUEST"
            )
            return
        allowed: dict[str, str | int | float | bool] = {}
        for key, value in params.items():
            if key not in _REALTIME_TELEMETRY_FIELDS:
                continue
            if isinstance(value, (str, int, float, bool)):
                allowed[key] = value
        for key in ("question", "query", "task"):
            if isinstance(allowed.get(key), str):
                allowed[key] = allowed[key][:1_000]
        for key in ("result", "realtime_answer"):
            if isinstance(allowed.get(key), str):
                allowed[key] = allowed[key][:50_000]
        if event_name == "current_task_applied":
            allowed["stage"] = "current_task_applied"
            await asyncio.to_thread(_append_video_task_log, allowed)
        elif event_name in {
            "realtime_answer_final",
            "search_result_received",
            "search_result_duplicate_ignored",
            "search_result_queued",
            "search_result_queue_failed",
            "search_result_dispatched",
            "search_result_answered",
            "search_result_response_interrupted",
            "search_result_response_empty",
        }:
            allowed["stage"] = event_name
            await asyncio.to_thread(_append_video_task_log, allowed)
        elif event_name.startswith("realtime_") or event_name == "active_task_reminder_sent":
            await asyncio.to_thread(_append_realtime_session_log, allowed)
        else:
            allowed["server_time"] = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(_append_realtime_telemetry, allowed)
        await channel.send_response(ws, req_id, ok=True, payload={"logged": True})

    async def _tts_synthesize(ws, req_id, params, session_id):
        started_at = time.perf_counter()
        text = str(params.get("text") or "").strip() if isinstance(params, dict) else ""
        request_log = {
            "stage": "tts_requested",
            "request_id": str(req_id),
            "session_id": str(session_id or ""),
            "text_chars": len(text),
            "text_preview": text[:300],
        }
        await asyncio.to_thread(_append_video_task_log, request_log)
        if not text or len(text) > video_voice.MAX_TTS_TEXT_CHARS:
            await asyncio.to_thread(_append_video_task_log, {
                **request_log,
                "stage": "tts_failed",
                "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
                "error": f"text must contain 1-{video_voice.MAX_TTS_TEXT_CHARS} characters",
            })
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=f"text must contain 1-{video_voice.MAX_TTS_TEXT_CHARS} characters",
                code="BAD_REQUEST",
            )
            return
        try:
            audio, mime, model = await video_voice.synthesize_speech(
                text, use_joyai_voice=_uses_joyai_voice_channel()
            )
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(_append_video_task_log, {
                **request_log,
                "stage": "tts_failed",
                "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
                "error": str(exc).strip() or "TTS failed",
            })
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(exc).strip() or "TTS failed",
                code="TTS_ERROR",
            )
            return
        await asyncio.to_thread(_append_video_task_log, {
            **request_log,
            "stage": "tts_completed",
            "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
            "audio_bytes": len(audio),
            "audio_mime": mime,
            "model": model,
        })
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "success": True,
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "audio_mime": mime,
                "model": model,
            },
        )

    async def _run_tts_stream(
        ws,
        *,
        stream_id: str,
        text: str,
        request_id: str,
        session_id: str,
    ) -> None:
        started_at = time.perf_counter()
        first_chunk_ms: float | None = None
        sequence = 0
        stream_key = (id(ws), stream_id)

        async def emit_chunk(chunk: bytes) -> None:
            nonlocal first_chunk_ms, sequence
            sequence += 1
            elapsed_ms = round((time.perf_counter() - started_at) * 1_000, 1)
            if first_chunk_ms is None:
                first_chunk_ms = elapsed_ms
                await asyncio.to_thread(_append_video_task_log, {
                    "stage": "tts_stream_first_chunk",
                    "request_id": request_id,
                    "session_id": session_id,
                    "stream_id": stream_id,
                    "text_chars": len(text),
                    "first_chunk_ms": first_chunk_ms,
                    "chunk_bytes": len(chunk),
                })
            await channel.send_event(ws, "video.tts.chunk", {
                "stream_id": stream_id,
                "sequence": sequence,
                "sample_rate": 24_000,
                "audio_base64": base64.b64encode(chunk).decode("ascii"),
            })

        try:
            audio_bytes, chunk_count = await joyai_provider.stream_channel_pcm(
                text, emit_chunk
            )
            latency_ms = round((time.perf_counter() - started_at) * 1_000, 1)
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "tts_stream_completed",
                "request_id": request_id,
                "session_id": session_id,
                "stream_id": stream_id,
                "text_chars": len(text),
                "latency_ms": latency_ms,
                "first_chunk_ms": first_chunk_ms,
                "audio_bytes": audio_bytes,
                "chunk_count": chunk_count,
                "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            })
            await channel.send_event(ws, "video.tts.done", {
                "stream_id": stream_id,
                "audio_bytes": audio_bytes,
                "chunk_count": chunk_count,
                "latency_ms": latency_ms,
                "first_chunk_ms": first_chunk_ms,
            })
        except asyncio.CancelledError:
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "tts_stream_cancelled",
                "request_id": request_id,
                "session_id": session_id,
                "stream_id": stream_id,
                "text_chars": len(text),
                "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
                "first_chunk_ms": first_chunk_ms,
            })
            await channel.send_event(ws, "video.tts.cancelled", {"stream_id": stream_id})
        except Exception as exc:  # noqa: BLE001
            error = str(exc).strip() or "TTS stream failed"
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "tts_stream_failed",
                "request_id": request_id,
                "session_id": session_id,
                "stream_id": stream_id,
                "text_chars": len(text),
                "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
                "first_chunk_ms": first_chunk_ms,
                "error": error,
            })
            await channel.send_event(ws, "video.tts.error", {
                "stream_id": stream_id,
                "error": error,
            })
        finally:
            current = asyncio.current_task()
            if tts_stream_tasks.get(stream_key) is current:
                tts_stream_tasks.pop(stream_key, None)

    async def _tts_stream_start(ws, req_id, params, session_id):
        params = params if isinstance(params, dict) else {}
        text = str(params.get("text") or "").strip()
        stream_id = str(params.get("stream_id") or "").strip()
        if not text or len(text) > video_voice.MAX_TTS_TEXT_CHARS:
            await channel.send_response(
                ws, req_id, ok=False,
                error=f"text must contain 1-{video_voice.MAX_TTS_TEXT_CHARS} characters",
                code="BAD_REQUEST",
            )
            return
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", stream_id):
            await channel.send_response(
                ws, req_id, ok=False, error="stream_id is invalid", code="BAD_REQUEST"
            )
            return
        if not _uses_joyai_voice_channel():
            await channel.send_response(
                ws, req_id, ok=False,
                error="streaming TTS is unavailable for the configured voice provider",
                code="TTS_STREAM_UNAVAILABLE",
            )
            return
        stream_key = (id(ws), stream_id)
        existing = tts_stream_tasks.get(stream_key)
        if existing is not None and not existing.done():
            await channel.send_response(
                ws, req_id, ok=False, error="stream_id is already active", code="CONFLICT"
            )
            return
        request_log = {
            "stage": "tts_stream_requested",
            "request_id": str(req_id),
            "session_id": str(session_id or ""),
            "stream_id": stream_id,
            "text_chars": len(text),
            "text_preview": text[:300],
        }
        await asyncio.to_thread(_append_video_task_log, request_log)
        task = asyncio.create_task(_run_tts_stream(
            ws,
            stream_id=stream_id,
            text=text,
            request_id=str(req_id),
            session_id=str(session_id or ""),
        ))
        tts_stream_tasks[stream_key] = task
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"started": True, "stream_id": stream_id, "sample_rate": 24_000},
        )

    async def _tts_stream_cancel(ws, req_id, params, session_id):
        del session_id
        stream_id = (
            str(params.get("stream_id") or "").strip()
            if isinstance(params, dict)
            else ""
        )
        task = tts_stream_tasks.get((id(ws), stream_id))
        cancelled = bool(task is not None and not task.done())
        if cancelled and task is not None:
            task.cancel()
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"stream_id": stream_id, "cancelled": cancelled},
        )

    async def _video_transcribe(ws, req_id, params, session_id):
        started_at = time.perf_counter()
        data_url = params.get("audio_data_url") if isinstance(params, dict) else None
        request_log: dict[str, Any] = {
            "request_id": str(req_id),
            "session_id": str(session_id or ""),
            "audio_chars": len(data_url) if isinstance(data_url, str) else 0,
            "audio_mime": (
                data_url[5:].partition(";")[0]
                if isinstance(data_url, str) and data_url.startswith("data:")
                else ""
            ),
        }
        if (
            not isinstance(data_url, str)
            or len(data_url) > video_voice.MAX_AUDIO_CHARS
            or not video_voice.is_allowed_audio_data_url(data_url)
        ):
            await asyncio.to_thread(_append_asr_log, {
                **request_log,
                "outcome": "rejected",
                "transcript": "",
                "has_transcript": False,
                "latency_ms": round((time.perf_counter() - started_at) * 1000),
                "error": "audio_data_url is invalid",
            })
            await channel.send_response(
                ws, req_id, ok=False, error="audio_data_url is invalid", code="BAD_REQUEST"
            )
            return
        try:
            transcript = await video_voice.transcribe_audio(
                [(data_url, "用户麦克风")],
                use_joyai_voice=_uses_joyai_voice_channel(),
                log_event=_append_asr_log,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or "ASR failed"
            await asyncio.to_thread(_append_asr_log, {
                **request_log,
                "outcome": "failed",
                "transcript": "",
                "has_transcript": False,
                "latency_ms": round((time.perf_counter() - started_at) * 1000),
                "error": error,
            })
            await channel.send_response(
                ws, req_id, ok=False, error=error, code="ASR_ERROR"
            )
            return
        raw_transcript = transcript
        ignored_filler = video_voice.is_ignorable_asr_filler(raw_transcript)
        if ignored_filler:
            transcript = ""
        asr_event = {
            **request_log,
            "outcome": (
                "ignored_filler"
                if ignored_filler
                else ("completed" if transcript.strip() else "empty")
            ),
            "transcript": transcript,
            "has_transcript": bool(transcript.strip()),
            "latency_ms": round((time.perf_counter() - started_at) * 1000),
        }
        if ignored_filler:
            asr_event.update({
                "raw_transcript": raw_transcript,
                "ignored_reason": "filler_only",
            })
        await asyncio.to_thread(_append_asr_log, asr_event)
        await asyncio.to_thread(_append_video_task_log, {
            "stage": "asr_ignored_filler" if ignored_filler else "asr_completed",
            "request_id": str(req_id),
            "transcript": transcript,
            "has_transcript": bool(transcript.strip()),
            **(
                {"raw_transcript": raw_transcript, "ignored_reason": "filler_only"}
                if ignored_filler
                else {}
            ),
        })
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "transcript": transcript,
                **({"ignored_reason": "filler_only"} if ignored_filler else {}),
            },
        )

    async def _video_agent(ws, req_id, params, session_id):
        del session_id
        question = str(params.get("question") or "").strip() if isinstance(params, dict) else ""
        frame_data_url = (
            str(params.get("frame_data_url") or "").strip()
            if isinstance(params, dict)
            else ""
        )
        realtime_answer = (
            str(params.get("realtime_answer") or params.get("visual_answer") or "").strip()
            if isinstance(params, dict)
            else ""
        )
        current_task = str(params.get("current_task") or "").strip() if isinstance(params, dict) else ""
        recent_chat = str(params.get("recent_chat") or "").strip() if isinstance(params, dict) else ""
        search_session_id = str(params.get("search_session_id") or "").strip() if isinstance(params, dict) else ""
        if not question or len(question) > 500:
            await channel.send_response(
                ws, req_id, ok=False, error="question must contain 1-500 characters", code="BAD_REQUEST"
            )
            return
        if frame_data_url and (
            len(frame_data_url) > joyai_provider.MAX_FRAME_CHARS
            or not _is_allowed_image_data_url(frame_data_url)
        ):
            await channel.send_response(
                ws, req_id, ok=False, error="frame_data_url is invalid", code="BAD_REQUEST"
            )
            return
        if _video_live_mode() == "realtime" and _realtime_provider() == "qwen_omni":
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "agent_router_bypassed",
                "request_id": str(req_id),
                "provider": "qwen_omni",
                "question": question,
                "reason": "qwen_native_tool_routing",
            })
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={
                    "answer": realtime_answer,
                    "current_task": "",
                    "tools_used": [],
                    "search_job": None,
                },
            )
            return
        try:
            original_task = current_task
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "agent_requested",
                "request_id": str(req_id),
                "question": question,
                "realtime_answer": realtime_answer,
                "recent_chat": recent_chat,
                "current_task_before": original_task,
            })
            answer, current_task, tools_used = await _agent_answer(
                question,
                realtime_answer,
                current_task,
                recent_chat[:4_000],
                {"request_id": str(req_id)},
            )
        except Exception as exc:  # noqa: BLE001
            await channel.send_response(
                ws, req_id, ok=False, error=str(exc) or "agent failed", code="AGENT_ERROR"
            )
            return
        search_job = None
        if "jiuwen_research" in tools_used:
            query = answer or question
            search_job = search_manager.start(
                ws,
                question=question,
                query=query,
                search_session_id=search_session_id,
                visual_context=realtime_answer,
                frame_data_url=frame_data_url,
            )
            answer = ""

        await asyncio.to_thread(_append_video_task_log, {
            "stage": "agent_result",
            "request_id": str(req_id),
            "question": question,
            "realtime_answer": realtime_answer,
            "tools_used": tools_used,
            "current_task_before": original_task,
            "current_task_after": current_task,
            "answer": answer,
            "search_job": search_job,
        })
        await channel.send_response(
            ws, req_id, ok=True,
            payload={
                "answer": answer,
                "current_task": current_task,
                "tools_used": tools_used,
                "search_job": search_job,
            },
        )

    async def _qwen_tool(ws, req_id, params, session_id):
        del session_id
        raw_params = params if isinstance(params, dict) else {}
        question = str(raw_params.get("question") or "").strip()
        search_session_id = str(raw_params.get("search_session_id") or "").strip()
        frame_data_url = str(raw_params.get("frame_data_url") or "").strip()
        request_log = {
            "stage": "qwen_tool_requested",
            "request_id": str(req_id),
            "name": str(raw_params.get("name") or "").strip(),
            "call_id": str(raw_params.get("call_id") or "").strip(),
            "question": question,
            "search_session_id": search_session_id,
        }
        await asyncio.to_thread(_append_video_task_log, request_log)
        try:
            if _video_live_mode() != "realtime" or _realtime_provider() != "qwen_omni":
                raise ValueError("Qwen Omni Realtime is not the active video provider")
            tool_call = parse_qwen_omni_tool_call(raw_params)
            if not question:
                question = tool_call.query
            if len(question) > 500:
                raise ValueError("question must not exceed 500 characters")
            if not search_session_id or len(search_session_id) > 200:
                raise ValueError("search_session_id must contain 1-200 characters")
            if frame_data_url and (
                len(frame_data_url) > joyai_provider.MAX_FRAME_CHARS
                or not _is_allowed_image_data_url(frame_data_url)
            ):
                raise ValueError("frame_data_url is invalid")
        except ValueError as exc:
            error = str(exc)
            await asyncio.to_thread(_append_video_task_log, {
                **request_log,
                "stage": "qwen_tool_rejected",
                "error": error,
            })
            await channel.send_response(
                ws, req_id, ok=False, error=error, code="BAD_REQUEST"
            )
            return

        search_job = search_manager.start(
            ws,
            question=question,
            query=tool_call.query,
            search_session_id=search_session_id,
            frame_data_url=frame_data_url,
            tool_call_id=tool_call.call_id,
            tool_name=tool_call.name,
        )
        await asyncio.to_thread(_append_video_task_log, {
            **request_log,
            "stage": "qwen_tool_accepted",
            "query": tool_call.query,
            "job_id": search_job["id"],
        })
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "name": tool_call.name,
                "call_id": tool_call.call_id,
                "search_job": search_job,
            },
        )

    channel.register_method("video.realtime.config", _realtime_config)
    channel.register_method("video.joyai.frame", _joyai_frame)
    channel.register_method("video.realtime.telemetry", _realtime_telemetry)
    channel.register_method("video.transcribe", _video_transcribe)
    channel.register_method("video.agent", _video_agent)
    channel.register_method("video.qwen.tool", _qwen_tool)
    channel.register_method("video.search.status", search_manager.handle_status)
    channel.register_method("tts.synthesize", _tts_synthesize)
    channel.register_method("tts.stream.start", _tts_stream_start)
    channel.register_method("tts.stream.cancel", _tts_stream_cancel)
    _append_video_task_log({
        "stage": "video_handlers_registered",
        "module_path": __file__,
        "search_status_enabled": True,
    })
