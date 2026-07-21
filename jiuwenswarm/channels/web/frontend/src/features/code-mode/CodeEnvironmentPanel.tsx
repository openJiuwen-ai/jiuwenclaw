import { useCallback, useEffect, useRef, useState } from 'react';
import { FileDiff, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ProjectInfo } from '../../types';
import { CodeBranchSelector } from './CodeBranchSelector';
import { gitClient } from './gitClient';
import type { GitDiffStats } from './types';

interface CodeEnvironmentPanelProps {
  project: ProjectInfo;
  sessionId: string;
  isProcessing: boolean;
  onReview: () => void;
}

const EMPTY_STATS: GitDiffStats = {
  files_changed: 0,
  lines_added: 0,
  lines_removed: 0,
};

const DIFF_STATS_POLL_MS = 3000;

function hasDiffStats(stats: GitDiffStats | null | undefined): stats is GitDiffStats {
  return !!stats && (
    stats.files_changed > 0
    || stats.lines_added > 0
    || stats.lines_removed > 0
  );
}

export function CodeEnvironmentPanel({ project, sessionId, isProcessing, onReview }: CodeEnvironmentPanelProps) {
  const { t } = useTranslation();
  const [stats, setStats] = useState<GitDiffStats>(EMPTY_STATS);
  const [loading, setLoading] = useState(false);
  const previousProcessingRef = useRef(isProcessing);
  const loadingRef = useRef(false);

  const loadStats = useCallback(async (options: { silent?: boolean } = {}) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    if (!options.silent) {
      setLoading(true);
    }
    try {
      const status = await gitClient.diffStatus(project.project_id, sessionId);
      const currentStats = status.current?.stats;
      setStats(hasDiffStats(currentStats) ? currentStats : status.last_turn?.stats ?? EMPTY_STATS);
    } catch (error) {
      console.warn('[code-mode] Failed to load environment diff stats', error);
      setStats(EMPTY_STATS);
    } finally {
      loadingRef.current = false;
      if (!options.silent) {
        setLoading(false);
      }
    }
  }, [project.project_id, sessionId]);

  useEffect(() => {
    void loadStats();
    const timer = window.setInterval(() => {
      void loadStats({ silent: true });
    }, DIFF_STATS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadStats]);

  useEffect(() => {
    const completed = previousProcessingRef.current && !isProcessing;
    previousProcessingRef.current = isProcessing;
    if (isProcessing) return;
    if (!completed) return;
    const timer = window.setTimeout(() => void loadStats(), 350);
    return () => window.clearTimeout(timer);
  }, [isProcessing, loadStats]);

  return (
    <section className='code-environment' aria-label={t('codeMode.environment')}>
      <h3 className='code-environment__title'>
        <Info size={15} />
        <span>{t('codeMode.environment')}</span>
      </h3>
      <button type='button' className='code-environment__row' onClick={onReview} title='打开代码审核'>
        <FileDiff size={15} />
        <span>{t('codeMode.changes')}</span>
        <small className='code-environment__stats' aria-live='polite'>
          {loading ? (
            '…'
          ) : (
            <>
              <b className='code-stat-added'>+{stats.lines_added}</b>
              <b className='code-stat-removed'>-{stats.lines_removed}</b>
            </>
          )}
        </small>
      </button>
      <div className='code-environment__row code-environment__row--branch'>
        <CodeBranchSelector project={project} compact variant='environment' disabled={isProcessing} />
      </div>
    </section>
  );
}
