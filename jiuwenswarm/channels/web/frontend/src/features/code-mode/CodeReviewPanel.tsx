import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, ChevronUp, Columns2, FileCode2, Files, Folder, GitBranch, List, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import type { ProjectInfo } from '../../types';
import { gitClient } from './gitClient';
import type { GitDiffFile, GitDiffHunk, GitTurnDiff } from './types';

type DiffViewMode = 'unified' | 'split';
type DiffLineKind = 'added' | 'removed' | 'context' | 'meta';

interface RenderedDiffLine {
  key: string;
  kind: DiffLineKind;
  marker: string;
  content: string;
  oldNumber: number | null;
  newNumber: number | null;
}

interface FileTreeFile {
  type: 'file';
  name: string;
  path: string;
  file: GitDiffFile;
}

interface FileTreeDirectory {
  type: 'directory';
  name: string;
  path: string;
  children: FileTreeNode[];
}

type FileTreeNode = FileTreeFile | FileTreeDirectory;

interface MutableFileTreeDirectory {
  directories: Map<string, MutableFileTreeDirectory>;
  files: FileTreeFile[];
}

const fileTreeCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function buildFileTree(files: GitDiffFile[]): FileTreeNode[] {
  const root: MutableFileTreeDirectory = { directories: new Map(), files: [] };

  files.forEach(file => {
    const parts = file.file_path.split(/[\\/]+/).filter(Boolean);
    const fileName = parts.pop() || file.file_path;
    let current = root;
    parts.forEach(part => {
      let directory = current.directories.get(part);
      if (!directory) {
        directory = { directories: new Map(), files: [] };
        current.directories.set(part, directory);
      }
      current = directory;
    });
    current.files.push({ type: 'file', name: fileName, path: file.file_path, file });
  });

  const materialize = (directory: MutableFileTreeDirectory, parentPath = ''): FileTreeNode[] => {
    const directories = [...directory.directories.entries()]
      .sort(([left], [right]) => fileTreeCollator.compare(left, right))
      .map(([name, child]) => {
        const path = parentPath ? `${parentPath}/${name}` : name;
        return { type: 'directory' as const, name, path, children: materialize(child, path) };
      });
    const childFiles = [...directory.files].sort((left, right) => fileTreeCollator.compare(left.name, right.name));
    return [...directories, ...childFiles];
  };

  return materialize(root);
}

interface FileTreeNodesProps {
  nodes: FileTreeNode[];
  depth?: number;
  selectedPath: string;
  expandedDirectories: Set<string>;
  searchActive: boolean;
  onToggleDirectory: (path: string) => void;
  onSelectFile: (path: string) => void;
}

function FileTreeNodes({ nodes, depth = 0, selectedPath, expandedDirectories, searchActive, onToggleDirectory, onSelectFile }: FileTreeNodesProps) {
  return (
    <>
      {nodes.map(node => {
        const paddingLeft = 8 + depth * 16;
        if (node.type === 'directory') {
          const expanded = searchActive || expandedDirectories.has(node.path);
          return (
            <div key={`directory:${node.path}`} className='code-review__tree-directory'>
              <button
                type='button'
                className='code-review__tree-button code-review__tree-folder'
                style={{ paddingLeft }}
                onClick={() => onToggleDirectory(node.path)}
                aria-expanded={expanded}
                title={node.path}
              >
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <Folder size={16} />
                <span>{node.name}</span>
              </button>
              {expanded ? (
                <FileTreeNodes
                  nodes={node.children}
                  depth={depth + 1}
                  selectedPath={selectedPath}
                  expandedDirectories={expandedDirectories}
                  searchActive={searchActive}
                  onToggleDirectory={onToggleDirectory}
                  onSelectFile={onSelectFile}
                />
              ) : null}
            </div>
          );
        }

        return (
          <button
            type='button'
            key={`file:${node.path}`}
            className={`code-review__tree-button code-review__tree-file${node.path === selectedPath ? ' is-active' : ''}`}
            style={{ paddingLeft }}
            onClick={() => onSelectFile(node.path)}
            title={node.path}
          >
            <FileCode2 size={15} />
            <span>{node.name}</span>
            <small className='code-stat-added'>+{node.file.lines_added}</small>
            <small className='code-stat-removed'>-{node.file.lines_removed}</small>
          </button>
        );
      })}
    </>
  );
}

function renderHunkLines(hunk: GitDiffHunk, hunkIndex: number): RenderedDiffLine[] {
  let oldNumber = hunk.old_start;
  let newNumber = hunk.new_start;
  return hunk.lines.map((line, lineIndex) => {
    const marker = line.charAt(0);
    const content = marker === '+' || marker === '-' || marker === ' ' ? line.slice(1) : line;
    const key = `${hunkIndex}:${lineIndex}`;
    if (marker === '+') {
      const rendered = { key, kind: 'added' as const, marker, content, oldNumber: null, newNumber };
      newNumber += 1;
      return rendered;
    }
    if (marker === '-') {
      const rendered = { key, kind: 'removed' as const, marker, content, oldNumber, newNumber: null };
      oldNumber += 1;
      return rendered;
    }
    if (marker === ' ') {
      const rendered = { key, kind: 'context' as const, marker: ' ', content, oldNumber, newNumber };
      oldNumber += 1;
      newNumber += 1;
      return rendered;
    }
    return { key, kind: 'meta', marker: '', content, oldNumber: null, newNumber: null };
  });
}

