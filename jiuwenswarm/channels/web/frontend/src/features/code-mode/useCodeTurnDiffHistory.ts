import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Message, ProjectInfo } from '../../types';
import { gitClient } from './gitClient';
import type { GitTurnDiff } from './types';
import { bindTurnDiffsToMessages } from './codeTurnDiffBinding';
export { bindTurnDiffsToMessages } from './codeTurnDiffBinding';

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
