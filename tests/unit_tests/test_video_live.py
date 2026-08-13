from __future__ import annotations

import base64

import pytest

from jiuwenswarm.common import config as common_config
from jiuwenswarm.gateway.channel_manager.web import video_live


class FakeChannel:
    def __init__(self) -> None:
        self.handlers = {}
        self.responses = []
        self.events = []

    def register_method(self, method, handler) -> None:
        self.handlers[method] = handler

    async def send_response(self, ws, req_id, **response) -> None:
        self.responses.append((req_id, response))

    async def send_event(self, ws, event, payload) -> None:
        self.events.append((event, payload))


def test_model_configs_do_not_guess_other_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(common_config, "get_config", lambda: {})
    for name in ("VIDEO_API_BASE", "VIDEO_API_KEY", "VIDEO_MODEL_NAME", "TTS_API_BASE", "TTS_API_KEY", "TTS_MODEL_NAME", "TTS_VOICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VISION_API_BASE", "https://wrong.example/v1")
    monkeypatch.setenv("AUDIO_API_BASE", "https://wrong.example/v1")

    assert video_live._video_model_config() == ("", "", "")
    assert video_live._tts_model_config() == ("", "", "", "")


def test_realtime_url_is_derived_from_video_config(monkeypatch) -> None:
    monkeypatch.delenv("VIDEO_REALTIME_PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        video_live,
        "_video_model_config",
        lambda: ("https://video.example/v1", "key", "model"),
    )

    assert video_live._realtime_public_url() == "wss://video.example/v1/realtime"


def test_realtime_ref_audio_reads_configured_file(monkeypatch, tmp_path) -> None:
    reference = tmp_path / "ref.wav"
    reference.write_bytes(b"wave")
    monkeypatch.setenv("VIDEO_REALTIME_REF_AUDIO_PATH", str(reference))

    assert video_live._realtime_ref_audio() == base64.b64encode(b"wave").decode()


def test_registers_only_realtime_support_methods() -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    assert set(channel.handlers) == {
        "video.realtime.config",
        "video.transcribe",
        "video.agent",
        "tts.synthesize",
    }


@pytest.mark.asyncio
async def test_transcribe_handler_returns_verified_text(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_transcribe(audio_inputs):
        assert audio_inputs[0][1] == "用户麦克风"
        return "这个是什么"

    monkeypatch.setattr(video_live, "_transcribe_audio", fake_transcribe)
    await channel.handlers["video.transcribe"](
        object(), "request-1", {"audio_data_url": "data:audio/wav;base64,ZmFrZQ=="}, "session"
    )

    assert channel.responses[-1][1]["payload"] == {"transcript": "这个是什么"}


@pytest.mark.asyncio
async def test_agent_handler_returns_jiuwen_tool_action(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_agent(question, visual_answer, current_task, tool_progress):
        assert question == "如果出现书就告诉我"
        assert visual_answer == ""
        assert current_task == ""
        assert callable(tool_progress)
        await tool_progress("started", "DuckDuckGo 免费搜索")
        return "好的", "看到书时提醒", ["set_current_task"]

    monkeypatch.setattr(video_live, "_agent_answer", fake_agent)
    await channel.handlers["video.agent"](
        object(), "request-2", {"question": "如果出现书就告诉我", "client_token": "client-1"}, "session"
    )

    assert channel.responses[-1][1]["payload"]["current_task"] == "看到书时提醒"
    assert channel.events == [(
        "video.agent.progress",
        {"client_token": "client-1", "stage": "started", "engine": "DuckDuckGo 免费搜索"},
    )]


@pytest.mark.asyncio
async def test_tts_handler_returns_audio(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_synthesize(text):
        assert text == "hello"
        return b"audio", "audio/mpeg", "speech-model"

    monkeypatch.setattr(video_live, "_synthesize_speech", fake_synthesize)
    await channel.handlers["tts.synthesize"](
        object(), "request-3", {"text": "hello"}, "session"
    )

    assert channel.responses[-1][1]["payload"]["audio_base64"] == base64.b64encode(b"audio").decode()
