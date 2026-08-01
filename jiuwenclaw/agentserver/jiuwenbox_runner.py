# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""管理本地 jiuwenbox uvicorn 子进程 — 由 agent-server 启动链触发.

移植自 ``jiuwenclaw_bk`` (develop 分支) 的
``jiuwenswarm/server/sandbox/jiuwenbox_runner.py``（方案 B，详见
``docs/windows_sandbox_officeace_integration_design.md`` §1.7 / §4.2）。
自包含：仅依赖 stdlib + httpx，无 jiuwenswarm 自引用。

设计要点:
- 默认地址 ``http://127.0.0.1:8321``; 端口是否空闲由 agent-server 启动链在
  调用 ``ensure_running`` *之前* 决定 (占用就换随机空闲端口, 避免与未知第三方
  进程冲突); 本 runner 不再尝试识别 / 接管端口上的"外部进程".
- ``startup_mode='internal'`` 时: 若 runner 已经在该 host:port 上拥有一个
  与当前 ``policy_path`` 匹配的进程, 直接复用; 否则停掉旧进程并重新 spawn
  ``uvicorn jiuwenbox.server.app:app`` (通过 ``JIUWENBOX_POLICY_PATH`` 注入
  policy 文件路径)。
- ``startup_mode='external'`` 时: 仅做健康检查; 不可达则直接失败, 提示用户
  自行 (含 ``sudo``) 启动 jiuwenbox-server, 配合 ``policy_path`` 传入相应 policy;
- agent-server 进程退出时调用 ``stop()`` 终止子进程, 避免悬挂. Windows 上
  ``stop()`` 用 ``proc.terminate()/proc.kill()`` (跨平台 asyncio API);
  ``_sync_terminate`` 的 atexit 兜底在 Windows 上也走 ``proc.terminate()``
  (不能用 ``os.kill(SIGTERM)`` —— Windows 不识别 SIGTERM, 见 §8.1 Q4).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Linux ``prctl`` PR_SET_PDEATHSIG 选项常量 (来自 ``<linux/prctl.h>``);
# 模块级常量, 避免在 ``_try_set_pdeathsig`` 函数内出现 UPPER_CASE 局部变量。
_PR_SET_PDEATHSIG = 1

# Windows 沙箱出站代理端口范围 (win_proxy 监听). 启动 box-server 前若这些端口
# 被残留的旧 box-server 进程占用 (agent-server 上次被强杀未走 stop → 子进程
# 孤儿, 仍占着端口), 新 box-server 的 win_proxy bind 会上 WinError 10048.
# policy 里端口范围可配, 但 60080-60089 是 windows-policy.yaml 默认值, 这里
# 做清理兜底; 实际范围由调用方 (ensure_running) 按当前 policy 决定, 这里只
# 提供按范围清理的能力.
_WIN_PROXY_DEFAULT_PORT_START = 60080
_WIN_PROXY_DEFAULT_PORT_END = 60089


