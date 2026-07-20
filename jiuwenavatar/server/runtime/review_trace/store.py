"""Persistence bridge for normalized Committer PR-review traces."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .adapter import trajectory_to_review_trace
from .json_io import read_json, sha256_json, write_json
from .redaction import redact_sensitive_values


DEFAULT_VERSION = "default"
PR_URL_PATTERN = re.compile(
    r"https://gitcode\.com/[^/\s]+/[^/\s]+/(?:pull|merge_requests)/\d+"
)


class CommitterReviewTraceStore:
    """Persist raw trajectories and/or normalized Committer review traces.

    ``TrajectoryRail`` only needs the ``save`` method. ``load`` and ``query`` are
    provided for local inspection and tests, returning JSON-compatible raw data.
    Production wiring enables ``require_pr_review`` and disables ``save_raw`` so
    only executed dev-reviewer PR traces are written by default.
    """

    def __init__(
        self,
        base_dir: Path,
        *,
        review_traces_dir: Path | None = None,
        case_id: str | None = None,
        write_review_trace: bool = False,
        save_raw: bool = True,
        require_pr_review: bool = False,
        redact_secrets: bool = True,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.review_traces_dir = Path(review_traces_dir) if review_traces_dir else None
        self.case_id = case_id
        self.write_review_trace = write_review_trace
        self.save_raw = save_raw
        self.require_pr_review = require_pr_review
        self.redact_secrets = redact_secrets
        if self.save_raw:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.review_traces_dir:
            self.review_traces_dir.mkdir(parents=True, exist_ok=True)

    def save(self, trajectory: Any, version: str | None = None) -> None:
        """Normalize one trajectory and persist the configured artifacts."""

        data = trajectory_to_plain_json(trajectory)
        if self.redact_secrets:
            data = redact_sensitive_values(data)
        execution_id = _execution_id(data)
        trace: dict[str, Any] | None = None
        if self.write_review_trace:
            if not self.review_traces_dir:
                raise ValueError("review_traces_dir is required when write_review_trace=True")
            trace = trajectory_to_review_trace(data, case_id=self.case_id)
            case_id = str(trace.get("case_id") or "")
            if not case_id and not self.require_pr_review:
                raise ValueError("case_id is required when the trajectory does not contain a GitCode PR URL")
            if case_id:
                trace["trace_id"] = f"{case_id}_{_safe_name(execution_id)}_trace"
            else:
                trace = None

        if self.require_pr_review and (trace is None or not is_executed_pr_review(trace)):
            return

        if self.save_raw:
            write_json(self._raw_path(execution_id, version), data)
        if trace is not None:
            write_json(self.review_traces_dir / f"{trace['trace_id']}.json", trace)

    def load(self, execution_id: str, version: str | None = None) -> dict[str, Any] | None:
        """Load one raw trajectory as JSON-compatible data."""

        path = self._raw_path(execution_id, version)
        if not path.exists():
            return None
        data = read_json(path)
        return data if isinstance(data, dict) else None

    def query(self, version: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        """Return raw trajectories matching top-level field filters."""

        raw_dir = self._raw_dir(version)
        if not raw_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(raw_dir.glob("*.json")):
            data = read_json(path)
            if isinstance(data, dict) and all(data.get(key) == value for key, value in filters.items()):
                results.append(data)
        return results

    def _raw_dir(self, version: str | None) -> Path:
        return self.base_dir / (version or DEFAULT_VERSION) / "raw"

    def _raw_path(self, execution_id: str, version: str | None) -> Path:
        return self._raw_dir(version) / f"{_safe_name(execution_id)}.json"


def trajectory_to_plain_json(value: Any) -> Any:
    """Convert openjiuwen trajectory objects into JSON-compatible data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): trajectory_to_plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [trajectory_to_plain_json(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return trajectory_to_plain_json(model_dump())
    if is_dataclass(value):
        return trajectory_to_plain_json(asdict(value))
    return str(value)


def is_executed_pr_review(trace: dict[str, Any]) -> bool:
    """Return whether a normalized trace is an executed dev-reviewer PR review.

    This deliberately requires structured execution evidence.  A Committer
    conversation that merely mentions a PR or the dev-reviewer skill must not
    become a training-case candidate.
    """

    task = trace.get("task") if isinstance(trace.get("task"), dict) else {}
    steps = trace.get("runner_steps") if isinstance(trace.get("runner_steps"), dict) else {}
    has_pr = bool(PR_URL_PATTERN.fullmatch(str(task.get("pr_url") or "")))
    used_dev_reviewer = trace.get("skill") == "dev-reviewer"
    collect = steps.get("collect") if isinstance(steps.get("collect"), dict) else {}
    # Reading a previous review.md/result.json is not a new PR review.  The
    # dev-reviewer contract requires collect for every fresh review, so only
    # current-run collect evidence may authorize persistence.
    collected_current_pr = collect.get("status") == "done"
    return has_pr and used_dev_reviewer and collected_current_pr


def _execution_id(data: Any) -> str:
    if isinstance(data, dict):
        execution_id = data.get("execution_id")
        if execution_id:
            return str(execution_id)
        session_id = data.get("session_id")
        if session_id:
            return str(session_id)
    return sha256_json(data).replace("sha256:", "trajectory-")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "trajectory"


def _stringify(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_stringify(item) for item in value)
    return str(value)
