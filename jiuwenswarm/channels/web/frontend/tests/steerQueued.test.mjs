import assert from 'node:assert/strict';
import test from 'node:test';

import { formatSteerQueuedNote } from '../node_modules/.cache/steer-queued/features/steerQueued.js';

test('steer_queued explains next model step', () => {
  assert.match(formatSteerQueuedNote('en', 'steer_queued'), /next model step/i);
  assert.match(formatSteerQueuedNote('zh', 'steer_queued'), /下一步模型调用/);
});

test('follow_up_queued explains next attempt', () => {
  assert.match(formatSteerQueuedNote('en', 'follow_up_queued'), /next attempt/i);
  assert.match(formatSteerQueuedNote('zh', 'follow_up_queued'), /下一次尝试/);
});

test('other dispositions stay silent', () => {
  assert.equal(formatSteerQueuedNote('en', 'turn_queued'), null);
  assert.equal(formatSteerQueuedNote('en', undefined), null);
  assert.equal(formatSteerQueuedNote('zh', 'rejected'), null);
});
