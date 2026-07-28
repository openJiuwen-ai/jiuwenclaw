import assert from 'node:assert/strict';
import test from 'node:test';

import {
  dismissHistoryPagerMessage,
  initHistoryPagerMessageState,
  reduceHistoryPagerMessage,
} from '../node_modules/.cache/history-pager-message/components/ChatPanel/historyPagerMessage.js';

function apply(state, steps) {
  return steps.reduce((acc, progress) => reduceHistoryPagerMessage(acc, progress), state);
}

test('first render of an unfinished history does not show the loaded message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = reduceHistoryPagerMessage(initial, { loadedPages: 1, loadingMore: false });
  assert.equal(state.showLoadedMessage, false);
});

test('background prefetch pages do not show the loaded message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = apply(initial, [
    { loadedPages: 2, loadingMore: false },
    { loadedPages: 3, loadingMore: false },
  ]);
  assert.equal(state.showLoadedMessage, false);
});

test('completed load more shows the loaded message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 2, loadingMore: false },
  ]);
  assert.equal(state.showLoadedMessage, true);
});

test('load more that batches page increase with loading end still shows the message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 2, loadingMore: true },
    { loadedPages: 2, loadingMore: false },
  ]);
  assert.equal(state.showLoadedMessage, true);
});

test('failed load more does not show the loaded message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 1, loadingMore: false },
  ]);
  assert.equal(state.showLoadedMessage, false);
});

test('starting a new load more hides a visible message', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const loaded = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 2, loadingMore: false },
  ]);
  assert.equal(loaded.showLoadedMessage, true);

  const loadingAgain = reduceHistoryPagerMessage(loaded, { loadedPages: 2, loadingMore: true });
  assert.equal(loadingAgain.showLoadedMessage, false);
});

test('a second load more shows the message again', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const state = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 2, loadingMore: false },
    { loadedPages: 2, loadingMore: true },
    { loadedPages: 3, loadingMore: false },
  ]);
  assert.equal(state.showLoadedMessage, true);
});

test('reduce keeps state identity when nothing changes', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 2, loadingMore: false });
  assert.equal(reduceHistoryPagerMessage(initial, { loadedPages: 2, loadingMore: false }), initial);

  const loading = reduceHistoryPagerMessage(initial, { loadedPages: 2, loadingMore: true });
  assert.equal(reduceHistoryPagerMessage(loading, { loadedPages: 3, loadingMore: true }), loading);
});

test('dismiss clears the message and is a no-op when already hidden', () => {
  const initial = initHistoryPagerMessageState({ loadedPages: 1, loadingMore: false });
  const loaded = apply(initial, [
    { loadedPages: 1, loadingMore: true },
    { loadedPages: 2, loadingMore: false },
  ]);
  const dismissed = dismissHistoryPagerMessage(loaded);
  assert.equal(dismissed.showLoadedMessage, false);
  assert.equal(dismissHistoryPagerMessage(dismissed), dismissed);
});
