import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatSteerRejection,
  withDraftRestoredNote,
} from '../node_modules/.cache/steer-rejection/features/steerRejection.js';

test('known tokens get written messages', () => {
  assert.match(formatSteerRejection('en', 'interaction_terminated'), /already finished/);
  assert.match(formatSteerRejection('zh', 'unsupported_runtime'), /不支持/);
});

test('unknown tokens echo rather than collapse', () => {
  assert.match(formatSteerRejection('en', 'brand_new_reason'), /brand_new_reason/);
});

test('draft restore note is conditional', () => {
  const base = formatSteerRejection('en', 'interaction_terminated');
  assert.doesNotMatch(withDraftRestoredNote('en', base, false), /composer/);
  assert.match(withDraftRestoredNote('en', base, true), /composer/);
  assert.match(
    withDraftRestoredNote('zh', formatSteerRejection('zh', 'no_active_round'), true),
    /输入框/,
  );
});
