/**
 * Cron job run-state sync (Pull transport).
 *
 * Compares successive `cron.job.list` snapshots and detects new executions via
 * `last_run_at` / `last_session_id`. Used by HTTP mode when server push is unavailable.
 */

export interface CronJobRunFingerprint {
  jobId: string;
  lastRunAt: number | null;
  lastSessionId: string | null;
}

export interface CronJobRunListItem {
  id: string;
  last_run_at?: unknown;
  last_session_id?: unknown;
}

export interface CronJobRunUpdateResult {
  updatedJobIds: string[];
  nextFingerprints: Record<string, CronJobRunFingerprint>;
}

export const CRON_JOB_SYNC_INTERVAL_MS = 30_000;

function normalizeRunAt(raw: unknown): number | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    if (!trimmed) {
      return null;
    }
    const parsed = Number(trimmed);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function normalizeSessionId(raw: unknown): string | null {
  if (typeof raw !== 'string') {
    return null;
  }
  const trimmed = raw.trim();
  return trimmed || null;
}

export function fingerprintFromCronJob(job: CronJobRunListItem): CronJobRunFingerprint {
  return {
    jobId: job.id,
    lastRunAt: normalizeRunAt(job.last_run_at),
    lastSessionId: normalizeSessionId(job.last_session_id),
  };
}

export function buildCronJobFingerprintMap(
  jobs: CronJobRunListItem[]
): Record<string, CronJobRunFingerprint> {
  const out: Record<string, CronJobRunFingerprint> = {};
  for (const job of jobs) {
    const id = String(job.id || '').trim();
    if (!id) {
      continue;
    }
    out[id] = fingerprintFromCronJob(job);
  }
  return out;
}

function fingerprintChanged(
  previous: CronJobRunFingerprint,
  next: CronJobRunFingerprint
): boolean {
  if (previous.lastRunAt !== next.lastRunAt) {
    return true;
  }
  if (previous.lastSessionId !== next.lastSessionId) {
    return true;
  }
  return false;
}

/**
 * Diff job list against the last sync baseline.
 * Jobs without a prior fingerprint only seed the baseline (no unread).
 */
export function detectCronJobRunUpdates(
  previous: Record<string, CronJobRunFingerprint>,
  jobs: CronJobRunListItem[]
): CronJobRunUpdateResult {
  const nextFingerprints = buildCronJobFingerprintMap(jobs);
  const updatedJobIds: string[] = [];

  for (const [jobId, nextFp] of Object.entries(nextFingerprints)) {
    const prevFp = previous[jobId];
    if (!prevFp) {
      continue;
    }
    if (fingerprintChanged(prevFp, nextFp)) {
      updatedJobIds.push(jobId);
    }
  }

  return { updatedJobIds, nextFingerprints };
}
