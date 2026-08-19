from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import struct
from types import SimpleNamespace
import wave

import openai
import pytest
import websockets
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from jiuwenswarm.common import config as common_config
from jiuwenswarm.gateway.channel_manager.web import video_live, web_connect


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


def test_joyai_config_is_opt_in_and_does_not_reuse_video_settings(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_API_BASE", "http://video.example/v1")
    monkeypatch.setenv("VIDEO_API_KEY", "video-key")
    monkeypatch.setenv("VIDEO_MODEL_NAME", "video-model")
    for name in ("JOYAI_API_BASE", "JOYAI_API_KEY", "JOYAI_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    assert video_live._joyai_model_config() == ("", "", "")

    monkeypatch.setenv("JOYAI_API_BASE", "http://joyai.example/v1/")
    monkeypatch.setenv("JOYAI_API_KEY", "EMPTY")
    monkeypatch.setenv("JOYAI_MODEL_NAME", "jdopensource/JoyAI-VL-Interaction")

    assert video_live._joyai_model_config() == (
        "http://joyai.example/v1",
        "EMPTY",
        "jdopensource/JoyAI-VL-Interaction",
    )


def _test_wav(pcm: bytes, sample_rate: int = 16_000) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm)
    return target.getvalue()


class _FakeVoiceSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return next(self.incoming)


@pytest.mark.asyncio
async def test_joyai_channel_asr_sends_pcm_with_native_header(monkeypatch) -> None:
    pcm = struct.pack("<4h", 100, -100, 200, -200)
    audio = base64.b64encode(_test_wav(pcm)).decode()
    socket = _FakeVoiceSocket([json.dumps({
        "asr_response": {
            "recognition_result": {"hypothesis": [{"text": "测试语音"}]}
        }
    })])
    monkeypatch.setenv("JOYAI_ASR_WS_URL", "ws://asr.example/ws/asr")
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: socket)

    result = await video_live._transcribe_joyai_channel(
        f"data:audio/wav;base64,{audio}"
    )

    assert result == "测试语音"
    assert json.loads(socket.sent[0])["request"]["sample_rate"] == 16_000
    assert json.loads(socket.sent[0])["recognize"]["do_partial_result"] is False
    assert socket.sent[1][:12] == struct.pack(">iii", -1, 0, 0)
    assert socket.sent[1][12:] == pcm


@pytest.mark.asyncio
async def test_joyai_channel_asr_retries_normal_close_before_result(monkeypatch) -> None:
    pcm = struct.pack("<4h", 100, -100, 200, -200)
    audio = base64.b64encode(_test_wav(pcm)).decode()
    closed = ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"), True)
    first_socket = _FakeVoiceSocket([closed])
    second_socket = _FakeVoiceSocket([json.dumps({
        "asr_response": {
            "recognition_result": {"hypothesis": [{"text": "重试成功"}]}
        }
    })])
    sockets = iter((first_socket, second_socket))

    async def recv_or_raise(self):
        message = next(self.incoming)
        if isinstance(message, BaseException):
            raise message
        return message

    monkeypatch.setattr(_FakeVoiceSocket, "recv", recv_or_raise)
    monkeypatch.setenv("JOYAI_ASR_WS_URL", "ws://asr.example/ws/asr")
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: next(sockets))
    retry_logs = []
    monkeypatch.setattr(video_live, "_append_asr_log", retry_logs.append)

    result = await video_live._transcribe_joyai_channel(
        f"data:audio/wav;base64,{audio}"
    )

    assert result == "重试成功"
    assert first_socket.sent[1][12:] == pcm
    assert second_socket.sent[1][12:] == pcm
    assert retry_logs[0]["outcome"] == "retrying"
    assert retry_logs[0]["retry_attempt"] == 1


@pytest.mark.asyncio
async def test_joyai_channel_tts_collects_pcm_and_returns_wav(monkeypatch) -> None:
    pcm = struct.pack("<4h", 1, 2, 3, 4)
    socket = _FakeVoiceSocket([
        pcm[:4],
        pcm[4:],
        json.dumps({"type": "response.done"}),
    ])
    monkeypatch.setenv("JOYAI_TTS_WS_URL", "ws://tts.example/ws/tts")
    monkeypatch.setenv("JOYAI_TTS_VOICE", "this-must-not-override-the-fixed-voice")
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: socket)

    audio, mime, model = await video_live._synthesize_joyai_channel("你好")

    assert mime == "audio/wav"
    assert "Qwen3-TTS" in model
    with wave.open(io.BytesIO(audio), "rb") as stream:
        assert stream.getframerate() == 24_000
        assert stream.readframes(stream.getnframes()) == pcm
    config = json.loads(socket.sent[0])["config"]
    assert config["voice"] == "vivian"
    assert config["temperature"] == 0.2
    assert "1.2x normal speed" in config["instructions"]
    assert json.loads(socket.sent[1])["type"] == "input_text.append"
    assert json.loads(socket.sent[2])["type"] == "input_text.commit"


def test_video_live_mode_is_explicit_and_defaults_to_realtime(monkeypatch) -> None:
    monkeypatch.delenv("VIDEO_LIVE_MODE", raising=False)
    assert video_live._video_live_mode() == "realtime"

    monkeypatch.setenv("VIDEO_LIVE_MODE", "JoyAI")
    assert video_live._video_live_mode() == "joyai"

    monkeypatch.setenv("VIDEO_LIVE_MODE", "unknown")
    assert video_live._video_live_mode() == "realtime"


