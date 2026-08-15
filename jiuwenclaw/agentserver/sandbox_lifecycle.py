# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""jiuwenclaw 进程关停时主动回收 jiuwenbox 远端沙箱。

jiuwenbox 服务端**没有** idle TTL (参见
``openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox._JiuwenBoxClient.delete_sandbox``
的源码注释 — "sandbox registry 视为 ephemeral across restarts"), 所以 jiuwenclaw
进程退出后, openjiuwen jiuwenbox provider 进程内缓存的 ``sandbox_id`` 会随
Python 进程一起消失, 但 jiuwenbox 那一头对应的 ``bwrap`` 守护进程仍然活着,
长期累积会让 jiuwenbox 服务端的活跃 sandbox 数线性增长直到 jiuwenbox 本身重启
才会自然 GC。

本模块只暴露一个同步入口 :func:`shutdown_jiuwenbox_sandboxes`, 由
``app_agentserver.py`` 在 ``finally`` 段通过 ``asyncio.to_thread`` 调用, 做的事:

  1. 从 :func:`jiuwenclaw.config.get_sandbox_endpoint` 读 ``base_url`` (即
     ``config.yaml::sandbox.url``)。
  2. 调
     :func:`openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.clear_jiuwenbox_shared_sandbox`
     把**本进程**已知的 ``sandbox_id`` 列表一次性 ``pop`` 出来 (其内部走类锁,
     线程安全)。
  3. 对每个 id 用一个独立、短超时的 ``_JiuwenBoxClient`` 同步发
     ``DELETE /api/v1/sandboxes/{id}``; 单个失败 (网络断、jiuwenbox 已先退出、
     远端已不认这个 id 等) 只打 ``warning``, 继续清下一个。
  4. 永远不向上抛异常, 不阻塞 jiuwenclaw 自身的关停流程。

设计原则是**"软清"**: 只删本 Python 进程缓存里的 sandbox, 不去
``GET /api/v1/sandboxes`` 扫整张表删全部 — 这样多 jiuwenclaw 进程共用同一台
jiuwenbox 时, 不会出现"A 关停顺手把 B 的活跃 sandbox 也回收掉"的串台。

不在本模块兜底处理 ``SIGKILL`` / OOM / kernel panic 这类强杀场景: 那种情况
Python 不会跑任何 finalizer, 唯一根治办法是让 jiuwenbox 服务端实现 idle TTL,
属于"动 jiuwenbox 代码"的范围, 不在本次范围里。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 单条 ``DELETE /api/v1/sandboxes/{id}`` 的超时。
# - 过大: jiuwenbox 卡死时整个 jiuwenclaw 关停会被拖很久 (sandbox 数 ×
#   timeout 是最坏情况)。
# - 过小: 慢盘 / 跨容器场景下正常 DELETE 也会被中途切断, 沙箱泄漏。
# 5s 是经验值: 本机 jiuwenbox DELETE 实测在 200ms 量级, 跨 docker 在 1s 量级;
# 5s 给一倍以上余量。需要调可以再开 env 入口, 暂时常量更稳。
_PER_DELETE_TIMEOUT_SECONDS: float = 5.0


