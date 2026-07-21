import { webRequest } from '../../services/webClient';
import type { GitRepoStatus, ProjectGitDiffStatus } from './types';

export const gitClient = {
  status: (projectId: string) =>
    webRequest<GitRepoStatus>('project.git.status', {
      project_id: projectId,
    }),

  probe: (projectId: string) =>
    webRequest<GitRepoStatus>('project.git.probe', {
      project_id: projectId,
    }),

  init: (projectId: string, initialBranch = 'main') =>
    webRequest<GitRepoStatus>('project.git.init', {
      project_id: projectId,
      initial_branch: initialBranch,
    }),

  switchBranch: (projectId: string, branch: string) =>
    webRequest<{
      switched: boolean;
      previous_branch: string | null;
      current_branch: string;
      status: GitRepoStatus;
    }>('project.git.switch_branch', {
      project_id: projectId,
      branch,
      require_clean: true,
    }),

  createBranch: (projectId: string, branch: string, startPoint?: string | null) =>
    webRequest<{
      created: boolean;
      checked_out: boolean;
      branch: string;
      status: GitRepoStatus;
    }>('project.git.create_branch', {
      project_id: projectId,
      branch,
      checkout: true,
      ...(startPoint ? { start_point: startPoint } : {}),
    }),

  diffStatus: (projectId: string, sessionId: string, options: { includeFiles?: boolean; includeHunks?: boolean } = {}) =>
    webRequest<ProjectGitDiffStatus>('project.git.diff_status', {
      project_id: projectId,
      session_id: sessionId,
      include_files: options.includeFiles ?? false,
      include_hunks: options.includeHunks ?? false,
    }),
};
