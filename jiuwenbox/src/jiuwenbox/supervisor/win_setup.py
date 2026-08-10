# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
r"""Windows 沙箱一次性环境准备.

幂等: 通过注册表 ``HKLM\Software\JiuwenBox\WindowsSandbox\installed`` 标记
判断是否已完成, 重复执行无副作用.

UAC 提权: 若当前进程非管理员, 通过 ShellExecuteW "runas" verb 拉起一个
提权子进程执行 ``python -m jiuwenbox.supervisor.win_setup --install``.
"""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import sys
import threading
from pathlib import Path
from ctypes import wintypes

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const

configure_logging()
logger = logging.getLogger(__name__)

# Windows netapi32 (NetUserAdd 等).
_netapi32: ctypes.WinDLL | None = None
_advapi32: ctypes.WinDLL | None = None
_kernel32: ctypes.WinDLL | None = None
_shell32: ctypes.WinDLL | None = None
# userenv.dll (DeleteProfileW — 删 profile 目录 + ProfileList 注册项).
_userenv: ctypes.WinDLL | None = None


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            f"win_setup 仅在 Windows 平台可用; 当前平台 {sys.platform!r}"
        )


def _get_netapi32() -> ctypes.WinDLL:
    global _netapi32
    if _netapi32 is None:
        _netapi32 = ctypes.WinDLL("netapi32", use_last_error=True)
        _netapi32.NetUserAdd.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        _netapi32.NetUserAdd.restype = wintypes.DWORD
        _netapi32.NetLocalGroupAddMembers.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD,
        ]
        _netapi32.NetLocalGroupAddMembers.restype = wintypes.DWORD
        _netapi32.NetLocalGroupAdd.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        _netapi32.NetLocalGroupAdd.restype = wintypes.DWORD
        _netapi32.NetUserGetInfo.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _netapi32.NetUserGetInfo.restype = wintypes.DWORD
        # NetUserSetInfo(servername, username, level, buf, parm_err): 重设用户属性 (含密码).
        _netapi32.NetUserSetInfo.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ]
        _netapi32.NetUserSetInfo.restype = wintypes.DWORD
        # NetUserDel(servername, username): 删用户 (uninstall).
        _netapi32.NetUserDel.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        _netapi32.NetUserDel.restype = wintypes.DWORD
        # NetLocalGroupDel(servername, groupname): 删本地组 (uninstall).
        _netapi32.NetLocalGroupDel.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        _netapi32.NetLocalGroupDel.restype = wintypes.DWORD
        _netapi32.NetApiBufferFree.argtypes = [ctypes.c_void_p]
        _netapi32.NetApiBufferFree.restype = wintypes.DWORD
    return _netapi32


def _get_advapi32() -> ctypes.WinDLL:
    global _advapi32
    if _advapi32 is None:
        _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        _advapi32.LookupAccountNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.LookupAccountNameW.restype = wintypes.BOOL
        _advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
        ]
        _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        _advapi32.RegCreateKeyExW.argtypes = [
            wintypes.HKEY, wintypes.LPCWSTR, wintypes.DWORD,
            wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.HKEY),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.RegCreateKeyExW.restype = wintypes.LONG
        _advapi32.RegSetValueExW.argtypes = [
            wintypes.HKEY, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD,
        ]
        _advapi32.RegSetValueExW.restype = wintypes.LONG
        _advapi32.RegQueryValueExW.argtypes = [
            wintypes.HKEY, wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        _advapi32.RegQueryValueExW.restype = wintypes.LONG
        _advapi32.RegCloseKey.argtypes = [wintypes.HKEY]
        _advapi32.RegCloseKey.restype = wintypes.LONG
    return _advapi32


def get_kernel32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _kernel32.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        _kernel32.CreateEventW.restype = wintypes.HANDLE
        _kernel32.OpenEventW.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        _kernel32.OpenEventW.restype = wintypes.HANDLE
        _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        _kernel32.SetEvent.restype = wintypes.BOOL
    return _kernel32


def _get_shell32() -> ctypes.WinDLL:
    global _shell32
    if _shell32 is None:
        _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        _shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.INT,
        ]
        _shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    return _shell32


def _get_userenv() -> ctypes.WinDLL:
    """userenv.dll 的 DeleteProfileW: 删 profile 目录 + ProfileList 注册项. 需 admin. """
    global _userenv
    if _userenv is None:
        _userenv = ctypes.WinDLL("userenv", use_last_error=True)
        _userenv.DeleteProfileW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        _userenv.DeleteProfileW.restype = wintypes.BOOL
    return _userenv


# ---------------------------------------------------------------------------
# 注册表读写.
# ---------------------------------------------------------------------------
HKEY_LOCAL_MACHINE = wintypes.HKEY(0x80000002)  # noqa: N816 - Win32 常量
# Win32 SAM 权限位: KEY_READ=0x20019, KEY_WRITE=0x20006, KEY_READ|KEY_WRITE=0x2001F.
# KEY_READ_ONLY 给 reg_get_str (普通用户可读已存在 key), KEY_READ_WRITE 给 _reg_open_create (写注册表, 需 admin).
KEY_READ_ONLY = 0x20019  # noqa: N816 - Win32 常量
KEY_READ_WRITE = 0x2001F  # noqa: N816 - Win32 常量
REG_SZ = 1  # noqa: N816 - Win32 常量
REG_DWORD = 4  # noqa: N816 - Win32 常量


def _reg_open_create() -> wintypes.HKEY:
    advapi32 = _get_advapi32()
    hkey = wintypes.HKEY()
    disp = wintypes.DWORD(0)
    ret = advapi32.RegCreateKeyExW(
        HKEY_LOCAL_MACHINE, const.REG_BASE_KEY, 0, None, 0,
        KEY_READ_WRITE, None, ctypes.byref(hkey), ctypes.byref(disp),
    )
    if ret != 0:
        raise ctypes.WinError(ret)
    return hkey


def _reg_set_str(name: str, value: str) -> None:
    advapi32 = _get_advapi32()
    hkey = _reg_open_create()
    try:
        buf = ctypes.create_unicode_buffer(value)
        ret = advapi32.RegSetValueExW(
            hkey, name, 0, REG_SZ, buf, (len(value) + 1) * 2,
        )
        if ret != 0:
            raise ctypes.WinError(ret)
    finally:
        advapi32.RegCloseKey(hkey)


