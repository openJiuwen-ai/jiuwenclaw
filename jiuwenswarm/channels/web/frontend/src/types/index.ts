/**
 * 类型导出
 */

export * from './message';
export * from './skillTree';
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
  team_name?: string;
  // ---- session.list 扩展字段 ----
  channel_id?: string;         // 渠道ID
  user_id?: string;            // 创建人ID
  last_message_at?: number;    // 最近对话时间(Unix时间戳)
}

export type AgentMode = 'agent.fast' | 'agent.plan' | 'team' | 'auto_harness';
export type SessionStatus = 'active' | 'paused' | 'completed' | 'interrupted';

export interface ModelEntry {
  model_name: string;
  api_base: string;
  api_key: string;
  model_provider: string;
  timeout?: number;
  temperature?: number;
  reasoning_level?: string;
  context_window_tokens?: number;
  /** 同 model_name 组内的默认勾选标识 */
  is_default?: boolean;
  /** 可选别名，用于快捷切换模型（如 "mimo" → "xiaomi/mimo-v2-omni"） */
  alias?: string;
  /** 用于原子性重命名操作，指定原模型名 */
  original_model_name?: string;
  /**
   * 持久化条目在 models.defaults 中的索引；由 models.list 透传。
   * replace_all 据此识别"未编辑字段"并保留 YAML 占位符（如 ${API_KEY}）。
   * 新增条目不带此字段。
   */
  origin_index?: number;
}

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

export interface PackageInfo {
  id: string;
  extension_name: string;
  runtime_path: string;
  config_path: string;
  created_at: string;
  activated_at?: string;
  is_active: boolean;
  version_label?: string;
  description?: string;
}

export interface NativeVersionInfo {
  id: 'native';
  extension_name: 'Native Agent';
  is_active: boolean;
}

export interface PackagesPayload {
  packages: PackageInfo[];
  native_version: NativeVersionInfo;
  active_package_ids: string[];
  last_updated?: string;
}

export interface ActivatePayload {
  activated_package_id: string;
  extension_name: string;
  runtime_path: string;
  config_path: string;
  message: string;
  loaded_resources?: string[];
}

export interface DeactivatePayload {
  deactivated_package_id: string;
  extension_name: string;
  message: string;
}