@pytest.mark.asyncio
async def test_video_config_selects_joyai_without_realtime_reference_audio(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    monkeypatch.setattr(video_live, "_video_live_mode", lambda: "joyai")
    monkeypatch.setattr(
        video_live,
        "_joyai_model_config",
        lambda: (
            "http://127.0.0.1:18007/v1",
            "EMPTY",
            "jdopensource/JoyAI-VL-Interaction",
        ),
    )
    monkeypatch.setattr(
        video_live,
        "_realtime_ref_audio",
        lambda: pytest.fail("JoyAI config must not load Realtime reference audio"),
    )

    await channel.handlers["video.realtime.config"](
        object(), "config-request", {}, "web-session"
    )

    assert channel.responses[-1][1]["payload"] == {
        "provider": "joyai",
        "model": "jdopensource/JoyAI-VL-Interaction",
    }


@pytest.mark.parametrize(
    ("raw", "decision", "response", "delegation"),
    [
        ("</silence>", "silence", "", ""),
        ("</response> 新物体出现了", "response", "新物体出现了", ""),
        (
            "</response> 我帮你查询。 </delegation> 查询这个品牌",
            "delegation",
            "我帮你查询。",
            "查询这个品牌",
        ),
        ("plain fallback", "response", "plain fallback", ""),
    ],
)
def test_parse_joyai_action(raw, decision, response, delegation) -> None:
    assert video_live._parse_joyai_action(raw) == {
        "decision": decision,
        "response": response,
        "delegation": delegation,
    }


@pytest.mark.asyncio
async def test_request_joyai_frame_uses_stateful_chat_completion(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "model": "streaming-infer-adapter",
                "choices": [{
                    "message": {
                        "content": "</response> Checking. </delegation> Search current weather"
                    }
                }],
                "streamingharness": {
                    "timing": {"vllm_inference_ms": 321.0},
                    "memory": {"long_term_memory": "remembered"},
                },
            }

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(video_live.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_joyai_model_config",
        lambda: ("http://joyai.example/v1", "EMPTY", "joyai-model"),
    )

    result = await video_live._request_joyai_frame(
        "data:image/jpeg;base64,ZmFrZQ==",
        "持续观察，有重要变化时回应",
        "joyai-session-1",
        "1.0 seconds ~ 2.0 seconds",
    )

    request = calls[1]
    assert request["url"] == "http://joyai.example/v1/chat/completions"
    assert request["headers"]["x-streaming-session"] == "joyai-session-1"
    assert request["headers"]["x-system-prompt-key"] == "DEFAULT_SYSTEM_PROMPT_EN"
    assert request["headers"]["Authorization"] == "Bearer EMPTY"
    assert request["json"]["model"] == "joyai-model"
    request_text = request["json"]["messages"][0]["content"][0]
    assert request_text["type"] == "text"
    assert request_text["text"] == "持续观察，有重要变化时回应"
    assert request["json"]["messages"][0]["content"][1]["type"] == "image_url"
    assert request["json"]["max_tokens"] == 512
    assert request["json"]["temperature"] == 0.7
    assert request["json"]["top_p"] == 0.9
    assert request["json"]["extra_body"] == {
        "frame_time_range": "1.0 seconds ~ 2.0 seconds"
    }
    assert result["decision"] == "delegation"
    assert result["response"] == "Checking."
    assert result["delegation"] == "Search current weather"
    assert result["timing"] == {"vllm_inference_ms": 321.0}
    assert result["memory"] == {"long_term_memory": "remembered"}


@pytest.mark.asyncio
async def test_request_joyai_frame_without_instruction_sends_image_only(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "model": "streaming-infer-adapter",
                "choices": [{"message": {"content": "</silence>"}}],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(video_live.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_joyai_model_config",
        lambda: ("http://joyai.example/v1", "EMPTY", "joyai-model"),
    )

    result = await video_live._request_joyai_frame(
        "data:image/jpeg;base64,ZmFrZQ==",
        "",
        "joyai-session-frame-only",
    )

    request = calls[1]
    assert request["headers"]["x-streaming-session"] == "joyai-session-frame-only"
    assert request["headers"]["x-system-prompt-key"] == "DEFAULT_SYSTEM_PROMPT_EN"
    content = request["json"]["messages"][0]["content"]
    assert content == [{
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="},
    }]
    assert "extra_body" not in request["json"]
    assert result["decision"] == "silence"


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


@pytest.mark.parametrize(
    "question",
    ["停止监控", "请暂停当前任务", "不用再观察画面了", "先停一下"],
)
def test_recognizes_explicit_current_task_stop_requests(question) -> None:
    assert video_live._requests_task_stop(question)


@pytest.mark.parametrize(
    "question",
    ["不要停止监控", "画面里有什么", "停止播放视频"],
)
def test_does_not_treat_unrelated_requests_as_task_stop(question) -> None:
    assert not video_live._requests_task_stop(question)


@pytest.mark.asyncio
async def test_transcribe_audio_uses_audio_endpoint_for_sensevoice(monkeypatch) -> None:
    calls = []

    class FakeTranscriptions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="你好 世界")

    class FakeClient:
        def __init__(self, **kwargs):
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.fail_chat)
            )

        async def fail_chat(self, **kwargs):
            raise AssertionError("SenseVoice must not use chat completions")

        async def close(self):
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_model_config",
        lambda prefix: ("https://asr.example/v1", "key", "FunAudioLLM/SenseVoiceSmall"),
    )
    monkeypatch.delenv("ASR_API_MODE", raising=False)

    transcript = await video_live._transcribe_audio([
        ("data:audio/wav;base64,ZmFrZQ==", "用户麦克风")
    ])

    assert transcript == "你好 世界"
    assert calls[0]["model"] == "FunAudioLLM/SenseVoiceSmall"
    assert calls[0]["file"] == ("microphone-1.wav", b"fake", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_audio_keeps_chat_endpoint_for_omni(monkeypatch) -> None:
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="测试语音"))]
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=self.fail_audio)
            )
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def fail_audio(self, **kwargs):
            raise AssertionError("Omni ASR must keep using chat completions")

        async def close(self):
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_model_config",
        lambda prefix: ("https://asr.example/v1", "key", "Qwen/Qwen3-Omni-30B-A3B-Instruct"),
    )
    monkeypatch.delenv("ASR_API_MODE", raising=False)

    transcript = await video_live._transcribe_audio([
        ("data:audio/wav;base64,ZmFrZQ==", "用户麦克风")
    ])

    assert transcript == "测试语音"
    assert calls[0]["model"] == "Qwen/Qwen3-Omni-30B-A3B-Instruct"
    assert calls[0]["messages"][0]["content"][0]["type"] == "audio_url"


@pytest.mark.asyncio
async def test_agent_stops_current_task_without_model_call() -> None:
    answer, current_task, tools_used = await video_live._agent_answer(
        "停止监控", "", "持续翻译画面英文"
    )

    assert answer == "好的，已暂停当前任务。"
    assert current_task == ""
    assert tools_used == ["stop_current_task"]


@pytest.mark.asyncio
async def test_agent_router_is_bypassed_in_joyai_mode(monkeypatch) -> None:
    monkeypatch.setattr(video_live, "_video_live_mode", lambda: "joyai")

    answer, current_task, tools_used = await video_live._agent_answer(
        "现在你猜猜我在哪",
        "从画面看像是在工厂或仓库里。",
        "",
        "用户：请搜索农夫山泉的竞争对手",
    )

    assert answer == "从画面看像是在工厂或仓库里。"
    assert current_task == ""
    assert tools_used == []


