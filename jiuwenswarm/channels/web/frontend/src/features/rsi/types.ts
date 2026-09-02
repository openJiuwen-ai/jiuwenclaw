// RSI 优化平台前端类型定义
// 严格对齐后端接口契约 rsi_web_api_contract_v0.2.3.md（§3–§9）。
// 本文件是接口字段的 TypeScript 投影，便于 mock 层与真实接口共用同一形状。

// §3.1 场景标识
export type RsiScenario = 'harness' | 'artifact';
// 产物优化子类型（仅 scenario=artifact 时有意义）
export type RsiArtifactType = 'paper' | 'program';

// §3.3 任务状态枚举
export type RsiTaskStatus = 'created' | 'queued' | 'running' | 'completed' | 'failed' | 'paused' | 'terminated';

// §3.3 演进树节点类型枚举（全场景统一 5 值）
export type RsiNodeType = 'root' | 'adopted' | 'rejected' | 'provisional' | 'pruned';

// §3.4 usage / token 统一结构
export interface RsiUsage {
  tokens: {
    input: number;
    output: number;
    cache_hit: number;
  };
  cost_estimate: number;
  call_count: number;
}

// §5.1 rsi.dataset.validate
export interface RsiDatasetValidateParams {
  dataset_file: string;
}
export interface RsiDatasetValidateResult {
  valid: boolean;
  sample_count: number | null;
  errors: Array<{ reason: string; code: string }>;
}

// §6.1 rsi.task.create
export interface RsiTaskCreateParams {
  scenario: RsiScenario;
  artifact_type?: RsiArtifactType;
  name: string;
  dataset_file: string;
  model_refs: {
    optimizer: string;
    tester?: string; // 仅 harness 优化
  };
  max_iterations?: number; // 默认 1
  search_width?: number; // 默认 1
  optimization_instruction?: string; // 产物优化·论文可选（≤1000 字）
  artifact_path?: string; // 产物优化；论文可选、程序必选
}
export interface RsiTaskCreateResult {
  task_id: string;
  status: 'created';
  scenario: RsiScenario;
  artifact_type: RsiArtifactType | null;
}

// §6.2 rsi.task.list
export interface RsiTaskListParams {
  scenario?: RsiScenario;
  artifact_type?: RsiArtifactType;
}
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

// §6.3 rsi.task.get
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
  progress: {
    iteration: number;
    total_iterations: number;
    score: number | null;
    baseline: number | null;
  };
  best_artifact: RsiBestArtifact | null;
  usage: RsiUsage | null;
}

// §7 训练控制（start/pause/resume/terminate 统一入参 task_id）
export interface RsiTrainingControlResult {
  status: RsiTaskStatus;
}

// §8.1 rsi.report.get
export interface RsiReportGetResult {
  status: RsiTaskStatus;
  best_score: number | null;
  baseline: number | null;
  metrics: {
    eval_passed: number;
    eval_total: number;
    pruned_count: number | null; // 仅 harness 优化；产物优化为 null
    iterations: number;
    best_artifact_id: string | null;
  };
  usage: RsiUsage | null;
  best_artifact: RsiBestArtifact | null;
  report_summary: string;
  markdown: string | null; // 前端本期占位，不渲染
}

// §8.2 rsi.usage.get
export interface RsiUsageGetResult {
  usage: RsiUsage;
  per_iteration: Array<{ iteration: number; usage: RsiUsage }>;
  usage_by_node: Record<string, RsiUsage> | null;
}

// §8.3 rsi.artifact.download（HTTP 文件流，前端走 window 触发下载）

// §9.1 rsi.tree.get
export interface RsiNodeChange {
  group: string; // harness: prompt/skill/tool/rail；产物：按 paper/program 域
  operation: string;
  function: string;
  target: string;
  summary: string;
}
export interface RsiTreeNode {
  node_id: string;
  iteration: number;
  parent_id: string | null; // 根为 null
  type: RsiNodeType;
  adopted: boolean;
  score: number | null;
  summary: string | null;
  snapshot_artifact_id: string | null; // 仅被采纳节点
  reason: string | null;
  failure_class: string | null;
  changes: RsiNodeChange[] | null;
  extra: Record<string, unknown> | null; // 产物优化扩展容器
}
export interface RsiTreeGetResult {
  nodes: RsiTreeNode[];
  depth: number;
  iteration: number;
}

// ── 推送事件（P1/P2/P3，§4 清单）──
// 契约文档仅列出清单未给出独立 schema，下列形状按字段惯例（§3.3 status / §3.4 usage /
// §11.1 iteration 派生口径 / §9.2 tree.delta 节点结构）推导，接口 ready 后以服务端为准。

// P1 rsi.training.status.changed —— 状态迁移
export interface RsiTrainingStatusChangedPayload {
  task_id: string;
  status: RsiTaskStatus;
  from?: RsiTaskStatus; // 旧状态（v0.2.2 删除了 P1.from，但保留可选字段兼容展示）
}

// P2 rsi.training.progress —— 迭代进度 + score + 累计开销
export interface RsiTrainingProgressPayload {
  task_id: string;
  iteration: number; // 当前 iteration（§11.1）
  total: number; // 总量（= max_iterations）
  score: number | null; // 当前最优分
  baseline: number | null; // 基线分
  usage: RsiUsage | null; // 累计开销
}

// P3 rsi.training.tree.delta —— 树增量节点（与 tree.get 同源，§9.2）
export interface RsiTrainingTreeDeltaPayload {
  task_id: string;
  nodes: Array<{
    node_id: string;
    iteration: number;
    parent_id: string | null;
    type: RsiNodeType;
    adopted: boolean;
    score: number | null;
    summary?: string | null;
    snapshot_artifact_id?: string | null;
    changes?: RsiNodeChange[] | null;
    extra?: Record<string, unknown> | null;
  }>;
}
