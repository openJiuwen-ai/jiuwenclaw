import { useState } from 'react';
import { ChevronDown, ChevronUp, FileCode2, RefreshCw } from 'lucide-react';
import type { CodeReviewTarget, GitTurnDiff } from './types';

interface CodeChangesCardProps {
  diff: GitTurnDiff;
  refreshing?: boolean;
  onRefresh: () => void;
  onReview: (target: CodeReviewTarget) => void;
}

export function CodeChangesCard({ diff, refreshing = false, onRefresh, onReview }: CodeChangesCardProps) {
  const [expanded, setExpanded] = useState(false);
  const files = Object.values(diff.files);
  const visibleFiles = expanded ? files : files.slice(0, 3);
  const reviewTarget: CodeReviewTarget = {
    changeSetId: diff.change_set_id,
    turnIndex: diff.turn_index,
  };
  const discarded = diff.status === 'discarded';

  if (files.length === 0) return null;

  return (
    <section className={`code-changes-card${discarded ? ' is-discarded' : ''}`} aria-label='已编辑文件'>
      <div className='code-changes-card__header'>
        <span className='code-changes-card__icon'>
          <FileCode2 size={20} />
        </span>
        <div className='code-changes-card__heading'>
          <strong>已编辑文件</strong>
          <span>
            <b className='code-stat-added'>+{diff.stats.lines_added}</b>
            <b className='code-stat-removed'>-{diff.stats.lines_removed}</b>
            {discarded ? <b className='code-changes-card__status'>已撤销</b> : null}
          </span>
        </div>
        <button
          type='button'
          className='code-changes-card__refresh'
          onClick={onRefresh}
          disabled={refreshing}
          title='刷新修改历史'
        >
          <RefreshCw className={refreshing ? 'code-mode-spin' : undefined} size={15} />
        </button>
        <button type='button' className='code-changes-card__review' onClick={() => onReview(reviewTarget)}>
          审核
        </button>
      </div>
      <div className='code-changes-card__files'>
        {visibleFiles.map(file => (
          <button type='button' key={file.file_path} onClick={() => onReview(reviewTarget)}>
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
