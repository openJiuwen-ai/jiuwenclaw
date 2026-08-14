/**
 * WebSocket 消息类型
 */

export type WebConnectionState =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'reconnecting'
  | 'closed';

export interface WsRequest {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

export interface WsResponse {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: string;
  code?: string;
}

export interface WsEvent {
  type: 'event';
  event: string;
  payload: Record<string, unknown>;
  seq?: number;
  stream_id?: string;
  request_id?: string;
}

export type WebMessage = WsRequest | WsResponse | WsEvent;

export interface WebRequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
  requestId?: string;
}

export interface WebConnectOptions {
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectPath?: string;
}

export interface WebError extends Error {
  code?: string;
  requestId?: string;
  retriable?: boolean;
}

export interface ConnectionAckPayload {
  session_id?: string;
  mode?: string;
  tools?: string[];
  protocol_version?: string;
}

export interface ProcessingStatusPayload {
  is_processing: boolean;
  current_task?: string;
}

export interface ErrorPayload {
  error: string;
  code?: string;
  recoverable: boolean;
}

/**
 * 中断意图类型
 */
export type InterruptIntent = 'pause' | 'cancel' | 'supplement' | 'resume';

/**
 * 中断结果 Payload
 */
export interface InterruptResultPayload {
  intent: InterruptIntent;
  success: boolean;
  message: string;
  new_input?: string;
  merged_input?: string;
  paused_task?: string;
}

/**
 * 子任务状态类型
 */
export type SubtaskStatus = 'starting' | 'tool_call' | 'tool_result' | 'completed' | 'error';

/**
 * 子任务更新 Payload
 */
export interface SubtaskUpdatePayload {
  task_id: string;
  description: string;
  status: SubtaskStatus;
  index: number;
  total: number;
  tool_name?: string;
  tool_count?: number;
  message?: string;
  is_parallel?: boolean;
}

/**
 * 问题选项
 */
export interface QuestionOption {
  label: string;
  description?: string;
}

/**
 * 问题定义
 */
export interface Question {
  question: string;
  header: string;
  options: QuestionOption[];
  multi_select?: boolean;
}

/**
 * 用户问题请求 Payload（服务端 -> 客户端）
 */
export interface AskUserQuestionPayload {
  request_id: string;
  questions: Question[];
  source?: string; // 来源标识，用于区分自进化确认和工具权限确认
  agent_scope_id?: string; // 子 Agent 委托审批的严格关联标识
  /** Unix 毫秒，与侧车 wait_for_answer 截止时刻一致 */
  expires_at_ms?: number;
  timeout_sec?: number;
  /** Skill 加载审批卡（可选；缺失时回退渲染 message markdown） */
  skill_approval_card?: SkillApprovalCardPayload;
}

/**
 * 用户回答
 */
export interface UserAnswer {
  selected_options: string[];
  custom_input?: string;
  /** Skill 审批卡显式动作（approve_once/approve_session/continue_without_overlay），普通卡片无此字段 */
  action?: string;
}

/**
 * 用户回答 Payload（客户端 -> 服务端）
 */
export interface UserAnswerPayload {
  request_id: string;
  answers: UserAnswer[];
  agent_scope_id?: string;
}

/**
 * Skill 加载审批动作（与后端 SkillApprovalAction 契约一致）
 */
export type SkillApprovalAction =
  | 'approve_once'
  | 'approve_session'
  | 'continue_without_overlay';

/**
 * Skill 权限差分（放宽项在前、收紧其次、被安全策略丢弃的单列）
 */
export interface SkillApprovalDiff {
  widened: string[];
  tightened: string[];
  rejected: string[];
}

/**
 * Skill 加载审批卡结构化 Payload（对应后端 SkillApprovalCard.to_dict()，
 * 经 interrupt payload_schema["x-skill-approval-card"] 下发）
 */
export interface SkillApprovalCardPayload {
  kind: "skill_approval";
  schema_version: 1;
  skill_name: string;
  source: string;
  version?: string | null;
  trust: "builtin" | "other";
  permissions_hash: string;
  agent_scope_id: string;
  cached_decision?: "local" | "session" | null;
  diff: SkillApprovalDiff;
  actions: SkillApprovalAction[];
}
