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
import sys
from ctypes import wintypes

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const
from jiuwenbox.supervisor.daemon_ipc import (
    MAX_FILE_BYTES,
    MAX_HEADER_BYTES,
    MAX_STDIN_BYTES,
    MAX_STDOUT_BYTES,
    recv_frame,
    send_frame,
)

configure_logging()
logger = logging.getLogger(__name__)

# runner 脚本路径 (本模块的 runner 入口函数以 `python -m` 形式启动).
RUNNER_MODULE = "jiuwenbox.supervisor.win_exec"
RUNNER_SUBCOMMAND = "runner"


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


class _TOKEN_GROUPS(ctypes.Structure):
    """TOKEN_GROUPS 变长结构, 仅取第一个组用于解析 (我们只需 logon session SID)."""
    _fields_ = [
        ("GroupCount", wintypes.DWORD),
        ("Groups", ctypes.c_byte * 0),  # 变长, 实际用 pointer 解析
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
            wintypes.DWORD, ctypes.c_void_p,  # restricting sids
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
            ctypes.c_void_p, wintypes.BYTE, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
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
def _build_runner_command(
    sandbox_id: str,
    workspace: str,
    proxy_port_start: int,
    proxy_port_end: int,
) -> str:
    """构造 runner 命令行 (CreateProcessWithLogonW 的 lpCommandLine).

    用 ``python -m jiuwenbox.supervisor.win_exec runner --sandbox-id ...``.
    """
    py = sys.executable or "python"
    parts = [
        py,
        "-m", RUNNER_MODULE,
        RUNNER_SUBCOMMAND,
        "--sandbox-id", sandbox_id,
        "--workspace", workspace,
        "--proxy-port-start", str(proxy_port_start),
        "--proxy-port-end", str(proxy_port_end),
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
    env: dict[str, str] | None = None,
) -> "tuple[int, int, int, int]":
    """第一跳: 以 jbx-sandbox 身份启动 runner.

    Returns:
        (runner_pid, stdin_write_handle, stdout_read_handle, runner_process_handle):
            box-server 通过 stdin_write_handle 向 runner 发请求,
            从 stdout_read_handle 读响应. runner_process_handle 用于
            停止/等待 runner.
    """
    _require_windows()
    advapi32 = _get_advapi32()
    kernel32 = _get_kernel32()

    cmd = _build_runner_command(
        sandbox_id, workspace, proxy_port_start, proxy_port_end,
    )

    # 创建一对继承的 pipe: runner stdin (box-server 写) + runner stdout (box-server 读).
    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.bInheritHandle = True
    sa.lpSecurityDescriptor = None

    child_stdin_read = wintypes.HANDLE()
    child_stdin_write = wintypes.HANDLE()
    child_stdout_read = wintypes.HANDLE()
    child_stdout_write = wintypes.HANDLE()

    if not kernel32.CreatePipe(
        ctypes.byref(child_stdin_read), ctypes.byref(child_stdin_write),
        ctypes.byref(sa), 0,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel32.CreatePipe(
        ctypes.byref(child_stdout_read), ctypes.byref(child_stdout_write),
        ctypes.byref(sa), 0,
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    # box-server 持有的端 (stdin 写端 + stdout 读端) 关闭继承, 确保 runner
    # 只继承它该用的端 (stdin 读端 + stdout 写端). 否则 runner 之后
    # CreateProcessAsUserW(bInheritHandle=True) 起 child 时, child 会继承
    # runner 持有的全部句柄 (含 box-server 这两个端), 造成 pipe 隔离泄露
    # (对标 Linux daemon 的 close_fds=True).
    _clear_inherit(int(child_stdin_write.value))
    _clear_inherit(int(child_stdout_read.value))

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
    startup.hStdInput = child_stdin_read
    startup.hStdOutput = child_stdout_write
    startup.hStdError = child_stdout_write
    pi = PROCESS_INFORMATION()

    # LOGON_WITH_PROFILE = 0x1, LOGON_NETCREDENTIALS_ONLY = 0x2
    LOGON_WITH_PROFILE = 0x00000001
    creation_flags = const.CREATE_NO_WINDOW

    ok = advapi32.CreateProcessWithLogonW(
        sandbox_user, None, sandbox_password,
        LOGON_WITH_PROFILE,
        None, cmd,
        creation_flags,
        None, None,
        ctypes.byref(startup), ctypes.byref(pi),
    )
    if not ok:
        err = ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(child_stdin_read)
        kernel32.CloseHandle(child_stdin_write)
        kernel32.CloseHandle(child_stdout_read)
        kernel32.CloseHandle(child_stdout_write)
        raise RuntimeError(
            f"两跳第一跳 CreateProcessWithLogonW 失败 (sandbox_id={sandbox_id}): {err}"
        )

    # 关闭 runner 侧的读/写端副本 (runner 自己有继承的副本).
    kernel32.CloseHandle(child_stdin_read)
    kernel32.CloseHandle(child_stdout_write)
    kernel32.CloseHandle(pi.hThread)

    logger.info(
        "两跳第一跳成功: sandbox_id=%s runner_pid=%d",
        sandbox_id, pi.dwProcessId,
    )
    return (
        int(pi.dwProcessId),
        int(child_stdin_write.value),
        int(child_stdout_read.value),
        int(pi.hProcess),
    )


def _stop_runner(pid: int, process_handle: int, timeout_ms: int = 5000) -> None:
    """发送 shutdown 请求 + TerminateProcess 兜底."""
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
    """构造合成 JHXSandboxWrite SID (AllocateAndInitializeSid)."""
    advapi32 = _get_advapi32()
    # SID_IDENTIFIER_AUTHORITY = 6 字节 (NT Authority = 5).
    SID_AUTH_NT = (ctypes.c_byte * 6)(0, 0, 0, 0, 0, 5)
    sid_ptr = ctypes.c_void_p()
    # AllocateAndInitializeSid(auth, sub_authority_count, *subauths, &sid)
    # 这里 sub_authority_count = 3 (固定前缀 21 的 3 个 sub authority + RID).
    # 实际 SID: S-1-5-21-<sub0>-<sub1>-<RID> -> 4 个 sub authority (21 本身是
    # identifier authority 5 + 第一个 sub = 21). 故 sub count = 3 (sub0,sub1,RID).
    ok = advapi32.AllocateAndInitializeSid(
        ctypes.byref(SID_AUTH_NT), 3,
        const.SYNTHETIC_WRITE_SID_SUBAUTHS[0],
        const.SYNTHETIC_WRITE_SID_SUBAUTHS[1],
        const.SYNTHETIC_WRITE_SID_RID,
        0, 0, 0, 0, 0, 0,
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
        logon_sid = _get_logon_session_sid()
        everyone_sid = _get_everyone_sid()
        write_sid = _get_synthetic_write_sid_ptr()
        # 构造 restricting sids 数组 (PVOID[]).
        restricting = (ctypes.c_void_p * 3)(
            everyone_sid, logon_sid, write_sid,
        )
        restricted = wintypes.HANDLE()
        ok = advapi32.CreateRestrictedToken(
            h_token, const.RESTRICTED_TOKEN_FLAGS,
            0, None,       # disabling sids (空)
            0, None,       # deleting privileges (空)
            3, restricting,  # restricting sids
            ctypes.byref(restricted),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(restricted.value)
    finally:
        kernel32.CloseHandle(h_token)


def _create_process_as_user(
    restricted_token: int,
    command: list[str],
    env: dict[str, str] | None,
    workdir: str | None,
    stdin_fd: int,
    stdout_fd: int,
) -> "tuple[int, int]":
    """CreateProcessAsUserW 以受限 token 启动子命令.

    Returns: (child_pid, child_process_handle).
    """
    advapi32 = _get_advapi32()
    cmd_line = " ".join(
        f'"{c}"' if " " in c or "\t" in c else c for c in command
    )
    # 构造子进程环境块 (Windows 要求 \0\0 结尾的 unicode 环境块).
    env_block = _build_environment_block(env) if env else None

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
    startup.hStdInput = wintypes.HANDLE(stdin_fd)
    startup.hStdOutput = wintypes.HANDLE(stdout_fd)
    startup.hStdError = wintypes.HANDLE(stdout_fd)
    pi = PROCESS_INFORMATION()

    creation_flags = const.CREATE_NO_WINDOW | const.CREATE_NEW_PROCESS_GROUP
    cwd = workdir if workdir else None

    ok = advapi32.CreateProcessAsUserW(
        wintypes.HANDLE(restricted_token),
        None,
        cmd_line,
        None, None,
        True,  # inherit handles
        creation_flags,
        env_block,
        cwd,
        ctypes.byref(startup), ctypes.byref(pi),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    advapi32  # silence unused
    return int(pi.dwProcessId), int(pi.hProcess)


def _build_environment_block(env: dict[str, str]) -> "ctypes.c_wchar_p":
    """把 env dict 编码成 Windows 环境块 (KEY=VALUE\\0...\\0)."""
    parts = [f"{k}={v}" for k, v in env.items()]
    block = "\0".join(parts) + "\0\0"
    # 分配可写缓冲.
    buf = ctypes.create_unicode_buffer(block)
    return ctypes.cast(buf, ctypes.c_void_p)


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
    args = parser.parse_args(argv)

    _require_windows()
    logger.info(
        "runner 启动: sandbox_id=%s workspace=%s",
        args.sandbox_id, args.workspace,
    )

    restricted_token = _create_restricted_token()

    # 从 stdin 读请求帧, 写 stdout 响应帧. stdin/stdout 是继承自 broker 的 pipe.
    # Python 的 sys.stdin/sys.stdout 在 Windows 上是 textio, 需要用底层 buffer.
    stdin = sys.stdin.buffer  # type: ignore[union-attr]
    stdout = sys.stdout.buffer  # type: ignore[union-attr]

    try:
        while True:
            try:
                header_frame = recv_frame(stdin, MAX_HEADER_BYTES)
            except (ConnectionError, OSError, ValueError):
                # pipe 关闭 (broker 退出), runner 优雅退出.
                break
            try:
                header = json.loads(header_frame.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                _send_error_response(stdout, f"invalid request header: {exc}")
                continue

            req_type = header.get("type")
            if req_type == "shutdown":
                _send_response(stdout, {"ok": True})
                break
            if req_type == "exec":
                # exec 请求的 stdin body 帧 (紧跟 header).
                stdin_size = int(header.get("stdin_size", 0))
                stdin_bytes = recv_frame(stdin, MAX_STDIN_BYTES) if stdin_size > 0 else b""
                _handle_exec_request(
                    stdout, header, restricted_token, args.workspace,
                    stdin_bytes,
                )
            elif req_type == "write_file":
                _handle_write_file_request(stdout, header, stdin)
            elif req_type == "read_file":
                _handle_read_file_request(stdout, header)
            elif req_type == "list_dir":
                _handle_list_dir_request(stdout, header)
            else:
                _send_error_response(stdout, f"unknown request type: {req_type!r}")
    finally:
        kernel32 = _get_kernel32()
        kernel32.CloseHandle(wintypes.HANDLE(restricted_token))
    return 0


def _send_response(stream, payload: dict) -> None:
    send_frame(stream, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _send_error_response(stream, detail: str) -> None:
    _send_response(stream, {"ok": False, "error": "io_error", "detail": detail})


def _handle_exec_request(stream, header, restricted_token, workspace, stdin_bytes) -> None:
    """处理 exec 请求: 以受限 token 起子命令, 回传 stdout/stderr/exit.

    stdin_bytes 透传给子进程 stdin (若非空).
    """
    command = header.get("command", [])
    if not command:
        _send_error_response(stream, "exec requires non-empty command")
        return
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
        pid, proc_handle = _create_process_as_user(
            restricted_token, list(command), env, workdir,
            stdin_fd=int(child_in_read.value),
            stdout_fd=int(child_out_write.value),
        )
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
        # 读取子进程 stdout 全部输出.
        # 用 os.fdopen 包装 child_out_read handle -> 文件对象.
        import msvcrt  # type: ignore[import-not-found]
        read_fd = msvcrt.open_osfhandle(
            int(child_out_read.value), os.O_RDONLY | os.O_BINARY,
        )
        out_buf = bytearray()
        with os.fdopen(read_fd, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                out_buf.extend(chunk)
                if len(out_buf) > MAX_STDOUT_BYTES:
                    out_buf = out_buf[:MAX_STDOUT_BYTES]
                    break
        # 等待子进程退出.
        INFINITE = 0xFFFFFFFF
        kernel32.WaitForSingleObject(wintypes.HANDLE(proc_handle), INFINITE)
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(
            wintypes.HANDLE(proc_handle), ctypes.byref(exit_code),
        )
        kernel32.CloseHandle(wintypes.HANDLE(proc_handle))
        _send_response(stream, {
            "ok": True,
            "exit_code": int(exit_code.value),
            "stdout": bytes(out_buf).decode("utf-8", errors="replace"),
            "stderr": "",
        })
    except Exception as exc:  # noqa: BLE001
        _send_error_response(stream, f"exec failed: {exc}")
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
