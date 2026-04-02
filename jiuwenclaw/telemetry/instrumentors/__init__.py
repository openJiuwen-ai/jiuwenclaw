# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unified instrumentor registration."""

from __future__ import annotations

from jiuwenclaw.utils import logger


def apply_instrumentors(
    log_messages: bool = True,
    session_stuck_threshold_ms: float = 300000.0,
    session_stuck_check_interval_s: float = 30.0,
) -> None:
    """Apply all monkey-patch instrumentors. Called once at startup."""
    from jiuwenclaw.telemetry.instrumentors.entry import instrument_entry
    from jiuwenclaw.telemetry.instrumentors.gateway_agent_client import instrument_gateway_agent_client
    from jiuwenclaw.telemetry.instrumentors.agent import instrument_agent
    from jiuwenclaw.telemetry.instrumentors.llm import instrument_llm, set_log_messages
    from jiuwenclaw.telemetry.instrumentors.tool import instrument_tools
    from jiuwenclaw.telemetry.instrumentors.session import instrument_session
    from jiuwenclaw.telemetry.instrumentors.queue import instrument_queue

    set_log_messages(log_messages)

    instrument_entry()
    logger.info("[Telemetry] entry instrumentor applied")

    instrument_gateway_agent_client()
    logger.info("[Telemetry] gateway agent client instrumentor applied")

    instrument_agent()
    logger.info("[Telemetry] agent instrumentor applied")

    instrument_llm()
    logger.info("[Telemetry] llm instrumentor applied")

    instrument_tools()
    logger.info("[Telemetry] tool instrumentor applied")

    instrument_session(
        stuck_threshold_ms=session_stuck_threshold_ms,
        stuck_check_interval_s=session_stuck_check_interval_s,
    )
    logger.info("[Telemetry] session instrumentor applied")

    instrument_queue()
    logger.info("[Telemetry] queue instrumentor applied")
