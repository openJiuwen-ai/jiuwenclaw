"""Generic after-tool capture for Auto Permission."""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.agents.harness.common.rails.permissions.artifact_path_provenance import (
    ArtifactPostGateResult,
    consume_artifact_candidate_state,
    tool_result_succeeded,
)
from jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.invocation_context import (
    _extract_invocation,
)
from jiuwenswarm.server.runtime.sandbox_no_host_fallback import (
    clear_no_host_fallback,
)

logger = logging.getLogger(__name__)


class AutoPermissionArtifactCaptureMixin:
    """Record bounded runtime context without inferring generated artifacts."""

    async def after_tool_call(self, *args: Any, **kwargs: Any) -> Any:
        """Capture one bounded result before delegating to the base rail."""

        clear_no_host_fallback()
        invocation = _extract_invocation(args, kwargs)
        state = consume_artifact_candidate_state(
            invocation.ctx,
            tool_name=invocation.tool_name,
        )
        if state is not None and self.session_artifact_paths is not None:
            post_gate_result = ArtifactPostGateResult(
                rejected=len(state.candidates),
                reason_codes=("execution_not_successful",),
            )
            if tool_result_succeeded(invocation.ctx):
                excluded = self.auto_options.get("bounded_write_excluded_paths", ())
                excluded_paths = (
                    excluded if isinstance(excluded, (list, tuple)) else ()
                )
                try:
                    post_gate_result = self.session_artifact_paths.record_verified(
                        state=state,
                        excluded_paths=excluded_paths,
                    )
                except Exception:  # noqa: BLE001 - observation must not affect execution
                    logger.exception(
                        "artifact provenance post gate failed tool_name=%s",
                        invocation.tool_name,
                    )
                    post_gate_result = ArtifactPostGateResult(
                        rejected=len(state.candidates),
                        reason_codes=("post_gate_exception",),
                    )
            if state.facts is not None:
                try:
                    self._emit_audit(
                        state.facts,
                        decision="observe",
                        reason="artifact_provenance_post_gate",
                        degraded=False,
                        extra={
                            "record_kind": "artifact_provenance_post_gate",
                            "artifact_candidate_count": len(state.candidates),
                            "artifact_accepted_count": post_gate_result.accepted,
                            "artifact_rejected_count": post_gate_result.rejected,
                            "artifact_reason_codes": list(
                                post_gate_result.reason_codes
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001 - observation must not affect execution
                    logger.exception(
                        "artifact provenance audit failed tool_name=%s",
                        invocation.tool_name,
                    )
        return await self._call_base_after_rail(args, kwargs, invocation)
