# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# 启动入口：WebChannel + FeishuChannel 共用一个 ChannelManager，端到端联调 JiuWenClaw。
# 环境变量从 .env 加载。

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import secrets
import shutil
import sys
import time
import re

from pathlib import Path
from dotenv import load_dotenv
from typing import Any
import psutil

# 减少日志打印
from openjiuwen.core.common.logging import LogManager

from jiuwenclaw.channel import (
    DingTalkChannel,
    DingTalkConfig,
)

for logger in LogManager.get_all_loggers().values():
    logger.set_level(logging.CRITICAL)
from openjiuwen.core.foundation.llm import ProviderType

from jiuwenclaw.utils import get_config_file, get_root_dir, is_package_installation, logger
from jiuwenclaw.config import get_config, update_heartbeat_in_config, update_channel_in_config, update_browser_in_config

_PROJECT_ROOT = get_root_dir()
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_FILE)


def _get_package_dir() -> Path:
    """Get the jiuwenclaw package directory (for accessing package-internal files)."""
    if is_package_installation():
        # In package mode, app.py is at site-packages/jiuwenclaw/app.py
        # So parent is site-packages/jiuwenclaw/
        return Path(__file__).resolve().parent
    else:
        # In source mode, app.py is at project root
        # So parent.parent is project root/jiuwenclaw/
        return Path(__file__).resolve().parent.parent / "jiuwenclaw"


# 仅满足 Channel 构造所需，不入队、不路由；仅用 channel_manager + message_handler 做入站/出站
class _DummyBus:
    async def publish_user_messages(self, msg):  # noqa: ANN001, ARG002
        pass

    async def route_incoming_message(self, msg):  # noqa: ANN001, ARG002
        pass

    async def route_user_message(self, msg):
        pass


# 仅转发到 Agent 的 Web method
_FORWARD_REQ_METHODS = frozenset({
    "chat.send",
    "chat.interrupt",
    "chat.resume",
    "chat.user_answer",
    # "tts.synthesize",
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
})

_FORWARD_NO_LOCAL_HANDLER_METHODS = frozenset({
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
})

# 配置信息：config.get 返回、config.set 可修改的键（前端 param 名 -> 环境变量名）
_CONFIG_SET_ENV_MAP = {
    "model_provider": "MODEL_PROVIDER",
    "model": "MODEL_NAME",
    "api_base": "API_BASE",
    "api_key": "API_KEY",
    "email_address": "EMAIL_ADDRESS",
    "email_token": "EMAIL_TOKEN",
    "embed_api_key": "EMBED_API_KEY",
    "embed_api_base": "EMBED_API_BASE",
    "embed_model": "EMBED_MODEL",
    "jina_api_key": "JINA_API_KEY",
    "serper_api_key": "SERPER_API_KEY",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "evolution_auto_scan": "EVOLUTION_AUTO_SCAN",
}
# 配置项键名列表，用于日志等说明
CONFIG_KEYS = tuple(_CONFIG_SET_ENV_MAP.keys())


def _clear_agent_config_cache() -> None:
    """写回 config.yaml 后清除 agent 侧配置缓存，使下次读取时得到最新文件内容。"""
    try:
        from jiuwenclaw.agentserver.memory.config import clear_config_cache
        clear_config_cache()
    except Exception:  # noqa: BLE001
        pass


def _make_session_id() -> str:
    # 与前端 generateSessionId 保持一致：毫秒时间戳(16进制) + 6位随机16进制
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"sess_{ts}_{suffix}"


