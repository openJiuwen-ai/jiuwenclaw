# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SSH server channel: accept SSH clients and deliver sessions to MessageHandler."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["SshChannel", "SshChannelConfig"]

if TYPE_CHECKING:
    from jiuwenswarm.gateway.channel_manager.protocol.ssh.ssh_connect import (
        SshChannel,
        SshChannelConfig,
    )


def __getattr__(name: str):
    if name in __all__:
        from jiuwenswarm.gateway.channel_manager.protocol.ssh import ssh_connect

        return getattr(ssh_connect, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
