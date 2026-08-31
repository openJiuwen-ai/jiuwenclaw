# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""桌面集成形态的密钥引导：spawn 后由主进程经 stdin 下发密钥包（首帧）。

背景（docs/named-pipe-migration-design.md §2 支柱 1，claw_desktop 仓）：
环境变量与命令行对同用户进程可读（PEB 枚举/WMI），是本地密钥最薄弱的落点。
目标态下所有秘密（proxyKey / localAuth ak/sk / E2A token 等）只经 stdin 首帧
（匿名管道 + 句柄继承，第三方进程无法连接/枚举）下发，且只进内存 vault——
不回注 os.environ、不落盘、不进日志。

契约（与 claw_desktop 侧 src/core/runtime/secrets-frame.ts 同一规范）：
    stdin 首帧 = 4 字节小端长度前缀 + UTF-8 JSON：
    {"type":"secrets","version":1,"secrets":{...}}

注意：读取使用进程内唯一的 stdin 二进制 reader（stdin_binary_stream 缓存）——
E2A stdio 通道（agent_ws_server 的 stdio 传输实现）必须复用同一 reader，
BufferedReader 的预读字节才不会丢失。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import IO, Any

from jiuwenswarm.common.np_transport import FRAME_MAX_BYTES, FrameCodecError

# 密钥包内存 vault（get_secret 读取；不回注 os.environ）
_SECRETS: dict[str, Any] = {}
_LOADED = False

_STDIN_BIN: IO[bytes] | None = None


def stdin_binary_stream() -> IO[bytes]:
    """进程内唯一的 stdin 二进制 reader（BufferedReader 预读不丢字节的关键）。"""
    global _STDIN_BIN
    if _STDIN_BIN is not None:
        return _STDIN_BIN
    stream = sys.stdin
    if stream is not None and hasattr(stream, "buffer"):
        _STDIN_BIN = stream.buffer  # type: ignore[assignment]
        return _STDIN_BIN
    # frozen console=False exe：sys.stdin 可能为 None，但 fd 0 在父进程 pipe 时是有效的
    _STDIN_BIN = os.fdopen(0, "rb", closefd=False)
    return _STDIN_BIN


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise FrameCodecError(f"stdin 提前关闭（缺 {remaining}/{size} 字节）")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _read_secrets_frame() -> dict[str, Any]:
    """同步读 stdin 首帧并校验为密钥包（异常抛给调用方）。"""
    stream = stdin_binary_stream()
    header = _read_exact(stream, 4)
    length = int.from_bytes(header, "little")
    if length == 0 or length > FRAME_MAX_BYTES:
        raise FrameCodecError(f"密钥包帧长度非法: {length}")
    body = _read_exact(stream, length)
    frame = json.loads(body.decode("utf-8"))
    if not isinstance(frame, dict) or frame.get("type") != "secrets" or not isinstance(
        frame.get("secrets"), dict
    ):
        raise FrameCodecError("stdin 首帧不是密钥包（type != 'secrets'）")
    return frame["secrets"]


def bootstrap_secrets_from_stdin(timeout: float = 15.0) -> dict[str, Any]:
    """从 stdin 读取密钥包首帧（启动早期、任何业务初始化之前调用）。

    读取失败/超时返回空 dict——非桌面形态（无 --desktop-secrets-stdin）不调用本函数；
    调用方据此与 env 回退形态兼容。超时用守护线程实现（Windows 上阻塞读 stdin
    无法设超时；线程随进程退出回收）。
    """
    global _SECRETS, _LOADED
    if _LOADED:
        return _SECRETS
    _LOADED = True

    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result.update(_read_secrets_frame())
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=_worker, name="secrets-bootstrap", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        _log_stderr("密钥包读取超时（stdin 无输入），回退 env 兼容形态")
        return {}
    if error:
        _log_stderr(f"密钥包读取失败（回退 env 兼容形态）: {type(error[0]).__name__}: {error[0]}")
        return {}
    _SECRETS = result
    _log_stderr(f"密钥包已接收（{len(result)} 个键；值不落日志）")
    return _SECRETS


def secrets_loaded() -> bool:
    """密钥包是否已成功接收（False = env 兼容形态）。"""
    return bool(_SECRETS)


def get_secret(path: str, default: Any = None) -> Any:
    """从密钥包取值，支持点路径（如 'localAuth.sk'）。缺失返回 default。"""
    node: Any = _SECRETS
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _log_stderr(message: str) -> None:
    try:
        sys.stderr.write(f"[secrets-bootstrap] {message}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
