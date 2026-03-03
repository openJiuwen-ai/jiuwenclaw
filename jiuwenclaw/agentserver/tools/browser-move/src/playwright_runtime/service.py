#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Browser backend service with sticky sessions and guardrails."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import shlex
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jiuwenclaw.browser_timeout_policy import (
    allow_short_timeout_override,
    resolve_browser_task_timeout,
)
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.middleware.base import (
    AgentCallbackEvent,
    AgentMiddleware,
    AnyAgentCallback,
)
from playwright_runtime import REPO_ROOT
from playwright_runtime.agents import build_browser_worker_agent
from playwright_runtime.config import BrowserRunGuardrails, resolve_playwright_mcp_cwd
from playwright_runtime.drivers.managed_browser import ManagedBrowserDriver
from playwright_runtime.hooks import BrowserCancellationMiddleware, BrowserRunCancelled
from playwright_runtime.profiles import BrowserProfile, BrowserProfileStore


def extract_json_object(text: Any) -> Dict[str, Any]:
    """Best-effort JSON extraction from model text."""
    if isinstance(text, dict):
        return text
    if text is None:
        return {}

    raw = str(text).strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    if "```json" in raw:
        start = raw.find("```json") + len("```json")
        end = raw.find("```", start)
        if end > start:
            block = raw[start:end].strip()
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first:last + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return {}


