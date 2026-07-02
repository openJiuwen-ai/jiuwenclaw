import { useChatStore } from './chatStore';
import { useHarnessStore } from './harnessStore';
import { useSessionStore } from './sessionStore';
import { useTodoStore } from './todoStore';

export function ensureSessionRuntimes(sessionId: string): void {
  useChatStore.getState().ensureRuntime(sessionId);
  useSessionStore.getState().ensureRuntime(sessionId);
  useTodoStore.getState().ensureRuntime(sessionId);
  useHarnessStore.getState().ensureRuntime(sessionId);
}
