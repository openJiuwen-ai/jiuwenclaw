# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 文件系统 ACL 控制 (合成 SID + NTFS DACL).

对齐 docs/window沙箱.md 6.7:
  - 读控制: deny-then-allow (依赖安装阶段预装读 ACL; 本模块施加 Deny/Allow Read).
  - 写控制: allow-only (合成 SID JHXSandboxWrite 对 allow_write 授 Allow Write
    + Execute + Delete; 对 deny_write 施加 Deny Write ACE 精细化封锁).

实现通过 ``pywin32`` 的 ``win32security`` 操作 DACL. 所有 win32 调用延迟到
函数体内执行, 模块顶层不 import 任何 pywin32, 因此 Linux 下可 import (运行
时函数会因 ``import win32security`` 失败而抛出明确错误, 由上层 ``sys.platform``
守卫避免误触).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Literal

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const

configure_logging()
logger = logging.getLogger(__name__)


def _require_windows() -> None:
    """守卫: 非 Windows 平台直接抛错而非走到 win32 调用."""
    if sys.platform != "win32":
        raise RuntimeError(
            "win_acl 仅在 Windows 平台可用; 当前平台 "
            f"{sys.platform!r} 不支持 NTFS DACL 操作"
        )


def _ensure_pywin32():
    """惰性加载 pywin32.win32security, 失败时给出清晰错误."""
    try:
        import win32security  # type: ignore[import-not-found]
        import win32con  # type: ignore[import-not-found]
        import win32api  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 仅 Windows 缺包时触发
        raise RuntimeError(
            "pywin32 (win32security/win32con/win32api) 未安装, 无法操作 NTFS ACL; "
            "请先 pip install pywin32"
        ) from exc
    return win32security, win32con, win32api


def _resolve_sid(sid_str: str):
    """把 SID 字符串解析为 pywin32 SID 对象."""
    win32security, _, _ = _ensure_pywin32()
    return win32security.ConvertStringSidToSid(sid_str)


def get_synthetic_write_sid() -> str:
    """返回合成写权限 SID 字符串.

    SID 格式: S-1-5-21-<sub0>-<sub1>-<RID>. 固定 sub-authority 区段 +
    固定 RID, 不关联任何真实账户. 详见 docs/window沙箱.md 2.2.
    """
    sub_auths = "-".join(str(s) for s in const.SYNTHETIC_WRITE_SID_SUBAUTHS)
    return (
        f"{const.SYNTHETIC_WRITE_SID_PREFIX}-"
        f"{sub_auths}-{const.SYNTHETIC_WRITE_SID_RID}"
    )


def _parse_getace_tuple(ace_tuple: tuple) -> "tuple[int, int, int, object]":
    """解析 pywin32 ``PyACL.GetAce`` 的返回, 兼容不同版本/ACE 形态.

    实测 pywin32 (Python 3.13) 对普通目录 GetAce 返回 3 元组:
      ((ace_type, ace_flags), access_mask, sid)
    即首元素是 header 子元组 (ace_type, ace_flags), 而非裸 int.
    原实现假设 3 元组为 (access_mask, ace_flags, sid), 把 header 子元组
    当 access_mask → int(tuple) TypeError.

    兼容形态:
      - 3 元组 ((ace_type, ace_flags), access_mask, sid)  — 实测当前版本
      - 3 元组 (access_mask, ace_flags, sid)              — 旧版无 header 子元组
      - 4 元组 (ace_type, ace_flags, access_mask, sid)
      - 5 元组 (ace_type, ace_flags, ace_size, access_mask, sid)

    返回 (ace_type, ace_flags, access_mask, sid). 无法区分 Allow/Deny 时
    默认按 Allow 处理 (保守: 宁可多放行再靠 Deny 显式拒绝).
    """
    if len(ace_tuple) == 5:
        ace_type, ace_flags, _ace_size, access_mask, sid = ace_tuple
    elif len(ace_tuple) == 4:
        ace_type, ace_flags, access_mask, sid = ace_tuple
    elif len(ace_tuple) == 3:
        first, access_mask, sid = ace_tuple
        if isinstance(first, tuple):
            # 当前 pywin32: header 子元组 (ace_type, ace_flags).
            ace_type, ace_flags = first
        else:
            # 旧版: (access_mask, ace_flags, sid), 无 ace_type → 视为 Allow.
            ace_type = const.ACCESS_ALLOWED_ACE_TYPE
            ace_flags = access_mask
            access_mask = first
    else:
        ace_type = const.ACCESS_ALLOWED_ACE_TYPE
        ace_flags = 0
        access_mask = 0
        sid = None
    return int(ace_type), int(ace_flags), int(access_mask), sid


