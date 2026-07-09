# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared keys for runtime callback/context dictionaries."""

from __future__ import annotations


# Agent callback context extra key used to preserve the original request channel
# when downstream model-call inputs no longer carry channel metadata.
JIUWENAVATAR_CHANNEL_CONTEXT_KEY = "__jiuwenavatar_channel__"


__all__ = ["JIUWENAVATAR_CHANNEL_CONTEXT_KEY"]
