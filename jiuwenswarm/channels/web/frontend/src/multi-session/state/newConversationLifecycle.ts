import {
  ensureSessionRuntimes,
  useChatStore,
  useHarnessStore,
  useSessionStore,
  useTodoStore,
} from '../../stores';
import type { AgentMode, Session } from '../../types';

export const NEW_CONVERSATION_ID = 'new';

interface ConversationRuntimeSettings {
  mode: AgentMode;
  selectedModelName: string | null;
}

const locallyCreatedConversations = new Map<string, Session>();

export function createConversationTitle(content: string): string {
  return content.trim().replace(/\n/g, ' ');
}

function applyRuntimeSettings(
  sessionId: string,
  { mode, selectedModelName }: ConversationRuntimeSettings,
): void {
  ensureSessionRuntimes(sessionId);
  useSessionStore.getState().setMode(sessionId, mode);
  if (selectedModelName) {
    useSessionStore.getState().setSelectedModelName(sessionId, selectedModelName);
  }
}

export function resetNewConversationRuntime(settings: ConversationRuntimeSettings): void {
  const preservedDraft = useChatStore.getState().getRuntime(NEW_CONVERSATION_ID)?.inputValue ?? '';
  useChatStore.getState().removeRuntime(NEW_CONVERSATION_ID);
  useSessionStore.getState().removeRuntime(NEW_CONVERSATION_ID);
  useTodoStore.getState().removeRuntime(NEW_CONVERSATION_ID);
  useHarnessStore.getState().removeRuntime(NEW_CONVERSATION_ID);
  applyRuntimeSettings(NEW_CONVERSATION_ID, settings);
  if (preservedDraft) {
    useChatStore.getState().setInputValue(NEW_CONVERSATION_ID, preservedDraft);
  }
  useChatStore.getState().setActiveSessionId(NEW_CONVERSATION_ID);
}

export function registerCreatedConversation(
  sessionId: string,
  settings: ConversationRuntimeSettings,
  createdAt = Date.now(),
  initialContent = '',
): void {
  applyRuntimeSettings(sessionId, settings);
  useChatStore.getState().setProcessing(sessionId, true);

  const timestamp = new Date(createdAt).toISOString();
  const session: Session = {
    session_id: sessionId,
    title: createConversationTitle(initialContent),
    project_path: '',
    mode: settings.mode,
    status: 'active',
    message_count: 0,
    created_at: timestamp,
    updated_at: timestamp,
    last_message_at: createdAt,
    last_user_message_at: createdAt,
    is_processing: true,
  };
  locallyCreatedConversations.set(sessionId, session);
  useSessionStore.getState().addSession(session);
}

/**
 * Keep a session confirmed by session.create visible until session.list has
 * observed it. This closes the consistency window between the two endpoints.
 */
export function reconcileCreatedConversations(serverSessions: Session[]): Session[] {
  const reconciledServerSessions = serverSessions.map((serverSession) => {
    const localSession = locallyCreatedConversations.get(serverSession.session_id);
    if (!localSession) return serverSession;

    if (serverSession.title?.trim() || !localSession.title?.trim()) {
      locallyCreatedConversations.delete(serverSession.session_id);
      return serverSession;
    }

    return { ...serverSession, title: localSession.title };
  });

  const serverSessionIds = new Set(serverSessions.map((session) => session.session_id));

  const pendingSessions = Array.from(locallyCreatedConversations.values()).filter(
    (session) => !serverSessionIds.has(session.session_id),
  );
  return [...pendingSessions, ...reconciledServerSessions];
}

export function forgetCreatedConversation(sessionId: string): void {
  locallyCreatedConversations.delete(sessionId);
}

export function isConversationMissing(
  sessionId: string,
  initialDataLoaded: boolean,
  sessions: Session[],
): boolean {
  return initialDataLoaded
    && !locallyCreatedConversations.has(sessionId)
    && !sessions.some((session) => session.session_id === sessionId);
}
