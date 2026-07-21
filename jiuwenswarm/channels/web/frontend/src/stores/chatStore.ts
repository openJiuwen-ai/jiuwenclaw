/**
 * 聊天状态管理（多 session 版本）
 *
 * 所有对话运行态按 session 隔离存储在 runtimes 中。
 * 组件通过 activeSessionId 读取当前会话的运行态。
 */

import { create } from 'zustand';
import {
  Message,
  ToolCall,
  ToolResult,
  ToolExecution,
  ToolExecutionStatus,
  InterruptResultPayload,
  SubtaskUpdatePayload,
  AskUserQuestionPayload,
  EvolutionStatusPayload,
  UsageSummary,
  FileDownloadItem,
  ContextCompressionRuntime,
  ContextCompressionSummary,
  TodoItem,
} from '../types';
import { useTodoStore } from './todoStore';

const TOOL_TIMEOUT_MS = 12_000_000;
const EVOLUTION_STATUS_END_VISIBLE_MS = 3_000;

function computeTimeoutAt(baseIso: string): string {
  return new Date(Date.parse(baseIso) + TOOL_TIMEOUT_MS).toISOString();
}

function resolveExecutionStatus(result: ToolResult): ToolExecutionStatus {
  return result.success ? 'completed' : 'error';
}

/**
 * 子任务状态
 */
export interface SubtaskState {
  task_id: string;
  description: string;
  status: string;
  index: number;
  total: number;
  tool_name?: string;
  tool_count: number;
  message?: string;
  is_parallel: boolean;
}

interface TaskItem {
  id: string;
  content: string;
  timestamp: number;
}

export interface HistoryPagerMeta {
  loadedPages: number;
  totalPages: number;
}

/**
 * 单个 session 的对话运行态。
 * 原全局字段全部迁移到这里，按 session 隔离。
 */
export interface ChatRuntime {
  messages: Message[];
  isProcessing: boolean;
  executionError: string | null;
  isThinking: boolean;
  isLoadingHistory: boolean;
  historyPagerMeta: HistoryPagerMeta | null;
  evolutionStatus: EvolutionStatusPayload | null;
  isPaused: boolean;
  pausedTask: string | null;
  interruptResult: InterruptResultPayload | null;
  switchingMode: boolean;
  isNewSession: boolean;
  currentStreamContent: string;
  currentStreamId: string | null;
  messageRenderKeySeq: number;
  /** 最近一次 chat.error 的错误信息，用于会话列表展示异常标记 */
  error: string | null;
  streamBuffers: Map<string, string>;
  activeSubtasks: Map<string, SubtaskState>;
  toolExecutions: Map<string, ToolExecution>;
  toolExecutionOrder: string[];
  orphanResults: Map<string, ToolResult>;
  contextCompressionRuntime?: ContextCompressionRuntime;
  contextCompressionSummary?: ContextCompressionSummary;
  toolMetrics: {
    toolCallDedupDropped: number;
    toolResultDedupDropped: number;
  };
  taskQueue: TaskItem[];
  queuePaused: boolean;
  pendingQuestion: AskUserQuestionPayload | null;
  inputValue: string;
  /** evolutionStatus 自动清除定时器，按 session 隔离 */
  evolutionStatusClearTimer: ReturnType<typeof setTimeout> | null;
  /** interruptResult 自动清除定时器，按 session 隔离 */
  interruptResultClearTimer: ReturnType<typeof setTimeout> | null;
}

