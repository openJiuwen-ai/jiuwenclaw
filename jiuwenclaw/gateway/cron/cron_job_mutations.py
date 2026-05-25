"""CronJob create/update mutations (build, patch, sort) shared by file and Redis stores."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any

from jiuwenclaw.gateway.cron.models import CronJob


def sort_cron_jobs(jobs: list[CronJob]) -> list[CronJob]:
    jobs.sort(key=lambda j: (j.updated_at or 0.0, j.created_at or 0.0), reverse=True)
    return jobs


def build_new_cron_job(
    *,
    job_id: str | None = None,
    name: str,
    cron_expr: str,
    timezone: str,
    description: str,
    targets: str,
    enabled: bool = True,
    wake_offset_seconds: int | None = None,
    session_id: str | None = None,
    chat_type: str | None = None,
    mode: str | None = None,
    delete_after_run: bool | None = None,
) -> CronJob:
    now = time.time()
    sid = str(session_id).strip() if isinstance(session_id, str) and session_id.strip() else None
    ct = str(chat_type).strip() if isinstance(chat_type, str) and chat_type.strip() else None
    m = str(mode).strip().lower() if isinstance(mode, str) and mode.strip() else "agent"
    dar = bool(delete_after_run) if delete_after_run is not None else False
    job = CronJob(
        id=str(job_id or "").strip() or uuid.uuid4().hex,
        name=str(name or "").strip(),
        enabled=bool(enabled),
        cron_expr=str(cron_expr or "").strip(),
        timezone=str(timezone or "").strip(),
        wake_offset_seconds=int(wake_offset_seconds) if wake_offset_seconds is not None else 60,
        description=str(description or ""),
        targets=str(targets or "").strip(),
        session_id=sid,
        created_at=now,
        updated_at=now,
        chat_type=ct,
        mode=m,
        delete_after_run=dar,
    )
    CronJob.from_dict(job.to_dict())
    return job


def apply_cron_job_patch(existing: CronJob, patch: dict[str, Any]) -> CronJob:
    patch = dict(patch or {})
    updated = existing
    if "name" in patch:
        updated = replace(updated, name=str(patch.get("name") or "").strip())
    if "enabled" in patch:
        enabled_val = bool(patch.get("enabled"))
        updated = replace(updated, enabled=enabled_val)
        if enabled_val and "expired" not in patch:
            updated = replace(updated, expired=False)
    if "cron_expr" in patch:
        updated = replace(updated, cron_expr=str(patch.get("cron_expr") or "").strip())
        if "expired" not in patch:
            updated = replace(updated, expired=False)
    if "timezone" in patch:
        updated = replace(updated, timezone=str(patch.get("timezone") or "").strip())
    if "wake_offset_seconds" in patch:
        raw = patch.get("wake_offset_seconds")
        try:
            wos = int(raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("wake_offset_seconds must be int") from exc
        updated = replace(updated, wake_offset_seconds=max(0, wos))
    if "description" in patch:
        updated = replace(updated, description=str(patch.get("description") or ""))
    if "targets" in patch:
        updated = replace(updated, targets=str(patch.get("targets") or "").strip())
    if "session_id" in patch:
        raw_sid = patch.get("session_id")
        new_sid = str(raw_sid).strip() if isinstance(raw_sid, str) and str(raw_sid).strip() else None
        updated = replace(updated, session_id=new_sid)
    if "chat_type" in patch:
        raw_ct = patch.get("chat_type")
        new_ct = str(raw_ct).strip() if isinstance(raw_ct, str) and str(raw_ct).strip() else None
        updated = replace(updated, chat_type=new_ct)
    if "expired" in patch:
        updated = replace(updated, expired=bool(patch.get("expired")))
    if "mode" in patch:
        raw_mode = patch.get("mode")
        new_mode = (
            str(raw_mode).strip().lower()
            if isinstance(raw_mode, str) and str(raw_mode).strip()
            else "agent"
        )
        updated = replace(updated, mode=new_mode)
    if "delete_after_run" in patch:
        updated = replace(updated, delete_after_run=bool(patch.get("delete_after_run")))
    updated.updated_at = time.time()
    CronJob.from_dict(updated.to_dict())
    return updated
