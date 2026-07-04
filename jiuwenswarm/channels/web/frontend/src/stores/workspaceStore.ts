import { create } from 'zustand';
import { projectRegistryClient } from '../features/workspace/projectRegistryClient';
import type { ProjectInfo, Session } from '../types';
import { useSessionStore } from './sessionStore';

export const PROJECT_SESSION_PAGE_SIZE = 10;

interface WorkspaceState {
  projects: ProjectInfo[];
  projectSessions: Record<string, Session[]>;
  projectSessionTotals: Record<string, number>;
  sessionVisibility: Record<string, { visibleCount: number }>;
  pinnedSessions: Session[];
  selectedProject: ProjectInfo | null;
  expandedProjectIds: Record<string, boolean>;
  isLoadingProjects: boolean;
  error: string | null;
  loadProjects: () => Promise<void>;
  loadProjectSessions: (projectId: string, limit?: number) => Promise<void>;
  showMoreSessions: (projectId: string) => Promise<void>;
  collapseSessions: (projectId: string) => Promise<void>;
  loadPinnedSessions: () => Promise<void>;
  setSelectedProject: (project: ProjectInfo | null) => void;
  toggleProjectExpanded: (projectId: string) => void;
  createProject: (name: string, projectPath: string) => Promise<ProjectInfo>;
  renameProject: (projectId: string, name: string) => Promise<void>;
  pinProject: (projectId: string, pinned: boolean) => Promise<void>;
  removeProject: (projectId: string) => Promise<void>;
  pinSession: (sessionId: string, pinned: boolean) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  refreshSessionWorkspace: (session: Pick<Session, 'project_path' | 'pinned'> | null | undefined) => Promise<void>;
}

function findProject(projects: ProjectInfo[], projectId: string): ProjectInfo | null {
  return projects.find((project) => project.project_id === projectId) ?? null;
}

function findProjectIdForSession(projects: ProjectInfo[], session: Pick<Session, 'project_path'>): string | null {
  const defaultProjectId = projects.find((project) => project.is_default || project.project_id === 'default')?.project_id ?? null;
  if (!session.project_path) {
    return defaultProjectId;
  }
  return projects.find((project) => project.project_path === session.project_path)?.project_id ?? defaultProjectId;
}

function patchSessionLists(
  lists: Record<string, Session[]>,
  sessionId: string,
  patch: Partial<Session>,
  options: { removeFromProjectLists?: boolean } = {},
): Record<string, Session[]> {
  let changed = false;
  const next: Record<string, Session[]> = {};
  for (const [projectId, sessions] of Object.entries(lists)) {
    const patched = sessions
      .map((session) => {
        if (session.session_id !== sessionId) return session;
        changed = true;
        return { ...session, ...patch };
      })
      .filter((session) => !(options.removeFromProjectLists && session.session_id === sessionId));
    next[projectId] = patched;
  }
  return changed ? next : lists;
}