def _register_web_handlers(
        channel,
        agent_client=None,
        message_handler=None,
        channel_manager=None,
        on_config_saved=None,
        heartbeat_service=None,
        cron_controller=None,
):
    """注册 Web 前端需要的 method 与 on_connect。
    on_config_saved: 可选，config.set 写回 .env 后调用的回调；返回 True 表示已热更新未重启，False 表示已安排进程重启。
    heartbeat_service: 可选，GatewayHeartbeatService 实例，用于处理 heartbeat.get_conf / heartbeat.set_conf。
    """
    from jiuwenclaw.schema.message import Message, EventType

    def _resolve(ref, key="value"):
        """若为 ref 字典则取 key（无则返回 None），否则返回自身。"""
        if isinstance(ref, dict):
            return ref.get(key)
        return ref

    def _resolve_env_vars(value: Any) -> Any:
        """Recursively resolve environment variables in config values."""
        if isinstance(value, str):
            pattern = r'\$\{([^:}]+)(?::-([^}]*))?\}'

            def replace_env(match):
                var_name = match.group(1)
                default = match.group(2) if match.group(2) is not None else ""
                return os.getenv(var_name, default)

            return re.sub(pattern, replace_env, value)
        elif isinstance(value, dict):
            return {k: _resolve_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_resolve_env_vars(item) for item in value]
        else:
            return value

    async def _on_connect(ws):
        ac = _resolve(agent_client)
        if ac is None or not getattr(ac, "server_ready", False):
            logger.debug("[_on_connect] Agent 未就绪，跳过 connection.ack")
            return
        sid = _make_session_id()
        ack_msg = Message(
            id=f"ack-{sid}",
            type="event",
            channel_id=channel.channel_id,
            session_id=sid,
            params={},
            timestamp=time.time(),
            ok=True,
            event_type=EventType.CONNECTION_ACK,
            payload={
                "session_id": sid,
                "mode": "BUILD",
                "tools": [],
                "protocol_version": "1.0",
            },
        )
        mh = _resolve(message_handler)
        if mh:
            await mh.publish_robot_messages(ack_msg)
        else:
            await channel.send(ack_msg)

    channel.on_connect(_on_connect)

    async def _config_get(ws, req_id, params, session_id):
        # 返回 _CONFIG_SET_ENV_MAP 里所有键对应的环境变量当前值
        payload = {
            param_key: (os.getenv(env_key) or "")
            for param_key, env_key in _CONFIG_SET_ENV_MAP.items()
        }
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    def _persist_env_updates(updates: dict[str, str]) -> None:
        """把已更新的环境变量写回 .env（仅覆盖或追加对应 KEY=value 行）。"""
        env_path = _ENV_FILE
        if not updates:
            return
        try:
            lines: list[str] = []
            if env_path.is_file():
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            updated_keys = set(updates.keys())
            new_lines: list[str] = []
            for line in lines:
                stripped = line.strip()
                found = False
                for env_key, value in updates.items():
                    if stripped.startswith(env_key + "="):
                        new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
                        found = True
                        break
                if not found:
                    new_lines.append(line)
            for env_key, value in updates.items():
                if not any(s.strip().startswith(env_key + "=") for s in new_lines):
                    new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
            env_path.parent.mkdir(parents=True, exist_ok=True)
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except OSError as e:
            logger.warning("[config.set] 写回 .env 失败: %s", e)

    async def _config_set(ws, req_id, params, session_id):
        """根据前端消息内容更新配置（仅允许 _CONFIG_SET_ENV_MAP 中的键），并写回 .env。"""
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        updates: dict[str, str] = {}
        available_model_providers = [provider.value for provider in ProviderType]
        for param_key, env_key in _CONFIG_SET_ENV_MAP.items():
            if param_key not in params:
                continue
            val = params[param_key]
            if param_key == "model_provider" and val not in available_model_providers:
                await channel.send_response(
                    ws, req_id, ok=False, error=f"Model provider must in: {available_model_providers} ", code="BAD_REQUEST"
                )
                return
            if val is None:
                updates[env_key] = ""
            else:
                updates[env_key] = str(val).strip()
        for env_key, value in updates.items():
            os.environ[env_key] = value
        applied_without_restart = True
        if updates:
            _persist_env_updates(updates)
            logger.info("[config.set] 已更新: %s", list(updates.keys()))
            if on_config_saved:
                callback_result = on_config_saved(set(updates.keys()))
                if inspect.isawaitable(callback_result):
                    callback_result = await callback_result
                applied_without_restart = bool(callback_result)
        updated_param_keys = [k for k, e in _CONFIG_SET_ENV_MAP.items() if e in updates]
        await channel.send_response(
            ws, req_id, ok=True,
            payload={"updated": updated_param_keys, "applied_without_restart": applied_without_restart},
        )

    async def _channel_get(ws, req_id, params, session_id):
        """返回已注册的 channel 列表."""
        cm = _resolve(channel_manager)
        if cm is not None:
            channels = [{"channel_id": cid} for cid in cm.enabled_channels]
        else:
            channels = []
        await channel.send_response(ws, req_id, ok=True, payload={"channels": channels})

    async def _session_list(ws, req_id, params, session_id):
        """返回 workspace/session 下的 session_id 列表（子目录名）。"""
        limit = 20
        if isinstance(params, dict):
            raw_limit = params.get("limit")
            if isinstance(raw_limit, int):
                limit = raw_limit
            elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                limit = int(raw_limit.strip())
        limit = max(1, min(limit, 200))

        workspace_session_dir = _PROJECT_ROOT / "workspace" / "session"
        if not workspace_session_dir.exists() or not workspace_session_dir.is_dir():
            sessions = []
        else:
            sessions = sorted(
                [d.name for d in workspace_session_dir.iterdir() if d.is_dir()],
                reverse=True,
            )
            sessions = sessions[:limit]
        await channel.send_response(ws, req_id, ok=True, payload={"sessions": sessions})

    async def _session_create(ws, req_id, params, session_id):
        """创建一个新 session（在 workspace/session 下创建一个新目录）。"""
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST",
            )
            return
        session_id_to_create = params.get("session_id")
        if not isinstance(session_id_to_create, str) or not session_id_to_create.strip():
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST",
            )
            return
        session_id_to_create = session_id_to_create.strip()

        workspace_session_dir = _PROJECT_ROOT / "workspace" / "session"
        if not workspace_session_dir.exists():
            workspace_session_dir.mkdir(parents=True)
        session_dir = workspace_session_dir / session_id_to_create
        if session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session already exists", code="ALREADY_EXISTS",
            )
            return
        session_dir.mkdir()
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": session_id_to_create})

    async def _session_delete(ws, req_id, params, session_id):
        """删除一个 session（在 workspace/session 下删除一个目录）。"""
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST",
            )
            return
        session_id_to_delete = params.get("session_id")
        if not isinstance(session_id_to_delete, str) or not session_id_to_delete.strip():
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST",
            )
            return
        session_id_to_delete = session_id_to_delete.strip()

        workspace_session_dir = _PROJECT_ROOT / "workspace" / "session"
        session_dir = workspace_session_dir / session_id_to_delete
        if not session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session not found", code="NOT_FOUND",
            )
            return
        if not session_dir.is_dir():
            await channel.send_response(
                ws, req_id, ok=False, error="session is not a directory", code="BAD_REQUEST",
            )
            return
        shutil.rmtree(session_dir)
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": session_id_to_delete})

    async def _path_get(ws, req_id, params, session_id):
        """读 browser.chrome_path 并返回给前端（会解析环境变量）。"""
        try:
            config_base = get_config()
        except FileNotFoundError:
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={"chrome_path": ""},
            )
            return

        if not isinstance(config_base, dict):
            config_base = {}

        config = _resolve_env_vars(config_base)
        browser_cfg = config.get("browser", {}) if isinstance(config, dict) else {}
        chrome_path = ""
        if isinstance(browser_cfg, dict):
            value = browser_cfg.get("chrome_path", "")
            if isinstance(value, str):
                chrome_path = value

        await channel.send_response(ws, req_id, ok=True, payload={"chrome_path": chrome_path})

    async def _path_set(ws, req_id, params, session_id):
        """更新 browser.chrome_path 并写回 config。"""
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return

        chrome_path = params.get("chrome_path")
        if not isinstance(chrome_path, str):
            await channel.send_response(ws, req_id, ok=False, error="chrome_path must be string", code="BAD_REQUEST")
            return
        chrome_path = chrome_path.strip()

        try:
            update_browser_in_config({"chrome_path": chrome_path})
            _clear_agent_config_cache()
        except Exception as e:  # noqa: BLE001
            logger.warning("[path.set] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")
            return

        await channel.send_response(ws, req_id, ok=True, payload={"chrome_path": chrome_path})

    async def _browser_start(ws, req_id, params, session_id):
        """收到 browser.start 请求时，通过 import 调用 start_browser 启动浏览器。"""
        try:
            from jiuwenclaw.agentserver.tools.browser_start_client import start_browser

            config_path = str(get_config_file())
            returncode = start_browser(dry_run=False, config_file=config_path)
            await channel.send_response(
                ws,
                req_id,
                ok=True,
                payload={"returncode": returncode},
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("[browser.start] failed: %s", e)
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error=str(e),
                code="INTERNAL_ERROR",
            )

    async def _memory_compute(ws, req_id, params, session_id):

        process = psutil.Process()
        rss_bytes = process.memory_info().rss   # 物理内存
        rss_mb = rss_bytes / (1024 * 1024)     
        
        mem = psutil.virtual_memory()
        total_mb = mem.total / (1024 * 1024)
        available_mb = mem.available / (1024 * 1024)
        used_percent = mem.percent

        await channel.send_response(ws, req_id, ok=True, 
        payload={"rss_mb": rss_mb, "total_mb": total_mb, 
        "available_mb": available_mb})
    
    

    async def _chat_send(ws, req_id, params, session_id):
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"accepted": True, "session_id": session_id},
        )

    async def _chat_resume(ws, req_id, params, session_id):
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"accepted": True, "session_id": session_id},
        )

    async def _chat_interrupt(ws, req_id, params, session_id):
        intent = params.get("intent") if isinstance(params, dict) else None
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(intent, str) and intent:
            payload["intent"] = intent
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _chat_user_answer(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        request_id = params.get("request_id") if isinstance(params, dict) else None
        if isinstance(request_id, str) and request_id:
            payload["request_id"] = request_id
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _heartbeat_get_conf(ws, req_id, params, session_id):
        """返回当前心跳配置（every / target / active_hours）。"""
        hb = _resolve(heartbeat_service)
        if hb is None:
            await channel.send_response(ws, req_id, ok=False, error="heartbeat service not available",
                                        code="SERVICE_UNAVAILABLE")
            return
        try:
            payload = dict(hb.get_heartbeat_conf())
            await channel.send_response(ws, req_id, ok=True, payload=payload)
        except Exception as e:
            logger.exception("[heartbeat.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _heartbeat_set_conf(ws, req_id, params, session_id):
        """更新心跳配置并重启心跳服务；params 可含 every、target、active_hours。"""
        hb = _resolve(heartbeat_service)
        if hb is None:
            await channel.send_response(ws, req_id, ok=False, error="heartbeat service not available",
                                        code="SERVICE_UNAVAILABLE")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            every = params.get("every")
            target = params.get("target")
            active_hours = params.get("active_hours")
            if every is not None:
                every = float(every)
            if target is not None:
                target = str(target)
            if active_hours is not None:
                if not isinstance(active_hours, dict):
                    active_hours = None
                elif active_hours and ("start" not in active_hours or "end" not in active_hours):
                    # 必须同时包含 start/end，否则视为清除时间段（始终生效）
                    active_hours = None
            await hb.set_heartbeat_conf(every=every, target=target, active_hours=active_hours)
            payload = dict(hb.get_heartbeat_conf())
            try:
                update_heartbeat_in_config(payload)
                _clear_agent_config_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("[heartbeat.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload=payload)
        except ValueError as e:
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")
        except Exception as e:
            logger.exception("[heartbeat.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_feishu_get_conf(ws, req_id, params, session_id):
        """返回 FeishuChannel 的当前配置（由 ChannelManager 管理）。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("feishu")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.feishu.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_feishu_set_conf(ws, req_id, params, session_id):
        """更新 FeishuChannel 的配置，并按新配置重新实例化通道。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("feishu", params)
            conf = cm.get_conf("feishu")
            try:
                update_channel_in_config("feishu", conf)
                _clear_agent_config_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.feishu.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.feishu.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_xiaoyi_get_conf(ws, req_id, params, session_id):
        """返回 XiaoyiChannel 的当前配置（由 ChannelManager 管理）。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("xiaoyi")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.xiaoyi.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_xiaoyi_set_conf(ws, req_id, params, session_id):
        """更新 XiaoyiChannel 的配置，并按新配置重新实例化通道。"""
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("xiaoyi", params)
            conf = cm.get_conf("xiaoyi")
            try:
                update_channel_in_config("xiaoyi", conf)
                _clear_agent_config_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.xiaoyi.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.xiaoyi.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_dingtalk_get_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        try:
            conf = cm.get_conf("dingtalk")
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.dingtalk.get_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    async def _channel_dingtalk_set_conf(ws, req_id, params, session_id):
        cm = _resolve(channel_manager)
        if cm is None:
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="channel manager not available",
                code="SERVICE_UNAVAILABLE",
            )
            return
        if not isinstance(params, dict):
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="params must be object",
                code="BAD_REQUEST",
            )
            return
        try:
            await cm.set_conf("dingtalk", params)
            conf = cm.get_conf("dingtalk")
            try:
                update_channel_in_config("dingtalk", conf)
                _clear_agent_config_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("[channel.dingtalk.set_conf] 写回 config.yaml 失败: %s", e)
            await channel.send_response(ws, req_id, ok=True, payload={"config": conf})
        except Exception as e:  # noqa: BLE001
            logger.exception("[channel.dingtalk.set_conf] %s", e)
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    # ----- cron jobs -----

    def _get_cron():
        return _resolve(cron_controller)

    async def _cron_job_list(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        jobs = await cc.list_jobs()
        await channel.send_response(ws, req_id, ok=True, payload={"jobs": jobs})

    async def _cron_job_get(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        job = await cc.get_job(job_id)
        if job is None:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
            return
        await channel.send_response(ws, req_id, ok=True, payload={"job": job})

    async def _cron_job_create(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        try:
            job = await cc.create_job(params)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_update(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        patch = params.get("patch") or {}
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if not isinstance(patch, dict):
            await channel.send_response(ws, req_id, ok=False, error="patch must be object", code="BAD_REQUEST")
            return
        try:
            job = await cc.update_job(job_id, patch)
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_delete(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        deleted = await cc.delete_job(job_id)
        if not deleted:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
            return
        await channel.send_response(ws, req_id, ok=True, payload={"deleted": True})

    async def _cron_job_toggle(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        enabled = params.get("enabled", None)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        if enabled is None:
            await channel.send_response(ws, req_id, ok=False, error="enabled is required", code="BAD_REQUEST")
            return
        try:
            job = await cc.toggle_job(job_id, bool(enabled))
            await channel.send_response(ws, req_id, ok=True, payload={"job": job})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")

    async def _cron_job_preview(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        count = params.get("count", 5)
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            next_runs = await cc.preview_job(job_id, int(count) if count is not None else 5)
            await channel.send_response(ws, req_id, ok=True, payload={"next": next_runs})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="BAD_REQUEST")

    async def _cron_job_run_now(ws, req_id, params, session_id):
        cc = _get_cron()
        if cc is None:
            await channel.send_response(ws, req_id, ok=False, error="cron not available", code="INTERNAL_ERROR")
            return
        if not isinstance(params, dict):
            await channel.send_response(ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST")
            return
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            await channel.send_response(ws, req_id, ok=False, error="id is required", code="BAD_REQUEST")
            return
        try:
            run_id = await cc.run_now(job_id)
            await channel.send_response(ws, req_id, ok=True, payload={"run_id": run_id})
        except KeyError:
            await channel.send_response(ws, req_id, ok=False, error="job not found", code="NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            await channel.send_response(ws, req_id, ok=False, error=str(e), code="INTERNAL_ERROR")

    channel.register_method("config.get", _config_get)
    channel.register_method("config.set", _config_set)
    channel.register_method("channel.get", _channel_get)

    channel.register_method("session.list", _session_list)
    channel.register_method("session.create", _session_create)
    channel.register_method("session.delete", _session_delete)

    channel.register_method("path.get", _path_get)
    channel.register_method("path.set", _path_set)
    channel.register_method("browser.start", _browser_start)

    channel.register_method("memory.compute", _memory_compute)

    channel.register_method("chat.send", _chat_send)
    channel.register_method("chat.resume", _chat_resume)
    channel.register_method("chat.interrupt", _chat_interrupt)
    channel.register_method("chat.user_answer", _chat_user_answer)
    channel.register_method("heartbeat.get_conf", _heartbeat_get_conf)
    channel.register_method("heartbeat.set_conf", _heartbeat_set_conf)
    channel.register_method("channel.feishu.get_conf", _channel_feishu_get_conf)
    channel.register_method("channel.feishu.set_conf", _channel_feishu_set_conf)
    channel.register_method("channel.xiaoyi.get_conf", _channel_xiaoyi_get_conf)
    channel.register_method("channel.xiaoyi.set_conf", _channel_xiaoyi_set_conf)
    channel.register_method("channel.dingtalk.get_conf", _channel_dingtalk_get_conf)
    channel.register_method("channel.dingtalk.set_conf", _channel_dingtalk_set_conf)
    channel.register_method("cron.job.list", _cron_job_list)
    channel.register_method("cron.job.get", _cron_job_get)
    channel.register_method("cron.job.create", _cron_job_create)
    channel.register_method("cron.job.update", _cron_job_update)
    channel.register_method("cron.job.delete", _cron_job_delete)
    channel.register_method("cron.job.toggle", _cron_job_toggle)
    channel.register_method("cron.job.preview", _cron_job_preview)
    channel.register_method("cron.job.run_now", _cron_job_run_now)


async def _run() -> None:
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig
    from jiuwenclaw.channel.web_channel import WebChannel, WebChannelConfig
    from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
    from jiuwenclaw.gateway import (
        AgentWebSocketServer,
        GatewayHeartbeatService,
        HeartbeatConfig,
        WebSocketAgentServerClient,
    )
    from jiuwenclaw.gateway.channel_manager import ChannelManager
    from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.schema.message import Message, EventType, ReqMethod
    from jiuwenclaw.agentserver.memory.config import _load_config as _load_agent_config
    from jiuwenclaw.agentserver.tools.browser_tools import restart_local_browser_runtime_server
    from jiuwenclaw.utils import prepare_workspace, USER_WORKSPACE_DIR

    agent_port = int(os.getenv("AGENT_PORT", "18092"))
    web_host = os.getenv("WEB_HOST", "127.0.0.1")
    web_port = int(os.getenv("WEB_PORT", "19000"))
    web_path = os.getenv("WEB_PATH", "/ws")

    if not USER_WORKSPACE_DIR.exists():
        prepare_workspace(overwrite=False)

    def _do_restart() -> None:
        """重新执行当前进程以加载新 .env（配置修改后重启服务）。"""
        logger.info("[App] 配置已写回 .env，正在重启服务…")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def _schedule_restart() -> None:
        """延迟 2 秒后重启，便于先返回 config.set 的响应。"""
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(2.0, _do_restart)
        except RuntimeError:
            _do_restart()

    # ---------- 一次启动所有服务 ----------
    agent = JiuWenClaw()

    server = AgentWebSocketServer(
        agent, host="127.0.0.1", port=agent_port,
        ping_interval=20.0, ping_timeout=20.0,
    )
    await server.start()
    await asyncio.sleep(0.3)
    uri = f"ws://127.0.0.1:{agent_port}"

    client = WebSocketAgentServerClient(ping_interval=20.0, ping_timeout=20.0)
    await client.connect(uri)
    message_handler = MessageHandler(client)
    await message_handler.start_forwarding()

    cron_store = CronJobStore()
    cron_scheduler = CronSchedulerService(store=cron_store, agent_client=client, message_handler=message_handler)
    cron_controller = CronController.get_instance(store=cron_store, scheduler=cron_scheduler)

    # agent实例化需要在定时任务后
    await agent.create_instance()

    # 探活：周期性向 AgentServer 发送心跳，便于检测连接与 Agent 可用性
    # 优先从 config/config.yaml 的 heartbeat 段读取配置，其次回退到环境变量/默认值
    heartbeat_cfg: dict | None = None
    channels_cfg: dict | None = None
    try:
        full_cfg = _load_agent_config()
        heartbeat_cfg = full_cfg.get("heartbeat") if isinstance(full_cfg, dict) else None
        channels_cfg = full_cfg.get("channels") if isinstance(full_cfg, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[App] 读取 config.yaml heartbeat 配置失败，将使用默认值: %s", e)
        heartbeat_cfg = None
        channels_cfg = None

    if isinstance(heartbeat_cfg, dict):
        cfg_every = heartbeat_cfg.get("every")
        cfg_target = heartbeat_cfg.get("target")
        cfg_active_hours = heartbeat_cfg.get("active_hours")
    else:
        cfg_every = None
        cfg_target = None
        cfg_active_hours = None

    # interval_seconds：环境变量优先，其次 heartbeat.every，最后默认 60
    heartbeat_interval = float(
        os.getenv("HEARTBEAT_INTERVAL")
        or (str(cfg_every) if cfg_every is not None else "60")
    )
    # timeout_seconds 依旧仅由环境变量控制，保持兼容
    heartbeat_timeout = float(os.getenv("HEARTBEAT_TIMEOUT", "30")) if os.getenv("HEARTBEAT_TIMEOUT") else None
    # relay_channel_id：环境变量优先，其次 heartbeat.target，最后默认 "web"
    heartbeat_relay_channel = os.getenv("HEARTBEAT_RELAY_CHANNEL_ID") or (
        str(cfg_target) if cfg_target is not None else "web"
    )

    heartbeat_config = HeartbeatConfig(
        interval_seconds=heartbeat_interval,
        timeout_seconds=heartbeat_timeout,
        relay_channel_id=heartbeat_relay_channel,
        active_hours=cfg_active_hours if isinstance(cfg_active_hours, dict) else None,
    )
    heartbeat_service = GatewayHeartbeatService(client, heartbeat_config, message_handler=message_handler)
    await heartbeat_service.start()

    # 初始 Channel 配置（来自 config.yaml 的 channels 段，若不存在则为空）
    initial_channels_conf: dict = channels_cfg if isinstance(channels_cfg, dict) else {}

    channel_manager = ChannelManager(message_handler, config=initial_channels_conf)

    def _on_config_saved(updated_env_keys: set[str] | None = None) -> bool:
        """先尝试热更新，失败则安排延迟重启。返回 True 表示已热更新未重启，False 表示已安排重启。"""
        browser_runtime_keys = {"MODEL_PROVIDER", "MODEL_NAME", "API_BASE", "API_KEY"}
        try:
            agent.reload_agent_config()
            if updated_env_keys and (browser_runtime_keys & set(updated_env_keys)):
                restart_local_browser_runtime_server()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[App] 配置热更新失败，将延迟重启: %s", e)
            _schedule_restart()
            return False

    web_config = WebChannelConfig(
        enabled=True, host=web_host, port=web_port, path=web_path,
    )
    web_channel = WebChannel(web_config, _DummyBus())
    _register_web_handlers(
        web_channel,
        agent_client=client,
        message_handler=message_handler,
        channel_manager=channel_manager,
        on_config_saved=_on_config_saved,
        heartbeat_service=heartbeat_service,
        cron_controller=cron_controller,
    )

    def _norm_and_forward(msg: Message) -> bool:
        method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
        if method_val not in _FORWARD_REQ_METHODS:
            return False
        is_stream = bool(msg.is_stream or method_val == ReqMethod.CHAT_SEND.value)
        params = dict(msg.params or {})
        if "query" not in params and "content" in params:
            params["query"] = params["content"]
        normalized = Message(
            id=msg.id,
            type=msg.type,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params=params,
            timestamp=msg.timestamp,
            ok=msg.ok,
            req_method=getattr(msg, "req_method", None) or ReqMethod.CHAT_SEND,
            mode=msg.mode,
            is_stream=is_stream,
            stream_seq=msg.stream_seq,
            stream_id=msg.stream_id,
            metadata=msg.metadata,
        )
        channel_manager._message_handler.handle_message(normalized)
        logger.info("[App] Web 入站 -> MessageHandler: id=%s channel_id=%s", msg.id, msg.channel_id)
        # 对仅转发、无本地处理器的方法，标记为“已处理”，避免 WebChannel 再返回 METHOD_NOT_FOUND。
        if method_val in _FORWARD_NO_LOCAL_HANDLER_METHODS:
            return True
        return False

    web_channel.on_message(_norm_and_forward)
    channel_manager._channels[web_channel.channel_id] = web_channel

    # ---------- 按配置管理 FeishuChannel / XiaoyiChannel（配置来源：config/config.yaml -> channels.*） ----------
    feishu_channel = None
    feishu_task = None
    xiaoyi_channel = None
    xiaoyi_task = None
    dingtalk_channel = None
    dingtalk_task = None
    _last_channels_conf = {}  # Store previous config to detect changes

    def _should_restart_channel(channel_name: str, old_conf: dict, new_conf: dict) -> bool:
        """Check if a channel configuration changed enough to require restart."""
        old_channel_conf = old_conf.get(channel_name) if isinstance(old_conf, dict) else None
        new_channel_conf = new_conf.get(channel_name) if isinstance(new_conf, dict) else None

        # If one exists and the other doesn't, changed
        if (old_channel_conf is None) != (new_channel_conf is None):
            return True

        # If both are None, not changed
        if old_channel_conf is None:
            return False

        # Compare all config fields (including nested structures)
        return old_channel_conf != new_channel_conf

    async def _stop_channel(channel, task, channel_name: str, background_wait: bool = False) -> None:
        """Stop a channel and its task.

        Args:
            channel: The channel instance to stop
            task: The async task to cancel
            channel_name: Name of the channel for logging
            background_wait: If True, wait for task cancellation in background (like DingtalkChannel)
        """
        if task is not None:
            task.cancel()

            if background_wait:
                async def wait_cancel():
                    try:
                        await task
                    except (TypeError, asyncio.CancelledError):
                        logger.info("[App] 取消旧 %sChannel 任务成功", channel_name.capitalize())
                asyncio.create_task(wait_cancel(), name=f"wait_{channel_name}_cancel")
            else:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[App] 等待 %sChannel 任务取消超时", channel_name.capitalize())
                except asyncio.CancelledError:
                    pass

        if channel is not None:
            try:
                await asyncio.wait_for(channel.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("[App] 停止 %sChannel 超时", channel_name.capitalize())
            except Exception as e:  # noqa: BLE001
                logger.warning("[App] 停止旧 %sChannel 失败: %s", channel_name.capitalize(), e)
            channel_manager.unregister_channel(channel.channel_id)

    def _is_channel_enabled(conf: dict | None, required_fields: list[str]) -> tuple[bool, str]:
        """Check if a channel should be enabled based on config. Returns (enabled, reason_log)."""
        if conf is None:
            return False, "未配置或格式错误"
        enabled_raw = conf.get("enabled", None)
        if enabled_raw is None:
            # Auto-enable if all required fields are present
            all_fields_present = all(conf.get(f) for f in required_fields)
            return all_fields_present, f"缺少 {','.join(required_fields)}" if not all_fields_present else ""
        return bool(enabled_raw), "enabled = false" if not enabled_raw else ""

    async def _apply_channel_config(conf: dict) -> None:
        """根据最新 Channel 配置重新实例化各 Channel"""
        nonlocal feishu_channel, feishu_task, xiaoyi_channel, xiaoyi_task, dingtalk_channel, dingtalk_task, _last_channels_conf

        # Detect which channels changed
        changed_channels = [c for c in ["feishu", "xiaoyi", "dingtalk"]
                           if _should_restart_channel(c, _last_channels_conf, conf)]
        _last_channels_conf = dict(conf or {})

        # ----- FeishuChannel -----
        if "feishu" in changed_channels:
            feishu_conf = conf.get("feishu") if isinstance(conf, dict) else None
            await _stop_channel(feishu_channel, feishu_task, "feishu")
            feishu_channel, feishu_task = None, None

            if isinstance(feishu_conf, dict):
                enabled, reason = _is_channel_enabled(feishu_conf, ["app_id", "app_secret"])
                if not enabled:
                    logger.info("[App] channels.feishu.%s，FeishuChannel 未启用", reason)
                else:
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=str(feishu_conf.get("app_id") or "").strip(),
                        app_secret=str(feishu_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(feishu_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(feishu_conf.get("verification_token") or "").strip(),
                        allow_from=feishu_conf.get("allow_from") or [],
                        chat_id=str(feishu_conf.get("chat_id") or "").strip(),
                    )
                    feishu_channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(feishu_channel)
                    feishu_task = asyncio.create_task(feishu_channel.start(), name="feishu")
                    logger.info("[App] 已按 config.yaml.channels.feishu 注册 FeishuChannel")
            else:
                logger.info("[App] channels.feishu 未配置或格式错误，FeishuChannel 不启用")

        # ----- XiaoyiChannel -----
        if "xiaoyi" in changed_channels:
            xiaoyi_conf = conf.get("xiaoyi") if isinstance(conf, dict) else None
            await _stop_channel(xiaoyi_channel, xiaoyi_task, "xiaoyi")
            xiaoyi_channel, xiaoyi_task = None, None

            if isinstance(xiaoyi_conf, dict):
                enabled, reason = _is_channel_enabled(xiaoyi_conf, ["ak", "sk", "agent_id"])
                if not enabled:
                    logger.info("[App] channels.xiaoyi.%s，XiaoyiChannel 未启用", reason)
                else:
                    xiaoyi_config = XiaoyiChannelConfig(
                        enabled=True,
                        ak=str(xiaoyi_conf.get("ak") or "").strip(),
                        sk=str(xiaoyi_conf.get("sk") or "").strip(),
                        agent_id=str(xiaoyi_conf.get("agent_id") or "").strip(),
                        ws_url1=str(xiaoyi_conf.get("ws_url1") or "wss://116.63.174.231/openclaw/v1/ws/link").strip(),
                        ws_url2=str(xiaoyi_conf.get("ws_url2") or "wss://hag.cloud.huawei.com/openclaw/v1/ws/link").strip(),
                        enable_streaming=bool(xiaoyi_conf.get("enable_streaming", True)),
                    )
                    xiaoyi_channel = XiaoyiChannel(xiaoyi_config, _DummyBus())
                    channel_manager.register_channel(xiaoyi_channel)
                    xiaoyi_task = asyncio.create_task(xiaoyi_channel.start(), name="xiaoyi")
                    logger.info("[App] 已按 config.yaml.channels.xiaoyi 注册 XiaoyiChannel")
            else:
                logger.info("[App] channels.xiaoyi 未配置或格式错误，XiaoyiChannel 不启用")

        # ----- DingtalkChannel -----
        if "dingtalk" in changed_channels:
            dingtalk_conf = conf.get("dingtalk") if isinstance(conf, dict) else None
            await _stop_channel(dingtalk_channel, dingtalk_task, "dingtalk", background_wait=True)
            dingtalk_channel, dingtalk_task = None, None

            if isinstance(dingtalk_conf, dict):
                enabled, reason = _is_channel_enabled(dingtalk_conf, ["client_id", "client_secret"])
                if not enabled:
                    logger.info("[App] channels.dingtalk.%s，DingtalkChannel 未启用", reason)
                else:
                    dingtalk_config = DingTalkConfig(
                        enabled=True,
                        client_id=str(dingtalk_conf.get("client_id") or "").strip(),
                        client_secret=str(dingtalk_conf.get("client_secret") or "").strip(),
                        allow_from=dingtalk_conf.get("allow_from") or [],
                    )
                    dingtalk_channel = DingTalkChannel(dingtalk_config, _DummyBus())
                    channel_manager.register_channel(dingtalk_channel)
                    dingtalk_task = asyncio.create_task(dingtalk_channel.start(), name="dingtalk")
                    logger.info("[App] 已按 config.yaml.channels.dingtalk 注册 DingtalkChannel")
            else:
                logger.info("[App] channels.dingtalk 未配置或格式错误，DingtalkChannel 不启用")

    # 将「配置更新时如何重新实例化 Channel」逻辑注册到 ChannelManager
    channel_manager.set_config_callback(_apply_channel_config)
    # 使用初始配置实例化一次（启动时，针对 feishu / xiaoyi）
    await channel_manager.set_config(initial_channels_conf)

    await channel_manager.start_dispatch()
    await cron_scheduler.start()
    web_task = asyncio.create_task(web_channel.start(), name="web-channel")
    logger.info(
        "[App] 已启动: Web ws://%s:%s%s  修改配置后将自动重启服务。Ctrl+C 退出。",
        web_host, web_port, web_path,
    )

    # 主循环仅以 WebChannel 的生命周期为准：
    # Feishu/Xiaoyi/Dingtalk 等 Channel 的 start/stop 由 _apply_channel_config 动态管理，
    # 不再将其任务纳入这里的 gather，以避免在热更新（如关闭 Feishu）时取消任务导致整个 E2E 提前退出。
    try:
        await web_task
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出…")
    except asyncio.CancelledError:
        pass
    finally:
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass
        await web_channel.stop()
        if feishu_channel is not None and feishu_task is not None:
            feishu_task.cancel()
            try:
                await feishu_task
            except asyncio.CancelledError:
                pass
            await feishu_channel.stop()
        if xiaoyi_channel is not None and xiaoyi_task is not None:
            xiaoyi_task.cancel()
            try:
                await xiaoyi_task
            except asyncio.CancelledError:
                pass
            await xiaoyi_channel.stop()
        if dingtalk_channel is not None and dingtalk_task is not None:
            dingtalk_task.cancel()
            try:
                await dingtalk_task
            except (TypeError, asyncio.CancelledError):
                pass
            await dingtalk_channel.stop()
        await cron_scheduler.stop()
        await channel_manager.stop_dispatch()
        await heartbeat_service.stop()
        await message_handler.stop_forwarding()
        await client.disconnect()
        await server.stop()
        logger.info("[App] E2E 已停止")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