def shutdown_jiuwenbox_sandboxes() -> int:
    """关停时回收本进程缓存里的所有 jiuwenbox sandbox。

    本函数是**同步**入口, 内部 HTTP 调用走 ``httpx.Client``, 调用方应在 worker
    线程里跑 (例如 ``await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)``)
    以免阻塞 event loop 的关停。

    Returns:
        实际 ``DELETE`` 成功 (或 ``404`` 幂等成功) 的 sandbox 个数; 任何环节
        出错都只记 ``warning``, 已经成功删的部分如实返回, 不抛异常。
    """
    try:
        # 局部 import: 避免 jiuwenclaw 启动时就拉 openjiuwen 沙箱 provider 链路
        # (沙箱可能并未启用)。这条 import 只在关停时发生一次, 不影响冷启动。
        from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
            _JiuwenBoxClient,
            clear_jiuwenbox_shared_sandbox,
        )
        from jiuwenclaw.config import get_sandbox_endpoint
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] dependency import failed, skip cleanup: %s",
            exc,
        )
        return 0

    try:
        endpoint = get_sandbox_endpoint()
        base_url = str(endpoint.get("url") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] read sandbox endpoint failed: %s", exc,
        )
        return 0

    if not base_url:
        # sandbox.url 没配, 也就根本不会有 provider 创建过任何远端 sandbox,
        # 缓存必为空。直接早退, 跳过 DELETE 路径。
        logger.debug(
            "[sandbox_lifecycle] sandbox.url is empty, nothing to clean",
        )
        return 0

    try:
        sandbox_ids = list(clear_jiuwenbox_shared_sandbox(base_url))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] clear shared cache failed (base_url=%s): %s",
            base_url, exc,
        )
        return 0

    if not sandbox_ids:
        logger.debug(
            "[sandbox_lifecycle] no cached sandboxes for %s", base_url,
        )
        return 0

    try:
        # 共用一个 client 比每个 sandbox 新建一个更轻量 (复用底层 httpx 连接池),
        # 同时所有 DELETE 共享同一个超时配置。
        client = _JiuwenBoxClient(
            base_url,
            timeout_seconds=_PER_DELETE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] build _JiuwenBoxClient failed (base_url=%s): %s",
            base_url, exc,
        )
        return 0

    released = 0
    try:
        for sandbox_id in sandbox_ids:
            if not sandbox_id:
                continue
            try:
                client.delete_sandbox(sandbox_id)
                released += 1
                logger.info(
                    "[sandbox_lifecycle] DELETE jiuwenbox sandbox ok: %s",
                    sandbox_id,
                )
            except Exception as exc:  # noqa: BLE001
                # 404 已被 ``_JiuwenBoxClient.delete_sandbox`` 内部吞为幂等成功;
                # 进到这里的多半是网络断 / 5xx / 超时。jiuwenbox 重启时这些 id
                # 自然消失, 不会无限累积。
                logger.warning(
                    "[sandbox_lifecycle] DELETE jiuwenbox sandbox %s failed: %s",
                    sandbox_id, exc,
                )
    finally:
        client.close()

    logger.info(
        "[sandbox_lifecycle] released %d/%d jiuwenbox sandbox(es) for %s",
        released, len(sandbox_ids), base_url,
    )
    return released


async def recreate_all_sandboxes() -> int:
    """销毁本进程已知的所有 jiuwenbox 沙箱, 让下次 exec 按需 lazy 建新沙箱."""
    try:
        return await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] recreate_all_sandboxes failed: %s", exc,
        )
        return 0


# ---------------------------------------------------------------------------
# 内部 box-server 启动 (internal 模式), 由 _bootstrap_internal_jiuwenbox 和
# _apply_sandbox_change 共用. 调用方需确保已通过 sandbox.enabled 和
# startup_mode 门控.
# ---------------------------------------------------------------------------


def _is_std_cpython(python_exe: str) -> bool:
    """判断 python.exe 是否标准 CPython 安装 (非 venv trampoline/launcher)."""
    p = Path(python_exe)
    try:
        if not p.is_file():
            return False
    except OSError:
        return False
    parent = p.parent
    if parent.name.lower() == "scripts":
        return False
    has_dll = any(parent.glob("python3*.dll"))
    return has_dll


