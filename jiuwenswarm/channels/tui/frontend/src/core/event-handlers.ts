import {
  parseHistoryFrame,
  createAttachmentInfoEntry,
  createSessionResultToolDisplay,
  extractMediaItems,
  isHistoryDonePayload,
} from "./history-parser.js";
import { normalizeFinalContent } from "./final-content.js";
import type { EventFrame } from "./protocol.js";
import {
  StreamingState,
  type ContextCompressionStats,
  type HistoryItem,
  type JsonObject,
  type SubtaskState,
  type TeamMemberEvent,
  type TeamMessageEvent,
  type TeamTaskEvent,
  type TodoItem,
  type ToolCallDisplay,
} from "./types.js";
import type { ConnectionStatus } from "./ws-client.js";
import { createId, findLastIndex, isIgnorableHistoryRestoreError } from "./app-state-helpers.js";
import { isClientMode, type ClientMode } from "./modes.js";

export interface PendingQuestion {
  requestId: string;
  source?: string;
  questions: PendingQuestionItem[];
}

export interface PendingQuestionItem {
  header: string;
  question: string;
  options: PendingQuestionOption[];
  multiSelect?: boolean;
}

export interface PendingQuestionOption {
  label: string;
  description?: string;
}

export interface UserAnswer {
  selected_options: string[];
  custom_input?: string;
}

export interface AppEventDelegate {
  getConnectionStatus(): ConnectionStatus;
  getSessionId(): string;
  setSessionId(sessionId: string): void;
  setMode(mode: ClientMode): void;
  getMode(): ClientMode;
  getEntries(): HistoryItem[];
  setEntries(entries: HistoryItem[]): void;
  setStreamingState(state: StreamingState): void;
  setPendingQuestion(question: PendingQuestion | null): void;
  setLastError(error: string | null): void;
  getActiveSubtasks(): Map<string, SubtaskState>;
  setTodos(todos: TodoItem[]): void;
  appendTeamMemberEvent(event: TeamMemberEvent): void;
  appendTeamTaskEvent(event: TeamTaskEvent): void;
  appendTeamMessageEvent(event: TeamMessageEvent): void;
  setEvolutionStatus(status: "idle" | "running"): void;
  setContextCompression(stats: ContextCompressionStats | null): void;
  setSessionTitle(title: string): void;
  safeFetchSessionTitle(sessionId: string): void;
  addToolCallPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    startedAt?: string,
  ): void;
  addToolResultPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    updatedAt?: string,
  ): void;
  addSyntheticToolExecution(
    tool: ToolCallDisplay,
    sessionId: string,
    requestId?: string,
    at?: string,
  ): void;
  clearToolExecutionState(): void;
  /** 用户中断：将 running 的工具标为已结束，避免 TUI 继续转圈 */
  markRunningToolsInterrupted(): void;
  /** 退出前 cancel({showNotice:false}) 置 true，抑制 interrupt_result UI 通知。 */
  getSuppressInterruptResult(): boolean;
  clearSuppressInterruptResult(): void;
  /** 清除本地中断请求标志（streaming 结束后调用） */
  clearInterruptRequested(): void;
  pushHistoryEntry(entry: HistoryItem): void;
  scheduleHistoryFlush(): void;
  safeRestoreHistory(sessionId: string): void;
  /** 报告 history.get 流返回的分页元数据（本页 page_idx / total_pages）。 */
  reportHistoryPageMeta(meta: { pageIdx?: number; totalPages?: number }): void;
  /** 某一页 history.get 流已结束（收到 `status: done` 帧），由 app-state 决定是否继续拉下一页。 */
  notifyHistoryPageDone(pageIdx: number): void;
  /** cancel 成功后判断是否需要自动回退 */
  tryAutoRestoreAfterCancel(): Promise<void>;
  /** 累加 chat.usage_summary 事件的 token/cost 数据（按 model 分桶）。 */
  appendUsageSummary(usage: Record<string, unknown>, model?: string): void;
  /** 回合结束时记录执行耗时条目到对话区。 */
  addWorkedForEntry(): void;
}