def test_agent_router_prompt_uses_temporal_requirement_not_task_domain() -> None:
    prompt = video_live._AGENT_ROUTER_SYSTEM_PROMPT

    assert "继续接收后续画面" in prompt
    assert "跨多个时刻" in prompt
    assert "持续识别或转换内容" in prompt
    assert "跟踪对象或状态" in prompt
    assert "记录过程" in prompt
    assert "检查规则" in prompt
    assert "明确询问当前这一帧" in prompt
    assert "不得仅因任务不是翻译、提醒或计数而返回none" in prompt
    assert "外部事实或时效信息" in prompt
    assert "天气、新闻、价格" in prompt
    assert "只说‘我帮你查一下’而返回none" in prompt


def test_registers_only_realtime_support_methods() -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    assert set(channel.handlers) == {
        "video.realtime.config",
        "video.joyai.frame",
        "video.realtime.telemetry",
        "video.transcribe",
        "video.agent",
        "video.search.status",
        "tts.synthesize",
        "tts.stream.start",
        "tts.stream.cancel",
    }
    assert "video.search.status" in web_connect._LOCAL_ONLY_METHODS
    assert "video.joyai.frame" in web_connect._LOCAL_ONLY_METHODS
    assert "tts.stream.start" in web_connect._LOCAL_ONLY_METHODS
    assert "tts.stream.cancel" in web_connect._LOCAL_ONLY_METHODS


@pytest.mark.asyncio
async def test_joyai_frame_handler_returns_action_and_writes_metadata_only(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    calls = []
    logs = []

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        calls.append((frame_data_url, instruction, joyai_session_id))
        return {
            "decision": "response",
            "response": "检测到变化",
            "delegation": "",
            "raw_content": "</response> 检测到变化",
            "model": "streaming-infer-adapter",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 456.7,
            "timing": {"vllm_inference_ms": 300.0},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", logs.append)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-request-1",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "持续观察画面变化",
            "joyai_session_id": "joyai-session-1",
        },
        "web-session",
    )

    assert calls == [(
        "data:image/jpeg;base64,ZmFrZQ==",
        "持续观察画面变化",
        "joyai-session-1",
    )]
    assert channel.responses[-1][1]["payload"]["decision"] == "response"
    assert channel.responses[-1][1]["payload"]["tools_used"] == []
    assert channel.responses[-1][1]["payload"]["search_job"] is None
    assert [record["stage"] for record in logs] == ["requested", "completed"]
    assert logs[0]["frame_chars"] == 31
    assert "frame_data_url" not in logs[0]
    assert logs[1]["raw_content"] == "</response> 检测到变化"


@pytest.mark.asyncio
async def test_joyai_user_instruction_preserves_native_silence(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    calls = []

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        calls.append((instruction, joyai_session_id))
        return {
            "decision": "silence",
            "response": "",
            "delegation": "",
            "raw_content": "</silence>",
            "model": "joyai",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 100.0,
            "timing": {},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", lambda event: None)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-user-question",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "每当画面出现瓶子时介绍它的样子。",
            "question": "每当画面出现瓶子时介绍它的样子。",
            "request_kind": "user",
            "joyai_session_id": "joyai-session-user",
        },
        "web-session",
    )

    assert calls == [(
        "每当画面出现瓶子时介绍它的样子。",
        "joyai-session-user",
    )]
    payload = channel.responses[-1][1]["payload"]
    assert payload["decision"] == "silence"
    assert payload["response"] == ""


@pytest.mark.asyncio
async def test_joyai_monitor_silence_is_not_retried(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    calls = []

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        calls.append(joyai_session_id)
        return {
            "decision": "silence",
            "response": "",
            "delegation": "",
            "raw_content": "</silence>",
            "model": "joyai",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 100.0,
            "timing": {},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", lambda event: None)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-monitor",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "继续观察",
            "request_kind": "monitor",
            "joyai_session_id": "joyai-session-monitor",
        },
        "web-session",
    )

    assert calls == ["joyai-session-monitor"]
    assert channel.responses[-1][1]["payload"]["decision"] == "silence"


@pytest.mark.asyncio
async def test_joyai_monitor_accepts_frame_only_request(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    calls = []
    logs = []

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        calls.append((frame_data_url, instruction, joyai_session_id))
        return {
            "decision": "silence",
            "response": "",
            "delegation": "",
            "raw_content": "</silence>",
            "model": "joyai",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 50.0,
            "timing": {},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", logs.append)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-frame-only",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "",
            "request_kind": "monitor",
            "joyai_session_id": "joyai-session-monitor",
        },
        "web-session",
    )

    assert calls == [(
        "data:image/jpeg;base64,ZmFrZQ==",
        "",
        "joyai-session-monitor",
    )]
    assert channel.responses[-1][1]["payload"]["decision"] == "silence"
    assert logs[0]["frame_only"] is True


@pytest.mark.asyncio
async def test_joyai_delegation_starts_async_search_and_reuses_running_job(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    release_search = asyncio.Event()
    search_calls = []

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        return {
            "decision": "delegation",
            "response": "我帮你查询。",
            "delegation": "JD.com current stock price",
            "raw_content": (
                "</response> 我帮你查询。 "
                "</delegation> JD.com current stock price"
            ),
            "model": "streaming-infer-adapter",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 300.0,
            "timing": {"vllm_inference_ms": 200.0},
            "memory": {},
        }

    async def fake_search(query):
        search_calls.append(query)
        await release_search.wait()
        return (
            "Free search results (DuckDuckGo) for: JD.com stock\n"
            "1. Market result\n"
            "   URL: https://example.com/jd\n"
            "   Snippet: Current market data"
        )

    async def fake_fetch(sources):
        return [{**sources[0], "content": "Current market body", "error": ""}]

    async def fake_summary(question, query, search_result, evidence, trace_context):
        return "九问检索摘要（主对话模型根据网页正文生成）\nMarket summary.[来源1]"

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_execute_free_search", fake_search)
    monkeypatch.setattr(video_live, "_fetch_search_evidence", fake_fetch)
    monkeypatch.setattr(video_live, "_summarize_search_evidence", fake_summary)
    monkeypatch.setattr(video_live, "_append_joyai_log", lambda event: None)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)

    params = {
        "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
        "instruction": "回答用户关于当前股价的问题",
        "question": "京东现在的股价是多少？",
        "joyai_session_id": "joyai-session-tool",
    }
    await channel.handlers["video.joyai.frame"](
        object(), "joyai-tool-1", params, "web-session"
    )
    first = channel.responses[-1][1]["payload"]
    await channel.handlers["video.joyai.frame"](
        object(), "joyai-tool-2", params, "web-session"
    )
    second = channel.responses[-1][1]["payload"]

    assert first["tools_used"] == ["mcp_free_search"]
    assert first["search_job"]["status"] == "running"
    assert first["search_job"]["query"] == "JD.com current stock price"
    assert second["search_job"]["id"] == first["search_job"]["id"]
    assert second["search_job"]["reused"] is True
    await asyncio.sleep(0)
    assert search_calls == [
        "JD.com current stock price",
        "JD.com current stock price 官方 最新 更新时间",
    ]

    release_search.set()
    for _ in range(100):
        if any(event == "video.search.completed" for event, _ in channel.events):
            break
        await asyncio.sleep(0.01)
    assert any(event == "video.search.completed" for event, _ in channel.events)