function createEmptyRuntime(): ChatRuntime {
  return {
    messages: [],
    isProcessing: false,
    executionError: null,
    isThinking: false,
    isLoadingHistory: false,
    historyPagerMeta: null,
    evolutionStatus: null,
    isPaused: false,
    pausedTask: null,
    interruptResult: null,
    switchingMode: false,
    isNewSession: false,
    currentStreamContent: '',
    currentStreamId: null,
    messageRenderKeySeq: 0,
    error: null,
    streamBuffers: new Map(),
    activeSubtasks: new Map(),
    toolExecutions: new Map(),
    toolExecutionOrder: [],
    orphanResults: new Map(),
    contextCompressionRuntime: undefined,
    contextCompressionSummary: undefined,
    toolMetrics: {
      toolCallDedupDropped: 0,
      toolResultDedupDropped: 0,
    },
    taskQueue: [],
    queuePaused: false,
    pendingQuestion: null,
    inputValue: '',
    evolutionStatusClearTimer: null,
    interruptResultClearTimer: null,
  };
}

function assignMessageRenderKeys(
  runtime: ChatRuntime,
  messages: Message[]
): { messages: Message[]; messageRenderKeySeq: number } {
  let messageRenderKeySeq = runtime.messageRenderKeySeq;
  return {
    messages: messages.map((message) => {
      if (message.renderKey) {
        return message;
      }
      messageRenderKeySeq += 1;
      return {
        ...message,
        renderKey: `message-${messageRenderKeySeq}`,
      };
    }),
    messageRenderKeySeq,
  };
}

interface ChatState {
  runtimes: Record<string, ChatRuntime>;
  activeSessionId: string | null;
  /** Gateway broadcasts this status without a session id, so it is intentionally app-wide. */
  globalTaskRunning: boolean;

  ensureRuntime: (sessionId: string) => ChatRuntime;
  getRuntime: (sessionId: string | null) => ChatRuntime | undefined;
  setActiveSessionId: (sessionId: string | null) => void;
  setGlobalTaskRunning: (running: boolean) => void;
  removeRuntime: (sessionId: string) => void;

  addMessage: (sessionId: string, message: Message) => void;
  replaceHistoryMessages: (sessionId: string, messages: Message[]) => void;
  updateMessage: (sessionId: string, id: string, updates: Partial<Message>) => void;
  appendStreamContent: (sessionId: string, content: string, streamKey?: string) => void;
  startStreaming: (sessionId: string, messageId: string, streamKey?: string) => void;
  stopStreaming: (sessionId: string, streamKey?: string) => void;
  setExecutionError: (sessionId: string, error: string | null) => void;
  setProcessing: (sessionId: string, status: boolean) => void;
  setThinking: (sessionId: string, status: boolean) => void;
  setLoadingHistory: (sessionId: string, status: boolean) => void;
  setHistoryPagerMeta: (sessionId: string, meta: HistoryPagerMeta | null) => void;
  setEvolutionStatus: (sessionId: string, status: EvolutionStatusPayload | null) => void;
  setPaused: (sessionId: string, paused: boolean, task?: string | null) => void;
  setQueuePaused: (sessionId: string, paused: boolean) => void;
  setInterruptResult: (sessionId: string, result: InterruptResultPayload | null) => void;
  setSwitchingMode: (sessionId: string, switching: boolean) => void;
  setNewSession: (sessionId: string, isNew: boolean) => void;
  addToolCall: (sessionId: string, toolCall: ToolCall, options?: { startedAt?: string; requestId?: string }) => void;
  addToolResult: (sessionId: string, toolResult: ToolResult, options?: { updatedAt?: string }) => void;
  markTimedOutExecutions: (sessionId: string) => void;
  updateSubtask: (sessionId: string, payload: SubtaskUpdatePayload) => void;
  clearSubtasks: (sessionId: string) => void;
  clearMessages: (sessionId: string) => void;
  clearCurrentTurnData: (sessionId: string, requestId?: string) => void;
  prependMessages: (sessionId: string, olderFirst: Message[]) => void;
  addToTaskQueue: (sessionId: string, content: string) => void;
  clearTaskQueue: (sessionId: string) => void;
  removeFromTaskQueue: (sessionId: string, id: string) => void;
  reorderTaskQueue: (sessionId: string, fromIndex: number, toIndex: number) => void;
  setPendingQuestion: (sessionId: string, question: AskUserQuestionPayload | null) => void;
  setInputValue: (sessionId: string, value: string) => void;
  setSessionError: (sessionId: string, error: string | null) => void;
  setUsageSummary: (sessionId: string, messageId: string, usage: UsageSummary) => void;
  addFileItems: (sessionId: string, files: FileDownloadItem[]) => void;
  setContextCompressionStatus: (
    sessionId: string,
    runtime?: ContextCompressionRuntime,
    summary?: ContextCompressionSummary
  ) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  runtimes: {},
  activeSessionId: null,
  globalTaskRunning: false,

