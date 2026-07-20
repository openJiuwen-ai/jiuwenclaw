# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent/Code debug trace — request-level human-readable dump.

Phase 1: request-level ``/debug`` directive only. Mirrors the Team mode
``TeamStreamLogger`` concept (see ``openjiuwen.agent_teams.monitor``) but
with mode/source fields instead of team member/role, plus explicit run
boundaries. OTel and the ``debug_trace`` config block are deferred to
later phases.
"""

from jiuwenswarm.server.runtime.debug_trace.config import (
    DebugTraceSettings,
    resolve_debug_trace_settings,
)
from jiuwenswarm.server.runtime.debug_trace.directives import strip_debug_directive
from jiuwenswarm.server.runtime.debug_trace.paths import (
    debug_trace_dir,
    debug_trace_file,
)
from jiuwenswarm.server.runtime.debug_trace.stream_logger import DebugTraceLogger

__all__ = [
    "DebugTraceLogger",
    "DebugTraceSettings",
    "debug_trace_dir",
    "debug_trace_file",
    "resolve_debug_trace_settings",
    "strip_debug_directive",
]