@pytest.mark.asyncio
async def test_joyai_tool_response_does_not_start_recursive_search(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        return {
            "decision": "delegation",
            "response": "根据刚才的查询结果，香港今天有雨。",
            "delegation": "再次查询香港天气",
            "raw_content": (
                "</response> 根据刚才的查询结果，香港今天有雨。"
                "</delegation> 再次查询香港天气"
            ),
            "model": "joyai",
            "joyai_session_id": joyai_session_id,
            "latency_ms": 100.0,
            "timing": {},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", lambda event: None)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-tool-result",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "[搜索结果] 请根据查询结果回答用户",
            "question": "香港今天天气如何？",
            "request_kind": "tool",
            "joyai_session_id": "joyai-session-tool-result",
        },
        "web-session",
    )

    payload = channel.responses[-1][1]["payload"]
    assert payload["decision"] == "delegation"
    assert payload["tools_used"] == []
    assert payload["search_job"] is None


@pytest.mark.asyncio
async def test_joyai_frame_handler_rejects_non_image_payload(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fail_request(*args):
        raise AssertionError("invalid frames must not reach JoyAI")

    monkeypatch.setattr(video_live, "_request_joyai_frame", fail_request)
    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-request-bad",
        {"frame_data_url": "not-an-image", "instruction": "观察画面"},
        "web-session",
    )

    assert channel.responses[-1][1]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_realtime_telemetry_writes_sanitized_event(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    captured = []
    monkeypatch.setattr(video_live, "_append_realtime_telemetry", captured.append)

    await channel.handlers["video.realtime.telemetry"](
        object(),
        "telemetry-1",
        {"event": "barge_in_confirmed", "level": 1800, "secret": "discard"},
        "session",
    )

    assert channel.responses[-1][1]["payload"] == {"logged": True}
    assert captured[0]["event"] == "barge_in_confirmed"
    assert captured[0]["level"] == 1800
    assert "secret" not in captured[0]


@pytest.mark.asyncio
async def test_current_task_applied_telemetry_uses_task_log(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    task_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)

    await channel.handlers["video.realtime.telemetry"](
        object(),
        "telemetry-task",
        {"event": "current_task_applied", "previous_task": "", "current_task": "持续翻译"},
        "session",
    )

    assert channel.responses[-1][1]["payload"] == {"logged": True}
    assert task_logs == [{
        "event": "current_task_applied",
        "previous_task": "",
        "current_task": "持续翻译",
        "stage": "current_task_applied",
    }]


@pytest.mark.asyncio
async def test_realtime_start_telemetry_uses_session_log(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    session_logs = []
    monkeypatch.setattr(video_live, "_append_realtime_session_log", session_logs.append)

    await channel.handlers["video.realtime.telemetry"](
        object(),
        "telemetry-start",
        {
            "event": "realtime_start_clicked",
            "source": "screen",
            "frame_count": 0,
            "secret": "discard",
        },
        "session",
    )

    assert channel.responses[-1][1]["payload"] == {"logged": True}
    assert session_logs == [{
        "event": "realtime_start_clicked",
        "source": "screen",
        "frame_count": 0,
    }]


@pytest.mark.asyncio
async def test_realtime_task_scheduler_telemetry_preserves_build_and_task(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    session_logs = []
    monkeypatch.setattr(video_live, "_append_realtime_session_log", session_logs.append)

    await channel.handlers["video.realtime.telemetry"](
        object(),
        "telemetry-reminder",
        {
            "event": "active_task_reminder_sent",
            "client_build": "async-search-v1",
            "task": "continuously translate new English text",
            "frame_count": 42,
            "secret": "discard",
        },
        "session",
    )

    assert channel.responses[-1][1]["payload"] == {"logged": True}
    assert session_logs == [{
        "event": "active_task_reminder_sent",
        "client_build": "async-search-v1",
        "task": "continuously translate new English text",
        "frame_count": 42,
    }]


@pytest.mark.asyncio
async def test_tool_flow_telemetry_uses_task_log(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    task_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)

    await channel.handlers["video.realtime.telemetry"](
        object(),
        "telemetry-tool",
        {
            "event": "search_result_answered",
            "job_id": "search-1",
            "realtime_answer": "这是搜索后的答案",
            "secret": "discard",
        },
        "session",
    )

    assert task_logs == [{
        "event": "search_result_answered",
        "job_id": "search-1",
        "realtime_answer": "这是搜索后的答案",
        "stage": "search_result_answered",
    }]


def test_jsonl_appends_are_atomic_across_threads(tmp_path) -> None:
    path = tmp_path / "concurrent.jsonl"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda index: video_live._append_jsonl(path, {"index": index, "text": "测试"}),
            range(100),
        ))

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(record["index"] for record in records) == list(range(100))


@pytest.mark.asyncio
async def test_transcribe_handler_returns_verified_text(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_transcribe(audio_inputs):
        assert audio_inputs[0][1] == "用户麦克风"
        return "这个是什么"

    monkeypatch.setattr(video_live, "_transcribe_audio", fake_transcribe)
    task_logs = []
    asr_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)
    monkeypatch.setattr(video_live, "_append_asr_log", asr_logs.append)
    await channel.handlers["video.transcribe"](
        object(), "request-1", {"audio_data_url": "data:audio/wav;base64,ZmFrZQ=="}, "session"
    )

    assert channel.responses[-1][1]["payload"] == {"transcript": "这个是什么"}
    assert task_logs[-1]["stage"] == "asr_completed"
    assert task_logs[-1]["transcript"] == "这个是什么"
    assert asr_logs == [{
        "request_id": "request-1",
        "session_id": "session",
        "audio_chars": 30,
        "audio_mime": "audio/wav",
        "outcome": "completed",
        "transcript": "这个是什么",
        "has_transcript": True,
        "latency_ms": asr_logs[0]["latency_ms"],
    }]


@pytest.mark.parametrize("transcript", [
    "嗯。",
    "呃……",
    "啊嗯",
    "hmm",
    "uh-huh",
    "ああ。",
])
def test_asr_filler_only_detection(transcript) -> None:
    assert video_live._is_ignorable_asr_filler(transcript) is True


@pytest.mark.parametrize("transcript", [
    "停止",
    "嗯，今天香港天气怎么样？",
    "好",
])
def test_asr_filler_detection_preserves_real_requests(transcript) -> None:
    assert video_live._is_ignorable_asr_filler(transcript) is False


@pytest.mark.asyncio
async def test_transcribe_handler_ignores_filler_only_result(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def filler_transcribe(audio_inputs):
        return "嗯。"

    monkeypatch.setattr(video_live, "_transcribe_audio", filler_transcribe)
    task_logs = []
    asr_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)
    monkeypatch.setattr(video_live, "_append_asr_log", asr_logs.append)
    await channel.handlers["video.transcribe"](
        object(), "filler-request", {"audio_data_url": "data:audio/wav;base64,ZmFrZQ=="}, "session"
    )

    assert channel.responses[-1][1]["payload"] == {
        "transcript": "",
        "ignored_reason": "filler_only",
    }
    assert asr_logs[0]["outcome"] == "ignored_filler"
    assert asr_logs[0]["raw_transcript"] == "嗯。"
    assert asr_logs[0]["transcript"] == ""
    assert asr_logs[0]["ignored_reason"] == "filler_only"
    assert task_logs[-1]["stage"] == "asr_ignored_filler"
    assert task_logs[-1]["raw_transcript"] == "嗯。"


@pytest.mark.asyncio
async def test_transcribe_handler_logs_empty_and_failed_results(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    asr_logs = []
    monkeypatch.setattr(video_live, "_append_asr_log", asr_logs.append)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)

    async def empty_transcribe(audio_inputs):
        return ""

    monkeypatch.setattr(video_live, "_transcribe_audio", empty_transcribe)
    await channel.handlers["video.transcribe"](
        object(), "empty-request", {"audio_data_url": "data:audio/wav;base64,ZmFrZQ=="}, "session"
    )

    async def failed_transcribe(audio_inputs):
        raise RuntimeError("upstream disconnected")

    monkeypatch.setattr(video_live, "_transcribe_audio", failed_transcribe)
    await channel.handlers["video.transcribe"](
        object(), "failed-request", {"audio_data_url": "data:audio/wav;base64,ZmFrZQ=="}, "session"
    )

    assert asr_logs[0]["outcome"] == "empty"
    assert asr_logs[0]["transcript"] == ""
    assert asr_logs[1]["outcome"] == "failed"
    assert asr_logs[1]["error"] == "upstream disconnected"
    assert channel.responses[-1][1]["code"] == "ASR_ERROR"


@pytest.mark.asyncio
async def test_transcribe_handler_logs_rejected_input(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    asr_logs = []
    monkeypatch.setattr(video_live, "_append_asr_log", asr_logs.append)

    await channel.handlers["video.transcribe"](
        object(), "bad-request", {"audio_data_url": "not-a-data-url"}, "session"
    )

    assert asr_logs[0]["outcome"] == "rejected"
    assert asr_logs[0]["error"] == "audio_data_url is invalid"
    assert channel.responses[-1][1]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_agent_handler_returns_jiuwen_tool_action(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)

    async def fake_agent(question, realtime_answer, current_task, recent_chat, trace_context):
        assert question == "如果出现书就告诉我"
        assert realtime_answer == "好的，我会持续观察，看到书时告诉你。"
        assert current_task == ""
        assert recent_chat == ""
        assert trace_context == {"request_id": "request-2"}
        return "好的", "看到书时提醒", ["set_current_task"]

    monkeypatch.setattr(video_live, "_agent_answer", fake_agent)
    task_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)
    await channel.handlers["video.agent"](
        object(),
        "request-2",
        {
            "question": "如果出现书就告诉我",
            "realtime_answer": "好的，我会持续观察，看到书时告诉你。",
        },
        "session",
    )

    assert channel.responses[-1][1]["payload"]["current_task"] == "看到书时提醒"
    assert channel.events == []
    assert [event["stage"] for event in task_logs] == ["agent_requested", "agent_result"]
    assert task_logs[-1]["current_task_after"] == "看到书时提醒"
    assert task_logs[0]["realtime_answer"] == "好的，我会持续观察，看到书时告诉你。"


@pytest.mark.asyncio
async def test_agent_router_receives_user_realtime_answer_and_current_task(monkeypatch) -> None:
    calls = []
    router_logs = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"action":"set_current_task","task":"持续翻译画面中的英文"}'
            ))])

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self):
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_model_config",
        lambda prefix: ("https://router.example/v1", "key", "router-model"),
    )
    monkeypatch.setattr(video_live, "_append_video_task_log", router_logs.append)

    _, task, tools = await video_live._agent_answer(
        "帮我翻译画面里的英文",
        "好的，我会持续观察并翻译之后出现的英文。",
        "",
        "用户：瓶身写着Luckin Coffee",
        {"request_id": "router-request"},
    )

    prompt = calls[0]["messages"][1]["content"]
    assert "用户原话：帮我翻译画面里的英文" in prompt
    assert "Realtime自然回答：好的，我会持续观察并翻译之后出现的英文。" in prompt
    assert "已有任务：无" in prompt
    assert "近期对话：用户：瓶身写着Luckin Coffee" in prompt
    system_prompt = calls[0]["messages"][0]["content"]
    assert "必须优先采用Realtime回答中从画面识别出的具体品牌" in system_prompt
    assert "纠正用户原话中明显的ASR同音误识别" in system_prompt
    assert "query必须包含‘农夫山泉’" in system_prompt
    assert task == "持续翻译画面中的英文"
    assert tools == ["set_current_task"]
    assert router_logs == [{
        "stage": "router_model_response",
        "request_id": "router-request",
        "model": "router-model",
        "raw_response": '{"action":"set_current_task","task":"持续翻译画面中的英文"}',
    }]