function _handleSwitchModeToolResult(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): void {
  if (payload.tool_name !== "switch_mode") return;

  const resultRaw = payload.result;
  let subMode: string | null = null;

  if (typeof resultRaw === "object" && resultRaw !== null) {
    // result 已经是解析后的对象
    const data = (resultRaw as Record<string, unknown>).data;
    if (typeof data === "object" && data !== null) {
      const cm = (data as Record<string, unknown>).current_mode;
      if (typeof cm === "string") subMode = cm;
    }
  } else if (typeof resultRaw === "string") {
    // 先尝试 JSON 解析
    try {
      const parsed = JSON.parse(resultRaw);
      if (typeof parsed === "object" && parsed !== null) {
        const data = (parsed as Record<string, unknown>).data;
        if (typeof data === "object" && data !== null) {
          const cm = (data as Record<string, unknown>).current_mode;
          if (typeof cm === "string") subMode = cm;
        }
      }
    } catch {
      // JSON 解析失败，尝试从 Python str 表示中提取
      // 格式如: "success=True data={'current_mode': 'normal', 'message': '...'} error=None"
      const match = resultRaw.match(/current_mode['"]\s*:\s*['"](\w+)['"]/);
      if (match) subMode = match[1];
    }
  }

  if (!subMode) return;

  const existingMode = delegate.getMode();
  let newMode: ClientMode | null = null;
  if (existingMode.startsWith("code.")) {
    newMode = subMode === "plan" ? "code.plan" : subMode === "team" ? "code.team" : "code.normal";
  } else if (existingMode.startsWith("agent.")) {
    newMode = subMode === "plan" ? "agent.plan" : "agent.fast";
  }

  if (newMode && newMode !== existingMode) {
    delegate.setMode(newMode);
  }
}

function appendEntry(delegate: AppEventDelegate, entry: HistoryItem): void {
  delegate.setEntries([...delegate.getEntries(), entry]);
}

function appendThinkingChunk(
  delegate: AppEventDelegate,
  activeSessionId: string,
  content: string,
): void {
  const entries = delegate.getEntries();
  const lastEntry = entries[entries.length - 1];
  if (lastEntry && lastEntry.kind === "thinking" && lastEntry.sessionId === activeSessionId) {
    delegate.setEntries([
      ...entries.slice(0, -1),
      {
        ...lastEntry,
        content: `${lastEntry.content}${content}`,
      },
    ]);
    return;
  }

  appendEntry(delegate, {
    kind: "thinking",
    id: createId("reasoning"),
    sessionId: activeSessionId,
    content,
    at: new Date().toISOString(),
  });
}

function addSessionResultEntry(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
  effectiveEvent: string,
): void {
  const tool = createSessionResultToolDisplay(payload, effectiveEvent);
  delegate.addSyntheticToolExecution(
    tool,
    activeSessionId,
    typeof payload.request_id === "string" ? payload.request_id : undefined,
    new Date().toISOString(),
  );
}

function handleConnectionAck(delegate: AppEventDelegate, frame: EventFrame): boolean {
  if (frame.event !== "connection.ack") {
    return false;
  }
  // session_id is determined at construction time; connection.ack is only
  // used as a signal to restore history once connected.
  const sessionId = delegate.getSessionId();
  if (sessionId && delegate.getConnectionStatus() === "connected") {
    delegate.safeRestoreHistory(sessionId);
    delegate.safeFetchSessionTitle(sessionId);
  }
  return true;
}

