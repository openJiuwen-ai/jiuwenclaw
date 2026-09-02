// RSI 实验状态管理（Zustand）。
// 集中管理：实验列表 / 当前选中实验 / 运行态 KPI / 演进树 / 节点选中。
// 推送事件（P1/P2/P3）的归并也落在本 store，由 rsiEvents hook 驱动。

import { create } from 'zustand';
import type {
  RsiTaskListItem,
  RsiTaskGetResult,
  RsiTreeGetResult,
  RsiReportGetResult,
  RsiUsageGetResult,
  RsiTaskStatus,
  RsiTrainingStatusChangedPayload,
  RsiTrainingProgressPayload,
  RsiTrainingTreeDeltaPayload,
} from './types';

interface RsiDetailState {
  task: RsiTaskGetResult | null;
  report: RsiReportGetResult | null;
  usage: RsiUsageGetResult | null;
  tree: RsiTreeGetResult | null;
  selectedNodeId: string | null;
  // 运行态进度（来自 P2 推送，覆盖 task.progress 展示最新）
  liveProgress: {
    iteration: number;
    total: number;
    score: number | null;
    baseline: number | null;
    usageCost: number | null;
  } | null;
}

interface RsiState {
  list: RsiTaskListItem[];
  listLoading: boolean;
  listError: string | null;

  selectedTaskId: string | null;
  detail: Record<string, RsiDetailState>;
  detailLoading: boolean;

  // 列表
  loadList: () => Promise<void>;
  // 选中实验，拉取详情
  selectTask: (taskId: string | null) => Promise<void>;
  // 刷新单个实验详情（task.get / report.get / usage.get / tree.get）
  refreshDetail: (taskId: string) => Promise<void>;

  // 节点选中
  setSelectedNode: (nodeId: string | null) => void;

  // 本地状态变更（创建后插入列表、删除后移除、状态切换后更新）
  upsertListItem: (item: RsiTaskListItem) => void;
  removeListItem: (taskId: string) => void;
  patchTaskStatus: (taskId: string, status: RsiTaskStatus) => void;

  // 推送事件归并
  applyStatusChanged: (payload: RsiTrainingStatusChangedPayload) => void;
  applyProgress: (payload: RsiTrainingProgressPayload) => void;
  applyTreeDelta: (payload: RsiTrainingTreeDeltaPayload) => void;

  reset: () => void;
}

function emptyDetail(): RsiDetailState {
  return {
    task: null,
    report: null,
    usage: null,
    tree: null,
    selectedNodeId: null,
    liveProgress: null,
  };
}

export const useRsiStore = create<RsiState>((set, get) => ({
  list: [],
  listLoading: false,
  listError: null,
  selectedTaskId: null,
  detail: {},
  detailLoading: false,

  loadList: async () => {
    set({ listLoading: true, listError: null });
    try {
      const { rsiTaskList } = await import('./rsiApi');
      const list = await rsiTaskList();
      set({ list, listLoading: false });
    } catch (e) {
      set({
        listLoading: false,
        listError: e instanceof Error ? e.message : String(e),
      });
    }
  },

  selectTask: async (taskId) => {
    set({ selectedTaskId: taskId });
    if (taskId) {
      await get().refreshDetail(taskId);
    }
  },

  refreshDetail: async (taskId) => {
    set({ detailLoading: true });
    try {
      const [{ rsiTaskGet, rsiReportGet, rsiUsageGet, rsiTreeGet }] = await Promise.all([import('./rsiApi')]);
      const [task, report, usage, tree] = await Promise.all([
        rsiTaskGet(taskId),
        rsiReportGet(taskId),
        rsiUsageGet(taskId),
        rsiTreeGet(taskId),
      ]);
      set((state) => ({
        detail: {
          ...state.detail,
          [taskId]: {
            ...(state.detail[taskId] ?? emptyDetail()),
            task,
            report,
            usage,
            tree,
          },
        },
        detailLoading: false,
      }));
    } catch (e) {
      set({ detailLoading: false });
      console.error('[rsi] refreshDetail failed', e);
    }
  },

  setSelectedNode: (nodeId) => {
    const tid = get().selectedTaskId;
    if (!tid) return;
    set((state) => {
      const cur = state.detail[tid] ?? emptyDetail();
      return {
        detail: { ...state.detail, [tid]: { ...cur, selectedNodeId: nodeId } },
      };
    });
  },

  upsertListItem: (item) => {
    set((state) => {
      const idx = state.list.findIndex((t) => t.task_id === item.task_id);
      const list = [...state.list];
      if (idx >= 0) list[idx] = item;
      else list.unshift(item);
      return { list };
    });
  },

  removeListItem: (taskId) => {
    set((state) => ({
      list: state.list.filter((t) => t.task_id !== taskId),
      detail: Object.fromEntries(Object.entries(state.detail).filter(([id]) => id !== taskId)),
      selectedTaskId: state.selectedTaskId === taskId ? null : state.selectedTaskId,
    }));
  },

  patchTaskStatus: (taskId, status) => {
    set((state) => {
      const list = state.list.map((t) => (t.task_id === taskId ? { ...t, status, running: status === 'running' } : t));
      const cur = state.detail[taskId];
      const detail = cur
        ? {
            ...state.detail,
            [taskId]: {
              ...cur,
              task: cur.task ? { ...cur.task, status } : cur.task,
            },
          }
        : state.detail;
      return { list, detail };
    });
  },

  applyStatusChanged: (payload) => {
    get().patchTaskStatus(payload.task_id, payload.status);
  },

  applyProgress: (payload) => {
    const tid = payload.task_id;
    set((state) => {
      const cur = state.detail[tid] ?? emptyDetail();
      const list = state.list.map((t) =>
        t.task_id === tid
          ? {
              ...t,
              iter: { current: payload.iteration, total: payload.total },
              score: payload.score,
              base: payload.baseline ?? t.base,
            }
          : t,
      );
      return {
        list,
        detail: {
          ...state.detail,
          [tid]: {
            ...cur,
            liveProgress: {
              iteration: payload.iteration,
              total: payload.total,
              score: payload.score,
              baseline: payload.baseline,
              usageCost: payload.usage?.cost_estimate ?? cur.liveProgress?.usageCost ?? null,
            },
          },
        },
      };
    });
  },

  applyTreeDelta: (payload) => {
    const tid = payload.task_id;
    set((state) => {
      const cur = state.detail[tid] ?? emptyDetail();
      if (!cur.tree) return state;
      // 增量节点与全量节点按 node_id 去重合并（同源投影，覆盖更新）
      const map = new Map(cur.tree.nodes.map((n) => [n.node_id, n]));
      for (const delta of payload.nodes) {
        map.set(delta.node_id, { ...map.get(delta.node_id), ...delta } as never);
      }
      const nodes = [...map.values()];
      const depth = nodes.reduce((m, n) => Math.max(m, n.iteration), cur.tree.depth);
      const tree: RsiTreeGetResult = { nodes, depth, iteration: depth };
      return {
        detail: { ...state.detail, [tid]: { ...cur, tree } },
      };
    });
  },

  reset: () => {
    set({
      list: [],
      listLoading: false,
      listError: null,
      selectedTaskId: null,
      detail: {},
      detailLoading: false,
    });
  },
}));
