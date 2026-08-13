"""Jiuwen Web RPC for short-window realtime audio-video Q&A."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


_MAX_AUDIO_CHARS = 2_000_000
_MAX_TTS_TEXT_CHARS = 800
_ALLOWED_AUDIO_MIME_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg"}
_ToolProgress = Callable[[str, str], Awaitable[None]]


def _is_allowed_audio_data_url(value: str) -> bool:
    header, separator, _ = value.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return False
    parts = header[5:].lower().split(";")
    return parts[0] in _ALLOWED_AUDIO_MIME_TYPES and "base64" in parts[1:]


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


def _task_text(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:500]
    return str(value or "").strip()[:500]


async def _transcribe_audio(audio_inputs: list[tuple[str, str]]) -> str:
    from openai import AsyncOpenAI

    api_base, api_key, model = _model_config("ASR_")
    if not audio_inputs or not api_base or not api_key or not model:
        return ""
    content = [
        {"type": "audio_url", "audio_url": {"url": data_url}}
        for data_url, _ in audio_inputs
    ]
    content.append({"type": "text", "text": "只转写清晰可辨的用户中文，不要解释；静音、噪声或听不清时返回空字符串，不得补写。"})
    client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=45.0)
    try:
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
    visual_answer: str,
    current_task: str = "",
    tool_progress: _ToolProgress | None = None,
) -> tuple[str, str, list[str]]:
    from openai import AsyncOpenAI

    api_base, api_key, model = _model_config("")
    if not api_base or not api_key or not model:
        return visual_answer, "", []
    client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=45.0)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": (
                "只判断用户原话是否需要九问工具，返回JSON。action只能是none、set_current_task、mcp_free_search。"
                "明确要求未来或持续观察画面并在条件满足时提醒或计数，返回set_current_task，并将task编译为完整规则；"
                "询问公司、品牌、组织介绍或最新外部信息，返回mcp_free_search及query；可用画面回答补全‘这个/这家公司’的搜索对象。"
                "普通视觉问答返回none。画面回答不参与持续任务判断。"
                "已有任务的迟到分片或复述返回none。"
            )}, {"role": "user", "content": f"已有任务：{current_task or '无'}\n用户原话：{question}\n画面回答：{visual_answer}"}],
            response_format={"type": "json_object"},
            max_tokens=240,
            temperature=0,
            extra_body={"enable_thinking": False},
        )
        decision = json.loads(response.choices[0].message.content or "{}")
        action = str(decision.get("action") or "none")
        if action == "set_current_task":
            task = _task_text(decision.get("task") or decision.get("task_rule"))
            if task:
                return f"好的，已设为当前任务：{task}", task, [action]
        if action != "mcp_free_search":
            return visual_answer, "", []
        from jiuwenswarm.agents.harness.common.tools.search_tools import mcp_free_search

        if tool_progress:
            await tool_progress("started", "DuckDuckGo 免费搜索")
        result = await mcp_free_search.invoke({"query": str(decision.get("query") or question), "max_results": 5, "timeout_seconds": 20})
        answer = str(result).strip()
        if answer.startswith("[ERROR]"):
            if tool_progress:
                await tool_progress("failed", "DuckDuckGo 免费搜索")
            raise RuntimeError(answer.removeprefix("[ERROR]:").strip() or "free search failed")
        first_line = answer.partition("\n")[0]
        engine = "免费搜索"
        prefix = "Free search results ("
        if first_line.startswith(prefix) and ") for:" in first_line:
            engine = first_line[len(prefix):].split(") for:", 1)[0]
        if tool_progress:
            await tool_progress("completed", engine)
        return answer, "", [action]
    finally:
        await client.close()


async def _synthesize_speech(text: str) -> tuple[bytes, str, str]:
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


def register_video_live_handler(channel: Any) -> None:
    async def _realtime_config(ws, req_id, params, session_id):
        del params, session_id
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
            payload={"url": url, "model": model, "ref_audio_base64": ref_audio},
        )

    async def _tts_synthesize(ws, req_id, params, session_id):
        del session_id
        text = str(params.get("text") or "").strip() if isinstance(params, dict) else ""
        if not text or len(text) > _MAX_TTS_TEXT_CHARS:
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
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(exc).strip() or "TTS failed",
                code="TTS_ERROR",
            )
            return
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

    async def _video_transcribe(ws, req_id, params, session_id):
        del session_id
        data_url = params.get("audio_data_url") if isinstance(params, dict) else None
        if (
            not isinstance(data_url, str)
            or len(data_url) > _MAX_AUDIO_CHARS
            or not _is_allowed_audio_data_url(data_url)
        ):
            await channel.send_response(
                ws, req_id, ok=False, error="audio_data_url is invalid", code="BAD_REQUEST"
            )
            return
        try:
            transcript = await _transcribe_audio([(data_url, "用户麦克风")])
        except Exception as exc:  # noqa: BLE001
            await channel.send_response(
                ws, req_id, ok=False, error=str(exc) or "ASR failed", code="ASR_ERROR"
            )
            return
        await channel.send_response(
            ws, req_id, ok=True, payload={"transcript": transcript}
        )

    async def _video_agent(ws, req_id, params, session_id):
        del session_id
        question = str(params.get("question") or "").strip() if isinstance(params, dict) else ""
        visual_answer = str(params.get("visual_answer") or "").strip() if isinstance(params, dict) else ""
        current_task = str(params.get("current_task") or "").strip() if isinstance(params, dict) else ""
        client_token = str(params.get("client_token") or "").strip() if isinstance(params, dict) else ""
        if not question or len(question) > 500:
            await channel.send_response(
                ws, req_id, ok=False, error="question must contain 1-500 characters", code="BAD_REQUEST"
            )
            return
        try:
            async def _tool_progress(stage: str, engine: str) -> None:
                if client_token:
                    await channel.send_event(
                        ws,
                        "video.agent.progress",
                        {"client_token": client_token, "stage": stage, "engine": engine},
                    )

            answer, current_task, tools_used = await _agent_answer(
                question, visual_answer, current_task, _tool_progress
            )
        except Exception as exc:  # noqa: BLE001
            await channel.send_response(
                ws, req_id, ok=False, error=str(exc) or "agent failed", code="AGENT_ERROR"
            )
            return
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"answer": answer, "current_task": current_task, "tools_used": tools_used},
        )

    channel.register_method("video.realtime.config", _realtime_config)
    channel.register_method("video.transcribe", _video_transcribe)
    channel.register_method("video.agent", _video_agent)
    channel.register_method("tts.synthesize", _tts_synthesize)
