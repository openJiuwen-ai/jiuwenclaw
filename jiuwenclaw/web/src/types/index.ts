/**
 * 类型导出
 */

export * from './message';
export * from './todo';
export * from './websocket';

// 会话类型
export interface Session {
  session_id: string;
  title: string;
  project_path: string;
  mode: AgentMode;
  status: SessionStatus;
  message_count: number;
  created_at: string;
  updated_at: string;
  is_active?: boolean;
  is_processing?: boolean;
  current_task?: string;
  tools?: string[];
}

export type AgentMode = 'agent' | 'plan';
export type SessionStatus = 'active' | 'paused' | 'completed' | 'interrupted';

export interface OffloadFileListResponse {
  session_id: string;
  files: string[];
  path: string;
  total: number;
}

export interface OffloadFileContentResponse {
  session_id: string;
  filename: string;
  content: string;
  path: string;
}
