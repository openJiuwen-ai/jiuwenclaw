# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Server-managed workspace paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform != "win32":
    import pwd


def _effective_user_home() -> Path:
    """Return the effective user's home directory without trusting $HOME."""
    if sys.platform == "win32":
        # Windows 无 pwd/geteuid, 直接用 Path.home() (走 USERPROFILE/USERDOMAIN).
        return Path.home()
    try:
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except KeyError:
        return Path.home()


# Windows: JIUWENBOX_HOME 与 agent-server (jiuwenclaw) 同根, 放在其
# <workspace>/jiuwenbox 下. agent-server (或上游产品如 office-claw) 启动时设
# JIUWENCLAW_DATA_DIR (~/.office-claw/.jiuwenclaw), box-server 是 agent-server
# 拉起的子进程, 继承该 env. 同根保证 box-server (liubuyu) 天然是这些目录
# owner → 改 DACL 不会 WinError 5, 子目录继承 ACL 顺. 旧版用 ~/.jiuwenbox 时
# 该目录 owner 可能非当前用户或 ACL 被 revoke 残留, 导致 upload/list 频繁
# Permission denied. 所有 JIUWENBOX_HOME 引用 (policies/sandboxes/logs/workspace)
# 统一走新根, 不再生成 ~/.jiuwenbox. Linux 不变 (~/.jiuwenbox).
#
# OFFICE_CLAW_DATA_ROOT: 上游产品 (relay-claw / office-claw) 的数据根
# (~/.office-claw), 与 relay-claw 的 officeClawDataDirEnv 同算法
# (env OFFICE_CLAW_DATA_DIR > fallback ~/.office-claw). box 优先读 env
# (上游若注入则用之), 否则 fallback _effective_user_home()/.office-claw.
# 沙箱 workspace / isolation_venv / 业务产物都在该根的子树
# (.office-claw/.jiuwenclaw/...), 受限 token 访问子路径需该根 traverse,
# 但该根默认 ACL 不含 jbx-sandbox/合成 SID → lstat EPERM (实测 npx/npm
# lstat 'C:\Users\liubuyu\.office-claw'). apply_sandbox_acl 对该根施加非递归
# traverse read 解决 (非递归避免跨沙箱 workspace 泄露). 见 win_acl.py.
if sys.platform == "win32":
    _win_root_env = os.environ.get("JIUWENCLAW_DATA_DIR", "").strip()
    JIUWENCLAW_DATA_DIR_PATH = (
        Path(_win_root_env).expanduser().resolve()
        if _win_root_env
        else _effective_user_home() / ".jiuwenclaw"
    )
    JIUWENBOX_HOME = JIUWENCLAW_DATA_DIR_PATH / "jiuwenbox"
    _office_claw_env = os.environ.get("OFFICE_CLAW_DATA_DIR", "").strip()
    OFFICE_CLAW_DATA_ROOT = (
        Path(_office_claw_env).expanduser().resolve()
        if _office_claw_env
        else _effective_user_home() / ".office-claw"
    )
else:
    JIUWENCLAW_DATA_DIR_PATH = _effective_user_home() / ".jiuwenclaw"
    JIUWENBOX_HOME = _effective_user_home() / ".jiuwenbox"
    OFFICE_CLAW_DATA_ROOT = _effective_user_home() / ".office-claw"
SANDBOX_WORKSPACE = JIUWENBOX_HOME / "workspace"
WIN_SANDBOX_WORKSPACE_ROOT = SANDBOX_WORKSPACE
