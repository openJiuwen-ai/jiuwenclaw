# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""临时计费标记：模型调用 x-hag-trace-id 生命周期前缀方案（2026-08-27）。

最终方案（fulfillment NEW/FINISH/FAILED 状态上报，桌面条目 billing-service）短期
不可用期间，把一轮 query 的生命周期直接标记到模型调用的 x-hag-trace-id 上，
计费方从模型网关日志按前缀归集一轮 query 的全部模型消耗：

  - 一轮 query 的首次模型调用前：jiuwen 追加一次虚拟模型调用携带
    ``xiaoyi-work-begin-<core>``（2026-08-31 起 begin 独立成虚拟调用，与终态同形态）
  - 该轮所有真实模型调用：``xiaoyi-work-<core>``
  - 正常结束：jiuwen 追加一次虚拟模型调用携带 ``xiaoyi-work-end-<core>``
  - 异常结束：jiuwen 追加一次虚拟模型调用携带 ``xiaoyi-work-failed-<core>``

core = ``sessionId&interactionId短码``（interactionId 超 12 取前 8）。长度约束：
celia 模型网关拒绝 x-hag-trace-id > 64（回 ``data: {"error":{}}`` 空错误帧），
最长前缀 ``xiaoyi-work-failed-``（19 字符）→ core 上限 45（同时满足"core < 50"
与"整体 ≤ 64"两个约束，取严）；超长先截 sessionId 段，保住 interaction 短码
（每轮 query 的唯一区分维度）。

挂点（最终方案上线时随本模块整体移除）：
  - ``model_trace.TraceAwareModel.invoke/stream``：每次真实模型调用经
    ``mark_model_call`` 登记并携带裸前缀；某 core 首次登记时先经
    ``schedule_marker_call`` 派发 begin 虚拟调用（显式头不被改写、不递归）；
  - ``interface_deep.process_message_stream_impl`` finally：按终态经
    ``schedule_marker_call`` 派发 end/failed 虚拟调用；
  - ``xiaoyi_invocation.build_trace_id``：core 构造委托 ``build_billing_core``。

开关：环境变量 ``JIUWEN_BILLING_TRACE_MARKER=off`` 整体关闭（默认开）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

TRACE_PREFIX = "xiaoyi-work-"
BEGIN_PREFIX = "xiaoyi-work-begin-"
END_PREFIX = "xiaoyi-work-end-"
FAILED_PREFIX = "xiaoyi-work-failed-"

# x-hag-trace-id 头值硬上限（celia 模型网关拒绝更长）
MAX_TRACE_ID_LEN = 64
# core 上限：MAX - 最长前缀（failed-，19）= 45；同时满足用户约束 core < 50
MAX_CORE_LEN = MAX_TRACE_ID_LEN - len(FAILED_PREFIX)

# interactionId 超过该长度才缩短（取前 8，如 UUID 第一段）；短 id（cron-run-1 等）原样保留
SHORT_INTERACTION_ID_MAX_LEN = 12

# begin 注册表：core → 最近活跃时间戳。TTL 2h + LRU 上限 2048（防崩溃残留泄漏；
# 终态不驱逐——HITL 续跑同 core 复跑模型调用仍应判 middle 而非误发 begin）。
_REGISTRY_TTL_SECONDS = 2 * 3600
_REGISTRY_MAX_SIZE = 2048
_begun: OrderedDict[str, float] = OrderedDict()


def billing_marker_enabled() -> bool:
    """临时计费标记开关（默认开；JIUWEN_BILLING_TRACE_MARKER=off 关闭）。"""
    return os.getenv("JIUWEN_BILLING_TRACE_MARKER", "").strip().lower() != "off"


