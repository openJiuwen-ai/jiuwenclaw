/**
 * RSI Web API 客户端。
 *
 * 组件只调用这里的 typed API。这里同时承担两件事：把页面的小写枚举转成
 * AgentServer 的 wire contract，以及把当前服务层/Provider 的字段差异归一为
 * 页面稳定模型。生产链路默认走真实 WebSocket，不再依赖浏览器端假数据。
 */

import { webRequest } from '../../services/webClient';
import type {
  RsiArtifactDownloadResult,
  RsiArtifactType,
  RsiDatasetValidateParams,
  RsiDatasetValidateResult,
  RsiNodeChange,
  RsiNodeType,
  RsiReportGetResult,
  RsiScenario,
  RsiTaskCreateParams,
  RsiTaskCreateResult,
  RsiTaskGetResult,
  RsiTaskListItem,
  RsiTaskListParams,
  RsiTaskStatus,
  RsiTrainingControlResult,
  RsiTrainingProgressPayload,
  RsiTrainingStatusChangedPayload,
  RsiTrainingTreeDeltaPayload,
  RsiTreeGetResult,
  RsiTreeNode,
  RsiUsage,
  RsiUsageGetResult,
} from './types';

export const RSI_EVENTS = {
  statusChanged: 'rsi.training.status.changed',
  progress: 'rsi.training.progress',
  treeDelta: 'rsi.training.tree.delta',
} as const;

const METHOD = {
  datasetValidate: 'rsi.dataset.validate',
  taskCreate: 'rsi.task.create',
  taskList: 'rsi.task.list',
  taskGet: 'rsi.task.get',
  taskDelete: 'rsi.task.delete',
  trainingStart: 'rsi.training.start',
  trainingPause: 'rsi.training.pause',
  trainingResume: 'rsi.training.resume',
  trainingTerminate: 'rsi.training.terminate',
  reportGet: 'rsi.report.get',
  usageGet: 'rsi.usage.get',
  artifactDownload: 'rsi.artifact.download',
  treeGet: 'rsi.tree.get',
} as const;

const RSI_SESSION_STORAGE_KEY = 'jiuwenswarm.rsi.session_id';
let inMemoryRsiSessionId: string | null = null;