def _cleanup_stale_win_proxy_ports(
    port_start: int = _WIN_PROXY_DEFAULT_PORT_START,
    port_end: int = _WIN_PROXY_DEFAULT_PORT_END,
) -> None:
    """启动新 box-server 前, 清理占用 win_proxy 端口范围的残留进程.

    agent-server 重启后, self._process 不再持有旧 box-server 引用 → ensure_running
    的 "owned_match" 判定失效, 旧 box-server (孤儿) 不会被 stop, 仍占着
    60080-60089 → 新 box-server win_proxy bind WinError 10048. 这里检测占用
    这些端口的进程, kill 掉 (Windows 用 PowerShell Get-NetTCPConnection).

    仅 Windows internal 模式调用. best-effort: 检测/kill 失败只 warning, 不阻断
    spawn (极端情况端口被非 jiuwenbox 第三方占, 不该误杀).
    """
    if sys.platform != "win32":
        return
    import subprocess
    stale_pids: dict[int, list[int]] = {}  # pid -> ports
    for port in range(port_start, port_end + 1):
        try:
            # Get-NetTCPConnection 查 Listen 状态的端口占用.
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
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
    # 获取进程名, 避免误杀非 python 进程 (win_proxy 只可能由 python 进程占).
    for pid, ports in stale_pids.items():
        try:
            name_result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
                capture_output=True, timeout=5,
            )
            proc_name = name_result.stdout.decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            proc_name = ""
        # P2-20: 旧版严格 == "python" 漏杀 python3.13/pythonw (uv venv 的 python 名).
        # 改 startswith("python") 覆盖 python/python3/python3.13/pythonw 等. 端口范围
        # 已限定 (win_proxy 60080-60089), 误杀系统 python 风险极低 (该端口范围专属).
        if not proc_name.lower().startswith("python"):
            logger.warning(
                "[JiuwenBoxRunner] 端口 %s 被非 python 进程 PID=%d (%s) 占用, 跳过清理",
                ports, pid, proc_name or "<unknown>",
            )
            continue
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
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
    """探测仓库内 ``code_agent/jiuwenbox/src``; 若存在则供 PYTHONPATH 注入用.

    便于无需 ``pip install -e jiuwenbox/`` 也能直接运行本地源码版 jiuwenbox.
    返回 ``None`` 表示未找到 (则依赖 site-packages 中的安装版).
    """
    here = Path(__file__).resolve()
    for ancestor in here.parents[1:7]:
        candidate = ancestor / "jiuwenbox" / "src" / "jiuwenbox" / "__init__.py"
        if candidate.exists():
            return candidate.parent.parent
    return None


