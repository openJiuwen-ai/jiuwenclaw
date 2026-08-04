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
from jiuwenbox.server.workspace import (
    JIUWENBOX_HOME,
    JIUWENCLAW_DATA_DIR_PATH,
    OFFICE_CLAW_DATA_ROOT,
)

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
    """解析 pywin32 PyACL.GetAce 返回, 兼容 3/4/5 元组形态.

    返回 (ace_type, ace_flags, access_mask, sid). 无法区分 Allow/Deny 时默认 Allow.
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


def _sid_dedup_key(sid) -> str:
    """把 pywin32 SID 对象转成稳定字符串, 供 ACE 去重比较 (SDDL 形式)."""
    win32security, _, _ = _ensure_pywin32()
    try:
        return win32security.ConvertSidToStringSid(sid)
    except Exception:  # noqa: BLE001 - 回退 repr, 去重降级 best-effort
        return repr(sid)


def _rebuild_acl_with_order(
    existing_dacl,
    new_ace: "tuple[int, int, int, object] | None",
) -> "object":
    """重建 ACL: Deny ACE 在前, Allow 在后 (NTFS 显式 Deny 优先).

    追加 new_ace 前按 (sid, type, mask, flags) 去重, 避免重复施加相同 ACE 致
    DACL 膨胀 (review #5: 旧版每次建沙箱对 ~/.office-claw 递归 grant Read 各
    追加一份, N 次循环后单文件 N 条重复 ACE).
    """
    win32security, _, _ = _ensure_pywin32()
    deny_aces: list[tuple[int, int, object]] = []  # (flags, mask, sid)
    allow_aces: list[tuple[int, int, object]] = []
    seen: set[tuple[str, int, int, int]] = set()  # (sid_key, ace_type, mask, flags)

    def _add(ace_type: int, flags: int, mask: int, sid,
             bucket: "list[tuple[int, int, object]]") -> None:
        key = (_sid_dedup_key(sid), ace_type, int(mask), int(flags))
        if key in seen:
            return  # 已存在相同 ACE, 跳过 (去重).
        seen.add(key)
        bucket.append((flags, mask, sid))

    if existing_dacl is not None:
        # GetAclSize() 返回字节数非 ACE 个数, 用 GetAceCount() 拿真实个数.
        # 同时对 existing_dacl 内部已有重复 ACE 也去重 (重建只保留一份).
        for i in range(existing_dacl.GetAceCount()):
            ace_type, ace_flags, access_mask, sid = _parse_getace_tuple(
                existing_dacl.GetAce(i),
            )
            if ace_type == const.ACCESS_DENIED_ACE_TYPE:
                _add(ace_type, ace_flags, access_mask, sid, deny_aces)
            else:
                _add(ace_type, ace_flags, access_mask, sid, allow_aces)
    if new_ace is not None:
        nt, nf, nm, ns = new_ace
        if nt == const.ACCESS_DENIED_ACE_TYPE:
            _add(nt, nf, nm, ns, deny_aces)
        else:
            _add(nt, nf, nm, ns, allow_aces)
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

    # 不设 PROTECTED_DACL_SECURITY_INFORMATION: 旧版 recursive 时切断继承且 revoke 不恢复, 致用户继承读写权限丢失 (review MAJOR #5).
    # 现保留继承, 仅在 DACL 上增删显式 ACE.
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


def grant_read_ace(
    path: str,
    sid: str | object,
    *,
    recursive: bool = True,
) -> None:
    """施加 Allow Read ACE (合成 SID 或真实 SID).

    对齐 docs/window沙箱.md 6.7 读控制 allow: 用 FILE_GENERIC_READ
    (含 Read + Execute). deny-then-allow 模型里由 apply_sandbox_acl
    控制 deny 在前 allow 在后的施加顺序, 本函数只负责单条 ACE.
    """
    grant_ace(
        path, sid,
        rights=const.FILE_GENERIC_READ,
        mode="ALLOW",
        recursive=recursive,
    )


def deny_read_ace(
    path: str,
    sid: str | object,
    *,
    recursive: bool = True,
) -> None:
    """施加 Deny Read ACE (合成 SID).

    对齐 docs/window沙箱.md 6.7 读控制 deny: 用 FILE_GENERIC_READ
    (NTFS Deny 优先于 Allow, 显式 deny_read 路径将被沙箱进程读不了).
    """
    grant_ace(
        path, sid,
        rights=const.FILE_GENERIC_READ,
        mode="DENY",
        recursive=recursive,
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
    """对沙箱工作区施加文件 ACL (写控制 allow-only + 读控制 deny-then-allow).

    Returns: 施加过 ACE 的顶层路径列表, 供 revoke_sandbox_acl 按清单撤销.
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
        # runner 是 jbx-sandbox 真实 SID + token 未受限, 合成 SID 的 ACE 对它不生效.
        # runner 负责 upload 文件进沙箱 (写操作), 须给真实 SID grant Write 才能写.
        # runner 是 box-server 起的可信代理, 给写权限符合预期.
        if sandbox_user_sid:
            grant_ace(
                expanded, sandbox_user_sid,
                rights=const.ALLOW_WRITE_RIGHTS,
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
        # child 用 jbx-sandbox 真实 SID + token 未受限 (受限 token 弃用, 0xC0000142),
        # 合成 SID 的 Deny 对 child 不生效, 须给真实 SID 也加 Deny, 否则 deny_write (.git/.env) 被 child 绕过 (P0-3).
        # Deny ACE 残留无害 (NTFS Deny 优先, 幂等施加).
        if sandbox_user_sid:
            grant_ace(
                expanded, sandbox_user_sid,
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
        # runner (jbx-sandbox 真实 SID, token 未受限) 读不了合成 SID 授权路径,
        # 须给真实 SID 也 grant Read (含 Execute), 否则 runner 读不了 venv python/DLL.
        if sandbox_user_sid:
            grant_ace(
                expanded, sandbox_user_sid,
                rights=const.FILE_GENERIC_READ,
                mode="ALLOW",
                recursive=recursive,
            )
        applied.append(expanded)

    # 数据根 traverse (非递归 Allow Read): child 访问 workspace/venv/产物时路径上每级父目录需 traverse,
    # 残缺会 lstat EPERM 导致 playwright install 失败. 非递归 (只目录本身) 避免跨沙箱读其他 workspace.
    # 非递归 ACE 不进 applied 清单 (revoke 会 rglob 递归扫误删其他沙箱 ACE). traverse read 残留无害.
    _traverse_roots: list[Path] = []
    for _root in (OFFICE_CLAW_DATA_ROOT, JIUWENCLAW_DATA_DIR_PATH, JIUWENBOX_HOME):
        if _root and _root not in _traverse_roots and os.path.isdir(str(_root)):
            _traverse_roots.append(_root)
    for _root in _traverse_roots:
        grant_ace(
            str(_root), sid,
            rights=const.FILE_GENERIC_READ,
            mode="ALLOW",
            recursive=False,
        )
        if sandbox_user_sid:
            grant_ace(
                str(_root), sandbox_user_sid,
                rights=const.FILE_GENERIC_READ,
                mode="ALLOW",
                recursive=False,
            )
    if _traverse_roots:
        logger.info(
            "施加数据根 traverse: roots=%s (非递归, 不进 revoke 清单)",
            [str(r) for r in _traverse_roots],
        )

    # ~/.office-claw 数据根的递归 Read ACL 不在此每次建沙箱补授:
    # review #5 — 旧版每次建沙箱递归 grant Read 一份, revoke 不撤销,
    # N 次循环后单文件 N 条重复 ACE 致 DACL 膨胀. 改由 win_setup.install
    # install 时对整树递归 grant 一次 (合成 SID + 真实 SID 各一份, 与
    # jbx-sandbox 用户生命周期绑定, reinstall 不变). 此处 traverse ACE
    # (非递归, 不膨胀) 仍每次施加, 让 child 能 traverse 数据根.

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
