"""Jiuwen Web RPC for short-window realtime audio-video Q&A."""

from __future__ import annotations

import base64
import asyncio
from array import array
from datetime import datetime, timezone
import io
import json
import os
import re
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable
import uuid

import httpx

from jiuwenswarm.common.video_tool_profile import VIDEO_TOOL_CHANNEL_ID
from jiuwenswarm.server.runtime.attachments.media_attachments import (
    normalize_chat_media_attachments,
)


_MAX_AUDIO_CHARS = 2_000_000
_MAX_TTS_TEXT_CHARS = 800
_MAX_JOYAI_FRAME_CHARS = 4_000_000
_MAX_JOYAI_INSTRUCTION_CHARS = 2_000
_JOYAI_TTS_VOICE = "vivian"
_JOYAI_TTS_INSTRUCTIONS = (
    "Always use the same Vivian voice. Speak at a slightly faster pace, "
    "around 1.2x normal speed, while keeping pronunciation clear and natural."
)
_JOYAI_TTS_TEMPERATURE = 0.2
_JOYAI_ACTION_TEMPERATURE = 0.0
_JOYAI_SYSTEM_PROMPT_KEY = "DEFAULT_SYSTEM_PROMPT_EN"
_JOYAI_TOOL_SYSTEM_PROMPT_KEY = "DEFAULT_SYSTEM_PROMPT_NO_DELEGATION"
_JOYAI_USER_KNOWLEDGE_GUARD = (
    "【本轮动作约束】你必须自行选择官方动作。只依据当前或近期清晰画面、用户明确提供的信息和已确认的工具结果回答。"
    "天气、新闻、价格、公司或品牌背景等外部或时效事实需要搜索核实，不得凭记忆猜测。"
    "当且仅当搜索对象已经明确且需要外部核实时，必须在本次推理中一次性输出完整的 Delegate 动作："
    "</response> 简短说明 </delegation> 包含明确对象和查询事项的可独立执行搜索请求。"
    "Delegate 是不可拆分的原子动作；只说‘需要搜索’、‘我来查询’或其他搜索承诺却没有在同一输出中给出 </delegation>，均为无效动作。"
    "不得先 Speak、再等待下一帧补发 Delegate，也不得用 </delegation> 询问‘这是什么’、‘哪个品牌’或‘请提供对象’。"
    "若画面和会话历史都无法确认搜索所需的关键对象，只选择 Speak，说明缺少的信息并请用户调整画面或补充，不要 Delegate。"
    "利用当前画面和会话历史解析‘这个品牌’、‘这个人’、‘这里’等指代。若先前因对象不明而追问，用户或后续清晰画面一旦补齐对象，"
    "立即结合先前搜索意图输出一个完整 Delegate 动作，不要只承诺搜索。纯视觉问答无需搜索。"
)
_ALLOWED_AUDIO_MIME_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg"}
_ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_IMAGE_FILENAME_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_LOG_WRITE_LOCK = threading.Lock()


class _JoyAIRateLimitError(RuntimeError):
    """JoyAI rejected a request because its rolling token quota was exhausted."""


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


def _is_allowed_audio_data_url(value: str) -> bool:
    header, separator, _ = value.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return False
    parts = header[5:].lower().split(";")
    return parts[0] in _ALLOWED_AUDIO_MIME_TYPES and "base64" in parts[1:]


def _is_allowed_image_data_url(value: str) -> bool:
    header, separator, _ = value.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return False
    parts = header[5:].lower().split(";")
    return parts[0] in _ALLOWED_IMAGE_MIME_TYPES and "base64" in parts[1:]


def _frame_media_item(frame_data_url: str) -> dict[str, str] | None:
    """Convert one validated video frame into the browser media-item schema."""
    if not _is_allowed_image_data_url(frame_data_url):
        return None
    header, _, encoded = frame_data_url.partition(",")
    mime_type = header[5:].split(";", 1)[0].lower()
    suffix = _IMAGE_FILENAME_SUFFIXES.get(mime_type)
    if not suffix or not encoded:
        return None
    return {
        "type": "image",
        "filename": f"video-search-frame{suffix}",
        "mimeType": mime_type,
        "base64Data": encoded,
    }


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
    except Exception:
        pass
    api_base = str(configured.get("api_base") or os.environ.get("VIDEO_API_BASE") or "").strip()
    api_key = str(configured.get("api_key") or os.environ.get("VIDEO_API_KEY") or "").strip()
    model = str(configured.get("model_name") or os.environ.get("VIDEO_MODEL_NAME") or "").strip()
    return api_base.rstrip("/"), api_key, model


def _joyai_model_config() -> tuple[str, str, str]:
    """Read the opt-in JoyAI endpoint without falling back to VIDEO_* settings."""
    return (
        os.environ.get("JOYAI_API_BASE", "").strip().rstrip("/"),
        os.environ.get("JOYAI_API_KEY", "").strip(),
        os.environ.get("JOYAI_MODEL_NAME", "").strip(),
    )


