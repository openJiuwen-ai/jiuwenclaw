# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""管理本地 jiuwenbox uvicorn 子进程 — 由 agent-server 启动链触发."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import signal
import socket
import sys
import time
import shutil
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


_PR_SET_PDEATHSIG = 1


_WIN_PROXY_DEFAULT_PORT_START = 60080
_WIN_PROXY_DEFAULT_PORT_END = 60089


def _probe_win_proxy_ports_free(
    port_start: int = _WIN_PROXY_DEFAULT_PORT_START,
    port_end: int = _WIN_PROXY_DEFAULT_PORT_END,
) -> bool:
    """try-bind 探测 win_proxy 端口范围是否全部可占用."""
    if sys.platform != "win32":
        return True
    for port in range(port_start, port_end + 1):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            probe.close()
            return False
        probe.close()
    return True


def _get_powershell() -> str:
    for candidate in ("pwsh", "powershell", "powershell.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "powershell"


def _cleanup_stale_win_proxy_ports(
    port_start: int = _WIN_PROXY_DEFAULT_PORT_START,
    port_end: int = _WIN_PROXY_DEFAULT_PORT_END,
) -> None:
    """启动新 box-server 前, 清理占用 win_proxy 端口范围的残留进程."""
    if sys.platform != "win32":
        return
    import subprocess
    stale_pids: dict[int, list[int]] = {}  # pid -> ports
    for port in range(port_start, port_end + 1):
        try:
            # Get-NetTCPConnection 查 Listen 状态的端口占用.
            result = subprocess.run(
                [_get_powershell(), "-NoProfile", "-Command",
                 f"Get-NetTCPConnection -LocalPort {port} -State Listen "
                 f"-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
                capture_output=True, timeout=5,
            )
            out = result.stdout.decode("utf-8", errors="replace").strip()
            if not out:
                continue
            for pid_str in out.splitlines():
                pid_str = pid_str.strip()
                if pid_str.isdigit():
                    pid = int(pid_str)
                    stale_pids.setdefault(pid, []).append(port)
        except Exception:  # noqa: BLE001
            continue
    if not stale_pids:
        return
    # 获取进程名, 避免误杀非 python 进程
    for pid, ports in stale_pids.items():
        try:
            name_result = subprocess.run(
                [_get_powershell(), "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
                capture_output=True, timeout=5,
            )
            proc_name = name_result.stdout.decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            proc_name = ""
        if not proc_name.lower().startswith("python"):
            logger.warning(
                "[JiuwenBoxRunner] 端口 %s 被非 python 进程 PID=%d (%s) 占用, 跳过清理",
                ports, pid, proc_name or "<unknown>",
            )
            continue
        try:
            subprocess.run(
                [_get_powershell(), "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
                capture_output=True, timeout=5,
            )
            logger.warning(
                "[JiuwenBoxRunner] 清理占用 win_proxy 端口 %s 的残留进程 PID=%d "
                "(旧 box-server 孤儿, 阻止新 win_proxy bind)",
                ports, pid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[JiuwenBoxRunner] 清理残留进程 PID=%d 失败 (端口 %s): %s",
                pid, ports, exc,
            )


def _resolve_jiuwenbox_src_dir() -> Optional[Path]:
    """探测仓库内 ``code_agent/jiuwenbox/src``; 若存在则供 PYTHONPATH 注入用."""
    here = Path(__file__).resolve()
    for ancestor in here.parents[1:7]:
        candidate = ancestor / "jiuwenbox" / "src" / "jiuwenbox" / "__init__.py"
        if candidate.exists():
            return candidate.parent.parent
    return None


def _resolve_jiuwenbox_configs_dir() -> Optional[Path]:
    """探测 ``jiuwenbox/configs/`` 目录 (policy 模板所在)."""
    here = Path(__file__).resolve()
    for ancestor in here.parents[1:7]:
        for candidate in (
            ancestor / "jiuwenbox" / "src" / "jiuwenbox" / "configs",
            ancestor / "jiuwenbox" / "configs",
        ):
            if candidate.is_dir():
                return candidate
    try:
        import jiuwenbox  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pkg_dir = Path(jiuwenbox.__file__).resolve().parent
    except Exception:  # noqa: BLE001
        return None
    direct = pkg_dir / "configs"
    if direct.is_dir():
        return direct
    return None


def _try_set_pdeathsig() -> None:
    """Linux: 让子进程在父进程退出时收到 SIGTERM, 避免 SIGKILL 父进程时 jiuwenbox 残留.

    通过 ``preexec_fn`` 调用; 在非 Linux 平台是 no-op.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except Exception:  # noqa: BLE001
        pass


class JiuwenBoxRunner:
    """单例形态管理本地 jiuwenbox 子进程."""

    _INSTANCE: "JiuwenBoxRunner | None" = None
    # stderr 滚动缓冲最大行数; 类级常量, 避免实例级 UPPER_CASE 属性。
    _STDERR_TAIL_MAX: int = 80

    def __init__(self) -> None:
        self.process: Optional[asyncio.subprocess.Process] = None
        self.host: str = "127.0.0.1"
        self.port: int = 8321
        self._lock = asyncio.Lock()
        # 标记进程是否由本 runner 启动; 若用户在外部已起服务, 不应在 stop() 时杀掉
        self.owns_process: bool = False
        # 进程退出兜底: atexit 同步钩子, 即便 stop() 没被走到也尝试终止子进程
        self._atexit_registered: bool = False
        # 持续 drain 的后台任务以及 stderr 滚动缓冲, 便于子进程异常退出时反查原因
        self._stdout_pump_task: Optional[asyncio.Task] = None
        self._stderr_pump_task: Optional[asyncio.Task] = None
        self._stderr_tail: list[str] = []
        # 记录最近一次 ensure_running 用到的 startup_mode, 便于诊断 / 日志透出。
        self._last_startup_mode: str = "internal"
        # 上次 spawn 注入的 JIUWENBOX_POLICY_PATH; 下次 ensure_running 发现不一致则停旧重启,
        # 避免老进程用旧 policy 服务新 sandbox.
        self.spawned_policy_path: Optional[Path] = None
        # 上次 spawn 的 policy 内容指纹 (sha256). 网络配置变更改写运行时副本 (path 不变内容变),
        # 须比指纹才检测到 → 触发 stop+spawn 重启 box-server 重建 EgressFilter.
        self._spawned_policy_fingerprint: Optional[str] = None

    @classmethod
    def instance(cls) -> "JiuwenBoxRunner":
        if cls._INSTANCE is None:
            cls._INSTANCE = JiuwenBoxRunner()
        return cls._INSTANCE

    @classmethod
    def resolve_policy_path(cls, filename: str) -> Optional[Path]:
        """把 policy 文件名解析为绝对路径 (供 ``ensure_running(policy_path=...)``)."""
        if not filename:
            return None
        configs_dir = _resolve_jiuwenbox_configs_dir()
        if configs_dir is None:
            return None
        candidate = (configs_dir / filename).resolve()
        return candidate if candidate.is_file() else None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get_stderr_tail(self, lines: int = 40) -> str:
        """返回最近 ``lines`` 行子进程 stderr, 便于错误诊断."""
        if not self._stderr_tail:
            return ""
        return "\n".join(self._stderr_tail[-lines:])

    def is_owned_listener(self, host: str, port: int) -> bool:
        """``True`` 表示当前 runner 持有一个仍在跑的子进程, 且监听在 ``host:port``."""
        proc = self.process
        if proc is None or proc.returncode is not None:
            return False
        if not self.owns_process:
            return False
        return self.host == host and self.port == port

    def get_owned_endpoint(self) -> Optional[tuple[str, int]]:
        """返回当前由本 runner 拥有的 (host, port); 没有就返回 None."""
        proc = self.process
        if proc is None or proc.returncode is not None:
            return None
        if not self.owns_process:
            return None
        return (self.host, self.port)

    @staticmethod
    def _policy_fingerprint(policy_path: Optional[Path]) -> Optional[str]:
        """计算 policy 文件内容指纹 (sha256). 不存在返回 None."""
        if policy_path is None or not policy_path.is_file():
            return None
        try:
            import hashlib
            return hashlib.sha256(policy_path.read_bytes()).hexdigest()
        except OSError:
            return None

    async def health_check(self, host: str | None = None, port: int | None = None) -> bool:
        target_host = host or self.host
        target_port = port or self.port
        url = f"http://{target_host}:{target_port}/health"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_health(self, host: str | None = None, port: int | None = None) -> dict[str, Any] | None:
        """Return parsed jiuwenbox ``/health`` JSON, or ``None`` on failure."""
        target_host = host or self.host
        target_port = port or self.port
        url = f"http://{target_host}:{target_port}/health"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def ensure_running(
        self,
        host: str = "127.0.0.1",
        port: int = 8321,
        *,
        timeout: float = 30.0,
        startup_mode: str = "internal",
        policy_path: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> bool:
        """确保 jiuwenbox 在 ``host:port`` 已就绪。"""
        async with self._lock:
            normalized_mode = (startup_mode or "internal").strip().lower()
            if normalized_mode not in ("internal", "external"):
                normalized_mode = "internal"
            self._last_startup_mode = normalized_mode
            if normalized_mode == "external":
                self.host = host
                self.port = port
                if await self.health_check(host, port):
                    logger.info(
                        "[JiuwenBoxRunner] external jiuwenbox alive at %s:%d "
                        "(policy_path env is user's responsibility, expected=%s)",
                        host,
                        port,
                        policy_path,
                    )
                    return True
                logger.warning(
                    "[JiuwenBoxRunner] startup_mode=external but %s:%d unreachable; "
                    "user is expected to start jiuwenbox-server manually",
                    host,
                    port,
                )
                return False
            new_fp = self._policy_fingerprint(policy_path)
            owned_match = (
                self.process is not None
                and self.process.returncode is None
                and self.owns_process
                and self.host == host
                and self.port == port
                and self.spawned_policy_path == policy_path
                and self._spawned_policy_fingerprint == new_fp
            )
            if owned_match:
                if await self.health_check(host, port):
                    logger.info(
                        "[JiuwenBoxRunner] reuse owned jiuwenbox at %s:%d "
                        "(policy_path=%s)",
                        host,
                        port,
                        policy_path,
                    )
                    return True
                return await self._wait_until_ready(host, port, timeout=timeout)

            if self.process is not None and self.owns_process:
                logger.info(
                    "[JiuwenBoxRunner] stopping owned jiuwenbox before spawning new one "
                    "(prev host=%s port=%d policy=%s -> new host=%s port=%d policy=%s)",
                    self.host,
                    self.port,
                    self.spawned_policy_path,
                    host,
                    port,
                    policy_path,
                )
                await self._stop_no_lock()

            if sys.platform == "win32":
                # 先用 try-bind 探测 (<10ms); 仅当某端口被占用 (即真有孤儿进程) 才
                # 走 _cleanup_stale_win_proxy_ports 的 PowerShell 扫描清理 (~40s).
                if not _probe_win_proxy_ports_free():
                    logger.info(
                        "[JiuwenBoxRunner] win_proxy 端口探测发现占用, 走残留进程清理",
                    )
                    _cleanup_stale_win_proxy_ports()

            self.host = host
            self.port = port

            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "jiuwenbox.server.app:app",
                "--host",
                host,
                "--port",
                str(port),
            ]
            _env_allowlist = {  # noqa: N806 - Win32 常量风格
                "PATH", "PATHEXT", "SystemRoot", "windir", "COMSPEC",
                "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                "HOME", "LANG", "LC_ALL", "LC_CTYPE",
                "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING",
                # HNP Python and native wheels need the parent loader paths.
                "LD_LIBRARY_PATH", "OPENSSL_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
                "JIUWENCLAW_RUNTIME_PLATFORM",
                "JIUWENCLAW_DATA_DIR", "OFFICE_CLAW_DATA_DIR",
            }
            env: dict[str, str] = {}
            for _k in _env_allowlist:
                _v = os.environ.get(_k)
                if _v:
                    env[_k] = _v
            for _k, _v in os.environ.items():
                if _k.startswith("JIUWENBOX_") and _k not in env:
                    env[_k] = _v
            if extra_env:
                env.update({str(k): str(v) for k, v in extra_env.items()})
            local_src = _resolve_jiuwenbox_src_dir()
            if local_src is not None:
                existing = env.get("PYTHONPATH", "")
                parts = [str(local_src)]
                if existing:
                    parts.append(existing)
                env["PYTHONPATH"] = os.pathsep.join(parts)
                logger.info(
                    "[JiuwenBoxRunner] prepending local jiuwenbox src to PYTHONPATH: %s",
                    local_src,
                )
            if policy_path is not None:
                env["JIUWENBOX_POLICY_PATH"] = str(policy_path)
                logger.info(
                    "[JiuwenBoxRunner] injecting JIUWENBOX_POLICY_PATH=%s",
                    policy_path,
                )
            else:
                env.pop("JIUWENBOX_POLICY_PATH", None)

            logger.info("[JiuwenBoxRunner] spawning: %s", " ".join(cmd))
            try:
                spawn_kwargs: dict = {
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.PIPE,
                    "env": env,
                }
                # Linux: 父进程退出时让子进程收到 SIGTERM (PR_SET_PDEATHSIG)
                if sys.platform.startswith("linux"):
                    spawn_kwargs["preexec_fn"] = _try_set_pdeathsig
                if sys.platform == "win32":
                    create_new_process_group = 0x00000200  # noqa: N806 - Win32 常量风格
                    spawn_kwargs["creationflags"] = create_new_process_group
                self.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    **spawn_kwargs,
                )
                self.owns_process = True
                self.spawned_policy_path = policy_path
                self._spawned_policy_fingerprint = new_fp
                # 同步退出兜底
                self._register_atexit_once()
                self._stderr_tail = []
                self._stdout_pump_task = asyncio.create_task(
                    self._pump_stream(self.process.stdout, "stdout")
                )
                self._stderr_pump_task = asyncio.create_task(
                    self._pump_stream(self.process.stderr, "stderr")
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[JiuwenBoxRunner] spawn failed: %s", exc)
                self.process = None
                self.owns_process = False
                self.spawned_policy_path = None
                self._spawned_policy_fingerprint = None
                return False

            ok = await self._wait_until_ready(host, port, timeout=timeout)
            if not ok:
                tail = "\n".join(self._stderr_tail[-40:])
                if self.process is not None and self.process.returncode is not None:
                    logger.error(
                        "[JiuwenBoxRunner] jiuwenbox subprocess exited rc=%s during startup. "
                        "stderr tail:\n%s",
                        self.process.returncode,
                        tail or "(empty; check if uvicorn / jiuwenbox is installed)",
                    )
                else:
                    logger.warning(
                        "[JiuwenBoxRunner] health check timeout after %ss; pid=%s. "
                        "stderr tail:\n%s",
                        timeout,
                        self.process.pid if self.process else None,
                        tail or "(empty)",
                    )
            return ok

    async def _pump_stream(self, stream: Any, kind: str) -> None:  # type: ignore[override]
        """持续读取子进程 stdout/stderr, 写入 logger info; stderr 额外保留滚动尾部."""
        if stream is None:
            return
        try:
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    return
                try:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                except Exception:  # noqa: BLE001
                    line = repr(line_bytes)
                if kind == "stderr":
                    self._stderr_tail.append(line)
                    if len(self._stderr_tail) > self._STDERR_TAIL_MAX:
                        # 保留尾部 N 行
                        del self._stderr_tail[0:len(self._stderr_tail) - self._STDERR_TAIL_MAX]
                logger.info("[jiuwenbox/%s] %s", kind, line)
        except Exception as exc:  # noqa: BLE001
            logger.info("[JiuwenBoxRunner] pump %s stopped: %s", kind, exc)

    async def _wait_until_ready(self, host: str, port: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.returncode is not None:
                logger.warning(
                    "[JiuwenBoxRunner] subprocess exited prematurely (rc=%s)",
                    self.process.returncode,
                )
                return False
            if await self.health_check(host, port):
                logger.info(
                    "[JiuwenBoxRunner] jiuwenbox ready at %s:%d",
                    host,
                    port,
                )
                return True
            await asyncio.sleep(0.1)
        return False

    def _register_atexit_once(self) -> None:
        if self._atexit_registered:
            return
        try:
            atexit.register(self._sync_terminate)
            self._atexit_registered = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[JiuwenBoxRunner] atexit register failed: %s", exc)

    def _sync_terminate(self) -> None:
        """同步退出兜底: ``atexit`` / 异常退出场景调用, 不依赖事件循环."""
        proc = self.process
        if proc is None or not self.owns_process:
            return
        # asyncio.subprocess.Process exposes returncode / pid 同步可读
        if proc.returncode is not None:
            return
        pid = proc.pid
        logger.info("[JiuwenBoxRunner] atexit: terminating subprocess pid=%s", pid)
        if sys.platform == "win32":
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("[JiuwenBoxRunner] atexit terminate failed: %s", exc)
                return
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if proc.returncode is not None:
                    return
                time.sleep(0.1)
            with contextlib.suppress(ProcessLookupError, Exception):
                proc.kill()
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[JiuwenBoxRunner] atexit SIGTERM failed: %s", exc)
            return
        # 等待最多 3s
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                # 0 信号: 探测进程是否存在
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except Exception:  # noqa: BLE001
                return
            time.sleep(0.1)
        # 超时则 SIGKILL
        with contextlib.suppress(ProcessLookupError, Exception):
            os.kill(pid, signal.SIGKILL)

    async def stop(self) -> None:
        """优雅停止由本 runner 启动的子进程."""
        async with self._lock:
            await self._stop_no_lock()

    async def _stop_no_lock(self) -> None:
        """``stop()``  调用方必须已经持有 ``self._lock``."""
        for task_attr in ("_stdout_pump_task", "_stderr_pump_task"):
            task = getattr(self, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            setattr(self, task_attr, None)

        proc = self.process
        if proc is None or proc.returncode is not None:
            self.process = None
            self.spawned_policy_path = None
            self._spawned_policy_fingerprint = None
            return
        if not self.owns_process:
            self.process = None
            self.spawned_policy_path = None
            self._spawned_policy_fingerprint = None
            return
        logger.info("[JiuwenBoxRunner] stopping subprocess pid=%s", proc.pid)
        if sys.platform == "win32":
            _sent_ctrl = False
            try:
                import ctypes as _ct
                kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
                ctrl_break_event = 1  # noqa: N806 - Win32 常量风格
                if kernel32.GenerateConsoleCtrlEvent(ctrl_break_event, proc.pid):
                    _sent_ctrl = True
                    logger.info("[JiuwenBoxRunner] sent CTRL_BREAK to pid=%s (graceful uvicorn shutdown)", proc.pid)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[JiuwenBoxRunner] CTRL_BREAK failed: %s", exc)
            if not _sent_ctrl:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    self.process = None
                    self.spawned_policy_path = None
                    self._spawned_policy_fingerprint = None
                    return
        else:
            try:
                proc.terminate()
            except ProcessLookupError:
                self.process = None
                self.spawned_policy_path = None
                self._spawned_policy_fingerprint = None
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[JiuwenBoxRunner] terminate timeout (60s); killing pid=%s "
                "(sandbox-daemon orphans may remain on host)",
                proc.pid,
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[JiuwenBoxRunner] kill failed: %s", exc)
        self.process = None
        self.owns_process = False
        self.spawned_policy_path = None
        self._spawned_policy_fingerprint = None
