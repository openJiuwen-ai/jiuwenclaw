// src/components/HeartbeatPanel/index.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import type { WebError } from '../../types';
import type { HeartbeatJobDTO, HeartbeatMeta, HeartbeatTaskUI } from '../../types/heartbeat';
import { summarizeHeartbeatSchedule } from './heartbeatScheduleConvert';
import HeartbeatStatusBadge from './HeartbeatStatusBadge';

interface HeartbeatPanelProps {
  sessionId: string;
  onClose: () => void;
}

function heartbeatJobToUI(job: HeartbeatJobDTO): HeartbeatTaskUI {
  return {
    id: job.id,
    name: job.name,
    prompt: job.prompt,
    enabled: job.enabled,
    status: job.status,
    schedule: job.schedule,
    timezone: job.timezone,
    concurrencyPolicy: job.concurrency_policy,
    sessionDeletedPolicy: job.session_deleted_policy,
    maxRuns: job.max_runs,
    createdAt: job.created_at,
    updatedAt: job.updated_at,
    nextRunAt: job.next_run_at,
    lastRunAt: job.last_run_at,
    runCount: job.run_count,
    runState: job.run_state,
  };
}

export default function HeartbeatPanel({ sessionId, onClose }: HeartbeatPanelProps) {
  const { t } = useTranslation();
  const [meta, setMeta] = useState<HeartbeatMeta | null>(null);
  const [jobs, setJobs] = useState<HeartbeatTaskUI[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 会话切换/组件卸载时中止未完成请求，避免旧会话的响应覆盖新会话状态，见接口规格说明 §16.3
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const loadAll = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const [metaPayload, listPayload] = await Promise.all([
        webRequest<HeartbeatMeta>('heartbeat.job.meta', { session_id: sessionId }, { signal }),
        webRequest<{ jobs: HeartbeatJobDTO[] }>('heartbeat.job.list', { session_id: sessionId }, { signal }),
      ]);
      if (sessionIdRef.current !== sessionId) return; // 会话已切换，丢弃过期响应
      setMeta(metaPayload);
      setJobs((listPayload.jobs ?? []).map(heartbeatJobToUI));
    } catch (err) {
      if (typeof err === 'object' && err !== null && 'code' in err && (err as WebError).code === 'REQUEST_ABORTED') return;
      if (sessionIdRef.current !== sessionId) return;
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      if (sessionIdRef.current === sessionId) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadAll(controller.signal);
    return () => controller.abort();
  }, [loadAll]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-overlay-cron-dialog" onClick={onClose}>
      <div
        className="flex h-full w-[520px] max-w-full flex-col bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-bold text-text-strong">{t('heartbeat.panel.title')}</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && <p className="text-sm text-text-muted">{t('heartbeat.panel.loading')}</p>}
          {!loading && loadError && <p className="text-sm text-red-500">{loadError}</p>}
          {!loading && !loadError && jobs.length === 0 && (
            <p className="text-sm text-text-muted">{t('heartbeat.panel.empty')}</p>
          )}
          {!loading && !loadError && jobs.length > 0 && meta && (
            <ul className="space-y-3">
              {jobs.map((job) => (
                <li key={job.id} className="rounded-lg border border-border p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-text-strong">{job.name}</span>
                    <HeartbeatStatusBadge status={job.status} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-text-muted">{job.prompt}</p>
                  <p className="mt-1 text-xs text-text-muted">{summarizeHeartbeatSchedule(job.schedule, t)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
