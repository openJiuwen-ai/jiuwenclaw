import assert from 'node:assert/strict';
import test from 'node:test';

import { copyText } from '../node_modules/.cache/copy-text/utils/copyText.js';

function okClipboard(sink) {
  return { writeText: async (text) => { sink.push(text); } };
}

const failingClipboard = {
  writeText: async () => {
    throw new Error('NotAllowedError: clipboard write denied');
  },
};

test('returns true when the Clipboard API succeeds', async () => {
  const written = [];
  const copied = await copyText('hello', { clipboard: okClipboard(written) });
  assert.equal(copied, true);
  assert.deepEqual(written, ['hello']);
});

test('falls back to execCommand when the Clipboard API throws', async () => {
  const legacyCalls = [];
  const copied = await copyText('hello', {
    clipboard: failingClipboard,
    legacyCopy: (text) => {
      legacyCalls.push(text);
      return true;
    },
  });
  assert.equal(copied, true);
  assert.deepEqual(legacyCalls, ['hello']);
});

test('returns false when both the Clipboard API and execCommand fail', async () => {
  const copied = await copyText('hello', {
    clipboard: failingClipboard,
    legacyCopy: () => false,
  });
  assert.equal(copied, false);
});

test('returns false when execCommand throws', async () => {
  const copied = await copyText('hello', {
    clipboard: failingClipboard,
    legacyCopy: () => {
      throw new Error('execCommand is not a function');
    },
  });
  assert.equal(copied, false);
});

test('uses the fallback when no clipboard is available', async () => {
  const copied = await copyText('hello', { clipboard: null, legacyCopy: () => true });
  assert.equal(copied, true);
});

test('returns false for empty text without touching the clipboard', async () => {
  let called = false;
  const copied = await copyText('', {
    clipboard: { writeText: async () => { called = true; } },
    legacyCopy: () => { called = true; return true; },
  });
  assert.equal(copied, false);
  assert.equal(called, false);
});