async def start_box_server_internal() -> bool:
    """根据 config.yaml 当前 sandbox 配置启动 box-server (internal 模式).

    包含: 端点解析, policy 路径解析, 端口分配, 环境注入, 子进程拉起.
    调用方需确保已通过 sandbox.enabled 和 startup_mode 门控.

    Returns:
        True if box-server is/was started successfully, False otherwise.
    """
    from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner
    from jiuwenclaw.config import (
        DEFAULT_SANDBOX_POLICY_FILE,
        get_sandbox_endpoint,
        resolve_sandbox_policy_path,
        update_sandbox_endpoint,
    )

    try:
        endpoint = get_sandbox_endpoint()
        url = endpoint.get("url") or "http://127.0.0.1:8321"
        sandbox_type = endpoint.get("type") or "jiuwenbox"
        raw_policy = endpoint.get("policy_file") or ""
        effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
        policy_path = resolve_sandbox_policy_path(effective_policy_file)
        if policy_path is None or not policy_path.is_file():
            logger.warning(
                "[sandbox_lifecycle] box-server start skipped: "
                "policy_file=%r 无法解析到存在的文件 (resolved=%s).",
                effective_policy_file, policy_path,
            )
            return False

        if sys.platform == "win32":
            try:
                from jiuwenclaw.agentserver.sandbox_policy_render import (
                    _ensure_copy_exists,
                )
                runtime_policy = _ensure_copy_exists()
                if runtime_policy is not None and runtime_policy.is_file():
                    policy_path = runtime_policy
                    logger.info(
                        "[sandbox_lifecycle] using runtime policy copy: %s "
                        "(box-server merges base + copy)",
                        policy_path,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[sandbox_lifecycle] ensure runtime copy failed, "
                    "fall back to base policy: %s",
                    exc,
                )

        from jiuwenclaw.agentserver.sandbox.port_util import (
            allocate_internal_jiuwenbox_port,
            parse_sandbox_host_port,
        )
        host, preferred_port = parse_sandbox_host_port(url)
        port = allocate_internal_jiuwenbox_port(host, preferred_port)
        if port != preferred_port:
            url = f"http://{host}:{port}"
            logger.info(
                "[sandbox_lifecycle] jiuwenbox auto-start: "
                "preferred port %d busy, using %d",
                preferred_port, port,
            )

        sandbox_env: dict[str, str] = {}
        try:
            from jiuwenclaw.runtime.pip_env import (
                ensure_runtime_venv,
                resolve_base_python,
            )
            venv_dir = ensure_runtime_venv()
            sandbox_env["JIUWENBOX_VENV_DIR"] = str(venv_dir)
            bundled_python = resolve_base_python()
            sandbox_env["JIUWENBOX_BUNDLED_PYTHON"] = str(bundled_python.parent)
            if not (sandbox_env.get("JIUWENBOX_RUNNER_PYTHON")
                    or os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip():
                logger.info("[sandbox_lifecycle] JIUWENBOX_RUNNER_PYTHON 未注入探测候选路径...")
                import glob as _glob
                import shutil as _shutil
                _runner_py: str | None = None
                _candidates: list[str] = []
                # 1. 打包
                _candidates.append(
                    str(Path(__file__).resolve().parents[2] / "tools" / "python" / "python.exe"))
                # 2. C:\Python3* (系统安装, 逐版本 glob 覆盖 3.10-3.13+)
                _candidates += sorted(_glob.glob(r"C:\Python3*\python.exe"))
                # 3. %LOCALAPPDATA%\Programs\Python\Python3* (用户级安装)
                _lad = os.environ.get("LOCALAPPDATA", "")
                if _lad:
                    _candidates += sorted(_glob.glob(
                        str(Path(_lad) / "Programs" / "Python" / "Python3*" / "python.exe")))
                for _cand in _candidates:
                    if _cand and Path(_cand).is_file() and _is_std_cpython(_cand):
                        _runner_py = _cand
                        break
                # 4. PATH 里的 python.exe (校验非 venv)
                if not _runner_py:
                    _which = _shutil.which("python") or _shutil.which("python3")
                    if _which and _is_std_cpython(_which):
                        _runner_py = _which
                if _runner_py:
                    sandbox_env["JIUWENBOX_RUNNER_PYTHON"] = _runner_py
            logger.info(
                "[sandbox_lifecycle] injected env: "
                "JIUWENBOX_VENV_DIR=%s, JIUWENBOX_BUNDLED_PYTHON=%s, "
                "JIUWENBOX_RUNNER_PYTHON=%s",
                venv_dir, bundled_python.parent,
                sandbox_env.get("JIUWENBOX_RUNNER_PYTHON") or "<未注入>",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[sandbox_lifecycle] inject JIUWENBOX_BUNDLED_PYTHON/VENV_DIR failed: %s",
                exc,
            )

        logger.info(
            "[sandbox_lifecycle] spawning box-server (startup_mode=internal)..."
        )
        runner = JiuwenBoxRunner.instance()
        ok = await runner.ensure_running(
            host=host,
            port=port,
            startup_mode="internal",
            policy_path=policy_path,
            extra_env=sandbox_env or None,
            timeout=120.0,
        )
        if not ok:
            stderr_tail = runner.get_stderr_tail(20)
            hint = "\n--- jiuwenbox stderr (tail) ---\n" + stderr_tail if stderr_tail else ""
            logger.warning(
                "[sandbox_lifecycle] box-server start failed at %s:%d "
                "(policy=%s).%s",
                host, port, policy_path, hint,
            )
            return False

        actual_url = runner.base_url
        if actual_url and actual_url != endpoint.get("url"):
            try:
                update_sandbox_endpoint(
                    actual_url, sandbox_type,
                    startup_mode="internal",
                    policy_file=effective_policy_file,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[sandbox_lifecycle] persist sandbox endpoint failed "
                    "after auto-start: %s", exc,
                )
        logger.info(
            "[sandbox_lifecycle] box-server ready at %s, "
            "sandbox_id 按需 lazy 创建",
            actual_url,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sandbox_lifecycle] box-server start failed: %s", exc,
        )
        return False


__all__ = ["shutdown_jiuwenbox_sandboxes", "recreate_all_sandboxes", "start_box_server_internal"]