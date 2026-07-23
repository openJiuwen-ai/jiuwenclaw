/**
 * WebSocket Hook
 *
 * 管理 WebSocket 连接和消息处理
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
  EvolutionStatusPayload,
  UserAnswer,
  MediaItem,
  AgentMode,
  Session,
  ToolResult,
  ToolCall,
  UsageSummary,
  FileDownloadItem,
  ContextCompressionRuntime,
  ContextCompressionSummary,
  WsEvent,
  GoalRecord,
  GoalAction,
  Message,
} from '../types';
import {
  ensureSessionRuntimes,
  useChatStore,
  useTodoStore,
  useGoalStore,
  useSessionStore,
  useHarnessStore,
  useWorkspaceStore,
  useCronStore,
} from '../stores';
import type { TeamTask, TeamTaskStatus, TeamTaskUpsert } from '../stores/sessionStore';
import { webClient, requestGoalAction, sendGoalStreamCommand } from '../services/webClient';
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
  normalizeToolUpdatePayload,
} from '../features/tool-events/toolEventNormalizer';
import { findActiveTeamLeaderMessage as findActiveTeamLeaderMessageInTurn } from '../features/teamLeaderMessages';
import { buildGoalCompletedContent } from '../components/GoalBar/goalCompletedMessage';

const WS_RECONNECT_EVENT = 'jiuwenclaw:ws-reconnect-request';

function isCompletedResumeResult(interruptResult: unknown): boolean {
  if (!interruptResult || typeof interruptResult !== 'object') {
    return false;
  }
  const result = interruptResult as {
    intent?: unknown;
    success?: unknown;
    has_active_task?: unknown;
  };
  return result.intent === 'resume' && result.success === true && result.has_active_task === false;
}

const GOAL_COMPLETED_AUTO_HIDE_MS = 4000;
const GOAL_COMPLETED_SETTLE_FALLBACK_MS = 8000;
/** get 失败后的重试间隔（毫秒）：首次失败后再试 2 次，都失败才判定为 unknown（真实环境联调方案）。 */
const GOAL_GET_RETRY_DELAYS_MS = [3000, 5000];
/** 目标处于 active/paused/blocked 期间，超过这个时长没收到新的 goal.snapshot/goal.updated 就主动 get 兜底。 */
const GOAL_STALE_REFRESH_MS = 60000;
const GOAL_STALE_REFRESH_CHECK_INTERVAL_MS = 15000;
/**
 * 已经判定为 unknown（performGoalGet 自己的 3 次重试都失败过）之后，巡检 effect 不再按
 * GOAL_STALE_REFRESH_MS 的节奏重试——那样等于每 15s 就重新打一轮 3 连击，事实上会无限循环
 * 高频重试一个已知连不上的后端。改成退避到这个更长的间隔再试一次，只要某次 get 成功
 * （queryStatus 收敛回 'ok'），下一次巡检就会自动切回正常的 GOAL_STALE_REFRESH_MS 节奏。
 */
const GOAL_UNKNOWN_RETRY_INTERVAL_MS = 5 * 60 * 1000;
/**
 * set 发出后，等这么久还没等到 goal.snapshot/execution.error 把 pendingAction 清掉，才补一次
 * 兜底 get——正常路径下 snapshot 应该早就到了，不必让这个兜底跟它赛跑（赛跑赢了反而会用 set
 * 落地前的旧数据提前清掉 pendingAction，重新打开"按钮提前解禁、能打出冲突指令"的窗口）。这个值
 * 只是给首次设置目标时快照丢包这种小概率情况兜底，不需要很短。
 */
const GOAL_SET_CONVERGENCE_DELAY_MS = 4000;

/**
 * 目标完成事件（goal.updated）和它所在这一轮回复的正文（chat.delta/chat.final），走的是两条
 * 独立的推送通道，后端不保证到达顺序——真机复现过 goal.updated 先到、回复气泡还没 flush 进
 * 消息列表的情况，这时候直接插入"目标完成"提示会出现在它引用的那条回复上方，很怪。用
 * isProcessing 变 false（chat.processing_status 事件驱动，标志这一轮真正结束）当"可以展示了"
 * 的信号：当时已经不在 processing 就立即展示；还在 processing 就等它落地。加一个兜底超时，
 * 防止极端情况下 isProcessing 迟迟不落地导致完成提示永远卡住不出现。
 */
function scheduleAfterTurnSettles(sessionId: string, run: () => void): void {
  const isProcessing = useChatStore.getState().getRuntime(sessionId)?.isProcessing ?? false;
  if (!isProcessing) {
    run();
    return;
  }
  let settled = false;
  let fallbackTimer: ReturnType<typeof window.setTimeout>;
  const finish = () => {
    if (settled) return;
    settled = true;
    unsubscribe();
    window.clearTimeout(fallbackTimer);
    run();
  };
  const unsubscribe = useChatStore.subscribe(
    (state) => state.runtimes[sessionId]?.isProcessing ?? false,
    (isProcessing) => {
      if (!isProcessing) finish();
    }
  );
  fallbackTimer = window.setTimeout(finish, GOAL_COMPLETED_SETTLE_FALLBACK_MS);
}

/**
 * goal.snapshot/goal.updated 事件、command.goal 的 RPC 响应，统一落进 goalStore 的入口
 * （被 goalAction 和事件订阅共用，定义成模块级函数而不是 hook 内的 useCallback，避免要把它塞进
 * 那个几乎跑满全文件的大 useEffect 的依赖数组）。处理三件事：
 *
 * 1) created_at 兜底（后端永久不下发，backend-requests.md #2）；
 * 2) completed 展示策略——只有"本地在这次页面存活期间亲眼见过这个 goal_id 处于非 completed
 *    状态"才当成实时目睹的跳变：冻结计时、插一条完成消息、展示几秒后本地隐藏（不调用后端
 *    clearGoal，后端记录原样保留）。单纯靠 get/事件第一次拿到就已经是 completed（切会话/
 *    刷新页面最常见）一律直接不展示、不插消息——用户没看见"跳变过程"，硬展示一条早就完成的
 *    目标条没有意义，还可能重复插消息。
 * 3) 其余状态（active/paused/blocked）照常落状态，不做特殊处理，blocked 也不会自动隐藏。
 */
function applyIncomingGoal(
  sessionId: string,
  goal: GoalRecord | null,
  hideTimerMap: Map<string, ReturnType<typeof window.setTimeout>>,
  lastGoalEventAtMap?: Map<string, number>
): void {
  const goalStore = useGoalStore.getState();
  // 任何一次成功落地（不管是 get 的响应，还是 goal.snapshot/goal.updated 事件）都说明查询链路
  // 是通的，把"多次 get 失败"攒出来的 unknown 态收敛回 ok——见 goalStore.ts queryStatus 注释。
  goalStore.setQueryStatus(sessionId, 'ok');

  if (!goal) {
    const prevGoalId = goalStore.runtimes[sessionId]?.goal?.goal_id;
    if (prevGoalId) {
      goalStore.clearLocalCreatedAt(prevGoalId);
      goalStore.clearGoalCompletionPhase(prevGoalId);
      goalStore.clearBannerHidden(prevGoalId);
      goalStore.clearCompletedGoalMessage(prevGoalId);
      const timer = hideTimerMap.get(prevGoalId);
      if (timer !== undefined) {
        window.clearTimeout(timer);
        hideTimerMap.delete(prevGoalId);
      }
    }
    lastGoalEventAtMap?.delete(sessionId);
    goalStore.setGoal(sessionId, null);
    goalStore.setPendingAction(sessionId, null);
    return;
  }

  // 未完成目标才需要参与"1 分钟无更新兜底 get"的巡检（见 useWebSocket 里的巡检 effect），
  // completed 记一次时间戳没有意义，巡检本身也会按 status 过滤掉，这里顺手不记，语义更清楚。
  if (goal.status !== 'completed') {
    lastGoalEventAtMap?.set(sessionId, Date.now());
  } else {
    lastGoalEventAtMap?.delete(sessionId);
  }

  goalStore.setLocalCreatedAt(goal.goal_id, new Date().toISOString());

  if (goal.status !== 'completed') {
    goalStore.markGoalSeenActive(goal.goal_id);
    // 目标曾经 completed 过（编辑复活/其它途径重新进入非 completed 状态）——清掉旧的隐藏标记，
    // 不然它下次再完成时 GoalBar 会因为这个陈旧的 true 直接判定"该隐藏"，一秒都不展示就消失。
    goalStore.clearBannerHidden(goal.goal_id);
    goalStore.setGoal(sessionId, goal);
    goalStore.setPendingAction(sessionId, null);
    return;
  }

  const phase = goalStore.getGoalCompletionPhase(goal.goal_id);
  goalStore.setPendingAction(sessionId, null);
  // goal 数据本身（含 objective）永远落进 store，不管是不是要展示 GoalBar——MessageItem 的
  // "设为目标"徽章靠字符串匹配 goal.objective，之前完成态曾经把 goal 整个置 null 来隐藏
  // GoalBar，副作用是刷新页面后连徽章也一起丢了。GoalBar 是否展示改用下面的 hideGoalBanner
  // 单独控制，不再靠 goal 是否存在来判断。
  goalStore.setGoal(sessionId, goal);
  if (phase === 'completed-announced') {
    // 已经宣布/判定过一次了（无论是走过完整跳变流程，还是当初就判定为"发现即完成"），不重复处理
    return;
  }

  const isLiveTransition = phase === 'seen-active';
  goalStore.markGoalCompletedAnnounced(goal.goal_id);

  if (!isLiveTransition) {
    // get/事件第一次拿到就已经是 completed——GoalBar 不展示（用户没看见跳变过程），但 goal
    // 数据保留，徽章匹配等消费方不受影响
    goalStore.hideGoalBanner(goal.goal_id);
    return;
  }

  // 实时目睹的跳变：等这一轮回复正文落地后，再一起展示冻结态 + 完成消息，避免完成提示抢跑到
  // 它引用的那条回复上方。内容用 goal.completed: 前缀 + JSON 编码，MessageItem 检测到后渲染
  // GoalCompletedCard（卡片+头部标签样式），跟普通回复气泡明显区分开。
  scheduleAfterTurnSettles(sessionId, () => {
    const messageId = `goal-completed-${goal.goal_id}`;
    // "现在"必然晚于它引用的那条回复——回复的时间戳是收到第一个 chat.delta 时盖的章，不会再被
    // chat.final 收尾时改动（见 chat.final 处理里去掉时间戳覆盖的注释），早于整轮跑完+判定目标
    // 完成的这一刻，不需要额外兜底。
    const timestamp = new Date().toISOString();
    const content = buildGoalCompletedContent({ evidence: goal.last_assessment?.evidence?.trim() });
    useChatStore.getState().addMessage(sessionId, {
      id: messageId,
      role: 'assistant',
      content,
      timestamp,
    });
    // 这条消息纯前端合成，从未写进后端 session 历史——刷新页面后 history.get 拉回来的历史里
    // 没有它，会随 replaceHistoryMessages 整体覆盖消失。存一份到 localStorage，历史加载完成后
    // （App.tsx）按时间戳把它插回去，见 mergePersistedGoalCompletionMessages。
    goalStore.recordCompletedGoalMessage({ sessionId, id: messageId, content, timestamp });

    const existingTimer = hideTimerMap.get(goal.goal_id);
    if (existingTimer !== undefined) {
      window.clearTimeout(existingTimer);
    }
    const timer = window.setTimeout(() => {
      hideTimerMap.delete(goal.goal_id);
      const current = useGoalStore.getState().runtimes[sessionId]?.goal;
      // 展示期间用户没有手动清除/编辑目标才自动隐藏，避免和用户的显式操作打架
      if (current?.goal_id === goal.goal_id) {
        useGoalStore.getState().hideGoalBanner(goal.goal_id);
      }
    }, GOAL_COMPLETED_AUTO_HIDE_MS);
    hideTimerMap.set(goal.goal_id, timer);
  });
}

/**
 * 目标查询（command.goal get）的统一入口，带重试 + unknown 兜底（真实环境联调方案 C.e）：
 * 失败先按 GOAL_GET_RETRY_DELAYS_MS 重试，全部重试完还失败就把 queryStatus 置 'unknown'
 * （GoalBar 据此展示"状态未知"、全部操作按钮置灰），不再抛错、不插聊天错误消息——这是背景
 * 巡检性质的调用（会话切换、断线重连、1 分钟无更新兜底都会触发），不是用户主动点击的操作，
 * 不需要用系统消息打扰聊天记录。
 *
 * 也被 goalAction 的 pause/resume/clear 失败兜底复用：一元 RPC 失败时 webClient 目前不会把
 * payload.goal 透传进 WebError（见 webClient.ts resolvePending 只读顶层 error/code），拿不到
 * 失败当时的目标快照，索性直接用这个函数重新问一次权威状态。
 *
 * lastAttemptAtMap 记录的是"每次调用本函数的时刻"（不管这轮 3 连击最终成没成功），只给巡检
 * effect 在 unknown 态下判断退避窗口用；跟 lastGoalEventAtMap（只在成功时更新，判断数据新鲜度）
 * 是两回事，不要混用。
 */
async function performGoalGet(
  sessionId: string,
  mode: string,
  hideTimerMap: Map<string, ReturnType<typeof window.setTimeout>>,
  lastGoalEventAtMap?: Map<string, number>,
  lastAttemptAtMap?: Map<string, number>
): Promise<void> {
  lastAttemptAtMap?.set(sessionId, Date.now());
  for (let attempt = 0; ; attempt += 1) {
    try {
      const goal = await requestGoalAction({ sessionId, action: 'get', mode });
      applyIncomingGoal(sessionId, goal, hideTimerMap, lastGoalEventAtMap);
      return;
    } catch {
      if (attempt >= GOAL_GET_RETRY_DELAYS_MS.length) {
        useGoalStore.getState().setQueryStatus(sessionId, 'unknown');
        useGoalStore.getState().setPendingAction(sessionId, null);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, GOAL_GET_RETRY_DELAYS_MS[attempt]));
    }
  }
}

/**
 * 会话历史加载完成后调用：把 localStorage 里持久化的"目标完成"消息（见 applyIncomingGoal 的
 * 实时跳变分支）按时间戳合并回刚加载的历史消息数组里——这些消息从未写进后端 session 历史，
 * 单靠 history.get 的结果永远不会包含它们。已经在 messages 里的（极端情况下未来后端也开始
 * 持久化同 id 的消息）不重复插入。
 */