def _joyai_voice_config() -> tuple[str, str, str]:
    """Return the native JoyAI channel ASR/TTS WebSocket settings."""
    return (
        os.environ.get("JOYAI_ASR_WS_URL", "ws://127.0.0.1:8994/ws/asr").strip(),
        os.environ.get("JOYAI_TTS_WS_URL", "ws://127.0.0.1:8992/ws/tts").strip(),
        _JOYAI_TTS_VOICE,
    )


def _video_live_mode() -> str:
    mode = os.environ.get("VIDEO_LIVE_MODE", "realtime").strip().casefold()
    return "joyai" if mode == "joyai" else "realtime"


def _uses_joyai_voice_channel() -> bool:
    """Return whether JoyAI mode should use its native ASR/TTS WebSockets."""
    if _video_live_mode() != "joyai":
        return False
    provider = os.environ.get("JOYAI_VOICE_PROVIDER", "native").strip().casefold()
    return provider not in {"openai", "openai_compatible", "siliconflow"}


def _realtime_public_url() -> str:
    explicit = os.environ.get("VIDEO_REALTIME_PUBLIC_URL", "").strip()
    if explicit:
        return explicit
    api_base, _, _ = _video_model_config()
    if api_base.startswith("https://"):
        api_base = "wss://" + api_base[8:]
    elif api_base.startswith("http://"):
        api_base = "ws://" + api_base[7:]
    return api_base.rstrip("/") + "/realtime" if api_base else ""


def _realtime_ref_audio() -> str:
    path = os.environ.get("VIDEO_REALTIME_REF_AUDIO_PATH", "").strip()
    if not path:
        return ""
    with open(path, "rb") as stream:
        return base64.b64encode(stream.read()).decode("ascii")


def _tts_model_config() -> tuple[str, str, str, str]:
    """Read the dedicated TTS endpoint without guessing from other models."""
    return (
        os.environ.get("TTS_API_BASE", "").strip().rstrip("/"),
        *(os.environ.get(f"TTS_{key}", "").strip() for key in ("API_KEY", "MODEL_NAME", "VOICE")),
    )


def _model_config(prefix: str) -> tuple[str, str, str]:
    return (
        os.environ.get(f"{prefix}API_BASE", "").strip().rstrip("/"),
        *(os.environ.get(f"{prefix}{key}", "").strip() for key in ("API_KEY", "MODEL_NAME")),
    )