def _resolve_jiuwenbox_configs_dir() -> Optional[Path]:
    """探测 ``jiuwenbox/configs/`` 目录 (policy 模板所在).

    优先在仓库目录树里寻找 (开发场景, ``jiuwenbox/src/jiuwenbox/configs``);
    失败再尝试已 ``pip install`` 的 jiuwenbox 包内 ``configs/`` (打包态:
    jiuwenbox 随 jiuwenclaw wheel 装进 site-packages/jiuwenbox/, configs/
    随 package-data 一起带出, 见 §8.1 Q1)。
    返回 ``None`` 表示未找到。
    """
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
        self._process: Optional[asyncio.subprocess.Process] = None
        self._host: str = "127.0.0.1"
        self._port: int = 8321
        self._lock = asyncio.Lock()
        # 标记进程是否由本 runner 启动; 若用户在外部已起服务, 不应在 stop() 时杀掉
        self._owns_process: bool = False
        # 进程退出兜底: atexit 同步钩子, 即便 stop() 没被走到也尝试终止子进程
        self._atexit_registered: bool = False
        # 持续 drain 的后台任务以及 stderr 滚动缓冲, 便于子进程异常退出时反查原因
        self._stdout_pump_task: Optional[asyncio.Task] = None
        self._stderr_pump_task: Optional[asyncio.Task] = None
        self._stderr_tail: list[str] = []
        # 记录最近一次 ensure_running 用到的 startup_mode, 便于诊断 / 日志透出。
        self._last_startup_mode: str = "internal"
        # 记录本 runner 上次 spawn 子进程时实际注入的 ``JIUWENBOX_POLICY_PATH``;
        # 下次 ``ensure_running`` 若发现期望值与之不一致, 必须停掉旧实例重启,
        # 避免老进程继续用旧 policy (例如 default-policy.yaml) 服务新 sandbox。
        self._spawned_policy_path: Optional[Path] = None
        # 记录上次 spawn 时 policy 文件的内容指纹 (sha256). 网络配置变更会改写运行时
        # policy 副本 (path 不变但内容变), 仅比 path 无法检测到 → 必须比内容指纹才会
        # 触发 stop+spawn 重启 box-server (重跑 lifespan 重建 EgressFilter).
        self._spawned_policy_fingerprint: Optional[str] = None

    @classmethod
    def instance(cls) -> "JiuwenBoxRunner":
        if cls._INSTANCE is None:
            cls._INSTANCE = JiuwenBoxRunner()
        return cls._INSTANCE

    @classmethod
    def resolve_policy_path(cls, filename: str) -> Optional[Path]:
        """把 policy 文件名解析为绝对路径 (供 ``ensure_running(policy_path=...)``).

        ``filename`` 通常是 ``default-policy.yaml`` (Linux) 或
        ``windows-policy.yaml`` (Windows)。优先在仓库内
        ``jiuwenbox/src/jiuwenbox/configs/`` 找 (开发态), 失败回落到
        site-packages 里随 wheel 带出的 ``jiuwenbox/configs/`` (打包态)。
        找不到返回 ``None`` —— ``ensure_running`` 接受 ``None``, 由
        jiuwenbox-server 自身回落到内置默认 policy。
        """
        if not filename:
            return None
        configs_dir = _resolve_jiuwenbox_configs_dir()
        if configs_dir is None:
            return None
        candidate = (configs_dir / filename).resolve()
        return candidate if candidate.is_file() else None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def get_stderr_tail(self, lines: int = 40) -> str:
        """返回最近 ``lines`` 行子进程 stderr, 便于错误诊断."""
        if not self._stderr_tail:
            return ""
        return "\n".join(self._stderr_tail[-lines:])

    def is_owned_listener(self, host: str, port: int) -> bool:
        """``True`` 表示当前 runner 持有一个仍在跑的子进程, 且监听在 ``host:port``.

        ``agent_ws_server`` 在分配端口前用它判断"8321 是不是我自己刚才拉起的",
        避免误把自己的进程当成外部占用而无谓换端口。
        """
        proc = self._process
        if proc is None or proc.returncode is not None:
            return False
        if not self._owns_process:
            return False
        return self._host == host and self._port == port

    def get_owned_endpoint(self) -> Optional[tuple[str, int]]:
        """返回当前由本 runner 拥有的 (host, port); 没有就返回 None."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return None
        if not self._owns_process:
            return None
        return (self._host, self._port)

    @staticmethod
    def _policy_fingerprint(policy_path: Optional[Path]) -> Optional[str]:
        """计算 policy 文件内容指纹 (sha256). 不存在返回 None.

        用于检测运行时 policy 副本 path 不变但内容变 (例如网络配置改写副本),
        触发 stop+spawn 重启 box-server. runner 自包含 (只依赖 stdlib), 不耦合
        ``sandbox_policy_render``.
        """
        if policy_path is None or not policy_path.is_file():
            return None
        try:
            import hashlib
            return hashlib.sha256(policy_path.read_bytes()).hexdigest()
        except OSError:
            return None

    async def health_check(self, host: str | None = None, port: int | None = None) -> bool:
        target_host = host or self._host
        target_port = port or self._port
        url = f"http://{target_host}:{target_port}/health"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_health(self, host: str | None = None, port: int | None = None) -> dict[str, Any] | None:
        """Return parsed jiuwenbox ``/health`` JSON, or ``None`` on failure."""
        target_host = host or self._host
        target_port = port or self._port
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
        """确保 jiuwenbox 在 ``host:port`` 已就绪。

        Args:
            host / port: jiuwenbox 监听地址。
            timeout: 健康检查 / 等待就绪的总超时秒数。
            startup_mode: ``internal`` (agent-server 拉起) 或 ``external``
                (用户自己启动 jiuwenbox); ``external`` 模式下本方法不会 spawn 子进程,
                仅做健康检查。
            policy_path: jiuwenbox 启动时使用的 policy 文件路径; 仅在
                ``startup_mode='internal'`` 下生效, 通过 ``JIUWENBOX_POLICY_PATH``
                环境变量传给子进程。
            extra_env: 传给 box-server 子进程的额外 env (P0-5: 收口 env 透传,
                避免全量 dict(os.environ) 把 agent-server 敏感凭据灌进子进程 +
                避免 os.environ 全局污染). 仅 JIUWENBOX_* / PYTHONPATH 等白名单
                键会从父进程继承, 其余敏感 env (token/API key/DB 口令) 不透传.

        Returns:
            True 表示启动 / 已运行并通过健康检查; False 表示超时未就绪。
        """
        async with self._lock:
            normalized_mode = (startup_mode or "internal").strip().lower()
            if normalized_mode not in ("internal", "external"):
                normalized_mode = "internal"
            self._last_startup_mode = normalized_mode

            # external 模式: 只做健康检查, 不 spawn / 不 kill 任何进程。
            # 用户负责保证 jiuwenbox-server 使用合适的 JIUWENBOX_POLICY_PATH 启动。
            if normalized_mode == "external":
                self._host = host
                self._port = port
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

            # internal 模式: agent-server 自己管理 jiuwenbox 生命周期。
            # 不再尝试识别端口上的外部进程; 上游 agent_ws_server 已经保证传进来的
            # ``port`` 是 (a) 我们自己之前拉起的同 host:port 或 (b) 一个空闲端口。
            #
            # 决策矩阵:
            # - 我们拥有的进程仍然 alive 且 host/port/policy_path 全部匹配 → 复用;
            # - policy_path 不变但内容指纹变 (运行时副本被用户改了网络配置) → 也算 mismatch, 重 spawn;
            # - 否则: 停掉旧进程 (如有), 在新的 host:port 上 spawn 全新实例。
            new_fp = self._policy_fingerprint(policy_path)
            owned_match = (
                self._process is not None
                and self._process.returncode is None
                and self._owns_process
                and self._host == host
                and self._port == port
                and self._spawned_policy_path == policy_path
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
                # 进程在跑但还没 ready, 继续等
                return await self._wait_until_ready(host, port, timeout=timeout)

            # 任何 mismatch (端口变了 / policy 变了 / 进程已退) 都先把旧的清掉。
            if self._process is not None and self._owns_process:
                logger.info(
                    "[JiuwenBoxRunner] stopping owned jiuwenbox before spawning new one "
                    "(prev host=%s port=%d policy=%s -> new host=%s port=%d policy=%s)",
                    self._host,
                    self._port,
                    self._spawned_policy_path,
                    host,
                    port,
                    policy_path,
                )
                await self._stop_no_lock()

            # Windows: 启动新 box-server 前清理占用 win_proxy 端口 (60080-60089)
            # 的残留进程. agent-server 重启后 self._process 不持有旧 box-server 引用,
            # 上面的 _stop_no_lock 清不到孤儿进程. 旧 box-server 仍占端口 → 新
            # win_proxy bind WinError 10048. 这里按默认端口范围兜底清理 (若用户
            # 改了 policy 的 proxy 端口范围, 用默认范围清不到, 但 bind 失败日志
            # 会显示具体端口).
            if sys.platform == "win32":
                _cleanup_stale_win_proxy_ports()

            self._host = host
            self._port = port

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
            # 构造 box-server 子进程 env (P0-5 收口: 不全量 dict(os.environ),
            # 避免 agent-server 的敏感凭据 token/API key/DB 口令灌进子进程).
            # 只继承白名单键 (PATH/SystemRoot 等子进程运行必需) + JIUWENBOX_*
            # (调用方经 extra_env 传入的动态路径), 其余敏感 env 不透传.
            _ENV_ALLOWLIST = {
                "PATH", "PATHEXT", "SystemRoot", "windir", "COMSPEC",
                "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                "HOME", "LANG", "LC_ALL", "LC_CTYPE",
                "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING",
                "JIUWENCLAW_DATA_DIR", "OFFICE_CLAW_DATA_DIR",
            }
            env: dict[str, str] = {}
            for _k in _ENV_ALLOWLIST:
                _v = os.environ.get(_k)
                if _v:
                    env[_k] = _v
            # JIUWENBOX_* 前缀键全部继承 (box-server 运行时依赖: policy 路径/
            # venv 目录/runner python/skills 目录 等, 由调用方经 extra_env 注入).
            for _k, _v in os.environ.items():
                if _k.startswith("JIUWENBOX_") and _k not in env:
                    env[_k] = _v
            # extra_env (调用方显式传入) 覆盖, 权限最高.
            if extra_env:
                env.update({str(k): str(v) for k, v in extra_env.items()})
            # 若 jiuwenbox 未安装到 site-packages, 尝试用仓库内源码目录注入 PYTHONPATH
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
            # 把 policy 路径通过环境变量传给 jiuwenbox-server (与 README 中
            # ``JIUWENBOX_POLICY_PATH`` 用法一致, 即 ``server/app.py`` 启动时读取)。
            # 如果调用方没给, 显式删掉父进程继承下来的同名变量, 避免误用旧值。
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
                # P1-14: Windows 加 CREATE_NEW_PROCESS_GROUP, 让 stop() 时能发
                # CTRL_BREAK_EVENT 触发 uvicorn FastAPI lifespan shutdown (清活沙箱),
                # 而非 TerminateProcess 即时强杀 (活沙箱成孤儿).
                if sys.platform == "win32":
                    CREATE_NEW_PROCESS_GROUP = 0x00000200
                    spawn_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    **spawn_kwargs,
                )
                self._owns_process = True
                self._spawned_policy_path = policy_path
                self._spawned_policy_fingerprint = new_fp
                # 同步退出兜底: 即便没走 stop() 也尽可能 terminate 子进程
                self._register_atexit_once()
                # 后台持续 drain stdout/stderr, 防止管道堆积阻塞子进程; 同时
                # 记录滚动 stderr 尾部, 便于失败时反查 uvicorn 的导入/启动错误
                self._stderr_tail = []
                self._stdout_pump_task = asyncio.create_task(
                    self._pump_stream(self._process.stdout, "stdout")
                )
                self._stderr_pump_task = asyncio.create_task(
                    self._pump_stream(self._process.stderr, "stderr")
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[JiuwenBoxRunner] spawn failed: %s", exc)
                self._process = None
                self._owns_process = False
                self._spawned_policy_path = None
                self._spawned_policy_fingerprint = None
                return False

            ok = await self._wait_until_ready(host, port, timeout=timeout)
            if not ok:
                tail = "\n".join(self._stderr_tail[-40:])
                if self._process is not None and self._process.returncode is not None:
                    logger.error(
                        "[JiuwenBoxRunner] jiuwenbox subprocess exited rc=%s during startup. "
                        "stderr tail:\n%s",
                        self._process.returncode,
                        tail or "(empty; check if uvicorn / jiuwenbox is installed)",
                    )
                else:
                    logger.warning(
                        "[JiuwenBoxRunner] health check timeout after %ss; pid=%s. "
                        "stderr tail:\n%s",
                        timeout,
                        self._process.pid if self._process else None,
                        tail or "(empty)",
                    )
            return ok

    async def _pump_stream(self, stream: Any, kind: str) -> None:  # type: ignore[override]
        """持续读取子进程 stdout/stderr, 写入 logger info; stderr 额外保留滚动尾部.

        注意: 这里用 INFO 而非 DEBUG. box-server (uvicorn 子进程) 的所有运行期
        日志——含 Windows 沙箱创建/ACL/spawn runner/runner 经日志长连回传的
        [win-runner] 上报——都经此 pump 转发到 agent_server 日志. 若用 DEBUG,
        在 agent_server 默认 INFO 级别下全部被过滤, 沙箱内部失败无法定位.
        """
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
        # ``asyncio.CancelledError`` 是 ``BaseException`` 子类 (Python 3.8+),
        # 不会被 ``except Exception`` 捕获, 因此无需显式 ``except ... raise``。
        except Exception as exc:  # noqa: BLE001
            logger.info("[JiuwenBoxRunner] pump %s stopped: %s", kind, exc)

    async def _wait_until_ready(self, host: str, port: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.returncode is not None:
                logger.warning(
                    "[JiuwenBoxRunner] subprocess exited prematurely (rc=%s)",
                    self._process.returncode,
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
        """同步退出兜底: ``atexit`` / 异常退出场景调用, 不依赖事件循环.

        - 若 ``stop()`` 已正常清理, 则什么都不做;
        - 否则尽可能 ``terminate`` / ``kill`` 子进程, 避免 jiuwenbox 残留.
        """
        proc = self._process
        if proc is None or not self._owns_process:
            return
        # asyncio.subprocess.Process exposes returncode / pid 同步可读
        if proc.returncode is not None:
            return
        pid = proc.pid
        logger.info("[JiuwenBoxRunner] atexit: terminating subprocess pid=%s", pid)
        if sys.platform == "win32":
            # Windows: SIGTERM 不被识别 (见 docs §8.1 Q4), 用 Process 同步 API。
            # asyncio.subprocess.Process.terminate()/kill() 是同步方法, atexit
            # 上下文可直接调; 等 returncode 最多 3s (atexit 不能 await wait())。
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
        """``stop()`` 的去锁版本; 调用方必须已经持有 ``self._lock``.

        被 ``ensure_running`` 复用: 监测到 policy_path 变更, 需要重启子进程以让
        新的 ``JIUWENBOX_POLICY_PATH`` 生效。
        """
        for task_attr in ("_stdout_pump_task", "_stderr_pump_task"):
            task = getattr(self, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            setattr(self, task_attr, None)

        proc = self._process
        if proc is None or proc.returncode is not None:
            self._process = None
            self._spawned_policy_path = None
            self._spawned_policy_fingerprint = None
            return
        if not self._owns_process:
            self._process = None
            self._spawned_policy_path = None
            self._spawned_policy_fingerprint = None
            return
        logger.info("[JiuwenBoxRunner] stopping subprocess pid=%s", proc.pid)
        # P1-14: Windows 上 proc.terminate()=TerminateProcess 即时强杀, 不给
        # uvicorn lifespan shutdown 机会 → shutdown_all_sandboxes 跑不到, 活沙箱
        # 成孤儿. 改用 CTRL_BREAK_EVENT (spawn 时已加 CREATE_NEW_PROCESS_GROUP):
        # uvicorn 收到 Ctrl+Break 跑 lifespan shutdown (清活沙箱) 后退出. 失败回退 terminate.
        if sys.platform == "win32":
            _sent_ctrl = False
            try:
                import ctypes as _ct
                kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
                CTRL_BREAK_EVENT = 1
                # GenerateConsoleCtrlEvent 给同 process group 的进程发 Ctrl+Break.
                if kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, proc.pid):
                    _sent_ctrl = True
                    logger.info("[JiuwenBoxRunner] sent CTRL_BREAK to pid=%s (graceful uvicorn shutdown)", proc.pid)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[JiuwenBoxRunner] CTRL_BREAK failed: %s", exc)
            if not _sent_ctrl:
                # 没发成功 (如无 console / CreateNewProcessGroup 没生效), 回退 terminate.
                try:
                    proc.terminate()
                except ProcessLookupError:
                    self._process = None
                    self._spawned_policy_path = None
                    self._spawned_policy_fingerprint = None
                    return
        else:
            try:
                proc.terminate()
            except ProcessLookupError:
                self._process = None
                self._spawned_policy_path = None
                self._spawned_policy_fingerprint = None
                return
        # uvicorn 收到 SIGTERM 后会跑 FastAPI lifespan shutdown, 期间会调
        # ``SandboxManager.shutdown_all_sandboxes`` 给每个活的 sandbox 做
        # SIGTERM -> wait -> SIGKILL 三段式 teardown (每个最坏要 ~15s,
        # 见 jiuwenbox.server.runtime.process.SandboxRuntime.stop)。如果这里
        # 留的 grace 不够长, lifespan 还没清完就被 SIGKILL, sandbox-daemon.py
        # 会被 reparent 到 init 成为孤儿进程, 留在 host 上一直跑。所以这里
        # 给一个相对宽松的 60s 上限; 实操中没活 sandbox 时 uvicorn 自己很快
        # 就退了, 不会真等满。
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
        self._process = None
        self._owns_process = False
        self._spawned_policy_path = None
        self._spawned_policy_fingerprint = None
