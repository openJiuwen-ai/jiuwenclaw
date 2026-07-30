# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 两跳启动 (broker -> runner -> 沙箱子进程).

对齐 docs/window沙箱.md 6.5:
  第一跳 (broker, 运行在 box-server 进程):
    CreateProcessWithLogonW("jbx-sandbox", password, runner)
    以沙箱用户身份启动 runner, token 未受限.
  第二跳 (runner, 已在 jbx-sandbox 上下文):
    OpenProcessToken(GetCurrentProcess()) -> GetTokenInformation(TokenGroups)
    CreateWellKnownSid(WinWorldSid) -> 合成 JHXSandboxWrite SID
    CreateRestrictedToken(WRITE_RESTRICTED, restricting=[Everyone, Logon, JHXSandboxWrite])
    CreateProcessAsUserW(restricted_token, child_command)

两跳的必要性: CreateProcessAsUserW 有 SeAssignPrimaryTokenPrivilege 权限墙,
把 Restricted Token 创建放在 runner 自己的上下文中可绕过跨用户边界限制.

box-server 与 runner 之间通过继承的 stdin/stdout anonymous pipe 通信,
帧协议复用 ``daemon_ipc`` 的长度前缀格式 (平台无关). runner 作为长寿进程
监听 pipe 上的 exec / file-op / shutdown 请求.

所有 win32 调用通过 ctypes 延迟加载, 模块顶层只定义结构体和常量.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import sys
from ctypes import wintypes

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const
from jiuwenbox.supervisor.daemon_ipc import (
    LOG_FIELD_LEVEL,
    LOG_FIELD_MESSAGE,
    LOG_FIELD_TIMESTAMP,
    LOG_FIELD_TRACEBACK,
    LOG_FRAME_TYPE,
    MAX_FILE_BYTES,
    MAX_HEADER_BYTES,
    MAX_STDIN_BYTES,
    MAX_STDOUT_BYTES,
    REQUEST_TYPE_SUBSCRIBE_LOG,
    recv_frame,
    send_frame,
)

configure_logging()
logger = logging.getLogger(__name__)

# runner 脚本路径 (本模块的 runner 入口函数以 `python -m` 形式启动).
RUNNER_MODULE = "jiuwenbox.supervisor.win_exec"
RUNNER_SUBCOMMAND = "runner"

# box-server 与 runner 的 TCP loopback 控制端口 (env 注入).
# box-server 分配空闲端口, env 传给 runner, runner bind 同端口做 server,
# box-server 每次 exec connect. 对齐 Linux AF_UNIX listener 模型
# (Windows 不能传 fd, 改传端口号).
LISTENER_PORT_ENV = "JIUWENBOX_CONTROL_LISTENER_PORT"

# 日志订阅连接集合 (runner 端). box-server 创建 sandbox 后会 connect control_port
# 发 subscribe_log 握手帧, runner 把该连接存入此集合并保持, 之后任何阶段往里 push
# log 帧. runner 在 jbx-sandbox 受限 token 下, CREATE_NO_WINDOW 导致 stderr 无落盘,
# 早期异常 (尤其 _create_restricted_token 失败) 静默退出时, 靠这条长连把异常发回
# box-server 由主进程打印. 线程安全: 多个 exec worker 线程会并发 _push_log.
import threading as _threading  # noqa: E402

_log_subscribers: list = []  # list[socket.socket]
_log_sub_lock = _threading.Lock()

# runner 启动时记下的代理端口范围, 供 _create_process_as_user 自动注入
# HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 子进程环境 (对齐文档 §6.6). runner_main 解析
# args 后写入. box-server 起 win_proxy 监听 127.0.0.1:<port_range>, 沙箱子进程
# 的出网流量经 WFP 只能到这些端口, env 让遵守代理协议的程序自动走代理.
_proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START
_proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END


def _push_log(level: str, msg: str, exc: str | None = None) -> None:
    """往所有日志订阅连接 push 一个 log 帧 (best-effort, 不抛错).

    runner 任何阶段都可调: 启动/受限 token 创建失败/bind 失败/exec 子命令结果/
    退出前. 单个订阅连接发送失败则移除该连接 (订阅方已断开), 不影响主流程.
    本地也 logger 一份 (CREATE_NO_WINDOW 下虽不落盘, 但若有 console 仍可见).
    """
    import json as _json
    import time as _time
    payload = {
        "v": 1,
        "type": LOG_FRAME_TYPE,
        LOG_FIELD_LEVEL: level,
        LOG_FIELD_MESSAGE: msg,
        LOG_FIELD_TIMESTAMP: _time.time(),
    }
    if exc:
        payload[LOG_FIELD_TRACEBACK] = exc
    blob = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    dead: list = []
    with _log_sub_lock:
        subs = list(_log_subscribers)
    for sock in subs:
        try:
            send_frame(sock, blob)
        except (OSError, ConnectionError, ValueError):
            dead.append(sock)
    if dead:
        with _log_sub_lock:
            for sock in dead:
                if sock in _log_subscribers:
                    _log_subscribers.remove(sock)
    # 本地也记一份 (与 push 同时, 方便有 console 的场景).
    try:
        if level == "ERROR":
            logger.error("[runner] %s", msg)
        elif level == "WARNING":
            logger.warning("[runner] %s", msg)
        else:
            logger.info("[runner] %s", msg)
    except Exception:  # noqa: BLE001
        pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            f"win_exec 仅在 Windows 平台可用; 当前平台 {sys.platform!r}"
        )


# ---------------------------------------------------------------------------
# advapi32 / kernel32 函数签名.
# ---------------------------------------------------------------------------
_advapi32: ctypes.WinDLL | None = None
_kernel32: ctypes.WinDLL | None = None


class STARTUPINFOEX(ctypes.Structure):
    pass  # 前向声明


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    """SID_AND_ATTRIBUTES (Win32): {PVOID Sid; DWORD Attributes}.

    CreateRestrictedToken 的 restricting sids 参数是 PSID_AND_ATTRIBUTES
    (指向此结构数组的指针), 非 PVOID 数组. 旧版用 c_void_p*3 传给 c_void_p
    参数, ctypes marshal 错指针 → WinError 998. 模块级定义供 argtypes 引用.
    """
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_GROUPS(ctypes.Structure):
    """TOKEN_GROUPS 变长结构, 仅取第一个组用于解析 (我们只需 logon session SID).

    Groups 字段用 _SID_AND_ATTRIBUTES 数组 (而非 c_byte*0): SID_AND_ATTRIBUTES
    在 64 位对齐 8, ctypes 自动给 GroupCount 后加 4 字节 padding → Groups.offset=8,
    与 Win32 TOKEN_GROUPS 实际布局一致. 旧版 c_byte*0 对齐 1 → offset=4 → 读错位.
    """
    _fields_ = [
        ("GroupCount", wintypes.DWORD),
        ("Groups", _SID_AND_ATTRIBUTES * 1),  # 变长, 实际按 count 重新 cast
    ]


