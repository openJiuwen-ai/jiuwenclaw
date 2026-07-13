/**
 * 消息类型定义
 */

import type { SkillTreePath } from './skillTree';

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface MediaItem {
  type: 'image' | 'audio' | 'video' | 'document';
  mimeType: string;
  mime_type?: string;
  filename: string;
  base64Data?: string;
  base64_data?: string;
  url?: string;
  path?: string;
  sizeBytes?: number;
  size_bytes?: number;
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost?: number;
  output_cost?: number;
  total_cost?: number;
}

export interface FileDownloadItem {
  name: string;
  size: number;
  mime_type: string;
  download_url: string;
  download_token: string;
}

export interface ContextCompressionRuntime {
  status: 'running' | 'completed' | 'unchanged' | 'failed';
  summary: string;
  operationId: string;
  phase?: string;
  processor?: string;
}

export interface ContextCompressionSummary {
  count: number;
  summaries: string[];
}

export interface TeamMemberContextCompressionState {
  runtime?: ContextCompressionRuntime;
  summary?: ContextCompressionSummary;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  audioBase64?: string;
  audioMime?: string;
  mediaItems?: MediaItem[];
  fileItems?: FileDownloadItem[];
  // 工具调用相关
  toolCall?: ToolCall;
  toolResult?: ToolResult;
  // 是否正在流式输出
  isStreaming?: boolean;
  usageSummary?: UsageSummary;
  // Harness message flag for special styling
  isHarnessMessage?: boolean;
  // 主动推荐消息标记
  isProactiveRecommendation?: boolean;
  proactiveType?: 'skill_recommend' | 'task_reminder' | 'need_exploration';
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  description?: string;  // 操作描述，如 "创建 3 个任务"
  formatted_args?: string;  // 格式化参数摘要
  memberName?: string;
}

export interface ToolResult {
  toolName: string;
  result: string;
  success: boolean;
  toolCallId?: string;
  summary?: string;  // 结果摘要
  // agentic search（symphony 技能检索）下发的技能树路径，用于内联回放路径流转
  skillTree?: SkillTreePath;
}

export type ToolExecutionStatus = 'pending' | 'timeout' | 'completed' | 'error';

export interface ToolExecution {
  toolCallId: string;
  toolCall: ToolCall;
  result?: ToolResult;
  status: ToolExecutionStatus;
  startedAt: string;
  updatedAt: string;
  timeoutAt: string;
  timedOutAt?: string;
  resultArrivedAfterTimeout?: boolean;
  requestId?: string;
}

export interface Conversation {
  id: string;
  messages: Message[];
  createdAt: string;
  updatedAt: string;
}
