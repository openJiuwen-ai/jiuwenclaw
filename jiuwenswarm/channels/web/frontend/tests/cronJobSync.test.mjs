import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCronJobFingerprintMap,
  detectCronJobRunUpdates,
  fingerprintFromCronJob,
} from '../node_modules/.cache/cron-job-sync/cronJobSync.mjs';

test('fingerprintFromCronJob normalizes last_run_at and last_session_id', () => {
  assert.deepEqual(
    fingerprintFromCronJob({
      id: 'job-1',
      last_run_at: '1735689600',
      last_session_id: '  cron_abc_job-1  ',
    }),
    {
      jobId: 'job-1',
      lastRunAt: 1735689600,
      lastSessionId: 'cron_abc_job-1',
    }
  );
});

test('detectCronJobRunUpdates seeds baseline without unread on first sight', () => {
  const jobs = [{ id: 'job-1', last_run_at: 100, last_session_id: 'cron_a' }];
  const result = detectCronJobRunUpdates({}, jobs);
  assert.deepEqual(result.updatedJobIds, []);
  assert.deepEqual(result.nextFingerprints, buildCronJobFingerprintMap(jobs));
});

test('detectCronJobRunUpdates marks last_run_at changes', () => {
  const previous = buildCronJobFingerprintMap([
    { id: 'job-1', last_run_at: 100, last_session_id: 'cron_a' },
  ]);
  const result = detectCronJobRunUpdates(previous, [
    { id: 'job-1', last_run_at: 200, last_session_id: 'cron_b' },
  ]);
  assert.deepEqual(result.updatedJobIds, ['job-1']);
});

test('detectCronJobRunUpdates ignores unchanged snapshots', () => {
  const jobs = [{ id: 'job-1', last_run_at: 100, last_session_id: 'cron_a' }];
  const previous = buildCronJobFingerprintMap(jobs);
  const result = detectCronJobRunUpdates(previous, jobs);
  assert.deepEqual(result.updatedJobIds, []);
});
