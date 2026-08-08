import assert from 'node:assert/strict';
import test from 'node:test';

import {
  INITIAL_DENY_DRAFT,
  buildDenyAnswers,
  canCollectDenyFeedback,
  handleDenyEscape,
  reduceDenyDraft,
  resolveAuthActionLabel,
  resolveAuthActionTip,
} from '../node_modules/.cache/authorization-prompt-answers/authorizationPromptAnswers.mjs';

const tEn = (key) =>
  ({
    'authPrompt.allowOnce': 'Allow once',
    'authPrompt.allowAlways': 'Always allow',
    'authPrompt.sessionAllow': 'Remember for session',
    'authPrompt.reject': 'Reject',
    'authPrompt.tip.allowOnce': 'Allow this action only this time',
    'authPrompt.tip.reject': 'Reject this action',
  })[key] ?? key;

const questions = [
  {
    question: 'Allow write?',
    header: 'Write',
    options: [
      { label: 'Allow once', value: 'allow_once' },
      { label: 'Reject', value: 'reject' },
    ],
  },
  {
    question: 'Allow shell?',
    header: 'Shell',
    options: [
      { label: '本次允许', value: 'allow_once' },
      { label: '拒绝', value: '拒绝' },
    ],
  },
];

test('buildDenyAnswers selects each reject value and trims feedback', () => {
  assert.deepEqual(buildDenyAnswers(questions, '  use Read instead  '), [
    { selected_options: ['reject'], custom_input: 'use Read instead' },
    { selected_options: ['拒绝'], custom_input: 'use Read instead' },
  ]);
});

test('buildDenyAnswers keeps an empty deny note valid', () => {
  assert.deepEqual(buildDenyAnswers([questions[0]], '   '), [
    { selected_options: ['reject'], custom_input: '' },
  ]);
});

test('buildDenyAnswers falls back to the localized reject label', () => {
  const questionWithoutClassifiedReject = {
    question: 'Continue?',
    header: 'Confirm',
    options: [{ label: '拒绝此操作', value: 'deny-operation' }],
  };

  assert.deepEqual(buildDenyAnswers([questionWithoutClassifiedReject], 'No'), [
    { selected_options: ['deny-operation'], custom_input: 'No' },
  ]);
});

test('reduceDenyDraft clears mode and note when the request changes', () => {
  const staleDraft = { mode: true, note: 'use Read instead' };

  assert.deepEqual(reduceDenyDraft(staleDraft, { type: 'reset' }), INITIAL_DENY_DRAFT);
});

test('reduceDenyDraft opens deny mode and updates its note', () => {
  const opened = reduceDenyDraft(INITIAL_DENY_DRAFT, { type: 'open' });
  assert.deepEqual(opened, { mode: true, note: '' });
  assert.deepEqual(reduceDenyDraft(opened, { type: 'update-note', note: 'use Read' }), {
    mode: true,
    note: 'use Read',
  });
});

test('canCollectDenyFeedback only allows permission_interrupt and confirm_interrupt (#2, #3)', () => {
  assert.equal(canCollectDenyFeedback('permission_interrupt'), true);
  assert.equal(canCollectDenyFeedback('confirm_interrupt'), true);
  // activate_confirm's activate_response never threads custom_input/feedback through,
  // so the deny-note UI must stay suppressed there (#2).
  assert.equal(canCollectDenyFeedback('activate_confirm'), false);
  // Keep Web scope aligned with the TUI helper, which only covers permission/confirm (#3).
  assert.equal(canCollectDenyFeedback('evolution_interrupt'), false);
  assert.equal(canCollectDenyFeedback('ask_user_interrupt'), false);
  assert.equal(canCollectDenyFeedback(undefined), false);
  assert.equal(canCollectDenyFeedback('confirm_interrupt', 'plan_approval'), false);
});

test('resolveAuthActionLabel maps known semantics to i18n keys', () => {
  assert.equal(resolveAuthActionLabel('reject', '拒绝', tEn), 'Reject');
  assert.equal(resolveAuthActionLabel('allow-once', '本次允许', tEn), 'Allow once');
  assert.equal(resolveAuthActionLabel('other', 'Custom', tEn), 'Custom');
});

test('resolveAuthActionTip prefers i18n over backend description', () => {
  assert.equal(resolveAuthActionTip('reject', '后端说明', tEn), 'Reject this action');
  assert.equal(resolveAuthActionTip('other', 'Backend tip', tEn), 'Backend tip');
});

test('handleDenyEscape cancels deny mode only for Escape', () => {
  let cancelCount = 0;
  let preventedCount = 0;
  const cancel = () => {
    cancelCount += 1;
  };

  handleDenyEscape({ key: 'Enter', preventDefault: () => { preventedCount += 1; } }, cancel);
  assert.equal(cancelCount, 0);
  assert.equal(preventedCount, 0);

  handleDenyEscape({ key: 'Escape', preventDefault: () => { preventedCount += 1; } }, cancel);
  assert.equal(cancelCount, 1);
  assert.equal(preventedCount, 1);
});
