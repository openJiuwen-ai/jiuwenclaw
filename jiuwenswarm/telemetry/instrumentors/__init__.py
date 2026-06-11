# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unified instrumentor registration.

For enterprise_dev (SDK Adapter architecture):
  - LLM/Tool/Agent/Session: handled by TelemetryRail (no monkey-patch)
  - Entry/Queue/Gateway Agent Client: monkey-patch still applies

For officeclaw (ReActAgent architecture):
  - All instrumentors use monkey-patch
"""

from __future__ import annotations

from jiuwenswarm.common.utils import logger

# Architecture detection
_SDK_ADAPTER_MODE = False


def _detect_architecture() -> bool:
    """Detect if running in SDK Adapter mode (enterprise_dev).

    Returns True if JiuWenClawDeepAdapter is the active backend,
    False if using legacy ReActAgent (officeclaw).
    """
    try:
        # Check if JiuWenClawDeepAdapter exists (SDK Adapter architecture)
        from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter
        return True  # enterprise_dev mode
    except ImportError:
        pass

    return False


def apply_instrumentors(
    log_messages: bool = True,
    session_stuck_threshold_ms: float = 300000.0,
    session_stuck_check_interval_s: float = 30.0,
) -> None:
    """Apply all instrumentors. Called once at startup.

    For SDK Adapter mode (enterprise_dev):
      - Entry, Queue, Gateway Agent Client: monkey-patch
      - LLM/Tool/Agent/Session: handled by TelemetryRail

    For ReActAgent mode (officeclaw):
      - All instrumentors: monkey-patch
    """
    global _SDK_ADAPTER_MODE
    _SDK_ADAPTER_MODE = _detect_architecture()

    from jiuwenswarm.telemetry.instrumentors.entry import instrument_entry
    from jiuwenswarm.telemetry.instrumentors.gateway_agent_client import instrument_gateway_agent_client
    from jiuwenswarm.telemetry.instrumentors.queue import instrument_queue

    # These instrumentors work in both architectures modes
    instrument_entry()
    logger.info("[Telemetry] entry instrumentor applied")

    instrument_gateway_agent_client()
    logger.info("[Telemetry] gateway agent client instrumentor applied")

    instrument_queue()
    logger.info("[Telemetry] queue instrumentor applied")

    if _SDK_ADAPTER_MODE:
        # SDK Adapter mode: LLM/Tool/Agent handled by TelemetryRail; Session
        # lifecycle still needs its own monkey-patch on SessionManager.
        logger.info("[Telemetry] SDK Adapter mode detected - LLM/Tool/Agent telemetry via TelemetryRail")

        # Import and configure TelemetryRail settings
        from jiuwenswarm.telemetry.instrumentors.telemetry_rail import set_log_messages
        set_log_messages(log_messages)

        logger.info("[Telemetry] telemetry_rail instrumentor configured")

        # TODO: session instrumentor needs adaptation for jiuwenswarm SessionManager API
        # from jiuwenswarm.telemetry.instrumentors.session import instrument_session
        # instrument_session(
        #     stuck_threshold_ms=session_stuck_threshold_ms,
        #     stuck_check_interval_s=session_stuck_check_interval_s,
        # )
        # logger.info("[Telemetry] session instrumentor applied (SessionManager)")
    else:
        # ReActAgent mode (officeclaw): traditional monkey-patch
        from jiuwenswarm.telemetry.instrumentors.agent import instrument_agent
        from jiuwenswarm.telemetry.instrumentors.llm import instrument_llm, set_log_messages
        from jiuwenswarm.telemetry.instrumentors.tool import instrument_tools
        from jiuwenswarm.telemetry.instrumentors.session import instrument_session

        set_log_messages(log_messages)

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