@pytest.mark.asyncio
async def test_agent_search_returns_job_before_background_result(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    release_search = asyncio.Event()

    async def fake_agent(question, realtime_answer, current_task, recent_chat, trace_context):
        return "Luckin Coffee company profile", "", ["mcp_free_search"]

    async def fake_search(query):
        assert query == "Luckin Coffee company profile"
        await release_search.wait()
        return (
            "Free search results (DuckDuckGo) for: Luckin Coffee\n"
            "1. Official result\n"
            "   URL: https://example.com/official\n"
            "   Snippet: Official company information"
        )

    async def fake_fetch(sources):
        assert sources[0]["url"] == "https://example.com/official"
        return [{**sources[0], "content": "URL: https://example.com/official\nContent:\nOfficial body", "error": ""}]

    async def fake_summary(question, query, search_result, evidence, trace_context):
        assert question == "介绍一下这家公司"
        assert query == "Luckin Coffee company profile"
        assert "Official result" in search_result
        assert "Official body" in evidence[0]["content"]
        assert trace_context["job_id"]
        return "九问检索摘要（主对话模型根据网页正文生成）\n瑞幸咖啡的官方资料。[来源1]"

    monkeypatch.setattr(video_live, "_agent_answer", fake_agent)
    monkeypatch.setattr(video_live, "_execute_free_search", fake_search)
    monkeypatch.setattr(video_live, "_fetch_search_evidence", fake_fetch)
    monkeypatch.setattr(video_live, "_summarize_search_evidence", fake_summary)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)

    await channel.handlers["video.agent"](
        object(),
        "search-request",
        {
            "question": "介绍一下这家公司",
            "search_session_id": "realtime-session-1",
        },
        "session",
    )

    payload = channel.responses[-1][1]["payload"]
    assert payload["answer"] == ""
    assert payload["tools_used"] == ["mcp_free_search"]
    assert payload["search_job"]["status"] == "running"
    assert not any(event == "video.search.completed" for event, _ in channel.events)

    release_search.set()
    for _ in range(300):
        if any(
            event in {"video.search.completed", "video.search.failed"}
            for event, _ in channel.events
        ):
            break
        await asyncio.sleep(0.01)

    assert not any(event == "video.search.failed" for event, _ in channel.events), channel.events
    completed = next(
        event_payload
        for event, event_payload in channel.events
        if event == "video.search.completed"
    )
    assert completed["job_id"] == payload["search_job"]["id"]
    assert completed["search_session_id"] == "realtime-session-1"
    assert completed["question"] == "介绍一下这家公司"
    assert "九问检索摘要" in completed["result"]
    assert "官方资料" in completed["result"]

    await channel.handlers["video.search.status"](
        object(),
        "search-status-request",
        {
            "job_id": payload["search_job"]["id"],
            "search_session_id": "realtime-session-1",
        },
        "session",
    )

    recovered = channel.responses[-1][1]["payload"]
    assert recovered["status"] == "completed"
    assert recovered["job_id"] == payload["search_job"]["id"]
    assert "九问检索摘要" in recovered["result"]


