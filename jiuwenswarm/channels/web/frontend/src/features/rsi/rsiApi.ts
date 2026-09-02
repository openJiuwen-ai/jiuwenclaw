// RSI API 客户端：业务层唯一入口，统一通过本文件调用后端接口。
// 接口未就绪时走 mock（rsiMock），ready 后将 USE_MOCK 置 false 即可，
// 业务层与组件无需改动。照抄 pluginPackagesApi.ts 的薄封装惯例。

import { webRequest } from '../../services/webClient';
import type {
  RsiDatasetValidateParams,
  RsiDatasetValidateResult,
  RsiTaskCreateParams,
  RsiTaskCreateResult,
  RsiTaskListParams,
  RsiTaskListItem,
  RsiTaskGetResult,
  RsiTrainingControlResult,
  RsiReportGetResult,
  RsiUsageGetResult,
  RsiTreeGetResult,
  RsiTaskStatus,
} from './types';
import { rsiMock } from './mockData';

// ============ mock 开关 ============
// 后端 rsi.* 接口就绪后改为 false，所有方法自动走真实 webRequest，无需删 mock 代码。
// 也可通过 localStorage('rsi_use_mock') 临时覆盖，便于联调时快速切换。
const STORAGE_KEY = 'rsi_use_mock';
function readStorageFlag(): boolean | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === 'true') return true;
    if (raw === 'false') return false;
  } catch {
    /* ignore */
  }
  return null;
}
// TODO(backend): 后端 rsi.* 接口就绪后改为 false。
const USE_MOCK = readStorageFlag() ?? true;

// 推送事件名常量（§4），集中定义避免散落字符串
export const RSI_EVENTS = {
  statusChanged: 'rsi.training.status.changed',
  progress: 'rsi.training.progress',
  treeDelta: 'rsi.training.tree.delta',
} as const;

// 复用既有接口的方法名常量（§12 复用清单）
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

// ============ 模型列表：复用 models.list（§14 #1 / §12）============
// 直接复用会话页的 models.list 接口，返回完整 ModelEntry（含 is_free / alias / model_provider），
// 前端据此区分免费模型与自定义模型，与聊天页模型选择器保持一致。
export interface RsiModelOption {
  id: string; // model_name（后端唯一标识）
  name: string; // 显示名（alias 优先，回退 model_name）
  is_free: boolean;
  provider?: string;
}
export async function rsiListModels(): Promise<RsiModelOption[]> {
  // models.list 是全局既有接口（非 rsi.* 专有），不走 USE_MOCK 开关，始终请求真实数据。
  try {
    const resp = await webRequest<{ models: Array<Record<string, unknown>>; active_model?: string }>('models.list');
    const list = Array.isArray(resp?.models) ? resp.models : [];
    return list.map((m, i) => ({
      id: (m.model_name as string) ?? `model-${i}`,
      name: (m.alias as string) ?? (m.model_name as string) ?? `model-${i}`,
      is_free: m.is_free === true,
      provider: (m.model_provider as string) ?? undefined,
    }));
  } catch {
    // models.list 失败时退回空列表，组件展示"无可用模型"，不静默假装成功
    return [];
  }
}

// ============ §5 数据集 ============
export function rsiDatasetValidate(params: RsiDatasetValidateParams): Promise<RsiDatasetValidateResult> {
  if (USE_MOCK) return rsiMock.delay({ valid: true, sample_count: 2288, errors: [] });
  return webRequest<RsiDatasetValidateResult>(METHOD.datasetValidate, params as unknown as Record<string, unknown>);
}

// ============ §6 任务管理 ============
export function rsiTaskCreate(params: RsiTaskCreateParams): Promise<RsiTaskCreateResult> {
  if (USE_MOCK) {
    const id = `rsi-task-${Math.random().toString(36).slice(2, 8)}`;
    return rsiMock.delay({
      task_id: id,
      status: 'created' as const,
      scenario: params.scenario,
      artifact_type: params.scenario === 'artifact' ? (params.artifact_type ?? null) : null,
    });
  }
  return webRequest<RsiTaskCreateResult>(METHOD.taskCreate, params as unknown as Record<string, unknown>);
}

