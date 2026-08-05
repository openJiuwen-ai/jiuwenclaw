/**
 * 类型导出
 */

export * from './goal';
export * from './message';
export * from './skillTree';
export * from './beamSearch';
export * from './todo';
export * from './websocket';
export * from '../features/workspace/projectTypes';

// 会话类型
export interface Session {
  session_id: string;
  title: string;
  project_id: string;
  project_dir: string;
  work_mode?: import('../features/workspace/projectTypes').WorkMode;
  pinned?: boolean;
  pin_order?: number;
  renamed_at?: string | null;
  display_title?: string | null;
  is_custom_title?: boolean;
  title_source?: 'auto' | 'user';
  model?: string;
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
  last_user_message_at?: number; // 最后一条用户消息时间(Unix时间戳)
}

export type AgentMode = 'agent' | 'team' | 'auto_harness';
export type SessionStatus = 'active' | 'paused' | 'completed' | 'interrupted';
export type Permission = 'default' | 'full_access';

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
  /** 可选别名，用于快捷切换模型（如 "gpt" → "gpt-4o"） */
  alias?: string;
  /** 用于原子性重命名操作，指定原模型名 */
  original_model_name?: string;
  /**
   * 持久化条目在 models.defaults 中的索引；由 models.list 透传。
   * replace_all 据此识别"未编辑字段"并保留 YAML 占位符（如 ${API_KEY}）。
   * 新增条目不带此字段。
   */
  origin_index?: number;
  /**
   * 厂商选择器的预设 key（如 "alibaba"/"baidu"）。提示性字段：
   * 不参与后端校验，仅用于前端回显图标 / 重新选中预设。由 vendors.list
   * 返回的预设表与 models.list 的回带字段对应。
   */
  vendor_key?: string;
  /** 该条目所属的 plan 分桶（'token_plan'|'coding_plan'|'custom_api'|'custom'）。提示性。 */
  plan?: string;
}

/** 厂商预设：vendors.list RPC 返回的单个厂商卡片。 */
export interface VendorPreset {
  vendor_key: string;
  display_name: string;
  plan: string;
  client_provider: string;
  api_base: string;
  default_model: string;
  model_options: string[];
  icon_key: string;
  models_endpoint: string | null;
  models_needs_key: boolean;
  models_extra_auth: Record<string, string>;
  anthropic_base: string | null;
  needs_third_party: boolean;
  needs_ak_sk: boolean;
}

/** vendors.list RPC 返回的载荷：按 plan 分组的厂商预设。 */
export type VendorPresetMap = Record<'token_plan' | 'coding_plan' | 'custom_api', VendorPreset[]>;

/** vendors.fetch_models RPC 返回的载荷。 */
export interface VendorFetchModelsResult {
  models: string[];
  source: 'remote' | 'preset';
  reason?: string;
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