@pytest.mark.asyncio
async def test_video_search_uses_official_research_agent_rpc(monkeypatch) -> None:
    channel = FakeChannel()
    requests = []

    class FakeAgentClient:
        async def send_request(self, envelope):
            requests.append(envelope)
            return SimpleNamespace(
                ok=True,
                payload={
                    "answer": "瑞幸咖啡是中国咖啡连锁品牌。https://example.com/luckin",
                    "sources": ["https://example.com/luckin"],
                    "tools_used": ["mcp_free_search", "mcp_fetch_webpage"],
                    "model": "default",
                },
            )

    video_live.register_video_live_handler(channel, agent_client=FakeAgentClient())

    async def fake_agent(question, realtime_answer, current_task, recent_chat, trace_context):
        del question, current_task, recent_chat, trace_context
        assert realtime_answer == "瓶身品牌是 Luckin Coffee。"
        return "Luckin Coffee company profile", "", ["mcp_free_search"]

    monkeypatch.setattr(video_live, "_agent_answer", fake_agent)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)
    monkeypatch.setattr(
        video_live,
        "_execute_free_search",
        lambda query: pytest.fail("official ResearchAgent result must bypass legacy search"),
    )

    await channel.handlers["video.agent"](
        object(),
        "official-search-request",
        {
            "question": "介绍一下这个牌子",
            "realtime_answer": "瓶身品牌是 Luckin Coffee。",
            "search_session_id": "realtime-session-official",
        },
        "session",
    )

    job = channel.responses[-1][1]["payload"]["search_job"]
    for _ in range(100):
        if any(event == "video.search.completed" for event, _ in channel.events):
            break
        await asyncio.sleep(0.01)

    assert len(requests) == 1
    envelope = requests[0]
    assert envelope.method == "video.research"
    assert envelope.params == {
        "question": "介绍一下这个牌子",
        "query": "Luckin Coffee company profile",
        "visual_context": "瓶身品牌是 Luckin Coffee。",
        "search_session_id": "realtime-session-official",
    }
    completed = next(
        payload for event, payload in channel.events if event == "video.search.completed"
    )
    assert completed["job_id"] == job["id"]
    assert completed["engine"] == "Jiuwen ResearchAgent"
    assert "瑞幸咖啡" in completed["result"]


@pytest.mark.asyncio
async def test_video_search_falls_back_when_official_research_fails(monkeypatch) -> None:
    channel = FakeChannel()
    task_logs = []

    class FakeAgentClient:
        async def send_request(self, envelope):
            del envelope
            return SimpleNamespace(
                ok=False,
                payload={"error": "Max iterations reached without completion"},
            )

    video_live.register_video_live_handler(channel, agent_client=FakeAgentClient())

    async def fake_agent(question, realtime_answer, current_task, recent_chat, trace_context):
        del question, realtime_answer, current_task, recent_chat, trace_context
        return "香港天气", "", ["mcp_free_search"]

    async def fake_search(query):
        assert query in {"香港天气", "香港天气 官方 最新 更新时间"}
        return (
            "Free search results (DuckDuckGo) for: 香港天气\n"
            "1. 香港天文台\n"
            "   URL: https://www.hko.gov.hk/weather\n"
            "   Snippet: 官方实时天气"
        )

    async def fake_fetch(sources):
        return [{
            **sources[0],
            "content": "URL: https://www.hko.gov.hk/weather\nContent:\n香港天气资料",
            "error": "",
        }]

    async def fake_summary(question, query, search_result, evidence, trace_context):
        del question, query, search_result, evidence, trace_context
        return "香港天气资料。[来源1]"

    monkeypatch.setattr(video_live, "_agent_answer", fake_agent)
    monkeypatch.setattr(video_live, "_execute_free_search", fake_search)
    async def fake_official_evidence(question, query):
        del question, query
        return []

    monkeypatch.setattr(video_live, "_official_search_evidence", fake_official_evidence)
    monkeypatch.setattr(video_live, "_fetch_search_evidence", fake_fetch)
    monkeypatch.setattr(video_live, "_summarize_search_evidence", fake_summary)
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)

    await channel.handlers["video.agent"](
        object(),
        "official-search-fallback-request",
        {
            "question": "今天香港天气怎么样？",
            "search_session_id": "search-session-fallback",
        },
        "session",
    )

    for _ in range(100):
        if any(event == "video.search.completed" for event, _ in channel.events):
            break
        await asyncio.sleep(0.01)

    completed = next(
        payload for event, payload in channel.events if event == "video.search.completed"
    )
    assert completed["engine"] == "DuckDuckGo"
    assert completed["result"] == "香港天气资料。[来源1]"
    assert "Max iterations reached" not in completed["result"]
    assert any(
        item["stage"] == "official_research_failed_fallback"
        for item in task_logs
    )


