"""ASR and TTS adapters shared by video-live providers."""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from jiuwenswarm.gateway.channel_manager.web import joyai_provider


MAX_AUDIO_CHARS = 2_000_000
MAX_TTS_TEXT_CHARS = 800
_ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/mp4",
    "audio/mpeg",
}
_ASR_FILLER_ONLY = re.compile(
    r"^(?:(?:嗯|恩|呃|啊|哦|噢|唔|哼|额|诶|欸|哎|呀|唉|hmm|uhhuh|uh|um|ah|oh|ああ|ええ))+$",
    flags=re.IGNORECASE,
)


def is_allowed_audio_data_url(value: str) -> bool:
    header, separator, _ = value.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return False
    parts = header[5:].lower().split(";")
    return parts[0] in _ALLOWED_AUDIO_MIME_TYPES and "base64" in parts[1:]


def tts_model_config() -> tuple[str, str, str, str]:
    """Read the dedicated TTS endpoint without guessing from other models."""
    return (
        (os.environ.get("VOICE_TTS_ENDPOINT") or os.environ.get("TTS_API_BASE") or "")
        .strip()
        .rstrip("/"),
        (os.environ.get("VOICE_API_KEY") or os.environ.get("TTS_API_KEY") or "").strip(),
        (os.environ.get("VOICE_TTS_MODEL") or os.environ.get("TTS_MODEL_NAME") or "").strip(),
        (os.environ.get("VOICE_TTS_VOICE") or os.environ.get("TTS_VOICE") or "").strip(),
    )


def asr_model_config() -> tuple[str, str, str]:
    """Read the normalized ASR endpoint, with legacy ASR_* fallback."""
    return (
        (os.environ.get("VOICE_ASR_ENDPOINT") or os.environ.get("ASR_API_BASE") or "")
        .strip()
        .rstrip("/"),
        (os.environ.get("VOICE_API_KEY") or os.environ.get("ASR_API_KEY") or "").strip(),
        (os.environ.get("VOICE_ASR_MODEL") or os.environ.get("ASR_MODEL_NAME") or "").strip(),
    )


def clean_model_text(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def is_ignorable_asr_filler(transcript: str) -> bool:
    """Return true only when ASR produced punctuation and filler sounds."""
    normalized = re.sub(r"[\W_]+", "", str(transcript or "").lower())
    return bool(normalized and _ASR_FILLER_ONLY.fullmatch(normalized))


def _asr_uses_transcription_endpoint(model: str) -> bool:
    mode = (
        os.environ.get("VOICE_ASR_MODE") or os.environ.get("ASR_API_MODE") or ""
    ).strip().casefold()
    if mode in {"transcription", "transcriptions", "audio"}:
        return True
    if mode in {"chat", "chat_completion", "chat_completions"}:
        return False
    normalized = model.casefold()
    return any(name in normalized for name in ("sensevoice", "whisper", "telespeechasr"))


def _openai_endpoint_base(endpoint: str, route: str) -> str:
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if not path.casefold().endswith(route.casefold()):
        return endpoint.rstrip("/")
    base_path = path[: -len(route)].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))


def resolve_asr_endpoint(endpoint: str, model: str) -> tuple[str, str]:
    """Resolve a full ASR endpoint to its request kind and SDK base URL."""
    path = urlsplit(endpoint).path.rstrip("/").casefold()
    transcription_route = "/audio/transcriptions"
    chat_route = "/chat/completions"
    if path.endswith(transcription_route):
        return "transcription", _openai_endpoint_base(endpoint, transcription_route)
    if path.endswith(chat_route):
        return "chat", _openai_endpoint_base(endpoint, chat_route)
    kind = "transcription" if _asr_uses_transcription_endpoint(model) else "chat"
    return kind, endpoint.rstrip("/")


def resolve_tts_endpoint(endpoint: str) -> str:
    """Accept a full speech endpoint while retaining legacy API-base compatibility."""
    normalized = endpoint.rstrip("/")
    if urlsplit(normalized).path.casefold().endswith("/audio/speech"):
        return normalized
    return f"{normalized}/audio/speech"


def _decode_audio_data_url(data_url: str, index: int) -> tuple[str, bytes, str]:
    header, separator, encoded = data_url.partition(",")
    if not separator or not is_allowed_audio_data_url(data_url):
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


async def transcribe_audio(
    audio_inputs: list[tuple[str, str]],
    *,
    use_joyai_voice: bool,
    log_event: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    if use_joyai_voice:
        if not audio_inputs:
            return ""
        transcripts = [
            await joyai_provider.transcribe_channel(data_url, log_event=log_event)
            for data_url, _ in audio_inputs
        ]
        return "。".join(text for text in transcripts if text)

    from openai import AsyncOpenAI

    endpoint, api_key, model = asr_model_config()
    if not audio_inputs or not all((endpoint, api_key, model)):
        return ""
    endpoint_kind, api_base = resolve_asr_endpoint(endpoint, model)
    client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=45.0)
    try:
        if endpoint_kind == "transcription":
            transcripts: list[str] = []
            for index, (data_url, _) in enumerate(audio_inputs, start=1):
                filename, audio, mime_type = _decode_audio_data_url(data_url, index)
                response = await client.audio.transcriptions.create(
                    model=model,
                    file=(filename, audio, mime_type),
                )
                text = response if isinstance(response, str) else getattr(response, "text", "")
                cleaned = clean_model_text(str(text or ""))
                if cleaned:
                    transcripts.append(cleaned)
            return "。".join(transcripts)

        content = [
            {"type": "audio_url", "audio_url": {"url": data_url}}
            for data_url, _ in audio_inputs
        ]
        content.append({
            "type": "text",
            "text": "只转写清晰可辨的用户中文，不要解释；静音、噪声或听不清时返回空字符串，不得补写。",
        })
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=160,
            temperature=0,
        )
        return clean_model_text(response.choices[0].message.content or "")
    finally:
        await client.close()


async def synthesize_speech(text: str, *, use_joyai_voice: bool) -> tuple[bytes, str, str]:
    if use_joyai_voice:
        return await joyai_provider.synthesize_channel(text)

    endpoint, api_key, model, voice = tts_model_config()
    if not endpoint or not model:
        raise RuntimeError("请配置 VOICE_TTS_ENDPOINT 和 VOICE_TTS_MODEL")
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
            resolve_tts_endpoint(endpoint), headers=headers, json=payload
        )
    if response.status_code >= 400:
        raise RuntimeError(f"TTS 请求失败 ({response.status_code})")
    if not response.content:
        raise RuntimeError("TTS 没有返回音频")
    return response.content, "audio/mpeg", model