def _rebuild_acl_with_order(
    existing_dacl,
    new_ace: "tuple[int, int, int, object] | None",
) -> "object":
    """重建 ACL: 所有 Deny ACE 在前, Allow ACE 在后 (NTFS 显式 Deny 优先).

    现有 ACE 按类型分桶保留, 追加新 ACE 到对应桶, 再 Deny-then-Allow 串接.
    保留现有 ACE 的 flags/mask/sid 不变 (修正旧实现把 Deny ACE 当 Allow
    写回的 bug).
    """
    win32security, _, _ = _ensure_pywin32()
    deny_aces: list[tuple[int, int, object]] = []  # (flags, mask, sid)
    allow_aces: list[tuple[int, int, object]] = []
    if existing_dacl is not None:
        # GetAclSize() 返回 ACL 字节数 (非 ACE 个数), 88 字节 ACL 实际只 3 个 ACE,
        # range(88) → GetAce(3) pywintypes.error (87, 'GetAce', '参数错误').
        # 用 GetAceCount() 拿真实 ACE 个数.
        for i in range(existing_dacl.GetAceCount()):
            ace_type, ace_flags, access_mask, sid = _parse_getace_tuple(
                existing_dacl.GetAce(i),
            )
            if ace_type == const.ACCESS_DENIED_ACE_TYPE:
                deny_aces.append((ace_flags, access_mask, sid))
            else:
                allow_aces.append((ace_flags, access_mask, sid))
    if new_ace is not None:
        nt, nf, nm, ns = new_ace
        if nt == const.ACCESS_DENIED_ACE_TYPE:
            deny_aces.append((nf, nm, ns))
        else:
            allow_aces.append((nf, nm, ns))
    acl = win32security.ACL()
    for flags, mask, sid in deny_aces:
        # AddAccess{Denied,Allowed}AceEx 新版 pywin32 要 (revision, flags, mask, sid);
        # 旧版只收 (flags, mask, sid) 3 参. ACL_REVISION=2 (普通文件 ACE).
        acl.AddAccessDeniedAceEx(2, flags, mask, sid)
    for flags, mask, sid in allow_aces:
        acl.AddAccessAllowedAceEx(2, flags, mask, sid)
    return acl


def grant_ace(
    path: str,
    sid: str | object,
    *,
    rights: int,
    mode: Literal["ALLOW", "DENY"],
    recursive: bool = True,
) -> None:
    """对 ``path`` 施加一个 ACE.

    Args:
        path: 文件/目录绝对路径.
        sid: SID 字符串或 pywin32 SID 对象.
        rights: 访问掩码 (FILE_GENERIC_WRITE 等组合).
        mode: "ALLOW" -> ACCESS_ALLOWED_ACE_TYPE; "DENY" -> ACCESS_DENIED_ACE_TYPE.
        recursive: 目录时是否让子对象继承.
    """
    _require_windows()
    win32security, win32con, _ = _ensure_pywin32()

    if isinstance(sid, str):
        sid_obj = _resolve_sid(sid)
    else:
        sid_obj = sid

    ace_type = (
        const.ACCESS_ALLOWED_ACE_TYPE if mode == "ALLOW"
        else const.ACCESS_DENIED_ACE_TYPE
    )
    inherit_flags = (
        const.RECURSIVE_ACE_FLAGS if recursive else 0
    )

    # 读取现有 Security Descriptor 的 DACL.
    sd = win32security.GetNamedSecurityInfo(
        path,
        win32security.SE_FILE_OBJECT,  # SE_FILE_OBJECT 在 win32security (非 win32con)
        win32security.DACL_SECURITY_INFORMATION,
    )
    existing_dacl = sd.GetSecurityDescriptorDacl()

    # 重建 ACL: 现有 ACE 按类型保留 + 新 ACE 按 Deny-then-Allow 顺序串接,
    # 修正旧实现拷贝时把 Deny ACE 当 Allow 写回的 bug (文档 2.3: 显式 Deny 优先).
    acl = _rebuild_acl_with_order(
        existing_dacl,
        (ace_type, inherit_flags, rights, sid_obj),
    )

    # 不设 PROTECTED_DACL_SECURITY_INFORMATION: 旧版在 recursive 时总是切断
    # 继承, 把工作区从父目录继承链永久脱离, 且 revoke 时也不恢复, 导致用户
    # 自己的继承读写权限丢失 (review MAJOR #5). 现在保留继承, 仅在 DACL 上
    # 增删显式 ACE.
    flags = win32security.DACL_SECURITY_INFORMATION

    win32security.SetNamedSecurityInfo(
        path,
        win32security.SE_FILE_OBJECT,  # SE_FILE_OBJECT 在 win32security (非 win32con)
        flags,
        None,  # owner 不变
        None,  # group 不变
        acl,
        None,  # SACL 不变
    )
    logger.debug(
        "施加 ACE: path=%s mode=%s rights=0x%X recursive=%s",
        path, mode, rights, recursive,
    )