function normalizePendingQuestion(payload: Record<string, unknown>): PendingQuestionItem[] {
  const rawQuestions = Array.isArray(payload.questions) ? payload.questions : [];
  const normalized = rawQuestions
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      header: typeof item.header === "string" ? item.header : "Question",
      question: typeof item.question === "string" ? item.question : "",
      options: Array.isArray(item.options)
        ? item.options
            .filter((option): option is Record<string, unknown> =>
              Boolean(option && typeof option === "object"),
            )
            .map((option) => ({
              label: typeof option.label === "string" ? option.label : "",
              description: typeof option.description === "string" ? option.description : undefined,
            }))
            .filter((option) => option.label.length > 0)
        : [],
      multiSelect: item.multi_select === true,
    }))
    .filter((item) => item.question.length > 0);

  if (normalized.length > 0) {
    return normalized;
  }

  const fallbackText =
    typeof payload.text === "string"
      ? payload.text
      : typeof payload.content === "string"
        ? payload.content
        : "";
  if (!fallbackText) {
    return [];
  }

  return [
    {
      header: "Question",
      question: fallbackText,
      options: [],
      multiSelect: false,
    },
  ];
}

function handleDelta(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = typeof payload.content === "string" ? payload.content : "";
  if (!content) return false;

  const entries = delegate.getEntries();
  if (payload.source_chunk_type === "llm_reasoning") {
    appendThinkingChunk(delegate, activeSessionId, content);
    return true;
  }

  const requestId = typeof payload.request_id === "string" ? payload.request_id : undefined;
  const existingIndex = findLastIndex(
    entries,
    (entry) => entry.kind === "assistant" && entry.streaming === true,
  );
  if (existingIndex === -1) {
    delegate.setEntries([
      ...entries,
      {
        kind: "assistant",
        id: createId("stream"),
        sessionId: activeSessionId,
        content,
        requestId,
        streaming: true,
        at: new Date().toISOString(),
      },
    ]);
  } else {
    delegate.setEntries(
      entries.map((entry, index) =>
        index === existingIndex && entry.kind === "assistant"
          ? { ...entry, content: entry.content + content, requestId: entry.requestId ?? requestId }
          : entry,
      ),
    );
  }
  delegate.setStreamingState(StreamingState.Responding);
  return true;
}

function chooseFinalAssistantContent(streamedContent: string, finalContent: string): string {
  if (!streamedContent) {
    return finalContent;
  }
  if (!finalContent) {
    return streamedContent;
  }
  if (finalContent === streamedContent || finalContent.startsWith(streamedContent)) {
    return finalContent;
  }
  if (streamedContent.includes(finalContent)) {
    return streamedContent;
  }
  return finalContent.length >= streamedContent.length ? finalContent : streamedContent;
}

function handleFinal(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = normalizeFinalContent(payload);
  const finalizedAt = new Date().toISOString();
  const entries = delegate.getEntries();
  const streamingIndex = findLastIndex(
    entries,
    (entry) => entry.kind === "assistant" && entry.streaming === true,
  );
  const streamedContent =
    streamingIndex !== -1 && entries[streamingIndex]?.kind === "assistant"
      ? entries[streamingIndex].content
      : "";
  const finalContent = chooseFinalAssistantContent(streamedContent, content);
  delegate.setEntries(
    streamingIndex !== -1
      ? [
          ...entries.filter(
            (entry, index) => !(index === streamingIndex && entry.kind === "assistant"),
          ),
          {
            ...(entries[streamingIndex] as Extract<HistoryItem, { kind: "assistant" }>),
            content: finalContent,
            requestId:
              typeof payload.request_id === "string"
                ? payload.request_id
                : entries[streamingIndex]?.kind === "assistant"
                  ? entries[streamingIndex].requestId
                  : undefined,
            at: finalizedAt,
            streaming: false,
          },
        ]
      : [
          ...entries,
          {
            kind: "assistant",
            id: createId("assistant-final"),
            sessionId: activeSessionId,
            content,
            requestId: typeof payload.request_id === "string" ? payload.request_id : undefined,
            streaming: false,
            at: finalizedAt,
          },
        ],
  );
  delegate.setStreamingState(StreamingState.Idle);
  delegate.addWorkedForEntry();
  return true;
}

function handleReasoning(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = typeof payload.content === "string" ? payload.content : "";
  if (!content) return false;
  appendThinkingChunk(delegate, activeSessionId, content);
  return true;
}

