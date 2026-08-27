# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""服务端主动推送的订阅者注册表

本模块把推给当前连接抽象成推给已注册的订阅者，WS与HTTP都注册进来：
HTTP客户端通过一条SSE长连接（``GET /api/v1/events/stream``）订阅，
WS连接以:data:`WS_PUSH_SUBSCRIBER_ID`注册。

WS 侧的单槽位语义（刻意如此，勿当 bug 修）
---------------------------------------
WS 一律用 :data:`WS_PUSH_SUBSCRIBER_ID` 这**一个**固定 id 注册，由此得到两条
既有语义：

1. **后连接覆盖前者** —— 同 id 重复注册即覆盖，「同时只有最后一条 Gateway
   连接能收到推送」；
2. **断开时无条件清空** —— ``unregister(WS_PUSH_SUBSCRIBER_ID)`` 无条件生效，
   即使已有新连接接管也会抹掉。

2026-08-20 真机实测过它的杀伤力：几条只活 5 秒的短连接轮流接入/断开，把长连接
挤出槽位后又清空；长连接因为一直没断**不会重新注册**，于是前端**永久收不到推送
且毫无报错**，只能重启服务恢复（刷新浏览器无效）。

反向 RPC 另有一个显式 owner。普通推送仍按原语义扇出；ACP/A2A 请求只投递给
最后注册的 RPC-capable Gateway，避免多个 SSE 订阅者重复执行同一个工具请求。

> 想改成「多 Gateway 连接各自收推送」，只需把固定 id 换成每连接唯一 id、并在
> ``finally`` 里注销**自己那个** id。那是一次有意的行为变更，应单独验证。

与 ``server.gateway_push`` 的区别（名字相近，角色相反）
--------------------------------------------------
- ``server.gateway_push`` 是**调用方**侧的推送客户端：cron 工具、team 远端成员、
  agent adapter 等在业务过程中用它**发起**一次推送；
- 本模块是**服务端**侧的订阅者注册表：推送发起后由它决定**扇给谁**。

两者不是重复实现，改动其一不要顺手合并另一个。

所有方法都假定在同一个事件循环内调用AgentServer 单循环模型。
``push`` 对订阅者快照后再扇出，因此扇出过程中注册/注销不会影响本次遍历。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jiuwenswarm.server.transports.sink import ResponseSink

logger = logging.getLogger(__name__)


#: Gateway WebSocket 连接在注册表里的**固定** id。
#:
#: 固定而非每连接唯一，由此得到 WS 侧的单槽位语义：同 id 重复注册即覆盖
#: （后连接顶掉前者），``unregister`` 无条件生效（断开即清空）。详见模块 docstring。
WS_PUSH_SUBSCRIBER_ID = "gateway-ws"

#: 单个订阅者的推送投递上限（秒）。超时即判定该订阅者停滞并注销 ——
#: 见 :meth:`PushRegistry.push` 的 note。
SEND_TIMEOUT = 5.0


@dataclass(frozen=True)
class _Subscriber:
    """一个已注册的推送订阅者。

    Attributes:
        sink: 出口。推送直接走 :meth:`ResponseSink.send_wire`，
            因为 ``build_server_push_wire`` 已经产出完整 wire 帧。
        session_id: 会话过滤。``None`` 表示接收全部推送；
            指定后只接收 ``session_id`` 相同的推送。
        channel_id: 频道过滤，语义同上。
        drop_on_stall: 发送迟迟不返回时，是否给它设上限并就地注销。

            ``True``（默认，SSE 用）：``send_wire`` 可能是 ``await queue.put``，
            队列满即无限阻塞，必须设上限，否则一个停止读取的客户端能把整轮扇出
            连同调用方一起卡死。

            ``False``（**WS 用**）：不设上限、也不注销。WS 侧的出口是
            ``_GatewayWSPushSink``，它的契约是「发送失败只记 warning、连接照旧留着」——
            因为 ``gateway-ws`` 是**固定 id 的单槽位**，一旦被摘掉，长连接不会重新注册，
            前端就此永久收不到推送、只能重启服务。给它套超时等于绕过那层保护：
            一次慢发送（大帧、背压、排在连接级 ``send_lock`` 后面）就会触发注销。
    """

    sink: ResponseSink
    session_id: str | None = None
    channel_id: str | None = None
    drop_on_stall: bool = True

    def matches(self, wire: dict[str, Any]) -> bool:
        """本订阅者是否该收到这条推送。

        过滤是**订阅者侧的收窄**：没声明过滤条件就全收；声明了就必须相等。
        推送帧本身缺该字段时视为不匹配（宁可不发，也不要把别的会话的内容漏给它）。
        """
        if self.session_id is not None and wire.get("session_id") != self.session_id:
            return False
        if self.channel_id is not None and wire.get("channel") != self.channel_id:
            return False
        return True


