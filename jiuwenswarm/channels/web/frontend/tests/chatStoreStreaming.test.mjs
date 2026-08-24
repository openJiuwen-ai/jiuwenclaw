import assert from 'node:assert/strict';
import test from 'node:test';

import { useChatStore } from '../node_modules/.cache/chat-store-streaming/chatStore.mjs';

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

test('reviewer-only progress preserves the running tool lifecycle', () => {
  const sessionId = 'streaming-reviewer-progress';
  const toolCallId = 'call-reviewer-progress';
  useChatStore.getState().ensureRuntime(sessionId);

  try {
    useChatStore.getState().addToolCall(sessionId, {
      id: toolCallId,
      name: 'bash',
      arguments: { command: 'echo ok' },
    });
    const before = useChatStore.getState().getRuntime(sessionId).toolExecutions.get(toolCallId);

    useChatStore.getState().updateToolReviewer(sessionId, toolCallId, {
      reviewer_status: 'approved',
      final_reviewer_status: 'approved',
      decision_source: 'auto_reviewer',
    });

    const updated = useChatStore.getState().getRuntime(sessionId).toolExecutions.get(toolCallId);
    assert.equal(updated.status, 'pending');
    assert.equal(updated.result, undefined);
    assert.equal(updated.updatedAt, before.updatedAt);
    assert.equal(updated.toolCall.reviewer.final_reviewer_status, 'approved');

    useChatStore.getState().updateToolReviewer(sessionId, toolCallId, {
      reviewer_status: 'manual',
    });
    const late = useChatStore.getState().getRuntime(sessionId).toolExecutions.get(toolCallId);
    assert.equal(late.status, 'pending');
    assert.equal(late.result, undefined);
    assert.equal(late.updatedAt, before.updatedAt);
    assert.equal(late.toolCall.reviewer.final_reviewer_status, 'approved');

    useChatStore.getState().addToolResult(sessionId, {
      toolName: 'bash',
      toolCallId,
      result: 'command failed',
      success: false,
      reviewer: {
        reviewer_status: 'approved',
        final_reviewer_status: 'approved',
        decision_source: 'auto_reviewer',
      },
    });
    const failed = useChatStore.getState().getRuntime(sessionId).toolExecutions.get(toolCallId);
    assert.equal(failed.status, 'error');
    assert.equal(failed.result.success, false);
    assert.equal(failed.result.reviewer.final_reviewer_status, 'approved');
    const failedUpdatedAt = failed.updatedAt;

    useChatStore.getState().updateToolReviewer(sessionId, toolCallId, {
      reviewer_status: 'manual',
    });
    const afterLateUpdate = useChatStore.getState().getRuntime(sessionId).toolExecutions.get(toolCallId);
    assert.equal(afterLateUpdate.status, 'error');
    assert.equal(afterLateUpdate.result, failed.result);
    assert.equal(afterLateUpdate.updatedAt, failedUpdatedAt);
    assert.equal(afterLateUpdate.result.reviewer.final_reviewer_status, 'approved');

    useChatStore.getState().updateToolReviewer(sessionId, 'unknown-call', {
      final_reviewer_status: 'denied',
    });
    assert.equal(
      useChatStore.getState().getRuntime(sessionId).toolExecutions.has('unknown-call'),
      false,
    );
  } finally {
    useChatStore.getState().removeRuntime(sessionId);
  }
});