function handleError(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const message =
    typeof payload.error === "string"
      ? payload.error
      : typeof payload.content === "string"
        ? payload.content
        : "Unknown error";
  if (isIgnorableHistoryRestoreError(message)) {
    return false;
  }
  appendEntry(delegate, {
    kind: "error",
    id: createId("error"),
    sessionId: activeSessionId,
    content: message,
    at: new Date().toISOString(),
  });
  delegate.setLastError(message);
  delegate.setStreamingState(StreamingState.Idle);
  delegate.addWorkedForEntry();
  return true;
}

function handleMediaEvent(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
  effectiveEvent: "chat.media" | "chat.file",
): boolean {
  const at = new Date().toISOString();
  const content = typeof payload.content === "string" ? payload.content.trim() : "";
  const mediaItems = extractMediaItems(payload);
  if (effectiveEvent === "chat.media" && (content || mediaItems.length > 0)) {
    const entries = delegate.getEntries();
    const assistantIndex = findLastIndex(
      entries,
      (entry) => entry.kind === "assistant" && (entry.streaming === true || !entry.streaming),
    );
    if (assistantIndex !== -1) {
      delegate.setEntries(
        entries.map((entry, index) =>
          index === assistantIndex && entry.kind === "assistant"
            ? {
                ...entry,
                ...(content ? { content } : {}),
                ...(mediaItems.length > 0 ? { mediaItems } : {}),
                streaming: false,
              }
            : entry,
        ),
      );
    } else {
      appendEntry(delegate, {
        kind: "assistant",
        id: createId("assistant-media"),
        sessionId: activeSessionId,
        content,
        ...(mediaItems.length > 0 ? { mediaItems } : {}),
        at,
        streaming: false,
      });
    }
    return true;
  }

  const infoEntry = createAttachmentInfoEntry(payload, activeSessionId, effectiveEvent, at);
  if (infoEntry) {
    appendEntry(delegate, infoEntry);
    return true;
  }

  appendEntry(delegate, {
    kind: "system",
    id: createId("system"),
    sessionId: activeSessionId,
    content: `[${effectiveEvent}]`,
    at,
    meta: {
      eventType: effectiveEvent,
      rawPayload: payload as JsonObject,
    },
  });
  return true;
}