def _get_advapi32() -> ctypes.WinDLL:
    global _advapi32
    if _advapi32 is None:
        _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        _advapi32.CreateProcessWithLogonW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
            wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        _advapi32.CreateProcessWithLogonW.restype = wintypes.BOOL
        _advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        _advapi32.OpenProcessToken.restype = wintypes.BOOL
        _advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.GetTokenInformation.restype = wintypes.BOOL
        _advapi32.CreateRestrictedToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_void_p,  # disabling sids
            wintypes.DWORD, ctypes.c_void_p,  # deleting privileges
            wintypes.DWORD, ctypes.POINTER(_SID_AND_ATTRIBUTES),  # restricting sids
            ctypes.POINTER(wintypes.HANDLE),
        ]
        _advapi32.CreateRestrictedToken.restype = wintypes.BOOL
        _advapi32.CreateProcessAsUserW.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
            ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
        _advapi32.LookupAccountNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.LookupAccountNameW.restype = wintypes.BOOL
        _advapi32.AllocateAndInitializeSid.argtypes = [
            ctypes.c_void_p, wintypes.BYTE,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _advapi32.AllocateAndInitializeSid.restype = wintypes.BOOL
        _advapi32.CreateWellKnownSid.argtypes = [
            wintypes.DWORD, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    return _advapi32


def _get_kernel32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        _kernel32.CreatePipe.restype = wintypes.BOOL
        _kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        _kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
        ]
        _kernel32.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32.TerminateProcess.argtypes = [
            wintypes.HANDLE, wintypes.UINT,
        ]
        _kernel32.TerminateProcess.restype = wintypes.BOOL
        # SetHandleInformation: 关闭 box-server 持有端的继承, 防 runner/child
        # 拿到多余 pipe 句柄 (对标 Linux close_fds=True 隔离).
        _kernel32.SetHandleInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ]
        _kernel32.SetHandleInformation.restype = wintypes.BOOL
    return _kernel32


# HANDLE_FLAG_INHERIT = 0x1, HANDLE_FLAG_PROTECT_FROM_CLOSE = 0x2.
HANDLE_FLAG_INHERIT = 0x1


def _clear_inherit(handle: int) -> None:
    """关闭 handle 的继承位 (box-server 持有端, 不让 runner 拿到副本)."""
    kernel32 = _get_kernel32()
    kernel32.SetHandleInformation(
        wintypes.HANDLE(handle), HANDLE_FLAG_INHERIT, 0,
    )


# ---------------------------------------------------------------------------
# broker 侧: 第一跳.
# ---------------------------------------------------------------------------
def _build_env_block(env: dict[str, str]) -> ctypes.c_wchar_p:
    """构造 CreateProcessWithLogonW 的 lpEnvironment (UTF-16 环境块).

    格式: 一串 "KEY=VALUE\\0" 序列, 末尾额外一个 "\\0" (双 null 终止).
    CreateProcessWithLogonW 要求 UNICODE (UTF-16) 环境块.
    """
    parts = [f"{k}={v}" for k, v in env.items()]
    block = "\0".join(parts) + "\0\0"
    return ctypes.create_unicode_buffer(block)


def _build_runner_command(
    sandbox_id: str,
    workspace: str,
    proxy_port_start: int,
    proxy_port_end: int,
    control_port: int,
) -> str:
    """构造 runner 命令行 (CreateProcessWithLogonW 的 lpCommandLine).

    用 ``python -m jiuwenbox.supervisor.win_exec runner --sandbox-id ...``.
    control_port 经命令行参数传 (而非 env), 避开 CreateProcessWithLogonW
    传 env 块时 WinError 87 (ctypes 传 c_wchar_Array 给 c_void_p 参数报错).

    runner python 用标准 CPython (非 uv trampoline/venv launcher):
    jbx-sandbox 对 uv 缓存/AppData 无读权限, 任何 uv 体系 venv python (`.venv`
    / isolation_venv / uv 全局) 第一跳 CreateProcessWithLogonW 都报 WinError 5
    或 trampoline spawn child permission denied. 必须用自包含的标准 CPython
    (jbx-sandbox grant RX 到安装目录即可跑).

    优先级: ``JIUWENBOX_RUNNER_PYTHON`` env (显式指定) > 默认系统 python 路径.
    dev 实测设 ``JIUWENBOX_RUNNER_PYTHON`` 指向系统 CPython 安装;
    打包环境设 tools/python/python.exe. 系统 python 需先装 jiuwenbox_dev.pth
    指向源码 (否则 ``-m jiuwenbox...`` 找不到) + pip install uvicorn
    (logging_config 触发).
    """
    py = (os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip()
    if not py or not os.path.isfile(py):
        py = sys.executable or "python"
    parts = [
        py,
        "-m", RUNNER_MODULE,
        RUNNER_SUBCOMMAND,
        "--sandbox-id", sandbox_id,
        "--workspace", workspace,
        "--proxy-port-start", str(proxy_port_start),
        "--proxy-port-end", str(proxy_port_end),
        "--control-port", str(control_port),
    ]
    # Windows 命令行需要引号包裹含空格的参数.
    quoted = []
    for p in parts:
        if " " in p or "\t" in p:
            quoted.append(f'"{p}"')
        else:
            quoted.append(p)
    return " ".join(quoted)


def two_hop_spawn(
    sandbox_id: str,
    *,
    sandbox_user: str,
    sandbox_password: str,
    workspace: str,
    proxy_port_start: int,
    proxy_port_end: int,
    control_port: int,
    env: dict[str, str] | None = None,
) -> "tuple[int, int, int]":
    """第一跳: 以 jbx-sandbox 身份启动 runner (CREATE_SUSPENDED).

    Runner 以挂起状态启动, 调用方在 AssignProcessToJobObject 之后调用
    win_job.resume_process(thread_handle) 恢复执行 (review MAJOR #1:
    设计 6.8 要求 SUSPEND→Assign→Resume, 否则 Job 逃逸窗口).

    Returns:
        (runner_pid, runner_process_handle, runner_thread_handle):
            runner_process_handle 用于停止/等待 runner. runner_thread_handle
            用于 Job assign 后 ResumeThread; assign+resume 完成后由调用方
            CloseHandle.
    """
    _require_windows()
    advapi32 = _get_advapi32()
    kernel32 = _get_kernel32()

    cmd = _build_runner_command(
        sandbox_id, workspace, proxy_port_start, proxy_port_end, control_port,
    )

    # runner 不再用 stdin/stdout pipe (改 TCP loopback). control_port 走命令行参数
    # (避 CreateProcessWithLogonW 传 env 块的 WinError 87). STARTUPINFO 用默认,
    # 不设 STARTF_USESTDHANDLES, runner 的 stdin/stdout 是空/inherited.
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = 0
    pi = PROCESS_INFORMATION()

    # LOGON_WITH_PROFILE = 0x1, LOGON_NETCREDENTIALS_ONLY = 0x2
    #
    # 为什么必须保留 LOGON_WITH_PROFILE (而非去掉它避免建 C:\Users\jbx-sandbox):
    # CreateProcessWithLogonW 的 dwLogonFlags 不能为 0 (传 0 直接 WinError 87),
    # 只有两个合法取值:
    #   LOGON_WITH_PROFILE     — 以 jbx-sandbox 身份登录 + 加载 profile (建目录).
    #   LOGON_NETCREDENTIALS_ONLY — 不加载 profile 不建目录, 但进程用调用方
    #                              (box-server) 凭据跑, 破坏 jbx-sandbox 隔离前提.
    # runner 必须以 jbx-sandbox 身份跑 (合成 SID ACL / logon session SID 都依赖
    # 此身份), 故只能选 LOGON_WITH_PROFILE. profile 加载是其代价.
    #
    # 本项目对 profile 目录的态度: "接口保留, 运行期不再主动触发额外 profile 创建".
    #   - 主源 (本处): 登录链路必需, profile 首次登录后复用, 保留不动.
    #   - 副源 (win_setup._verify_or_reset_sandbox_user_password): 原用
    #     LogonUserW(INTERACTIVE) 每次校验密码也会加载 profile, 已改为 NETWORK
    #     不加载 profile, 消除高频副源.
    #   - 清理接口 (_purge_stale_profile_dirs / DeleteProfileW): 保留, uninstall
    #     及 reinstall 时清理 jbx-sandbox* 残留目录, 不在运行期调用.
    LOGON_WITH_PROFILE = 0x00000001
    # CREATE_SUSPENDED: runner 主线程挂起, 调用方 assign Job 后再 ResumeThread,
    # 避免 Job 逃逸窗口 (设计 6.8 SUSPEND→Assign→Resume).
    # CREATE_UNICODE_ENVIRONMENT: 传 Unicode env 块时必须带, 否则 WinError 87.
    creation_flags = const.CREATE_NO_WINDOW | const.CREATE_SUSPENDED
    # 传 env 块给 runner 进程: box-server 把 tool_paths 目录拼进 PATH 后,
    # runner 必须继承这个 PATH (否则 runner 的 os.environ 不含工具目录 →
    # 起 child 时 env=None 回退到 os.environ 也无 PATH → WinError 2).
    # 旧版 env=None "用调用方环境" 会丢掉调用方拼的 PATH_prefix.
    # env_block_buf 必须存活到 CreateProcessWithLogonW 返回 (悬垂指针防护,
    # 同 _create_process_as_user 的 env_block_buf 处理).
    env_block_buf = None
    env_block_ptr = None
    if env:
        env_block_buf = _build_env_block(env)
        env_block_ptr = ctypes.cast(env_block_buf, ctypes.c_void_p)
        creation_flags |= const.CREATE_UNICODE_ENVIRONMENT

    ok = advapi32.CreateProcessWithLogonW(
        sandbox_user, None, sandbox_password,
        LOGON_WITH_PROFILE,
        None, cmd,
        creation_flags,
        env_block_ptr,  # env 块; NULL 则用调用方环境
        None, ctypes.byref(startup), ctypes.byref(pi),
    )
    if not ok:
        err = ctypes.WinError(ctypes.get_last_error())
        raise RuntimeError(
            f"两跳第一跳 CreateProcessWithLogonW 失败 (sandbox_id={sandbox_id}): {err}"
        )

    # pi.hThread 不在此关闭: 调用方 assign Job 后 resume 主线程, 再 CloseHandle.
    logger.info(
        "两跳第一跳成功 (suspended): sandbox_id=%s runner_pid=%d control_port=%d",
        sandbox_id, pi.dwProcessId, control_port,
    )
    return (
        int(pi.dwProcessId),
        int(pi.hProcess),
        int(pi.hThread),
    )


def _stop_runner(pid: int, process_handle: int, timeout_ms: int = 5000) -> None:
    """停止 runner: TerminateProcess 兜底.

    改 socket 后不再有 pipe stdin 可发 shutdown 帧, 直接 TerminateProcess
    (runner accept 循环被强杀, 进程退出). 后续可加 connect control_port 发
    shutdown 帧的优雅退出, 但 TerminateProcess 够用.
    """
    _require_windows()
    kernel32 = _get_kernel32()
    try:
        kernel32.TerminateProcess(wintypes.HANDLE(process_handle), 1)
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(process_handle))


