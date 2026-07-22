export interface GitRepoStatus {
  project_id: string;
  project_name: string;
  project_dir: string;
  work_mode: 'code';
  repo: {
    is_git: boolean;
    repo_root: string | null;
    branch: string | null;
    head: string | null;
    detached: boolean;
    transient: boolean;
    upstream: string | null;
  };
  working_tree: {
    is_dirty: boolean;
    staged: number;
    unstaged: number;
    untracked: number;
    conflicted: number;
  };
  branches: {
    current: string | null;
    locals: string[];
    remotes: string[];
  };
  generated_at: number;
}

export interface GitDiffHunk {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  lines: string[];
}

export interface GitDiffFile {
  file_path: string;
  status: 'modified' | 'added' | 'deleted' | 'renamed' | 'missing' | string;
  change_type?: 'modified' | 'added' | 'deleted' | 'renamed' | 'missing' | string;
  lines_added: number;
  lines_removed: number;
  is_binary: boolean;
  is_new_file: boolean;
  is_deleted_file: boolean;
  is_untracked: boolean;
  is_large_file: boolean;
  is_truncated: boolean;
  hunks: GitDiffHunk[];
}

export interface GitDiffSummary {
  kind: 'working_tree';
  is_dirty: boolean;
  stats: GitDiffStats;
  files: Record<string, GitDiffFile>;
}

export interface GitDiffStats {
  files_changed: number;
  lines_added: number;
  lines_removed: number;
}

export interface GitTurnDiff {
  kind: 'conversation_turn';
  change_set_id: string;
  turn_index: number;
  request_id: string;
  user_message_id: string;
  assistant_message_id: string;
  timestamp: string;
  user_prompt_preview: string;
  repo_root?: string | null;
  branch?: string | null;
  base_head?: string | null;
  status: 'completed' | 'discarded' | 'partial' | 'failed' | string;
  stats: GitDiffStats;
  files: Record<string, GitDiffFile>;
}

export interface GitTurnDiffList {
  project_id: string;
  session_id: string;
  repo_root: string | null;
  branch: string | null;
  base_head: string | null;
  turns: GitTurnDiff[];
  cursor: number;
  next_cursor: number;
  has_more: boolean;
  limit: number;
  total: number;
}

export interface CodeReviewTarget {
  changeSetId: string;
  turnIndex: number;
}

export interface ProjectGitDiffStatus {
  project_id: string;
  session_id: string | null;
  work_mode: 'code';
  repo: {
    is_git: boolean;
    repo_root: string | null;
    branch: string | null;
    head: string | null;
    transient: boolean;
  };
  current: GitDiffSummary | null;
  last_turn: GitTurnDiff | null;
  generated_at: number;
}