def _clean_model_text(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


_ASR_FILLER_ONLY = re.compile(
    r"^(?:(?:嗯|恩|呃|啊|哦|噢|唔|哼|额|诶|欸|哎|呀|唉|hmm|uhhuh|uh|um|ah|oh|ああ|ええ))+$",
    flags=re.IGNORECASE,
)


def _is_ignorable_asr_filler(transcript: str) -> bool:
    """Return true only when ASR produced punctuation and filler sounds."""
    normalized = re.sub(r"[\W_]+", "", str(transcript or "").lower())
    return bool(normalized and _ASR_FILLER_ONLY.fullmatch(normalized))


_JOYAI_RESPONSE_MARKER = re.compile(r"</?response>", flags=re.IGNORECASE)
_JOYAI_SILENCE_MARKER = re.compile(r"</?silence>", flags=re.IGNORECASE)
_JOYAI_DELEGATION_MARKER = re.compile(r"</?delegation>", flags=re.IGNORECASE)


def _parse_joyai_action(raw_content: str) -> dict[str, str]:
    """Normalize JoyAI's native silence/response/delegation action protocol."""
    raw = str(raw_content or "").strip()
    delegation_match = _JOYAI_DELEGATION_MARKER.search(raw)
    if delegation_match:
        response = _JOYAI_RESPONSE_MARKER.sub("", raw[:delegation_match.start()], count=1).strip()
        delegation = raw[delegation_match.end():].strip()
        return {
            "decision": "delegation",
            "response": response,
            "delegation": delegation,
        }
    if _JOYAI_SILENCE_MARKER.search(raw):
        return {"decision": "silence", "response": "", "delegation": ""}
    if _JOYAI_RESPONSE_MARKER.search(raw):
        response = _JOYAI_RESPONSE_MARKER.sub("", raw, count=1).strip()
        return {"decision": "response", "response": response, "delegation": ""}
    if not raw:
        return {"decision": "silence", "response": "", "delegation": ""}
    return {"decision": "response", "response": raw, "delegation": ""}


def _ground_joyai_user_instruction(instruction: str) -> str:
    """Add per-turn grounding rules without changing monitor or tool turns."""
    instruction = str(instruction or "").strip()
    if not instruction:
        return ""
    # Put the action protocol last: JoyAI follows end-of-turn constraints more
    # reliably than an equivalent prefix before the user's question.
    return f"【用户原话】{instruction}\n\n{_JOYAI_USER_KNOWLEDGE_GUARD}"


async def _request_joyai_completion(
    frame_data_url: str,
    prompt: str,
    joyai_session_id: str,
    *,
    max_tokens: int,
    frame_time_range: str = "",
    system_prompt_key: str = "",
) -> dict[str, Any]:
    api_base, api_key, model = _joyai_model_config()
    if not api_base or not model:
        raise RuntimeError(
            "请配置 JOYAI_API_BASE 和 JOYAI_MODEL_NAME；现有 VIDEO_* 配置不会被自动复用"
        )

    content: list[dict[str, Any]] = []
    if prompt.strip():
        content.append({
            "type": "text",
            "text": prompt,
        })
    content.append({"type": "image_url", "image_url": {"url": frame_data_url}})

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": content,
        }],
        "max_tokens": max_tokens,
        "temperature": _JOYAI_ACTION_TEMPERATURE,
        "stream": False,
    }
    if frame_time_range:
        payload["extra_body"] = {"frame_time_range": frame_time_range}
    headers = {
        "x-streaming-session": joyai_session_id,
        "x-system-prompt-key": system_prompt_key.strip() or os.environ.get(
            "JOYAI_SYSTEM_PROMPT_KEY", _JOYAI_SYSTEM_PROMPT_KEY
        ).strip() or _JOYAI_SYSTEM_PROMPT_KEY,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started_at = time.perf_counter()
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{api_base}/chat/completions",
            headers=headers,
            json=payload,
        )
    if response.status_code >= 400:
        detail = response.text.strip()[:1_000]
        if response.status_code == 429:
            raise _JoyAIRateLimitError(f"JoyAI 请求失败 (429): {detail}")
        raise RuntimeError(f"JoyAI 请求失败 ({response.status_code}): {detail}")
    try:
        data = response.json()
        raw_content = str(data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("JoyAI 返回了无效的 Chat Completions 响应") from exc

    streaming = data.get("streamingharness")
    if not isinstance(streaming, dict):
        streaming = {}
    return {
        "raw_content": raw_content,
        "model": str(data.get("model") or model),
        "joyai_session_id": joyai_session_id,
        "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
        "timing": streaming.get("timing") if isinstance(streaming.get("timing"), dict) else {},
        "memory": streaming.get("memory") if isinstance(streaming.get("memory"), dict) else {},
    }


async def _request_joyai_frame(
    frame_data_url: str,
    instruction: str,
    joyai_session_id: str,
    frame_time_range: str = "",
    *,
    system_prompt_key: str = "",
) -> dict[str, Any]:
    instruction = instruction.strip()
    completion = await _request_joyai_completion(
        frame_data_url,
        instruction,
        joyai_session_id,
        max_tokens=512 if instruction else 128,
        frame_time_range=frame_time_range,
        system_prompt_key=system_prompt_key,
    )
    return {
        **_parse_joyai_action(completion["raw_content"]),
        **completion,
    }


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


def _asr_uses_transcription_endpoint(model: str) -> bool:
    mode = os.environ.get("ASR_API_MODE", "").strip().casefold()
    if mode in {"transcription", "transcriptions", "audio"}:
        return True
    if mode in {"chat", "chat_completion", "chat_completions"}:
        return False
    normalized = model.casefold()
    return any(name in normalized for name in ("sensevoice", "whisper", "telespeechasr"))


def _decode_audio_data_url(data_url: str, index: int) -> tuple[str, bytes, str]:
    header, separator, encoded = data_url.partition(",")
    if not separator or not _is_allowed_audio_data_url(data_url):
        raise ValueError("audio data URL is invalid")
    mime_type = header[5:].partition(";")[0].casefold()
    suffix = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
        "audio/wav": ".wav",
    }.get(mime_type, ".audio")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("audio base64 payload is invalid") from exc
    if not audio:
        raise ValueError("audio payload is empty")
    return f"microphone-{index}{suffix}", audio, mime_type