export function mergePersistedGoalCompletionMessages(sessionId: string, messages: Message[]): Message[] {
  const persisted = useGoalStore.getState().getCompletedGoalMessagesForSession(sessionId);
  if (persisted.length === 0) return messages;
  const existingIds = new Set(messages.map((m) => m.id));
  const missing = persisted.filter((record) => !existingIds.has(record.id));
  if (missing.length === 0) return messages;
  const merged: Message[] = [
    ...messages,
    ...missing.map((record) => ({
      id: record.id,
      role: 'assistant' as const,
      content: record.content,
      timestamp: record.timestamp,
    })),
  ];
  merged.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  return merged;
}

/**
 * 会话历史加载完成后调用：给命中 goalStore 持久化 objective 文本列表的 user 消息回填
 * `isGoalObjectiveMessage`（见 goalStore.ts objectiveMessageTexts 的注释）。history.get
 * 返回的消息不带任何前端专属字段，本地回显时打的标记刷新后会被这批新对象整体替换掉，只能
 * 在这里按 content 重新核对一次——不依赖当前 Goal 状态，即使目标已被清除/替换也照样命中。
 */
export function stampGoalObjectiveMessages(sessionId: string, messages: Message[]): Message[] {
  const objectiveTexts = useGoalStore.getState().getGoalObjectiveTextsForSession(sessionId);
  if (objectiveTexts.length === 0) return messages;
  const objectiveTextSet = new Set(objectiveTexts);
  let changed = false;
  const stamped = messages.map((message) => {
    if (message.role !== 'user' || message.isGoalObjectiveMessage) return message;
    if (!objectiveTextSet.has(message.content)) return message;
    changed = true;
    return { ...message, isGoalObjectiveMessage: true };
  });
  return changed ? stamped : messages;
}

function getConnectSignature(options: WebConnectOptions): string {
  return JSON.stringify({
    provider: options.provider || '',
    apiKey: options.apiKey || '',
    apiBase: options.apiBase || '',
    model: options.model || '',
    projectDir: options.projectDir || '',
  });
}

const TEAM_TASK_STATUS_SET = new Set<TeamTaskStatus>([
  'pending',
  'blocked',
  'planning',
  'in_progress',
  'in_review',
  'completed',
  'cancelled',
]);

function normalizeTeamTaskStatus(
  status: unknown,
  fallback: TeamTaskStatus = 'pending'
): TeamTaskStatus {
  return typeof status === 'string' && TEAM_TASK_STATUS_SET.has(status as TeamTaskStatus)
    ? status as TeamTaskStatus
    : fallback;
}

function pickString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function resolveInterruptResumeMode(sessionId: string): AgentMode {
  const sessionStore = useSessionStore.getState();
  const session =
    sessionStore.currentSession?.session_id === sessionId
      ? sessionStore.currentSession
      : sessionStore.sessions.find((item) => item.session_id === sessionId);
  if (session?.team_name?.trim()) return 'team';
  return normalizeAgentMode(sessionStore.runtimes[sessionId]?.mode);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getPayloadSessionId(payload: Record<string, unknown>): string | undefined {
  const direct = pickString(payload.session_id);
  if (direct) {
    return direct;
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = pickString(nestedPayload.session_id);
    if (nested) {
      return nested;
    }
    const nestedEvent = nestedPayload.event;
    if (isRecord(nestedEvent)) {
      return pickString(nestedEvent.session_id);
    }
  }
  const event = payload.event;
  if (isRecord(event)) {
    return pickString(event.session_id);
  }
  return undefined;
}

function getPayloadRequestId(payload: Record<string, unknown>): string | undefined {
  const direct = pickString(payload.request_id, payload.rid);
  if (direct) {
    return direct;
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = pickString(nestedPayload.request_id, nestedPayload.rid);
    if (nested) {
      return nested;
    }
  }
  return undefined;
}

function normalizeStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const normalized = value.filter(
    (item): item is string => typeof item === 'string' && item.trim().length > 0
  );
  return normalized.length ? normalized : undefined;
}

function normalizeTaskEvent(value: unknown): TeamTaskUpsert | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const taskId = pickString(raw.task_id, raw.id);
  if (!taskId) {
    return null;
  }
  // Status is resolved server-side (swarm layer) and read directly here — the
  // frontend no longer derives it from the event type. An absent status means
  // "no change"; the store preserves the task's existing status.
  const rawStatus = pickString(raw.status);
  return {
    task_id: taskId,
    title: pickString(raw.title, raw.name, raw.description),
    content: pickString(raw.content),
    status: rawStatus ? normalizeTeamTaskStatus(rawStatus) : undefined,
    assignee: pickString(raw.assignee, raw.member_id, raw.claimed_by, raw.claimedBy, raw.from_member),
    team_id: pickString(raw.team_id),
    timestamp: typeof raw.timestamp === 'number' ? raw.timestamp : Date.now(),
    skills: normalizeStringArray(raw.skills),
    files: normalizeStringArray(raw.files),
  };
}

function normalizeTaskRecord(
  value: unknown,
  fallbackStatus: TeamTaskStatus = 'pending'
): TeamTask | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const taskId = pickString(raw.task_id, raw.id);
  if (!taskId) {
    return null;
  }
  const title = pickString(raw.title, raw.name, raw.description);
  const content = pickString(raw.content);
  return {
    task_id: taskId,
    title,
    content,
    status: normalizeTeamTaskStatus(raw.status, fallbackStatus),
    assignee: pickString(raw.assignee, raw.member_id, raw.claimed_by, raw.claimedBy, raw.from_member),
    team_id: pickString(raw.team_id),
    timestamp: typeof raw.timestamp === 'number' ? raw.timestamp : Date.now(),
    skills: normalizeStringArray(raw.skills),
    files: normalizeStringArray(raw.files),
  };
}

function parseShutdownMemberName(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }
  const match = value.match(/Member shutdown:\s*member_name=([^\s,]+)/);
  return match?.[1]?.trim() || undefined;
}

function getShutdownMemberFromToolCall(toolCall: ToolCall): string | undefined {
  if (toolCall.name !== 'shutdown_member') {
    return undefined;
  }
  return pickString(
    toolCall.arguments.member_name,
    toolCall.arguments.member_id,
    toolCall.arguments.name
  );
}

function getShutdownMemberFromToolResult(toolResult: ToolResult): string | undefined {
  if (toolResult.toolName !== 'shutdown_member') {
    return parseShutdownMemberName(toolResult.result);
  }
  return parseShutdownMemberName(toolResult.result) || parseShutdownMemberName(toolResult.summary);
}

function upsertTaskRecords(sessionId: string, values: unknown, fallbackStatus: TeamTaskStatus = 'pending') {
  if (!Array.isArray(values)) {
    const task = normalizeTaskRecord(values, fallbackStatus);
    if (task) {
      useSessionStore.getState().upsertTeamTask(sessionId, task);
    }
    return;
  }
  values.forEach((item) => {
    const task = normalizeTaskRecord(item, fallbackStatus);
    if (task) {
      useSessionStore.getState().upsertTeamTask(sessionId, task);
    }
  });
}

function applyTeamTaskToolCall(sessionId: string, toolCall: ToolCall) {
  if (toolCall.name === 'create_task') {
    upsertTaskRecords(sessionId, Array.isArray(toolCall.arguments.tasks) ? toolCall.arguments.tasks : toolCall.arguments);
    return;
  }
  if (toolCall.name === 'update_task') {
    const taskId = pickString(toolCall.arguments.task_id, toolCall.arguments.id);
    const existingStatus = taskId
      ? useSessionStore.getState().getRuntime(sessionId)?.teamTasks.find((task) => task.task_id === taskId)?.status
      : undefined;
    upsertTaskRecords(sessionId, toolCall.arguments, existingStatus || 'pending');
    return;
  }
  if (toolCall.name === 'claim_task') {
    return;
  }
}

interface UseWebSocketOptions {
  activeSessionId?: string;
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectDir?: string;
  onConnect?: (payload: ConnectionAckPayload) => void;
  onDisconnect?: () => void;
  onError?: (error: string) => void;
  onConfigChanged?: (updatedKeys?: string[]) => void;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  connectionState: WebConnectionState;
  request: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: WebRequestOptions
  ) => Promise<T>;
  persistMedia: (content: string, sessionId: string, mediaItems: MediaItem[]) => Promise<PersistMediaResponse>;
  sendMessage: (content: string, sessionId: string, mediaItems?: MediaItem[]) => Promise<boolean>;
  sendStructuredChatContent: (content: unknown, sessionId: string) => Promise<void>;
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
  respondActivate: (
    sessionId: string,
    interactionId: string,
    action: 'accept' | 'reject',
    feedback?: string
  ) => Promise<void>;
  setGoalObjective: (sessionId: string, objective: string) => Promise<void>;
  pauseGoal: (sessionId: string) => Promise<void>;
  resumeGoal: (sessionId: string) => Promise<void>;
  clearGoal: (sessionId: string) => Promise<void>;
  refreshGoal: (sessionId: string) => Promise<void>;
  drainTaskQueueIfIdle: (sessionId: string) => void;
  getInflightCount: () => number;
}

interface PersistMediaResponse {
  content?: string;
  query?: string;
  media_items?: Record<string, unknown>[];
  files?: Record<string, unknown>;
}

function isPersistedMediaItem(item: MediaItem): boolean {
  return typeof item.path === 'string' && item.path.trim().length > 0;
}

function getMediaMimeType(item: MediaItem): string {
  return item.mime_type || item.mimeType;
}

function toPersistedMediaRecord(item: MediaItem): Record<string, unknown> {
  return {
    type: item.type,
    filename: item.filename,
    mime_type: getMediaMimeType(item),
    path: item.path,
    size_bytes: item.size_bytes ?? item.sizeBytes,
  };
}

function buildPersistedMediaFiles(mediaItems: MediaItem[]): Record<string, unknown> {
  return {
    uploaded_images: mediaItems.map((item) => ({
      filename: item.filename,
      path: item.path,
      mime_type: getMediaMimeType(item),
      size_bytes: item.size_bytes ?? item.sizeBytes,
    })),
  };
}

function getSessionWorkContext(sessionId: string): Record<string, unknown> {
  const sessionStore = useSessionStore.getState();
  const workspaceStore = useWorkspaceStore.getState();
  const session =
    sessionStore.currentSession?.session_id === sessionId
      ? sessionStore.currentSession
      : sessionStore.sessions.find((item) => item.session_id === sessionId);
  const selectedProject = workspaceStore.selectedProject;
  const projectId = session?.project_id || selectedProject?.project_id || '';
  const projectDir = session?.project_dir || selectedProject?.project_dir || '';
  const workMode = session?.work_mode || selectedProject?.work_mode || workspaceStore.workMode;
  return {
    ...(projectId ? { project_id: projectId } : {}),
    ...(projectDir ? { project_dir: projectDir } : {}),
    ...(workMode ? { work_mode: workMode } : {}),
  };
}

interface ContextCompressionStatePayload extends Record<string, unknown> {
  status?: string;
  summary?: string;
  operation_id?: string;
  phase?: string;
  processor?: string;
}

interface PendingContextCompressionStart {
  timer: ReturnType<typeof setTimeout>;
  runtimeState: Omit<ContextCompressionRuntime, 'status'>;
  shown: boolean;
}

function normalizeAgentMode(rawMode: unknown): AgentMode {
  if (typeof rawMode !== 'string') return 'agent';
  const normalized = rawMode.trim().toLowerCase();
  if (normalized === 'team') return 'team';
  if (normalized === 'auto_harness') return 'auto_harness';
  return 'agent';
}

function unsupportedEvolutionModeMessage(content: string, mode: AgentMode): string | null {
  const trimmed = content.trim();
  const isEvolutionCommand =
    trimmed === '/evolve' ||
    trimmed.startsWith('/evolve ') ||
    trimmed === '/evolve_simplify' ||
    trimmed.startsWith('/evolve_simplify ');
  if (!isEvolutionCommand || mode === 'agent' || mode === 'team') {
    return null;
  }
  return `${mode} 模式下演进功能不可用。`;
}

const EVENT_DEDUP_WINDOW_MS = 1500;
const CONTEXT_COMPRESSION_START_DELAY_MS = 300;

function normalizeEventTimestampIso(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    const millis = value > 1_000_000_000_000 ? value : value * 1000;
    const date = new Date(millis);
    if (!Number.isNaN(date.getTime())) {
      return date.toISOString();
    }
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }
  return new Date().toISOString();
}

function isTeamTeammateMessagePayload(payload: Record<string, unknown>): boolean {
  return typeof payload.role === 'string' && payload.role.trim().toLowerCase() === 'teammate';
}

function isHiddenTeamTeammateMessagePayload(mode: AgentMode, payload: Record<string, unknown>): boolean {
  return mode === 'team' && isTeamTeammateMessagePayload(payload);
}

function getTeamPayloadMemberName(payload: Record<string, unknown>): string | undefined {
  return pickString(payload.member_name, payload.member_id, payload.source_member);
}

function eventTimestampMs(payload: Record<string, unknown>): number {
  const value = payload.timestamp;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1_000_000_000_000 ? value : value * 1000;
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return Date.now();
}

function stableEventId(...parts: unknown[]): string {
  return parts
    .map((part) => String(part ?? '').trim())
    .filter(Boolean)
    .join(':')
    .replace(/[^a-zA-Z0-9:_-]+/g, '-')
    .slice(0, 180);
}

function getAgentRefId(payload: Record<string, unknown>): string | undefined {
  const direct = payload.agent_ref;
  if (isRecord(direct)) {
    const id = pickString(direct.id);
    if (id) {
      return id;
    }
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = nestedPayload.agent_ref;
    if (isRecord(nested)) {
      return pickString(nested.id);
    }
  }
  return undefined;
}

function upsertHumanShareCommandFromEvent(
  payload: Record<string, unknown>,
  event: { member_id?: string; name?: string; mode?: string; timestamp?: number }
): void {
  if (event.mode !== 'human' || !event.member_id) {
    return;
  }
  const sessionId = getPayloadSessionId(payload);
  if (!sessionId) {
    return;
  }
  const teamName = getAgentRefId(payload) || 'unknown';
  const sessionRef = `team_${teamName}_session_${sessionId}`;
  useSessionStore.getState().upsertTeamHumanShareCommand(
    sessionId,
    {
      memberName: event.member_id,
      displayName: event.name,
      sessionId,
      teamName,
      sessionRef,
      joinCommand: `/join ${sessionRef} as ${event.member_id}`,
      exitCommand: `/exit ${sessionRef}`,
      status: 'pending',
      updatedAt: event.timestamp || Date.now(),
    },
  );
}