def build_billing_core(session_id: str, interaction_id: str) -> str:
    """构造 trace 核心段：sessionId&interactionId 短码，上限 MAX_CORE_LEN(45)。

    超长先截 sessionId 段（保住 interaction 短码——每轮 query 的唯一区分维度）；
    与桌面 billing-service.coreTraceId 同口径（最终方案两侧关联依赖该一致性）。
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


def _purge_registry(now: float) -> None:
    expired = [k for k, ts in _begun.items() if now - ts > _REGISTRY_TTL_SECONDS]
    for key in expired:
        _begun.pop(key, None)
    while len(_begun) > _REGISTRY_MAX_SIZE:
        _begun.popitem(last=False)


def mark_model_call(core: str) -> tuple[str, bool]:
    """一轮 query 的模型调用标记登记：返回 (本调用应携带的 trace 值, 是否该 core 首次登记)。

    真实模型调用一律携带裸前缀 ``xiaoyi-work-<core>``；首次登记（is_first=True）
    由调用方（TraceAwareModel）在真实调用前经 ``schedule_marker_call`` 补发 begin
    虚拟调用——begin 形态只出现在虚拟标记调用上。非计费 trace（空 core）或开关
    关闭时原样返回 (core, False)。注册表按 core 记首次——HITL 续跑
    （interaction_id/task_id 不变）的模型调用仍判 middle，不会重发 begin。
    """
    if not core or not billing_marker_enabled():
        return core, False
    now = time.monotonic()
    _purge_registry(now)
    if core in _begun:
        _begun[core] = now
        _begun.move_to_end(core)
        return f"{TRACE_PREFIX}{core}"[:MAX_TRACE_ID_LEN], False
    _begun[core] = now
    _purge_registry(now)
    return f"{TRACE_PREFIX}{core}"[:MAX_TRACE_ID_LEN], True


def has_begun(core: str) -> bool:
    """该 core 是否已登记过首次模型调用（终态触发守卫：无模型调用的早退路径不发 end）。"""
    return core in _begun


def begin_trace_id(core: str) -> str:
    """begin 虚拟调用的 trace：``xiaoyi-work-begin-<core>``。"""
    return f"{BEGIN_PREFIX}{core}"[:MAX_TRACE_ID_LEN]


def terminal_trace_id(core: str, ok: bool) -> str:
    """终态虚拟调用的 trace：ok → xiaoyi-work-end-，否则 xiaoyi-work-failed-。"""
    prefix = END_PREFIX if ok else FAILED_PREFIX
    return f"{prefix}{core}"[:MAX_TRACE_ID_LEN]


# 虚拟标记调用的提示词与 fire-and-forget 任务集合（防 GC；任务自带 done_callback 移除）
_MARKER_PROMPT = "please only reply NO_REPLY"
_MARKER_TASKS: set[asyncio.Task] = set()


def schedule_marker_call(model: object, trace_value: str) -> bool:
    """派发一次虚拟标记模型调用（fire-and-forget，不阻塞调用方主路径）。

    成功派发返回 True；无运行中事件循环等派发失败返回 False（调用方可兜底，
    如 begin 退回真实首呼自身携带）。计费标记永不影响会话主路径。
    """
    coro = _run_marker_call(model, trace_value)
    try:
        task = asyncio.create_task(coro)
    except Exception:
        # 无运行中事件循环等：协程未挂起，主动 close 防 unawaited 警告
        coro.close()
        logger.debug("[billing-trace] 标记调用派发失败: %s", trace_value, exc_info=True)
        return False
    _MARKER_TASKS.add(task)
    task.add_done_callback(_MARKER_TASKS.discard)
    return True


async def _run_marker_call(model: object, trace_value: str) -> None:
    """执行虚拟标记调用（system/user 均为 NO_REPLY 提示词，显式 trace 头——
    TraceAwareModel 不覆盖不改写），失败重试 1 次；仍失败记 warn（临时方案
    允许丢单，不阻塞/不影响会话）。"""
    from openjiuwen.core.foundation.llm.schema.message import (
        SystemMessage,
        UserMessage,
    )

    messages = [
        SystemMessage(content=_MARKER_PROMPT),
        UserMessage(content=_MARKER_PROMPT),
    ]
    for attempt in (1, 2):
        try:
            await model.invoke(
                messages,
                custom_headers={"x-hag-trace-id": trace_value},
            )
            logger.info("[billing-trace] 标记已上行: %s", trace_value)
            return
        except Exception as exc:
            if attempt == 2:
                logger.warning(
                    "[billing-trace] 标记上行失败（重试后仍失败）: %s trace=%s",
                    exc,
                    trace_value,
                )
            else:
                logger.info("[billing-trace] 标记上行失败，重试一次: %s", exc)
                await asyncio.sleep(1)


def reset_billing_trace_registry() -> None:
    """测试用：清空 begin 注册表。"""
    _begun.clear()


__all__ = [
    "BEGIN_PREFIX",
    "END_PREFIX",
    "FAILED_PREFIX",
    "MAX_CORE_LEN",
    "MAX_TRACE_ID_LEN",
    "SHORT_INTERACTION_ID_MAX_LEN",
    "TRACE_PREFIX",
    "begin_trace_id",
    "billing_marker_enabled",
    "build_billing_core",
    "has_begun",
    "mark_model_call",
    "reset_billing_trace_registry",
    "schedule_marker_call",
    "terminal_trace_id",
]