def apply_sandbox_acl(
    workspace: str,
    allow_write: list[str],
    deny_write: list[str],
    allow_read: list[str] | None = None,
    deny_read: list[str] | None = None,
    *,
    recursive: bool = True,
    sandbox_user_sid: str | None = None,
) -> list[str]:
    """对沙箱工作区施加文件 ACL.

    写控制 (allow-only):
      1. 对 allow_write 路径施加 Allow Write+Execute+Delete ACE (合成 SID).
      2. 对 deny_write 路径施加 Deny Write ACE (合成 SID), 在 allow 范围内精细化封锁.
    读控制 (deny-then-allow, 对齐 docs/window沙箱.md 6.7):
      3. 对 deny_read 路径施加 Deny Read ACE (合成 SID).
      4. 对 allow_read 路径施加 Allow Read ACE (合成 SID), 覆盖 deny.
      5. 若 allow_read 为空, 对 workspace 根施加 Allow Read ACE (合成 SID),
         使独立用户身份的沙箱至少能读自己工作区 (review MAJOR #4: 读控制
         之前完全缺失; Windows 独立用户默认读不了用户 profile, 靠 install
         预装补, 但 workspace 仍需显式 Allow Read).
      6. 若 sandbox_user_sid 给定, 对 allow_read 路径再给 jbx-sandbox 真实 SID
         grant Allow Read ACE. 第一跳 runner 进程是 jbx-sandbox 真实 SID 且
         token 未受限 (CreateProcessWithLogonW 拉起), 合成 SID 的 ACE 对它
         不生效, 必须真实 SID 的 ACE 才能让 runner 读到 venv python / DLL.

    Returns: 施加过 ACE 的顶层路径列表 (含 workspace + allow/deny 各项),
        供 revoke_sandbox_acl 按清单撤销 (旧版只扫 workspace 树, 漏掉系统
        路径上的 ACE, review MAJOR #6).
    """
    _require_windows()
    sid = get_synthetic_write_sid()
    allow_read = list(allow_read) if allow_read else []
    deny_read = list(deny_read) if deny_read else []

    applied: list[str] = []

    # --- 写控制 ---
    write_targets = list(allow_write) or [workspace]
    for path in write_targets:
        expanded = os.path.expandvars(path)
        if not os.path.exists(expanded):
            logger.warning("allow_write 路径不存在, 跳过 ACL: %s", expanded)
            continue
        grant_ace(
            expanded, sid,
            rights=const.ALLOW_WRITE_RIGHTS,
            mode="ALLOW",
            recursive=recursive,
        )
        # venv_dir (runner python 所在目录) 落在 allow_write, 第一跳 runner 用
        # jbx-sandbox 真实 SID token 读不了只授合成 SID 的路径 → CreateProcessWithLogonW
        # WinError 5. 给真实 SID 也 grant Read+Execute (FILE_GENERIC_READ 已含),
        # Write 仍只给合成 SID (受限 token 第二跳才写), 真实 SID 能读能执行不能写.
        if sandbox_user_sid:
            grant_ace(
                expanded, sandbox_user_sid,
                rights=const.FILE_GENERIC_READ,
                mode="ALLOW",
                recursive=recursive,
            )
        applied.append(expanded)
    for path in deny_write:
        expanded = os.path.expandvars(path)
        if not os.path.exists(expanded):
            logger.warning("deny_write 路径不存在, 跳过 ACL: %s", expanded)
            continue
        grant_ace(
            expanded, sid,
            rights=const.FILE_GENERIC_WRITE,
            mode="DENY",
            recursive=recursive,
        )
        applied.append(expanded)

    # --- 读控制: deny_read (先施加 Deny Read) ---
    for path in deny_read:
        expanded = os.path.expandvars(path)
        if not os.path.exists(expanded):
            logger.warning("deny_read 路径不存在, 跳过 ACL: %s", expanded)
            continue
        grant_ace(
            expanded, sid,
            rights=const.DENY_READ_RIGHTS,
            mode="DENY",
            recursive=recursive,
        )
        applied.append(expanded)

    # --- 读控制: allow_read (再施加 Allow Read 覆盖 deny) ---
    read_targets = allow_read
    if not read_targets:
        # workspace 默认: 独立用户沙箱至少能读自己工作区.
        read_targets = [workspace]
    for path in read_targets:
        expanded = os.path.expandvars(path)
        if not os.path.exists(expanded):
            logger.warning("allow_read 路径不存在, 跳过 ACL: %s", expanded)
            continue
        grant_ace(
            expanded, sid,
            rights=const.FILE_GENERIC_READ,
            mode="ALLOW",
            recursive=recursive,
        )
        # 第一跳 runner (jbx-sandbox 真实 SID, token 未受限) 读不了合成 SID
        # 授权的路径, 必须给真实 SID 也 grant Read (含 Execute, FILE_GENERIC_READ
        # 已含). 否则 runner 读不了 venv python 及其依赖 DLL, 起不来.
        if sandbox_user_sid:
            grant_ace(
                expanded, sandbox_user_sid,
                rights=const.FILE_GENERIC_READ,
                mode="ALLOW",
                recursive=recursive,
            )
        applied.append(expanded)

    logger.info(
        "施加沙箱 ACL 完成: workspace=%s allow_write=%d deny_write=%d "
        "allow_read=%d deny_read=%d",
        workspace, len(write_targets), len(deny_write),
        len(read_targets), len(deny_read),
    )
    # 去重保序 (同一路径可能同时出现在 allow_write 与 allow_read).
    seen: set[str] = set()
    unique: list[str] = []
    for p in applied:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def revoke_sandbox_acl(paths: list[str] | str) -> None:
    """撤销合成 SID 施加的所有 ACE.

    Args:
        paths: apply_sandbox_acl 返回的施加路径清单 (含 workspace + allow/
            deny 各项). 旧版只以 workspace 为根 rglob 扫描, 漏掉系统路径
            上预装的合成 SID ACE (review MAJOR #6). 现按清单逐路径递归撤销.

    兼容旧调用: 传单个字符串 (workspace) 时退化为只扫该路径树.
    """
    _require_windows()
    win32security, win32con, _ = _ensure_pywin32()
    sid_str = get_synthetic_write_sid()
    target_sid = _resolve_sid(sid_str)

    if isinstance(paths, str):
        root_list = [paths]
    else:
        root_list = list(paths)

    # 收集所有需清理的路径: 每个 root 本身 + (若是目录) 其下所有子项.
    paths_to_clean: list[str] = []
    seen: set[str] = set()
    for root_path in root_list:
        root = Path(os.path.expandvars(root_path))
        if not root.exists():
            continue
        for p in [str(root), *([str(e) for e in root.rglob("*")] if root.is_dir() else [])]:
            if p not in seen:
                seen.add(p)
                paths_to_clean.append(p)

    for path in paths_to_clean:
        try:
            sd = win32security.GetNamedSecurityInfo(
                path,
                win32security.SE_FILE_OBJECT,  # SE_FILE_OBJECT 在 win32security (非 win32con)
                win32security.DACL_SECURITY_INFORMATION,
            )
            existing_dacl = sd.GetSecurityDescriptorDacl()
            if existing_dacl is None:
                continue
            # 按类型分桶, 过滤掉合成 SID 的 ACE, Deny 在前 Allow 在后重建.
            deny_aces: list[tuple[int, int, object]] = []
            allow_aces: list[tuple[int, int, object]] = []
            removed = 0
            for i in range(existing_dacl.GetAceCount()):  # 见 _rebuild_acl_with_order 注释
                ace_type, ace_flags, ace_mask, ace_sid = _parse_getace_tuple(
                    existing_dacl.GetAce(i),
                )
                if win32security.EqualSid(ace_sid, target_sid):
                    removed += 1
                    continue
                if ace_type == const.ACCESS_DENIED_ACE_TYPE:
                    deny_aces.append((ace_flags, ace_mask, ace_sid))
                else:
                    allow_aces.append((ace_flags, ace_mask, ace_sid))
            if removed == 0:
                continue
            acl = win32security.ACL()
            for flags, mask, sid in deny_aces:
                acl.AddAccessDeniedAceEx(2, flags, mask, sid)  # ACL_REVISION=2, 见 _rebuild_acl_with_order
            for flags, mask, sid in allow_aces:
                acl.AddAccessAllowedAceEx(2, flags, mask, sid)
            # 不设 PROTECTED_DACL: 恢复继承, 不切断工作区继承链 (review MAJOR #5).
            win32security.SetNamedSecurityInfo(
                path,
                win32security.SE_FILE_OBJECT,  # SE_FILE_OBJECT 在 win32security (非 win32con)
                win32security.DACL_SECURITY_INFORMATION,
                None, None, acl, None,
            )
            logger.debug("revoke: 清理 %s 上 %d 个 ACE", path, removed)
        except Exception:  # noqa: BLE001 - ACL 清理是 best-effort
            logger.debug("revoke 单个路径失败: %s", path, exc_info=True)
    logger.info("撤销沙箱 ACL 完成: 清理路径数=%d", len(paths_to_clean))
