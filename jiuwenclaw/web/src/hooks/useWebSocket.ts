/**
 * WebSocket Hook
 *
 * 管理 WebSocket 连接和消息处理
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import {
  ConnectionAckPayload,
  WebConnectOptions,
  WebError,
  WebRequestOptions,
  WebConnectionState,
  InterruptResultPayload,
  InterruptIntent,
  SubtaskUpdatePayload,
  AskUserQuestionPayload,
  UserAnswer,
  MediaItem,
  AgentMode,
  Session,
  ToolResult,
  ToolCall,
  UsageSummary,
  WsEvent,
} from '../types';
import { useChatStore, useTodoStore, useSessionStore } from '../stores';
import { webClient } from '../services/webClient';
import i18n from '../i18n';
import {
  fetchTtsAudio,
  playAudioBase64,
  sanitizeTtsText,
  stopAllTts,
  normalizeFinalContent,
} from '../utils';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
  tryDeepResearchStandaloneAssistantTurn,
} from '../features/tool-events/toolEventNormalizer';
import {
  shouldHandleRequestEvent,
  type ShouldHandleRequestEventOptions,
} from './requestEventFilter';

const WS_RECONNECT_EVENT = 'jiuwenclaw:ws-reconnect-request';

interface UseWebSocketOptions {
  activeSessionId?: string;
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectPath?: string;
  onConnect?: (payload: ConnectionAckPayload) => void;
  onDisconnect?: () => void;
  onError?: (error: string) => void;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  connectionState: WebConnectionState;
  request: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: WebRequestOptions
  ) => Promise<T>;
  sendMessage: (content: string, sessionId: string) => Promise<void>;
  interrupt: (
    sessionId: string,
    intent: InterruptIntent,
    options?: { newInput?: string }
  ) => Promise<void>;
  pause: (sessionId: string) => Promise<void>;
  cancel: (sessionId: string) => Promise<void>;
  supplement: (sessionId: string, newInput: string) => Promise<void>;
  resume: (sessionId: string) => Promise<void>;
  switchMode: (sessionId: string, mode: AgentMode) => Promise<void>;
  disconnect: () => void;
  sendUserAnswer: (
    sessionId: string,
    requestId: string,
    answers: UserAnswer[],
    source?: string
  ) => Promise<void>;
  getInflightCount: () => number;
}

function normalizeAgentMode(rawMode: unknown): AgentMode {
  if (typeof rawMode !== 'string') return 'agent.plan';
  const normalized = rawMode.trim().toLowerCase();
  if (normalized === 'agent.fast') return 'agent.fast';
  if (normalized === 'team') return 'team';
  return 'agent.plan';
}

const EVENT_DEDUP_WINDOW_MS = 1500;

function stringifyPayloadForDedup(payload: Record<string, unknown>): string {
  try {
    const serialized = JSON.stringify(payload);
    if (!serialized) {
      return '';
    }
    return serialized.length > 800 ? serialized.slice(0, 800) : serialized;
  } catch {
    return '';
  }
}

function makeEventDedupKey(eventName: string, payload: Record<string, unknown>): string {
  const payloadSessionId =
    typeof payload.session_id === 'string' ? payload.session_id : '';
  const payloadEventType =
    typeof payload.event_type === 'string' ? payload.event_type : '';
  const payloadSnapshot = stringifyPayloadForDedup(payload);
  return `${eventName}::${payloadSessionId}::${payloadEventType}::${payloadSnapshot}`;
}

function makeClientRequestId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
}

export { shouldHandleRequestEvent, type ShouldHandleRequestEventOptions };

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const {
    activeSessionId,
    provider,
    apiKey,
    apiBase,
    model,
    projectPath,
    onConnect,
    onDisconnect,
    onError,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] =
    useState<WebConnectionState>('idle');
  const userInputVersionRef = useRef(0);
  const activeSessionIdRef = useRef(activeSessionId);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const sendMessageRef = useRef<typeof sendMessage>();
  const recentEventRef = useRef<Map<string, number>>(new Map());
  const eventDedupDroppedRef = useRef<Record<string, number>>({});
  const activeRequestIdRef = useRef<string | null>(null);
  /** 本会话实例发出的 interrupt 请求的 ws req id（用于识别属于本 tab 的 interrupt_result） */
  const pendingInterruptRequestIdsRef = useRef<Set<string>>(new Set());

  // Stores
  const {
    addMessage,
    appendStreamContent,
    startStreaming,
    stopStreaming,
    updateMessage,
    setProcessing,
    setThinking,
    setPaused,
    setInterruptResult,
    addToolCall,
    addToolResult,
    markTimedOutExecutions,
    markPendingExecutionsCancelled,
    updateSubtask,
    clearSubtasks,
    clearMessages,
    setPendingQuestion,
    removeFromTaskQueue,
  } = useChatStore();
  const { setTodos, clearTodos } = useTodoStore();
  const {
    setMode,
    setConnected,
    setAvailableTools,
    setConnectionStats,
    updateSession,
    setContextCompressionStats,
    setContextWindowUsage,
    setHeartbeatStatus,
  } =
    useSessionStore();

  const handleTtsPlayback = useCallback(
    (messageId: string, content: string) => {
      const sanitized = sanitizeTtsText(content);
      if (!sanitized || sanitized.startsWith('[任务已中断]')) {
        return;
      }

      const { messages } = useChatStore.getState();
      const existing = messages.find((msg) => msg.id === messageId);
      if (existing?.audioBase64) {
        return;
      }

      void (async () => {
        const versionAtStart = userInputVersionRef.current;
        const ttsSessionId = activeSessionIdRef.current;
        const response = await fetchTtsAudio(
          sanitized,
          ttsSessionId && ttsSessionId !== 'new' ? ttsSessionId : undefined
        );
        if (!response?.success || !response.audio_base64) {
          return;
        }

        updateMessage(messageId, {
          audioBase64: response.audio_base64,
          audioMime: response.audio_mime,
        });

        if (versionAtStart !== userInputVersionRef.current) {
          return;
        }

        await playAudioBase64(
          response.audio_base64,
          response.audio_mime || 'audio/mpeg'
        );
      })();
    },
    [updateMessage]
  );

  const shouldHandleSessionEvent = useCallback(
    (payload: Record<string, unknown>): boolean => {
      const payloadSessionId = payload.session_id;
      if (typeof payloadSessionId !== 'string' || !payloadSessionId) {
        return true;
      }
      const currentSessionId = activeSessionIdRef.current;
      if (!currentSessionId || currentSessionId === 'new') {
        return true;
      }
      return payloadSessionId === currentSessionId;
    },
    []
  );

  const shouldHandleCurrentRequestEvent = useCallback((event: WsEvent): boolean => {
    return shouldHandleRequestEvent(event, {
      activeRequestId: activeRequestIdRef.current,
      pendingInterruptRequestIds: pendingInterruptRequestIdsRef.current,
    });
  }, []);

  const handleConnectionAck = useCallback(
    (payload: Record<string, unknown>) => {
      const ackPayload = payload as unknown as ConnectionAckPayload;
      setConnected(true);
      if (Array.isArray(ackPayload.tools)) {
        setAvailableTools(ackPayload.tools);
      }
      onConnectRef.current?.(ackPayload);
    },
    [setAvailableTools, setConnected]
  );

  // 断开连接
  const disconnect = useCallback(() => {
    webClient.disconnect();
  }, [setConnected]);

  const request = useCallback(
    async <T = unknown>(
      method: string,
      params?: Record<string, unknown>,
      requestOptions?: WebRequestOptions
    ): Promise<T> => {
      return webClient.request<T>(method, params, requestOptions);
    },
    []
  );

  // 发送聊天消息
  const sendMessage = useCallback(
    async (content: string, sessionId: string) => {
      if (!content.trim()) return;

      userInputVersionRef.current += 1;
      stopAllTts();

      // 添加用户消息
      addMessage({
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      });

      // 不再预先创建助手消息，而是在收到第一个 content_chunk 时创建
      // 这样工具调用会先显示，然后才是助手的回复

      setProcessing(true);
      setThinking(true);
      
      // 生成并记录 request_id 用于过滤事件
      const requestId = makeClientRequestId('chat');
      activeRequestIdRef.current = requestId;
      
      // 正常调用接口
      const currentMode = useSessionStore.getState().mode;
      const selectedModel = useSessionStore.getState().selectedModelName;
      try {
        await request('chat.send', {
          session_id: sessionId,
          content,
          interactive_ask: true,
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
        }, { requestId });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        setProcessing(false);
        setThinking(false);
        activeRequestIdRef.current = null;
        const errorMsg = webError.message || i18n.t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        addMessage({
          id: `error-${Date.now()}`,
          role: 'system',
          content: i18n.t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }
    },
    [addMessage, request, setProcessing, setThinking]
  );

  // 存储sendMessage函数到ref
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  // 统一中断接口 - pause/cancel/supplement/resume
  const interrupt = useCallback(
    async (
      sessionId: string,
      intent: InterruptIntent,
      options?: { newInput?: string }
    ) => {
      const newInput = options?.newInput;
      if (intent === 'supplement' && newInput) {
        userInputVersionRef.current += 1;
        stopAllTts();
        addMessage({
          id: `user-${Date.now()}`,
          role: 'user',
          content: newInput,
          timestamp: new Date().toISOString(),
        });
      }
      const interruptRequestId = makeClientRequestId('interrupt');
      pendingInterruptRequestIdsRef.current.add(interruptRequestId);
      try {
        const params: Record<string, unknown> = {
          session_id: sessionId,
          intent,
        };
        if (intent === 'supplement') {
          params.new_input = newInput ?? '';
        }
        await request('chat.interrupt', params, { requestId: interruptRequestId });
      } catch (error) {
        pendingInterruptRequestIdsRef.current.delete(interruptRequestId);
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.interruptFailed'));
      }
    },
    [addMessage, request, setConnectionStats]
  );

  // 暂停 - 显式暂停当前任务
  const pause = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'pause');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.pauseFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  const cancel = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'cancel');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.cancelFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  const supplement = useCallback(
    async (sessionId: string, newInput: string) => {
      try {
        await interrupt(sessionId, 'supplement', { newInput });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.supplementFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  // 恢复 - 恢复暂停的任务
  const resume = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'resume');
        setPaused(false);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.resumeFailed'));
      }
    },
    [interrupt, setConnectionStats, setPaused]
  );

  // 切换模式
  const switchMode = useCallback(
    async (sessionId: string, mode: AgentMode) => {
      if (sessionId && sessionId !== 'new') {
        try {
          await interrupt(sessionId, 'cancel');
        } catch {
          // 忽略中断错误，继续切换模式
        }
      }
      setProcessing(false);
      setThinking(false);
      setMode(mode);
      if (sessionId && sessionId !== 'new') {
        updateSession(sessionId, { mode });
      }
    },
    [setMode, updateSession, setProcessing, setThinking, interrupt]
  );

  // 发送用户回答
  const sendUserAnswer = useCallback(
    async (sessionId: string, requestId: string, answers: UserAnswer[], source?: string) => {
      try {
        // 如果是工具权限确认，发送 chat.send
        if (source === 'permission_interrupt') {
          const outboundId = makeClientRequestId('perm');
          activeRequestIdRef.current = outboundId;
          await request(
            'chat.send',
            {
              session_id: sessionId,
              query: '',
              request_id: requestId,
              answers: answers,
              ...(source ? { source } : {}),
            },
            { requestId: outboundId }
          );
        } else {
          // 否则发送 chat.user_answer（自进化确认）
          await request('chat.user_answer', {
            session_id: sessionId,
            request_id: requestId,
            answers,
            ...(source ? { source } : {}),
          });
        }
        setPendingQuestion(null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.submitAnswerFailed'));
      }
    },
    [request, setConnectionStats, setPendingQuestion]
  );

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    setContextWindowUsage(null);
  }, [activeSessionId, setContextWindowUsage]);

  useEffect(() => {
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onErrorRef.current = onError;
  }, [onConnect, onDisconnect, onError]);

  const shouldDropDuplicatedEvent = useCallback(
    (eventName: string, payload: Record<string, unknown>): boolean => {
      const now = Date.now();
      const dedupKey = makeEventDedupKey(eventName, payload);
      const recent = recentEventRef.current;
      const lastSeen = recent.get(dedupKey);
      recent.set(dedupKey, now);

      // 控制 map 大小，避免长期运行后无限增长
      if (recent.size > 400) {
        for (const [key, ts] of recent) {
          if (now - ts > EVENT_DEDUP_WINDOW_MS * 6) {
            recent.delete(key);
          }
        }
      }

      const dropped = lastSeen != null && now - lastSeen <= EVENT_DEDUP_WINDOW_MS;
      if (dropped && import.meta.env.DEV) {
        const nextCount = (eventDedupDroppedRef.current[eventName] || 0) + 1;
        eventDedupDroppedRef.current[eventName] = nextCount;
        if (nextCount === 1 || nextCount % 10 === 0) {
          console.debug('[ws][metrics] eventDedupDropped', {
            eventName,
            count: nextCount,
          });
        }
      }
      return dropped;
    },
    []
  );

  useEffect(() => {
    const unsubs = [
      webClient.on('connection.ack', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('hello', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('chat.delta', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        
        const currentMode = useSessionStore.getState().mode;
        const content = typeof event.payload.content === 'string' ? event.payload.content : '';
        
        // team 模式下，累积 chat.delta 内容
        if (currentMode === 'team' && content) {
          setThinking(false);
          
          const { messages } = useChatStore.getState();
          const existingMsg = messages.find(m => 
            m.id.startsWith('team-leader-') && 
            (m as { isStreaming?: boolean }).isStreaming === true
          );
          
          if (existingMsg) {
            const existingContent = existingMsg.content || '';
            const newContent = existingContent + content;
            const updatePayload: { content: string; isStreaming?: boolean } = { content: newContent };
            if (content.includes('MEDIA:')) {
              updatePayload.isStreaming = false;
            }
            updateMessage(existingMsg.id, updatePayload);
          } else {
            const msgId = `team-leader-${Date.now()}`;
            addMessage({
              id: msgId,
              role: 'system',
              content: content,
              timestamp: new Date().toISOString(),
              isStreaming: true,
            });
          }
          return;
        }
        
        const { currentStreamId } = useChatStore.getState();
        setThinking(false);
        if (!currentStreamId && content) {
          const assistantMsgId = `assistant-${Date.now()}`;
          addMessage({
            id: assistantMsgId,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            isStreaming: true,
          });
          startStreaming(assistantMsgId);
        }
        appendStreamContent(content);
      }),
      webClient.on('chat.final', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        
        const currentMode = useSessionStore.getState().mode;
        const content = normalizeFinalContent(event.payload);
        
        // team 模式下，将 chat.final 作为 team_leader 消息处理
        if (currentMode === 'team' && content) {
          setThinking(false);
          
          const { messages } = useChatStore.getState();
          const existingMsg = messages.find(m => 
            m.id.startsWith('team-leader-') && 
            (m as { isStreaming?: boolean }).isStreaming === true
          );
          
          if (existingMsg) {
            updateMessage(existingMsg.id, { content, isStreaming: false });
          } else {
            const timestamp = event.payload.timestamp || Date.now();
            addMessage({
              id: `team-leader-${Date.now()}`,
              role: 'system',
              content: `team.leader:${JSON.stringify({ content, timestamp })}`,
              timestamp: new Date().toISOString(),
            });
          }
          return;
        }
        
        const { currentStreamId, messages } = useChatStore.getState();
        const payloadSessionId =
          typeof event.payload.session_id === 'string' ? event.payload.session_id.trim() : '';
        // 仅当有明确会话绑定时才把 final 合并进当前流式气泡。
        // 定时任务等广播的 session_id 为空/null，若仍走 currentStreamId 会写到错误气泡甚至“无可见更新”。
        const streamId = currentStreamId;
        if (streamId && payloadSessionId) {
          updateMessage(streamId, { ...(content ? { content } : {}), isStreaming: false });
          stopStreaming();
          if (content && !content.includes('MEDIA:')) {
            handleTtsPlayback(streamId, content);
          }
          return;
        }
        if (content) {
          const cronMeta = event.payload.cron as Record<string, unknown> | undefined;
          const cronRunId =
            typeof cronMeta?.run_id === 'string' ? cronMeta.run_id.trim() : '';
          const isCronPlaceholderContent = /^\[cron\].*正在执行中/.test(content);

          // 正式结果：替换同 run_id 的占位气泡，或最近的 [cron]…正在执行中…
          if (!isCronPlaceholderContent) {
            let placeholderId: string | null = null;
            if (cronRunId) {
              const byRun = messages.find((m) => m.id === `cron-placeholder-${cronRunId}`);
              if (byRun) placeholderId = byRun.id;
            }
            if (!placeholderId) {
              for (let i = messages.length - 1; i >= 0; i -= 1) {
                const msg = messages[i];
                if (msg.role !== 'assistant' || typeof msg.content !== 'string') continue;
                if (/^\[cron\].*正在执行中/.test(msg.content)) {
                  placeholderId = msg.id;
                  break;
                }
              }
            }
            if (placeholderId) {
              updateMessage(placeholderId, { content, isStreaming: false });
              if (!content.includes('MEDIA:')) {
                handleTtsPlayback(placeholderId, content);
              }
              return;
            }
          }

          const messageId =
            isCronPlaceholderContent && cronRunId
              ? `cron-placeholder-${cronRunId}`
              : cronRunId && !isCronPlaceholderContent
                ? `cron-final-${cronRunId}`
                : `msg-${Date.now()}`;

          const existing = messages.find((m) => m.id === messageId);
          if (existing) {
            if (existing.content === content) {
              return;
            }
            updateMessage(messageId, { content, isStreaming: false });
            if (!content.includes('MEDIA:')) {
              handleTtsPlayback(messageId, content);
            }
            return;
          }

          // 去重：若上一条已是相同内容的助手消息（同一回复被收到两次），不再追加
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant' && last.content === content) {
            return;
          }
          addMessage({
            id: messageId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
          });
          if (!content.includes('MEDIA:')) {
            handleTtsPlayback(messageId, content);
          }
        }
      }),
      webClient.on('chat.media', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        const mediaPayload = event.payload as {
          content?: string;
          media_items?: MediaItem[];
        };
        const { currentStreamId, messages } = useChatStore.getState();
        const targetId =
          currentStreamId ??
          [...messages].reverse().find((msg) => msg.role === 'assistant')?.id;
        if (!targetId) {
          return;
        }
        const updates: { content?: string; mediaItems?: MediaItem[] } = {};
        if (mediaPayload.content !== undefined) {
          updates.content = mediaPayload.content;
        }
        if (mediaPayload.media_items?.length) {
          updates.mediaItems = mediaPayload.media_items;
        }
        if (Object.keys(updates).length > 0) {
          updateMessage(targetId, updates);
        }
        if (mediaPayload.content) {
          handleTtsPlayback(targetId, mediaPayload.content);
        }
      }),
      webClient.on('chat.tool_call', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('chat.tool_call', event.payload)) return;
        setThinking(false);
        const { currentStreamId, currentStreamContent } = useChatStore.getState();
        if (currentStreamId && currentStreamContent) {
          updateMessage(currentStreamId, { isStreaming: false });
          stopStreaming();
          handleTtsPlayback(currentStreamId, currentStreamContent);
        }
        const normalized = normalizeToolCallPayload(event.payload);
        addToolCall({
          id: normalized.id,
          name: normalized.name,
          arguments: normalized.arguments,
          description: normalized.description,
          formatted_args: normalized.formatted_args,
          memberId: normalized.memberId,
          memberName: normalized.memberName,
        });
      }),
      webClient.on('chat.tool_result', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('chat.tool_result', event.payload)) return;
        const standalone = tryDeepResearchStandaloneAssistantTurn(
          event.payload as Record<string, unknown>,
        );
        if (standalone) {
          const { messages } = useChatStore.getState();
          if (!messages.some((m) => m.id === standalone.messageId)) {
            addMessage({
              id: standalone.messageId,
              role: 'assistant',
              content: standalone.content,
              timestamp: new Date().toISOString(),
            });
          }
          return;
        }
        addToolResult(normalizeToolResultPayload(event.payload));
      }),
      // Team 成员子 agent：后端以 team.member.tool_* 广播，与 leader 的 chat.tool_* 区分
      webClient.on('team.member.tool_call', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('team.member.tool_call', event.payload)) return;
        setThinking(false);
        const normalized = normalizeToolCallPayload(event.payload);
        addToolCall({
          id: normalized.id,
          name: normalized.name,
          arguments: normalized.arguments,
          description: normalized.description,
          formatted_args: normalized.formatted_args,
          memberId: normalized.memberId,
          memberName: normalized.memberName,
        });
      }),
      webClient.on('team.member.tool_result', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('team.member.tool_result', event.payload)) return;
        addToolResult(normalizeToolResultPayload(event.payload));
      }),
      webClient.on('todo.updated', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('todo.updated', event.payload)) return;
        const todos = Array.isArray(event.payload.todos) ? event.payload.todos : [];
        setTodos(todos as Parameters<typeof setTodos>[0]);
      }),
      webClient.on('context.compressed', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        const rate =
          typeof event.payload.rate === 'number' ? event.payload.rate : 0;
        const beforeCompressed =
          typeof event.payload.before_compressed === 'number' && Number.isFinite(event.payload.before_compressed)
            ? event.payload.before_compressed
            : null;
        const afterCompressed =
          typeof event.payload.after_compressed === 'number' && Number.isFinite(event.payload.after_compressed)
            ? event.payload.after_compressed
            : null;
        setContextCompressionStats({ rate, beforeCompressed, afterCompressed });
        console.debug('[ws] context.compressed', {
          session_id: event.payload.session_id,
          rate,
          before_compressed: beforeCompressed,
          after_compressed: afterCompressed,
        });
      }),
      webClient.on('context.usage', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const usedFromPayload =
          typeof payload.used_tokens === 'number' && Number.isFinite(payload.used_tokens)
            ? Math.max(Math.round(payload.used_tokens), 0)
            : null;
        const inputTokens =
          typeof payload.input_tokens === 'number' && Number.isFinite(payload.input_tokens)
            ? payload.input_tokens
            : null;
        const outputTokens =
          typeof payload.output_tokens === 'number' && Number.isFinite(payload.output_tokens)
            ? payload.output_tokens
            : null;
        const totalTokens =
          typeof payload.total_tokens === 'number' && Number.isFinite(payload.total_tokens)
            ? payload.total_tokens
            : null;
        const limitTokens =
          typeof payload.limit_tokens === 'number' && Number.isFinite(payload.limit_tokens)
            ? Math.max(Math.round(payload.limit_tokens), 1)
            : 1;
        const fromParts = (inputTokens ?? 0) + (outputTokens ?? 0);
        const hasAnyTokenField =
          usedFromPayload != null ||
          inputTokens != null ||
          outputTokens != null ||
          totalTokens != null;
        const usedTokens = !hasAnyTokenField
          ? null
          : usedFromPayload != null
            ? usedFromPayload
            : Math.max(totalTokens ?? 0, fromParts);
        const percent =
          typeof payload.usage_percent === 'number' && Number.isFinite(payload.usage_percent)
            ? payload.usage_percent
            : usedTokens != null
              ? Number(((usedTokens / limitTokens) * 100).toFixed(1))
              : null;

        setContextWindowUsage({
          inputTokens,
          outputTokens,
          usedTokens,
          limitTokens,
          percent,
        });
      }),
      webClient.on('heartbeat.relay', (event: WsEvent) => {
        const heartbeatText =
          typeof event.payload.heartbeat === 'string' ? event.payload.heartbeat : '';
        // 只要成功收到 relay 即表示已成功发到前端，始终为 ok，不存在 alert
        setHeartbeatStatus(
          'ok',
          heartbeatText || null,
          new Date().toISOString()
        );
      }),
      webClient.on('session.updated', (event: WsEvent) => {
        const sessionId =
          typeof event.payload.session_id === 'string' ? event.payload.session_id : '';
        if (!sessionId) return;
        updateSession(sessionId, event.payload as Partial<Session>);
        if (sessionId === activeSessionIdRef.current && typeof event.payload.mode === 'string') {
          setMode(normalizeAgentMode(event.payload.mode));
        }
      }),
      webClient.on('chat.processing_status', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        const isProcessingNow = Boolean(event.payload.is_processing);
        // 结束态按 session 生效：权限确认会换 perm-* request_id，旧 chat-* 的 false 不能丢弃。
        if (isProcessingNow && !shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('chat.processing_status', event.payload)) return;
        const procRid = typeof event.request_id === 'string' ? event.request_id.trim() : '';
        if (
          isProcessingNow &&
          procRid &&
          !pendingInterruptRequestIdsRef.current.has(procRid)
        ) {
          activeRequestIdRef.current = procRid;
        }
        setProcessing(isProcessingNow);
        if (!isProcessingNow) {
          setThinking(false);
          clearSubtasks();
          activeRequestIdRef.current = null;
          
          // 检查是否有等待的任务队列
          const currentMode = useSessionStore.getState().mode;
          const { taskQueue } = useChatStore.getState();
          if (currentMode === 'agent.fast' && taskQueue.length > 0) {
            // 智能执行模式下，自动处理队列中的下一个任务
            const nextTask = taskQueue[0];
            if (nextTask && activeSessionIdRef.current && sendMessageRef.current) {
              // 从队列中移除该任务
              removeFromTaskQueue(nextTask.id);
              // 发送下一个任务
              sendMessageRef.current(nextTask.content, activeSessionIdRef.current);
            }
          }
        }
      }),
      webClient.on('chat.error', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('chat.error', event.payload)) return;
        setThinking(false);
        activeRequestIdRef.current = null;
        const errorMsg =
          typeof event.payload.error === 'string' ? event.payload.error : i18n.t('network.unknownError');
        // 忽略 "invalid page_idx or session history not found" 错误，因为这是新会话的正常情况
        if (errorMsg.includes('invalid page_idx or session history not found')) {
          return;
        }
        onErrorRef.current?.(errorMsg);
        addMessage({
          id: `error-${Date.now()}`,
          role: 'system',
          content: i18n.t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('security.alert', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;

        const alertMsg =
          typeof payload.message === 'string'
            ? payload.message
            : '安全警告';

        window.dispatchEvent(new CustomEvent('security-alert', {
          detail: {
            message: alertMsg,
            message_id: payload.message_id || '',
            tool_call_id: payload.tool_call_id || '',
            alert_type: payload.alert_type || 'security',
            tool_name: payload.tool_name || '',
          }
        }));
      }),
      webClient.on('chat.retract', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;

        const retractMsg =
          typeof event.payload.message === 'string'
            ? event.payload.message
            : '内容已因安全原因撤回';

        const { currentStreamId, messages } = useChatStore.getState();

        if (currentStreamId) {
          updateMessage(currentStreamId, {
            content: retractMsg,
            isStreaming: false,
          });
          stopStreaming();
        }

        let lastUserIdx = -1;
        for (let i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i].role === 'user') {
            lastUserIdx = i;
            break;
          }
        }
        if (lastUserIdx >= 0) {
          for (let i = lastUserIdx + 1; i < messages.length; i++) {
            if (messages[i].role === 'assistant') {
              updateMessage(messages[i].id, { content: retractMsg });
            }
          }
        } else {
          for (const msg of messages) {
            if (msg.role === 'assistant') {
              updateMessage(msg.id, { content: retractMsg });
            }
          }
        }

        setProcessing(false);
        setThinking(false);
        activeRequestIdRef.current = null;

        const retractRequestId = typeof event.payload.request_id === 'string' ? event.payload.request_id : undefined;
        useChatStore.getState().clearCurrentTurnData(retractRequestId);
      }),
      webClient.on('chat.interrupt_result', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        if (shouldDropDuplicatedEvent('chat.interrupt_result', event.payload)) return;
        const resultPayload = event.payload as unknown as InterruptResultPayload;
        setInterruptResult(resultPayload);
        if (resultPayload.intent === 'pause') {
          if (resultPayload.success) {
            setPaused(true, resultPayload.paused_task);
          }
          setProcessing(false);
          setThinking(false);
          activeRequestIdRef.current = null;
        } else if (resultPayload.intent === 'resume') {
          if (resultPayload.success) {
            setPaused(false);
          }
        } else if (resultPayload.intent === 'cancel') {
          setPaused(false);
          setProcessing(false);
          setThinking(false);
          activeRequestIdRef.current = null;
          if (resultPayload.success) {
            markPendingExecutionsCancelled();
            clearSubtasks();
            stopStreaming();
          }
        } else if (resultPayload.intent === 'supplement') {
          setPaused(false);
        }
        const irid = typeof event.request_id === 'string' ? event.request_id.trim() : '';
        if (irid) {
          window.setTimeout(() => {
            pendingInterruptRequestIdsRef.current.delete(irid);
          }, 0);
        }
      }),
      webClient.on('chat.subtask_update', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        updateSubtask(event.payload as unknown as SubtaskUpdatePayload);
      }),
      webClient.on('chat.ask_user_question', (event: WsEvent) => {
        if (!shouldHandleSessionEvent(event.payload)) return;
        if (!shouldHandleCurrentRequestEvent(event)) return;
        setPendingQuestion(event.payload as unknown as AskUserQuestionPayload);
      }),
      // 同时监听 session_result 事件，以处理后端可能发送的不同格式
      webClient.on('session_result', (event: WsEvent) => {
        setThinking(false);
        const sessionId =
          typeof event.payload.session_id === 'string' ? event.payload.session_id : '';
        const description =
          typeof event.payload.description === 'string' ? event.payload.description : '';
        const result = typeof event.payload.result === 'string' ? event.payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '会话完成',
          formatted_args: `会话任务：【${description || '未知任务'}】`,
        };
        addToolCall(sessionToolCall);
        // 组合 description 和 result 作为完整结果
        const fullResult = description
          ? `描述: ${description}\n\n结果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        addToolResult(sessionResult);
      }),
      webClient.on('chat.session_result', (event: WsEvent) => {
        if (shouldDropDuplicatedEvent('chat.session_result', event.payload)) {
          return;
        }
        setThinking(false);
        const sessionId =
          typeof event.payload.session_id === 'string' ? event.payload.session_id : '';
        const description =
          typeof event.payload.description === 'string' ? event.payload.description : '';
        const result = typeof event.payload.result === 'string' ? event.payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '会话完成',
          formatted_args: `会话任务：【${description || '未知任务'}】`,
        };
        addToolCall(sessionToolCall);
        // 组合 description 和 result 作为完整结果
        const fullResult = description
          ? `描述: ${description}\n\n结果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        addToolResult(sessionResult);
      }),
      webClient.on('team.event', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.event', payload)) {
          return;
        }
        setThinking(false);
        addMessage({
          id: `team-event-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.message', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.message', payload)) {
          return;
        }
        setThinking(false);
        addMessage({
          id: `team-message-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.task', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.task', payload)) {
          return;
        }
        setThinking(false);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as { type?: string; team_id?: string; task_id?: string; status?: string; timestamp?: number };
          useSessionStore.getState().addTeamTaskEvent({
            id: `task-${Date.now()}`,
            type: e.type || '',
            team_id: e.team_id || '',
            task_id: e.task_id || '',
            status: e.status || '',
            timestamp: e.timestamp || Date.now(),
          });
        }
      }),
      webClient.on('team.member', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.member', payload)) {
          return;
        }
        setThinking(false);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as { type?: string; member_id?: string; status?: string; new_status?: string; timestamp?: number };
          if (e.type === 'team.member.status_changed' && e.member_id && e.new_status) {
            useSessionStore.getState().updateTeamMemberStatus(
              e.member_id,
              e.new_status,
              e.timestamp
            );
          } else {
            useSessionStore.getState().addTeamMember({
              id: `member-${Date.now()}`,
              member_id: e.member_id || '',
              status: e.status || '',
              timestamp: e.timestamp || Date.now(),
            });
          }
        }
      }),
      webClient.on('chat.usage_summary', ({ payload }) => {
        console.log('[usage_summary] received:', payload);
        if (!shouldHandleSessionEvent(payload)) {
          console.log('[usage_summary] filtered by session check');
          return;
        }
        const usage = payload.usage as UsageSummary | undefined;
        if (!usage) {
          console.log('[usage_summary] no usage field in payload');
          return;
        }
        const { currentStreamId, messages } = useChatStore.getState();
        let targetId = currentStreamId;
        if (!targetId) {
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'assistant') {
              targetId = messages[i].id;
              break;
            }
          }
        }
        console.log('[usage_summary] targetId:', targetId, 'usage:', usage);
        if (targetId) {
          useChatStore.getState().setUsageSummary(targetId, usage);
        }
      }),
    ];

    return () => {
      unsubs.forEach((fn) => fn());
    };
  }, [
    addMessage,
    addToolCall,
    addToolResult,
    appendStreamContent,
    clearSubtasks,
    handleConnectionAck,
    handleTtsPlayback,
    markPendingExecutionsCancelled,
    setMode,
    setPaused,
    setPendingQuestion,
    setProcessing,
    setThinking,
    setInterruptResult,
    setTodos,
    setContextCompressionStats,
    setContextWindowUsage,
    setHeartbeatStatus,
    updateSession,
    shouldHandleSessionEvent,
    shouldHandleCurrentRequestEvent,
    shouldDropDuplicatedEvent,
    startStreaming,
    stopStreaming,
    updateMessage,
    updateSubtask,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectPath,
    };
    void webClient.connect(connectOptions).catch((error) => {
      const webError = error as WebError;
      setConnectionStats({ lastError: webError.message });
      onErrorRef.current?.(webError.message || 'WebSocket connection error');
    });

    return () => {
      webClient.disconnect();
      pendingInterruptRequestIdsRef.current.clear();
      activeRequestIdRef.current = null;
      clearMessages();
      clearTodos();
      clearSubtasks();
      setConnected(false);
      // 不再重置上下文压缩信息，保持本地存储的状态
      // setContextCompressionStats(null);
      setContextWindowUsage(null);
      setHeartbeatStatus('unknown', null, null);
      setConnectionStats({ state: 'closed', inflight: 0 });
    };
  }, [
    apiBase,
    apiKey,
    clearMessages,
    clearSubtasks,
    clearTodos,
    model,
    projectPath,
    provider,
    setContextCompressionStats,
    setContextWindowUsage,
    setConnectionStats,
    setConnected,
    setHeartbeatStatus,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectPath,
    };
    const reconnectByDebugToggle = () => {
      void webClient.disconnect('debug mode toggled').then(() => {
        void webClient.connect(connectOptions).catch((error) => {
          const webError = error as WebError;
          setConnectionStats({ lastError: webError.message });
          onErrorRef.current?.(webError.message || 'WebSocket reconnect error');
        });
      });
    };
    window.addEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    return () => {
      window.removeEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    };
  }, [apiBase, apiKey, model, projectPath, provider, setConnectionStats]);

  useEffect(() => {
    const unsub = webClient.onStateChange((state) => {
      setConnectionState(state);
      const connected = state === 'ready';
      setIsConnected(connected);
      setConnected(connected);
      setConnectionStats({
        state,
        inflight: webClient.getInflightCount(),
        lastError: null,
      });
      if (!connected && (state === 'reconnecting' || state === 'closed')) {
        onDisconnectRef.current?.();
      }
    });
    return () => {
      unsub();
    };
  }, [setConnected, setConnectionStats]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setConnectionStats({
        inflight: webClient.getInflightCount(),
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [setConnectionStats]);

  useEffect(() => {
    markTimedOutExecutions();
    const timer = window.setInterval(() => {
      markTimedOutExecutions();
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [markTimedOutExecutions]);

  return {
    isConnected,
    connectionState,
    request,
    sendMessage,
    interrupt,
    pause,
    cancel,
    supplement,
    resume,
    switchMode,
    disconnect,
    sendUserAnswer,
    getInflightCount: () => webClient.getInflightCount(),
  };
}
