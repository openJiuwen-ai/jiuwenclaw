import { create } from 'zustand';
import { webRequest } from '../services/webClient';
import { projectRegistryClient } from '../features/workspace/projectRegistryClient';
import type { Session } from '../types';

export interface SidebarCronJob {
  id: string;
  name: string;
  enabled: boolean;
  expired?: boolean;
  cron_expr: string;
  project_id: string;
  session_id?: string;
  created_at: number | string | null;
  updated_at: number | string | null;
}

interface CronState {
  jobs: SidebarCronJob[];
  isLoading: boolean;
  expandedCronGroups: Record<string, boolean>;
  // cron_id → 触发会话列表
  cronSessions: Record<string, Session[]>;
  // cron_id → 加载中状态
  cronSessionsLoading: Record<string, boolean>;
  loadJobs: () => Promise<void>;
  reload: () => Promise<void>;
  toggleCronGroup: (groupId: string) => void;
  loadCronSessions: (projectId: string, cronId: string) => Promise<void>;
  isCronGroupExpanded: (groupId: string) => boolean;
}

export const useCronStore = create<CronState>((set, get) => ({
  jobs: [],
  isLoading: false,
  expandedCronGroups: {},
  cronSessions: {},
  cronSessionsLoading: {},

  loadJobs: async () => {
    set({ isLoading: true });
    try {
      const payload = await webRequest<{ jobs: SidebarCronJob[] }>('cron.job.list');
      set({ jobs: payload.jobs || [], isLoading: false });
    } catch {
      set({ jobs: [], isLoading: false });
    }
  },

  reload: async () => {
    await get().loadJobs();
  },

  toggleCronGroup: (groupId: string) => {
    set((state) => ({
      expandedCronGroups: {
        ...state.expandedCronGroups,
        [groupId]: !state.expandedCronGroups[groupId],
      },
    }));
  },

  isCronGroupExpanded: (groupId: string) => {
    return get().expandedCronGroups[groupId] ?? false;
  },

  loadCronSessions: async (projectId: string, cronId: string) => {
    set((state) => ({
      cronSessionsLoading: { ...state.cronSessionsLoading, [cronId]: true },
    }));
    try {
      const payload = await projectRegistryClient.getCronSessions(projectId, cronId);
      set((state) => ({
        cronSessions: {
          ...state.cronSessions,
          [cronId]: payload.sessions || [],
        },
        cronSessionsLoading: { ...state.cronSessionsLoading, [cronId]: false },
      }));
    } catch {
      set((state) => ({
        cronSessionsLoading: { ...state.cronSessionsLoading, [cronId]: false },
      }));
    }
  },

}));

const DEFAULT_PROJECT_ID = 'default';

// 系统自动维护的 cron job id（与后端 proactive_cron_sync.PROACTIVE_JOB_ID、
// CronPanel/index.tsx 的 PROACTIVE_AUTO_JOB_ID 一致）。这类 job 由配置开关自动创建/删除，
// 不创建会话、不给推送的会话打 cron_id——所以"触发的会话"列表恒空，在会话侧栏是个空壳。
// 从会话侧栏隐藏它（Cron 面板里仍可见可编辑 cron 表达式/时区），避免空壳 item 碍眼。
const SYSTEM_AUTO_JOB_IDS = new Set(['proactive-tick-auto']);

export function isDefaultProjectId(projectId: string): boolean {
  return !projectId || projectId === DEFAULT_PROJECT_ID;
}

/** 按项目过滤定时任务（默认项目返回 project_id 为空的）；系统自动维护 job 不进会话侧栏。 */
export function filterJobsForProject(jobs: SidebarCronJob[], projectId: string): SidebarCronJob[] {
  const filtered = isDefaultProjectId(projectId)
    ? jobs.filter((job) => isDefaultProjectId(job.project_id) && !SYSTEM_AUTO_JOB_IDS.has(job.id))
    : jobs.filter((job) => job.project_id === projectId && !SYSTEM_AUTO_JOB_IDS.has(job.id));
  return filtered.sort((a, b) => {
    const au = typeof a.updated_at === 'number' ? a.updated_at : 0;
    const bu = typeof b.updated_at === 'number' ? b.updated_at : 0;
    return bu - au;
  });
}
