import assert from 'node:assert/strict';
import test from 'node:test';

import { useChatStore } from '../node_modules/.cache/chat-store-streaming/chatStore.mjs';

test('a mid-turn user message can preserve live reasoning', () => {
  const sessionId = 'steer-preserve-reasoning';
  const store = useChatStore.getState();
  store.ensureRuntime(sessionId);
  store.appendReasoning(sessionId, 'thinking about the plan');
  const before = useChatStore.getState().getRuntime(sessionId)?.reasoningSegments ?? [];
  assert.ok(before.length > 0);

  useChatStore.getState().addMessage(
    sessionId,
    {
      id: 'user-steer-1',
      role: 'user',
      content: 'prefer async',
      timestamp: new Date().toISOString(),
    },
    { preserveLiveReasoning: true },
  );
  const afterSteer = useChatStore.getState().getRuntime(sessionId)?.reasoningSegments ?? [];
  assert.equal(afterSteer.length, before.length);

  useChatStore.getState().addMessage(sessionId, {
    id: 'user-new-turn',
    role: 'user',
    content: 'next turn',
    timestamp: new Date().toISOString(),
  });
  const afterNewTurn = useChatStore.getState().getRuntime(sessionId)?.reasoningSegments ?? [];
  assert.equal(afterNewTurn.length, 0);

  useChatStore.getState().removeRuntime(sessionId);
});

test('setThinking does not notify subscribers when the value is unchanged', () => {
  const sessionId = 'streaming-thinking-noop';
  useChatStore.getState().ensureRuntime(sessionId);
  let notifications = 0;
  const unsubscribe = useChatStore.subscribe(() => {
    notifications += 1;
  });

  try {
    useChatStore.getState().setThinking(sessionId, false);
    assert.equal(notifications, 0);

    useChatStore.getState().setThinking(sessionId, true);
    assert.equal(notifications, 1);

    useChatStore.getState().setThinking(sessionId, true);
    assert.equal(notifications, 1);
  } finally {
    unsubscribe();
    useChatStore.getState().removeRuntime(sessionId);
  }
});
