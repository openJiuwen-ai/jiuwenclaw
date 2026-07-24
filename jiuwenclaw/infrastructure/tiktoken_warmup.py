# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""tiktoken 启动预热。

ReActAgent._init_context 里 TiktokenCounter() 会同步调用 get_encoding；
缓存未命中时会下载词表并可能卡住事件循环。在 AgentServer ready 前同步预热一次
（与镜像构建 ``python -c "…get_encoding('cl100k_base')"`` 相同），避免首请求抢跑。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def warmup_tiktoken() -> None:
    """同步加载 cl100k_base；应在 server.start / ready 之前调用。"""
    t0 = time.perf_counter()
    try:
        import tiktoken

        tiktoken.get_encoding("cl100k_base")
        logger.info(
            "tiktoken warmup ok: cl100k_base elapsed_ms=%.1f",
            (time.perf_counter() - t0) * 1000.0,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "tiktoken warmup failed after %.1fms",
            (time.perf_counter() - t0) * 1000.0,
            exc_info=True,
        )
