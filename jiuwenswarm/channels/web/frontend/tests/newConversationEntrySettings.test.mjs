import assert from 'node:assert/strict';
import test from 'node:test';

import {
  registerCreatedConversation,
  resolveNewConversationEntrySettings,
} from '../node_modules/.cache/new-conversation-entry-settings/newConversationLifecycle.mjs';

test('returning to an unsent new conversation keeps its mode and model', () => {
  assert.deepEqual(
    resolveNewConversationEntrySettings('agent', 'main-model', 'other-model', {
      mode: 'team',
      selectedModelName: 'draft-model',
    }),
    { mode: 'team', selectedModelName: 'draft-model' },
  );
});

test('starting a new conversation still uses the configured default model', () => {
  assert.deepEqual(
    resolveNewConversationEntrySettings('team', 'main-model', 'other-model'),
    { mode: 'team', selectedModelName: 'main-model' },
  );
});

test('new conversation keeps the current model only while the model list is loading', () => {
  assert.deepEqual(
    resolveNewConversationEntrySettings('agent', null, 'other-model'),
    { mode: 'agent', selectedModelName: 'other-model' },
  );
});

for (const workMode of ['work', 'code']) {
  test(`${workMode} registration preserves the server persist flag without leaking it to ordinary sessions`, () => {
    const settings = { mode: 'agent', selectedModelName: 'test-model' };
    const persisted = registerCreatedConversation(
      `persist-${workMode}`, { ...settings, persistSession: true }, 0, '跟进发布',
      { work_mode: workMode, persist_session: true },
    );
    assert.equal(persisted.persist_session, true);
    assert.equal(persisted.title, '跟进发布');
    assert.equal(persisted.work_mode, workMode);
    const ordinary = registerCreatedConversation(
      `ordinary-${workMode}`, settings, 0, '普通任务', { work_mode: workMode },
    );
    assert.equal(ordinary.persist_session, false);
  });
}
