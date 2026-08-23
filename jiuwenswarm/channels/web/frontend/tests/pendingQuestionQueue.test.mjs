import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bindPendingPermissionCard,
  clearPermissionQuestions,
  consumePendingQuestion,
  enqueuePendingQuestions,
  pendingQuestionIdentity,
  shouldClearPermissionQuestionsForLifecycleEvent,
} from '../node_modules/.cache/pending-question-queue/pendingQuestionQueue.mjs';

function permission(cardId, text = cardId) {
  return {
    request_id: 'resume-request',
    source: 'permission_interrupt',
    questions: [{
      header: '权限审批',
      question: text,
      options: [{ label: '本次允许' }, { label: '拒绝' }],
      card_id: cardId,
    }],
  };
}

test('keeps one backend-addressed permission card per queue entry', () => {
  const first = permission('card-a');
  const second = permission('card-b');
  const queue = enqueuePendingQuestions(enqueuePendingQuestions([], first), second);

  assert.deepEqual(queue, [first, second]);
  assert.equal(pendingQuestionIdentity(first), 'permission\u0000card-a');
  assert.equal(pendingQuestionIdentity(second), 'permission\u0000card-b');
});

test('updates one card in place and consumes only that card', () => {
  const first = permission('card-a', 'old');
  const second = permission('card-b');
  const queue = enqueuePendingQuestions(enqueuePendingQuestions([], first), second);
  const updated = enqueuePendingQuestions(queue, permission('card-a', 'new'));

  assert.equal(updated[0].questions[0].question, 'new');
  assert.equal(updated[1].questions[0].card_id, 'card-b');
  assert.deepEqual(consumePendingQuestion(updated, updated[0]), [second]);
});

test('rejects malformed and multi-question permission payloads', () => {
  assert.deepEqual(enqueuePendingQuestions([], permission('')), []);
  assert.deepEqual(enqueuePendingQuestions([], permission('x'.repeat(129))), []);
  assert.deepEqual(
    enqueuePendingQuestions([], {
      ...permission('card-a'),
      questions: [permission('card-a').questions[0], permission('card-b').questions[0]],
    }),
    [],
  );
});

test('binds one answer to the backend card and rejects batches', () => {
  const answer = [{ selected_options: ['本次允许'], card_id: 'caller' }];
  assert.deepEqual(bindPendingPermissionCard(answer, permission('card-a').questions), [
    { selected_options: ['本次允许'], card_id: 'card-a' },
  ]);
  assert.deepEqual(
    bindPendingPermissionCard([answer[0], answer[0]], [
      permission('card-a').questions[0],
      permission('card-b').questions[0],
    ]),
    [],
  );
});

test('permission cleanup preserves ordinary ask-user cards', () => {
  const askUser = {
    request_id: 'ask-request',
    source: 'ask_user_interrupt',
    questions: [{ header: 'Question', question: 'Continue?', options: [] }],
  };
  const queue = enqueuePendingQuestions(
    enqueuePendingQuestions([], permission('card-a')),
    askUser,
  );
  assert.deepEqual(clearPermissionQuestions(queue), [askUser]);
});

test('lifecycle cleanup requires a terminal successful event', () => {
  assert.equal(shouldClearPermissionQuestionsForLifecycleEvent('retract'), true);
  assert.equal(shouldClearPermissionQuestionsForLifecycleEvent('pause', true), false);
  assert.equal(shouldClearPermissionQuestionsForLifecycleEvent('cancel', false), false);
  assert.equal(shouldClearPermissionQuestionsForLifecycleEvent('cancel', true), true);
  assert.equal(shouldClearPermissionQuestionsForLifecycleEvent('supplement', true), true);
});

test('ordinary confirmations retain request-scoped identity', () => {
  const confirm = {
    request_id: 'confirm-1',
    source: 'confirm_interrupt',
    questions: [{ question: 'Switch mode?', options: [] }],
  };
  assert.equal(
    pendingQuestionIdentity(confirm),
    'interaction\u0000confirm_interrupt\u0000confirm-1',
  );
});