def _pcm16_from_wav(audio: bytes, target_rate: int = 16_000) -> bytes:
    """Extract mono PCM16 and resample it for the JoyAI ASR channel."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            source_rate = source.getframerate()
            pcm = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as exc:
        raise ValueError("JoyAI ASR requires a valid WAV recording") from exc
    if channels != 1 or sample_width != 2:
        raise ValueError("JoyAI ASR requires mono PCM16 WAV audio")
    if source_rate <= 0:
        raise ValueError("JoyAI ASR WAV sample rate is invalid")
    if source_rate == target_rate:
        return pcm

    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return b""
    output_length = max(1, round(len(samples) * target_rate / source_rate))
    output = array("h", [0]) * output_length
    scale = source_rate / target_rate
    last_index = len(samples) - 1
    for output_index in range(output_length):
        position = output_index * scale
        left = min(int(position), last_index)
        right = min(left + 1, last_index)
        fraction = position - left
        output[output_index] = round(
            samples[left] + (samples[right] - samples[left]) * fraction
        )
    return output.tobytes()


def _wav_from_pcm16(pcm: bytes, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm)
    return output.getvalue()


def _joyai_asr_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    response = message.get("asr_response")
    if not isinstance(response, dict):
        response = message
    result = response.get("recognition_result")
    if not isinstance(result, dict):
        return ""
    hypotheses = result.get("hypothesis")
    if not isinstance(hypotheses, list) or not hypotheses:
        return ""
    first = hypotheses[0]
    return str(first.get("text") or "").strip() if isinstance(first, dict) else ""


def _is_joyai_asr_result(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    response = message.get("asr_response")
    if not isinstance(response, dict):
        response = message
    return isinstance(response.get("recognition_result"), dict)


async def _transcribe_joyai_channel(data_url: str) -> str:
    import websockets
    from websockets.exceptions import ConnectionClosedOK

    asr_url, _, _ = _joyai_voice_config()
    if not asr_url:
        raise RuntimeError("请配置 JOYAI_ASR_WS_URL")
    _, wav_audio, mime_type = _decode_audio_data_url(data_url, 1)
    if mime_type != "audio/wav":
        raise ValueError("JoyAI ASR requires audio/wav input")
    pcm = _pcm16_from_wav(wav_audio)
    if not pcm:
        return ""

    for attempt in range(2):
        request_id = uuid.uuid4().hex
        setup = {
            "request": {"sid": request_id, "reqid": request_id, "sample_rate": 16_000},
            "recognize": {"do_partial_result": False},
        }
        try:
            async with websockets.connect(
                asr_url,
                open_timeout=10,
                close_timeout=3,
                max_size=2_000_000,
            ) as socket:
                await socket.send(json.dumps(setup, ensure_ascii=False))
                await socket.send(struct.pack(">iii", -1, 0, 0) + pcm)
                deadline = time.monotonic() + 30
                last_text = ""
                while time.monotonic() < deadline:
                    message = await asyncio.wait_for(
                        socket.recv(), timeout=max(0.1, deadline - time.monotonic())
                    )
                    if isinstance(message, bytes):
                        continue
                    try:
                        event = json.loads(message)
                    except (TypeError, ValueError):
                        continue
                    error = event.get("error") if isinstance(event, dict) else None
                    if error:
                        raise RuntimeError(f"JoyAI ASR 服务错误: {error}")
                    code = event.get("code") if isinstance(event, dict) else None
                    if code not in (None, 0, "0"):
                        message = str(event.get("msg") or "unknown error")
                        raise RuntimeError(f"JoyAI ASR 服务错误 ({code}): {message}")
                    text = _joyai_asr_text(event)
                    if _is_joyai_asr_result(event):
                        return _clean_model_text(text)
                    if text:
                        last_text = text
                return _clean_model_text(last_text)
        except ConnectionClosedOK as exc:
            if attempt == 0:
                await asyncio.to_thread(_append_asr_log, {
                    "request_id": request_id,
                    "outcome": "retrying",
                    "transcript": "",
                    "has_transcript": False,
                    "retry_attempt": 1,
                    "error": str(exc),
                })
                continue
            raise RuntimeError("JoyAI ASR 连接在返回结果前连续关闭") from exc
        except TimeoutError as exc:
            raise RuntimeError("JoyAI ASR 等待结果超时") from exc

    return ""


async def _stream_joyai_channel_pcm(
    text: str,
    on_chunk: Callable[[bytes], Awaitable[None]],
) -> tuple[int, int]:
    import websockets

    _, tts_url, voice = _joyai_voice_config()
    if not tts_url:
        raise RuntimeError("请配置 JOYAI_TTS_WS_URL")
    request_id = uuid.uuid4().hex
    messages = (
        {
            "config": {
                "modalities": ["text", "audio"],
                "voice": voice,
                "instructions": _JOYAI_TTS_INSTRUCTIONS,
                "output_audio_format": "pcm16",
                "sample_rate": 24_000,
                "temperature": _JOYAI_TTS_TEMPERATURE,
                "max_tokens": 1024,
            }
        },
        {"type": "input_text.append", "text": text, "reqid": request_id},
        {"type": "input_text.commit", "reqid": request_id},
    )
    audio_bytes = 0
    chunk_count = 0
    try:
        async with websockets.connect(
            tts_url,
            open_timeout=10,
            close_timeout=3,
            max_size=4_000_000,
        ) as socket:
            for message in messages:
                await socket.send(json.dumps(message, ensure_ascii=False))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                message = await asyncio.wait_for(
                    socket.recv(), timeout=max(0.1, deadline - time.monotonic())
                )
                if isinstance(message, bytes):
                    if message:
                        await on_chunk(message)
                        audio_bytes += len(message)
                        chunk_count += 1
                    continue
                try:
                    event = json.loads(message)
                except (TypeError, ValueError):
                    continue
                error = event.get("error") if isinstance(event, dict) else None
                if error:
                    raise RuntimeError(f"JoyAI TTS 服务错误: {error}")
                if isinstance(event, dict) and event.get("type") == "response.done":
                    break
            else:
                raise RuntimeError("JoyAI TTS 等待结果超时")
    except TimeoutError as exc:
        raise RuntimeError("JoyAI TTS 等待结果超时") from exc
    if not audio_bytes:
        raise RuntimeError("JoyAI TTS 没有返回音频")
    return audio_bytes, chunk_count


async def _synthesize_joyai_channel(text: str) -> tuple[bytes, str, str]:
    chunks: list[bytes] = []

    async def collect_chunk(chunk: bytes) -> None:
        chunks.append(chunk)

    await _stream_joyai_channel_pcm(text, collect_chunk)
    pcm = b"".join(chunks)
    return _wav_from_pcm16(pcm, 24_000), "audio/wav", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


async def _transcribe_audio(audio_inputs: list[tuple[str, str]]) -> str:
    if _uses_joyai_voice_channel():
        if not audio_inputs:
            return ""
        transcripts = [
            await _transcribe_joyai_channel(data_url)
            for data_url, _ in audio_inputs
        ]
        return "。".join(text for text in transcripts if text)

    from openai import AsyncOpenAI

    api_base, api_key, model = _model_config("ASR_")
    if not audio_inputs or not api_base or not api_key or not model:
        return ""
    client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=45.0)
    try:
        if _asr_uses_transcription_endpoint(model):
            transcripts: list[str] = []
            for index, (data_url, _) in enumerate(audio_inputs, start=1):
                filename, audio, mime_type = _decode_audio_data_url(data_url, index)
                response = await client.audio.transcriptions.create(
                    model=model,
                    file=(filename, audio, mime_type),
                )
                text = response if isinstance(response, str) else getattr(response, "text", "")
                cleaned = _clean_model_text(str(text or ""))
                if cleaned:
                    transcripts.append(cleaned)
            return "。".join(transcripts)

        content = [
            {"type": "audio_url", "audio_url": {"url": data_url}}
            for data_url, _ in audio_inputs
        ]
        content.append({"type": "text", "text": "只转写清晰可辨的用户中文，不要解释；静音、噪声或听不清时返回空字符串，不得补写。"})
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=160,
            temperature=0,
        )
        return _clean_model_text(response.choices[0].message.content or "")
    finally:
        await client.close()


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


async def _synthesize_speech(text: str) -> tuple[bytes, str, str]:
    if _uses_joyai_voice_channel():
        return await _synthesize_joyai_channel(text)

    api_base, api_key, model, voice = _tts_model_config()
    if not api_base or not model:
        raise RuntimeError("请配置 TTS_API_BASE 和 TTS_MODEL_NAME")

    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "response_format": "mp3",
    }
    if voice:
        payload["voice"] = voice
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{api_base}/audio/speech",
            headers=headers,
            json=payload,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"TTS 请求失败 ({response.status_code})")
    if not response.content:
        raise RuntimeError("TTS 没有返回音频")
    return response.content, "audio/mpeg", model


def _core_agent_text(value: Any, *, limit: int = 280) -> str:
    if isinstance(value, str):
        text = value
    elif value is None:
        return ""
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _core_agent_progress(payload: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(payload.get("event_type") or "").strip()
    if event_type == "chat.reasoning":
        return {"stage": "reasoning", "title": "正在分析问题", "status": "running"}
    if event_type == "chat.tool_call":
        tool = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else payload
        name = str(
            tool.get("display_name") or tool.get("name") or payload.get("tool_name") or "工具"
        ).strip()
        detail = _core_agent_text(tool.get("formatted_args") or tool.get("arguments"))
        return {
            "stage": "tool_call",
            "title": f"调用工具：{name}",
            "detail": detail,
            "status": "running",
            "tool_call_id": str(tool.get("id") or tool.get("tool_call_id") or ""),
            "tool_name": name,
        }
    if event_type == "chat.tool_update":
        update = payload.get("tool_update") if isinstance(payload.get("tool_update"), dict) else payload
        name = str(update.get("tool_name") or update.get("name") or "工具").strip()
        detail = _core_agent_text(update.get("beam_search") or update.get("progress"))
        return {
            "stage": "tool_update",
            "title": f"{name} 正在执行",
            "detail": detail,
            "status": "running",
            "tool_call_id": str(update.get("tool_call_id") or ""),
            "tool_name": name,
        }
    if event_type == "chat.tool_result":
        result = payload.get("tool_result") if isinstance(payload.get("tool_result"), dict) else payload
        name = str(result.get("tool_name") or result.get("name") or "工具").strip()
        raw_status = str(result.get("status") or "").strip().lower()
        failed = result.get("success") is False or raw_status in {
            "error", "failed", "failure", "timeout", "timed_out",
        }
        detail = _core_agent_text(
            result.get("summary") or result.get("error") or result.get("result")
        )
        return {
            "stage": "tool_result",
            "title": f"{name}{'执行失败' if failed else '执行完成'}",
            "detail": detail,
            "status": "failed" if failed else "completed",
            "tool_call_id": str(result.get("tool_call_id") or ""),
            "tool_name": name,
        }
    if event_type == "todo.updated":
        todos = payload.get("todos")
        if not isinstance(todos, list) or not todos:
            return None
        completed = sum(
            1 for item in todos
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "completed"
        )
        return {
            "stage": "plan",
            "title": "执行计划已更新",
            "detail": f"{completed}/{len(todos)} 项已完成",
            "status": "running",
        }
    if event_type == "chat.delta" and str(payload.get("content") or "").strip():
        return {"stage": "answer", "title": "正在整理搜索结果", "status": "running"}
    if event_type == "chat.error":
        return {
            "stage": "error",
            "title": "Core Agent 执行失败",
            "detail": _core_agent_text(payload.get("error") or payload.get("content")),
            "status": "failed",
        }
    return None


async def _execute_core_agent(
    agent_client: Any,
    *,
    question: str,
    query: str,
    visual_context: str,
    search_session_id: str,
    frame_data_url: str = "",
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run one video research job through the standard, full Core Agent API."""
    from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
    from jiuwenswarm.common.schema.message import ReqMethod

    client = agent_client.get("value") if isinstance(agent_client, dict) else agent_client
    if client is None:
        raise RuntimeError("AgentServer client is unavailable")
    request_id = f"video-core-{uuid.uuid4().hex}"
    core_session_id = f"video-tool-{uuid.uuid4().hex}"
    prompt = (
        f"用户问题：{question or query}\n"
        f"建议搜索线索：{query or question}\n"
        f"Realtime视觉模型提供的画面线索：{visual_context or '无'}\n\n"
        "请使用可用工具完成任务。请求附带当前视频帧时，如果问题涉及画面中的实体、文字或指代，"
        "先使用图片理解工具核对画面，再生成准确搜索词；外部事实必须以搜索和网页正文为依据。"
    )
    params: dict[str, Any] = {
        "query": prompt,
        "content": prompt,
        "mode": "agent",
        "work_mode": "work",
        "source": "video_tool",
        "log_as_user": False,
        "video_question": question,
        "video_query": query,
        "video_visual_context": visual_context,
        "search_session_id": search_session_id,
    }
    media_item = _frame_media_item(frame_data_url)
    if media_item is not None:
        params["media_items"] = [media_item]
        normalize_chat_media_attachments(params, core_session_id)
    env = e2a_from_agent_fields(
        request_id=request_id,
        channel_id=VIDEO_TOOL_CHANNEL_ID,
        session_id=core_session_id,
        req_method=ReqMethod.CHAT_SEND,
        params=params,
        is_stream=False,
        timestamp=time.time(),
    )
    send_stream = getattr(client, "send_request_stream", None)
    if not callable(send_stream):
        response = await client.send_request(env)
        payload = response.payload if isinstance(response.payload, dict) else {}
        if not response.ok:
            raise RuntimeError(str(payload.get("error") or "Jiuwen Core Agent failed"))
        answer = str(payload.get("content") or payload.get("answer") or "").strip()
        if not answer:
            raise RuntimeError("Jiuwen Core Agent returned empty output")
        return {**payload, "answer": answer}

    final_payload: dict[str, Any] = {}
    delta_parts: list[str] = []
    tools_used: list[str] = []
    emitted_once: set[str] = set()
    async for chunk in send_stream(env):
        payload = chunk.payload if isinstance(chunk.payload, dict) else {}
        event_type = str(payload.get("event_type") or "").strip()
        if event_type == "chat.error":
            raise RuntimeError(str(payload.get("error") or payload.get("content") or "Jiuwen Core Agent failed"))
        content = str(payload.get("content") or "")
        if event_type == "chat.delta" and content:
            delta_parts.append(content)
        elif event_type == "chat.final" and content.strip():
            final_payload = payload
        progress = _core_agent_progress(payload)
        if progress is not None:
            stage = str(progress.get("stage") or "")
            tool_key = str(progress.get("tool_call_id") or "")
            dedupe_key = f"{stage}:{tool_key}" if tool_key else stage
            if stage in {"reasoning", "answer", "plan"} and dedupe_key in emitted_once:
                continue
            emitted_once.add(dedupe_key)
            tool_name = str(progress.get("tool_name") or "").strip()
            if tool_name and tool_name not in tools_used:
                tools_used.append(tool_name)
            if on_progress is not None:
                await on_progress(progress)

    answer = str(final_payload.get("content") or "").strip() or "".join(delta_parts).strip()
    if not answer:
        raise RuntimeError("Jiuwen Core Agent returned empty output")
    return {**final_payload, "answer": answer, "tools_used": tools_used}