function UnifiedDiff({ file }: { file: GitDiffFile }) {
  return (
    <div className='code-diff-table code-diff-table--unified'>
      {file.hunks.map((hunk, hunkIndex) => (
        <div key={`${hunk.old_start}:${hunk.new_start}:${hunkIndex}`} className='code-diff-hunk'>
          <div className='code-diff-hunk__header'>
            @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@
          </div>
          {renderHunkLines(hunk, hunkIndex).map(line => (
            <div key={line.key} className={`code-diff-line code-diff-line--${line.kind}`}>
              <span className='code-diff-line__number'>{line.oldNumber ?? ''}</span>
              <span className='code-diff-line__number'>{line.newNumber ?? ''}</span>
              <span className='code-diff-line__marker'>{line.marker}</span>
              <code>{line.content || ' '}</code>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function SplitDiff({ file }: { file: GitDiffFile }) {
  const rows = useMemo(() => file.hunks.flatMap((hunk, hunkIndex) => renderHunkLines(hunk, hunkIndex)), [file]);

  return (
    <div className='code-diff-table code-diff-table--split'>
      {rows.map(line => (
        <div key={line.key} className='code-diff-split-row'>
          <div className={`code-diff-split-cell code-diff-split-cell--${line.kind === 'added' ? 'empty' : line.kind}`}>
            <span className='code-diff-line__number'>{line.kind === 'added' ? '' : (line.oldNumber ?? '')}</span>
            <span className='code-diff-line__marker'>{line.kind === 'removed' ? '-' : line.kind === 'context' ? ' ' : ''}</span>
            <code>{line.kind === 'added' ? ' ' : line.content || ' '}</code>
          </div>
          <div className={`code-diff-split-cell code-diff-split-cell--${line.kind === 'removed' ? 'empty' : line.kind}`}>
            <span className='code-diff-line__number'>{line.kind === 'removed' ? '' : (line.newNumber ?? '')}</span>
            <span className='code-diff-line__marker'>{line.kind === 'added' ? '+' : line.kind === 'context' ? ' ' : ''}</span>
            <code>{line.kind === 'removed' ? ' ' : line.content || ' '}</code>
          </div>
        </div>
      ))}
    </div>
  );
}

function FileDiff({ file, viewMode }: { file: GitDiffFile; viewMode: DiffViewMode }) {
  if (file.is_binary) return <div className='code-review__empty'>二进制文件不能显示文本差异。</div>;
  if (file.is_large_file && file.hunks.length === 0) return <div className='code-review__empty'>文件过大，后端未返回差异内容。</div>;
  if (file.hunks.length === 0) return <div className='code-review__empty'>该文件没有可显示的差异内容。</div>;
  return viewMode === 'split' ? <SplitDiff file={file} /> : <UnifiedDiff file={file} />;
}

interface CodeReviewPanelProps {
  project: ProjectInfo;
  sessionId: string;
}

export function CodeReviewPanel({ project, sessionId }: CodeReviewPanelProps) {
  const [diff, setDiff] = useState<GitTurnDiff | null>(null);
  const [branch, setBranch] = useState(project.git.branch);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState('');
  const [search, setSearch] = useState('');
  const [filePanelOpen, setFilePanelOpen] = useState(false);
  const [viewMode, setViewMode] = useState<DiffViewMode>('unified');
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => new Set());
  const [expandedDirectories, setExpandedDirectories] = useState<Set<string>>(() => new Set());
  const fileSectionRefs = useRef(new Map<string, HTMLElement>());

  const loadDiff = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = await gitClient.diffStatus(project.project_id, sessionId, {
        includeFiles: true,
        includeHunks: true,
      });
      setBranch(status.repo.branch || project.git.branch);
      const lastTurn = status.last_turn;
      setDiff(lastTurn && Object.keys(lastTurn.files).length > 0 ? lastTurn : null);
    } catch (nextError) {
      setDiff(null);
      setError(nextError instanceof Error ? nextError.message : '加载审核结果失败');
    } finally {
      setLoading(false);
    }
  }, [project, sessionId]);

  useEffect(() => {
    void loadDiff();
  }, [loadDiff]);

  const files = useMemo(() => Object.values(diff?.files ?? {}), [diff]);
  useEffect(() => {
    if (!files.some(file => file.file_path === selectedPath)) setSelectedPath(files[0]?.file_path ?? '');
  }, [files, selectedPath]);

  useEffect(() => {
    const validPaths = new Set(files.map(file => file.file_path));
    setExpandedPaths(previous => {
      const next = new Set([...previous].filter(path => validPaths.has(path)));
      if (next.size === 0 && files[0]) next.add(files[0].file_path);
      return next;
    });
  }, [files]);

  useEffect(() => {
    setExpandedPaths(new Set());
    setExpandedDirectories(new Set());
    fileSectionRefs.current.clear();
  }, [project.project_id, sessionId]);

  const filteredFiles = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return files.filter(file => !query || file.file_path.toLocaleLowerCase().includes(query));
  }, [files, search]);
  const fileTree = useMemo(() => buildFileTree(filteredFiles), [filteredFiles]);
  const selectedFile = files.find(file => file.file_path === selectedPath) ?? files[0];

  const toggleDirectory = (path: string) => {
    setExpandedDirectories(previous => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleFile = (filePath: string) => {
    setSelectedPath(filePath);
    setExpandedPaths(previous => {
      const next = new Set(previous);
      if (next.has(filePath)) next.delete(filePath);
      else next.add(filePath);
      return next;
    });
  };

  const openFileFromSidebar = (filePath: string) => {
    setSelectedPath(filePath);
    setExpandedPaths(previous => new Set(previous).add(filePath));
    window.requestAnimationFrame(() => fileSectionRefs.current.get(filePath)?.scrollIntoView({ block: 'start' }));
  };

  if (loading && !diff) {
    return (
      <div className='code-review-state'>
        <LoaderCircle className='code-mode-spin' size={18} />
        <span>正在加载审核结果…</span>
      </div>
    );
  }

  if (!diff) {
    return (
      <div className='code-review-state'>
        <FileCode2 size={20} />
        <span>{error || '暂无最近一轮代码修改可供审核。'}</span>
        <button type='button' className='code-mode-button' onClick={() => void loadDiff()}>
          重新加载
        </button>
      </div>
    );
  }

  return (
    <section className='code-review code-review--embedded' aria-label='审核代码修改'>
      <div className='code-review__toolbar'>
        <button
          type='button'
          className={filePanelOpen ? 'code-review__icon-button is-active' : 'code-review__icon-button'}
          onClick={() => setFilePanelOpen(open => !open)}
          title='切换文件侧边栏'
        >
          <Files size={17} />
        </button>
        <div className='code-review__branch'>
          <GitBranch size={15} />
          <span>{branch || '当前分支'}</span>
        </div>
        <div className='code-review__summary'>
          <span>{files.length} 个文件已更改</span>
          <span className='code-stat-added'>+{diff.stats.lines_added}</span>
          <span className='code-stat-removed'>-{diff.stats.lines_removed}</span>
        </div>
        <div className='code-review__toolbar-spacer' />
        <button type='button' className='code-review__icon-button' onClick={() => void loadDiff()} title='刷新审核结果'>
          <RefreshCw size={16} />
        </button>
        <button
          type='button'
          className={viewMode === 'unified' ? 'code-review__icon-button is-active' : 'code-review__icon-button'}
          onClick={() => setViewMode('unified')}
          title='统一差异视图'
        >
          <List size={17} />
        </button>
        <button
          type='button'
          className={viewMode === 'split' ? 'code-review__icon-button is-active' : 'code-review__icon-button'}
          onClick={() => setViewMode('split')}
          title='拆分差异视图'
        >
          <Columns2 size={17} />
        </button>
      </div>

      <div className='code-review__body'>
        {filePanelOpen ? (
          <aside className='code-review__files'>
            <label className='code-review__search'>
              <Search size={15} />
              <input value={search} onChange={event => setSearch(event.target.value)} placeholder='搜索文件' />
            </label>
            <div className='code-review__file-list'>
              <FileTreeNodes
                nodes={fileTree}
                selectedPath={selectedFile?.file_path ?? ''}
                expandedDirectories={expandedDirectories}
                searchActive={search.trim().length > 0}
                onToggleDirectory={toggleDirectory}
                onSelectFile={openFileFromSidebar}
              />
            </div>
          </aside>
        ) : null}
        <main className='code-review__diff'>
          {files.length > 0 ? (
            <div className='code-review__diff-content'>
              {files.map(file => {
                const expanded = expandedPaths.has(file.file_path);
                return (
                  <section
                    key={file.file_path}
                    ref={element => {
                      if (element) fileSectionRefs.current.set(file.file_path, element);
                      else fileSectionRefs.current.delete(file.file_path);
                    }}
                    className='code-review__file-section'
                  >
                    <button type='button' className='code-review__file-header' onClick={() => toggleFile(file.file_path)} aria-expanded={expanded}>
                      {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      <span>{file.file_path}</span>
                      <span className='code-stat-added'>+{file.lines_added}</span>
                      <span className='code-stat-removed'>-{file.lines_removed}</span>
                    </button>
                    {expanded ? <FileDiff file={file} viewMode={viewMode} /> : null}
                  </section>
                );
              })}
            </div>
          ) : (
            <div className='code-review__empty'>没有可审核的文件。</div>
          )}
        </main>
      </div>
      <footer className='code-review__footer'>当前展示最近一轮 Agent 修改；后续修改后历史差异可能变化。</footer>
    </section>
  );
}