function stringifyCompact(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value ?? '');
  }
}

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
  const { t } = useTranslation();
  const {
    provider,
    apiKey,
    apiBase,
    model,
    projectDir,
    onConnect,
    onDisconnect,
    onError,
    onConfigChanged,
  } = options;

  // 同步更新 ref，避免竞态条件
  // 必须在渲染阶段同步更新，否则 effect 执行之前收到的事件会被错误过滤
  const userInputVersionRef = useRef(0);
  const activeRequestIdRef = useRef<string | undefined>(undefined);
  // 立即同步更新，不等待 effect

  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] =
    useState<WebConnectionState>('idle');
  const lastConnectSignatureRef = useRef<string>('');
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const onConfigChangedRef = useRef(onConfigChanged);
  const sendMessageRef = useRef<typeof sendMessage>();
  // 标记本地 sendMessage 刚发起但后端尚未确认 processing_status=true 的 session。
  // 用于区分"旧任务被打断的 false"和"任务正常结束的 false"——前者应跳过自动排空，
  // 因为新任务即将由后端启动（会紧跟一条 processing_status=true）。
  const localSendPendingRef = useRef<Set<string>>(new Set());
  const recentEventRef = useRef<Map<string, number>>(new Map());
  const teamToolCallMemberRef = useRef<Map<string, string>>(new Map());
  const shutdownMemberToolCallRef = useRef<Map<string, string>>(new Map());
  const clearedTeamPanelSessionRef = useRef<Set<string>>(new Set());
  const teamMemberOutputEventRef = useRef<Map<string, string>>(new Map());
  const eventDedupDroppedRef = useRef<Record<string, number>>({});
  const symphonyStatusTargetRef = useRef<Map<string, { messageId: string; baseContent: string }>>(
    new Map()
  );
  // goal_id -> 本地"完成后自动隐藏"定时器句柄，见 applyIncomingGoal
  const goalCompletedHideTimerRef = useRef<Map<string, ReturnType<typeof window.setTimeout>>>(new Map());
  /** session_id -> 最近一次成功落地 goal.snapshot/goal.updated 的时间戳，供 1 分钟无更新兜底巡检用 */
  const lastGoalEventAtRef = useRef<Map<string, number>>(new Map());
  /** session_id -> 最近一次调用 performGoalGet 的时间戳（不管成败），供 unknown 态退避巡检用 */
  const lastGoalGetAttemptAtRef = useRef<Map<string, number>>(new Map());
  const wasConnectedRef = useRef(false);
  const contextCompressionSummaryRef = useRef<Map<string, ContextCompressionSummary>>(new Map());
  const pendingContextCompressionStartRef =
    useRef<Map<string, PendingContextCompressionStart>>(new Map());
  const pendingTeamMemberContextCompressionStartRef =
    useRef<Map<string, PendingContextCompressionStart>>(new Map());
  const heldContextUsageSessionsRef = useRef<Set<string>>(new Set());
  const pendingContextUsageRef = useRef<Map<string, {
    rate: number;
    beforeCompressed: number | null;
    afterCompressed: number | null;
  }>>(new Map());

  // Stores: 仅保留全局 action（A 类，不需要 sessionId）
  const {
    setConnected,
    setAvailableTools,
    setConnectionStats,
    updateSession,
    setContextCompressionStats,
    setTeamMemberContextCompressionStatus,
    clearTeamMemberContextCompressionStatus,
    clearAllTeamMemberContextCompressionStatus,
  } = useSessionStore();

  const resolveEventSessionId = useCallback(
    (payload: Record<string, unknown>): string | null => {
      const payloadSessionId = getPayloadSessionId(payload);
      if (!payloadSessionId) return null;
      ensureSessionRuntimes(payloadSessionId);
      return payloadSessionId;
    },
    []
  );

  const handleTtsPlayback = useCallback(
    (sessionId: string, messageId: string, content: string) => {
      const sanitized = sanitizeTtsText(content);
      if (!sanitized || sanitized.startsWith('[任务已中断]')) {
        return;
      }

      const existing = useChatStore.getState().getRuntime(sessionId)?.messages.find((msg) => msg.id === messageId);
      if (existing?.audioBase64) {
        return;
      }

      void (async () => {
        const versionAtStart = userInputVersionRef.current;
        const ttsSessionId = sessionId;
        const response = await fetchTtsAudio(
          sanitized,
          ttsSessionId && ttsSessionId !== 'new' ? ttsSessionId : undefined
        );
        if (!response?.success || !response.audio_base64) {
          return;
        }

        useChatStore.getState().updateMessage(sessionId, messageId, {
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
    []
  );

  const handleConnectionAck = useCallback(
    (payload: Record<string, unknown>) => {
      const ackPayload = payload as unknown as ConnectionAckPayload;
      setConnected(true);
      if (Array.isArray(ackPayload.tools)) {
        setAvailableTools(ackPayload.tools);
      }
      useChatStore.getState().setGlobalTaskRunning(Boolean(ackPayload.task_running));
      onConnectRef.current?.(ackPayload);
    },
    [setAvailableTools, setConnected]
  );

  // 断开连接
  const disconnect = useCallback(() => {
    webClient.disconnect();
  }, []);

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

  const findActiveTeamLeaderMessage = useCallback((sessionId: string) => {
    const messages = useChatStore.getState().getRuntime(sessionId)?.messages ?? [];
    return findActiveTeamLeaderMessageInTurn(messages);
  }, []);

  const closeActiveTeamLeaderMessages = useCallback((sessionId: string) => {
    const messages = useChatStore.getState().getRuntime(sessionId)?.messages ?? [];
    for (const msg of messages) {
      if (msg.id.startsWith('team-leader-') && msg.isStreaming) {
        useChatStore.getState().updateMessage(sessionId, msg.id, { isStreaming: false });
      }
    }
  }, []);

  const clearPendingContextCompressionStart = useCallback((sessionId: string) => {
    const pending = pendingContextCompressionStartRef.current.get(sessionId);
    if (pending) {
      clearTimeout(pending.timer);
      pendingContextCompressionStartRef.current.delete(sessionId);
    }
  }, []);

  const getTeamMemberContextCompressionKey = useCallback(
    (sessionId: string, memberId: string) => `${sessionId}\u0000${memberId}`,
    []
  );

  const clearPendingTeamMemberContextCompressionStart = useCallback((sessionId: string, memberId: string) => {
    const key = getTeamMemberContextCompressionKey(sessionId, memberId);
    const pending = pendingTeamMemberContextCompressionStartRef.current.get(key);
    if (!pending) return;
    clearTimeout(pending.timer);
    pendingTeamMemberContextCompressionStartRef.current.delete(key);
  }, [getTeamMemberContextCompressionKey]);

  const clearAllPendingTeamMemberContextCompressionStarts = useCallback(() => {
    for (const pending of pendingTeamMemberContextCompressionStartRef.current.values()) {
      clearTimeout(pending.timer);
    }
    pendingTeamMemberContextCompressionStartRef.current.clear();
  }, []);

  const resetContextCompressionTurn = useCallback((sessionId: string) => {
    clearPendingContextCompressionStart(sessionId);
    contextCompressionSummaryRef.current.delete(sessionId);
    useChatStore.getState().setContextCompressionStatus(sessionId, undefined);
  }, [clearPendingContextCompressionStart]);

  const finishContextCompressionTurn = useCallback((sessionId: string) => {
    clearPendingContextCompressionStart(sessionId);
    const summary = contextCompressionSummaryRef.current.get(sessionId);
    useChatStore.getState().setContextCompressionStatus(sessionId, undefined, summary && summary.count > 0 ? summary : undefined);
  }, [clearPendingContextCompressionStart]);

  const buildContextCompressionRuntimeState = useCallback(
    (payload: ContextCompressionStatePayload): Omit<ContextCompressionRuntime, 'status'> | null => {
      const summary = payload.summary?.trim() || '';
      if (!summary) return null;
      return {
        summary,
        operationId: payload.operation_id?.trim() || '',
        phase: payload.phase?.trim() || undefined,
        processor: payload.processor?.trim() || undefined,
      };
    },
    []
  );

  const handleContextCompressionState = useCallback(
    (sessionId: string, payload: ContextCompressionStatePayload) => {
      const status = payload.status?.trim().toLowerCase() || '';
      const runtimeState = buildContextCompressionRuntimeState(payload);
      if (!status || !runtimeState) return;

      if (status === 'completed') {
        clearPendingContextCompressionStart(sessionId);
        const current = contextCompressionSummaryRef.current.get(sessionId) ?? { count: 0, summaries: [] };
        const nextSummary = {
          count: current.count + 1,
          summaries: [...current.summaries, runtimeState.summary],
        };
        contextCompressionSummaryRef.current.set(sessionId, nextSummary);
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'completed',
        });
        return;
      }

      if (status === 'started' || status === 'running') {
        clearPendingContextCompressionStart(sessionId);
        const pending: PendingContextCompressionStart = {
          runtimeState,
          shown: false,
          timer: setTimeout(() => {
            const current = pendingContextCompressionStartRef.current.get(sessionId);
            if (current !== pending) return;
            pending.shown = true;
            useChatStore.getState().setContextCompressionStatus(sessionId, {
              ...pending.runtimeState,
              status: 'running',
            });
          }, CONTEXT_COMPRESSION_START_DELAY_MS),
        };
        pendingContextCompressionStartRef.current.set(sessionId, pending);
        return;
      }

      if (status === 'noop' || status === 'skipped') {
        const pending = pendingContextCompressionStartRef.current.get(sessionId);
        if (pending && !pending.shown) {
          clearPendingContextCompressionStart(sessionId);
          return;
        }
        if (pending) {
          clearPendingContextCompressionStart(sessionId);
        }
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'unchanged',
        });
        return;
      }

      if (status === 'failed' || status === 'error') {
        clearPendingContextCompressionStart(sessionId);
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'failed',
        });
      }
    },
    [buildContextCompressionRuntimeState, clearPendingContextCompressionStart]
  );

  const findExistingTeamMemberId = useCallback((sessionId: string, memberName: unknown): string | null => {
    if (typeof memberName !== 'string' || !memberName.trim()) {
      return null;
    }
    const candidate = memberName.trim();
    const existingMember = useSessionStore
      .getState()
      .getRuntime(sessionId)
      ?.teamMembers.find((member) => member.member_id === candidate);
    return existingMember?.member_id || null;
  }, []);

  const handleTeamMemberContextCompressionState = useCallback(
    (sessionId: string, payload: ContextCompressionStatePayload, memberId: string) => {
      const status = payload.status?.trim().toLowerCase() || '';
      const runtimeState = buildContextCompressionRuntimeState(payload);
      if (!status || !runtimeState) return;

      if (status === 'completed') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        const current =
          useSessionStore.getState().getRuntime(sessionId)?.teamMemberContextCompression[memberId]?.summary;
        const nextSummary = {
          count: (current?.count || 0) + 1,
          summaries: [...(current?.summaries || []), runtimeState.summary],
        };
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'completed',
        }, nextSummary);
        return;
      }

      if (status === 'started' || status === 'running') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        const key = getTeamMemberContextCompressionKey(sessionId, memberId);
        const pending: PendingContextCompressionStart = {
          runtimeState,
          shown: false,
          timer: setTimeout(() => {
            if (pendingTeamMemberContextCompressionStartRef.current.get(key) !== pending) return;
            pending.shown = true;
            setTeamMemberContextCompressionStatus(sessionId, memberId, {
              ...pending.runtimeState,
              status: 'running',
            });
          }, CONTEXT_COMPRESSION_START_DELAY_MS),
        };
        pendingTeamMemberContextCompressionStartRef.current.set(key, pending);
        return;
      }

      if (status === 'noop' || status === 'skipped') {
        const key = getTeamMemberContextCompressionKey(sessionId, memberId);
        const pending = pendingTeamMemberContextCompressionStartRef.current.get(key);
        if (pending && !pending.shown) {
          clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
          return;
        }
        if (pending) {
          clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        }
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'unchanged',
        });
        return;
      }

      if (status === 'failed' || status === 'error') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'failed',
        });
      }
    },
    [
      buildContextCompressionRuntimeState,
      clearPendingTeamMemberContextCompressionStart,
      getTeamMemberContextCompressionKey,
      setTeamMemberContextCompressionStatus,
    ]
  );

  useEffect(() => {
    return () => {
      pendingContextCompressionStartRef.current.forEach((pending) => {
        clearTimeout(pending.timer);
      });
      pendingContextCompressionStartRef.current.clear();
      clearAllPendingTeamMemberContextCompressionStarts();
    };
  }, [clearAllPendingTeamMemberContextCompressionStarts]);

  const persistMedia = useCallback(
    async (content: string, sessionId: string, mediaItems: MediaItem[]) => {
      return request<PersistMediaResponse>('media.persist', {
        session_id: sessionId,
        content,
        media_items: mediaItems as unknown as Record<string, unknown>[],
      });
    },
    [request],
  );

  // Goal（持续目标）控制：get/set/pause/resume/clear 共用一套 loading + 错误处理。
  // get/pause/clear 走非流式一次性响应，真的会有 res，用 requestGoalAction() 正常等待；
  // set/resume 走流式、正常路径上永远不会有 res（见 backend-requests.md #4），改用
  // sendGoalStreamCommand() 发出去就不等，真实状态全靠 goal.snapshot/goal.updated 事件驱动。
  const goalAction = useCallback(
    async (sessionId: string, action: GoalAction | 'get', objective?: string) => {
      ensureSessionRuntimes(sessionId);
      useGoalStore.getState().setPendingAction(sessionId, action === 'get' ? null : action);
      const mode = useSessionStore.getState().getRuntime(sessionId)?.mode ?? 'agent';

      const reportFailure = (error: unknown) => {
        const webError = error as WebError;
        const errorMsg = webError.message || t('network.sendMessageFailed');
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      };

      if (action === 'get') {
        // 查询本身的失败/重试/unknown 兜底统一交给 performGoalGet，这里不需要额外 try/catch。
        await performGoalGet(sessionId, mode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
        return;
      }

      if (action === 'set' || action === 'resume') {
        if (action === 'set' && objective) {
          // "设为目标"徽章要靠这份历史记录复原（见 goalStore.ts objectiveMessageTexts 的
          // 注释），发送这一刻就是唯一能拿到 objective 原文的地方，不能等回包再记。
          useGoalStore.getState().recordGoalObjectiveText(sessionId, objective);
        }
        try {
          await sendGoalStreamCommand({ sessionId, action, objective, mode });
        } catch (error) {
          // WS 层直接发送失败（未连接等）：这是能明确识别的失败，弹提示；set 不做进一步兜底
          // （"没有创建"本来就成立，不需要额外收敛），resume 按 b/c 步骤的约定补一次 get 兜底。
          useGoalStore.getState().setPendingAction(sessionId, null);
          reportFailure(error);
          if (action === 'resume') {
            await performGoalGet(sessionId, mode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
          }
          return;
        }
        // 没有数据可落——pendingAction 会在 goal.snapshot/goal.updated/execution.error 事件到达时
        // 清掉（applyGoalSnapshot -> applyIncomingGoal）。协议文档确认这条流式失败事件的 payload
        // 也一定带 goal 字段（哪怕是 null），所以 resume 的业务层失败不需要额外兜底 get。
        if (action === 'set') {
          // set 是 fire-and-forget，正常路径下应该很快会收到 goal.snapshot/execution.error
          // 把 pendingAction 清掉。这里延迟 GOAL_SET_CONVERGENCE_DELAY_MS 后补一次兜底 get，
          // 只覆盖"事件真的丢了"这类小概率情况（尤其是这个 session 第一次设置目标时，1 分钟
          // 无更新兜底轮询要求 store 里已有非空 goal 才会巡检，覆盖不到这个场景）——不在发送后
          // 立刻发，是因为立刻发会跟真正的 snapshot 赛跑，赢了反而用 set 落地前的旧数据提前
          // 清掉 pendingAction，重新打开"按钮提前解禁、能打出冲突指令"的窗口，等于没解决问题。
          window.setTimeout(() => {
            // 到点一看 pendingAction 已经不是 'set' 了，说明真正的事件已经收敛过一次，不需要
            // 再补——不管是被这次 set 自己的事件清的，还是用户切走后又发起了别的操作。
            if (useGoalStore.getState().runtimes[sessionId]?.pendingAction !== 'set') return;
            void requestGoalAction({ sessionId, action: 'get', mode })
              .then((goal) => applyIncomingGoal(sessionId, goal, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current))
              .catch(() => {
                // 静默失败：这只是收敛用的兜底 get，真正的状态最终仍由 goal.updated 事件驱动。
              });
          }, GOAL_SET_CONVERGENCE_DELAY_MS);
        }
        return;
      }

      if (action === 'clear') {
        try {
          const goal = await requestGoalAction({ sessionId, action, mode });
          applyIncomingGoal(sessionId, goal, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current);
          // active 目标被删除时的会话结束由 App.tsx handleClearGoal 显式补发 cancel/pause 负责
          // （真机联调确认只重置前端本地态不够，得发真信号），这里不用再管会话态。
        } catch (error) {
          // 一元 clear 失败时 webClient 目前不会把 payload.goal 透传进 WebError（见 webClient.ts
          // resolvePending），拿不到失败当时的目标快照，主动补一次 get 收敛。
          reportFailure(error);
          await performGoalGet(sessionId, mode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
        }
        return;
      }

      // action === 'pause'：同样是一元 RPC，失败兜底同 clear。
      try {
        const goal = await requestGoalAction({ sessionId, action, mode });
        applyIncomingGoal(sessionId, goal, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current);
      } catch (error) {
        const webError = error as WebError;
        // code: invalid_state 说明目标这会儿已经不是 active 了（常见于跟 handleCancel 触发的
        // 后端"中断顺带暂停"竞态：两边几乎同时发，谁先到不确定，晚到的这次 pause 打过去时
        // 目标已经被另一条路径转过去了）——这只是提示"已经不需要再暂停"，不是真错误，不弹
        // 系统消息；其余失败（如 no_goal、网络异常）照常提示。
        if (webError.code !== 'invalid_state') {
          reportFailure(error);
        }
        await performGoalGet(sessionId, mode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
      }
    },
    [t]
  );

  const setGoalObjective = useCallback(
    (sessionId: string, objective: string) => goalAction(sessionId, 'set', objective),
    [goalAction]
  );
  const pauseGoal = useCallback((sessionId: string) => goalAction(sessionId, 'pause'), [goalAction]);
  const resumeGoal = useCallback((sessionId: string) => goalAction(sessionId, 'resume'), [goalAction]);
  const clearGoal = useCallback((sessionId: string) => goalAction(sessionId, 'clear'), [goalAction]);
  /**
   * 会话加载/切换时主动查一次当前 Goal 状态（协议文档 v2 §11 推荐流程第 3 步）——不这样做的话，
   * 刷新页面后 GoalBar 要等下一次 goal.updated 推送才会"自愈"重新出现，目标空闲/paused 时甚至
   * 会一直缺失（2026-07-21 真机联调发现，见 backend-requests.md #1 末尾）。
   */
  const refreshGoal = useCallback((sessionId: string) => goalAction(sessionId, 'get'), [goalAction]);

  // 发送聊天消息
  const sendMessage = useCallback(
    async (content: string, sessionId: string, mediaItems: MediaItem[] = []): Promise<boolean> => {
      const hasMedia = mediaItems.length > 0;
      if (!content.trim() && !hasMedia) return false;

      const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
      const unsupportedEvolutionMode = unsupportedEvolutionModeMessage(content, currentMode ?? 'agent');
      if (unsupportedEvolutionMode) {
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: unsupportedEvolutionMode,
          timestamp: new Date().toISOString(),
        });
        return false;
      }

      const isInitialUserMessage = !useChatStore
        .getState()
        .getRuntime(sessionId)
        ?.messages.some((message) => message.role === 'user');
      if (isInitialUserMessage) {
        heldContextUsageSessionsRef.current.add(sessionId);
        pendingContextUsageRef.current.delete(sessionId);
        setContextCompressionStats(sessionId, {
          rate: 0,
          beforeCompressed: 0,
          afterCompressed: 0,
        });
      }

      resetContextCompressionTurn(sessionId);
      userInputVersionRef.current += 1;
      stopAllTts();

      // A new query supersedes an unanswered inline question for this same session.
      if (useChatStore.getState().getRuntime(sessionId)?.pendingQuestion) {
        useChatStore.getState().setPendingQuestion(sessionId, null);
      }

      // 添加用户消息（附带输入栏选中的技能）
      const selectedSkills = useSessionStore.getState().getRuntime(sessionId)?.selectedSkills ?? [];
      useChatStore.getState().addMessage(sessionId, {
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        mediaItems,
        timestamp: new Date().toISOString(),
        ...(selectedSkills.length > 0 ? { skills: selectedSkills } : {}),
      });
      // 发送后清空输入栏已选技能
      if (selectedSkills.length > 0) {
        useSessionStore.getState().clearSelectedSkills(sessionId);
      }

      // 不再预先创建助手消息，而是在收到第一个 content_chunk 时创建
      // 这样工具调用会先显示，然后才是助手的回复

      useChatStore.getState().setProcessing(sessionId, true);
      useChatStore.getState().setThinking(sessionId, true);
      // 标记本地发起的发送，用于 processing_status 处理器区分"旧任务被打断"
      // 和"任务正常结束"——前者跳过自动排空
      localSendPendingRef.current.add(sessionId);

      // 正常调用接口
      const selectedModel = useSessionStore.getState().getEffectiveModelName(sessionId);
      const workContext = getSessionWorkContext(sessionId);
      if (currentMode === 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }
      if (currentMode === 'team') {
        if (clearedTeamPanelSessionRef.current.has(sessionId)) {
          clearedTeamPanelSessionRef.current.delete(sessionId);
        }
        useChatStore.getState().setPaused(sessionId, false);
      }
      try {
        let outgoingContent = content.replace(/\{\{skill:([^}]+)\}\}/g, '$1');
        let outgoingMediaItems: Record<string, unknown>[] | undefined;
        let outgoingFiles: Record<string, unknown> | undefined;
        if (hasMedia) {
          if (mediaItems.every(isPersistedMediaItem)) {
            outgoingMediaItems = mediaItems.map(toPersistedMediaRecord);
            outgoingFiles = buildPersistedMediaFiles(mediaItems);
          } else {
            const persisted = await persistMedia(content, sessionId, mediaItems);
            outgoingContent = persisted.content ?? persisted.query ?? content;
            outgoingMediaItems = persisted.media_items;
            outgoingFiles = persisted.files;
          }
        }
        // Goal 处于 active 时，普通输入按文档 §5.1 作为补充约束插入当前 Goal，而不是覆盖它
        const activeGoal = useGoalStore.getState().getRuntime(sessionId)?.goal;
        const inputMode = activeGoal?.status === 'active' ? 'steer' : undefined;
        await request('chat.send', {
          session_id: sessionId,
          content: outgoingContent,
          ...(outgoingMediaItems ? { media_items: outgoingMediaItems } : {}),
          ...(outgoingFiles ? { files: outgoingFiles } : {}),
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
          ...workContext,
          skills: selectedSkills,
          ...(inputMode ? { input_mode: inputMode } : {}),
        });
        return true;
      } catch (error) {
        const webError = error as WebError;
        localSendPendingRef.current.delete(sessionId);
        setConnectionStats({ lastError: webError.message });
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg = webError.message || t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
        return false;
      }
    },
    [
      persistMedia,
      request,
      resetContextCompressionTurn,
      setContextCompressionStats,
      setConnectionStats,
      t,
    ]
  );

  const sendStructuredChatContent = useCallback(
    async (content: unknown, sessionId: string) => {
      resetContextCompressionTurn(sessionId);
      userInputVersionRef.current += 1;
      stopAllTts();

      useChatStore.getState().setProcessing(sessionId, true);
      useChatStore.getState().setThinking(sessionId, true);

      const currentSessionState = useSessionStore.getState();
      const workContext = getSessionWorkContext(sessionId);
      const currentMode = currentSessionState.getRuntime(sessionId)?.mode;
      const selectedModel = currentSessionState.getEffectiveModelName(sessionId);
      if (currentMode === 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }
      if (currentMode === 'team') {
        useChatStore.getState().setPaused(sessionId, false);
      }
      try {
        await request('chat.send', {
          session_id: sessionId,
          content,
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
          ...workContext,
        });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg = webError.message || t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }
    },
    [request, resetContextCompressionTurn, setConnectionStats, t]
  );

  // 存储sendMessage函数到ref
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  /**
   * 队列非空时主动尝试排空一次，供"入队那一刻本来就没有任务在处理"的场景兜底
   * （典型是目标 active 但当前无聊天在跑时用户发消息——这条消息按设计要走排队，见
   * InputArea.tsx 里 isGoalActive 相关注释，但常规的两处自动排空触发点——
   * chat.processing_status 从 true→false、interrupt_result 完成——都要求"之前在
   * processing"，这种场景两个都不会触发，消息会永久卡在队列里，只能靠用户手动点
   * "恢复队列"）。isProcessing 为真时直接跳过，交给已有的 processing_status 处理器
   * 在真正空闲下来时接管，不会重复发送。
   */
  const drainTaskQueueIfIdle = useCallback((sessionId: string) => {
    const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
    if (currentMode !== 'agent') return;
    const runtime = useChatStore.getState().getRuntime(sessionId);
    if (runtime?.isProcessing || runtime?.queuePaused) return;
    const nextTask = runtime?.taskQueue[0];
    if (nextTask && sendMessageRef.current) {
      useChatStore.getState().removeFromTaskQueue(sessionId, nextTask.id);
      sendMessageRef.current(nextTask.content, sessionId);
    }
  }, []);

  // 统一中断接口 - pause/cancel/supplement/resume
  const interrupt = useCallback(
    async (
      sessionId: string,
      intent: InterruptIntent,
      options?: { newInput?: string }
    ) => {
      const newInput = options?.newInput;
      if (intent === 'supplement' && newInput) {
        resetContextCompressionTurn(sessionId);
        userInputVersionRef.current += 1;
        stopAllTts();
        if (useSessionStore.getState().getRuntime(sessionId)?.mode === 'team') {
          closeActiveTeamLeaderMessages(sessionId);
        }
        useChatStore.getState().addMessage(sessionId, {
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
          ...getSessionWorkContext(sessionId),
        };
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (['pause', 'resume', 'cancel', 'supplement'].includes(intent)) {
          params.mode = currentMode;
          if (currentMode === 'team') {
            params.team = true;
          }
        }
        if (intent === 'supplement') {
          params.new_input = newInput ?? '';
          const selectedModel = useSessionStore.getState().getEffectiveModelName(sessionId);
          if (selectedModel) params.model_name = selectedModel;
        }
        await request('chat.interrupt', params);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.interruptFailed'));
      }
    },
    [
      closeActiveTeamLeaderMessages,
      request,
      resetContextCompressionTurn,
      setConnectionStats,
      t,
    ]
  );

  // 暂停 - 显式暂停当前任务
  const pause = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'pause');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.pauseFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  const cancel = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'cancel');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.cancelFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  const supplement = useCallback(
    async (sessionId: string, newInput: string) => {
      try {
        await interrupt(sessionId, 'supplement', { newInput });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.supplementFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  // 恢复 - 恢复暂停的任务
  const resume = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'resume');
        useChatStore.getState().setPaused(sessionId, false);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.resumeFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  // 切换模式
  const switchMode = useCallback(
    async (sessionId: string, mode: AgentMode) => {
      // 标记正在切换模式
      useChatStore.getState().setSwitchingMode(sessionId, true);

      const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
      // Reset harnessStore when leaving auto_harness mode
      if (currentMode === 'auto_harness' && mode !== 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }

      // 只有在有任务执行时才调用 interrupt
      if (sessionId && sessionId !== 'new') {
        const runtime = useChatStore.getState().getRuntime(sessionId);
        if (runtime?.isProcessing || runtime?.isPaused) {
          try {
            await interrupt(sessionId, 'cancel');
          } catch {
            // 忽略中断错误
          }
        }
      }

      useSessionStore.getState().setMode(sessionId, mode);
      if (sessionId && sessionId !== 'new') {
        updateSession(sessionId, { mode });
      }
      // 延迟重置标志
      setTimeout(() => {
        useChatStore.getState().setSwitchingMode(sessionId, false);
      }, 300);
    },
    [updateSession, interrupt]
  );

  // 发送用户回答
  const sendUserAnswer = useCallback(
    async (sessionId: string, requestId: string, answers: UserAnswer[], source?: string) => {
      try {
        const pendingQuestion = useChatStore.getState().getRuntime(sessionId)?.pendingQuestion;
        const pendingMatches = pendingQuestion?.request_id === requestId;
        const effectiveSource = source ?? (pendingMatches ? pendingQuestion?.source : undefined);
        const approvalSchema =
          pendingMatches
            ? pendingQuestion?.approvalSchema
            : undefined;
        const evolutionMeta =
          pendingMatches
            ? pendingQuestion.evolutionMeta
            : undefined;
        const evolutionMetaPayload =
          evolutionMeta && typeof evolutionMeta === 'object'
            ? { evolution_meta: evolutionMeta }
            : {};
        const approvalSchemaPayload = approvalSchema ? { approval_schema: approvalSchema } : {};
        const sourcePayload = effectiveSource ? { source: effectiveSource } : {};
        const structuredPlanPayload =
          pendingMatches && pendingQuestion?.planApprovalKind === 'plan_approval'
            ? {
                plan_approval_kind: pendingQuestion.planApprovalKind,
                plan_content: pendingQuestion.planContent ?? '',
                plan_language: pendingQuestion.planLanguage ?? 'cn',
              }
            : {};
        const approvalTransport =
          evolutionMeta && typeof evolutionMeta.approval_transport === 'string'
            ? evolutionMeta.approval_transport
            : undefined;
        // 如果是需要走 interrupt/interact 的确认，发送 chat.send
        if (
          effectiveSource === 'permission_interrupt' ||
          effectiveSource === 'confirm_interrupt' ||
          effectiveSource === 'ask_user_interrupt' ||
          effectiveSource === 'evolution_interrupt' ||
          (effectiveSource === 'skill_evolution_approval' && approvalTransport === 'interrupt')
        ) {
          const resolvedResumeMode = resolveInterruptResumeMode(sessionId);
          await request('chat.send', {
            session_id: sessionId,
            query: '',
            mode: resolvedResumeMode,
            ...getSessionWorkContext(sessionId),
            request_id: requestId,
            answers: answers,
            ...sourcePayload,
            ...structuredPlanPayload,
            ...approvalSchemaPayload,
            ...evolutionMetaPayload,
          });
        } else if (effectiveSource === 'activate_confirm') {
          const action = answers[0]?.selected_options[0] === '拒绝' ? 'reject' : 'accept';
          const interactionId = requestId || useHarnessStore.getState().getRuntime(sessionId)?.activateInteraction?.interactionId || '';
          if (!interactionId) {
            throw new Error('missing activate interaction id');
          }
          await request('chat.send', {
            session_id: sessionId,
            content: '',
            mode: 'auto_harness',
            ...getSessionWorkContext(sessionId),
            activate_response: {
              interaction_id: interactionId,
              action,
              feedback: '',
            },
          });
          useHarnessStore.getState().setActivateInteraction(sessionId, null);
        } else {
          // 否则发送 chat.user_answer（自进化确认）
          await request('chat.user_answer', {
            session_id: sessionId,
            ...getSessionWorkContext(sessionId),
            request_id: requestId,
            answers,
            ...sourcePayload,
            ...approvalSchemaPayload,
            ...evolutionMetaPayload,
          });
        }
        useChatStore.getState().setPendingQuestion(sessionId, null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.submitAnswerFailed'));
      }
    },
    [request, setConnectionStats, t]
  );

  const respondActivate = useCallback(
    async (sessionId: string, interactionId: string, action: 'accept' | 'reject', feedback?: string) => {
      try {
        await request('chat.send', {
          session_id: sessionId,
          content: '',
          mode: 'auto_harness',
          ...getSessionWorkContext(sessionId),
          activate_response: {
            interaction_id: interactionId,
            action,
            feedback: feedback || '',
          },
        });
        useHarnessStore.getState().setActivateInteraction(sessionId, null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
      }
    },
    [request, setConnectionStats]
  );

  const revealPendingContextUsage = useCallback((sessionId: string) => {
    heldContextUsageSessionsRef.current.delete(sessionId);
    const pending = pendingContextUsageRef.current.get(sessionId);
    pendingContextUsageRef.current.delete(sessionId);
    if (pending) {
      setContextCompressionStats(sessionId, pending);
    }
  }, [setContextCompressionStats]);

  // 会话切换时不再重置上下文压缩信息，保持本地存储的状态
  // useEffect(() => {
  //   setContextCompressionStats(null);
  // }, [activeSessionId, setContextCompressionStats]);

  useEffect(() => {
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onErrorRef.current = onError;
    onConfigChangedRef.current = onConfigChanged;
  }, [onConfigChanged, onConnect, onDisconnect, onError]);

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

  const clearThinkingForVisibleOutput = useCallback((sessionId: string) => {
    const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
    const isProcessingNow = useChatStore.getState().getRuntime(sessionId)?.isProcessing;
    if (currentMode === 'auto_harness' && isProcessingNow) {
      return;
    }
    useChatStore.getState().setThinking(sessionId, false);
  }, []);

  const shouldRecoverProcessingFromReasoning = useCallback((sessionId: string, payload: Record<string, unknown>): boolean => {
    const runtime = useChatStore.getState().getRuntime(sessionId);
    if (!runtime || runtime.isProcessing || runtime.isLoadingHistory) {
      return false;
    }
    if (runtime.currentStreamId) {
      return true;
    }
    if (webClient.getInflightCount() > 0) {
      return true;
    }
    const payloadRequestId = getPayloadRequestId(payload);
    return Boolean(
      payloadRequestId &&
      activeRequestIdRef.current &&
      payloadRequestId === activeRequestIdRef.current
    );
  }, []);

  const getTeamMemberOutputKey = useCallback(
    (payload: Record<string, unknown>, memberId: string): string => stableEventId(
      'member-output-key',
      getPayloadSessionId(payload),
      memberId,
      payload.rid,
      payload.request_id
    ),
    []
  );

  const getOrCreateTeamMemberOutputEventId = useCallback(
    (payload: Record<string, unknown>, memberId: string): string => {
      const key = getTeamMemberOutputKey(payload, memberId);
      const existing = teamMemberOutputEventRef.current.get(key);
      if (existing) {
        return existing;
      }
      const id = stableEventId(
        'member-output',
        getPayloadSessionId(payload),
        memberId,
        payload.rid,
        payload.request_id,
        Date.now()
      );
      teamMemberOutputEventRef.current.set(key, id);
      return id;
    },
    [getTeamMemberOutputKey]
  );

  const takeTeamMemberOutputEventId = useCallback(
    (payload: Record<string, unknown>, memberId: string): string | undefined => {
      const key = getTeamMemberOutputKey(payload, memberId);
      const id = teamMemberOutputEventRef.current.get(key);
      if (id) {
        teamMemberOutputEventRef.current.delete(key);
      }
      return id;
    },
    [getTeamMemberOutputKey]
  );

  const appendTeamMemberOutputDelta = useCallback(
    (sessionId: string, payload: Record<string, unknown>, memberId: string, content: string) => {
      if (!content) {
        return;
      }
      const id = getOrCreateTeamMemberOutputEventId(payload, memberId);
      const existingContent =
        useSessionStore.getState().getRuntime(sessionId)?.teamMemberExecutionEvents.find((event) => event.id === id)?.content || '';
      useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
        id,
        member_id: memberId,
        kind: 'final',
        timestamp: eventTimestampMs(payload),
        title: t('team.process.execution.final'),
        content: `${existingContent}${content}`,
      });
    },
    [getOrCreateTeamMemberOutputEventId, t]
  );

  useEffect(() => {
    const applyTeamMemberShutdown = (memberId: string, sessionId?: string) => {
      const normalizedMemberId = memberId.trim();
      if (!normalizedMemberId) {
        return;
      }
      if (!sessionId) {
        return;
      }
      const sessionStore = useSessionStore.getState();
      const runtime = sessionStore.getRuntime(sessionId);
      const currentMembers = runtime?.teamMembers ?? [];
      const nextMembers = currentMembers.filter(
        (member) => member.member_id !== normalizedMemberId
      );
      if (nextMembers.length === currentMembers.length) {
        return;
      }
      clearPendingTeamMemberContextCompressionStart(sessionId, normalizedMemberId);
      clearTeamMemberContextCompressionStatus(sessionId, normalizedMemberId);
      sessionStore.setTeamMembers(sessionId, nextMembers);
      if (nextMembers.length === 0) {
        clearedTeamPanelSessionRef.current.add(sessionId);
        useTodoStore.getState().clearTodos(sessionId);
        const currentSessionStore = useSessionStore.getState();
        currentSessionStore.setTeamMembers(sessionId, []);
        currentSessionStore.setTeamTaskEvents(sessionId, []);
        currentSessionStore.setTeamHumanShareCommands(sessionId, []);
        currentSessionStore.setTeamTasks(sessionId, []);
        currentSessionStore.setTeamMemberExecutionEvents(sessionId, []);
        clearAllTeamMemberContextCompressionStatus(sessionId);
        currentSessionStore.setTeamHistoryMessages(sessionId, []);
      }
    };

    const isTeamPanelClearedForPayload = (payload: Record<string, unknown>) => {
      const sessionId = getPayloadSessionId(payload) || undefined;
      return Boolean(sessionId && clearedTeamPanelSessionRef.current.has(sessionId));
    };

    /**
     * goal.snapshot / goal.updated / execution.error(带 goal 字段) 的统一落状态入口。
     * goal 为 null 且 payload 没有顶层 session_id 时（文档 §4.4 第三种返回路径），
     * 只能退化用当前 activeSessionId 兜底——这是文档示例本身没给出 session_id 时的已知限制。
     */
    const applyGoalSnapshot = (payload: Record<string, unknown>) => {
      const goal = (payload.goal ?? null) as GoalRecord | null;
      const sessionId =
        getPayloadSessionId(payload) ||
        goal?.session_id ||
        useChatStore.getState().activeSessionId ||
        undefined;
      if (!sessionId) return;
      ensureSessionRuntimes(sessionId);
      applyIncomingGoal(sessionId, goal, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current);
    };

    const unsubs = [
      webClient.on('connection.ack', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('hello', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('chat.delta', ({ payload }) => {
          const sessionId = resolveEventSessionId(payload);
          if (!sessionId) return;

        // 页面刷新后，如果收到活跃事件但 isProcessing=false，自动恢复执行状态
        if (!useChatStore.getState().getRuntime(sessionId)?.isProcessing && !useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setProcessing(sessionId, true);
        }

        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const content = typeof payload.content === 'string' ? payload.content : '';

        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            appendTeamMemberOutputDelta(sessionId, payload, memberId, content);
          }
          return;
        }
        if (content) {
          revealPendingContextUsage(sessionId);
        }
        if (currentMode === 'team' && content) {
          clearThinkingForVisibleOutput(sessionId);
          const existingMsg = findActiveTeamLeaderMessage(sessionId);

          if (existingMsg) {
            const existingContent = existingMsg.content || '';
            const newContent = existingContent + content;
            const updatePayload: { content: string; isStreaming?: boolean } = { content: newContent };
            if (content.includes('MEDIA:')) {
              updatePayload.isStreaming = false;
            }
            useChatStore.getState().updateMessage(sessionId, existingMsg.id, updatePayload);
          } else {
            const msgId = `team-leader-${Date.now()}`;
            useChatStore.getState().addMessage(sessionId, {
              id: msgId,
              role: 'system',
              content: content,
              timestamp: new Date().toISOString(),
              isStreaming: true,
            });
          }
          return;
        }

        const currentStreamId = useChatStore.getState().getRuntime(sessionId)?.currentStreamId;
        clearThinkingForVisibleOutput(sessionId);
        if (!currentStreamId && content) {
          const assistantMsgId = `assistant-${Date.now()}`;
          useChatStore.getState().addMessage(sessionId, {
            id: assistantMsgId,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            isStreaming: true,
          });
          useChatStore.getState().startStreaming(sessionId, assistantMsgId);
        }
        useChatStore.getState().appendStreamContent(sessionId, content);
      }),
      webClient.on('chat.reasoning', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;

        // 只在明确属于当前活跃请求时恢复 processing，避免 evolution 后置 reasoning
        // 把已完成会话重新拉回处理中。
        if (shouldRecoverProcessingFromReasoning(sessionId, payload)) {
          useChatStore.getState().setProcessing(sessionId, true);
        }
      }),
      webClient.on('chat.final', ({ payload }) => {
        if (shouldDropDuplicatedEvent('chat.final', payload)) return;

        const cronMeta = payload.cron as Record<string, unknown> | undefined;

        // 达上限通知不绑定具体会话（后端 _send_notification_cb 不带 session_id），
        // 必须在下方 session 守卫（if (!sessionId) return）之前拦截，否则会被该守卫
        // 挡掉、用户看不到任何提示。走顶部 toast，不进会话历史。
        if (typeof payload.source === 'string' && payload.source === 'proactive_notification') {
          const notifContent = normalizeFinalContent(payload);
          if (notifContent) {
            useHarnessStore.getState().setProactiveNotification(notifContent);
          }
          return;
        }

        // cron 广播处理：结果到达时刷新触发会话列表（在 sessionId 路由之前，确保无论如何都刷新）
        if (cronMeta && typeof cronMeta === 'object') {
          const cronJobId = typeof cronMeta.job_id === 'string' ? cronMeta.job_id.trim() : '';
          const cronStatus = typeof cronMeta.status === 'string' ? cronMeta.status.trim() : '';
          const isPlaceholder = typeof cronMeta.is_placeholder === 'boolean' ? cronMeta.is_placeholder : false;
          if (cronJobId && cronStatus !== 'running') {
            const cronJob = useCronStore.getState().jobs.find((j) => j.id === cronJobId);
            const cronProjectId = cronJob?.project_id || 'default';
            void useCronStore.getState().loadCronSessions(cronProjectId, cronJobId);
          }
          // 非占位（最终结果）广播到达时标记定时任务未读
          if (cronJobId && !isPlaceholder) {
            useCronStore.getState().markCronJobUnread(cronJobId);
          }
        }

        let sessionId = resolveEventSessionId(payload);
        // cron 广播 session_id 为空（后端对 web 通道置空），
        // 优先使用 cronMeta.exec_session_id 路由到定时任务专属会话。
        // 若后端未提供 exec_session_id，用 job_id 查 lastRunSessionId（"立即执行"时存入）。
        if (!sessionId && cronMeta) {
          const execSessionId =
            typeof cronMeta.exec_session_id === 'string'
              ? (cronMeta.exec_session_id as string).trim()
              : '';
          if (execSessionId) {
            sessionId = execSessionId;
            ensureSessionRuntimes(sessionId);
          } else {
            const cronJobIdFallback = typeof cronMeta.job_id === 'string' ? cronMeta.job_id.trim() : '';
            if (cronJobIdFallback) {
              const lastSid = useCronStore.getState().lastRunSessionId[cronJobIdFallback] ?? '';
              if (lastSid) {
                sessionId = lastSid;
                ensureSessionRuntimes(sessionId);
              }
            }
          }
        }
        if (!sessionId) return;

        const memberAction = pickString(payload.member_action);
        const actionMemberName = pickString(payload.member_name);
        if (
          actionMemberName &&
          (memberAction === 'joined' || memberAction === 'left')
        ) {
          // 使用 upsert（若 spawned 事件尚未到达则创建占位，后续 spawned 事件会补全 teamName/sessionRef 等字段）
          useSessionStore.getState().upsertTeamHumanShareCommand(
            sessionId,
            {
              memberName: actionMemberName,
              displayName: pickString(payload.display_name),
              sessionId,
              teamName: '',
              sessionRef: '',
              joinCommand: '',
              exitCommand: '',
              status: memberAction === 'joined' ? 'joined' : 'left',
              sourceChannel: pickString(payload.source_channel),
              userId: pickString(payload.user_id),
              updatedAt: Date.now(),
            },
          );
          const content = normalizeFinalContent(payload);
          if (content) {
            useChatStore.getState().addMessage(sessionId, {
              id: `team-human-${memberAction}-${Date.now()}`,
              role: 'system',
              content,
              timestamp: normalizeEventTimestampIso(payload.timestamp),
            });
          }
          return;
        }

        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const content = normalizeFinalContent(payload);
        finishContextCompressionTurn(sessionId);

        // team 模式下，过滤成员输出，只保留外层 leader 回复。
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            const outputEventId = takeTeamMemberOutputEventId(payload, memberId);
            if (!content.trim()) {
              return;
            }
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: outputEventId || stableEventId('final', payload.session_id, memberId, payload.rid, timestamp, content.slice(0, 48)),
              member_id: memberId,
              kind: 'final',
              timestamp,
              title: t('team.process.execution.final'),
              content,
            });
          }
          return;
        }
        const teamLeaderMessageToFinalize =
          currentMode === 'team' && content
            ? findActiveTeamLeaderMessage(sessionId)
            : undefined;
        // Defensive: chat.final is the definitive end-of-response marker.
        // The primary state change is driven by chat.processing_status
        // (is_processing=false), but if that frame is lost the UI would be stuck
        // showing the stop button.
        // In team mode the backend suppresses chat.final while the team is
        // still running and only sends chat.processing_status(is_complete=true)
        // on team.completed, so we must NOT reset isProcessing here.
        if (!useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setExecutionError(sessionId, null);
          if (currentMode !== 'team') {
            useChatStore.getState().setProcessing(sessionId, false);
            // 正常情况下排空由 chat.processing_status(false) 负责；这里是它丢帧时的兜底
            // 重置，同样可能是"目标这一轮真正结束"的那个信号，一并兜底排空一次排队消息
            // （见问题3：目标完成后队列消息没有紧接着发出去）。已经排空过则是空操作，不会重复发送。
            drainTaskQueueIfIdle(sessionId);
          }
          useChatStore.getState().setThinking(sessionId, false);
          useChatStore.getState().clearSubtasks(sessionId);
        }
        if (content) {
          revealPendingContextUsage(sessionId);
        }
        if (currentMode === 'team' && content) {
          clearThinkingForVisibleOutput(sessionId);
          const timestamp = payload.timestamp || Date.now();

          if (teamLeaderMessageToFinalize) {
            useChatStore.getState().updateMessage(sessionId, teamLeaderMessageToFinalize.id, {
              content: `team.leader:${JSON.stringify({ content, timestamp })}`,
              isStreaming: false,
              timestamp: normalizeEventTimestampIso(payload.timestamp),
            });
            return;
          }
          useChatStore.getState().addMessage(sessionId, {
            id: `team-leader-${Date.now()}`,
            role: 'system',
            content: `team.leader:${JSON.stringify({ content, timestamp })}`,
            timestamp: new Date().toISOString(),
          });
          return;
        }

        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        const payloadSessionId =
          typeof payload.session_id === 'string' ? payload.session_id.trim() : '';

        // 检查是否为主动推荐消息
        const source = typeof payload.source === 'string' ? payload.source : '';
        const isProactiveRecommendation = source === 'proactive_recommendation';
        const proactiveType = typeof payload.proactive_type === 'string' ? payload.proactive_type : '';

        // 仅当有明确会话绑定时才把 final 合并进当前流式气泡。
        // 定时任务等广播的 session_id 为空/null，若仍走 currentStreamId 会写到错误气泡甚至”无可见更新”。
        const streamId = currentStreamId;
        if (streamId && payloadSessionId) {
          // 注意：这里不能再用 payload.timestamp 覆盖消息的 timestamp——它是"开始输出那一刻"
          // 盖的章，收尾只应该改 content/isStreaming。目标模式下 chat.final 和 goal.updated
          // (completed) 走的是两条独立通道、到达顺序不定，用后端 chat.final 自带的时间戳覆盖会
          // 让这条回复的时间戳跟"整轮真正跑完"（含目标评估）的时刻赛跑，一旦覆盖后的新时间戳
          // 晚于目标完成提示卡的时间戳，消息排序就会翻车（真机复现过，提示卡跑到回复上方）。
          useChatStore.getState().updateMessage(sessionId, streamId, {
            ...(content ? { content } : {}),
            isStreaming: false,
            ...(isProactiveRecommendation ? { isProactiveRecommendation, ...(proactiveType ? { proactiveType: proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration' } : {}) } : {}),
          });
          useChatStore.getState().stopStreaming(sessionId);
          if (content && !content.includes('MEDIA:')) {
            handleTtsPlayback(sessionId, streamId, content);
          }
          return;
        }
        if (content) {
          const cronMeta = payload.cron as Record<string, unknown> | undefined;
          const cronRunId =
            typeof cronMeta?.run_id === 'string' ? cronMeta.run_id.trim() : '';
          const isCronPlaceholderContent =
            cronMeta?.is_placeholder === true ||
            /正在执行中，结果稍后补发/.test(content) ||
            /^\[cron\].*正在执行中/.test(content);

          // 正式结果：替换同 run_id 的占位气泡，或最近的定时任务「正在执行中」占位
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
                if (
                  /正在执行中，结果稍后补发/.test(msg.content) ||
                  /^\[cron\].*正在执行中/.test(msg.content)
                ) {
                  placeholderId = msg.id;
                  break;
                }
              }
            }
            if (placeholderId) {
              useChatStore.getState().updateMessage(sessionId, placeholderId, { content, isStreaming: false });
              if (!content.includes('MEDIA:')) {
                handleTtsPlayback(sessionId, placeholderId, content);
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
            useChatStore.getState().updateMessage(sessionId, messageId, { content, isStreaming: false });
            if (!content.includes('MEDIA:')) {
              handleTtsPlayback(sessionId, messageId, content);
            }
            return;
          }

          // 去重：若上一条已是相同内容的助手消息（同一回复被收到两次），不再追加
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant' && last.content === content) {
            return;
          }
          useChatStore.getState().addMessage(sessionId, {
            id: messageId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
            isProactiveRecommendation,
            ...(proactiveType ? { proactiveType: proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration' } : {}),
          });
          if (!content.includes('MEDIA:')) {
            handleTtsPlayback(sessionId, messageId, content);
          }
        }
      }),
      webClient.on('chat.media', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const mediaPayload = payload as {
          content?: string;
          media_items?: MediaItem[];
        };
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
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
          useChatStore.getState().updateMessage(sessionId, targetId, updates);
        }
        if (mediaPayload.content) {
          handleTtsPlayback(sessionId, targetId, mediaPayload.content);
        }
      }),
      webClient.on('chat.file', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const files = (payload.files ?? []) as FileDownloadItem[];
        if (!files.length) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('file', payload.session_id, memberId, timestamp, files.map((file) => file.name).join(',')),
              member_id: memberId,
              kind: 'file',
              timestamp,
              title: t('team.process.execution.sentFile'),
              content: files.map((file) => file.name).join('\n'),
              files: files.map((file) => ({
                name: file.name,
                size: file.size,
                mime_type: file.mime_type,
                download_url: file.download_url,
              })),
            });
          }
          return;
        }
        if (currentMode === 'team') {
          const target = findActiveTeamLeaderMessage(sessionId);
          if (target) {
            useChatStore.getState().updateMessage(sessionId, target.id, {
              fileItems: [...(target.fileItems || []), ...files],
            });
          } else {
            useChatStore.getState().addMessage(sessionId, {
              id: `team-leader-${Date.now()}`,
              role: 'system',
              content: '',
              timestamp: new Date().toISOString(),
              isStreaming: true,
              fileItems: files,
            });
          }
          return;
        }
        useChatStore.getState().addFileItems(sessionId, files);
      }),
      webClient.on('chat.tool_call', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.tool_call', payload)) return;
        // 页面刷新后，如果收到活跃事件但 isProcessing=false，自动恢复执行状态
        if (!useChatStore.getState().getRuntime(sessionId)?.isProcessing && !useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setProcessing(sessionId, true);
        }
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        clearThinkingForVisibleOutput(sessionId);
        const toolCall = normalizeToolCallPayload(payload);
        const shutdownMemberId = getShutdownMemberFromToolCall(toolCall);
        if (shutdownMemberId) {
          shutdownMemberToolCallRef.current.set(toolCall.id, shutdownMemberId);
        }
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          if (currentMode === 'team' && !isTeamPanelClearedForPayload(payload)) {
            applyTeamTaskToolCall(sessionId, toolCall);
          }
          const memberId = getTeamPayloadMemberName(payload) || toolCall.memberName;
          if (memberId) {
            teamToolCallMemberRef.current.set(toolCall.id, memberId);
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('tool-call', payload.session_id, memberId, toolCall.id, timestamp),
              member_id: memberId,
              kind: 'tool_call',
              timestamp,
              title: t('team.process.execution.toolCallTitle', { tool: toolCall.name }),
              content: toolCall.description || toolCall.formatted_args || stringifyCompact(toolCall.arguments),
              tool_name: toolCall.name,
              tool_call_id: toolCall.id,
            });
          }
          return;
        }
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        const toolRequestId = getPayloadRequestId(payload) || activeRequestIdRef.current;
        const currentStreamMessage =
          currentMode === 'team'
            ? findActiveTeamLeaderMessage(sessionId)
            : currentStreamId
              ? messages.find((msg) => msg.id === currentStreamId)
              : undefined;
        useChatStore.getState().addToolCall(
          sessionId,
          toolCall,
          currentStreamMessage?.timestamp
            ? { startedAt: currentStreamMessage.timestamp, requestId: toolRequestId }
            : { requestId: toolRequestId }
        );
        if (currentMode === 'team' && !isTeamPanelClearedForPayload(payload)) {
          applyTeamTaskToolCall(sessionId, toolCall);
        }
      }),
      webClient.on('chat.tool_update', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const update = normalizeToolUpdatePayload(payload);
        if (!update.toolCallId || !update.beamSearch) return;
        useChatStore.getState().updateToolProgress(sessionId, update.toolCallId, {
          toolName: update.toolName,
          beamSearch: update.beamSearch,
        });
      }),
      webClient.on('chat.tool_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.tool_result', payload)) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const toolResult = normalizeToolResultPayload(payload);
        const activeSessionId = getPayloadSessionId(payload) || undefined;
        const shutdownMemberId =
          (toolResult.toolCallId
            ? shutdownMemberToolCallRef.current.get(toolResult.toolCallId)
            : undefined) ||
          getShutdownMemberFromToolResult(toolResult);
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId =
            getTeamPayloadMemberName(payload) ||
            (toolResult.toolCallId ? teamToolCallMemberRef.current.get(toolResult.toolCallId) : undefined);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('tool-result', payload.session_id, memberId, toolResult.toolCallId, timestamp),
              member_id: memberId,
              kind: 'tool_result',
              timestamp,
              title: t('team.process.execution.toolResultTitle', { tool: toolResult.toolName }),
              content: toolResult.summary || stringifyCompact(toolResult.result),
              tool_name: toolResult.toolName,
              tool_call_id: toolResult.toolCallId,
            });
          }
          if (shutdownMemberId) {
            if (toolResult.toolCallId) {
              shutdownMemberToolCallRef.current.delete(toolResult.toolCallId);
            }
            applyTeamMemberShutdown(
              shutdownMemberId,
              activeSessionId
            );
          }
          return;
        }
        if (shutdownMemberId) {
          if (toolResult.toolCallId) {
            shutdownMemberToolCallRef.current.delete(toolResult.toolCallId);
          }
          applyTeamMemberShutdown(
            shutdownMemberId,
            activeSessionId
          );
        }
        useChatStore.getState().addToolResult(sessionId, toolResult);
      }),
      webClient.on('todo.updated', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('todo.updated', payload)) return;
        if (isTeamPanelClearedForPayload(payload)) {
          return;
        }
        const todos = Array.isArray(payload.todos) ? payload.todos : [];
        useTodoStore.getState().setTodos(sessionId, todos as Parameters<ReturnType<typeof useTodoStore.getState>['setTodos']>[1]);
      }),
      webClient.on('goal.snapshot', ({ payload }) => {
        if (shouldDropDuplicatedEvent('goal.snapshot', payload)) return;
        applyGoalSnapshot(payload);
      }),
      webClient.on('goal.updated', ({ payload }) => {
        if (shouldDropDuplicatedEvent('goal.updated', payload)) return;
        applyGoalSnapshot(payload);
      }),
      webClient.on('runtime.accepted', () => {
        // Goal 的 loading 结束统一以 goal.snapshot 为准（文档 §4 中 set/resume 均先于
        // runtime.accepted 下发 goal.snapshot）；这里仅作为通用 ACK 占位，不做任何状态变更，
        // 不当作错误、不重试、不新增消息（文档 §6.1）。
      }),
      webClient.on('execution.error', ({ payload }) => {
        const goal = payload.goal;
        if (goal !== undefined) {
          applyGoalSnapshot(payload);
        }
      }),
      webClient.on('context.usage', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) return;
        const rate =
          typeof payload.rate === 'number' ? payload.rate : 0;
        const contextMax =
          typeof payload.context_max === 'number' && Number.isFinite(payload.context_max)
            ? payload.context_max
            : null;
        const tokensUsed =
          typeof payload.tokens_used === 'number' && Number.isFinite(payload.tokens_used)
            ? payload.tokens_used
            : null;
        const stats = { rate, beforeCompressed: contextMax, afterCompressed: tokensUsed };
        if (heldContextUsageSessionsRef.current.has(sessionId)) {
          pendingContextUsageRef.current.set(sessionId, stats);
          setContextCompressionStats(sessionId, {
            rate: 0,
            beforeCompressed: 0,
            afterCompressed: 0,
          });
        } else {
          setContextCompressionStats(sessionId, stats);
        }
        console.debug('[ws] context.usage', {
          session_id: payload.session_id,
          rate,
          context_max: contextMax,
          tokens_used: tokensUsed,
        });
      }),
      webClient.on<ContextCompressionStatePayload>(
        'context.compression_state',
        ({ payload }) => {
          const sessionId = resolveEventSessionId(payload);
          if (!sessionId) return;
          const memberId = findExistingTeamMemberId(sessionId, payload.member_name);
          if (memberId) {
            handleTeamMemberContextCompressionState(sessionId, payload, memberId);
            return;
          }
          handleContextCompressionState(sessionId, payload);
        }
      ),
      webClient.on('session.updated', ({ payload }) => {
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        if (!sessionId) return;
        updateSession(sessionId, payload as Partial<Session>);
        useWorkspaceStore.getState().patchSession(sessionId, payload as Partial<Session>);
        if (typeof payload.mode === 'string') {
          useSessionStore.getState().setMode(sessionId, normalizeAgentMode(payload.mode));
        }
      }),
      webClient.on('chat.processing_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.processing_status', payload)) return;
        // 切换模式时忽略处理状态更新
        if (useChatStore.getState().getRuntime(sessionId)?.switchingMode) return;
        // 加载历史消息时忽略处理状态更新
        if (useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) return;
        const isProcessingNow = Boolean(payload.is_processing);
        // 后端确认 processing=true 时清除本地发送标记——新任务已由后端接管
        if (isProcessingNow) {
          localSendPendingRef.current.delete(sessionId);
        }
        // 如果 interrupt_result 指示任务已完成，忽略 processing_status=true
        const interruptResult = useChatStore.getState().getRuntime(sessionId)?.interruptResult;
        const resumeAlreadyCompleted = isCompletedResumeResult(interruptResult);
        if (isProcessingNow && resumeAlreadyCompleted) {
          return;
        }
        if (isProcessingNow && useChatStore.getState().getRuntime(sessionId)?.isPaused) {
          return;
        }
        useChatStore.getState().setProcessing(sessionId, isProcessingNow);
        const sessionPatch: Partial<Session> = {
          is_processing: isProcessingNow,
          updated_at: new Date().toISOString(),
        };
        updateSession(sessionId, sessionPatch);
        useWorkspaceStore.getState().patchSession(sessionId, sessionPatch);
        if (!isProcessingNow) {
          useChatStore.getState().setThinking(sessionId, false);
          useChatStore.getState().clearSubtasks(sessionId);
          useChatStore.getState().stopStreaming(sessionId);

          // 检查是否有等待的任务队列
          const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
          const runtime = useChatStore.getState().getRuntime(sessionId);
          const taskQueue = runtime?.taskQueue ?? [];
          const queuePaused = runtime?.queuePaused ?? false;
          // 如果是本地 sendMessage 触发的打断（如队列"发送"按钮立即发送），
          // 跳过自动排空——后端即将发送 processing_status=true 启动新任务，
          // 不应在此刻再发队列首条导致两条消息同时到达后端
          const skipAutoDrain = localSendPendingRef.current.has(sessionId);
          if (skipAutoDrain) {
            localSendPendingRef.current.delete(sessionId);
          }
          if (
            !skipAutoDrain &&
            currentMode === 'agent' &&
            !resumeAlreadyCompleted &&
            !queuePaused &&
            taskQueue.length > 0
          ) {
            // 智能执行/单Agent模式下，自动处理队列中的下一个任务
            const nextTask = taskQueue[0];
            if (nextTask && sendMessageRef.current) {
              // 从队列中移除该任务
              useChatStore.getState().removeFromTaskQueue(sessionId, nextTask.id);
              // 发送下一个任务
              sendMessageRef.current(nextTask.content, sessionId);
            }
          }
        }
      }),
      webClient.on('chat.symphony_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content.trim() : '';
        if (!content) return;
        const operationId =
          typeof payload.operation_id === 'string' && payload.operation_id.trim()
            ? payload.operation_id.trim()
            : typeof payload.request_id === 'string' && payload.request_id.trim()
              ? payload.request_id.trim()
              : `${Date.now()}`;
        const messageId = `symphony-status-${operationId}`;
        const status = typeof payload.status === 'string' ? payload.status : '';
        const detail = typeof payload.detail === 'string' ? payload.detail.trim() : '';
        const displayContent =
          status === 'failed' && detail && !content.includes(detail)
            ? `${content}\n${detail}`
            : content;
        const chatState = useChatStore.getState();
        const messages = chatState.getRuntime(sessionId)?.messages ?? [];
        const cachedTarget = symphonyStatusTargetRef.current.get(operationId);
        const targetMessage = cachedTarget
          ? messages.find((message) => message.id === cachedTarget.messageId)
          : [...messages].reverse().find(
            (message) =>
              message.role === 'assistant' ||
              (message.role === 'system' && message.id?.startsWith('team-leader-'))
          );
        if (targetMessage) {
          const target = cachedTarget || {
            messageId: targetMessage.id,
            baseContent: targetMessage.content || '',
          };
          symphonyStatusTargetRef.current.set(operationId, target);
          const baseContent = target.baseContent.trimEnd();
          chatState.updateMessage(sessionId, target.messageId, {
            content: baseContent ? `${baseContent}\n\n${displayContent}` : displayContent,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        const existing = messages.find((message) => message.id === messageId);
        if (existing) {
          chatState.updateMessage(sessionId, messageId, {
            content: displayContent,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        chatState.addMessage(sessionId, {
          id: messageId,
          role: 'system',
          content: displayContent,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('chat.evolution_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.evolution_status', payload)) return;
        useChatStore.getState().setEvolutionStatus(sessionId, payload as unknown as EvolutionStatusPayload);
      }),
      webClient.on('chat.notice', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.notice', payload)) return;
        const content = pickString(payload.content, payload.message, payload.text);
        if (!content) return;
        const noticeType = pickString(payload.notice_type, payload.type) || 'notice';
        const requestId = getPayloadRequestId(payload) || `${Date.now()}`;
        const messageId = `notice-${noticeType}-${requestId}`;
        const chatState = useChatStore.getState();
        const existing = chatState.getRuntime(sessionId)?.messages.find((message) => message.id === messageId);
        if (existing) {
          chatState.updateMessage(sessionId, messageId, {
            content,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        chatState.addMessage(sessionId, {
          id: messageId,
          role: 'system',
          content,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('config.changed', ({ payload }) => {
        const updatedKeys = Array.isArray(payload?.updated_keys)
          ? payload.updated_keys.filter((key): key is string => typeof key === 'string')
          : undefined;
        onConfigChangedRef.current?.(updatedKeys);
      }),
      webClient.on('task.global_running', ({ payload }) => {
        useChatStore.getState().setGlobalTaskRunning(Boolean(payload?.running));
      }),
      webClient.on('chat.error', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.error', payload)) return;
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg =
          typeof payload.error === 'string' ? payload.error : t('network.unknownError');
        // 忽略 "invalid page_idx or session history not found" 错误，因为这是新会话的正常情况
        if (errorMsg.includes('invalid page_idx or session history not found')) {
          useChatStore.getState().setLoadingHistory(sessionId, false);
          return;
        }
        useChatStore.getState().setExecutionError(sessionId, errorMsg);
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().setSessionError(sessionId, errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('security.alert', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;

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
        const sessionId = resolveEventSessionId(event.payload);
        if (!sessionId) return;

        const retractMsg =
          typeof event.payload.message === 'string'
            ? event.payload.message
            : '内容已因安全原因撤回';

        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];

        // Replace current streaming message first
        if (currentStreamId) {
          useChatStore.getState().updateMessage(sessionId, currentStreamId, {
            content: retractMsg,
            isStreaming: false,
          });
          useChatStore.getState().stopStreaming(sessionId);
        }

        // Replace ALL assistant messages after the last user message
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
              useChatStore.getState().updateMessage(sessionId, messages[i].id, { content: retractMsg });
            }
          }
        } else {
          for (const msg of messages) {
            if (msg.role === 'assistant') {
              useChatStore.getState().updateMessage(sessionId, msg.id, { content: retractMsg });
            }
          }
        }

        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        activeRequestIdRef.current = undefined;

        const retractRequestId = typeof event.payload.request_id === 'string' ? event.payload.request_id : undefined;
        useChatStore.getState().clearCurrentTurnData(sessionId, retractRequestId);
      }),
      webClient.on('chat.interrupt_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.interrupt_result', payload)) return;
        // 切换模式时忽略中断结果
        if (useChatStore.getState().getRuntime(sessionId)?.switchingMode) return;
        const resultPayload = payload as unknown as InterruptResultPayload;
        useChatStore.getState().setInterruptResult(sessionId, resultPayload);
        // has_active_task 为 false 表示没有活跃任务（任务已完成）
        const hasActiveTask = resultPayload.has_active_task !== false;

        if (resultPayload.intent === 'pause') {
          if (resultPayload.success) {
            useChatStore.getState().setPaused(sessionId, true, resultPayload.paused_task);
          }
          useChatStore.getState().setProcessing(sessionId, false);
          useChatStore.getState().setThinking(sessionId, false);
        } else if (resultPayload.intent === 'resume') {
          if (resultPayload.success) {
            // 直接设置所有状态值
            if (hasActiveTask) {
              useChatStore.getState().setPaused(sessionId, false);
              useChatStore.getState().setProcessing(sessionId, true);
              useChatStore.getState().setThinking(sessionId, true);
            } else {
              useChatStore.getState().setPaused(sessionId, false);
              useChatStore.getState().setProcessing(sessionId, false);
              useChatStore.getState().setThinking(sessionId, false);
              // 任务已完成时，检查并触发队列中的下一个任务
              const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
              const runtime = useChatStore.getState().getRuntime(sessionId);
              const taskQueue = runtime?.taskQueue ?? [];
              const queuePaused = runtime?.queuePaused ?? false;
              if (currentMode === 'agent' && !queuePaused && taskQueue.length > 0) {
                const nextTask = taskQueue[0];
                if (nextTask && sendMessageRef.current) {
                  useChatStore.getState().removeFromTaskQueue(sessionId, nextTask.id);
                  sendMessageRef.current(nextTask.content, sessionId);
                }
              }
            }
          }
        } else if (resultPayload.intent === 'cancel') {
          useChatStore.getState().setPaused(sessionId, false);
          useChatStore.getState().setProcessing(sessionId, false);
          useChatStore.getState().setThinking(sessionId, false);
          // chat.interrupt_result 是一元响应，跟流式分片的 goal_intermediate 判断走的是完全
          // 独立的通道——不依赖后端把"目标已清除/暂停后这一轮该不该被当成中间态"判断对，
          // 用户主动点了停止/删除就该让当前气泡收尾，不再等一个可能被误判、永远不会来的
          // 真正 chat.final。
          useChatStore.getState().stopStreaming(sessionId);
        } else if (resultPayload.intent === 'supplement') {
          useChatStore.getState().setPaused(sessionId, false);
        }
      }),
      webClient.on('chat.subtask_update', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        useChatStore.getState().updateSubtask(sessionId, payload as unknown as SubtaskUpdatePayload);
      }),
      webClient.on('chat.ask_user_question', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const questionPayload = payload as Record<string, unknown>;
        const evolutionMeta =
          questionPayload.evolution_meta && typeof questionPayload.evolution_meta === 'object'
            ? (questionPayload.evolution_meta as Record<string, unknown>)
            : questionPayload._evolution_meta && typeof questionPayload._evolution_meta === 'object'
              ? (questionPayload._evolution_meta as Record<string, unknown>)
              : undefined;
        const questions = Array.isArray(questionPayload.questions) ? questionPayload.questions : [];
        const approvalSchema =
          typeof questionPayload.approval_schema === 'string'
            ? questionPayload.approval_schema
            : undefined;
        const planApprovalKind =
          typeof questionPayload.plan_approval_kind === 'string'
            ? questionPayload.plan_approval_kind
            : undefined;
        const planContent =
          typeof questionPayload.plan_content === 'string'
            ? questionPayload.plan_content
            : undefined;
        const planLanguage =
          questionPayload.plan_language === 'cn' || questionPayload.plan_language === 'en'
            ? questionPayload.plan_language
            : undefined;
        const normalizedPayload: AskUserQuestionPayload = {
          request_id: typeof questionPayload.request_id === 'string' ? questionPayload.request_id : '',
          source: typeof questionPayload.source === 'string' ? questionPayload.source : undefined,
          questions,
          ...(approvalSchema ? { approvalSchema } : {}),
          ...(evolutionMeta ? { evolutionMeta } : {}),
          ...(planApprovalKind ? { planApprovalKind } : {}),
          ...(planContent !== undefined ? { planContent } : {}),
          ...(planLanguage ? { planLanguage } : {}),
        };
        useChatStore.getState().setPendingQuestion(sessionId, normalizedPayload);
      }),
      // 同时监听 session_result 事件，以处理后端可能发送的不同格式
      webClient.on('session_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        clearThinkingForVisibleOutput(sessionId);
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
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
        useChatStore.getState().addToolCall(sessionId, sessionToolCall);
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
        useChatStore.getState().addToolResult(sessionId, sessionResult);
      }),
      webClient.on('chat.session_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.session_result', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
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
        useChatStore.getState().addToolCall(sessionId, sessionToolCall);
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
        useChatStore.getState().addToolResult(sessionId, sessionResult);
      }),
      webClient.on('proactive_recommendation', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content : '';
        if (!content) return;

        const proactiveType = typeof payload.proactive_type === 'string' ? payload.proactive_type : '';
        const proactiveTarget = typeof payload.proactive_target === 'string' ? payload.proactive_target : '';
        const proactiveReason = typeof payload.proactive_reason === 'string' ? payload.proactive_reason : '';

        const messageId = `proactive-${Date.now()}`;
        useChatStore.getState().addMessage(sessionId, {
          id: messageId,
          role: 'assistant',
          content,
          timestamp: new Date().toISOString(),
          isProactiveRecommendation: true,
          proactiveType: (proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration') || undefined,
        });

        console.debug('[ws] proactive_recommendation', {
          type: proactiveType,
          target: proactiveTarget,
          reason: proactiveReason,
        });
      }),
      webClient.on('team.event', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.event', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        useChatStore.getState().addMessage(sessionId, {
          id: `team-event-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.message', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.message', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        useChatStore.getState().addMessage(sessionId, {
          id: `team-message-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.task', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.task', payload)) {
          return;
        }
        if (isTeamPanelClearedForPayload(payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as {
            type?: string;
            team_id?: string;
            task_id?: string;
            status?: string;
            timestamp?: number;
            member_id?: string;
            assignee?: string;
            team_name?: string;
            title?: string;
            name?: string;
            description?: string;
            content?: string;
            updated_at?: number | string | null;
          };
          if (e.type === 'team.task.created' && e.task_id) {
            useSessionStore.getState().registerConfirmedTeamTaskCreation(sessionId, e.task_id);
          }
          useSessionStore.getState().addTeamTaskEvent(sessionId, {
            id: `task-${Date.now()}`,
            type: e.type || '',
            team_id: e.team_id || '',
            task_id: e.task_id || '',
            status: e.status || '',
            timestamp: e.timestamp || Date.now(),
            member_id: e.member_id,
            assignee: e.assignee,
            team_name: e.team_name,
            title: e.title || e.name || e.description,
            content: e.content,
            updated_at: e.updated_at,
          });
          const normalizedTask = normalizeTaskEvent(event);
          if (normalizedTask) {
            useSessionStore.getState().upsertTeamTask(sessionId, normalizedTask);
          }
        }
      }),
      webClient.on('team.member', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.member', payload)) {
          return;
        }
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as {
            type?: string;
            member_id?: string;
            status?: string;
            new_status?: string;
            timestamp?: number;
            name?: string;
            execution_status?: string | null;
            mode?: string;
          };
          const activeSessionId = getPayloadSessionId(payload) || undefined;
          upsertHumanShareCommandFromEvent(payload, e);
          if (e.type === 'team.member.shutdown' && e.member_id) {
            applyTeamMemberShutdown(e.member_id, activeSessionId);
          } else if (activeSessionId && clearedTeamPanelSessionRef.current.has(activeSessionId)) {
            return;
          } else if (e.type === 'team.member.status_changed' && e.member_id && e.new_status) {
            useSessionStore.getState().updateTeamMemberStatus(
              sessionId,
              e.member_id,
              e.new_status,
              e.timestamp
            );
          } else if (e.type === 'team.member.execution_changed' && e.member_id) {
            const existingMember = useSessionStore.getState().getRuntime(sessionId)?.teamMembers.some(
              (member) => member.member_id === e.member_id
            );
            if (existingMember) {
              useSessionStore.getState().addTeamMember(sessionId, {
                id: `member-${Date.now()}`,
                member_id: e.member_id,
                status: e.status || '',
                timestamp: e.timestamp || Date.now(),
                name: e.name,
                execution_status: e.execution_status || e.new_status,
                mode: e.mode,
              });
            }
          } else if (!e.type || e.type === 'team.member.spawned' || e.type === 'team.member.restarted') {
            useSessionStore.getState().addTeamMember(sessionId, {
              id: `member-${Date.now()}`,
              member_id: e.member_id || '',
              status: e.status || '',
              timestamp: e.timestamp || Date.now(),
              name: e.name,
              execution_status: e.execution_status,
              mode: e.mode,
            });
          }
        }
      }),
      webClient.on('chat.usage_summary', ({ payload }) => {
        console.log('[usage_summary] received:', payload);
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) {
          console.log('[usage_summary] filtered by session check');
          return;
        }
        const usage = payload.usage as UsageSummary | undefined;
        if (!usage) {
          console.log('[usage_summary] no usage field in payload');
          return;
        }
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
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
          useChatStore.getState().setUsageSummary(sessionId, targetId, usage);
        }
      }),
      webClient.on('harness.message', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content : '';
        const stage = typeof payload.stage === 'string' ? payload.stage : undefined;

        useHarnessStore.getState().addHarnessMessage(sessionId, content, stage);

        // Pipeline start message contains stages array: { content, pipeline, stages: [{slot, display_name}] }
        const rawStages = payload.stages;
        if (Array.isArray(rawStages) && rawStages.length > 0) {
          const stages: { slot: string; display_name: string }[] = [];
          for (const s of rawStages) {
            if (typeof s === 'object' && s !== null) {
              const obj = s as Record<string, unknown>;
              const slot = typeof obj.slot === 'string' ? obj.slot : '';
              const displayName = typeof obj.display_name === 'string' ? obj.display_name : '';
              if (slot) stages.push({ slot, display_name: displayName || slot });
            }
          }
          if (stages.length > 0) useHarnessStore.getState().setStageDefinitions(sessionId, stages);
        }

        // Mark stage as running (skip pipeline start message which has stages array)
        if (stage && !rawStages) {
          const existingStage = useHarnessStore.getState().getRuntime(sessionId)?.stageResults.find(s => s.stage === stage);
          if (existingStage?.status !== 'running') {
            useHarnessStore.getState().updateStageResult(sessionId, { stage, status: 'running', messages: [], metrics: {} });
          }
        }

        useChatStore.getState().addMessage(sessionId, {
          id: `harness-msg-${Date.now()}`,
          role: 'system',
          content,
          timestamp: new Date().toISOString(),
          isHarnessMessage: true,
        });
      }),
      webClient.on('harness.stage_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const stage = typeof payload.stage === 'string' ? payload.stage : '';
        const status = typeof payload.status === 'string' ? payload.status : 'success';
        const error = typeof payload.error === 'string' ? payload.error : undefined;
        const messages = Array.isArray(payload.messages) ? payload.messages.filter((m) => typeof m === 'string') : [];
        const metrics = typeof payload.metrics === 'object' && payload.metrics !== null && !Array.isArray(payload.metrics)
          ? payload.metrics as Record<string, unknown>
          : {};
        const scope = typeof payload.scope === 'string' ? payload.scope : '';
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const extensionStage = typeof payload.extension_stage === 'string' ? payload.extension_stage : '';
        const parentStage = typeof payload.parent_stage === 'string' ? payload.parent_stage : '';
        const taskId = typeof payload.task_id === 'string' ? payload.task_id : undefined;
        if (scope === 'extension' && extensionName) {
          useHarnessStore.getState().updateExtensionProgress(sessionId, {
            extensionName,
            taskId,
            parentStage: parentStage || stage,
            extensionStage,
            status: status as 'running' | 'success' | 'failed' | 'timeout' | 'pending' | 'waiting' | 'skipped' | 'rejected',
            error,
            messages,
          });
        }
        if (stage) {
          useHarnessStore.getState().updateStageResult(sessionId, {
            stage,
            status: status as 'running' | 'success' | 'failed' | 'timeout' | 'pending',
            error,
            messages,
            metrics,
          });
          if (status === 'failed' && error) {
            useChatStore.getState().addMessage(sessionId, {
              id: `harness-error-${Date.now()}`,
              role: 'system',
              content: `Stage ${stage} failed: ${error}`,
              timestamp: new Date().toISOString(),
            });
          }
        } else {
          console.warn('[harness.stage_result] No stage field in payload, skipping update');
        }
      }),
      webClient.on('harness.extension_ready', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const runtimePath = typeof payload.runtime_path === 'string' ? payload.runtime_path : '';
        const sessionRuntimePath = typeof payload.session_runtime_path === 'string' ? payload.session_runtime_path : runtimePath;
        const extensionRuntimePath = typeof payload.extension_runtime_path === 'string' ? payload.extension_runtime_path : '';
        const configPath = typeof payload.config_path === 'string' ? payload.config_path : '';
        const runtimeExtensions = Array.isArray(payload.runtime_extensions)
          ? payload.runtime_extensions
              .filter((item) => typeof item === 'object' && item !== null)
              .map((item) => {
                const obj = item as Record<string, unknown>;
                return {
                  extensionName: typeof obj.extension_name === 'string' ? obj.extension_name : '',
                  runtimePath: typeof obj.runtime_path === 'string' ? obj.runtime_path : '',
                  configPath: typeof obj.config_path === 'string' ? obj.config_path : '',
                };
              })
              .filter((item) => item.extensionName && item.runtimePath)
          : [];
        const verifyReport = typeof payload.verify_report === 'object' && payload.verify_report !== null && !Array.isArray(payload.verify_report)
          ? payload.verify_report as Record<string, unknown>
          : {};
        const componentsSummary = typeof payload.components_summary === 'object' && payload.components_summary !== null && !Array.isArray(payload.components_summary)
          ? payload.components_summary as Record<string, unknown>
          : {};

        useHarnessStore.getState().setExtensionReady(sessionId, {
          extensionName,
          runtimePath,
          sessionRuntimePath,
          extensionRuntimePath,
          configPath,
          runtimeExtensions,
          verifyReport,
          componentsSummary,
        });
      }),
      webClient.on('harness.activate_interaction', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const interactionId = typeof payload.interaction_id === 'string' ? payload.interaction_id : '';
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const runtimePath = typeof payload.runtime_path === 'string' ? payload.runtime_path : '';
        const options: string[] = Array.isArray(payload.options) ? payload.options : ['accept', 'reject'];

        useHarnessStore.getState().setActivateInteraction(sessionId, {
          interactionId,
          extensionName,
          runtimePath,
          options,
          pending: true,
        });
        useChatStore.getState().setPendingQuestion(sessionId, {
          request_id: interactionId,
          source: 'activate_confirm',
          questions: [{
            header: '扩展激活确认',
            question: `是否激活扩展 **${extensionName}**？`,
            options: options.map((opt: string) => ({
              label: opt === 'accept' ? '激活' : opt === 'reject' ? '拒绝' : opt,
              description: '',
            })),
          }],
        });
      }),
      webClient.on('harness.session_finished', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        useChatStore.getState().setExecutionError(sessionId, null);
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        useHarnessStore.getState().setHarnessRunning(sessionId, false);
      }),
    ];

    return () => {
      unsubs.forEach((fn) => fn());
    };
  }, [
    appendTeamMemberOutputDelta,
    clearAllTeamMemberContextCompressionStatus,
    clearPendingTeamMemberContextCompressionStart,
    clearTeamMemberContextCompressionStatus,
    findExistingTeamMemberId,
    finishContextCompressionTurn,
    handleConnectionAck,
    handleContextCompressionState,
    handleTeamMemberContextCompressionState,
    handleTtsPlayback,
    revealPendingContextUsage,
    setContextCompressionStats,
    clearThinkingForVisibleOutput,
    findActiveTeamLeaderMessage,
    updateSession,
    resolveEventSessionId,
    shouldDropDuplicatedEvent,
    shouldRecoverProcessingFromReasoning,
    t,
    takeTeamMemberOutputEventId,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectDir,
    };
    const nextSignature = getConnectSignature(connectOptions);
    const previousSignature = lastConnectSignatureRef.current;
    const state = webClient.getState();

    if (nextSignature === previousSignature && state !== 'closed') {
      return;
    }

    lastConnectSignatureRef.current = nextSignature;

    const runConnect = async () => {
      try {
        if (previousSignature && previousSignature !== nextSignature && state !== 'closed') {
          await webClient.disconnect('connect options changed');
        }
        await webClient.connect(connectOptions);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || 'WebSocket connection error');
      }
    };

    void runConnect();
  }, [
    apiBase,
    apiKey,
    model,
    projectDir,
    provider,
    setConnectionStats,
  ]);

  useEffect(() => {
    return () => {
      lastConnectSignatureRef.current = '';
      webClient.disconnect();
      setConnected(false);
      // 不再重置上下文压缩信息，保持本地存储的状态
      // setContextCompressionStats(null);
      setConnectionStats({ state: 'closed', inflight: 0 });
    };
  }, [
    setContextCompressionStats,
    setConnectionStats,
    setConnected,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectDir,
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
  }, [apiBase, apiKey, model, projectDir, provider, setConnectionStats]);

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
      // 断线恢复（false -> true 跳变）：真实环境联调方案 B.8——对"曾经查到过目标"的会话主动
      // get 一次，把 GoalBar 从 disconnected 收敛回真实状态。只挑非 completed 的目标查，
      // completed 不需要（也不参与下面的 1 分钟无更新巡检）。
      if (connected && !wasConnectedRef.current) {
        const runtimes = useGoalStore.getState().runtimes;
        for (const [sid, runtime] of Object.entries(runtimes)) {
          if (!runtime.goal || runtime.goal.status === 'completed') continue;
          const goalMode = useSessionStore.getState().getRuntime(sid)?.mode ?? 'agent';
          void performGoalGet(sid, goalMode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
        }
      }
      wasConnectedRef.current = connected;
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
    // 真实环境联调方案 9c：未完成目标超过 1 分钟没收到新的 goal.snapshot/goal.updated 事件，
    // 主动 get 一次兜底，避免长时间静默期间前端展示的状态跟后端实际状态脱节。用一个共享轮询
    // 巡检所有 session，而不是给每个 session 单独起 setTimeout——避免目标频繁切换 session 时
    // 需要额外维护一堆定时器的生命周期。
    //
    // unknown 态用不同的判断依据：lastGoalEventAtRef 只在成功时更新，一直失败的话这个时间戳
    // 永远不动，会导致每 15s 巡检都判定"超过 60s 未更新"、无限重触发 performGoalGet 自己的
    // 3 连击重试——变成每 15s 打一轮 3 连击轰炸一个已知连不上的后端。已经 unknown 的 session
    // 改用 lastGoalGetAttemptAtRef（记录"最近一次尝试"，不管成败）+ 更长的退避间隔，直到某次
    // get 成功、queryStatus 收敛回 'ok'，才会自动切回上面 60s 节奏的正常巡检。
    const timer = window.setInterval(() => {
      if (webClient.getState() !== 'ready') return;
      const runtimes = useGoalStore.getState().runtimes;
      const now = Date.now();
      for (const [sid, runtime] of Object.entries(runtimes)) {
        if (!runtime.goal || runtime.goal.status === 'completed') continue;
        if (runtime.queryStatus === 'unknown') {
          const lastAttempt = lastGoalGetAttemptAtRef.current.get(sid) ?? 0;
          if (now - lastAttempt < GOAL_UNKNOWN_RETRY_INTERVAL_MS) continue;
        } else {
          const lastAt = lastGoalEventAtRef.current.get(sid) ?? 0;
          if (now - lastAt < GOAL_STALE_REFRESH_MS) continue;
        }
        const goalMode = useSessionStore.getState().getRuntime(sid)?.mode ?? 'agent';
        void performGoalGet(sid, goalMode, goalCompletedHideTimerRef.current, lastGoalEventAtRef.current, lastGoalGetAttemptAtRef.current);
      }
    }, GOAL_STALE_REFRESH_CHECK_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const markAllRuntimes = () => {
      const runtimes = useChatStore.getState().runtimes;
      for (const sid of Object.keys(runtimes)) {
        useChatStore.getState().markTimedOutExecutions(sid);
      }
    };
    markAllRuntimes();
    const timer = window.setInterval(markAllRuntimes, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return {
    isConnected,
    connectionState,
    request,
    persistMedia,
    sendMessage,
    sendStructuredChatContent,
    interrupt,
    pause,
    cancel,
    supplement,
    resume,
    switchMode,
    disconnect,
    sendUserAnswer,
    respondActivate,
    setGoalObjective,
    pauseGoal,
    resumeGoal,
    clearGoal,
    refreshGoal,
    drainTaskQueueIfIdle,
    getInflightCount: () => webClient.getInflightCount(),
  };
}
