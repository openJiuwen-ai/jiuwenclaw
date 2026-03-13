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
} from '../types';
import { useChatStore, useTodoStore, useSessionStore } from '../stores';
import { webClient } from '../services/webClient';
import i18n from '../i18n';
import {
  fetchTtsAudio,
  playAudioBase64,
  sanitizeTtsText,
  stopAllTts,
} from '../utils';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
} from '../features/tool-events/toolEventNormalizer';

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
    answers: UserAnswer[]
  ) => Promise<void>;
  getInflightCount: () => number;
}

function decodeQuotedPythonLikeString(raw: string): string {
  return raw
    .replace(/\\r/g, '\r')
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\');
}

function normalizeFinalDisplayText(text: string): string {
  return text.replace(/^(?:\r?\n)+/, '');
}

function normalizeFinalContent(payload: Record<string, unknown>): string {
  const rawContent = payload.content;
  if (typeof rawContent !== 'string') {
    return '';
  }

  const trimmed = rawContent.trim();

  // 优先按标准 JSON 解析（例如 {"output":"...","result_type":"answer"}）
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    try {
      const parsed = JSON.parse(trimmed) as Record<string, unknown>;
      if (typeof parsed.output === 'string') {
        return normalizeFinalDisplayText(parsed.output);
      }
    } catch {
      // ignore: 继续尝试 Python dict 风格兼容解析
    }
  }

  // 常规场景：后端直接返回纯文本
  if (!trimmed.includes('result_type') || !trimmed.includes('output')) {
    return normalizeFinalDisplayText(rawContent);
  }

  // 兼容后端返回的 Python dict 字符串（例如 "{'output': '...'}"）
  const singleQuoted = rawContent.match(/['"]output['"]\s*:\s*'((?:\\'|[^'])*)'/s);
  if (singleQuoted?.[1] != null) {
    return normalizeFinalDisplayText(decodeQuotedPythonLikeString(singleQuoted[1]));
  }

  const doubleQuoted = rawContent.match(/['"]output['"]\s*:\s*"((?:\\"|[^"])*)"/s);
  if (doubleQuoted?.[1] != null) {
    return normalizeFinalDisplayText(decodeQuotedPythonLikeString(doubleQuoted[1]));
  }

  return normalizeFinalDisplayText(rawContent);
}

function normalizeAgentMode(rawMode: unknown): AgentMode {
  if (typeof rawMode !== 'string') return 'plan';
  const normalized = rawMode.trim().toLowerCase();
  return normalized === 'agent' ? 'agent' : 'plan';
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

  const handleConnectionAck = useCallback(
    (payload: Record<string, unknown>) => {
      const ackPayload = payload as unknown as ConnectionAckPayload;
      setConnected(true);
      if (Array.isArray(ackPayload.tools)) {
        setAvailableTools(ackPayload.tools);
      }
      if (ackPayload.mode) {
        setMode(normalizeAgentMode(ackPayload.mode));
      }
      onConnectRef.current?.(ackPayload);
    },
    [setAvailableTools, setConnected, setMode]
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
      try {
        const currentMode = useSessionStore.getState().mode;
        await request('chat.send', {
          session_id: sessionId,
          content,
          mode: currentMode,
        });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        setProcessing(false);
        setThinking(false);
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
      try {
        const params: Record<string, unknown> = {
          session_id: sessionId,
          intent,
        };
        if (intent === 'supplement') {
          params.new_input = newInput ?? '';
        }
        await request('chat.interrupt', params);
      } catch (error) {
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
      setMode(mode);
      if (sessionId && sessionId !== 'new') {
        updateSession(sessionId, { mode });
      }
    },
    [setMode, updateSession]
  );

  // 发送用户回答
  const sendUserAnswer = useCallback(
    async (sessionId: string, requestId: string, answers: UserAnswer[]) => {
      try {
        await request('chat.user_answer', {
          session_id: sessionId,
          request_id: requestId,
          answers,
        });
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
    setContextCompressionStats(null);
  }, [activeSessionId, setContextCompressionStats]);

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
      webClient.on('chat.delta', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const content = typeof payload.content === 'string' ? payload.content : '';
        const { currentStreamId } = useChatStore.getState();
        setThinking(false);
        if (!currentStreamId && content) {
          const assistantMsgId = `assistant-${Date.now()}`;
          addMessage({
            id: assistantMsgId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
            isStreaming: true,
          });
          startStreaming(assistantMsgId);
          return;
        }
        appendStreamContent(content);
      }),
      webClient.on('chat.final', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const content = normalizeFinalContent(payload);
        const { currentStreamId, messages } = useChatStore.getState();
        if (currentStreamId) {
          updateMessage(currentStreamId, { content, isStreaming: false });
          stopStreaming();
          if (content && !content.includes('MEDIA:')) {
            handleTtsPlayback(currentStreamId, content);
          }
          return;
        }
        if (content) {
          // 去重：若上一条已是相同内容的助手消息（同一回复被收到两次），不再追加
          const last = messages[messages.length - 1];
          if (
            last?.role === 'assistant' &&
            last.content === content
          ) {
            return;
          }
          const messageId = `msg-${Date.now()}`;
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
      webClient.on('chat.media', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const mediaPayload = payload as {
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
      webClient.on('chat.tool_call', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.tool_call', payload)) return;
        setThinking(false);
        const { currentStreamId, currentStreamContent } = useChatStore.getState();
        if (currentStreamId && currentStreamContent) {
          updateMessage(currentStreamId, { isStreaming: false });
          stopStreaming();
          handleTtsPlayback(currentStreamId, currentStreamContent);
        }
        addToolCall(normalizeToolCallPayload(payload));
      }),
      webClient.on('chat.tool_result', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.tool_result', payload)) return;
        addToolResult(normalizeToolResultPayload(payload));
      }),
      webClient.on('todo.updated', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('todo.updated', payload)) return;
        const todos = Array.isArray(payload.todos) ? payload.todos : [];
        setTodos(todos as Parameters<typeof setTodos>[0]);
      }),
      webClient.on('context.compressed', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const rate =
          typeof payload.rate === 'number' ? payload.rate : 0;
        const beforeCompressed =
          typeof payload.before_compressed === 'number' && Number.isFinite(payload.before_compressed)
            ? payload.before_compressed
            : null;
        const afterCompressed =
          typeof payload.after_compressed === 'number' && Number.isFinite(payload.after_compressed)
            ? payload.after_compressed
            : null;
        setContextCompressionStats({ rate, beforeCompressed, afterCompressed });
        console.debug('[ws] context.compressed', {
          session_id: payload.session_id,
          rate,
          before_compressed: beforeCompressed,
          after_compressed: afterCompressed,
        });
      }),
      webClient.on('heartbeat.relay', ({ payload }) => {
        const heartbeatText =
          typeof payload.heartbeat === 'string' ? payload.heartbeat : '';
        // 只要成功收到 relay 即表示已成功发到前端，始终为 ok，不存在 alert
        setHeartbeatStatus(
          'ok',
          heartbeatText || null,
          new Date().toISOString()
        );
      }),
      webClient.on('session.updated', ({ payload }) => {
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        if (!sessionId) return;
        updateSession(sessionId, payload as Partial<Session>);
        if (sessionId === activeSessionIdRef.current && typeof payload.mode === 'string') {
          setMode(normalizeAgentMode(payload.mode));
        }
      }),
      webClient.on('chat.processing_status', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.processing_status', payload)) return;
        const isProcessingNow = Boolean(payload.is_processing);
        setProcessing(isProcessingNow);
        if (!isProcessingNow) {
          setThinking(false);
          clearSubtasks();
          
          // 检查是否有等待的任务队列
          const currentMode = useSessionStore.getState().mode;
          const { taskQueue } = useChatStore.getState();
          if (currentMode === 'agent' && taskQueue.length > 0) {
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
      webClient.on('chat.error', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.error', payload)) return;
        setThinking(false);
        const errorMsg =
          typeof payload.error === 'string' ? payload.error : i18n.t('network.unknownError');
        onErrorRef.current?.(errorMsg);
        addMessage({
          id: `error-${Date.now()}`,
          role: 'system',
          content: i18n.t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('chat.interrupt_result', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.interrupt_result', payload)) return;
        const resultPayload = payload as unknown as InterruptResultPayload;
        setInterruptResult(resultPayload);
        if (resultPayload.intent === 'pause') {
          if (resultPayload.success) {
            setPaused(true, resultPayload.paused_task);
          }
          setProcessing(false);
          setThinking(false);
        } else if (resultPayload.intent === 'resume') {
          if (resultPayload.success) {
            setPaused(false);
          }
        } else if (resultPayload.intent === 'cancel') {
          setPaused(false);
          setProcessing(false);
          setThinking(false);
        } else if (resultPayload.intent === 'supplement') {
          setPaused(false);
        }
      }),
      webClient.on('chat.subtask_update', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        updateSubtask(payload as unknown as SubtaskUpdatePayload);
      }),
      webClient.on('chat.ask_user_question', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        setPendingQuestion(payload as unknown as AskUserQuestionPayload);
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
    setMode,
    setPaused,
    setPendingQuestion,
    setProcessing,
    setThinking,
    setInterruptResult,
    setTodos,
    setContextCompressionStats,
    setHeartbeatStatus,
    updateSession,
    shouldHandleSessionEvent,
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
      clearMessages();
      clearTodos();
      clearSubtasks();
      setConnected(false);
      setContextCompressionStats(null);
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
