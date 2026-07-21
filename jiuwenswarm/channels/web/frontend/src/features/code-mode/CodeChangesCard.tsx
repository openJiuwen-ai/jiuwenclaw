import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, FileCode2, RefreshCw } from 'lucide-react';
import type { ProjectInfo } from '../../types';
import { gitClient } from './gitClient';
import type { GitTurnDiff } from './types';

interface CodeChangesCardProps {
  project: ProjectInfo | null;
  sessionId: string;
  isProcessing: boolean;
  onReview: () => void;
}

export function CodeChangesCard({ project, sessionId, isProcessing, onReview }: CodeChangesCardProps) {
  const [diff, setDiff] = useState<GitTurnDiff | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const previousProcessingRef = useRef(isProcessing);

  const loadSummary = useCallback(async () => {
    if (!project || project.work_mode !== 'code' || project.is_default) {
      setDiff(null);
      return;
    }
    setLoading(true);
    try {
      const status = await gitClient.diffStatus(project.project_id, sessionId, { includeFiles: true });
      const lastTurn = status.last_turn;
      setDiff(lastTurn && Object.keys(lastTurn.files).length > 0 ? lastTurn : null);
    } catch (error) {
      console.warn('[code-mode] Failed to load last turn diff', error);
      setDiff(null);
    } finally {
      setLoading(false);
    }
  }, [project, sessionId]);

  useEffect(() => {
    const completed = previousProcessingRef.current && !isProcessing;
    previousProcessingRef.current = isProcessing;
    if (isProcessing) return;
    const timer = window.setTimeout(
      () => {
        void loadSummary();
      },
      completed ? 350 : 0
    );
    return () => window.clearTimeout(timer);
  }, [isProcessing, loadSummary]);

  if (!project || project.work_mode !== 'code' || (loading && !diff) || !diff) return null;
  const files = Object.values(diff.files);
  const visibleFiles = expanded ? files : files.slice(0, 3);

  return (
    <section className='code-changes-card' aria-label='已编辑文件'>
      <div className='code-changes-card__header'>
        <span className='code-changes-card__icon'>
          <FileCode2 size={20} />
        </span>
        <div className='code-changes-card__heading'>
          <strong>已编辑文件</strong>
          <span>
            <b className='code-stat-added'>+{diff.stats.lines_added}</b>
            <b className='code-stat-removed'>-{diff.stats.lines_removed}</b>
          </span>
        </div>
        <button type='button' className='code-changes-card__refresh' onClick={() => void loadSummary()} title='刷新修改统计'>
          <RefreshCw size={15} />
        </button>
        <button type='button' className='code-changes-card__review' onClick={onReview}>
          审核
        </button>
      </div>
      <div className='code-changes-card__files'>
        {visibleFiles.map(file => (
          <button type='button' key={file.file_path} onClick={onReview}>
            <span>{file.file_path}</span>
            <small className='code-stat-added'>+{file.lines_added}</small>
            <small className='code-stat-removed'>-{file.lines_removed}</small>
          </button>
        ))}
      </div>
      {files.length > 3 ? (
        <button type='button' className='code-changes-card__expand' onClick={() => setExpanded(value => !value)}>
          {expanded ? '收起文件' : `显示全部 ${files.length} 个文件`}
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      ) : null}
    </section>
  );
}