  ensureRuntime: (sessionId) => {
    const existing = get().runtimes[sessionId];
    if (existing) return existing;
    const runtime = createEmptyRuntime();
    set((state) => ({
      runtimes: { ...state.runtimes, [sessionId]: runtime },
    }));
    return runtime;
  },

  getRuntime: (sessionId) => {
    if (!sessionId) return undefined;
    return get().runtimes[sessionId];
  },

  setActiveSessionId: (sessionId) => {
    set({ activeSessionId: sessionId });
  },

  setGlobalTaskRunning: (running) => {
    set({ globalTaskRunning: running });
  },

  removeRuntime: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (runtime) {
        if (runtime.evolutionStatusClearTimer) clearTimeout(runtime.evolutionStatusClearTimer);
        if (runtime.interruptResultClearTimer) clearTimeout(runtime.interruptResultClearTimer);
      }
      const next = { ...state.runtimes };
      delete next[sessionId];
      return {
        runtimes: next,
        activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
      };
    });
  },

  addMessage: (sessionId, message) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const { messages, messageRenderKeySeq } = assignMessageRenderKeys(runtime, [message]);
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: [...runtime.messages, ...messages],
            messageRenderKeySeq,
          },
        },
      };
    });
  },

  replaceHistoryMessages: (sessionId, messages) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.evolutionStatusClearTimer) {
        clearTimeout(runtime.evolutionStatusClearTimer);
      }
      const assigned = assignMessageRenderKeys(runtime, messages);
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: assigned.messages,
            messageRenderKeySeq: assigned.messageRenderKeySeq,
            currentStreamContent: '',
            currentStreamId: null,
            streamBuffers: new Map(),
            evolutionStatus: null,
            evolutionStatusClearTimer: null,
            isPaused: false,
            pausedTask: null,
            interruptResult: null,
            switchingMode: false,
            activeSubtasks: new Map(),
            toolExecutions: new Map(),
            toolExecutionOrder: [],
            orphanResults: new Map(),
            contextCompressionRuntime: undefined,
            contextCompressionSummary: undefined,
            toolMetrics: {
              toolCallDedupDropped: 0,
              toolResultDedupDropped: 0,
            },
            taskQueue: [],
            pendingQuestion: null,
          },
        },
      };
    });
  },

  updateMessage: (sessionId, id, updates) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: runtime.messages.map((msg) =>
              msg.id === id ? { ...msg, ...updates } : msg
            ),
          },
        },
      };
    });
  },

  appendStreamContent: (sessionId, content, streamKey = 'default') => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime || !runtime.currentStreamId) return state;

      const existingBuffer = runtime.streamBuffers.get(streamKey) || '';
      const nextContent = existingBuffer + content;

      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            currentStreamContent: nextContent,
            streamBuffers: new Map(runtime.streamBuffers).set(streamKey, nextContent),
            messages: runtime.messages.map((msg) =>
              msg.id === runtime.currentStreamId
                ? { ...msg, content: nextContent }
                : msg
            ),
          },
        },
      };
    });
  },

  startStreaming: (sessionId, messageId, streamKey = 'default') => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            currentStreamId: messageId,
            currentStreamContent: '',
            streamBuffers: new Map(runtime.streamBuffers).set(streamKey, ''),
          },
        },
      };
    });
  },

  stopStreaming: (sessionId, streamKey = 'default') => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime || !runtime.currentStreamId) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: runtime.messages.map((msg) =>
              msg.id === runtime.currentStreamId ? { ...msg, isStreaming: false } : msg
            ),
            currentStreamId: null,
            currentStreamContent: '',
            streamBuffers: new Map(runtime.streamBuffers).set(streamKey, ''),
          },
        },
      };
    });
  },

  setExecutionError: (sessionId, error) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, executionError: error },
        },
      };
    });
  },

  setProcessing: (sessionId, status) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            isProcessing: status,
            executionError: status ? null : runtime.executionError,
            ...(status ? { error: null } : {}),
          },
        },
      };
    });
  },

  setSessionError: (sessionId, error) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, error },
        },
      };
    });
  },

  setThinking: (sessionId, status) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, isThinking: status },
        },
      };
    });
  },

  setLoadingHistory: (sessionId, status) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, isLoadingHistory: status },
        },
      };
    });
  },

  setHistoryPagerMeta: (sessionId, meta) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, historyPagerMeta: meta },
        },
      };
    });
  },

  setEvolutionStatus: (sessionId, status) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.evolutionStatusClearTimer) {
        clearTimeout(runtime.evolutionStatusClearTimer);
      }
      const nextRuntime: ChatRuntime = { ...runtime, evolutionStatus: status };
      if (status?.status === 'end') {
        nextRuntime.evolutionStatusClearTimer = setTimeout(() => {
          set((s) => {
            const r = s.runtimes[sessionId];
            if (!r || r.evolutionStatus !== status) return s;
            return {
              runtimes: {
                ...s.runtimes,
                [sessionId]: { ...r, evolutionStatus: null, evolutionStatusClearTimer: null },
              },
            };
          });
        }, EVOLUTION_STATUS_END_VISIBLE_MS);
      } else {
        nextRuntime.evolutionStatusClearTimer = null;
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: nextRuntime,
        },
      };
    });
  },

  setPaused: (sessionId, paused, task = null) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, isPaused: paused, pausedTask: task ?? null },
        },
      };
    });
  },

  setQueuePaused: (sessionId, paused) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, queuePaused: paused },
        },
      };
    });
  },

  setInterruptResult: (sessionId, result) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.interruptResultClearTimer) {
        clearTimeout(runtime.interruptResultClearTimer);
      }
      const nextRuntime: ChatRuntime = { ...runtime, interruptResult: result };
      if (result) {
        nextRuntime.interruptResultClearTimer = setTimeout(() => {
          set((s) => {
            const r = s.runtimes[sessionId];
            if (!r || r.interruptResult !== result) return s;
            return {
              runtimes: {
                ...s.runtimes,
                [sessionId]: { ...r, interruptResult: null, interruptResultClearTimer: null },
              },
            };
          });
        }, 3000);
      } else {
        nextRuntime.interruptResultClearTimer = null;
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: nextRuntime,
        },
      };
    });
  },

  setSwitchingMode: (sessionId, switching) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (switching) {
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              switchingMode: true,
              isProcessing: false,
              isPaused: false,
              pausedTask: null,
              interruptResult: null,
            },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, switchingMode: false },
        },
      };
    });
  },

  setNewSession: (sessionId, isNew) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, isNewSession: isNew },
        },
      };
    });
  },

  addToolCall: (sessionId, toolCall, options) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (!toolCall.id) {
        const nextDropped = runtime.toolMetrics.toolCallDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolCallDedupDropped', {
            count: nextDropped,
            reason: 'missing toolCallId',
          });
        }
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              toolMetrics: {
                ...runtime.toolMetrics,
                toolCallDedupDropped: nextDropped,
              },
            },
          },
        };
      }
      if (runtime.toolExecutions.has(toolCall.id)) {
        const nextDropped = runtime.toolMetrics.toolCallDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolCallDedupDropped', {
            count: nextDropped,
            reason: 'toolCallId execution hit',
          });
        }
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              toolMetrics: {
                ...runtime.toolMetrics,
                toolCallDedupDropped: nextDropped,
              },
            },
          },
        };
      }
      const nowIso = new Date().toISOString();
      const startedAt =
        typeof options?.startedAt === 'string' && options.startedAt.trim()
          ? options.startedAt.trim()
          : nowIso;
      const orphanResult = runtime.orphanResults.get(toolCall.id);
      const nextExecutions = new Map(runtime.toolExecutions);
      const nextOrphanResults = new Map(runtime.orphanResults);
      if (orphanResult) {
        nextOrphanResults.delete(toolCall.id);
      }
      const timeoutAt = computeTimeoutAt(startedAt);
      const resultStatus = orphanResult ? resolveExecutionStatus(orphanResult) : 'pending';
      nextExecutions.set(toolCall.id, {
        toolCallId: toolCall.id,
        toolCall,
        result: orphanResult,
        status: resultStatus,
        startedAt,
        updatedAt: startedAt,
        timeoutAt,
        requestId: options?.requestId,
      });

      const nextOrder = [...runtime.toolExecutionOrder, toolCall.id];
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            toolExecutions: nextExecutions,
            toolExecutionOrder: nextOrder,
            orphanResults: nextOrphanResults,
          },
        },
      };
    });
  },

  addToolResult: (sessionId, toolResult, options) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const incomingToolCallId = toolResult.toolCallId;
      if (!incomingToolCallId) {
        const nextDropped = runtime.toolMetrics.toolResultDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolResultDedupDropped', {
            count: nextDropped,
            reason: 'missing toolCallId',
          });
        }
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              toolMetrics: {
                ...runtime.toolMetrics,
                toolResultDedupDropped: nextDropped,
              },
            },
          },
        };
      }
      const nowIso = new Date().toISOString();
      const updatedAt =
        typeof options?.updatedAt === 'string' && options.updatedAt.trim()
          ? options.updatedAt.trim()
          : nowIso;
      const existingExecution = runtime.toolExecutions.get(incomingToolCallId);

      if (!existingExecution) {
        const nextOrphanResults = new Map(runtime.orphanResults);
        const duplicatedOrphan = nextOrphanResults.get(incomingToolCallId);
        if (
          duplicatedOrphan &&
          duplicatedOrphan.result === toolResult.result &&
          duplicatedOrphan.success === toolResult.success &&
          (duplicatedOrphan.summary || '') === (toolResult.summary || '')
        ) {
          const nextDropped = runtime.toolMetrics.toolResultDedupDropped + 1;
          if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
            console.debug('[ws][metrics] toolResultDedupDropped', {
              count: nextDropped,
              reason: 'orphan duplicate',
            });
          }
          return {
            runtimes: {
              ...state.runtimes,
              [sessionId]: {
                ...runtime,
                toolMetrics: {
                  ...runtime.toolMetrics,
                  toolResultDedupDropped: nextDropped,
                },
              },
            },
          };
        }
        nextOrphanResults.set(incomingToolCallId, toolResult);
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, orphanResults: nextOrphanResults },
          },
        };
      }

      if (existingExecution.result) {
        const duplicated =
          existingExecution.result.result === toolResult.result &&
          existingExecution.result.success === toolResult.success &&
          (existingExecution.result.summary || '') === (toolResult.summary || '');
        if (duplicated) {
          const nextDropped = runtime.toolMetrics.toolResultDedupDropped + 1;
          if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
            console.debug('[ws][metrics] toolResultDedupDropped', {
              count: nextDropped,
              reason: 'execution duplicate',
            });
          }
          return {
            runtimes: {
              ...state.runtimes,
              [sessionId]: {
                ...runtime,
                toolMetrics: {
                  ...runtime.toolMetrics,
                  toolResultDedupDropped: nextDropped,
                },
              },
            },
          };
        }
      }

      const nextExecutions = new Map(runtime.toolExecutions);
      const nextStatus = resolveExecutionStatus(toolResult);
      nextExecutions.set(incomingToolCallId, {
        ...existingExecution,
        result: toolResult,
        status: nextStatus,
        updatedAt,
        resultArrivedAfterTimeout:
          existingExecution.status === 'timeout' ? true : existingExecution.resultArrivedAfterTimeout,
      });
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, toolExecutions: nextExecutions },
        },
      };
    });
  },

  markTimedOutExecutions: (sessionId) => {
    const now = Date.now();
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      let changed = false;
      const nextExecutions = new Map(runtime.toolExecutions);
      for (const [toolCallId, execution] of nextExecutions) {
        if (execution.status !== 'pending') {
          continue;
        }
        const timeoutTs = Date.parse(execution.timeoutAt);
        if (Number.isNaN(timeoutTs) || timeoutTs > now) {
          continue;
        }
        changed = true;
        nextExecutions.set(toolCallId, {
          ...execution,
          status: 'timeout',
          timedOutAt: new Date(now).toISOString(),
          updatedAt: new Date(now).toISOString(),
        });
      }
      if (!changed) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, toolExecutions: nextExecutions },
        },
      };
    });
  },

  updateSubtask: (sessionId, payload) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const newSubtasks = new Map(runtime.activeSubtasks);

      if (payload.status === 'completed' || payload.status === 'error') {
        newSubtasks.delete(payload.task_id);
      } else {
        newSubtasks.set(payload.task_id, {
          task_id: payload.task_id,
          description: payload.description,
          status: payload.status,
          index: payload.index,
          total: payload.total,
          tool_name: payload.tool_name,
          tool_count: payload.tool_count || 0,
          message: payload.message,
          is_parallel: payload.is_parallel || false,
        });
      }

      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, activeSubtasks: newSubtasks },
        },
      };
    });

    const todoState = useTodoStore.getState();
    const todoRuntime = todoState.getRuntime(sessionId);
    const todos = todoRuntime?.todos ?? [];
    const setTodos = todoState.setTodos;

    const matchingTodo = todos.find(
      (todo: TodoItem) =>
        todo.status === 'in_progress' &&
        (todo.content.includes(payload.description) ||
         payload.description.includes(todo.content.slice(0, 20)))
    );

    if (matchingTodo) {
      let activeForm = '';
      if (payload.status === 'starting') {
        activeForm = `正在${payload.description}...`;
      } else if (payload.status === 'tool_call') {
        activeForm = `正在调用 ${payload.tool_name}...`;
      } else if (payload.status === 'completed') {
        activeForm = '';
      }

      if (activeForm || payload.status === 'completed') {
        const updatedTodos = todos.map((todo: TodoItem) =>
          todo.id === matchingTodo.id
            ? { ...todo, activeForm }
            : todo
        );
        setTodos(sessionId, updatedTodos);
      }
    }
  },

  clearSubtasks: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, activeSubtasks: new Map() },
        },
      };
    });
  },

  clearCurrentTurnData: (sessionId, requestId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (requestId) {
        const nextExecutions = new Map(runtime.toolExecutions);
        const nextOrder: string[] = [];
        for (const id of runtime.toolExecutionOrder) {
          const exec = nextExecutions.get(id);
          if (exec && exec.requestId === requestId) {
            nextExecutions.delete(id);
          } else {
            nextOrder.push(id);
          }
        }
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              toolExecutions: nextExecutions,
              toolExecutionOrder: nextOrder,
              orphanResults: new Map(),
              activeSubtasks: new Map(),
              interruptResult: null,
              pendingQuestion: null,
              toolMetrics: {
                toolCallDedupDropped: 0,
                toolResultDedupDropped: 0,
              },
            },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            toolExecutions: new Map(),
            toolExecutionOrder: [],
            orphanResults: new Map(),
            activeSubtasks: new Map(),
            interruptResult: null,
            pendingQuestion: null,
            toolMetrics: {
              toolCallDedupDropped: 0,
              toolResultDedupDropped: 0,
            },
          },
        },
      };
    });
    useTodoStore.getState().clearTodos(sessionId);
  },

  prependMessages: (sessionId, olderFirst) => {
    if (!olderFirst.length) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const assigned = assignMessageRenderKeys(runtime, olderFirst);
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: [...assigned.messages, ...runtime.messages],
            messageRenderKeySeq: assigned.messageRenderKeySeq,
          },
        },
      };
    });
  },

  clearMessages: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.evolutionStatusClearTimer) {
        clearTimeout(runtime.evolutionStatusClearTimer);
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: [],
            currentStreamContent: '',
            currentStreamId: null,
            streamBuffers: new Map(),
            evolutionStatus: null,
            evolutionStatusClearTimer: null,
            isPaused: false,
            pausedTask: null,
            interruptResult: null,
            switchingMode: false,
            activeSubtasks: new Map(),
            toolExecutions: new Map(),
            toolExecutionOrder: [],
            orphanResults: new Map(),
            contextCompressionRuntime: undefined,
            contextCompressionSummary: undefined,
            toolMetrics: {
              toolCallDedupDropped: 0,
              toolResultDedupDropped: 0,
            },
            taskQueue: [],
            pendingQuestion: null,
          },
        },
      };
    });
  },

  addToTaskQueue: (sessionId, content) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            taskQueue: [
              ...runtime.taskQueue,
              {
                id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
                content,
                timestamp: Date.now(),
              },
            ],
          },
        },
      };
    });
  },

  clearTaskQueue: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, taskQueue: [], queuePaused: false },
        },
      };
    });
  },

  removeFromTaskQueue: (sessionId, id) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            taskQueue: runtime.taskQueue.filter((task) => task.id !== id),
          },
        },
      };
    });
  },

  reorderTaskQueue: (sessionId, fromIndex, toIndex) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const queue = [...runtime.taskQueue];
      if (fromIndex < 0 || fromIndex >= queue.length || toIndex < 0 || toIndex >= queue.length || fromIndex === toIndex) {
        return state;
      }
      const [moved] = queue.splice(fromIndex, 1);
      queue.splice(toIndex, 0, moved);
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, taskQueue: queue },
        },
      };
    });
  },

  setPendingQuestion: (sessionId, question) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, pendingQuestion: question },
        },
      };
    });
  },

  setInputValue: (sessionId, value) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, inputValue: value },
        },
      };
    });
  },

  setUsageSummary: (sessionId, messageId, usage) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: runtime.messages.map((msg) =>
              msg.id === messageId ? { ...msg, usageSummary: usage } : msg
            ),
          },
        },
      };
    });
  },

  addFileItems: (sessionId, files) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const lastMessage = runtime.messages[runtime.messages.length - 1];
      const targetId =
        runtime.currentStreamId ??
        (lastMessage?.role === 'assistant' ? lastMessage.id : null);
      if (!targetId) {
        const msgId = `file-${Date.now()}`;
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: {
              ...runtime,
              messages: [
                ...runtime.messages,
                {
                  id: msgId,
                  role: 'assistant',
                  content: '',
                  timestamp: new Date().toISOString(),
                  fileItems: files,
                },
              ],
            },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            messages: runtime.messages.map((msg) =>
              msg.id === targetId
                ? { ...msg, fileItems: [...(msg.fileItems || []), ...files] }
                : msg
            ),
          },
        },
      };
    });
  },

  setContextCompressionStatus: (sessionId, runtime, summary) => {
    set((state) => {
      const r = state.runtimes[sessionId];
      if (!r) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...r,
            contextCompressionRuntime: runtime,
            contextCompressionSummary: summary,
          },
        },
      };
    });
  },
}));
