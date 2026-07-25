# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
r"""Windows 沙箱一次性环境准备.

对齐 docs/window沙箱.md 6.4:
  - 创建 jbx-sandbox 本地用户 + jbx-sandbox-users 组 (随机密码, 标记
    PASSWD_CANT_CHANGE|DONT_EXPIRE_PASSWD, 从登录界面隐藏).
  - LookupAccountName 取用户 SID, 写注册表供后续模块复用.
  - 安装 WFP filter set (win_wfp.install_wfp_filters).
  - 异步预装常用目录读 ACL (%USERPROFILE% / %SystemRoot% / Program Files /
    ProgramData) — 后台线程, 进度写注册表支持断点续传.

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
        # NetLocalGroupAdd(servername:LPCWSTR, level:DWORD, buf:LPBYTE, parm_err:LPDWORD)
        # 原代码 argtypes 错位 (把 level 标成 LPCWSTR, buf 标成 DWORD),
        # 调用时 int 0(level) 喂给 c_wchar_p → ctypes.ArgumentError.
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


def _get_kernel32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
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


# ---------------------------------------------------------------------------
# 注册表读写.
# ---------------------------------------------------------------------------
HKEY_LOCAL_MACHINE = wintypes.HKEY(0x80000002)
# Win32 SAM 权限位: KEY_READ=0x20019, KEY_WRITE=0x20006, KEY_READ|KEY_WRITE=0x2001F.
# 原代码误把 KEY_READ_WRITE 标为 0x20019 (实为只读 KEY_READ), 导致 _reg_set_str
# 用只读 hkey 调 RegSetValueExW → WinError 5 (即使 admin 也写不了). 拆成两个
# 常量: KEY_READ_ONLY 给 _reg_get_str 用 (普通用户能读 HKLM 已存在 key),
# KEY_READ_WRITE 给 _reg_open_create (写注册表, 需 admin).
KEY_READ_ONLY = 0x20019
KEY_READ_WRITE = 0x2001F
REG_SZ = 1
REG_DWORD = 4


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


def _reg_get_str(name: str) -> str | None:
    # 读操作只用 KEY_READ 打开已存在 key (RegOpenKeyEx, 不创建).
    # 原实现复用 _reg_open_create() 的 RegCreateKeyExW+KEY_READ_WRITE:
    # 普通用户对 HKLM 无写权限, 即使 key 已存在也被拒 (WinError 5),
    # 导致 ensure_windows_setup 第一步幂等检查就抛, install() 里的 UAC
    # 提权分支永远走不到. 改为只读打开: key 不存在/无权限均返回 None,
    # 让上层走 install() 的正常提权路径.
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
        # 两阶段读: 先用空 buffer 查真实 size (返回 ERROR_MORE_DATA=234),
        # 再按 size 分配 buffer. 原实现固定 512 chars (1024 bytes), DPAPI
        # 密码 blob 的 hex 串远超此长度 → RegQueryValueExW 返回 234, 代码
        # 误当"未读到"返回 None, 导致 get_sandbox_user_password 拿不到密码.
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
class _USER_INFO_1(ctypes.Structure):
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


USER_PRIV_USER = 1


def _generate_password() -> str:
    """生成随机强密码 (字母+数字+符号, 满足复杂度要求)."""
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(const.SANDBOX_USER_PASSWORD_LENGTH))


def _create_sandbox_user(password: str) -> None:
    """创建 jbx-sandbox 本地用户 (幂等: 已存在则跳过)."""
    netapi32 = _get_netapi32()
    info = _USER_INFO_1()
    # LPWSTR (c_wchar_p) 字段直接赋 str: ctypes 自动转为以 NUL 结尾的宽字符串
    # 指针并绑定到 info 的生命周期. 原实现用 create_unicode_buffer 返回
    # c_wchar_Array_N, 新版 Python (3.13) ctypes 严格类型检查拒绝数组→指针赋值
    # (TypeError: incompatible types, c_wchar_Array_N instead of c_wchar_p).
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
    # NERR_Success = 0; NERR_UserExists = 2224 (lmerr.h: 用户名已存在).
    # 原代码误写 2236, 导致幂等重装时遇到已存在用户被当失败 raise.
    if ret == 0:
        logger.info("创建沙箱用户 %s 成功", const.SANDBOX_USER_NAME)
    elif ret == 2224:
        logger.info("沙箱用户 %s 已存在, 跳过创建", const.SANDBOX_USER_NAME)
    else:
        raise RuntimeError(
            f"NetUserAdd 失败: ret={ret} err={err.value}"
        )


def _add_user_to_group() -> None:
    """把 jbx-sandbox 加入 jbx-sandbox-users 组 (幂等)."""
    netapi32 = _get_netapi32()
    # 先尝试建组 (已存在则忽略, 错误码 2237 = NERR_GroupExists).
    class _LOCALGROUP_INFO_0(ctypes.Structure):
        _fields_ = [("lgrpi0_name", wintypes.LPWSTR)]

    grp_info = _LOCALGROUP_INFO_0()
    grp_info.lgrpi0_name = const.SANDBOX_USER_GROUP  # LPWSTR 直接赋 str (见 _create_sandbox_user 注释)
    ret = netapi32.NetLocalGroupAdd(
        None, 0, ctypes.byref(grp_info), None,
    )
    if ret == 0:
        logger.info("创建组 %s 成功", const.SANDBOX_USER_GROUP)
    elif ret == 2237:
        logger.info("组 %s 已存在, 跳过", const.SANDBOX_USER_GROUP)
    else:
        logger.warning("NetLocalGroupAdd 返回 %d (继续)", ret)

    # 加入成员 (LOCALGROUP_MEMBERS_INFO_0 = { PSID }, 但用名字更简单 -> level 3).
    class _LOCALGROUP_MEMBERS_INFO_0(ctypes.Structure):
        _fields_ = [("lgrmi0_name", wintypes.LPWSTR)]

    member = _LOCALGROUP_MEMBERS_INFO_0()
    member.lgrmi0_name = const.SANDBOX_USER_NAME  # LPWSTR 直接赋 str
    ret = netapi32.NetLocalGroupAddMembers(
        None, const.SANDBOX_USER_GROUP, 0,
        ctypes.byref(member), 1,
    )
    # 0 = 成功; 1377 = ERROR_MEMBER_IN_ALIAS (已在组中).
    if ret not in (0, 1377):
        logger.warning("NetLocalGroupAddMembers 返回 %d", ret)


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


def _free_sid_str(sid_str: wintypes.LPWSTR) -> None:
    kernel32 = _get_kernel32()
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


def _elevate_and_run_install(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
) -> int:
    """通过 UAC 拉起提权子进程执行 install.

    转发 force / preinstall_paths / proxy_port_* 参数到提权子进程 (review
    MAJOR #9: 旧版只传 --install, force=True 从非管理员进程调用会静默 no-op).
    """
    import json
    shell32 = _get_shell32()
    py = sys.executable
    parts = [
        "-m", "jiuwenbox.supervisor.win_setup", const.INSTALL_SUBCOMMAND,
    ]
    if force:
        parts.append("--force")
    parts.append("--proxy-port-start")
    parts.append(str(proxy_port_start))
    parts.append("--proxy-port-end")
    parts.append(str(proxy_port_end))
    if preinstall_paths:
        # 用 JSON 编码列表传参, 子进程解码; 避免路径含空格/引号的转义问题.
        encoded = json.dumps(preinstall_paths)
        parts.append("--preinstall-paths")
        parts.append(encoded)
    # 用 subprocess.list2cmdline 风格构造参数串 (ShellExecuteW 接受单一 params 字符串).
    params = " ".join(_quote_arg(p) for p in parts)
    # ShellExecuteW(parent, verb, file, parameters, directory, show).
    SW_SHOWNORMAL = 1
    result = shell32.ShellExecuteW(
        None, "runas", py, params, None, SW_SHOWNORMAL,
    )
    if result <= 32:  # <= 32 表示失败.
        raise RuntimeError(
            f"UAC 提权失败 (ShellExecuteW 返回 {result}); 请以管理员身份手动运行 "
            f"'python -m jiuwenbox.supervisor.win_setup --install'"
        )
    logger.info("已通过 UAC 提权运行 install 子进程 (force=%s)", force)
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
    """对指定路径列表递归施加 Allow Read ACE (合成/沙箱用户 SID).

    best-effort: 单个路径失败不影响整体. 进度写注册表 (REG_VALUE_READ_ACL_PROGRESS,
    JSON 编码的已完成路径列表), 支持断点续传 (review MAJOR #8: 旧版从不写进度,
    install 被强杀后从头再来).
    """
    import json
    from jiuwenbox.supervisor import win_acl
    # 读已完成进度, 跳过已完成路径.
    done: set[str] = set()
    raw_progress = _reg_get_str(const.REG_VALUE_READ_ACL_PROGRESS)
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


# 预装读 ACL 的等待上限 (秒). 深度遍历大目录可能耗时, 但 install 作为
# 提权子进程不应无限挂起; 超时后写 installed 标记, 剩余路径由后续 ensure
# 补做 (grant_ace 幂等).
PREINSTALL_JOIN_TIMEOUT_SECONDS = 120.0


# ---------------------------------------------------------------------------
# 公开 API.
# ---------------------------------------------------------------------------
def install(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
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
    """
    _require_windows()
    if not _is_admin():
        _elevate_and_run_install(
            force=force,
            preinstall_paths=preinstall_paths,
            proxy_port_start=proxy_port_start,
            proxy_port_end=proxy_port_end,
        )
        return

    # 幂等检查.
    if not force and _reg_get_str(const.REG_VALUE_INSTALLED) == "1":
        logger.info("Windows 沙箱已安装, 跳过 (force=True 可重装)")
        return

    logger.info("开始 Windows 沙箱安装...")

    # 1. 创建用户 + 组.
    password = _generate_password()
    _create_sandbox_user(password)
    _add_user_to_group()
    # 从登录界面隐藏 jbx-sandbox 用户 (Winlogon SpecialAccounts\UserList=0).
    try:
        _reg_set_dword_under(
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
            r"\SpecialAccounts\UserList",
            const.SANDBOX_USER_NAME,
            0,
        )
    except Exception:  # noqa: BLE001 - 隐藏失败不阻断安装
        logger.warning("隐藏登录界面用户失败, 不影响功能", exc_info=True)

    # 2. 查 SID 并存注册表.
    sid = _lookup_user_sid(const.SANDBOX_USER_NAME)
    _reg_set_str(const.REG_VALUE_SANDBOX_USER_SID, sid)
    # 密码用 DPAPI 加密存储 (机器范围). 简化: 用 win32crypt.
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

    # 3. 安装 WFP filter set (网络隔离). 失败则降级到防火墙规则.
    # 使用调用方传入的 policy 端口范围 (M7), 不再硬编码默认端口.
    try:
        from jiuwenbox.supervisor import win_wfp
        win_wfp.install_wfp_filters(sid, proxy_port_start, proxy_port_end)
    except Exception:  # noqa: BLE001
        logger.warning(
            "WFP filter 安装失败, 降级到 PowerShell 防火墙规则", exc_info=True,
        )
        try:
            from jiuwenbox.supervisor import win_wfp
            win_wfp.install_firewall_rule_fallback(
                const.SANDBOX_USER_NAME, proxy_port_start, proxy_port_end,
            )
        except Exception:  # noqa: BLE001
            logger.error("防火墙规则降级也失败, 网络隔离不可用", exc_info=True)

    # 4. 异步预装读 ACL. 路径优先取 policy 的 read_acl_preinstall,
    # 否则用默认系统目录. install 是提权子进程, 必须等预装完成再退出,
    # 否则 daemon 线程被强杀 (文档 6.4.3: 后台线程异步执行, 但 install
    # 子进程本身要活到预装结束).
    if preinstall_paths:
        paths_to_preinstall = [
            os.path.expandvars(p) for p in preinstall_paths if p
        ]
    else:
        paths_to_preinstall = [
            os.environ.get("USERPROFILE", ""),
            os.environ.get("SystemRoot", r"C:\Windows"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramData", r"C:\ProgramData"),
        ]
        paths_to_preinstall = [p for p in paths_to_preinstall if p]
    preinstall_thread = _preinstall_read_acl_async(paths_to_preinstall, sid)
    preinstall_thread.join(timeout=PREINSTALL_JOIN_TIMEOUT_SECONDS)
    if preinstall_thread.is_alive():
        logger.warning(
            "读 ACL 预装未在 %.0fs 内完成, 剩余路径将由后续创建沙箱补做",
            PREINSTALL_JOIN_TIMEOUT_SECONDS,
        )

    # 5. 写完成标记; 全部预装成功则清进度标记 (断点续传已无用).
    _reg_set_str(const.REG_VALUE_INSTALLED, "1")
    _reg_set_str(const.REG_VALUE_READ_ACL_PROGRESS, "")
    logger.info("Windows 沙箱安装完成")


def ensure_windows_setup(
    force: bool = False,
    preinstall_paths: list[str] | None = None,
    proxy_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    proxy_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
) -> None:
    """运行时入口: 确保安装已完成 (幂等).

    由 ProcessRuntime.create / app.py lifespan 在 win32 分支调用.
    非管理员进程时, 会通过 UAC 拉起提权子进程完成首次安装.

    Args:
        preinstall_paths: 读 ACL 预装路径 (根 policy 的
            ``windows.filesystem.read_acl_preinstall``). 仅在首次安装时
            生效; 已安装则忽略.
        proxy_port_start/end: WFP Permit filter 放行的 loopback 端口范围
            (根 policy 的 ``windows.proxy.port_range_*``).
    """
    _require_windows()
    try:
        if not force and _reg_get_str(const.REG_VALUE_INSTALLED) == "1":
            return
        install(
            force=force,
            preinstall_paths=preinstall_paths,
            proxy_port_start=proxy_port_start,
            proxy_port_end=proxy_port_end,
        )
    except Exception:  # noqa: BLE001
        logger.error("ensure_windows_setup 失败", exc_info=True)
        raise


def get_sandbox_user_sid() -> str | None:
    """从注册表读 jbx-sandbox 用户 SID."""
    _require_windows()
    return _reg_get_str(const.REG_VALUE_SANDBOX_USER_SID)


def get_sandbox_user_password() -> str | None:
    """从注册表读 jbx-sandbox 用户密码 (DPAPI 解密)."""
    _require_windows()
    enc_hex = _reg_get_str(const.REG_VALUE_SANDBOX_USER_PW)
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
    return _reg_get_str(const.REG_VALUE_SYNTHETIC_WRITE_SID)


def uninstall() -> None:
    """卸载: 删除 WFP filter + 用户 + 注册表标记 (管理员)."""
    _require_windows()
    if not _is_admin():
        _elevate_uninstall()
        return
    try:
        from jiuwenbox.supervisor import win_wfp
        win_wfp.uninstall_wfp_filters()
    except Exception:  # noqa: BLE001
        logger.warning("WFP 卸载失败", exc_info=True)
    # 删用户/组略 (保留账户避免残留密码); 仅清注册表标记.
    _reg_set_str(const.REG_VALUE_INSTALLED, "")
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
    # argparse 子命令不能带 "--" 前缀; 调用方 (UAC 提权) 用的是
    # "--install"/"--uninstall", 这里规整为 "install"/"uninstall".
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
        help="读 ACL 预装路径列表 (JSON 编码字符串)",
    )
    sub.add_parser(
        const.UNINSTALL_SUBCOMMAND.lstrip("-"), help="执行卸载",
    )
    args = parser.parse_args(normalized)

    if args.cmd == const.INSTALL_SUBCOMMAND.lstrip("-"):
        preinstall_paths = None
        if args.preinstall_paths:
            try:
                preinstall_paths = json.loads(args.preinstall_paths)
            except (ValueError, TypeError) as exc:
                print(f"--preinstall-paths 解析失败: {exc}")
                return 2
        install(
            force=args.force,
            preinstall_paths=preinstall_paths,
            proxy_port_start=args.proxy_port_start,
            proxy_port_end=args.proxy_port_end,
        )
        return 0
    if args.cmd == const.UNINSTALL_SUBCOMMAND.lstrip("-"):
        uninstall()
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover - 模块入口
    raise SystemExit(_main(sys.argv[1:]))