class BrowserService:
    """Backend browser service with sticky logical sessions."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
        cancel_store: Optional[BaseKVStore] = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.mcp_cfg = mcp_cfg
        self.guardrails = guardrails
        self._cancel_store: BaseKVStore = cancel_store or InMemoryKVStore()

        self.started = False
        self._browser_agent: Optional[ReActAgent] = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._sessions: set[str] = set()
        self._inflight_tasks: Dict[str, set[asyncio.Task[Any]]] = {}
        self._pending_middlewares: List[AgentMiddleware] = []
        self._pending_callbacks: List[Tuple[AgentCallbackEvent, AnyAgentCallback, int]] = []
        self._screenshot_subdir = "screenshots"
        self._mcp_cwd = self._resolve_mcp_cwd()
        self._screenshots_dir = self._mcp_cwd / self._screenshot_subdir
        self._profile_store = BrowserProfileStore(self._resolve_profile_store_path())
        self._profile_name = (os.getenv("BROWSER_PROFILE_NAME") or "jiuwenclaw").strip() or "jiuwenclaw"
        self._driver_mode = self._resolve_driver_mode()
        self._active_profile: Optional[BrowserProfile] = None
        self._managed_driver: Optional[ManagedBrowserDriver] = None

    @staticmethod
    def _parse_env_args(value: str) -> List[str]:
        raw = (value or "").strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        return [part for part in shlex.split(raw) if part]

    def _resolve_profile_store_path(self) -> Path:
        configured = (os.getenv("BROWSER_PROFILE_STORE_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path(REPO_ROOT).expanduser() / ".browser" / "profiles.json"

    def _resolve_driver_mode(self) -> str:
        explicit = (os.getenv("BROWSER_DRIVER") or "").strip().lower()
        if explicit:
            if explicit not in {"remote", "managed"}:
                raise ValueError("BROWSER_DRIVER must be one of: remote, managed")
            return explicit
        return "remote"

    def _resolve_effective_timeout(self, timeout_s: Optional[int]) -> int:
        effective_timeout = resolve_browser_task_timeout(timeout_s, self.guardrails.timeout_s)
        requested_timeout = None
        if timeout_s is not None:
            try:
                parsed = int(timeout_s)
                if parsed > 0:
                    requested_timeout = parsed
            except (TypeError, ValueError):
                requested_timeout = None
        if (
            requested_timeout is not None
            and effective_timeout != requested_timeout
            and not allow_short_timeout_override()
        ):
            from openjiuwen.core.common.logging import logger as _logger

            _logger.info(
                "BrowserService: clamped browser task timeout from "
                f"{requested_timeout}s to {effective_timeout}s"
            )
        return effective_timeout

    @staticmethod
    def _cancel_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip() or "*"
        return f"playwright_runtime:cancel:{session_id}:{rid}"

    @staticmethod
    def _inflight_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip()
        return f"{session_id}:{rid}" if rid else session_id

    def _register_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            self._inflight_tasks.setdefault(key, set()).add(task)

    def _unregister_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            tasks = self._inflight_tasks.get(key)
            if not tasks:
                continue
            tasks.discard(task)
            if not tasks:
                self._inflight_tasks.pop(key, None)

    def _resolve_mcp_cwd(self) -> Path:
        params = getattr(self.mcp_cfg, "params", {}) or {}
        raw = str(params.get("cwd", "")).strip()
        if raw:
            return Path(raw).expanduser()
        return Path(resolve_playwright_mcp_cwd()).expanduser()

    def _build_managed_profile(self) -> BrowserProfile:
        host = (os.getenv("BROWSER_MANAGED_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        port_raw = (os.getenv("BROWSER_MANAGED_PORT") or "9333").strip()
        try:
            port = int(port_raw)
            if port <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Invalid BROWSER_MANAGED_PORT: {port_raw}") from exc

        user_data_dir = (
            os.getenv("BROWSER_MANAGED_USER_DATA_DIR")
            or str(Path(REPO_ROOT).expanduser() / ".browser-profiles" / self._profile_name)
        ).strip()
        browser_binary = (os.getenv("BROWSER_MANAGED_BINARY") or "").strip()
        extra_args = self._parse_env_args(os.getenv("BROWSER_MANAGED_ARGS") or "")
        cdp_url = f"http://{host}:{port}"
        return BrowserProfile(
            name=self._profile_name,
            driver_type="managed",
            cdp_url=cdp_url,
            browser_binary=browser_binary,
            user_data_dir=user_data_dir,
            debug_port=port,
            host=host,
            extra_args=extra_args,
        )

    def _inject_cdp_endpoint(self, endpoint: str) -> None:
        params = dict(getattr(self.mcp_cfg, "params", {}) or {})
        env_map = dict(params.get("env", {}) or {})
        env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = endpoint
        env_map.setdefault("PLAYWRIGHT_MCP_BROWSER", "chrome")
        env_map.pop("PLAYWRIGHT_MCP_DEVICE", None)
        params["env"] = env_map
        self.mcp_cfg.params = params

    async def _ensure_managed_driver_started(self) -> None:
        if self._driver_mode != "managed":
            return
        if self._managed_driver is not None:
            return

        profile = self._profile_store.get_profile(self._profile_name)
        if (
            profile is None
            or profile.driver_type != "managed"
            or profile.debug_port <= 0
            or not str(profile.user_data_dir).strip()
        ):
            profile = self._build_managed_profile()
        self._profile_store.upsert_profile(profile, select=True)
        self._active_profile = profile

        driver = ManagedBrowserDriver(profile=profile)
        endpoint = await asyncio.to_thread(driver.start, 20.0)
        self._inject_cdp_endpoint(endpoint)
        profile.cdp_url = endpoint
        self._profile_store.upsert_profile(profile, select=True)
        self._managed_driver = driver

    async def _stop_managed_driver(self) -> None:
        if self._managed_driver is None:
            return
        driver = self._managed_driver
        self._managed_driver = None
        await asyncio.to_thread(driver.stop)

    def _ensure_screenshots_dir(self) -> None:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_local_screenshot_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(
                [
                    self._mcp_cwd / path,
                    Path.cwd() / path,
                    path,
                ]
            )

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return path

    def _ensure_screenshot_in_folder(self, source_path: Path) -> Path:
        if not source_path.exists() or not source_path.is_file():
            return source_path

        self._ensure_screenshots_dir()
        try:
            source_resolved = source_path.resolve()
            target_dir_resolved = self._screenshots_dir.resolve()
            try:
                source_resolved.relative_to(target_dir_resolved)
                return source_resolved
            except ValueError:
                pass

            target_path = self._screenshots_dir / source_path.name
            if target_path.exists():
                target_resolved = target_path.resolve()
                if target_resolved != source_resolved:
                    target_path = self._screenshots_dir / (
                        f"{source_path.stem}-{uuid.uuid4().hex[:8]}{source_path.suffix}"
                    )

            shutil.copy2(source_path, target_path)
            return target_path
        except Exception:
            return source_path

    def _normalize_screenshot_value(self, screenshot: Any) -> Any:
        """Normalize screenshot for downstream multimodal APIs.

        - Keep remote URLs and existing data URLs as-is.
        - Ensure local screenshots are copied into screenshots/ folder.
        - Convert local image file paths to data URLs.
        """
        if screenshot is None or not isinstance(screenshot, str):
            return screenshot

        raw = screenshot.strip()
        if not raw:
            return None

        lowered = raw.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            return raw

        local_path_str = raw[7:] if lowered.startswith("file://") else raw
        local_path = self._resolve_local_screenshot_path(local_path_str)
        if not local_path.exists() or not local_path.is_file():
            return raw
        local_path = self._ensure_screenshot_in_folder(local_path)

        mime_type, _ = mimetypes.guess_type(str(local_path))
        if not mime_type or not mime_type.startswith("image/"):
            return raw

        try:
            encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
        except Exception:
            return raw
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _is_retryable_transport_error(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        markers = (
            "session terminated",
            "not connected",
            "endofstream",
            "closedresourceerror",
            "brokenresourceerror",
            "stream closed",
            "connection closed",
            "broken pipe",
            "remoteprotocolerror",
            "readerror",
            "writeerror",
        )
        return any(marker in name or marker in text for marker in markers)

    async def request_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required for cancellation")
        await self._cancel_store.set(self._cancel_key(sid, request_id), "1")

        request_id_clean = (request_id or "").strip()
        if request_id_clean:
            keys = [self._inflight_key(sid, request_id_clean)]
        else:
            keys = [self._inflight_key(sid)]

        for key in keys:
            for task in list(self._inflight_tasks.get(key, set())):
                if not task.done():
                    task.cancel()

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        if request_id:
            await self._cancel_store.delete(self._cancel_key(sid, request_id))
            return
        await self._cancel_store.delete(self._cancel_key(sid, "*"))

    async def is_cancelled(self, session_id: str, request_id: Optional[str] = None) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        if request_id:
            exact = await self._cancel_store.get(self._cancel_key(sid, request_id))
            if exact is not None:
                return True
        wildcard = await self._cancel_store.get(self._cancel_key(sid, "*"))
        return wildcard is not None

    def add_browser_middleware(self, middleware: AgentMiddleware) -> None:
        if self._browser_agent is None:
            self._pending_middlewares.append(middleware)
            return
        self._browser_agent.register_middleware(middleware)

    def add_browser_callback(
        self,
        event: AgentCallbackEvent,
        callback: AnyAgentCallback,
        priority: int = 100,
    ) -> None:
        if self._browser_agent is None:
            self._pending_callbacks.append((event, callback, priority))
            return
        self._browser_agent.register_callback(event, callback, priority=priority)

    def session_new(self, session_id: Optional[str] = None) -> str:
        sid = (session_id or "").strip() or f"browser-{uuid.uuid4().hex}"
        self._sessions.add(sid)
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return sid

    async def ensure_started(self) -> None:
        if self.started:
            return

        if shutil.which("npx") is None:
            raise RuntimeError("npx not found in PATH. Install Node.js first.")

        await self._ensure_managed_driver_started()
        self._ensure_screenshots_dir()
        await Runner.start()

        register_result = await Runner.resource_mgr.add_mcp_server(self.mcp_cfg, tag="browser.service")
        if register_result is not None and not getattr(register_result, "is_ok", lambda: False)():
            error_value = getattr(register_result, "value", register_result)
            if "already exist" not in str(error_value):
                raise RuntimeError(f"Failed to register Playwright MCP server: {error_value}")

        self._browser_agent = build_browser_worker_agent(
            provider=self.provider,
            api_key=self.api_key,
            api_base=self.api_base,
            model_name=self.model_name,
            mcp_cfg=self.mcp_cfg,
            max_steps=self.guardrails.max_steps,
            screenshot_subdir=self._screenshot_subdir,
        )
        self._browser_agent.register_middleware(BrowserCancellationMiddleware(self.is_cancelled))
        for middleware in self._pending_middlewares:
            self._browser_agent.register_middleware(middleware)
        self._pending_middlewares.clear()
        for event, callback, priority in self._pending_callbacks:
            self._browser_agent.register_callback(event, callback, priority=priority)
        self._pending_callbacks.clear()
        self.started = True

    async def _restart(self) -> None:
        """Tear down and reinitialize the browser service (e.g. after stdio subprocess dies)."""
        from openjiuwen.core.common.logging import logger as _logger
        _logger.warning("BrowserService: restarting due to broken MCP connection")
        try:
            server_resource_id = (self.mcp_cfg.server_id or "").strip() or self.mcp_cfg.server_name
            await Runner.resource_mgr.remove_tool_server(
                server_resource_id, ignore_not_exist=True
            )
        except Exception:
            pass
        self.started = False
        self._browser_agent = None
        await self.ensure_started()

    async def _run_task_once(self, task: str, session_id: str, request_id: str) -> Dict[str, Any]:
        if self._browser_agent is None:
            raise RuntimeError("BrowserService is not started")

        task_prompt = (
            f"Session id: {session_id}\n"
            f"Request id: {request_id}\n"
            f"Max steps: {self.guardrails.max_steps}\n"
            f"Max failures: {self.guardrails.max_failures}\n\n"
            f"Task:\n{task}\n\n"
            "Perform the task in the current logical browser session/tab for this session id."
        )
        result = await Runner.run_agent(
            self._browser_agent,
            {"query": task_prompt, "conversation_id": session_id, "request_id": request_id},
        )
        output_text = result.get("output") if isinstance(result, dict) else result
        parsed = extract_json_object(output_text)
        if parsed:
            return parsed
        return {
            "ok": False,
            "final": str(output_text) if output_text is not None else "",
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": "Browser worker did not return valid JSON output",
        }

    async def run_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        sid = self.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex
        effective_timeout = self._resolve_effective_timeout(timeout_s)
        attempts = 2 if self.guardrails.retry_once else 1

        async with self._locks[sid]:
            current_task = asyncio.current_task()
            if current_task is not None:
                self._register_inflight_task(sid, rid, current_task)
            try:
                if await self.is_cancelled(sid, rid):
                    await self.clear_cancel(sid, rid)
                    await self.clear_cancel(sid, None)
                    return {
                        "ok": False,
                        "session_id": sid,
                        "request_id": rid,
                        "final": "",
                        "page": {"url": "", "title": ""},
                        "screenshot": None,
                        "error": "cancelled_by_frontend",
                        "attempt": 0,
                    }
                last_error: Optional[str] = None
                for attempt_idx in range(attempts):
                    try:
                        parsed = await asyncio.wait_for(
                            self._run_task_once(task=task, session_id=sid, request_id=rid),
                            timeout=float(effective_timeout),
                        )
                        page = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                        screenshot = self._normalize_screenshot_value(parsed.get("screenshot"))
                        return {
                            "ok": bool(parsed.get("ok", False)),
                            "session_id": sid,
                            "request_id": rid,
                            "final": str(parsed.get("final", "")),
                            "page": {
                                "url": str(page.get("url", "")),
                                "title": str(page.get("title", "")),
                            },
                            "screenshot": screenshot,
                            "error": parsed.get("error"),
                            "attempt": attempt_idx + 1,
                        }
                    except TimeoutError:
                        last_error = f"task_timeout: exceeded {effective_timeout}s"
                        if attempt_idx + 1 >= attempts:
                            break
                    except asyncio.CancelledError:
                        await self.clear_cancel(sid, rid)
                        await self.clear_cancel(sid, None)
                        return {
                            "ok": False,
                            "session_id": sid,
                            "request_id": rid,
                            "final": "",
                            "page": {"url": "", "title": ""},
                            "screenshot": None,
                            "error": "cancelled_by_frontend",
                            "attempt": attempt_idx + 1,
                        }
                    except BrowserRunCancelled:
                        await self.clear_cancel(sid, rid)
                        await self.clear_cancel(sid, None)
                        return {
                            "ok": False,
                            "session_id": sid,
                            "request_id": rid,
                            "final": "",
                            "page": {"url": "", "title": ""},
                            "screenshot": None,
                            "error": "cancelled_by_frontend",
                            "attempt": attempt_idx + 1,
                        }
                    except Exception as exc:
                        last_error = str(exc) or repr(exc)
                        if attempt_idx + 1 >= attempts:
                            break
                        # Restart before retry on known transport/session failures.
                        if (not str(exc)) or self._is_retryable_transport_error(exc):
                            try:
                                await self._restart()
                            except Exception as restart_exc:
                                last_error = f"restart_failed: {restart_exc!r}"
                                break

                await self.clear_cancel(sid, rid)
                await self.clear_cancel(sid, None)
                return {
                    "ok": False,
                    "session_id": sid,
                    "request_id": rid,
                    "final": "",
                    "page": {"url": "", "title": ""},
                    "screenshot": None,
                    "error": last_error or "unknown browser execution error",
                    "attempt": attempts,
                }
            finally:
                if current_task is not None:
                    self._unregister_inflight_task(sid, rid, current_task)

    async def shutdown(self) -> None:
        try:
            if self.started:
                await Runner.stop()
            self.started = False
        finally:
            await self._stop_managed_driver()