function getVisibleCount(state: WorkspaceState, projectId: string): number {
  return state.sessionVisibility[projectId]?.visibleCount ?? PROJECT_SESSION_PAGE_SIZE;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  projects: [],
  projectSessions: {},
  projectSessionTotals: {},
  sessionVisibility: {},
  pinnedSessions: [],
  selectedProject: null,
  expandedProjectIds: {},
  isLoadingProjects: false,
  error: null,

  loadProjects: async () => {
    set({ isLoadingProjects: true, error: null });
    try {
      const payload = await projectRegistryClient.list();
      const projects = payload.projects || [];
      set((state) => ({
        projects,
        selectedProject: state.selectedProject
          ? findProject(projects, state.selectedProject.project_id)
          : null,
        isLoadingProjects: false,
      }));
      await get().loadPinnedSessions();
    } catch (error) {
      set({ isLoadingProjects: false, error: error instanceof Error ? error.message : String(error) });
    }
  },

  loadProjectSessions: async (projectId, limit) => {
    const requestedLimit = limit ?? getVisibleCount(get(), projectId);
    try {
      const payload = await projectRegistryClient.getSessions(projectId, requestedLimit);
      const sessions = payload.sessions || [];
      const total = Number.isFinite(payload.total) ? payload.total : sessions.length;
      set((state) => ({
        projectSessions: {
          ...state.projectSessions,
          [projectId]: sessions,
        },
        projectSessionTotals: {
          ...state.projectSessionTotals,
          [projectId]: total,
        },
        sessionVisibility: {
          ...state.sessionVisibility,
          [projectId]: { visibleCount: requestedLimit },
        },
      }));
    } catch (error) {
      console.error('Failed to load project sessions', error);
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },

  showMoreSessions: async (projectId) => {
    const state = get();
    const currentVisibleCount = getVisibleCount(state, projectId);
    const total = state.projectSessionTotals[projectId];
    const nextVisibleCount = total
      ? Math.min(currentVisibleCount + PROJECT_SESSION_PAGE_SIZE, total)
      : currentVisibleCount + PROJECT_SESSION_PAGE_SIZE;
    await get().loadProjectSessions(projectId, nextVisibleCount);
  },

  collapseSessions: async (projectId) => {
    await get().loadProjectSessions(projectId, PROJECT_SESSION_PAGE_SIZE);
  },

  loadPinnedSessions: async () => {
    const payload = await projectRegistryClient.pinnedSessions();
    set({ pinnedSessions: payload.sessions || [] });
  },

  setSelectedProject: (project) => set({ selectedProject: project }),
  toggleProjectExpanded: (projectId) => set((state) => ({
    expandedProjectIds: {
      ...state.expandedProjectIds,
      [projectId]: !state.expandedProjectIds[projectId],
    },
  })),

  createProject: async (name, projectPath) => {
    const { project_id: projectId } = await projectRegistryClient.create(name, projectPath);
    await get().loadProjects();
    const project = findProject(get().projects, projectId);
    if (!project) throw new Error('project.create returned a project that is missing from project.list');
    set((state) => ({
      selectedProject: project,
      expandedProjectIds: { ...state.expandedProjectIds, [project.project_id]: true },
    }));
    return project;
  },

  renameProject: async (projectId, name) => {
    await projectRegistryClient.rename(projectId, name);
    await get().loadProjects();
  },

  pinProject: async (projectId, pinned) => {
    await projectRegistryClient.pin(projectId, pinned);
    await get().loadProjects();
  },

  removeProject: async (projectId) => {
    await projectRegistryClient.remove(projectId);
    await get().loadProjects();
    const state = get();
    await Promise.all(state.projects
      .filter((project) => project.is_default || project.project_id === 'default' || (state.expandedProjectIds[project.project_id] ?? true))
      .map((project) => state.loadProjectSessions(project.project_id)));
  },

  pinSession: async (sessionId, pinned) => {
    const sessionState = useSessionStore.getState();
    const session =
      sessionState.currentSession?.session_id === sessionId
        ? sessionState.currentSession
        : sessionState.sessions.find((item) => item.session_id === sessionId);
    const result = await projectRegistryClient.pinSession(sessionId, pinned);
    const patch = { ...result, pinned };
    sessionState.updateSession(sessionId, patch);
    set((state) => ({
      projectSessions: patchSessionLists(
        state.projectSessions,
        sessionId,
        patch,
        { removeFromProjectLists: pinned },
      ),
      pinnedSessions: pinned
        ? state.pinnedSessions
        : state.pinnedSessions.filter((item) => item.session_id !== sessionId),
    }));
    await get().loadPinnedSessions();
    const projectId = session ? findProjectIdForSession(get().projects, session) : null;
    if (projectId) await get().loadProjectSessions(projectId);
  },

  renameSession: async (sessionId, title) => {
    const result = await projectRegistryClient.renameSession(sessionId, title);
    const renamedAt = new Date().toISOString();
    const patch: Partial<Session> = {
      title: result.title,
      display_title: result.title,
      is_custom_title: true,
      title_source: 'user',
      renamed_at: renamedAt,
    };
    useSessionStore.getState().updateSession(sessionId, patch);
    set((state) => ({
      projectSessions: patchSessionLists(state.projectSessions, sessionId, patch),
      pinnedSessions: state.pinnedSessions.map((session) => (
        session.session_id === sessionId ? { ...session, ...patch } : session
      )),
    }));
    await get().loadPinnedSessions();
    const sessionState = useSessionStore.getState();
    const session =
      sessionState.currentSession?.session_id === sessionId
        ? sessionState.currentSession
        : sessionState.sessions.find((item) => item.session_id === sessionId);
    const projectId = session ? findProjectIdForSession(get().projects, session) : null;
    if (projectId) await get().loadProjectSessions(projectId);
  },

  refreshSessionWorkspace: async (session) => {
    await get().loadProjects();
    const projectId = session ? findProjectIdForSession(get().projects, session) : null;
    if (projectId) {
      await get().loadProjectSessions(projectId);
    }
  },
}));
