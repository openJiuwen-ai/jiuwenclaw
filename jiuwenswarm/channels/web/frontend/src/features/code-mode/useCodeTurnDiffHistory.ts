import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Message, ProjectInfo } from '../../types';
import { gitClient } from './gitClient';
import type { GitTurnDiff } from './types';

function isAssistantTurnAnchor(message: Message): boolean {
  return message.role === 'assistant' || (message.role === 'system' && (message.id.startsWith('team-leader-') || message.content.startsWith('team.leader:')));
}

function findDirectMessageId(messageIds: Set<string>, turn: GitTurnDiff): string | null {
  const candidates = [
    turn.assistant_message_id,
    turn.request_id,
    turn.assistant_message_id ? `team-leader-${turn.assistant_message_id}` : '',
    turn.request_id ? `team-leader-${turn.request_id}` : '',
  ];
  return candidates.find(candidate => candidate && messageIds.has(candidate)) ?? null;
}

/** Bind backend user-turn indexes to the assistant bubble rendered for that turn. */
export function bindTurnDiffsToMessages(messages: Message[], turns: GitTurnDiff[]): Map<string, GitTurnDiff[]> {
  const messageIds = new Set(messages.map(message => message.id));
  const assistantByTurnIndex = new Map<number, string>();
  const localTurnIndexByAssistantId = new Map<string, number>();
  const assistantByUserMessageId = new Map<string, string>();
  let currentTurnIndex = 0;
  let currentUserMessageId = '';

  messages.forEach(message => {
    if (message.role === 'user') {
      currentTurnIndex += 1;
      currentUserMessageId = message.id;
      return;
    }
    if (!currentTurnIndex || !isAssistantTurnAnchor(message)) return;
    // Keep the last assistant output before the next user message as the turn result.
    assistantByTurnIndex.set(currentTurnIndex, message.id);
    localTurnIndexByAssistantId.set(message.id, currentTurnIndex);
    if (currentUserMessageId) assistantByUserMessageId.set(currentUserMessageId, message.id);
  });

  // History pagination normally provides the newest contiguous message window.
  // Prefer an exact message-id anchor; otherwise align the latest local and backend turns.
  const latestBackendTurn = turns.reduce((latest, turn) => Math.max(latest, turn.turn_index), 0);
  let turnIndexOffset = Math.max(0, latestBackendTurn - currentTurnIndex);
  let latestAnchoredTurn = 0;
  turns.forEach(turn => {
    const directMessageId = findDirectMessageId(messageIds, turn) || (turn.user_message_id ? assistantByUserMessageId.get(turn.user_message_id) : undefined);
    const localTurnIndex = directMessageId ? localTurnIndexByAssistantId.get(directMessageId) : undefined;
    if (localTurnIndex && turn.turn_index >= latestAnchoredTurn) {
      latestAnchoredTurn = turn.turn_index;
      turnIndexOffset = turn.turn_index - localTurnIndex;
    }
  });

  const result = new Map<string, GitTurnDiff[]>();
  turns.forEach(turn => {
    const messageId =
      findDirectMessageId(messageIds, turn) ||
      (turn.user_message_id ? assistantByUserMessageId.get(turn.user_message_id) : undefined) ||
      assistantByTurnIndex.get(turn.turn_index - turnIndexOffset);
    if (!messageId) return;
    const boundTurns = result.get(messageId) ?? [];
    boundTurns.push(turn);
    boundTurns.sort((left, right) => left.turn_index - right.turn_index);
    result.set(messageId, boundTurns);
  });
  return result;
}

interface UseCodeTurnDiffHistoryOptions {
  project: ProjectInfo | null;
  sessionId: string | null;
  isProcessing: boolean;
  messages: Message[];
}

export function useCodeTurnDiffHistory({ project, sessionId, isProcessing, messages }: UseCodeTurnDiffHistoryOptions) {
  const [turns, setTurns] = useState<GitTurnDiff[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequenceRef = useRef(0);
  const previousProcessingRef = useRef(isProcessing);
  const projectId = project?.work_mode === 'code' && !project.is_default ? project.project_id : null;

  const loadHistory = useCallback(async () => {
    if (!projectId || !sessionId || sessionId === 'new') {
      setTurns([]);
      setError(null);
      return;
    }
    const requestSequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestSequence;
    setLoading(true);
    setError(null);
    try {
      // limit=0 is explicitly defined by the backend as "return all turns".
      const response = await gitClient.turnDiffList(projectId, sessionId, { limit: 0 });
      if (requestSequenceRef.current !== requestSequence) return;
      setTurns(response.turns);
    } catch (nextError) {
      if (requestSequenceRef.current !== requestSequence) return;
      console.warn('[code-mode] Failed to load turn diff history', nextError);
      setError(nextError instanceof Error ? nextError.message : '加载逐轮修改历史失败');
    } finally {
      if (requestSequenceRef.current === requestSequence) setLoading(false);
    }
  }, [projectId, sessionId]);

  useEffect(() => {
    requestSequenceRef.current += 1;
    previousProcessingRef.current = isProcessing;
    setTurns([]);
    setError(null);
  }, [projectId, sessionId]);

  useEffect(() => {
    const completed = previousProcessingRef.current && !isProcessing;
    previousProcessingRef.current = isProcessing;
    if (isProcessing) return;
    const timer = window.setTimeout(() => void loadHistory(), completed ? 350 : 0);
    return () => window.clearTimeout(timer);
  }, [isProcessing, loadHistory]);

  const turnsByMessageId = useMemo(() => bindTurnDiffsToMessages(messages, turns), [messages, turns]);

  return { turns, turnsByMessageId, loading, error, reload: loadHistory };
}
