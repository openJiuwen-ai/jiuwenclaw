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