/** Keep RSI requests and asynchronous Provider pushes on the same browser session. */
function getRsiSessionId(): string {
  if (inMemoryRsiSessionId) return inMemoryRsiSessionId;
  if (typeof window !== 'undefined') {
    try {
      const stored = window.sessionStorage.getItem(RSI_SESSION_STORAGE_KEY)?.trim();
      if (stored) {
        inMemoryRsiSessionId = stored;
        return stored;
      }
    } catch {
      // sessionStorage can be unavailable in privacy mode; use the memory fallback.
    }
  }
  const generated = `rsi_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  inMemoryRsiSessionId = generated;
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.setItem(RSI_SESSION_STORAGE_KEY, generated);
    } catch {
      // The in-memory value is sufficient for the lifetime of this page.
    }
  }
  return generated;
}

function withRsiSession(params: WireRecord): WireRecord {
  return { ...params, session_id: getRsiSessionId() };
}

type WireRecord = Record<string, unknown>;

function asRecord(value: unknown): WireRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as WireRecord) : null;
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value == null ? fallback : String(value);
}

function asNullableString(value: unknown): string | null {
  const result = asString(value).trim();
  return result ? result : null;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function asNullableNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const parsed = asNumber(value, Number.NaN);
  return Number.isFinite(parsed) ? parsed : null;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function normalizeRsiScenario(value: unknown, fallback: RsiScenario = 'harness'): RsiScenario {
  return String(value ?? '').trim().toLowerCase() === 'artifact' ? 'artifact' :
    String(value ?? '').trim().toLowerCase() === 'harness' ? 'harness' : fallback;
}

export function normalizeRsiArtifactType(value: unknown): RsiArtifactType | null {
  const normalized = String(value ?? '').trim().toLowerCase();
  return normalized === 'paper' || normalized === 'program' ? normalized : null;
}

export function normalizeRsiStatus(value: unknown, fallback: RsiTaskStatus = 'created'): RsiTaskStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  const statuses: RsiTaskStatus[] = [
    'created',
    'queued',
    'running',
    'completed',
    'failed',
    'paused',
    'terminated',
  ];
  return statuses.includes(normalized as RsiTaskStatus) ? (normalized as RsiTaskStatus) : fallback;
}

function normalizeUsage(value: unknown): RsiUsage | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const tokens = asRecord(raw.tokens) ?? {};
  return {
    tokens: {
      input: asNumber(tokens.input),
      output: asNumber(tokens.output),
      cache_hit: asNumber(tokens.cache_hit ?? tokens.cacheHit),
    },
    cost_estimate: asNumber(raw.cost_estimate ?? raw.costEstimate),
    call_count: asNumber(raw.call_count ?? raw.callCount),
  };
}

function normalizeBestArtifact(value: unknown): RsiTaskGetResult['best_artifact'] {
  const raw = asRecord(value);
  if (!raw) return null;
  const artifactId = asNullableString(raw.artifact_id ?? raw.id);
  if (!artifactId) return null;
  return {
    artifact_id: artifactId,
    name: asString(raw.name, artifactId),
    adopted: asBoolean(raw.adopted, true),
  };
}

function normalizeProgress(value: unknown, defaults?: { total?: number }): RsiTaskGetResult['progress'] {
  const raw = asRecord(value) ?? {};
  return {
    iteration: asNumber(raw.iteration ?? raw.current_iteration),
    total_iterations: asNumber(raw.total_iterations ?? raw.total ?? defaults?.total),
    score: asNullableNumber(raw.score),
    baseline: asNullableNumber(raw.baseline ?? raw.base),
  };
}

function normalizeChange(value: unknown): RsiNodeChange | null {
  const raw = asRecord(value);
  if (!raw) return null;
  return {
    group: asString(raw.group ?? raw.element ?? raw.domain).toLowerCase(),
    operation: asString(raw.operation).toLowerCase(),
    function: asString(raw.function ?? raw.function_name),
    target: asString(raw.target),
    summary: asString(raw.summary ?? raw.reason ?? raw.description, '未提供变更说明'),
  };
}

function normalizeNodeType(value: unknown): RsiNodeType {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'root' || normalized === 'adopted' || normalized === 'rejected' || normalized === 'provisional' || normalized === 'pruned') {
    return normalized;
  }
  if (normalized === 'candidate' || normalized === 'reporting' || normalized === 'success') return 'adopted';
  return 'rejected';
}

function normalizeTreeNode(value: unknown): RsiTreeNode | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const nodeId = asNullableString(raw.node_id ?? raw.id);
  if (!nodeId) return null;
  const changes = Array.isArray(raw.changes)
    ? raw.changes.map(normalizeChange).filter((item): item is RsiNodeChange => item !== null)
    : null;
  const extra = asRecord(raw.extra);
  return {
    node_id: nodeId,
    iteration: asNumber(raw.iteration),
    parent_id: asNullableString(raw.parent_id),
    type: normalizeNodeType(raw.type),
    adopted: asBoolean(raw.adopted),
    score: asNullableNumber(raw.score),
    summary: asNullableString(raw.summary ?? raw.description),
    snapshot_artifact_id: asNullableString(raw.snapshot_artifact_id),
    reason: asNullableString(raw.reason ?? raw.failure_reason),
    failure_class: asNullableString(raw.failure_class),
    changes,
    extra,
  };
}

function normalizeTaskListItem(value: unknown): RsiTaskListItem | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const status = normalizeRsiStatus(raw.status);
  const iter = asRecord(raw.iter);
  const bestArtifact = normalizeBestArtifact(raw.best_artifact);
  return {
    task_id: asString(raw.task_id),
    name: asString(raw.name, asString(raw.task_id, 'RSI 实验')),
    scenario: normalizeRsiScenario(raw.scenario),
    artifact_type: normalizeRsiArtifactType(raw.artifact_type),
    status,
    iter: {
      current: asNumber(iter?.current ?? raw.iteration),
      total: asNumber(iter?.total ?? raw.total_iterations ?? raw.max_iterations),
    },
    score: asNullableNumber(raw.score),
    best: asNullableString(raw.best) ?? bestArtifact?.name ?? bestArtifact?.artifact_id ?? null,
    base: asNullableNumber(raw.base ?? raw.baseline),
    gain: asNullableNumber(raw.gain),
    running: asBoolean(raw.running, status === 'running'),
    created_at: asString(raw.created_at),
  };
}

function normalizeTask(value: unknown): RsiTaskGetResult {
  const raw = asRecord(value) ?? {};
  const config = asRecord(raw.config) ?? {};
  const model = asRecord(config.model) ?? {};
  const progress = normalizeProgress(raw.progress, { total: asNumber(config.max_iterations) });
  return {
    task_id: asString(raw.task_id),
    name: asString(raw.name, asString(raw.task_id, 'RSI 实验')),
    scenario: normalizeRsiScenario(raw.scenario),
    artifact_type: normalizeRsiArtifactType(raw.artifact_type),
    status: normalizeRsiStatus(raw.status),
    config: {
      model: {
        optimizer: asString(model.optimizer),
        tester: asNullableString(model.tester),
      },
      max_iterations: asNumber(config.max_iterations, 1),
      search_width: asNumber(config.search_width, 1),
      optimization_instruction: asNullableString(config.optimization_instruction),
      artifact_path: asNullableString(config.artifact_path),
    },
    progress,
    best_artifact: normalizeBestArtifact(raw.best_artifact),
    usage: normalizeUsage(raw.usage),
  };
}

function normalizeReport(value: unknown): RsiReportGetResult | null {
  if (value == null) return null;
  const raw = asRecord(value) ?? {};
  const metrics = asRecord(raw.metrics) ?? {};
  const bestArtifact = normalizeBestArtifact(raw.best_artifact);
  return {
    status: normalizeRsiStatus(raw.status),
    best_score: asNullableNumber(raw.best_score),
    baseline: asNullableNumber(raw.baseline),
    metrics: {
      eval_passed: asNumber(metrics.eval_passed),
      eval_total: asNumber(metrics.eval_total),
      pruned_count: asNullableNumber(metrics.pruned_count),
      iterations: asNumber(metrics.iterations),
      best_artifact_id: asNullableString(metrics.best_artifact_id) ?? bestArtifact?.artifact_id ?? null,
    },
    usage: normalizeUsage(raw.usage),
    best_artifact: bestArtifact,
    report_summary: asString(raw.report_summary ?? raw.summary),
    markdown: asNullableString(raw.markdown),
  };
}

function normalizeUsageResult(value: unknown): RsiUsageGetResult | null {
  if (value == null) return null;
  const raw = asRecord(value) ?? {};
  const usage = normalizeUsage(raw.usage);
  if (!usage) return null;
  const perIteration = Array.isArray(raw.per_iteration)
    ? raw.per_iteration.flatMap((item) => {
        const entry = asRecord(item);
        const entryUsage = normalizeUsage(entry?.usage);
        return entryUsage ? [{ iteration: asNumber(entry?.iteration), usage: entryUsage }] : [];
      })
    : [];
  const rawByNode = asRecord(raw.usage_by_node);
  const usageByNode: Record<string, RsiUsage> | null = rawByNode
    ? Object.fromEntries(
        Object.entries(rawByNode).flatMap(([key, item]) => {
          const itemUsage = normalizeUsage(item);
          return itemUsage ? [[key, itemUsage]] : [];
        }),
      )
    : null;
  return { usage, per_iteration: perIteration, usage_by_node: usageByNode };
}

function normalizeTree(value: unknown): RsiTreeGetResult {
  const raw = asRecord(value) ?? {};
  const nodes = Array.isArray(raw.nodes)
    ? raw.nodes.map(normalizeTreeNode).filter((item): item is RsiTreeNode => item !== null)
    : [];
  return {
    nodes,
    depth: asNumber(raw.depth),
    iteration: asNumber(raw.iteration),
  };
}

export function normalizeRsiStatusChangedPayload(value: unknown): RsiTrainingStatusChangedPayload | null {
  const raw = asRecord(value);
  const taskId = asNullableString(raw?.task_id);
  if (!taskId) return null;
  const from = raw?.from ?? raw?.old_status;
  return {
    task_id: taskId,
    status: normalizeRsiStatus(raw?.status ?? raw?.new_status),
    ...(from != null ? { from: normalizeRsiStatus(from) } : {}),
  };
}

export function normalizeRsiProgressPayload(value: unknown): RsiTrainingProgressPayload | null {
  const raw = asRecord(value);
  const taskId = asNullableString(raw?.task_id);
  if (!taskId) return null;
  const nested = asRecord(raw?.progress) ?? raw ?? {};
  return {
    task_id: taskId,
    iteration: asNumber(raw?.iteration ?? nested.iteration),
    total: asNumber(raw?.total ?? raw?.total_iterations ?? nested.total ?? nested.total_iterations),
    score: asNullableNumber(raw?.score ?? nested.score),
    baseline: asNullableNumber(raw?.baseline ?? nested.baseline),
    usage: normalizeUsage(raw?.usage ?? nested.usage),
  };
}

export function normalizeRsiTreeDeltaPayload(value: unknown): RsiTrainingTreeDeltaPayload | null {
  const raw = asRecord(value);
  const taskId = asNullableString(raw?.task_id);
  if (!taskId) return null;
  const rawNodes = Array.isArray(raw?.nodes) ? raw.nodes : [];
  return {
    task_id: taskId,
    nodes: rawNodes.map(normalizeTreeNode).filter((item): item is RsiTreeNode => item !== null),
  };
}

function toWireScenario(value: RsiScenario | undefined): string {
  return String(value ?? 'harness').toUpperCase();
}

function toWireArtifactType(value: RsiArtifactType | undefined): string | undefined {
  return value ? value.toUpperCase() : undefined;
}

export interface RsiModelOption {
  id: string;
  name: string;
  is_free: boolean;
  provider?: string;
}

export async function rsiListModels(): Promise<RsiModelOption[]> {
  try {
    const response = await webRequest<unknown>('models.list');
    const raw = asRecord(response);
    const models = Array.isArray(raw?.models) ? raw.models : [];
    return models.flatMap((item, index) => {
      const model = asRecord(item);
      if (!model) return [];
      const id = asString(model.model_name, `model-${index}`);
      return [{
        id,
        name: asString(model.alias, id),
        is_free: model.is_free === true,
        provider: asNullableString(model.model_provider) ?? undefined,
      }];
    });
  } catch {
    return [];
  }
}

export function rsiDatasetValidate(params: RsiDatasetValidateParams): Promise<RsiDatasetValidateResult> {
  const wire: Record<string, unknown> = {
    input_file: params.dataset_file,
    scenario: toWireScenario(params.scenario),
  };
  const artifactType = toWireArtifactType(params.artifact_type);
  if (artifactType) wire.artifact_type = artifactType;
  return webRequest<unknown>(METHOD.datasetValidate, withRsiSession(wire)).then((value) => {
    const raw = asRecord(value) ?? {};
    const errors = Array.isArray(raw.errors)
      ? raw.errors.flatMap((item) => {
          const error = asRecord(item);
          return error
            ? [{ reason: asString(error.reason ?? error.message, '输入校验失败'), code: asString(error.code, 'DATASET_INVALID') }]
            : [];
        })
      : [];
    return {
      valid: raw.valid === true,
      sample_count: raw.sample_count == null ? null : asNumber(raw.sample_count),
      errors,
    };
  });
}

export function rsiTaskCreate(params: RsiTaskCreateParams): Promise<RsiTaskCreateResult> {
  const wire: Record<string, unknown> = {
    scenario: toWireScenario(params.scenario),
    name: params.name,
    model_refs: params.model_refs,
  };
  const artifactType = toWireArtifactType(params.artifact_type);
  if (artifactType) wire.artifact_type = artifactType;
  if (params.dataset_file?.trim()) {
    // Harness 的公共契约字段是 input_file；Paper 也允许把数据集作为 input_file。
    wire.input_file = params.dataset_file;
  }
  if (params.artifact_path?.trim()) wire.artifact_path = params.artifact_path;
  if (params.max_iterations != null) wire.max_iterations = params.max_iterations;
  if (params.search_width != null) wire.search_width = params.search_width;
  if (params.optimization_instruction?.trim()) wire.optimization_instruction = params.optimization_instruction;
  return webRequest<unknown>(METHOD.taskCreate, withRsiSession(wire)).then((value) => {
    const raw = asRecord(value) ?? {};
    return {
      task_id: asString(raw.task_id),
      status: normalizeRsiStatus(raw.status),
      scenario: normalizeRsiScenario(raw.scenario, params.scenario),
      artifact_type: normalizeRsiArtifactType(raw.artifact_type) ?? params.artifact_type ?? null,
    };
  });
}

export function rsiTaskList(params: RsiTaskListParams = {}): Promise<RsiTaskListItem[]> {
  const wire: Record<string, unknown> = {};
  if (params.scenario) wire.scenario = toWireScenario(params.scenario);
  if (params.artifact_type) wire.artifact_type = toWireArtifactType(params.artifact_type);
  return webRequest<unknown>(METHOD.taskList, withRsiSession(wire)).then((value) =>
    (Array.isArray(value) ? value : []).flatMap((item) => {
      const normalized = normalizeTaskListItem(item);
      return normalized ? [normalized] : [];
    }),
  );
}

export function rsiTaskGet(taskId: string): Promise<RsiTaskGetResult> {
  return webRequest<unknown>(METHOD.taskGet, withRsiSession({ task_id: taskId })).then(normalizeTask);
}

export function rsiTaskDelete(taskId: string): Promise<{ ok: boolean }> {
  return webRequest<unknown>(METHOD.taskDelete, withRsiSession({ task_id: taskId })).then((value) => ({ ok: asRecord(value)?.ok === true }));
}

function trainingControl(method: string, taskId: string): Promise<RsiTrainingControlResult> {
  return webRequest<unknown>(method, withRsiSession({ task_id: taskId })).then((value) => ({
    status: normalizeRsiStatus(asRecord(value)?.status),
  }));
}

export function rsiTrainingStart(taskId: string): Promise<RsiTrainingControlResult> {
  return trainingControl(METHOD.trainingStart, taskId);
}

export function rsiTrainingPause(taskId: string): Promise<RsiTrainingControlResult> {
  return trainingControl(METHOD.trainingPause, taskId);
}

export function rsiTrainingResume(taskId: string): Promise<RsiTrainingControlResult> {
  return trainingControl(METHOD.trainingResume, taskId);
}

export function rsiTrainingTerminate(taskId: string): Promise<RsiTrainingControlResult> {
  return trainingControl(METHOD.trainingTerminate, taskId);
}

export function rsiReportGet(taskId: string): Promise<RsiReportGetResult | null> {
  return webRequest<unknown>(METHOD.reportGet, withRsiSession({ task_id: taskId })).then(normalizeReport);
}

export function rsiUsageGet(taskId: string): Promise<RsiUsageGetResult | null> {
  return webRequest<unknown>(METHOD.usageGet, withRsiSession({ task_id: taskId }))
    .then(normalizeUsageResult)
    .catch((error: unknown) => {
      // CREATED 阶段还没有 usage snapshot；服务层目前用 TASK_NOT_FOUND 表示该状态。
      const code = asRecord(error)?.code;
      if (code === 'TASK_NOT_FOUND' || code === 'INTERNAL_ERROR') return null;
      throw error;
    });
}

export function rsiArtifactDownload(taskId: string, artifactId?: string): Promise<RsiArtifactDownloadResult> {
  const params: Record<string, unknown> = { task_id: taskId };
  if (artifactId) params.artifact_id = artifactId;
  return webRequest<unknown>(METHOD.artifactDownload, withRsiSession(params)).then((value) => {
    const raw = asRecord(value) ?? {};
    return {
      path: asString(raw.path),
      kind: asString(raw.kind),
      is_best: raw.is_best === true,
      filename: asString(raw.filename, asString(raw.path).split(/[\\/]/).pop() || 'download'),
      download_url: asNullableString(raw.download_url) ?? undefined,
      download_token: asNullableString(raw.download_token) ?? undefined,
    };
  });
}

export function rsiArtifactDownloadUrl(result: RsiArtifactDownloadResult): string | null {
  return result.download_url ?? null;
}

export function rsiTreeGet(taskId: string): Promise<RsiTreeGetResult> {
  return webRequest<unknown>(METHOD.treeGet, withRsiSession({ task_id: taskId })).then(normalizeTree);
}
