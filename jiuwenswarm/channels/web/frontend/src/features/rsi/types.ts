/**
 * RSI 前端类型。
 *
 * 页面内部使用小写枚举；rsiApi.ts 在 WebSocket 边界统一完成大小写和字段
 * 兼容转换。这样页面不会被 AgentServer/Provider 的内部投影字段绑死。
 */

export type RsiScenario = 'harness' | 'artifact';
export type RsiArtifactType = 'paper' | 'program';
export type RsiTaskStatus = 'created' | 'queued' | 'running' | 'completed' | 'failed' | 'paused' | 'terminated';
export type RsiNodeType = 'root' | 'adopted' | 'rejected' | 'provisional' | 'pruned';

export interface RsiUsage {
  tokens: {
    input: number;
    output: number;
    cache_hit: number;
  };
  cost_estimate: number;
  call_count: number;
}

export interface RsiDatasetValidateParams {
  dataset_file: string;
  scenario?: RsiScenario;
  artifact_type?: RsiArtifactType;
}

export interface RsiDatasetValidateResult {
  valid: boolean;
  sample_count: number | null;
  errors: Array<{ reason: string; code: string }>;
}

export interface RsiTaskCreateParams {
  scenario: RsiScenario;
  artifact_type?: RsiArtifactType;
  name: string;
  /** Harness 数据集；Artifact Paper 可选，Program 通常为空。 */
  dataset_file?: string;
  model_refs: {
    optimizer: string;
    tester?: string;
  };
  max_iterations?: number;
  search_width?: number;
  optimization_instruction?: string;
  artifact_path?: string;
}

export interface RsiTaskCreateResult {
  task_id: string;
  status: RsiTaskStatus;
  scenario: RsiScenario;
  artifact_type: RsiArtifactType | null;
}

export interface RsiTaskListParams {
  scenario?: RsiScenario;
  artifact_type?: RsiArtifactType;
}

/** 页面列表的稳定投影；后端当前只保证六个 wire 字段，其余字段由 rsiApi 补默认值。 */
export interface RsiTaskListItem {
  task_id: string;
  name: string;
  scenario: RsiScenario;
  artifact_type: RsiArtifactType | null;
  status: RsiTaskStatus;
  iter: { current: number; total: number };
  score: number | null;
  best: string | null;
  base: number | null;
  gain: number | null;
  running: boolean;
  created_at: string;
}

export interface RsiBestArtifact {
  artifact_id: string;
  name: string;
  adopted: boolean;
}

export interface RsiTaskGetResult {
  task_id: string;
  name: string;
  scenario: RsiScenario;
  artifact_type: RsiArtifactType | null;
  status: RsiTaskStatus;
  config: {
    model: { optimizer: string; tester: string | null };
    max_iterations: number;
    search_width: number;
    optimization_instruction: string | null;
    artifact_path: string | null;
  };
  /** 页面始终拿到非空默认值，避免 CREATED 阶段渲染分支散落。 */
  progress: {
    iteration: number;
    total_iterations: number;
    score: number | null;
    baseline: number | null;
  };
  best_artifact: RsiBestArtifact | null;
  usage: RsiUsage | null;
}

export interface RsiTrainingControlResult {
  status: RsiTaskStatus;
}

export interface RsiReportGetResult {
  status: RsiTaskStatus;
  best_score: number | null;
  baseline: number | null;
  metrics: {
    eval_passed: number;
    eval_total: number;
    pruned_count: number | null;
    iterations: number;
    best_artifact_id: string | null;
  };
  usage: RsiUsage | null;
  best_artifact: RsiBestArtifact | null;
  report_summary: string;
  markdown: string | null;
}

export interface RsiUsageGetResult {
  usage: RsiUsage;
  per_iteration: Array<{ iteration: number; usage: RsiUsage }>;
  usage_by_node: Record<string, RsiUsage> | null;
}

export interface RsiArtifactDownloadResult {
  path: string;
  kind: string;
  is_best: boolean;
  filename: string;
  download_url?: string;
  download_token?: string;
}

export interface RsiNodeChange {
  group: string;
  operation: string;
  function: string;
  target: string;
  summary: string;
}

export interface RsiTreeNode {
  node_id: string;
  iteration: number;
  parent_id: string | null;
  type: RsiNodeType;
  adopted: boolean;
  score: number | null;
  summary: string | null;
  snapshot_artifact_id: string | null;
  reason: string | null;
  failure_class: string | null;
  changes: RsiNodeChange[] | null;
  extra: Record<string, unknown> | null;
}

export interface RsiTreeGetResult {
  nodes: RsiTreeNode[];
  depth: number;
  iteration: number;
}

export interface RsiTrainingStatusChangedPayload {
  task_id: string;
  status: RsiTaskStatus;
  from?: RsiTaskStatus;
}

export interface RsiTrainingProgressPayload {
  task_id: string;
  iteration: number;
  total: number;
  score: number | null;
  baseline: number | null;
  usage: RsiUsage | null;
}

export interface RsiTrainingTreeDeltaPayload {
  task_id: string;
  nodes: RsiTreeNode[];
}