export function rsiTaskList(params: RsiTaskListParams = {}): Promise<RsiTaskListItem[]> {
  if (USE_MOCK) {
    let list = [...rsiMock.tasks];
    if (params.scenario) list = list.filter((t) => t.scenario === params.scenario);
    if (params.artifact_type) list = list.filter((t) => t.artifact_type === params.artifact_type);
    return rsiMock.delay(list);
  }
  return webRequest<RsiTaskListItem[]>(METHOD.taskList, params as Record<string, unknown>);
}

export function rsiTaskGet(taskId: string): Promise<RsiTaskGetResult> {
  if (USE_MOCK) {
    const item = rsiMock.taskGet(taskId);
    return rsiMock.delay(item ?? rsiMock.taskGet('rsi-task-001'));
  }
  return webRequest<RsiTaskGetResult>(METHOD.taskGet, { task_id: taskId });
}

export function rsiTaskDelete(taskId: string): Promise<{ ok: boolean }> {
  if (USE_MOCK) return rsiMock.delay({ ok: true });
  return webRequest<{ ok: boolean }>(METHOD.taskDelete, { task_id: taskId });
}

// ============ §7 训练控制 ============
export function rsiTrainingStart(taskId: string): Promise<RsiTrainingControlResult> {
  if (USE_MOCK) return rsiMock.delay({ status: 'running' as RsiTaskStatus });
  return webRequest<RsiTrainingControlResult>(METHOD.trainingStart, { task_id: taskId });
}
export function rsiTrainingPause(taskId: string): Promise<RsiTrainingControlResult> {
  if (USE_MOCK) return rsiMock.delay({ status: 'paused' as RsiTaskStatus });
  return webRequest<RsiTrainingControlResult>(METHOD.trainingPause, { task_id: taskId });
}
export function rsiTrainingResume(taskId: string): Promise<RsiTrainingControlResult> {
  if (USE_MOCK) return rsiMock.delay({ status: 'queued' as RsiTaskStatus });
  return webRequest<RsiTrainingControlResult>(METHOD.trainingResume, { task_id: taskId });
}
export function rsiTrainingTerminate(taskId: string): Promise<RsiTrainingControlResult> {
  if (USE_MOCK) return rsiMock.delay({ status: 'terminated' as RsiTaskStatus });
  return webRequest<RsiTrainingControlResult>(METHOD.trainingTerminate, { task_id: taskId });
}

// ============ §8 报告 / 用量 / 产物 ============
export function rsiReportGet(taskId: string): Promise<RsiReportGetResult | null> {
  // 排队/新建等无报告的任务返回 null（不借用其他任务数据），保证空值如实透传给 UI 显示 --
  if (USE_MOCK) return rsiMock.delay(rsiMock.report(taskId) ?? null);
  return webRequest<RsiReportGetResult | null>(METHOD.reportGet, { task_id: taskId });
}
export function rsiUsageGet(taskId: string): Promise<RsiUsageGetResult | null> {
  // 排队/新建等无用量记录的任务返回 null（不借用其他任务数据）
  if (USE_MOCK) return rsiMock.delay(rsiMock.usage(taskId) ?? null);
  return webRequest<RsiUsageGetResult | null>(METHOD.usageGet, { task_id: taskId });
}

// §8.3 产物下载：HTTP Range 文件流 bridge。前端构造下载链接触发浏览器下载。
// gateway 侧已有 Range 解析，这里通过同源 /ws 代理路径触发，复用既有下载基础设施。
export function rsiArtifactDownloadUrl(taskId: string, artifactId?: string): string {
  const params = new URLSearchParams();
  params.set('method', METHOD.artifactDownload);
  params.set('task_id', taskId);
  if (artifactId) params.set('artifact_id', artifactId);
  return `/api/rsi/download?${params.toString()}`;
}

// ============ §9 演进树 ============
export function rsiTreeGet(taskId: string): Promise<RsiTreeGetResult> {
  if (USE_MOCK) return rsiMock.delay(rsiMock.tree(taskId));
  return webRequest<RsiTreeGetResult>(METHOD.treeGet, { task_id: taskId });
}

export const RSI_USE_MOCK = USE_MOCK;