class PushRegistry:
    """推送订阅者注册表：把「推给当前连接」变成「推给匹配的订阅者」。"""

    __slots__ = (
        "_subscribers",
        "_reverse_rpc_owner_id",
        "_reverse_rpc_owner_lost_callback",
    )

    def __init__(self) -> None:
        self._subscribers: dict[str, _Subscriber] = {}
        self._reverse_rpc_owner_id: str | None = None
        self._reverse_rpc_owner_lost_callback: Callable[[], None] | None = None

    def set_reverse_rpc_owner_lost_callback(
        self, callback: Callable[[], None] | None
    ) -> None:
        self._reverse_rpc_owner_lost_callback = callback

    def _notify_reverse_rpc_owner_lost(self) -> None:
        callback = self._reverse_rpc_owner_lost_callback
        if callback is not None:
            callback()

    def register(
        self,
        subscriber_id: str,
        sink: ResponseSink,
        *,
        session_id: str | None = None,
        channel_id: str | None = None,
        drop_on_stall: bool = True,
        reverse_rpc_capable: bool = False,
    ) -> None:
        """登记一个订阅者。同 ``subscriber_id`` 重复注册会覆盖旧的。

        ``drop_on_stall`` 的含义见 :class:`_Subscriber` —— WS 侧必须传 ``False``。
        """
        replacing_rpc_owner = self._reverse_rpc_owner_id == subscriber_id
        self._subscribers[subscriber_id] = _Subscriber(
            sink=sink,
            session_id=session_id,
            channel_id=channel_id,
            drop_on_stall=drop_on_stall,
        )
        if reverse_rpc_capable:
            if self._reverse_rpc_owner_id is not None:
                self._notify_reverse_rpc_owner_lost()
            self._reverse_rpc_owner_id = subscriber_id
        elif replacing_rpc_owner:
            self._reverse_rpc_owner_id = None
            self._notify_reverse_rpc_owner_lost()
        logger.info(
            "[PushRegistry] 订阅者接入: id=%s session_id=%s "
            "channel_id=%s 当前订阅数=%d",
            subscriber_id,
            session_id,
            channel_id,
            len(self._subscribers),
        )

    def unregister(self, subscriber_id: str) -> None:
        """注销订阅者。不存在时静默返回（断连清理可能重入）。"""
        if self._subscribers.pop(subscriber_id, None) is not None:
            if self._reverse_rpc_owner_id == subscriber_id:
                self._reverse_rpc_owner_id = None
                self._notify_reverse_rpc_owner_lost()
            logger.info(
                "[PushRegistry] 订阅者断开: id=%s 当前订阅数=%d",
                subscriber_id,
                len(self._subscribers),
            )

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def reverse_rpc_ready(self) -> bool:
        owner_id = self._reverse_rpc_owner_id
        return owner_id is not None and owner_id in self._subscribers

    async def push_reverse_rpc(self, wire: dict[str, Any]) -> int:
        """Deliver a point-to-point reverse RPC to the current Gateway owner."""
        owner_id = self._reverse_rpc_owner_id
        subscriber = self._subscribers.get(owner_id or "")
        if owner_id is None or subscriber is None or not subscriber.matches(wire):
            return 0
        try:
            if subscriber.drop_on_stall:
                sent = await asyncio.wait_for(
                    subscriber.sink.send_wire(wire), timeout=SEND_TIMEOUT
                )
            else:
                sent = await subscriber.sink.send_wire(wire)
        except asyncio.TimeoutError:
            logger.warning(
                "[PushRegistry] 反向 RPC 推送超时(%.1fs)，注销订阅者: id=%s",
                SEND_TIMEOUT,
                owner_id,
            )
            self.unregister(owner_id)
            return 0
        except Exception as exc:  # noqa: BLE001 - owner loss fails pending RPCs
            logger.warning(
                "[PushRegistry] 反向 RPC 推送失败，注销订阅者: id=%s error=%s",
                owner_id,
                exc,
            )
            self.unregister(owner_id)
            return 0
        return int(bool(sent))

    async def push(self, wire: dict[str, Any]) -> int:
        """向匹配的订阅者扇出一条已构造好的 wire 帧。

        Returns:
            成功送达的订阅者数量。

        单个订阅者**发送失败或停滞都不影响其它订阅者** —— 一条坏掉或卡住的 SSE
        连接不该让整轮推送失败。两种情况都就地注销，避免持续堆积。

        .. note:: 为什么必须有超时

           ``SSESink.send_wire`` 是 ``await queue.put``，队列满时会**阻塞**。
           一个停止读取的 SSE 客户端（网络停滞、进程挂起）把队列填满后，
           这里的串行扇出会卡在它身上：排在其后的订阅者一条都收不到，
           调用方（cron 到点提醒 / ``send_file_to_user`` / proactive）的协程
           一并卡死，整个进程的推送就此停摆，且只能重启恢复。
           光靠 ``try/except`` 拦不住 —— 那只兜异常，兜不住阻塞。
        """
        if not self._subscribers:
            return 0

        delivered = 0
        # 先快照：扇出过程中可能有订阅者注册/注销
        for subscriber_id, sub in list(self._subscribers.items()):
            if not sub.matches(wire):
                continue
            try:
                if sub.drop_on_stall:
                    sent = await asyncio.wait_for(
                        sub.sink.send_wire(wire), timeout=SEND_TIMEOUT
                    )
                else:
                    # 不设上限：WS 侧要与「从不被注销」的既有语义完全一致，
                    # 连超时都不能引入（见 _Subscriber.drop_on_stall）。
                    sent = await sub.sink.send_wire(wire)
                if sent:
                    delivered += 1
            except asyncio.TimeoutError:
                logger.warning(
                    "[PushRegistry] 推送超时(%.1fs)，注销停滞订阅者: id=%s",
                    SEND_TIMEOUT,
                    subscriber_id,
                )
                self.unregister(subscriber_id)
            # 不必显式放行 CancelledError：它继承 BaseException，本就不会被下面的
            # ``except Exception`` 接住（对照 agent_http_server._serve_guarded ——
            # 那里兜底是 BaseException，才**必须**显式 re-raise）。
            except Exception as exc:  # noqa: BLE001 - 单个订阅者故障不影响其它
                logger.warning(
                    "[PushRegistry] 推送失败，注销订阅者: id=%s error=%s", subscriber_id, exc
                )
                self.unregister(subscriber_id)
        return delivered


#: 进程级单例。``send_push`` 的调用方遍布 tools / proactive / cron，
#: 它们只能拿到 ``AgentWebSocketServer.get_instance()``，因此这里用模块级单例，
#: 与 ``_background_session_kvc_tasks`` 等共享状态同样的模式。
_REGISTRY = PushRegistry()


def get_push_registry() -> PushRegistry:
    """取进程级推送注册表。"""
    return _REGISTRY
