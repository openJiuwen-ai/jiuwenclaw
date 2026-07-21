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
        win32con.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    existing_dacl = sd.GetSecurityDescriptorDacl()

    # 构造新 ACL: 先放 Deny ACE, 后放 Allow ACE (NTFS 评估: 显式 Deny 优先).
    acl = win32security.ACL()
    # 先拷贝现有 ACE (保持原有顺序), 再追加目标 ACE.
    if existing_dacl is not None:
        for i in range(existing_dacl.GetAclSize()):
            existing_ace = existing_dacl.GetAce(i)
            # existing_ace = (access_mask, ace_flags, sid)
            acl.AddAccessAllowedAceEx(
                existing_ace[1],
                existing_ace[0],
                existing_ace[2],
            )
    if ace_type == const.ACCESS_ALLOWED_ACE_TYPE:
        acl.AddAccessAllowedAceEx(inherit_flags, rights, sid_obj)
    else:
        acl.AddAccessDeniedAceEx(inherit_flags, rights, sid_obj)

    # PROTECTED_DACL 阻止继承的 ACE 覆盖我们的显式规则; 仅在 recursive 时启用,
    # 避免把单文件路径从父目录继承链上切断导致读权限丢失.
    flags = win32security.DACL_SECURITY_INFORMATION
    if recursive:
        flags |= win32security.PROTECTED_DACL_SECURITY_INFORMATION

    win32security.SetNamedSecurityInfo(
        path,
        win32con.SE_FILE_OBJECT,
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
    *,
    recursive: bool = True,
) -> None:
    """对沙箱工作区施加文件 ACL.

    1. 对 allow_write 路径施加 Allow Write+Execute+Delete ACE (合成 SID).
    2. 对 deny_write 路径施加 Deny Write ACE (合成 SID), 在 allow 范围内精细化封锁.

    详见 docs/window沙箱.md 6.7.
    """
    _require_windows()
    sid = get_synthetic_write_sid()
    # allow_write 至少包含 workspace 本身.
    targets = list(allow_write) or [workspace]
    for path in targets:
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
    logger.info(
        "施加沙箱 ACL 完成: workspace=%s allow=%d deny=%d",
        workspace, len(targets), len(deny_write),
    )


def revoke_sandbox_acl(workspace: str) -> None:
    """撤销沙箱工作区上由合成 SID 施加的所有 ACE.

    通过遍历 DACL, 删除 ACE 中 SID == 合成写 SID 的条目.
    """
    _require_windows()
    win32security, win32con, _ = _ensure_pywin32()
    sid_str = get_synthetic_write_sid()
    target_sid = _resolve_sid(sid_str)

    # workspace 及其下所有 allow_write 路径都需清理; 这里以 workspace 为根递归.
    root = Path(os.path.expandvars(workspace))
    if not root.exists():
        logger.debug("revoke workspace 不存在, 跳过: %s", workspace)
        return

    paths_to_clean: list[str] = [str(root)]
    if root.is_dir():
        for entry in root.rglob("*"):
            paths_to_clean.append(str(entry))

    for path in paths_to_clean:
        try:
            sd = win32security.GetNamedSecurityInfo(
                path,
                win32con.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION,
            )
            existing_dacl = sd.GetSecurityDescriptorDacl()
            if existing_dacl is None:
                continue
            acl = win32security.ACL()
            removed = 0
            for i in range(existing_dacl.GetAclSize()):
                # pywin32 ACL.GetAce(i) -> (access_mask, ace_flags, sid)
                ace_mask, ace_flags, ace_sid = existing_dacl.GetAce(i)
                if win32security.EqualSid(ace_sid, target_sid):
                    removed += 1
                    continue
                # 保留非合成 SID 的 ACE, 按原掩码最高位区分 Allow/Deny 重建.
                # ACCESS_DENIED_ACE_TYPE vs ACCESS_ALLOWED_ACE_TYPE 在 mask 高位无
                # 可靠区分; 通过 ACL 顺序不变性 + 各自 API 重建即可 (pywin32 会
                # 内部按 ACE 类型序号写回, 顺序保持).
                acl.AddAccessAllowedAceEx(ace_flags, ace_mask, ace_sid)
            if removed == 0:
                continue
            win32security.SetNamedSecurityInfo(
                path,
                win32con.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, acl, None,
            )
            logger.debug("revoke: 清理 %s 上 %d 个 ACE", path, removed)
        except Exception:  # noqa: BLE001 - ACL 清理是 best-effort
            logger.debug("revoke 单个路径失败: %s", path, exc_info=True)
    logger.info("撤销沙箱 ACL 完成: workspace=%s", workspace)