@pytest.mark.asyncio
async def test_joyai_delegation_uses_same_official_research_agent_rpc(monkeypatch) -> None:
    channel = FakeChannel()
    requests = []

    class FakeAgentClient:
        async def send_request(self, envelope):
            requests.append(envelope)
            return SimpleNamespace(
                ok=True,
                payload={"answer": "农夫山泉品牌资料", "sources": []},
            )

    video_live.register_video_live_handler(channel, agent_client=FakeAgentClient())

    async def fake_request(frame_data_url, instruction, joyai_session_id):
        del frame_data_url, instruction, joyai_session_id
        return {
            "decision": "delegation",
            "response": "画面中的品牌是农夫山泉，我来查询它的资料。",
            "delegation": "农夫山泉品牌资料",
            "raw_content": "</response> 画面中的品牌是农夫山泉。 </delegation> 农夫山泉品牌资料",
            "model": "joyai",
            "joyai_session_id": "joyai-search-session",
            "latency_ms": 50.0,
            "timing": {},
            "memory": {},
        }

    monkeypatch.setattr(video_live, "_request_joyai_frame", fake_request)
    monkeypatch.setattr(video_live, "_append_joyai_log", lambda event: None)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)

    await channel.handlers["video.joyai.frame"](
        object(),
        "joyai-official-search",
        {
            "frame_data_url": "data:image/jpeg;base64,ZmFrZQ==",
            "instruction": "介绍一下这个品牌",
            "question": "介绍一下这个品牌",
            "request_kind": "user",
            "joyai_session_id": "joyai-search-session",
            "search_session_id": "search-session-joyai",
        },
        "session",
    )
    for _ in range(100):
        if any(event == "video.search.completed" for event, _ in channel.events):
            break
        await asyncio.sleep(0.01)

    assert len(requests) == 1
    assert requests[0].method == "video.research"
    assert requests[0].params["question"] == "介绍一下这个品牌"
    assert requests[0].params["query"] == "农夫山泉品牌资料"
    assert requests[0].params["visual_context"] == "画面中的品牌是农夫山泉，我来查询它的资料。"
    assert any(event == "video.search.completed" for event, _ in channel.events)


@pytest.mark.asyncio
async def test_video_search_uses_full_supported_result_count(monkeypatch) -> None:
    received = []

    class FakeSearch:
        async def invoke(self, arguments):
            received.append(arguments)
            return "result"

    import jiuwenswarm.agents.harness.common.tools.search_tools as search_tools

    monkeypatch.setattr(search_tools, "mcp_free_search", FakeSearch())

    assert await video_live._execute_free_search("测试搜索") == "result"
    assert received == [{
        "query": "测试搜索",
        "max_results": 20,
        "timeout_seconds": 20,
    }]


def test_search_result_sources_parses_ranked_urls_and_deduplicates() -> None:
    result = (
        "Free search results (DuckDuckGo) for: 香港天气\n"
        "1. 香港天文台\n"
        "   URL: https://www.hko.gov.hk/weather\n"
        "   Snippet: 官方实时天气\n"
        "2. 重复结果\n"
        "   URL: https://www.hko.gov.hk/weather\n"
        "   Snippet: 重复\n"
        "3. 其他来源\n"
        "   URL: https://weather.example/current\n"
        "   Snippet: 第三方天气"
    )

    assert video_live._search_result_sources(result) == [
        {
            "title": "香港天文台",
            "url": "https://www.hko.gov.hk/weather",
            "snippet": "官方实时天气",
        },
        {
            "title": "其他来源",
            "url": "https://weather.example/current",
            "snippet": "第三方天气",
        },
    ]


def test_search_queries_adds_official_supplement_for_time_sensitive_questions() -> None:
    assert video_live._search_queries("今天香港天气怎么样", "香港天气") == [
        "香港天气",
        "香港天气 官方 最新 更新时间",
    ]
    assert video_live._search_queries("介绍一下农夫山泉", "农夫山泉") == ["农夫山泉"]


def test_rank_search_sources_prioritizes_authoritative_domains() -> None:
    sources = [
        {"title": "第三方天气", "url": "https://weather.example/hk", "snippet": "香港天气"},
        {"title": "香港天文台", "url": "https://www.hko.gov.hk/tc/index.html", "snippet": ""},
        {"title": "百科", "url": "https://zh.wikipedia.org/wiki/Hong_Kong", "snippet": ""},
    ]

    ranked = video_live._rank_search_sources(sources, "香港天气")

    assert ranked[0]["url"] == "https://www.hko.gov.hk/tc/index.html"
    assert ranked[-1]["url"] == "https://zh.wikipedia.org/wiki/Hong_Kong"


@pytest.mark.asyncio
async def test_official_search_evidence_uses_hko_open_data_for_hong_kong_weather(monkeypatch) -> None:
    requested = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"forecastDesc": "大致多云，有几阵骤雨", "updateTime": "2026-08-18T14:45:00+08:00"}

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 12.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            requested.append(url)
            return FakeResponse()

    monkeypatch.setattr(video_live.httpx, "AsyncClient", FakeClient)

    evidence = await video_live._official_search_evidence("今天香港天气怎么样", "香港天气")

    assert len(requested) == 3
    assert all("lang=sc" in url for url in requested)
    assert len(evidence) == 3
    assert all(item["content"] for item in evidence)
    assert "大致多云" in evidence[0]["content"]
    assert await video_live._official_search_evidence("介绍农夫山泉", "农夫山泉") == []


