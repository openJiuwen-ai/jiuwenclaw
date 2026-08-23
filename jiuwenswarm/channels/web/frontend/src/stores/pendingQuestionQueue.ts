import type {
  AskUserQuestionPayload,
  UserAnswer,
} from '../types';

const PERMISSION_SOURCES = new Set(['permission_interrupt']);

function boundedIdentity(value: unknown, limit: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim();
  return normalized && normalized.length <= limit ? normalized : undefined;
}

export function pendingQuestionIdentity(payload: AskUserQuestionPayload): string | undefined {
  if (PERMISSION_SOURCES.has(payload.source ?? '')) {
    if (payload.questions.length !== 1) return undefined;
    const cardId = boundedIdentity(payload.questions[0]?.card_id, 128);
    return cardId ? `permission\u0000${cardId}` : undefined;
  }
  const requestId = boundedIdentity(payload.request_id, 512);
  if (!requestId) return undefined;
  return `interaction\u0000${payload.source ?? ''}\u0000${requestId}`;
}

function splitForQueue(payload: AskUserQuestionPayload): AskUserQuestionPayload[] {
  if (!PERMISSION_SOURCES.has(payload.source ?? '')) return [payload];
  return payload.questions.length === 1 ? [payload] : [];
}

export function enqueuePendingQuestions(
  queue: readonly AskUserQuestionPayload[],
  payload: AskUserQuestionPayload,
): AskUserQuestionPayload[] {
  const next = [...queue];
  for (const item of splitForQueue(payload)) {
    const identity = pendingQuestionIdentity(item);
    if (!identity) continue;
    const existing = next.findIndex((candidate) => pendingQuestionIdentity(candidate) === identity);
    if (existing >= 0) {
      next[existing] = item;
    }
    else next.push(item);
  }
  return next;
}

export function clearPermissionQuestions(
  queue: readonly AskUserQuestionPayload[],
): AskUserQuestionPayload[] {
  return queue.filter((item) => !PERMISSION_SOURCES.has(item.source ?? ''));
}

export function shouldClearPermissionQuestionsForLifecycleEvent(
  event: 'retract' | 'pause' | 'cancel' | 'supplement',
  success = true,
): boolean {
  return event === 'retract' || (success && (event === 'cancel' || event === 'supplement'));
}

export function consumePendingQuestion(
  queue: readonly AskUserQuestionPayload[],
  payload: AskUserQuestionPayload,
): AskUserQuestionPayload[] {
  const identity = pendingQuestionIdentity(payload);
  if (!identity) return [...queue];
  const index = queue.findIndex((candidate) => pendingQuestionIdentity(candidate) === identity);
  if (index < 0) return [...queue];
  return [...queue.slice(0, index), ...queue.slice(index + 1)];
}

export function bindPendingPermissionCard(
  answers: UserAnswer[],
  questions: AskUserQuestionPayload['questions'],
): UserAnswer[] {
  if (answers.length !== 1 || questions.length !== 1) return [];
  const cardId = boundedIdentity(questions[0]?.card_id, 128);
  if (!cardId) return [];
  const answer = { ...answers[0] };
  delete answer.card_id;
  return [{ ...answer, card_id: cardId }];
}