def reg_get_str(name: str) -> str | None:
    advapi32 = _get_advapi32()
    hkey = wintypes.HKEY()
    ret = advapi32.RegOpenKeyExW(
        HKEY_LOCAL_MACHINE, const.REG_BASE_KEY, 0, KEY_READ_ONLY,
        ctypes.byref(hkey),
    )
    if ret != 0:
        # ERROR_FILE_NOT_FOUND (2) / ERROR_ACCESS_DENIED (5) 均视为"未读到".
        return None
    try:
        typ = wintypes.DWORD(0)
        # 两阶段读: 先查真实 size (返回 ERROR_MORE_DATA=234) 再分配 buffer.
        size = wintypes.DWORD(0)
        ret = advapi32.RegQueryValueExW(
            hkey, name, None, ctypes.byref(typ), None, ctypes.byref(size),
        )
        if ret != 0 and ret != 234:
            return None
        # size 是字节数 (含 NUL). REG_SZ 是 UTF-16, char 数 = size/2.
        char_count = max(1, size.value // 2 + 1)
        buf = ctypes.create_unicode_buffer(char_count)
        ret = advapi32.RegQueryValueExW(
            hkey, name, None, ctypes.byref(typ), buf, ctypes.byref(size),
        )
        if ret != 0:
            return None
        return buf.value
    finally:
        advapi32.RegCloseKey(hkey)


def _reg_set_dword_under(full_subkey: str, name: str, value: int) -> None:
    """在 HKLM\\<full_subkey> 下写一个 REG_DWORD (用于隐藏登录界面用户等).

    ``full_subkey`` 是相对 HKLM 的完整路径 (不拼 REG_BASE_KEY).
    """
    advapi32 = _get_advapi32()
    hkey = wintypes.HKEY()
    disp = wintypes.DWORD(0)
    ret = advapi32.RegCreateKeyExW(
        HKEY_LOCAL_MACHINE, full_subkey, 0, None, 0, KEY_READ_WRITE,
        None, ctypes.byref(hkey), ctypes.byref(disp),
    )
    if ret != 0:
        raise ctypes.WinError(ret)
    try:
        dword = wintypes.DWORD(value)
        advapi32.RegSetValueExW(
            hkey, name, 0, REG_DWORD, ctypes.byref(dword),
            ctypes.sizeof(wintypes.DWORD),
        )
    finally:
        advapi32.RegCloseKey(hkey)


# ---------------------------------------------------------------------------
# 用户/组创建.
# ---------------------------------------------------------------------------
class UserInfo1(ctypes.Structure):
    _fields_ = [
        ("usri1_name", wintypes.LPWSTR),
        ("usri1_password", wintypes.LPWSTR),
        ("usri1_password_age", wintypes.DWORD),
        ("usri1_priv", wintypes.DWORD),
        ("usri1_home_dir", wintypes.LPWSTR),
        ("usri1_comment", wintypes.LPWSTR),
        ("usri1_flags", wintypes.DWORD),
        ("usri1_script_path", wintypes.LPWSTR),
    ]


USER_PRIV_USER = 1  # noqa: N816 - Win32 常量


def _generate_password() -> str:
    """生成 jbx-sandbox 用户随机密码."""
    return secrets.token_urlsafe(48)


def _create_sandbox_user(password: str) -> bool:
    """创建 jbx-sandbox 本地用户 (幂等: 已存在则跳过)."""
    netapi32 = _get_netapi32()
    info = UserInfo1()
    info.usri1_name = const.SANDBOX_USER_NAME
    info.usri1_password = password
    info.usri1_password_age = 0
    info.usri1_priv = USER_PRIV_USER
    info.usri1_home_dir = None
    info.usri1_comment = "JiuwenBox sandbox user"
    info.usri1_flags = const.SANDBOX_USER_FLAGS
    info.usri1_script_path = None

    err = wintypes.DWORD(0)
    ret = netapi32.NetUserAdd(
        None, const.USER_INFO_1_LEVEL, ctypes.byref(info), ctypes.byref(err),
    )
    # NERR_UserExists = 2224 (lmerr.h).
    if ret == 0:
        logger.info("创建沙箱用户 %s 成功", const.SANDBOX_USER_NAME)
        return True
    elif ret == 2224:
        logger.info("沙箱用户 %s 已存在, 跳过 (不重设密码, 用 DPAPI 旧密码)", const.SANDBOX_USER_NAME)
        return False
    else:
        raise RuntimeError(
            f"NetUserAdd 失败: ret={ret} err={err.value}"
        )


def _set_user_password(user_name: str, password: str) -> None:
    """重设已有用户的密码 (NetUserSetInfo level=1)."""
    netapi32 = _get_netapi32()
    info = UserInfo1()
    info.usri1_name = user_name
    info.usri1_password = password
    info.usri1_password_age = 0
    info.usri1_priv = USER_PRIV_USER
    info.usri1_home_dir = None
    info.usri1_comment = "JiuwenBox sandbox user"
    info.usri1_flags = const.SANDBOX_USER_FLAGS
    info.usri1_script_path = None
    err = wintypes.DWORD(0)
    ret = netapi32.NetUserSetInfo(
        None, user_name, const.USER_INFO_1_LEVEL, ctypes.byref(info), ctypes.byref(err),
    )
    if ret != 0:
        raise RuntimeError(
            f"NetUserSetInfo 失败: ret={ret} err={err.value}"
        )


def _add_user_to_group() -> None:
    """把 jbx-sandbox 加入 jbx-sandbox-users 组 (幂等, 失败 raise)."""
    netapi32 = _get_netapi32()

    class LocalGroupInfo0(ctypes.Structure):
        _fields_ = [("lgrpi0_name", wintypes.LPWSTR)]

    grp_info = LocalGroupInfo0()
    grp_info.lgrpi0_name = const.SANDBOX_USER_GROUP
    ret = netapi32.NetLocalGroupAdd(
        None, 0, ctypes.byref(grp_info), None,
    )
    if ret == 0:
        logger.info("创建组 %s 成功", const.SANDBOX_USER_GROUP)
    elif ret in (2237, 1379):
        logger.info("组 %s 已存在, 跳过", const.SANDBOX_USER_GROUP)
    else:
        raise RuntimeError(f"NetLocalGroupAdd 失败 ret={ret}")

    class LocalGroupMembersInfo3(ctypes.Structure):  # noqa: E306 - Win32 结构体
        _fields_ = [("lgrpi3_domainandname", wintypes.LPWSTR)]

    member = LocalGroupMembersInfo3()
    member.lgrpi3_domainandname = const.SANDBOX_USER_NAME  # LPWSTR 直接赋 str
    ret = netapi32.NetLocalGroupAddMembers(
        None, const.SANDBOX_USER_GROUP, 3,
        ctypes.byref(member), 1,
    )
    # 0 = 成功; 1377 = ERROR_MEMBER_IN_ALIAS (已在组中, 幂等).
    if ret not in (0, 1377):
        raise RuntimeError(
            f"NetLocalGroupAddMembers 失败 ret={ret} (user={const.SANDBOX_USER_NAME} "
            f"group={const.SANDBOX_USER_GROUP})"
        )
    logger.info("用户 %s 已加入组 %s", const.SANDBOX_USER_NAME, const.SANDBOX_USER_GROUP)


def _lookup_user_sid(user_name: str) -> str:
    """LookupAccountName 取用户 SID 字符串."""
    advapi32 = _get_advapi32()
    # 先查长度.
    sid_buf = (ctypes.c_byte * 256)()
    sid_size = wintypes.DWORD(256)
    domain_buf = ctypes.create_unicode_buffer(256)
    domain_size = wintypes.DWORD(256)
    use = wintypes.DWORD(0)
    ok = advapi32.LookupAccountNameW(
        None, user_name, sid_buf, ctypes.byref(sid_size),
        domain_buf, ctypes.byref(domain_size), ctypes.byref(use),
    )
    if not ok:
        # 长度不够时重试.
        sid_buf = (ctypes.c_byte * sid_size.value)()
        domain_buf = ctypes.create_unicode_buffer(domain_size.value)
        ok = advapi32.LookupAccountNameW(
            None, user_name, sid_buf, ctypes.byref(sid_size),
            domain_buf, ctypes.byref(domain_size), ctypes.byref(use),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
    # 转 SID 字符串.
    sid_str = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid_buf, ctypes.byref(sid_str)):
        raise ctypes.WinError(ctypes.get_last_error())
    result = sid_str.value
    _free_sid_str(sid_str)
    return result


def _lookup_current_user_sid() -> str | None:
    """拿当前进程用户的 SID 字符串 (install 提权进程的身份)."""
    try:
        import getpass as _getpass
        return _lookup_user_sid(_getpass.getuser())
    except Exception:  # noqa: BLE001
        return None


def _free_sid_str(sid_str: wintypes.LPWSTR) -> None:
    kernel32 = get_kernel32()
    try:
        kernel32.LocalFree(sid_str)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        pass


def _is_admin() -> bool:
    """检测当前进程是否以管理员身份运行."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:  # noqa: BLE001
        return False


def _delete_profile_by_sid(sid_str: str) -> bool:
    """DeleteProfileW 按 SID 删用户 profile (目录树 + ProfileList 注册项). 需 admin. """
    if not sid_str:
        return False
    userenv = _get_userenv()
    ok = userenv.DeleteProfileW(sid_str, None, None)
    if ok:
        return True
    err = ctypes.get_last_error()
    if err == 2:
        return True
    logger.warning(
        "DeleteProfileW(sid=%s) 失败 last_error=%d (profile 可能正被占用; "
        "注册表项已清, 目录待重启后可手动删)", sid_str, err,
    )
    return False


def _purge_stale_profile_dirs() -> None:
    """清理 C:\\Users 下 jbx-sandbox* 历史残留 profile 目录."""
    import shutil

    def _rmtree_onerror(func, fpath, exc_info):
        try:
            os.chmod(fpath, 0o777)
            func(fpath)
        except OSError as exc:
            logger.debug("清理残留 profile: 跳过 %s (%s)", fpath, exc)

    users_root = os.environ.get("SystemDrive", "C:") + "\\Users"  # pylint: disable=use-system-path
    try:
        entries = os.listdir(users_root)
    except OSError as exc:
        logger.warning("列出 %s 失败, 跳过残留 profile 清理: %s", users_root, exc)
        return
    for name in entries:
        # 严格前缀匹配, 避免误删无关目录
        if name == const.SANDBOX_USER_NAME or name.startswith(
            const.SANDBOX_USER_NAME + "."
        ):
            path = os.path.join(users_root, name)
            try:
                shutil.rmtree(path, onerror=_rmtree_onerror)
                if os.path.isdir(path):
                    try:
                        os.rmdir(path)
                    except OSError:
                        logger.warning("残留 profile 目录 %s 删不尽 (含系统锁定子项如 WinX),  留待重启后删", path)
                        continue
                logger.info("删除残留 profile 目录 %s", path)
            except OSError as exc:
                # 正被占用 / 权限不足: 目录留待重启后删, 不阻断卸载.
                logger.warning("删除 %s 失败 (可能正被占用): %s", path, exc)


def _load_policy_preinstall_paths(policy_path: str) -> list[str]:
    """从 windows-policy.yaml 读 read_acl_preinstall + tool_paths, 返回去重后的

    预装路径列表 (含 git_dir 的 usr/bin, bin 子目录 + bash_path 父目录).
    """
    import yaml as _yaml
    from pathlib import Path as _Path
    import shutil as _shutil

    with open(policy_path, encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}
    win = (data.get("windows") or {})
    win_fs = (win.get("filesystem") or {})
    paths: list[str] = []

    # read_acl_preinstall.
    for p in win_fs.get("read_acl_preinstall") or []:
        if isinstance(p, str) and p:
            paths.append(p)

    # tool_paths (含自动探测空字段).
    tp = win_fs.get("tool_paths") or {}
    git_dir = (tp.get("git_dir") or "").strip()
    node_dir = (tp.get("node_dir") or "").strip()
    python_dir = (tp.get("python_dir") or "").strip()
    bash_path = (tp.get("bash_path") or "").strip()

    # 探测空字段 (对齐 policy_reader._resolve_tool_paths).
    if not python_dir:
        try:
            py_dir = str(_Path(sys.executable).parent.resolve())
            if _Path(py_dir, "python.exe").is_file():
                python_dir = py_dir
        except OSError:
            pass
    if not node_dir and python_dir:
        cand = _Path(python_dir).parent / "node"
        if (cand / "node.exe").is_file():
            node_dir = str(cand)
    if not git_dir:
        git_exe = _shutil.which("git")
        if git_exe:
            git_path = _Path(git_exe).resolve()
            for ancestor in (git_path.parent, *git_path.parents):
                if (ancestor / "usr" / "bin" / "bash.exe").is_file():
                    git_dir = str(ancestor)
                    if not bash_path:
                        bash_path = str(ancestor / "usr" / "bin" / "bash.exe")
                    break

    for v in (git_dir, node_dir, python_dir):
        if v:
            paths.append(v)
    if git_dir:
        paths.append(git_dir.rstrip("\\/").replace("/", "\\") + "\\usr\\bin") # pylint: disable=use-system-path
        paths.append(git_dir.rstrip("\\/").replace("/", "\\") + "\\bin") # pylint: disable=use-system-path
    if bash_path:
        paths.append(os.path.dirname(bash_path))

    # 去重保序.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _elevate_and_run_install(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
    policy_path: str | None = None,
) -> int:
    """通过 UAC 拉起提权子进程执行 install, 并同步阻塞等待其完成."""
    import json
    shell32 = _get_shell32()
    kernel32 = get_kernel32()
    py = (os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip() or sys.executable
    event_name = f"Global\\JiuwenBox-Install-Done-{secrets.token_hex(8)}"
    h_event = kernel32.CreateEventW(None, False, False, event_name)
    if not h_event:
        err = ctypes.get_last_error()
        raise RuntimeError(f"创建 install 同步 Event 失败 (CreateEventW WinError {err})")

    parts = ["-m", "jiuwenbox.supervisor.win_setup", const.INSTALL_SUBCOMMAND]
    if force:
        parts.append("--force")
    parts.append("--proxy-port-start")
    parts.append(str(proxy_port_start))
    parts.append("--proxy-port-end")
    parts.append(str(proxy_port_end))
    parts.append("--install-done-event")
    parts.append(event_name)
    if preinstall_paths:
        import base64
        encoded = base64.b64encode(
            json.dumps(preinstall_paths).encode("utf-8")
        ).decode("ascii")
        parts.append("--preinstall-paths")
        parts.append(encoded)
    if policy_path:
        parts.append("--policy-path")
        parts.append(policy_path)
    params = " ".join(_quote_arg(p) for p in parts)
    logger.info(
        "install 提权调用: py=%s event=%s cmd='%s %s'",
        py, event_name, py, params,
    )

    # ShellExecuteW(parent, verb, file, parameters, directory, show).
    sw_shown_normal = 1  # noqa: N806 - Win32 常量
    result = shell32.ShellExecuteW(None, "runas", py, params, None, sw_shown_normal)
    logger.info(
        "ShellExecuteW(runas) 返回 %s (>32=已发起 UAC, 不代表用户已点确认)",
        result,
    )
    if result <= 32:  # <= 32 表示失败.
        kernel32.CloseHandle(wintypes.HANDLE(h_event))
        err = ctypes.get_last_error()
        if err == 1223:  # ERROR_CANCELLED: 用户点了"否".
            raise RuntimeError(
                "UAC 提权被用户取消; 请重新创建沙箱并同意 UAC, 或以管理员身份"
                " 手动运行 'python -m jiuwenbox.supervisor.win_setup --install'"
            )
        raise RuntimeError(
            f"UAC 提权失败 (ShellExecuteW 返回 {result}, WinError {err}); 请以"
            f"管理员身份手动运行 'python -m jiuwenbox.supervisor.win_setup --install'"
        )

    wait_obj_0 = 0  # noqa: N806 - Win32 常量
    wait_timeout = 0x00000102  # noqa: N806 - Win32 常量
    wait_failed = 0xFFFFFFFF  # noqa: N806 - Win32 常量
    install_wait_timeout = 120_000  # noqa: N806 - Win32 常量
    logger.info("已通过 UAC 提权运行 install 子进程 (force=%s), "
                "阻塞等待完成 (超时 %ds)...", force, install_wait_timeout // 1000)
    try:
        wait_result = kernel32.WaitForSingleObject(
            wintypes.HANDLE(h_event), install_wait_timeout,
        )
        if wait_result == wait_failed:
            err = ctypes.get_last_error()
            raise RuntimeError(f"等待 install 子进程完成失败 (WaitForSingleObject WinError {err})")
        if wait_result == wait_timeout:
            logger.warning(
                "install 提权子进程 %ds 未完成 SetEvent (超时降级). install 可能仍在"
                "运行或已崩溃; 后续将按 installed 标记判断, 若失败请重试",
                install_wait_timeout // 1000,
            )
        elif wait_result != wait_obj_0:
            raise RuntimeError(
                f"等待 install 子进程返回意外值 {wait_result:#x}"
            )
        else:
            logger.info("install 提权子进程已 SetEvent, 视为完成")
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(h_event))
    return 0


def _quote_arg(arg: str) -> str:
    """参数含空格/制表符时用双引号包裹 (Windows 命令行规则)."""
    if " " in arg or "\t" in arg:
        return f'"{arg}"'
    return arg


# ---------------------------------------------------------------------------
# 读 ACL 预装 (异步后台线程).
# ---------------------------------------------------------------------------
def _preinstall_read_acl(paths: list[str], sid: str) -> None:
    """对指定路径列表递归施加 Allow Read ACE (合成/沙箱用户 SID). """
    import json
    from jiuwenbox.supervisor import win_acl
    # 读已完成进度, 跳过已完成路径.
    done: set[str] = set()
    raw_progress = reg_get_str(const.REG_VALUE_READ_ACL_PROGRESS)
    if raw_progress:
        try:
            done = set(json.loads(raw_progress))
        except (ValueError, TypeError):
            done = set()
    try:
        for path in paths:
            expanded = os.path.expandvars(path)
            if expanded in done:
                logger.debug("预装读 ACL: 已完成, 跳过 %s", expanded)
                continue
            if not os.path.exists(expanded):
                logger.debug("预装读 ACL: 路径不存在 %s", expanded)
                continue
            # Allow Read ACE 给沙箱用户 SID (使其能读这些目录).
            win_acl.grant_ace(
                expanded, sid,
                rights=const.FILE_GENERIC_READ,
                mode="ALLOW",
                recursive=True,
            )
            done.add(expanded)
            # 每完成一个路径写一次进度, 支持断点续传.
            _reg_set_str(
                const.REG_VALUE_READ_ACL_PROGRESS,
                json.dumps(sorted(done)),
            )
            logger.debug("预装读 ACL 完成: %s", expanded)
    except Exception:  # noqa: BLE001 - best-effort
        logger.warning("预装读 ACL 失败", exc_info=True)


def _preinstall_read_acl_async(paths: list[str], sid: str) -> threading.Thread:
    """后台线程预装读 ACL.

    返回线程对象供调用方 join. install() 作为提权子进程必须等预装完成再
    退出, 否则 daemon 线程被强杀, 预装只做一半.
    """
    thread = threading.Thread(
        target=_preinstall_read_acl,
        args=(paths, sid),
        name="jbx-read-acl-preinstall",
        daemon=True,
    )
    thread.start()
    logger.info("读 ACL 预装已在后台线程启动 (%d 路径)", len(paths))
    return thread


PREINSTALL_JOIN_TIMEOUT_SECONDS = 120.0  # noqa: N816 - Win32 常量


# ---------------------------------------------------------------------------
# 公开 API.
# ---------------------------------------------------------------------------
def install(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
    policy_path: str | None = None,
) -> None:
    """执行一次性安装 (需管理员权限).

    Args:
        force: 忽略幂等标记强制重装.
        preinstall_paths: 读 ACL 预装路径 (来自根 policy 的
            ``windows.filesystem.read_acl_preinstall``). 为 None 时用
            默认 4 个系统目录.
        proxy_port_start/end: WFP Permit filter 放行的 loopback 端口范围
            (来自根 policy 的 ``windows.proxy.port_range_*``). 必须与
            win_proxy 实际监听端口一致, 否则代理路径被 Block 拦截
            (review MAJOR #7: 旧版硬编码默认端口, 忽略 policy).
        policy_path: windows-policy.yaml 路径; install 时读其 read_acl_preinstall
            + tool_paths 合并进预装路径. 用户改 tool_paths 后 --force 重装用.
    """
    _require_windows()
    if policy_path:
        try:
            _pp = _load_policy_preinstall_paths(policy_path)
            if _pp:
                preinstall_paths = (preinstall_paths or []) + _pp
        except Exception as exc:  # noqa: BLE001
            logger.warning("install 读 policy 预装路径失败: %s", exc)
    if not _is_admin():
        _elevate_and_run_install(
            force=force,
            preinstall_paths=preinstall_paths,
            proxy_port_start=proxy_port_start,
            proxy_port_end=proxy_port_end,
            policy_path=policy_path,
        )
        return

    # 幂等检查.
    if not force and reg_get_str(const.REG_VALUE_INSTALLED) == "1":
        logger.info("Windows 沙箱已安装, 跳过 (force=True 可重装)")
        return

    logger.info("开始 Windows 沙箱安装...")

    steps_done: set[str] = set()
    try:
        # 1. 创建用户 + 组 (致命).
        new_password = _generate_password()
        created = _create_sandbox_user(new_password)
        if created:
            # 新建用户: 用本次随机密码存 DPAPI.
            password = new_password
            steps_done.add("user_created")  # 本次新建, 失败回滚删用户安全
        else:
            # 用户已存在: 不能用本次新密码 (与实际不一致 → 1326). 读 DPAPI 旧密码保持一致;
            password = get_sandbox_user_password()
            if not password:
                password = new_password
                _set_user_password(const.SANDBOX_USER_NAME, password)
                logger.info("jbx-sandbox DPAPI 无旧密码, 已重设为新随机密码")
            # 用户已存在 (force 重装): 失败回滚不删用户 (避免误删既有账户影响并发沙箱).
            steps_done.add("user_existed")
        _add_user_to_group()
        # 从登录界面隐藏 jbx-sandbox 用户 (非致命).
        try:
            _reg_set_dword_under(
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
                r"\SpecialAccounts\UserList",
                const.SANDBOX_USER_NAME,
                0,
            )
        except Exception:  # noqa: BLE001
            logger.warning("隐藏登录界面用户失败, 不影响功能", exc_info=True)

        # 2. 查 SID 并存注册表.
        sid = _lookup_user_sid(const.SANDBOX_USER_NAME)
        _reg_set_str(const.REG_VALUE_SANDBOX_USER_SID, sid)
        # 密码用 DPAPI 加密 (机器绑定) 存 HKLM 注册表, 重启后 get_sandbox_user_password 解密读回.
        try:
            import win32crypt  # type: ignore[import-not-found]
            enc = win32crypt.CryptProtectData(
                password.encode("utf-8"), "jbx-sandbox-pw", None, None, None, 0,
            )
            _reg_set_str(const.REG_VALUE_SANDBOX_USER_PW, enc.hex())
        except ImportError:  # pragma: no cover
            logger.warning("pywin32 缺失, 沙箱用户密码未加密存储 (仅开发环境)")

        # 合成 SID 缓存.
        from jiuwenbox.supervisor import win_acl
        synth_sid = win_acl.get_synthetic_write_sid()
        _reg_set_str(const.REG_VALUE_SYNTHETIC_WRITE_SID, synth_sid)

        # 3. 安装 WFP filter set (网络隔离). 失败则降级到防火墙规则
        network_isolation_ok = False
        wfp_exc = None
        try:
            from jiuwenbox.supervisor import win_wfp
            # reinstall 时 jbx-sandbox SID 可能变了 (RID 递增), 先卸载旧 filter
            try:
                win_wfp.uninstall_wfp_filters()
                logger.info("WFP 旧 filter 已卸载 (reinstall 更新 SID)")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "WFP 旧 filter 卸载异常, install 将复用/覆盖现有 sublayer",
                    exc_info=True,
                )
            win_wfp.install_wfp_filters(sid, proxy_port_start, proxy_port_end)
            network_isolation_ok = True
        except Exception as exc:  # noqa: BLE001
            wfp_exc = exc
            logger.warning("WFP filter 安装失败, 降级到 PowerShell 防火墙规则", exc_info=True,)
            try:
                from jiuwenbox.supervisor import win_wfp
                fallback_ok = win_wfp.install_firewall_rule_fallback(
                    const.SANDBOX_USER_NAME, proxy_port_start, proxy_port_end,
                    sandbox_user_sid=sid,
                )
            except Exception as fallback_exc:  # noqa: BLE001
                logger.error("防火墙规则降级也失败, 网络隔离不可用", exc_info=True,)
                raise RuntimeError(
                    "网络隔离不可用: WFP 主路径与 PowerShell 降级均失败"
                ) from (exc, fallback_exc)
            if not fallback_ok:
                raise RuntimeError(
                    "网络隔离不可用: WFP 主路径失败, PowerShell 降级部分失败"
                ) from exc
            network_isolation_ok = True
        if not network_isolation_ok:
            raise RuntimeError("网络隔离不可用 (未知原因)") from wfp_exc
        steps_done.add("wfp")  # 网络隔离已装 (WFP 或降级), 失败回滚需卸载

        # 4. 异步预装读 ACL
        if preinstall_paths:
            paths_to_preinstall = [
                os.path.expandvars(p) for p in preinstall_paths if p
            ]
        else:
            paths_to_preinstall = []
        try:
            from jiuwenbox.supervisor import win_acl as _wa, win_constants as _wc
            _office_claw_root = str(Path.home() / ".office-claw")
            os.makedirs(_office_claw_root, exist_ok=True)
            # 递归 grant Read (含 Execute): 合成 SID + 真实 sandbox 用户 SID 各一份.
            _wa.grant_ace(
                _office_claw_root, synth_sid,
                rights=_wc.FILE_GENERIC_READ, mode="ALLOW", recursive=True,
            )
            _wa.grant_ace(
                _office_claw_root, sid,
                rights=_wc.FILE_GENERIC_READ, mode="ALLOW", recursive=True,
            )
            logger.info("预装数据根递归 Read ACL: %s (Write 由运行时子树单独授权)", _office_claw_root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("预装数据根 ACL 失败 (非致命): %s", exc)

        # 打包工具目录 (tools/python, tools/node 等) owner 可能是 Administrators,
        # 运行时 box-server 进程 (普通用户) 对它们无 WRITE_DAC → apply_sandbox_acl
        # 施加 Allow Read ACE 时 WinError 5. install 阶段给当前用户授予改 DACL 所需
        # 最小权限 (READ_CONTROL 读 SD + WRITE_DAC 改 DACL), 让运行时能改这些目录的 DACL.
        _current_user_sid = _lookup_current_user_sid()
        if _current_user_sid:
            _bundled_py = (os.environ.get("JIUWENBOX_BUNDLED_PYTHON") or "").strip()
            _bundled_dirs: list[str] = []
            if _bundled_py:
                _bundled_dirs.append(_bundled_py)
            for _p in list(_bundled_dirs):
                if os.path.isdir(_p):
                    try:
                        _wa.grant_ace(
                            _p, _current_user_sid,
                            rights=_wc.WRITE_DAC | _wc.READ_CONTROL,
                            mode="ALLOW", recursive=True,
                        )
                        logger.info("grant WRITE_DAC|READ_CONTROL 给当前用户: %s", _p)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("grant WRITE_DAC 失败 (非致命): %s=%s", _p, exc)
        preinstall_thread = _preinstall_read_acl_async(paths_to_preinstall, sid)
        preinstall_thread.join(timeout=PREINSTALL_JOIN_TIMEOUT_SECONDS)
        if preinstall_thread.is_alive():
            logger.warning(
                "读 ACL 预装未在 %.0fs 内完成, 剩余路径将由后续创建沙箱补做",
                PREINSTALL_JOIN_TIMEOUT_SECONDS,
            )

        # 5. 全部致命步骤通过, 写完成标记; 清进度标记 (断点续传已无用)。
        _reg_set_str(const.REG_VALUE_INSTALLED, "1")
        _reg_set_str(const.REG_VALUE_READ_ACL_PROGRESS, "")
        import json as _json5
        _reg_set_str(
            const.REG_VALUE_PREINSTALLED_PATHS,
            _json5.dumps(sorted({os.path.expandvars(p) for p in paths_to_preinstall})),
        )
        logger.info("Windows 沙箱安装完成")
    except Exception:
        logger.error("install 失败, 执行局部回滚", exc_info=True)
        try:
            _install_rollback(steps_done)
        except Exception:  # noqa: BLE001 - 回滚 best-effort, 不掩盖原异常
            logger.error("install 失败后局部回滚也失败", exc_info=True)
        raise


def collect_preinstall_paths(policy) -> list[str]:
    """收集 install 该预装读 ACL 的完整路径集."""
    fs = policy.windows.filesystem
    paths: list[str] = list(fs.read_acl_preinstall or [])
    tp = fs.tool_paths
    # git_dir / node_dir / python_dir
    for i, attr in enumerate(("git_dir", "node_dir", "python_dir")):
        d = (getattr(tp, attr, "") or "").strip()
        if not d:
            continue
        if d not in paths:
            paths.append(d)
        # git 安装根含 usr/bin/bash.exe + bin, 子目录也纳入 (PATH + 读 ACL).
        if attr == "git_dir":
            for sub in (os.path.join(d, "usr", "bin"), os.path.join(d, "bin")):
                if sub not in paths:
                    paths.append(sub)
    # bash_path 的父目录 (git_dir 未覆盖时用)
    bash_p = (getattr(tp, "bash_path", "") or "").strip()
    if bash_p:
        parent = os.path.dirname(bash_p)
        if parent and parent not in paths:
            paths.append(parent)
    return paths


def ensure_windows_setup(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
    policy_path: str | None = None,
) -> None:
    """运行时入口: 确保安装已完成 (幂等).

    Args:
        preinstall_paths: 读 ACL 预装路径 (根 policy 的
            ``windows.filesystem.read_acl_preinstall``). 仅在首次安装时
            生效; 已安装则忽略.
        proxy_port_start/end: WFP Permit filter 放行的 loopback 端口范围
            (根 policy 的 ``windows.proxy.port_range_*``).
    """
    _require_windows()
    try:
        if not force and reg_get_str(const.REG_VALUE_INSTALLED) == "1":
            self_check_paths = {
                os.path.expandvars(p) for p in (preinstall_paths or []) if p
            }
            recorded_raw = reg_get_str(const.REG_VALUE_PREINSTALLED_PATHS)
            recorded: set[str] = set()
            if recorded_raw:
                try:
                    import json as _json_chk
                    recorded = set(_json_chk.loads(recorded_raw))
                except (ValueError, TypeError):
                    recorded = set()
            new_paths = self_check_paths - recorded
            if new_paths:
                logger.info(
                    "Windows 沙箱已安装, 但检测到新增预装路径未预装读 ACL: %s. "
                    "自动弹 UAC 提权补预装 (CreateProcessAsUserW 否则会 WinError 2/5).",
                    sorted(new_paths),
                )
                _elevate_and_run_install(
                    force=True,
                    preinstall_paths=sorted(new_paths),
                    proxy_port_start=proxy_port_start,
                    proxy_port_end=proxy_port_end,
                    policy_path=policy_path,
                )
            # 密码一致性验证: install 回滚不彻底时 jbx-sandbox 可能残留旧密码, 注册表密码与实际不一致 → 1326.
            # 幂等检查只看 installed=1 发现不了. 用 LogonUserW 测登录, 失败则自动重设密码 (创建者有权限). 避免反复 1326.
            _verify_or_reset_sandbox_user_password()
            return
        # installed != "1" 或 force=True: 执行安装 (内部会判 admin / UAC 提权).
        install(
            force=force,
            preinstall_paths=preinstall_paths,
            proxy_port_start=proxy_port_start,
            proxy_port_end=proxy_port_end,
            policy_path=policy_path,
        )
    except Exception:  # noqa: BLE001
        logger.error("ensure_windows_setup 失败", exc_info=True)
        raise


def _verify_or_reset_sandbox_user_password() -> None:
    """验证 jbx-sandbox 密码与注册表一致, 不一致则重设."""
    password = get_sandbox_user_password()
    if not password:
        return
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    token = ctypes.c_void_p()
    logon_provider_default = 0  # noqa: N806 - Win32 常量
    logon_network = 3  # noqa: N806 - Win32 常量
    logon_interactivate = 2  # noqa: N806 - Win32 常量
    ok = advapi32.LogonUserW(
        const.SANDBOX_USER_NAME, None, password,
        logon_network, logon_provider_default,
        ctypes.byref(token),
    )
    if not ok:
        network_err = ctypes.get_last_error()
        # network logon 被策略拒 (常见 WinError 1327/1385/1326): 回退交互式.
        logger.debug(
            "jbx-sandbox network logon 失败 (WinError %d), 回退 interactive 校验密码",
            network_err,
        )
        ok = advapi32.LogonUserW(
            const.SANDBOX_USER_NAME, None, password,
            logon_interactivate, logon_provider_default,
            ctypes.byref(token),
        )
    if ok:
        try:
            ctypes.WinDLL("kernel32").CloseHandle(token)
        except OSError:
            pass
        logger.debug("jbx-sandbox 密码一致性验证通过")
        return
    err = ctypes.get_last_error()
    # WinError 1326 = ERROR_LOGON_FAILURE (密码不一致), 1327 = 账户限制等.
    # 失败就重设密码 (NetUserSetInfo), 让其与注册表一致.
    logger.warning(
        "jbx-sandbox 密码验证失败 (LogonUserW WinError %d), 重设密码以对齐注册表",
        err,
    )
    try:
        _set_user_password(const.SANDBOX_USER_NAME, password)
        logger.info("jbx-sandbox 密码已重设, 与注册表一致")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "jbx-sandbox 密码重设失败 (运行时非管理员可能无权, 请以管理员运行 "
            "'python -m jiuwenbox.supervisor.win_setup --install --force'): %s",
            exc,
        )


def get_sandbox_user_sid() -> str | None:
    """从注册表读 jbx-sandbox 用户 SID."""
    _require_windows()
    return reg_get_str(const.REG_VALUE_SANDBOX_USER_SID)


def get_sandbox_user_password() -> str | None:
    """从注册表读 jbx-sandbox 用户密码 (DPAPI 解密)."""
    _require_windows()
    enc_hex = reg_get_str(const.REG_VALUE_SANDBOX_USER_PW)
    if not enc_hex:
        return None
    try:
        import win32crypt  # type: ignore[import-not-found]
        enc = bytes.fromhex(enc_hex)
        _, plain = win32crypt.CryptUnprotectData(enc, None, None, None, 0)
        return plain.decode("utf-8")
    except ImportError:  # pragma: no cover
        logger.warning("pywin32 缺失, 无法解密沙箱用户密码")
        return None
    except Exception:  # noqa: BLE001
        logger.warning("沙箱用户密码解密失败", exc_info=True)
        return None


def get_synthetic_write_sid() -> str | None:
    """从注册表读合成写 SID."""
    _require_windows()
    return reg_get_str(const.REG_VALUE_SYNTHETIC_WRITE_SID)


def _install_rollback(steps_done: "set[str]") -> None:
    """install 失败局部回滚: 只清本次已成功步骤新增的资源 (review #4).

    Args:
        steps_done: install 主体记录的已成功步骤集合, 含 "user" / "wfp".
    """
    _require_windows()
    if not _is_admin():
        logger.warning("_install_rollback 非管理员, 跳过 (steps_done=%s)", steps_done)
        return
    # 回滚 WFP filter + 降级防火墙规则 (枚举法, 不依赖端口范围).
    if "wfp" in steps_done:
        try:
            from jiuwenbox.supervisor import win_wfp
            win_wfp.uninstall_wfp_filters()
            win_wfp.uninstall_firewall_rule_fallback()
        except Exception:  # noqa: BLE001
            logger.warning("局部回滚卸载 WFP/防火墙失败", exc_info=True)
    # 回滚用户 + 组 (幂等: 不存在跳过).
    if "user_created" in steps_done:
        netapi32 = _get_netapi32()
        ret = netapi32.NetUserDel(None, const.SANDBOX_USER_NAME)
        if ret not in (0, 2221):  # 2221 = NERR_UserNotFound (幂等)
            logger.warning("局部回滚 NetUserDel 返回 %d (继续)", ret)
        else:
            logger.info("局部回滚删除沙箱用户 %s", const.SANDBOX_USER_NAME)
        ret = netapi32.NetLocalGroupDel(None, const.SANDBOX_USER_GROUP)
        if ret not in (0, 2220):  # 2220 = NERR_GroupNotFound (幂等)
            logger.warning("局部回滚 NetLocalGroupDel 返回 %d (继续)", ret)
        else:
            logger.info("局部回滚删除沙箱组 %s", const.SANDBOX_USER_GROUP)
    elif "user_existed" in steps_done:
        logger.info("局部回滚: 用户已存在 (force 重装), 保留用户不删")
    # 清 installed 标记 (失败时本就没写 1, 兜底清空).
    _reg_set_str(const.REG_VALUE_INSTALLED, "")
    logger.info("install 局部回滚完成 (steps_done=%s)", steps_done)


def uninstall() -> None:
    """卸载: 删除 WFP filter + profile + 用户 + 注册表标记 (管理员)."""
    _require_windows()
    if not _is_admin():
        _elevate_uninstall()
        return
    try:
        from jiuwenbox.supervisor import win_wfp
        win_wfp.uninstall_wfp_filters()
        win_wfp.uninstall_firewall_rule_fallback()
    except Exception:  # noqa: BLE001
        logger.warning("WFP/防火墙卸载失败", exc_info=True)
    sid_str = get_sandbox_user_sid()
    if not sid_str:
        try:
            sid_str = _lookup_user_sid(const.SANDBOX_USER_NAME)
        except Exception:  # noqa: BLE001
            sid_str = None
    if sid_str:
        _delete_profile_by_sid(sid_str)
    _purge_stale_profile_dirs()
    netapi32 = _get_netapi32()
    ret = netapi32.NetUserDel(None, const.SANDBOX_USER_NAME)
    if ret not in (0, 2221):
        logger.warning("NetUserDel 返回 %d (继续)", ret)
    else:
        logger.info("删除沙箱用户 %s", const.SANDBOX_USER_NAME)
    ret = netapi32.NetLocalGroupDel(None, const.SANDBOX_USER_GROUP)
    if ret not in (0, 2220):
        logger.warning("NetLocalGroupDel 返回 %d (继续)", ret)
    else:
        logger.info("删除沙箱组 %s", const.SANDBOX_USER_GROUP)
    _reg_set_str(const.REG_VALUE_INSTALLED, "")
    _reg_set_str(const.REG_VALUE_SANDBOX_USER_PW, "")
    _reg_set_str(const.REG_VALUE_SANDBOX_USER_SID, "")
    logger.info("Windows 沙箱卸载完成")


def _elevate_uninstall() -> None:
    shell32 = _get_shell32()
    py = sys.executable
    params = f'-m jiuwenbox.supervisor.win_setup {const.UNINSTALL_SUBCOMMAND}'
    result = shell32.ShellExecuteW(
        None, "runas", py, params, None, 1,
    )
    if result <= 32:
        raise RuntimeError(f"UAC 提权卸载失败 (返回 {result})")


def _make_install_done_notifier(event_name: str | None):
    """返回一个闭包: 调它则 SetEvent 通知主进程 install 已结束."""
    def _notify() -> None:
        if not event_name:
            return
        try:
            kernel32 = get_kernel32()
            synchronize = 0x00100000  # noqa: N806 - Win32 常量
            event_modify_state = 0x0002  # noqa: N806 - Win32 常量
            h = kernel32.OpenEventW(
                synchronize | event_modify_state, False, event_name,
            )
            if not h:
                logger.warning(
                    "install 子进程 OpenEventW(%s) 失败 (WinError %d); "
                    "主进程可能仍在阻塞等待, 将靠超时/installed标记自行判断",
                    event_name, ctypes.get_last_error(),
                )
                return
            try:
                kernel32.SetEvent(wintypes.HANDLE(h))
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(h))
        except Exception:  # noqa: BLE001
            logger.warning("install 完成通知失败", exc_info=True)

    return _notify


def _main(argv: list[str]) -> int:
    """CLI 入口: 接收 --install / --uninstall 及 install 的参数.

    install 支持的可选参数 (review MAJOR #9: 旧版不接, 导致 force/端口/
    preinstall 从命令行无法传入):
      --force                  强制重装
      --proxy-port-start N     WFP Permit 放行的 loopback 端口范围起点
      --proxy-port-end   N     范围终点
      --preinstall-paths JSON  读 ACL 预装路径列表 (JSON 编码字符串)
    """
    import argparse
    import json
    try:
        _install_log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "install_force.log",
        )
        _install_log_path = os.path.normpath(_install_log_path)
        _fh = logging.FileHandler(_install_log_path, mode="w", encoding="utf-8")
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        ))
        logging.getLogger().addHandler(_fh)
        logging.getLogger().setLevel(logging.DEBUG)
    except Exception:  # noqa: BLE001
        pass
    normalized = [a.lstrip("-") if a.startswith("--") and a.lstrip("-") in (
        const.INSTALL_SUBCOMMAND.lstrip("-"),
        const.UNINSTALL_SUBCOMMAND.lstrip("-"),
    ) else a for a in argv]
    parser = argparse.ArgumentParser(
        prog="jiuwenbox.supervisor.win_setup",
        description="Windows 沙箱一次性环境准备 (管理员)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_install = sub.add_parser(
        const.INSTALL_SUBCOMMAND.lstrip("-"), help="执行安装",
    )
    p_install.add_argument("--force", action="store_true", help="强制重装")
    p_install.add_argument(
        "--proxy-port-start", type=int,
        default=const.DEFAULT_PROXY_PORT_RANGE_START,
    )
    p_install.add_argument(
        "--proxy-port-end", type=int,
        default=const.DEFAULT_PROXY_PORT_RANGE_END,
    )
    p_install.add_argument(
        "--preinstall-paths", default=None,
        help="读 ACL 预装路径列表 (base64 编码的 JSON 字符串; base64 避开"
        "Windows 命令行引号/空格转义问题)",
    )
    p_install.add_argument(
        "--policy-path", default=None,
        help="windows-policy.yaml 路径; install 时读其 read_acl_preinstall + "
        "tool_paths 合并进预装路径 (用户改 tool_paths 后 --force 重装用)",
    )
    p_install.add_argument(
        "--install-done-event", default=None,
        help="命名 Event 名 (主进程预创建), install 跑完后 SetEvent 通知主进程"
        "解除阻塞; 不传则不通知 (手动 CLI 跑 install 时用)",
    )
    sub.add_parser(
        const.UNINSTALL_SUBCOMMAND.lstrip("-"), help="执行卸载",
    )
    args = parser.parse_args(normalized)

    if args.cmd == const.INSTALL_SUBCOMMAND.lstrip("-"):
        preinstall_paths = None
        if args.preinstall_paths:
            try:
                import base64
                decoded = base64.b64decode(args.preinstall_paths).decode("utf-8")
                preinstall_paths = json.loads(decoded)
            except Exception as exc:
                # print(f"--preinstall-paths 解析失败: {exc}")
                return 2
        _notify = _make_install_done_notifier(args.install_done_event)
        try:
            install(
                force=args.force,
                preinstall_paths=preinstall_paths,
                proxy_port_start=args.proxy_port_start,
                proxy_port_end=args.proxy_port_end,
                policy_path=args.policy_path,
            )
        except BaseException:
            _notify()
            raise
        else:
            _notify()
        return 0
    if args.cmd == const.UNINSTALL_SUBCOMMAND.lstrip("-"):
        uninstall()
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover - 模块入口
    raise SystemExit(_main(sys.argv[1:]))