function handleContextCompressed(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const rate = typeof payload.rate === "number" ? payload.rate : 0;
  const before = typeof payload.before_compressed === "number" ? payload.before_compressed : null;
  const after = typeof payload.after_compressed === "number" ? payload.after_compressed : null;
  delegate.setContextCompression({
    rate,
    beforeCompressed: before,
    afterCompressed: after,
  });
  return true;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatNumber(value: unknown): string {
  const n = readNumber(value);
  return n == null ? "-" : Math.round(n).toLocaleString("en-US");
}

function formatPercent(value: unknown): string {
  const n = readNumber(value);
  return n == null ? "-" : `${Math.round(n)}%`;
}

function formatDuration(value: unknown): string {
  const n = readNumber(value);
  if (n == null) return "-";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`;
}

function formatChange(before: unknown, after: unknown, formatter = formatNumber): string {
  const beforeText = formatter(before);
  const afterText = formatter(after);
  if (afterText === "-") return beforeText;
  return `${beforeText} -> ${afterText}`;
}

function handleContextCompressionState(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const before = asRecord(payload.before);
  const after = asRecord(payload.after);
  const saved = asRecord(payload.saved);
  const status = readString(payload.status, "unknown");
  const phase = readString(payload.phase, "unknown");
  const processor = readString(payload.processor, "unknown") || "unknown";
  const model = readString(payload.model);
  const summary = readString(payload.summary);
  const error = readString(payload.error);

  const savedParts = [
    `${formatNumber(saved.tokens)} tokens`,
    `${formatNumber(saved.messages)} messages`,
    formatPercent(saved.percent),
  ].filter((part) => !part.startsWith("-"));

  appendEntry(delegate, {
    kind: "info",
    id: createId("context-compression"),
    sessionId: activeSessionId,
    content: `Context compression ${status}`,
    icon: "i",
    meta: {
      title: `Context compression ${status}`,
      items: [
        { label: "Processor", value: processor },
        { label: "Phase", value: phase },
        ...(model ? [{ label: "Model", value: model }] : []),
        { label: "Messages", value: formatChange(before.messages, after.messages) },
        { label: "Tokens", value: formatChange(before.tokens, after.tokens) },
        {
          label: "Context",
          value: formatChange(before.context_percent, after.context_percent, formatPercent),
        },
        ...(savedParts.length ? [{ label: "Saved", value: savedParts.join(" | ") }] : []),
        { label: "Duration", value: formatDuration(payload.duration_ms) },
        ...(summary ? [{ label: "Summary", description: summary }] : []),
        ...(error ? [{ label: "Error", description: error }] : []),
      ],
    },
    at: new Date().toISOString(),
  });
  return true;
}

function handleSubtaskUpdate(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const taskId = typeof payload.task_id === "string" ? payload.task_id : "";
  if (!taskId) return false;
  const subtasks = delegate.getActiveSubtasks();
  if (payload.status === "completed" || payload.status === "error") {
    subtasks.delete(taskId);
    return true;
  }
  subtasks.set(taskId, {
    task_id: taskId,
    description: typeof payload.description === "string" ? payload.description : "",
    status: (typeof payload.status === "string"
      ? payload.status
      : "starting") as SubtaskState["status"],
    index: typeof payload.index === "number" ? payload.index : 0,
    total: typeof payload.total === "number" ? payload.total : 0,
    tool_name: typeof payload.tool_name === "string" ? payload.tool_name : undefined,
    tool_count: typeof payload.tool_count === "number" ? payload.tool_count : 0,
    message: typeof payload.message === "string" ? payload.message : undefined,
    is_parallel: payload.is_parallel === true,
  });
  return true;
}

function normalizeTodoStatus(status: unknown): TodoItem["status"] | null {
  if (status === "deleted" || status === "cancelled" || status === "canceled") {
    return null;
  }
  if (status === "in_progress" || status === "completed") {
    return status;
  }
  return "pending";
}

function isTodoPayloadItem(item: unknown): item is Record<string, unknown> {
  return Boolean(item && typeof item === "object" && !Array.isArray(item));
}

function handleTodoUpdated(delegate: AppEventDelegate, payload: Record<string, unknown>): boolean {
  const todos = Array.isArray(payload.todos) ? payload.todos : [];
  delegate.setTodos(
    todos
      .filter(isTodoPayloadItem)
      .map((item): TodoItem | null => {
        const status = normalizeTodoStatus(item.status);
        if (status === null) {
          return null;
        }
        return {
          id: typeof item.id === "string" ? item.id : "",
          content: typeof item.content === "string" ? item.content : "",
          activeForm: typeof item.activeForm === "string" ? item.activeForm : "",
          status,
          createdAt: typeof item.createdAt === "string" ? item.createdAt : new Date().toISOString(),
          updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : new Date().toISOString(),
        };
      })
      .filter((item): item is TodoItem => item !== null && item.id.length > 0),
  );
  return true;
}

function normalizeNestedPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const nested = payload.payload;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return payload;
}

function normalizeTimestamp(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return Date.now();
}

function handleTeamMemberEvent(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const normalized = normalizeNestedPayload(payload);
  const event = normalized.event;
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return false;
  }
  const record = event as Record<string, unknown>;
  const memberId = typeof record.member_id === "string" ? record.member_id.trim() : "";
  if (!memberId) {
    return false;
  }
  delegate.appendTeamMemberEvent({
    id: createId("team-member"),
    type: typeof record.type === "string" ? record.type : "team.member",
    teamId: typeof record.team_id === "string" ? record.team_id : "",
    memberId,
    oldStatus: typeof record.old_status === "string" ? record.old_status : undefined,
    newStatus: typeof record.new_status === "string" ? record.new_status : undefined,
    reason: typeof record.reason === "string" ? record.reason : undefined,
    restartCount: typeof record.restart_count === "number" ? record.restart_count : undefined,
    force: typeof record.force === "boolean" ? record.force : undefined,
    timestamp: normalizeTimestamp(record.timestamp),
  });
  return true;
}

function handleTeamTaskEvent(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const normalized = normalizeNestedPayload(payload);
  const event = normalized.event;
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return false;
  }
  const record = event as Record<string, unknown>;
  const taskId = typeof record.task_id === "string" ? record.task_id.trim() : "";
  if (!taskId) {
    return false;
  }
  delegate.appendTeamTaskEvent({
    id: createId("team-task"),
    type: typeof record.type === "string" ? record.type : "team.task",
    teamId: typeof record.team_id === "string" ? record.team_id : "",
    taskId,
    status: typeof record.status === "string" ? record.status : undefined,
    timestamp: normalizeTimestamp(record.timestamp),
  });
  return true;
}

function handleTeamMessageEvent(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const normalized = normalizeNestedPayload(payload);
  const event = normalized.event;
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return false;
  }
  const record = event as Record<string, unknown>;
  const fromMember = typeof record.from_member === "string" ? record.from_member.trim() : "";
  if (!fromMember) {
    return false;
  }
  delegate.appendTeamMessageEvent({
    id: createId("team-message"),
    type: typeof record.type === "string" ? record.type : "team.message",
    teamId: typeof record.team_id === "string" ? record.team_id : "",
    messageId: typeof record.message_id === "string" ? record.message_id : undefined,
    fromMember,
    toMember: typeof record.to_member === "string" ? record.to_member : undefined,
    content: typeof record.content === "string" ? record.content : "",
    timestamp: normalizeTimestamp(record.timestamp),
  });
  return true;
}

export function handleIncomingFrame(delegate: AppEventDelegate, frame: EventFrame): boolean {
  const connectionChanged = handleConnectionAck(delegate, frame);

  const payload = frame.payload;
  const effectiveEvent = typeof payload.event_type === "string" ? payload.event_type : frame.event;
  const activeSessionId = delegate.getSessionId();
  const eventSessionId = typeof payload.session_id === "string" ? payload.session_id : "";
  if (effectiveEvent === "chat.processing_status" && !eventSessionId) {
    return connectionChanged;
  }
  if (eventSessionId && eventSessionId !== activeSessionId) {
    return connectionChanged;
  }

  switch (effectiveEvent) {
    case "chat.delta":
      return handleDelta(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.final":
      return handleFinal(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.reasoning":
      return handleReasoning(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.error":
      return handleError(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.tool_call":
      delegate.addToolCallPayload(
        payload,
        activeSessionId,
        typeof payload.request_id === "string" ? payload.request_id : undefined,
      );
      return true;

    case "chat.tool_result":
      _handleSwitchModeToolResult(delegate, payload);
      delegate.addToolResultPayload(
        payload,
        activeSessionId,
        typeof payload.request_id === "string" ? payload.request_id : undefined,
      );
      return true;

    case "chat.processing_status":
      delegate.setStreamingState(
        payload.is_processing === true ? StreamingState.Responding : StreamingState.Idle,
      );
      if (payload.is_processing !== true) {
        delegate.getActiveSubtasks().clear();
        delegate.setTodos([]);
        delegate.setEvolutionStatus("idle");
        delegate.clearInterruptRequested();
      }
      return true;

    case "chat.interrupt_result": {
      const intent = typeof payload.intent === "string" ? payload.intent : "cancel";
      if (intent === "cancel") {
        const suppressed = delegate.getSuppressInterruptResult();
        if (suppressed) {
          delegate.clearSuppressInterruptResult();
          return true;
        }
        const success = payload.success !== false;
        const message =
          typeof payload.message === "string" && payload.message.trim()
            ? payload.message
            : success
              ? "当前会话任务已终止"
              : "当前会话任务终止失败";
        if (success) {
          delegate.setStreamingState(StreamingState.Interrupted);
          delegate.getActiveSubtasks().clear();
          delegate.setEvolutionStatus("idle");
          delegate.markRunningToolsInterrupted();
          delegate.clearInterruptRequested();
          appendEntry(delegate, {
            kind: "info",
            id: createId("info"),
            sessionId: activeSessionId,
            content: message,
            icon: "i",
            at: new Date().toISOString(),
          });
          // 仅当目标 turn 之后无实质性 assistant 输出时执行
          delegate.tryAutoRestoreAfterCancel().catch(() => {
            // 自动回退失败不影响 cancel 本身，用户可手动 /rewind
          });
        } else {
          appendEntry(delegate, {
            kind: "error",
            id: createId("error"),
            sessionId: activeSessionId,
            content: message,
            at: new Date().toISOString(),
          });
          delegate.setLastError(message);
        }
      } else if (intent === "pause") {
        delegate.setStreamingState(StreamingState.Paused);
      } else {
        delegate.setStreamingState(StreamingState.Responding);
      }
      return true;
    }

    case "chat.ask_user_question": {
      const requestId = typeof payload.request_id === "string" ? payload.request_id : "";
      const questions = normalizePendingQuestion(payload);
      if (!requestId || questions.length === 0) {
        return connectionChanged;
      }
      delegate.setPendingQuestion({
        requestId,
        source: typeof payload.source === "string" ? payload.source : undefined,
        questions,
      });
      delegate.setStreamingState(StreamingState.WaitingForConfirmation);
      return true;
    }

    case "history.message": {
      // 先感知分页元数据（done 帧不会产生 entry，但必须让 app-state 感知）。
      const pageIdxRaw = payload.page_idx;
      const totalPagesRaw = payload.total_pages;
      delegate.reportHistoryPageMeta({
        pageIdx: typeof pageIdxRaw === "number" ? pageIdxRaw : undefined,
        totalPages: typeof totalPagesRaw === "number" ? totalPagesRaw : undefined,
      });
      if (isHistoryDonePayload(payload)) {
        if (typeof pageIdxRaw === "number") {
          delegate.notifyHistoryPageDone(pageIdxRaw);
        }
        return connectionChanged;
      }
      const entry = parseHistoryFrame(frame);
      if (!entry) {
        return connectionChanged;
      }
      delegate.pushHistoryEntry(entry);
      delegate.scheduleHistoryFlush();
      return connectionChanged;
    }

    case "chat.media":
    case "chat.file":
      return handleMediaEvent(delegate, payload, activeSessionId, effectiveEvent);

    case "context.compressed":
      return handleContextCompressed(delegate, payload);

    case "context_compression_state":
      return handleContextCompressionState(delegate, payload, activeSessionId);

    case "chat.subtask_update":
      return handleSubtaskUpdate(delegate, payload);

    case "chat.session_result":
    case "session_result":
      addSessionResultEntry(delegate, payload, activeSessionId, effectiveEvent);
      return true;

    case "chat.evolution_status":
      delegate.setEvolutionStatus(payload.status === "start" ? "running" : "idle");
      return true;

    case "todo.updated":
      return handleTodoUpdated(delegate, payload);

    case "session.updated": {
      const mode = typeof payload.mode === "string" ? payload.mode : "";
      if (isClientMode(mode)) {
        delegate.setMode(mode);
      }
      if (typeof payload.title === "string") {
        delegate.setSessionTitle(payload.title);
      }
      return true;
    }

    case "team.member":
      return handleTeamMemberEvent(delegate, payload);

    case "team.task":
      return handleTeamTaskEvent(delegate, payload);

    case "team.message":
      return handleTeamMessageEvent(delegate, payload);

    case "chat.usage_summary":
      delegate.appendUsageSummary(
        typeof payload.usage === "object" && payload.usage !== null
          ? (payload.usage as Record<string, unknown>)
          : {},
        typeof payload.model === "string" ? payload.model : undefined,
      );
      return true;

    default:
      return connectionChanged;
  }
}