def register_video_live_handler(channel: Any, *, agent_client: Any = None) -> None:
    search_tasks: set[asyncio.Task] = set()
    search_jobs: dict[str, dict[str, Any]] = {}
    search_semaphore = asyncio.Semaphore(2)
    tts_stream_tasks: dict[tuple[int, str], asyncio.Task] = {}

    async def _send_search_event(ws, event: str, payload: dict[str, Any]) -> None:
        try:
            await channel.send_event(ws, event, payload)
        except Exception:
            # A disconnected browser must not leave an unhandled task error.
            pass

    async def _run_search_job(
        ws,
        *,
        job_id: str,
        search_session_id: str,
        question: str,
        query: str,
        visual_context: str,
        frame_data_url: str,
    ) -> None:
        started_at = time.perf_counter()
        progress_history: list[dict[str, Any]] = []
        base_payload = {
            "job_id": job_id,
            "search_session_id": search_session_id,
            "question": question,
            "query": query,
            "engine": "Jiuwen Core Agent",
            "has_frame": bool(frame_data_url),
        }
        async def emit_progress(progress: dict[str, Any]) -> None:
            entry = {
                **progress,
                "sequence": len(progress_history) + 1,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
            }
            progress_history.append(entry)
            current = search_jobs.get(job_id, {})
            search_jobs[job_id] = {
                **current,
                **base_payload,
                "status": "running",
                "progress_history": list(progress_history),
            }
            await _send_search_event(ws, "video.search.progress", {
                **base_payload,
                "status": "running",
                "progress": entry,
            })

        start_progress = {
            "stage": "started",
            "title": "Core Agent 已开始处理",
            "status": "running",
            "sequence": 1,
            "elapsed_ms": 0,
        }
        progress_history.append(start_progress)
        search_jobs[job_id] = {
            **base_payload,
            "status": "running",
            "progress_history": list(progress_history),
        }
        await _send_search_event(ws, "video.search.started", {
            **base_payload,
            "status": "running",
            "progress_history": list(progress_history),
        })
        await asyncio.to_thread(_append_video_task_log, {
            "stage": "search_started",
            **base_payload,
        })
        try:
            async with search_semaphore:
                core_result = await _execute_core_agent(
                    agent_client,
                    question=question,
                    query=query,
                    visual_context=visual_context,
                    search_session_id=search_session_id,
                    frame_data_url=frame_data_url,
                    on_progress=emit_progress,
                )
                answer = core_result["answer"]
                await asyncio.to_thread(_append_video_task_log, {
                    "stage": "core_agent_completed",
                    **base_payload,
                    "tools_used": core_result.get("tools_used", []),
                    "model": core_result.get("model", ""),
                    "answer_chars": len(answer),
                })
            if not answer:
                raise RuntimeError("Jiuwen Core Agent returned empty output")
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            progress_history.append({
                "stage": "completed",
                "title": "Core Agent 已完成搜索",
                "status": "completed",
                "sequence": len(progress_history) + 1,
                "elapsed_ms": latency_ms,
            })
            completed_payload = {
                **base_payload,
                "status": "completed",
                "result": answer,
                "latency_ms": latency_ms,
                "progress_history": list(progress_history),
            }
            search_jobs[job_id] = completed_payload
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "search_completed",
                **completed_payload,
            })
            await _send_search_event(ws, "video.search.completed", completed_payload)
        except Exception as exc:  # noqa: BLE001
            error = str(exc).strip() or "Jiuwen Core Agent failed"
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            progress_history.append({
                "stage": "failed",
                "title": "Core Agent 执行失败",
                "detail": _core_agent_text(error),
                "status": "failed",
                "sequence": len(progress_history) + 1,
                "elapsed_ms": latency_ms,
            })
            failed_payload = {
                **base_payload,
                "status": "failed",
                "error": error,
                "latency_ms": latency_ms,
                "progress_history": list(progress_history),
            }
            search_jobs[job_id] = failed_payload
            await asyncio.to_thread(_append_video_task_log, {
                "stage": "search_failed",
                **failed_payload,
            })
            await _send_search_event(ws, "video.search.failed", failed_payload)

    def _start_search_job(
        ws,
        *,
        question: str,
        query: str,
        search_session_id: str,
        visual_context: str = "",
        frame_data_url: str = "",
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        search_job = {
            "id": job_id,
            "status": "running",
            "question": question,
            "query": query,
            "search_session_id": search_session_id,
        }
        # Keep a bounded recovery cache so a missed WebSocket event can be polled.
        if len(search_jobs) >= 128:
            search_jobs.pop(next(iter(search_jobs)))
        search_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "question": question,
            "query": query,
            "search_session_id": search_session_id,
        }
        task = asyncio.create_task(_run_search_job(
            ws,
            job_id=job_id,
            search_session_id=search_session_id,
            question=question,
            query=query,
            visual_context=visual_context,
            frame_data_url=frame_data_url,
        ))
        search_tasks.add(task)
        task.add_done_callback(search_tasks.discard)
        return search_job

    def _find_running_search_job(
        *,
        query: str,
        search_session_id: str,
    ) -> dict[str, Any] | None:
        normalized_query = re.sub(r"\s+", " ", query).strip().casefold()
        if not normalized_query:
            return None
        for job in reversed(list(search_jobs.values())):
            if (
                job.get("status") == "running"
                and job.get("search_session_id") == search_session_id
                and re.sub(r"\s+", " ", str(job.get("query") or "")).strip().casefold()
                == normalized_query
            ):
                return {
                    "id": str(job.get("job_id") or ""),
                    "status": "running",
                    "question": str(job.get("question") or ""),
                    "query": str(job.get("query") or ""),
                    "search_session_id": search_session_id,
                    "reused": True,
                }
        return None

    async def _search_status(ws, req_id, params, session_id):
        del session_id
        job_id = str(params.get("job_id") or "").strip() if isinstance(params, dict) else ""
        search_session_id = (
            str(params.get("search_session_id") or "").strip()
            if isinstance(params, dict)
            else ""
        )
        job = search_jobs.get(job_id)
        if not job or (
            search_session_id
            and job.get("search_session_id") != search_session_id
        ):
            await channel.send_response(
                ws, req_id, ok=False, error="search job not found", code="NOT_FOUND"
            )
            return
        await channel.send_response(ws, req_id, ok=True, payload=job)

    async def _realtime_config(ws, req_id, params, session_id):
        del params, session_id
        if _video_live_mode() == "joyai":
            api_base, _, model = _joyai_model_config()
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
        frame_time_range = str(params.get("frame_time_range") or "").strip()
        request_kind = str(params.get("request_kind") or "monitor").strip().casefold()
        if not _is_allowed_image_data_url(frame_data_url):
            await channel.send_response(
                ws, req_id, ok=False,
                error="frame_data_url must be a base64 JPEG, PNG, or WebP data URL",
                code="BAD_REQUEST",
            )
            return
        if len(frame_data_url) > _MAX_JOYAI_FRAME_CHARS:
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
            len(instruction) > _MAX_JOYAI_INSTRUCTION_CHARS
            or (not instruction and request_kind != "monitor")
        ):
            await channel.send_response(
                ws, req_id, ok=False,
                error=(
                    "instruction may be empty only for frame-only monitor requests; "
                    f"otherwise it must contain 1-{_MAX_JOYAI_INSTRUCTION_CHARS} characters"
                ),
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
        }
        await asyncio.to_thread(_append_joyai_log, {**request_log, "stage": "requested"})
        try:
            model_instruction = (
                _ground_joyai_user_instruction(instruction)
                if request_kind == "user"
                else instruction
            )
            request_args = [frame_data_url, model_instruction, upstream_session_id]
            if frame_time_range:
                request_args.append(frame_time_range)
            if request_kind == "tool":
                result = await _request_joyai_frame(
                    *request_args,
                    system_prompt_key=_JOYAI_TOOL_SYSTEM_PROMPT_KEY,
                )
            else:
                result = await _request_joyai_frame(*request_args)
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or "JoyAI frame request failed"
            error_code = (
                "JOYAI_RATE_LIMIT"
                if isinstance(exc, _JoyAIRateLimitError)
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
            search_job = _find_running_search_job(
                query=delegation,
                search_session_id=search_session_id,
            )
            if search_job is None:
                search_job = _start_search_job(
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
        allowed = {
            key: value
            for key, value in params.items()
            if key in {
                "event", "client_time", "level", "peak_level", "threshold",
                "noise_floor", "speech_ms", "assistant_playing", "response_active", "reason",
                "previous_task", "current_task",
                "source", "frame_count", "model", "url", "code", "message",
                "client_build", "task", "job_id", "search_session_id", "question",
                "query", "result", "realtime_answer", "turn_id", "attempt",
                "decision", "response_chars",
            }
            and isinstance(value, (str, int, float, bool))
        }
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
        if not text or len(text) > _MAX_TTS_TEXT_CHARS:
            await asyncio.to_thread(_append_video_task_log, {
                **request_log,
                "stage": "tts_failed",
                "latency_ms": round((time.perf_counter() - started_at) * 1_000, 1),
                "error": f"text must contain 1-{_MAX_TTS_TEXT_CHARS} characters",
            })
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=f"text must contain 1-{_MAX_TTS_TEXT_CHARS} characters",
                code="BAD_REQUEST",
            )
            return
        try:
            audio, mime, model = await _synthesize_speech(text)
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
            audio_bytes, chunk_count = await _stream_joyai_channel_pcm(text, emit_chunk)
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
        if not text or len(text) > _MAX_TTS_TEXT_CHARS:
            await channel.send_response(
                ws, req_id, ok=False,
                error=f"text must contain 1-{_MAX_TTS_TEXT_CHARS} characters",
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
            or len(data_url) > _MAX_AUDIO_CHARS
            or not _is_allowed_audio_data_url(data_url)
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
            transcript = await _transcribe_audio([(data_url, "用户麦克风")])
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
        ignored_filler = _is_ignorable_asr_filler(raw_transcript)
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
            len(frame_data_url) > _MAX_JOYAI_FRAME_CHARS
            or not _is_allowed_image_data_url(frame_data_url)
        ):
            await channel.send_response(
                ws, req_id, ok=False, error="frame_data_url is invalid", code="BAD_REQUEST"
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
            search_job = _start_search_job(
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

    channel.register_method("video.realtime.config", _realtime_config)
    channel.register_method("video.joyai.frame", _joyai_frame)
    channel.register_method("video.realtime.telemetry", _realtime_telemetry)
    channel.register_method("video.transcribe", _video_transcribe)
    channel.register_method("video.agent", _video_agent)
    channel.register_method("video.search.status", _search_status)
    channel.register_method("tts.synthesize", _tts_synthesize)
    channel.register_method("tts.stream.start", _tts_stream_start)
    channel.register_method("tts.stream.cancel", _tts_stream_cancel)
    _append_video_task_log({
        "stage": "video_handlers_registered",
        "module_path": __file__,
        "search_status_enabled": True,
    })