# ---------------------------------------------------------------------------
# runner 侧: 第二跳 (运行在 jbx-sandbox 上下文).
# ---------------------------------------------------------------------------
def _get_logon_session_sid() -> "ctypes.c_void_p":
    """从当前进程 token 提取登录会话 SID (TokenGroups 里的 logon session)."""
    advapi32 = _get_advapi32()
    kernel32 = _get_kernel32()
    h_token = wintypes.HANDLE()
    # TOKEN_QUERY = 0x0008
    TOKEN_QUERY = 0x0008
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(h_token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # 先 query 长度.
        ret_len = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            h_token, const.TOKEN_GROUPS, None, 0, ctypes.byref(ret_len),
        )
        buf = (ctypes.c_byte * ret_len.value)()
        if not advapi32.GetTokenInformation(
            h_token, const.TOKEN_GROUPS, buf, ret_len, ctypes.byref(ret_len),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # 解析: TOKEN_GROUPS { GroupCount, SID_AND_ATTRIBUTES[] }
        # SID_AND_ATTRIBUTES { Sid: PVOID, Attributes: DWORD }
        groups_struct = ctypes.cast(
            buf, ctypes.POINTER(_TOKEN_GROUPS),
        ).contents
        count = groups_struct.GroupCount
        # SID_AND_ATTRIBUTES 布局.
        class _SID_AND_ATTRS(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]
        arr_t = _SID_AND_ATTRS * count
        groups = ctypes.cast(
            ctypes.addressof(groups_struct) + ctypes.sizeof(wintypes.DWORD),
            ctypes.POINTER(arr_t),
        ).contents
        # 找 SE_GROUP_LOGON_ID (Attributes 位 0x40000000) 的 SID.
        SE_GROUP_LOGON_ID = 0x40000000
        for g in groups:
            if g.Attributes & SE_GROUP_LOGON_ID:
                return g.Sid
        # 兜底: 返回第一个.
        return groups[0].Sid if count else None
    finally:
        kernel32.CloseHandle(h_token)


def _get_everyone_sid() -> "ctypes.c_void_p":
    """CreateWellKnownSid(WinWorldSid) -> Everyone SID."""
    advapi32 = _get_advapi32()
    size = wintypes.DWORD(64)
    buf = (ctypes.c_byte * 64)()
    if not advapi32.CreateWellKnownSid(
        const.WIN_WORLD_SID, None, buf, ctypes.byref(size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return ctypes.cast(buf, ctypes.c_void_p)


def _get_synthetic_write_sid_ptr() -> "ctypes.c_void_p":
    """构造合成 JHXSandboxWrite SID (AllocateAndInitializeSid).

    生成的 SID 必须与 win_acl.get_synthetic_write_sid() 字符串版完全一致,
    即 S-1-5-21-<sub0>-<sub1>-<RID>. SID 表示法 S-R-I-S1-S2-... 中 I 是
    identifier authority, 其后每段是 sub-authority; "21" 是第一个 sub-authority
    (不是 identifier authority 5 的一部分). 故 AllocateAndInitializeSid 的
    nSubAuthorityCount = 4, sub list = [21, sub0, sub1, RID].
    旧版误用 nSubAuthorityCount=3 漏了 21, 产出 S-1-5-... 与 ACL 授权的
    S-1-5-21-... 不是同一个 SID, 导致受限 token 第二重 ACL 检查永远失败
    (review CRITICAL #1).
    """
    advapi32 = _get_advapi32()
    # SID_IDENTIFIER_AUTHORITY = 6 字节 (NT Authority = 5).
    SID_AUTH_NT = (ctypes.c_byte * 6)(0, 0, 0, 0, 0, 5)
    sid_ptr = ctypes.c_void_p()
    # AllocateAndInitializeSid(auth, nSubAuthorityCount, *subauths, &sid)
    # nSubAuthorityCount=4: [21, sub0, sub1, RID] -> S-1-5-21-<sub0>-<sub1>-<RID>.
    ok = advapi32.AllocateAndInitializeSid(
        ctypes.byref(SID_AUTH_NT), 4,
        21,  # sub0 = 21 (使 SID = S-1-5-21-...)
        const.SYNTHETIC_WRITE_SID_SUBAUTHS[0],
        const.SYNTHETIC_WRITE_SID_SUBAUTHS[1],
        const.SYNTHETIC_WRITE_SID_RID,
        0, 0, 0, 0,  # sub4..sub7 占位 (nSubAuthorityCount=4, 后 4 个忽略)
        ctypes.byref(sid_ptr),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return sid_ptr


def _create_restricted_token() -> int:
    """第二跳核心: 在 runner 上下文创建 Write-Restricted Token.

    受限 SID 列表 = [Everyone, 当前 LogonSession, JHXSandboxWrite].
    Flags = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT | WRITE_RESTRICTED.
    """
    advapi32 = _get_advapi32()
    kernel32 = _get_kernel32()
    h_token = wintypes.HANDLE()
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_DEFAULT = 0x0080
    desired = (
        TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT
    )
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), desired, ctypes.byref(h_token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # SID buffer 生命周期: _get_everyone_sid / _get_logon_session_sid
        # 返回的指针指向 Python 管理的 c_byte buffer, 函数返回后 buffer 被 GC
        # → 悬垂指针, CreateRestrictedToken 读它 → WinError 998. 这里内联构造
        # 并持有 buffer 引用直到 CreateRestrictedToken 返回.
        # Everyone SID (CreateWellKnownSid, 持久 buffer).
        everyone_buf = (ctypes.c_byte * 64)()
        everyone_size = wintypes.DWORD(64)
        if not advapi32.CreateWellKnownSid(
            const.WIN_WORLD_SID, None, everyone_buf, ctypes.byref(everyone_size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        # Logon session SID (从 token TokenGroups 提取, 持久 buffer).
        ret_len = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            h_token, const.TOKEN_GROUPS, None, 0, ctypes.byref(ret_len),
        )
        logon_buf = (ctypes.c_byte * ret_len.value)()
        if not advapi32.GetTokenInformation(
            h_token, const.TOKEN_GROUPS, logon_buf, ret_len, ctypes.byref(ret_len),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        groups_struct = ctypes.cast(logon_buf, ctypes.POINTER(_TOKEN_GROUPS)).contents
        count = groups_struct.GroupCount
        arr_t = _SID_AND_ATTRIBUTES * count
        # groups 起始 = Groups 字段偏移 (ctypes 自动算含对齐, 64 位 DWORD 后有
        # 4 字节 padding 让 SID_AND_ATTRIBUTES 8 字节对齐). 旧版手动加 sizeof(DWORD)
        # 在 64 位漏 padding → 读错位 → logon_sid 垃圾值 → WinError 998.
        groups = ctypes.cast(
            ctypes.addressof(groups_struct) + _TOKEN_GROUPS.Groups.offset,
            ctypes.POINTER(arr_t),
        ).contents
        SE_GROUP_LOGON_ID = 0x40000000
        logon_sid_val = None
        for g in groups:
            if g.Attributes & SE_GROUP_LOGON_ID:
                logon_sid_val = g.Sid
                break
        if logon_sid_val is None:
            logon_sid_val = groups[0].Sid if count else None
        # 防御: 若拿不到 logon session SID (count==0 或无 LOGON_ID 组),
        # 不要硬塞 NULL 进 restricting 数组 (CreateRestrictedToken 会
        # 返回 WinError 87). 此时只用 [Everyone, JHXSandboxWrite] 两个
        # restricting SID, 数组大小动态调整.
        entries = [
            _SID_AND_ATTRIBUTES(ctypes.cast(everyone_buf, ctypes.c_void_p), 0),
        ]
        if logon_sid_val is not None:
            entries.append(_SID_AND_ATTRIBUTES(logon_sid_val, 0))

        # 合成 JHXSandboxWrite SID (AllocateAndInitializeSid 堆分配, 不悬垂).
        SID_AUTH_NT = (ctypes.c_byte * 6)(0, 0, 0, 0, 0, 5)
        write_sid_ptr = ctypes.c_void_p()
        ok = advapi32.AllocateAndInitializeSid(
            ctypes.byref(SID_AUTH_NT), 4,
            21,
            const.SYNTHETIC_WRITE_SID_SUBAUTHS[0],
            const.SYNTHETIC_WRITE_SID_SUBAUTHS[1],
            const.SYNTHETIC_WRITE_SID_RID,
            0, 0, 0, 0,
            ctypes.byref(write_sid_ptr),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        entries.append(_SID_AND_ATTRIBUTES(write_sid_ptr, 0))

        restricting = (_SID_AND_ATTRIBUTES * len(entries))(*entries)
        restricted = wintypes.HANDLE()
        logger.info(
            "CreateRestrictedToken 调用: restricting_sids=%d, flags=0x%x",
            len(entries), const.RESTRICTED_TOKEN_FLAGS,
        )
        ok = advapi32.CreateRestrictedToken(
            h_token, const.RESTRICTED_TOKEN_FLAGS,
            0, None,       # disabling sids (空)
            0, None,       # deleting privileges (空)
            len(entries), restricting,  # restricting sids (PSID_AND_ATTRIBUTES, 数组对象自动转指针)
            ctypes.byref(restricted),
        )
        if not ok:
            err = ctypes.WinError(ctypes.get_last_error())
            logger.error("CreateRestrictedToken 失败: %s", err)
            raise err
        logger.info("CreateRestrictedToken 成功: handle=%d", int(restricted.value))
        return int(restricted.value)
    finally:
        kernel32.CloseHandle(h_token)


def _get_runner_primary_token() -> int:
    """拿 runner 自身进程的 primary token (未受限), 供 exec 起 child 用.

    方案1: 受限 token (CreateRestrictedToken) 会让任何 child 进程启动即
    0xC0000142 (DllMain 失败, 实测 cmd/bash/python 全挂, 根因是受限 token 的
    desktop/全局对象机制, 非 ACL/env). 故 exec 改用 runner 自身的未受限 token
    起 child — runner 在 jbx-sandbox 自己的上下文, 给自己 token 起进程绕开
    SeAssignPrimaryTokenPrivilege 权限墙 (设计 §2.5). 代价: 失去 Write-Restricted
    双重写检查, 写控制只剩合成 SID 的 ACL (allow-only 仍挡越权写). 安全降一重,
    但让 bash/cmd/python 能跑起来 (用户代码层写仍受合成 SID ACL 约束).

    返回 token handle (int), 调用方用完须 CloseHandle.
    """
    advapi32 = _get_advapi32()
    kernel32 = _get_kernel32()
    h_token = wintypes.HANDLE()
    TOKEN_QUERY = 0x0008
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ASSIGN_PRIMARY = 0x0001
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
        ctypes.byref(h_token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(h_token.value)


def _create_process_as_user(
    restricted_token: int,
    command: list[str],
    env: dict[str, str] | None,
    workdir: str | None,
    stdin_fd: int,
    stdout_fd: int,
    workspace: str | None = None,
) -> "tuple[int, int]":
    """CreateProcessAsUserW 以受限 token 启动子命令.

    Returns: (child_pid, child_process_handle).
    """
    advapi32 = _get_advapi32()
    cmd_line = " ".join(
        f'"{c}"' if " " in c or "\t" in c else c for c in command
    )
    # 始终构造 env block 并传 (env=None 回退 os.environ).
    # env=None 时不能传 NULL 给 CreateProcessAsUserW (那给子进程空环境块,
    # 无 PATH → 可执行名解析失败 → WinError 2). 回退到 runner 自身环境
    # (os.environ, 含 box-server 继承的 PATH + 工具目录), 并自动注入
    # HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 指向代理端口 (文档 §6.6 要求).
    env_block_buf = None
    env_block_ptr = None
    if env is None:
        env = dict(os.environ)
    else:
        env = dict(env)
    # 兜底补齐 DLL 加载 / 子进程运行所需的基本环境变量. 受限 token 下 child
    # 加载系统 DLL (kernel32 依赖 KnownDlls) 或 msys/cygwin runtime (msys-2.0.dll
    # 初始化需 SystemRoot) 时若缺这些变量会 STATUS_DLL_INIT_FAILED (0xC0000142,
    # 实测: bash/python 启动即退, stdout 全空). box-server 端 _build_windows_exec_env
    # 已补过一次, 但经 daemon IPC JSON 序列化 + 调用方 env 可能只含 PATH 片段,
    # 在此第二跳再补一次确保不缺. 用 setdefault 不覆盖已有值, 与 _create_windows
    # 给 runner 的 env 一致 (process.py §2962).
    for _var in ("SystemRoot", "windir", "PATHEXT", "COMSPEC"):
        _val = os.environ.get(_var)
        if _val:
            env.setdefault(_var, _val)
    # 沙箱可写临时区: 受限 token 写不了宿主 %TEMP% (C:\Users\liubuyu\AppData\
    # Local\Temp), Playwright mkdtemp 会 EPERM. 把 TEMP/TMP 指向 sandbox
    # workspace 下 .tmp (box-server 是 owner, 合成 SID ACL 允许受限 token 写).
    # 其余 profile 路径 (LOCALAPPDATA/APPDATA/USERPROFILE/HOME) 不覆盖 —
    # 让它们保持 jbx-sandbox 的默认值, Playwright 浏览器装在默认位置
    # (%LOCALAPPDATA%\ms-playwright), 由 install/apply_sandbox_acl 对该路径授权,
    # 不桥接、不重定向到共享目录.
    if workspace:
        _child_tmp = os.path.join(workspace, ".tmp")
        try:
            os.makedirs(_child_tmp, exist_ok=True)
        except OSError:
            pass
        env["TEMP"] = _child_tmp
        env["TMP"] = _child_tmp
    else:
        for _var in ("TEMP", "TMP"):
            _val = os.environ.get(_var)
            if _val:
                env.setdefault(_var, _val)
    # 自动注入代理 env (文档 §6.6): 即使调用方没传, 也让遵守代理协议的程序
    # (pip/git/curl/node 等) 走 win_proxy, WFP 兜底拦截不走代理的出网.
    proxy_url = f"http://127.0.0.1:{_proxy_port_start}"
    env.setdefault("HTTP_PROXY", proxy_url)
    env.setdefault("HTTPS_PROXY", proxy_url)
    env.setdefault("http_proxy", proxy_url)
    env.setdefault("https_proxy", proxy_url)
    env.setdefault("ALL_PROXY", proxy_url)
    # NO_PROXY 放行 loopback 自身 (代理 → 代理 不该再走代理).
    env.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
    _push_log(
        "INFO",
        f"child proxy injected: HTTPS_PROXY={env.get('HTTPS_PROXY')} "
        f"NO_PROXY={env.get('NO_PROXY')} "
        f"LOCALAPPDATA={env.get('LOCALAPPDATA')} "
        f"PLAYWRIGHT_BROWSERS_PATH={env.get('PLAYWRIGHT_BROWSERS_PATH')} "
        f"USERPROFILE={env.get('USERPROFILE')} HOME={env.get('HOME')} "
        f"TEMP={env.get('TEMP')}",
    )
    parts = [f"{k}={v}" for k, v in env.items()]
    block = "\0".join(parts) + "\0\0"
    env_block_buf = ctypes.create_unicode_buffer(block)
    env_block_ptr = ctypes.cast(env_block_buf, ctypes.c_void_p)

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
    startup.hStdInput = wintypes.HANDLE(stdin_fd)
    startup.hStdOutput = wintypes.HANDLE(stdout_fd)
    startup.hStdError = wintypes.HANDLE(stdout_fd)
    pi = PROCESS_INFORMATION()

    # env block 始终构造 (env=None 回退 os.environ), 故必须带 UNICODE flag,
    # 否则 CreateProcessAsUserW 按 ANSI 解析 env block → WinError 87.
    creation_flags = (
        const.CREATE_NO_WINDOW
        | const.CREATE_NEW_PROCESS_GROUP
        | const.CREATE_UNICODE_ENVIRONMENT
    )
    cwd = workdir if workdir else None

    ok = advapi32.CreateProcessAsUserW(
        wintypes.HANDLE(restricted_token),
        None,
        cmd_line,
        None, None,
        True,  # inherit handles
        creation_flags,
        env_block_ptr,  # CreateProcessAsUserW 在此返回前读取 env block
        cwd,
        ctypes.byref(startup), ctypes.byref(pi),
    )
    if not ok:
        # 诊断: CreateProcessAsUserW 失败时区分"PATH 找不到" vs "ACL 读不了".
        # WinError 2 通常是可执行名解析失败 (PATH 无该目录) 或受限 token 读不了
        # 可执行文件 (ACL). 打印 command[0]、PATH 片段、目标文件存在性+可读性,
        # 经 [win-runner] 日志长连发回 box-server 由主进程打印.
        _cmd0 = str(command[0]) if command else "<empty>"
        _path_val = env.get("PATH", "") if isinstance(env, dict) else ""
        _path_segs = (_path_val or "").split(os.pathsep)[:8]
        import ntpath as _ntp
        _resolved = None
        for _seg in _path_segs:
            if not _seg:
                continue
            _cand = _ntp.join(_seg, _cmd0)
            if os.path.isfile(_cand):
                _resolved = _cand
                break
        _exists = os.path.isfile(_cmd0)
        _readable = os.access(_cmd0, os.R_OK) if _exists else False
        _push_log(
            "ERROR",
            f"CreateProcessAsUserW 失败 cmd0={_cmd0!r} resolved_in_PATH={_resolved!r} "
            f"cmd0_exists={_exists} cmd0_readable={_readable} "
            f"PATH_segs(8)={_path_segs}",
        )
        raise ctypes.WinError(ctypes.get_last_error())
    return int(pi.dwProcessId), int(pi.hProcess)


def runner_main(argv: list[str]) -> int:
    """runner 入口 (运行在 jbx-sandbox 上下文, 由 broker 第一跳拉起).

    职责:
      1. 创建 Write-Restricted Token.
      2. 循环从 stdin 读长度前缀帧 (exec / write_file / read_file /
         list_dir / shutdown).
      3. 对每个 exec 请求, 以受限 token CreateProcessAsUserW 起子命令,
         收集 stdout/stderr/exit, 写回 stdout 帧.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="win_exec-runner")
    parser.add_argument("--sandbox-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--proxy-port-start", type=int, default=const.DEFAULT_PROXY_PORT_RANGE_START)
    parser.add_argument("--proxy-port-end", type=int, default=const.DEFAULT_PROXY_PORT_RANGE_END)
    parser.add_argument("--control-port", type=int, required=True)
    args = parser.parse_args(argv)

    # 记录代理端口到模块级, 供 _create_process_as_user 自动注入 HTTP_PROXY env.
    global _proxy_port_start, _proxy_port_end
    _proxy_port_start = args.proxy_port_start
    _proxy_port_end = args.proxy_port_end

    _require_windows()
    logger.info(
        "runner 启动: sandbox_id=%s workspace=%s control_port=%s",
        args.sandbox_id, args.workspace, args.control_port,
    )
    _push_log("INFO", f"runner 启动: sandbox_id={args.sandbox_id} "
               f"workspace={args.workspace} control_port={args.control_port}")

    # runner 由 CreateProcessWithLogonW + CREATE_NO_WINDOW 拉起, stderr 无落盘.
    # 任何早期异常 (尤其 _create_restricted_token) 会让 runner 静默退出,
    # box-server 端只看到 control_port ECONNREFUSED, 无法定位根因.
    # 这里在最外层包 try/except, 把异常完整落盘 + logger.error + _push_log,
    # 后者经日志长连发回 box-server 由主进程打印.
    try:
        restricted_token = _create_restricted_token()
    except Exception:
        import traceback as _tb
        tb = _tb.format_exc()
        logger.exception(
            "runner _create_restricted_token 失败, runner 退出 (sandbox_id=%s)",
            args.sandbox_id,
        )
        _push_log("ERROR",
                  f"runner _create_restricted_token 失败, runner 退出 "
                  f"(sandbox_id={args.sandbox_id})", exc=tb)
        return 1
    _push_log("INFO", f"restricted token 创建成功: handle={restricted_token}")

    # TCP loopback 控制端口 (box-server 分配, 命令行参数传入). runner bind + listen,
    # box-server 每次 exec connect 一条新连接, 发一帧请求读一帧响应后 close.
    # 对齐 Linux AF_UNIX 模型 (Windows 不能传 fd, 改传端口号).
    import socket
    port = args.control_port
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(64)
    except OSError as exc:
        logger.error("runner bind 127.0.0.1:%d 失败: %s", port, exc)
        _push_log("ERROR", f"runner bind 127.0.0.1:{port} 失败: {exc}")
        return 1
    logger.info("runner 监听 127.0.0.1:%d (sandbox_id=%s)", port, args.sandbox_id)
    _push_log("INFO", f"runner 监听 127.0.0.1:{port} (sandbox_id={args.sandbox_id})")

    try:
        while True:
            conn, _ = listener.accept()
            try:
                header_frame = recv_frame(conn, MAX_HEADER_BYTES)
            except (ConnectionError, OSError, ValueError):
                # client 连上后立刻断开 (探活/异常), 直接关连接等下一个.
                conn.close()
                continue
            try:
                header = json.loads(header_frame.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                _send_error_response(conn, f"invalid request header: {exc}")
                conn.close()
                continue

            req_type = header.get("type")
            # 日志订阅长连: box-server 创建 sandbox 后主动 connect 发此帧.
            # runner 把该连接存入订阅集保持, 之后任何阶段往里 push log 帧.
            # 不 close, 不回响应 (订阅是单向 push), 回 accept 等下一条短连.
            if req_type == REQUEST_TYPE_SUBSCRIBE_LOG:
                try:
                    conn.setblocking(True)
                    with _log_sub_lock:
                        _log_subscribers.append(conn)
                    _push_log("INFO",
                              "日志订阅连接已建立 (box-server -> runner 长连)")
                except OSError as exc:
                    _push_log("WARNING", f"订阅连接入集失败: {exc}")
                    try:
                        conn.close()
                    except OSError:
                        pass
                continue
            if req_type == "shutdown":
                _send_response(conn, {"ok": True})
                conn.close()
                break
            # 每个 request handler 独立 try/except: 单个连接的 OSError (如 box-server
            # 读超时主动 close 后 runner 发响应抛 ConnectionAbortedError) 不应杀掉
            # 整个 runner — 旧版直接冒泡到 accept 循环 except → runner 退出 → 后续
            # 所有 exec 全 timeout (409). 这里捕获后只 close 该连接, 继续 accept.
            try:
                if req_type == "exec":
                    # exec 请求的 stdin body 帧 (紧跟 header), 从同一连接读.
                    stdin_size = int(header.get("stdin_size", 0))
                    stdin_bytes = recv_frame(conn, MAX_STDIN_BYTES) if stdin_size > 0 else b""
                    _handle_exec_request(
                        conn, header, restricted_token, args.workspace,
                        stdin_bytes,
                    )
                elif req_type == "write_file":
                    _handle_write_file_request(conn, header, conn)
                elif req_type == "read_file":
                    _handle_read_file_request(conn, header)
                elif req_type == "list_dir":
                    _handle_list_dir_request(conn, header)
                else:
                    _send_error_response(conn, f"unknown request type: {req_type!r}")
            except OSError as exc:
                # 连接已断 (box-server 超时关闭 / 对端崩). 单连接失败不杀 runner.
                logger.debug("runner 处理 %s 请求连接异常, 跳过: %s", req_type, exc)
            except Exception as exc:  # noqa: BLE001
                import traceback as _tb_req
                logger.debug("runner 处理 %s 请求异常: %s", req_type, exc, exc_info=True)
                _push_log("WARNING",
                          f"runner 处理 {req_type} 请求异常 (单连接, 不杀 runner): "
                          f"{exc}", exc=_tb_req.format_exc())
            conn.close()
    except Exception:
        import traceback as _tb
        tb = _tb.format_exc()
        logger.exception("runner accept 循环异常 (sandbox_id=%s)", args.sandbox_id)
        _push_log("ERROR",
                  f"runner accept 循环异常 (sandbox_id={args.sandbox_id})", exc=tb)
    finally:
        _push_log("INFO", f"runner 退出 (sandbox_id={args.sandbox_id})")
        kernel32 = _get_kernel32()
        kernel32.CloseHandle(wintypes.HANDLE(restricted_token))
        listener.close()
        # 关闭所有日志订阅连接, 通知订阅方 runner 已退出.
        with _log_sub_lock:
            subs = list(_log_subscribers)
            _log_subscribers.clear()
        for sock in subs:
            try:
                sock.close()
            except OSError:
                pass
    return 0


def _send_response(stream, payload: dict) -> None:
    send_frame(stream, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _send_error_response(stream, detail: str) -> None:
    _send_response(stream, {"ok": False, "error": "io_error", "detail": detail})


# 匹配双引号包围的片段 (不含内部双引号). 用于规整 bash -lc script 中双引号内的
# Windows 路径反斜杠, 把 \ 替换为 / (bash 双引号内会吞掉非特殊反斜杠, 详见
# _handle_exec_request 注释). 单引号/双引号外内容不动, 避免误伤 shell 元字符.
_DQ_SEGMENT_RE = re.compile(r'"[^"]*"')


def _normalize_bash_script_backslashes(command: list) -> list | None:
    """对 ["bash"|"sh", "-lc"|"-c", script, ...] 的 script 做反斜杠规整.

    返回新 command list; 不命中模式返回 None (调用方保持原样).
    """
    if len(command) < 3:
        return None
    exe = str(command[0])
    flag = str(command[1])
    exe_base = os.path.basename(exe).lower()
    if exe_base not in ("bash", "bash.exe", "sh", "sh.exe"):
        return None
    if flag not in ("-lc", "-c"):
        return None
    script = str(command[2])
    if "\\" not in script:
        return None

    def _norm_dq(m: "re.Match[str]") -> str:
        seg = m.group(0)
        # seg 形如 "...", 把内部的反斜杠换成正斜杠 (Windows/node 能正确解析).
        inner = seg[1:-1].replace("\\", "/")
        return f'"{inner}"'

    new_script = _DQ_SEGMENT_RE.sub(_norm_dq, script)
    if new_script == script:
        return None
    new_command = list(command)
    new_command[2] = new_script
    _push_log(
        "DEBUG",
        f"bash script backslash normalized (flag={flag}): "
        f"before={script[:120]!r} after={new_script[:120]!r}",
    )
    return new_command


def _handle_exec_request(stream, header, restricted_token, workspace, stdin_bytes) -> None:
    """处理 exec 请求: 以受限 token 起子命令, 回传 stdout/stderr/exit.

    stdin_bytes 透传给子进程 stdin (若非空).
    """
    command = header.get("command", [])
    if not command:
        _send_error_response(stream, "exec requires non-empty command")
        return
    # Windows 兼容: agent-core 跨平台送的裸名 "python3" 在 Windows venv\Scripts
    # 里未必存在 (venv 通常只放 python.exe, python3 是 virtualenv 额外建的,
    # 受限 token 下也可能读不到). 归一化 "python3" → "python", 让 CreateProcess
    # 解析到 venv\Scripts\python.exe (venv 必有). 同理 "python3.<ver>" 也归一.
    # 只改裸名 (command[0]), 不碰带路径的 (含 \\ 或 /).
    _c0 = str(command[0])
    if _c0 in ("python3", "python3.13", "python3.12", "python3.11") or _c0.startswith("python3."):
        if "\\" not in _c0 and "/" not in _c0:
            command[0] = "python"
    # Windows bash 反斜杠规整: caller (openjiuwen) 把命令包成 ["bash","-lc",script],
    # script 形如 node "D:\...\cli.js" check-env. _create_process_as_user 把 argv 重新
    # 拼成 Windows cmdline 时, script 因含空格被双引号包裹; bash 解析双引号内非特殊
    # 字符前的反斜杠会直接吞掉 (bash 双引号内仅保留 $ ` \" \newline 的反斜杠), 导致
    # Windows 路径 D:\Workspace\...\cli.js 变成 D:Workspace...cli.js → MODULE_NOT_FOUND.
    # 日志证明: 改用正斜杠 D:/.../cli.js 后该问题消失. 这里在交给 bash 前, 把 script
    # 中双引号段内的反斜杠规整为正斜杠 (只动双引号内, 不碰单引号/双引号外, 避免误伤
    # shell 元字符). 只对 ["bash"|"sh", "-lc"|"-c", script, ...] 模式生效, 不影响
    # python -c / node -e 等非 bash 命令.
    _norm_command = _normalize_bash_script_backslashes(command)
    if _norm_command is not None:
        command = _norm_command
    # 建立 pipe 收集子进程 stdout/stderr.
    kernel32 = _get_kernel32()
    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.bInheritHandle = True
    child_out_read = wintypes.HANDLE()
    child_out_write = wintypes.HANDLE()
    kernel32.CreatePipe(
        ctypes.byref(child_out_read), ctypes.byref(child_out_write),
        ctypes.byref(sa), 0,
    )
    # 建立 stdin pipe: runner 写, child 读 (继承读端).
    child_in_read = wintypes.HANDLE()
    child_in_write = wintypes.HANDLE()
    kernel32.CreatePipe(
        ctypes.byref(child_in_read), ctypes.byref(child_in_write),
        ctypes.byref(sa), 0,
    )
    # runner 持有的写端关闭继承, 防 child 拿到.
    _clear_inherit(int(child_in_write.value))
    try:
        workdir = header.get("workdir")
        env = header.get("env")
        # 方案1: 不用受限 token (它让 child 启动即 0xC0000142), 改用 runner 自身
        # primary token (未受限). 见 _get_runner_primary_token 注释.
        _self_token = _get_runner_primary_token()
        try:
            pid, proc_handle = _create_process_as_user(
                _self_token, list(command), env, workdir,
                stdin_fd=int(child_in_read.value),
                stdout_fd=int(child_out_write.value),
                workspace=workspace,
            )
        finally:
            _get_kernel32().CloseHandle(wintypes.HANDLE(_self_token))
        # runner 不再需要 child 端的写端/读端副本.
        kernel32.CloseHandle(child_in_read)
        kernel32.CloseHandle(child_out_write)
        # 透传 stdin (若有).
        if stdin_bytes:
            import msvcrt  # type: ignore[import-not-found]
            in_write_fd = msvcrt.open_osfhandle(
                int(child_in_write.value), os.O_WRONLY | os.O_BINARY,
            )
            with os.fdopen(in_write_fd, "wb") as in_wf:
                in_wf.write(stdin_bytes)
            # 写完关闭写端让 child 读到 EOF.
            kernel32.CloseHandle(child_in_write)
        else:
            kernel32.CloseHandle(child_in_write)
        # 等待子进程退出, 再读 stdout. 顺序不能反: 若先 read 再 wait, child 崩溃
        # (如 0xc0000142 STATUS_DLL_INIT_FAILED) 后其继承的 stdout 写端可能未
        # 干净关闭 → pipe 不 EOF → runner read 阻塞 → control_port 无响应 →
        # box-server 端 exec 超时 (timed out). 先 wait 让内核回收 child 持有的
        # 所有 handle (含 stdout 写端), 再 drain stdout 能拿到 EOF.
        # 用带超时的循环 wait 而非 INFINITE: 防 child stdout 写满 pipe (64KB) 后
        # 阻塞在 write 等 runner 读 → 互相死锁. child 超时预算取调用方传的 timeout
        # (box-server payload["timeout"], 单位秒, 如 playwright install 的 600s),
        # 缺省 120s. 旧版硬编码 120000ms 不读 caller timeout → bash 工具传 600s
        # 也被 120s 强杀, 导致长命令被误杀.
        INFINITE = 0xFFFFFFFF
        WAIT_TIMEOUT_MS = 500
        deadline_waited_ms = 0
        _caller_timeout_s = header.get("timeout")
        try:
            _caller_timeout_s = int(_caller_timeout_s) if _caller_timeout_s is not None else 120
        except (TypeError, ValueError):
            _caller_timeout_s = 120
        if _caller_timeout_s <= 0:
            _caller_timeout_s = 120
        WAIT_BUDGET_MS = _caller_timeout_s * 1000
        _child_killed = False  # child 是否被超时强杀 (强杀后 stdout pipe 可能不 EOF)
        while True:
            result = kernel32.WaitForSingleObject(
                wintypes.HANDLE(proc_handle), WAIT_TIMEOUT_MS,
            )
            if result == 0:  # WAIT_OBJECT_0: child 已退出
                break
            deadline_waited_ms += WAIT_TIMEOUT_MS
            if deadline_waited_ms >= WAIT_BUDGET_MS:
                # child 长时间不退出 (可能 stdout 写满死锁, 或 child 卡住),
                # 强杀避免 runner 永久挂起. 后续 exec 还能继续 (单连接不杀 runner).
                kernel32.TerminateProcess(wintypes.HANDLE(proc_handle), 1)
                _child_killed = True
                _push_log("WARNING",
                          f"exec child 超时未退出 ({WAIT_BUDGET_MS}ms) 强杀, "
                          f"cmd={command[:3] if command else []!r}")
                break
        # 读取子进程 stdout 全部输出.
        # 用 os.fdopen 包装 child_out_read handle -> 文件对象.
        import msvcrt  # type: ignore[import-not-found]
        read_fd = msvcrt.open_osfhandle(
            int(child_out_read.value), os.O_RDONLY | os.O_BINARY,
        )
        out_buf = bytearray()
        # 强杀后的 stdout 读取必须带超时, 不能阻塞等 EOF: TerminateProcess 只杀
        # 直接 child (bash), 但 child 起的孙进程 (node/npm/下载子进程) 继承了
        # stdout pipe 写端仍持有 → pipe 不 EOF → fh.read 永久阻塞 → runner accept
        # 循环卡死 → 后续所有 exec IPC 全 timeout (实测: npx playwright install
        # 超时强杀后, 整个 sandbox 后续 bash 全 timed out + WinError 10053).
        # 正常退出 (child 自己关闭写端) 则 pipe 会 EOF, 用阻塞读完整输出.
        # 强杀后用 PeekNamedPipe 轮询 + 总期限读, 超时即放弃残缺 stdout 跳出, 不卡 runner.
        # (Windows select 只支持 socket, pipe 用 win32pipe.PeekNamedPipe 探测可读字节数.)
        if _child_killed:
            import time as _time
            import win32pipe  # type: ignore[import-not-found]
            _drain_deadline = _time.monotonic() + 5.0
            _read_handle = int(child_out_read.value)
            while True:
                if _time.monotonic() >= _drain_deadline:
                    _push_log("DEBUG",
                              f"exec stdout 读超时跳过 (child 强杀后 pipe 不 EOF), "
                              f"cmd={command[:3] if command else []!r}")
                    break
                try:
                    _avail, _ = win32pipe.PeekNamedPipe(_read_handle, 0)
                except Exception:
                    # pipe 已无效 (handle 关闭等): 视为 EOF.
                    break
                if _avail > 0:
                    try:
                        chunk = os.read(read_fd, min(_avail, 65536))
                    except OSError:
                        break
                    if not chunk:
                        break
                    out_buf.extend(chunk)
                    if len(out_buf) > MAX_STDOUT_BYTES:
                        out_buf = out_buf[:MAX_STDOUT_BYTES]
                        break
                else:
                    # 暂无数据, 短睡再轮询 (PeekNamedPipe 无数据时也可能因写端全关
                    # 而后续返回 EOF, 但孙进程仍持写端时永远不 EOF — 靠 deadline 兜底).
                    _time.sleep(0.1)
            # 强杀分支用的是裸 os.read(fd), fdopen 分支会自动关 fd; 这里显式关
            # 避免 osfhandle→fd 映射泄漏 (底层 handle 由 finally CloseHandle 关).
            try:
                os.close(read_fd)
            except OSError:
                pass
        else:
            with os.fdopen(read_fd, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    out_buf.extend(chunk)
                    if len(out_buf) > MAX_STDOUT_BYTES:
                        out_buf = out_buf[:MAX_STDOUT_BYTES]
                        break
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(
            wintypes.HANDLE(proc_handle), ctypes.byref(exit_code),
        )
        kernel32.CloseHandle(wintypes.HANDLE(proc_handle))
        ec = int(exit_code.value)
        out_text = bytes(out_buf).decode("utf-8", errors="replace")
        _send_response(stream, {
            "ok": True,
            "exit_code": ec,
            "stdout": out_text,
            "stderr": "",
        })
        # 上报 exec 结果 (尤其失败时). exit_code != 0 或输出为空是定位
        # "空 stderr exit=1" 类问题的关键, 把 command 摘要 + exit + 输出前缀
        # 经日志长连发回 box-server 由主进程打印. 输出截断防撑爆日志帧.
        cmd_summary = " ".join(str(c) for c in (header.get("command") or []))[:200]
        # 失败时若含 EPERM, 取更长 preview (含完整错误路径, 之前 512 截断 EPERM 路径).
        if ec != 0 and "EPERM" in out_text:
            out_preview = out_text[:4000]
        else:
            out_preview = out_text[:512]
        if ec != 0:
            # 失败时把完整 stdout 落盘到 workspace (.exec_failed.log), 供宿主机直接读.
            # 日志长连会截断 preview, 落盘拿完整 EPERM 路径等错误信息.
            if workspace:
                try:
                    _failed_log = os.path.join(str(workspace), ".exec_failed.log")
                    with open(_failed_log, "w", encoding="utf-8", errors="replace") as _fh:
                        _fh.write(f"cmd: {cmd_summary}\nexit: {ec}\n")
                        _fh.write(f"stdout_len: {len(out_text)}\n")
                        _fh.write("---- stdout ----\n")
                        _fh.write(out_text)
                except OSError:
                    pass
            _push_log("WARNING",
                      f"exec 失败 exit={ec} cmd={cmd_summary!r} "
                      f"stdout_len={len(out_text)} stdout_preview={out_preview!r}")
        else:
            _push_log("INFO",
                      f"exec 成功 exit=0 cmd={cmd_summary!r} "
                      f"stdout_len={len(out_text)}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb
        tb = _tb.format_exc()
        # 发 error response 时若连接已断 (box-server 读超时主动 close) 会再抛
        # ConnectionAbortedError/BrokenPipeError, 若不吞会冒泡到 accept 循环的
        # except → runner 退出 → 后续所有 exec 全 timeout (409). 这里兜底: 发
        # error response 失败就吞掉, 不让单个 exec 的连接异常杀掉整个 runner.
        try:
            _send_error_response(stream, f"exec failed: {exc}")
        except OSError:
            pass
        _push_log("ERROR", f"exec 处理异常: {exc}", exc=tb)
        try:
            kernel32.CloseHandle(child_out_write)
            kernel32.CloseHandle(child_out_read)
        except Exception:
            pass


def _handle_write_file_request(stream, header, stdin) -> None:
    import os
    path = header.get("path", "")
    size = int(header.get("content_size", 0))
    mkdir_parents = bool(header.get("mkdir_parents", True))
    mode = header.get("mode")
    try:
        content = recv_frame(stdin, MAX_FILE_BYTES) if size > 0 else b""
        if size and len(content) != size:
            raise ConnectionError(
                f"write_file content size mismatch: got {len(content)} want {size}"
            )
        if mkdir_parents:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
            if mode is not None:
                os.chmod(path, int(str(mode), 8) if isinstance(mode, str) else mode)
        _send_response(stream, {"ok": True})
    except OSError as exc:
        _send_response(stream, {
            "ok": False, "error": "io_error", "errno": exc.errno,
            "stderr": str(exc),
        })
    except Exception as exc:  # noqa: BLE001
        _send_error_response(stream, str(exc))


def _handle_read_file_request(stream, header) -> None:
    path = header.get("path", "")
    try:
        with open(path, "rb") as fh:
            content = fh.read(MAX_FILE_BYTES)
        _send_response(stream, {
            "ok": True, "content_size": len(content),
        })
        send_frame(stream, content)
    except OSError as exc:
        _send_response(stream, {
            "ok": False, "error": "io_error", "errno": exc.errno,
            "stderr": str(exc),
        })
    except Exception as exc:  # noqa: BLE001
        _send_error_response(stream, str(exc))


def _handle_list_dir_request(stream, header) -> None:
    import os
    path = header.get("path", "")
    recursive = bool(header.get("recursive", False))
    include_files = bool(header.get("include_files", True))
    include_dirs = bool(header.get("include_dirs", True))
    try:
        items: list[dict] = []
        if recursive:
            for root, dirs, files in os.walk(path):
                if include_dirs:
                    for d in dirs:
                        items.append({"type": "dir", "name": d, "path": os.path.join(root, d)})
                if include_files:
                    for f in files:
                        items.append({"type": "file", "name": f, "path": os.path.join(root, f)})
        else:
            for entry in os.listdir(path):
                full = os.path.join(path, entry)
                is_dir = os.path.isdir(full)
                if is_dir and include_dirs:
                    items.append({"type": "dir", "name": entry, "path": full})
                elif not is_dir and include_files:
                    items.append({"type": "file", "name": entry, "path": full})
        _send_response(stream, {"ok": True, "items": items})
    except OSError as exc:
        _send_response(stream, {
            "ok": False, "error": "io_error", "errno": exc.errno,
            "stderr": str(exc),
        })
    except Exception as exc:  # noqa: BLE001
        _send_error_response(stream, str(exc))


if __name__ == "__main__":  # pragma: no cover - runner 入口
    sys.argv.pop(0)  # 去掉脚本名
    if sys.argv and sys.argv[0] == RUNNER_SUBCOMMAND:
        sys.argv.pop(0)
        raise SystemExit(runner_main(sys.argv))
