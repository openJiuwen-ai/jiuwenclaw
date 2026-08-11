import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  countDroppedSteers,
  listDroppedSteerIds,
  shouldReportSteerApplied,
  steerUserBubbleId,
} from '../node_modules/.cache/steer-applied/features/steerApplied.js';

// The event's whole purpose: the ACK promises the text was queued, not that the
// model read it. A rail can drop it in between, and this is the only signal.

test('a dropped steer is counted', () => {
  assert.equal(countDroppedSteers({ applied: [], dropped: ['s1', 's2'] }), 2);
});

test('object-shaped drop markers count and list ids', () => {
  assert.equal(countDroppedSteers({ dropped: [{ id: 'x' }] }), 1);
  assert.deepEqual(listDroppedSteerIds({ dropped: ['a', { id: 'b' }, { noid: 1 }] }), [
    'a',
    'b',
  ]);
});

test('nothing dropped reports nothing', () => {
  assert.equal(countDroppedSteers({ applied: [{ id: 's1', text: 'x' }], dropped: [] }), 0);
  assert.equal(shouldReportSteerApplied({ applied: [{ id: 's1' }], dropped: [] }), false);
});

test('a drop is worth reporting', () => {
  assert.equal(shouldReportSteerApplied({ dropped: ['s1'] }), true);
});

test('steer bubble ids are stable for matching drops', () => {
  assert.equal(steerUserBubbleId('req-1'), 'user-steer-req-1');
});

// These values come off the wire, so every shape below is reachable. A malformed
// payload must leave the transcript alone rather than announce a drop that may
// not have happened -- and must not throw, because doing so inside a socket
// handler would take down the events queued behind it.
test('a malformed payload reports no drops instead of throwing', () => {
  for (const bad of [
    undefined,
    null,
    {},
    { dropped: undefined },
    { dropped: null },
    { dropped: 's1' },
    { dropped: 3 },
    { dropped: { s1: true } },
  ]) {
    assert.equal(countDroppedSteers(bad), 0, `payload ${JSON.stringify(bad)}`);
    assert.equal(shouldReportSteerApplied(bad), false);
    assert.deepEqual(listDroppedSteerIds(bad), []);
  }
});

// A string would otherwise count its characters, which is the specific way this
// goes wrong quietly: `dropped: "s1"` has length 2 and would claim two drops.
test('a string dropped field does not count its characters', () => {
  assert.equal(countDroppedSteers({ dropped: 'abcdef' }), 0);
});
