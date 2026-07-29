import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolveClawhubTokenAction,
} from '../node_modules/.cache/clawhub-token-action/features/SourceManagerModal/clawhubTokenAction.js';

test('a non-empty token is saved as typed, trimmed', () => {
  assert.deepEqual(resolveClawhubTokenAction('  clh_abc  ', false), {
    intent: 'save',
    token: 'clh_abc',
    canSubmit: true,
  });
});

test('clearing the field clears a configured token', () => {
  assert.deepEqual(resolveClawhubTokenAction('', true), {
    intent: 'clear',
    token: '',
    canSubmit: true,
  });
  assert.deepEqual(resolveClawhubTokenAction('   ', true), {
    intent: 'clear',
    token: '',
    canSubmit: true,
  });
});

test('an empty field with no configured token cannot be submitted', () => {
  assert.deepEqual(resolveClawhubTokenAction('', false), {
    intent: 'none',
    token: '',
    canSubmit: false,
  });
  assert.deepEqual(resolveClawhubTokenAction('  ', false), {
    intent: 'none',
    token: '',
    canSubmit: false,
  });
});

test('replacing an existing token is still a save', () => {
  assert.deepEqual(resolveClawhubTokenAction('clh_new', true), {
    intent: 'save',
    token: 'clh_new',
    canSubmit: true,
  });
});

test('nullish input is treated as empty', () => {
  assert.equal(resolveClawhubTokenAction(undefined, false).canSubmit, false);
  assert.equal(resolveClawhubTokenAction(null, true).intent, 'clear');
});
