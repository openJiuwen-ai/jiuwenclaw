# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""计费 trace 核心段构造（2026-09-02 起仅余 core 构造函数）。

历史：2026-08-27 ~ 2026-09-01 本模块是「临时计费标记方案」（模型调用
x-hag-trace-id 携带 xiaoyi-work-{begin|end|failed|}- 生命周期前缀 + NO_REPLY
虚拟标记调用，计费方从模型网关日志按前缀归集，见
docs/billing-trace-marker-design.md）。正式方案（fulfillment
task/status/update NEW/FINISH/FAILED 状态上报）上线后该标记机制已整体移除：

  - 模型调用的 x-hag-trace-id = 裸核心段（无任何前缀）；
  - 计费上报的 x-hag-trace-id 与模型调用完全同值（桌面 billing-service /
    本仓 common.billing_client 经 np://claw-billing 管道上报）；
  - 状态语义由 task/status/update 请求体 conversationStatus 承担。

core = ``sessionId&interactionId短码``（interactionId 超 12 取前 8）。长度约束：
celia 模型网关拒绝 x-hag-trace-id > 64（回 ``data: {"error":{}}`` 空错误帧），
core 上限 45（历史前缀形态余量 + "core < 50" 约束，取严）；超长先截 sessionId
段，保住 interaction 短码（每轮 query 的唯一区分维度）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# x-hag-trace-id 头值硬上限（celia 模型网关拒绝更长）
MAX_TRACE_ID_LEN = 64
# core 上限 45：历史前缀形态（最长 xiaoyi-work-failed- 19）余量 + "core < 50" 约束，取严
MAX_CORE_LEN = 45

# interactionId 超过该长度才缩短（取前 8，如 UUID 第一段）；短 id（cron-run-1 等）原样保留
SHORT_INTERACTION_ID_MAX_LEN = 12


def build_billing_core(session_id: str, interaction_id: str) -> str:
    """构造 trace 核心段：sessionId&interactionId 短码，上限 MAX_CORE_LEN(45)。

    超长先截 sessionId 段（保住 interaction 短码——每轮 query 的唯一区分维度）；
    与桌面 billing-service.coreTraceId 同口径（模型调用与计费上报的关联依赖该一致性）。
    """
    short = (
        interaction_id
        if len(interaction_id) <= SHORT_INTERACTION_ID_MAX_LEN
        else interaction_id[:8]
    )
    core = f"{session_id}&{short}"
    if len(core) <= MAX_CORE_LEN:
        return core
    keep = MAX_CORE_LEN - len(short) - 1
    if keep > 0:
        return f"{session_id[:keep]}&{short}"
    # 防御：interaction 短码自身超长（不可能，≤12）时的整体截断兜底
    return core[:MAX_CORE_LEN]


__all__ = [
    "MAX_CORE_LEN",
    "MAX_TRACE_ID_LEN",
    "SHORT_INTERACTION_ID_MAX_LEN",
    "build_billing_core",
]