@pytest.mark.asyncio
async def test_fetch_search_evidence_fetches_page_bodies_and_keeps_failures(monkeypatch) -> None:
    calls = []

    class FakeFetch:
        async def invoke(self, arguments):
            calls.append(arguments)
            if "failed" in arguments["url"]:
                return "[ERROR]: failed to fetch webpage: timeout"
            return f"URL: {arguments['url']}\nContent:\n正文"

    import jiuwenswarm.agents.harness.common.tools.web_fetch_tools as web_fetch_tools

    monkeypatch.setattr(web_fetch_tools, "mcp_fetch_webpage", FakeFetch())
    sources = [
        {"title": "成功", "url": "https://example.com/success", "snippet": ""},
        {"title": "失败", "url": "https://example.com/failed", "snippet": ""},
    ]

    evidence = await video_live._fetch_search_evidence(sources)

    assert len(calls) == 2
    assert calls[0]["max_chars"] == 6_000
    assert evidence[0]["content"].endswith("正文")
    assert evidence[1]["content"] == ""
    assert evidence[1]["error"].startswith("[ERROR]")


@pytest.mark.asyncio
async def test_search_summary_uses_main_model_and_only_fetched_evidence(monkeypatch) -> None:
    calls = []
    summary_logs = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="香港大致多云，有骤雨。[来源1]"
            ))])

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self):
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        video_live,
        "_model_config",
        lambda prefix: ("https://router.example/v1", "key", "router-model"),
    )
    monkeypatch.setattr(video_live, "_append_video_task_log", summary_logs.append)

    result = await video_live._summarize_search_evidence(
        "今天香港天气怎么样",
        "香港今天天气",
        "raw search snippets",
        [{
            "title": "香港天文台",
            "url": "https://www.hko.gov.hk/current",
            "snippet": "摘要",
            "content": "URL: https://www.hko.gov.hk/current\nContent:\n大致多云，有骤雨。",
            "error": "",
        }],
        {"job_id": "search-1"},
    )

    assert calls[0]["model"] == "router-model"
    assert "只允许使用所给网页正文回答" in calls[0]["messages"][0]["content"]
    assert "正文没有直接支持的结论一律不得输出" in calls[0]["messages"][0]["content"]
    assert "过期内容不得表述为当前事实" in calls[0]["messages"][0]["content"]
    assert "最终回答必须统一使用简体中文" in calls[0]["messages"][0]["content"]
    assert "不得输出繁体中文" in calls[0]["messages"][0]["content"]
    assert "直接用自然口语回答用户" in calls[0]["messages"][0]["content"]
    assert "不要使用标题、项目符号、表格或单独的来源清单" in calls[0]["messages"][0]["content"]
    assert "确实支持该事实" in calls[0]["messages"][0]["content"]
    assert "大致多云，有骤雨" in calls[0]["messages"][1]["content"]
    assert "香港大致多云，有骤雨。[来源1]" in result
    assert "https://www.hko.gov.hk/current" in result
    assert summary_logs[0]["stage"] == "search_summarized"


@pytest.mark.asyncio
async def test_tts_handler_returns_audio(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    task_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)

    async def fake_synthesize(text):
        assert text == "hello"
        return b"audio", "audio/mpeg", "speech-model"

    monkeypatch.setattr(video_live, "_synthesize_speech", fake_synthesize)
    await channel.handlers["tts.synthesize"](
        object(), "request-3", {"text": "hello"}, "session"
    )

    assert channel.responses[-1][1]["payload"]["audio_base64"] == base64.b64encode(b"audio").decode()
    assert [item["stage"] for item in task_logs] == ["tts_requested", "tts_completed"]
    assert task_logs[-1]["audio_bytes"] == 5
    assert task_logs[-1]["model"] == "speech-model"


@pytest.mark.asyncio
async def test_tts_stream_handler_pushes_pcm_before_completion(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    task_logs = []
    monkeypatch.setattr(video_live, "_append_video_task_log", task_logs.append)
    monkeypatch.setattr(video_live, "_video_live_mode", lambda: "joyai")
    pcm_chunks = [struct.pack("<2h", 1, 2), struct.pack("<2h", 3, 4)]

    async def fake_stream(text, on_chunk):
        assert text == "流式语音"
        for chunk in pcm_chunks:
            await on_chunk(chunk)
        return sum(map(len, pcm_chunks)), len(pcm_chunks)

    monkeypatch.setattr(video_live, "_stream_joyai_channel_pcm", fake_stream)
    await channel.handlers["tts.stream.start"](
        object(),
        "stream-request",
        {"text": "流式语音", "stream_id": "stream-1"},
        "session",
    )

    assert channel.responses[-1][1]["payload"] == {
        "started": True,
        "stream_id": "stream-1",
        "sample_rate": 24_000,
    }
    for _ in range(20):
        if any(event == "video.tts.done" for event, _ in channel.events):
            break
        await asyncio.sleep(0)

    chunks = [
        base64.b64decode(payload["audio_base64"])
        for event, payload in channel.events
        if event == "video.tts.chunk"
    ]
    assert chunks == pcm_chunks
    assert channel.events[-1][0] == "video.tts.done"
    assert channel.events[-1][1]["chunk_count"] == 2
    assert [record["stage"] for record in task_logs] == [
        "tts_stream_requested",
        "tts_stream_first_chunk",
        "tts_stream_completed",
    ]


@pytest.mark.asyncio
async def test_tts_stream_cancel_stops_background_generation(monkeypatch) -> None:
    channel = FakeChannel()
    video_live.register_video_live_handler(channel)
    monkeypatch.setattr(video_live, "_append_video_task_log", lambda event: None)
    monkeypatch.setattr(video_live, "_video_live_mode", lambda: "joyai")
    started = asyncio.Event()

    async def blocked_stream(text, on_chunk):
        del text, on_chunk
        started.set()
        await asyncio.Event().wait()
        return 0, 0

    monkeypatch.setattr(video_live, "_stream_joyai_channel_pcm", blocked_stream)
    ws = object()
    await channel.handlers["tts.stream.start"](
        ws,
        "stream-request",
        {"text": "很长的语音", "stream_id": "stream-cancel"},
        "session",
    )
    await started.wait()
    await channel.handlers["tts.stream.cancel"](
        ws,
        "cancel-request",
        {"stream_id": "stream-cancel"},
        "session",
    )
    for _ in range(20):
        if any(event == "video.tts.cancelled" for event, _ in channel.events):
            break
        await asyncio.sleep(0)

    assert channel.responses[-1][1]["payload"]["cancelled"] is True
    assert channel.events[-1] == (
        "video.tts.cancelled",
        {"stream_id": "stream-cancel"},
    )
