import assert from 'node:assert/strict';
import test from 'node:test';

import { shouldFreezeChatStreamOnGoalSnapshot } from '../node_modules/.cache/goal-stream-guard/hooks/goalStreamGuard.js';
import { useChatStore } from '../node_modules/.cache/chat-store-streaming/chatStore.mjs';

test('goal.snapshot set/resume should freeze the current chat stream', () => {
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'set' }), true);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'SET' }), true);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'resume' }), true);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'get' }), false);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'pause' }), false);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'clear' }), false);
  assert.equal(shouldFreezeChatStreamOnGoalSnapshot({}), false);
});

test('freezing currentStreamId keeps prior assistant content for a new Goal bubble', () => {
  const sessionId = 'goal-stream-freeze';
  useChatStore.getState().ensureRuntime(sessionId);

  try {
    useChatStore.getState().addMessage(sessionId, {
      id: 'assistant-essay',
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
    });
    useChatStore.getState().startStreaming(sessionId, 'assistant-essay');
    useChatStore.getState().appendStreamContent(sessionId, '作文正文');

    assert.equal(shouldFreezeChatStreamOnGoalSnapshot({ action: 'set' }), true);
    useChatStore.getState().stopStreaming(sessionId);

    const afterFreeze = useChatStore.getState().getRuntime(sessionId);
    assert.equal(afterFreeze?.currentStreamId, null);
    assert.equal(afterFreeze?.messages.find((m) => m.id === 'assistant-essay')?.content, '作文正文');
    assert.equal(afterFreeze?.messages.find((m) => m.id === 'assistant-essay')?.isStreaming, false);

    useChatStore.getState().addMessage(sessionId, {
      id: 'assistant-goal',
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
    });
    useChatStore.getState().startStreaming(sessionId, 'assistant-goal');
    useChatStore.getState().appendStreamContent(sessionId, '笑话正文');

    const afterGoal = useChatStore.getState().getRuntime(sessionId);
    assert.equal(afterGoal?.currentStreamId, 'assistant-goal');
    assert.equal(afterGoal?.messages.find((m) => m.id === 'assistant-essay')?.content, '作文正文');
    assert.equal(afterGoal?.messages.find((m) => m.id === 'assistant-goal')?.content, '笑话正文');
  } finally {
    useChatStore.getState().removeRuntime(sessionId);
  }
});
