import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_WORK_MODE,
  WORK_MODE_STORAGE_KEY,
  normalizeWorkMode,
  readWorkMode,
  writeWorkMode,
} from '../node_modules/.cache/work-mode-storage/features/workspace/workModeStorage.js';

function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, value),
    read: (key) => (map.has(key) ? map.get(key) : null),
  };
}

function throwingStorage() {
  return {
    getItem: () => {
      throw new Error('SecurityError: storage is disabled');
    },
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
  };
}

test('normalizeWorkMode only accepts known modes', () => {
  assert.equal(normalizeWorkMode('code'), 'code');
  assert.equal(normalizeWorkMode('work'), 'work');
  assert.equal(normalizeWorkMode(null), DEFAULT_WORK_MODE);
  assert.equal(normalizeWorkMode('CODE'), DEFAULT_WORK_MODE);
  assert.equal(normalizeWorkMode({ mode: 'code' }), DEFAULT_WORK_MODE);
});

test('readWorkMode returns the stored mode', () => {
  const storage = memoryStorage({ [WORK_MODE_STORAGE_KEY]: 'code' });
  assert.equal(readWorkMode(storage), 'code');
});

test('readWorkMode falls back to the default when storage is missing', () => {
  assert.equal(readWorkMode(null), DEFAULT_WORK_MODE);
  assert.equal(readWorkMode(undefined), DEFAULT_WORK_MODE);
  assert.equal(readWorkMode(memoryStorage()), DEFAULT_WORK_MODE);
});

test('readWorkMode falls back to the default when storage throws', () => {
  assert.equal(readWorkMode(throwingStorage()), DEFAULT_WORK_MODE);
});

test('writeWorkMode persists the mode', () => {
  const storage = memoryStorage();
  assert.equal(writeWorkMode(storage, 'code'), true);
  assert.equal(storage.read(WORK_MODE_STORAGE_KEY), 'code');
});

test('writeWorkMode reports failure instead of throwing', () => {
  assert.equal(writeWorkMode(throwingStorage(), 'code'), false);
  assert.equal(writeWorkMode(null, 'code'), false);
});
