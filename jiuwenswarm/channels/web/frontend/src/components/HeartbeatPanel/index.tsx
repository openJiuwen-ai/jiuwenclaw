// src/components/HeartbeatPanel/index.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import type { WebError } from '../../types';
import type {
  HeartbeatCancelResult,
  HeartbeatJobDTO,
  HeartbeatMeta,
  HeartbeatRunNowResult,
  HeartbeatTaskUI,
} from '../../types/heartbeat';
import { summarizeHeartbeatSchedule } from './heartbeatScheduleConvert';
import { heartbeatRunNowMessageKey, heartbeatCancelMessageKey } from './heartbeatStatusText';
import HeartbeatStatusBadge from './HeartbeatStatusBadge';
import HeartbeatTaskDrawer, {
  emptyHeartbeatTaskForm,
  jobToHeartbeatTaskForm,
  type HeartbeatTaskFormValue,
} from './HeartbeatTaskDrawer';
import { scheduleFormToDto } from './heartbeatScheduleConvert';
import ConfirmDialog from '../CronPanel/ConfirmDialog';

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

  const [drawer, setDrawer] = useState<
    | { mode: 'create'; form: HeartbeatTaskFormValue; submitting: boolean; error: string | null }
    | { mode: 'edit'; jobId: string; form: HeartbeatTaskFormValue; submitting: boolean; error: string | null }
    | null
  >(null);

  const openCreateDrawer = useCallback(() => {
    if (!meta) return;
    setDrawer({ mode: 'create', form: emptyHeartbeatTaskForm(meta), submitting: false, error: null });
  }, [meta]);

  const openEditDrawer = useCallback((job: HeartbeatTaskUI) => {
    setDrawer({ mode: 'edit', jobId: job.id, form: jobToHeartbeatTaskForm(job), submitting: false, error: null });
  }, []);

  const [toast, setToast] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<HeartbeatTaskUI | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actingJobId, setActingJobId] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // 有任务处于 running 时，每 3 秒静默刷新一次列表；全部离开 running 后自动停止，
  // 页面隐藏/组件卸载时也停止，见接口规格说明 §7 建议刷新策略
  useEffect(() => {
    const hasRunning = jobs.some((job) => job.status === 'running');
    if (!hasRunning) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void loadAll(controller.signal);
    }, 3000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [jobs, loadAll]);

  const handleToggle = useCallback(
    async (job: HeartbeatTaskUI) => {
      setActingJobId(job.id);
      try {
        await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.toggle', {
          session_id: sessionId,
          id: job.id,
          enabled: !job.enabled,
        });
        setToast(t(job.enabled ? 'heartbeat.toast.paused' : 'heartbeat.toast.resumed'));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const handleRunNow = useCallback(
    async (job: HeartbeatTaskUI) => {
      setActingJobId(job.id);
      try {
        const result = await webRequest<HeartbeatRunNowResult>('heartbeat.job.run_now', {
          session_id: sessionId,
          id: job.id,
          reschedule: false,
        });
        setToast(t(heartbeatRunNowMessageKey(result.accepted, result.reason, result.queued)));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const handleCancel = useCallback(
    async (job: HeartbeatTaskUI, pauseSchedule: boolean) => {
      setActingJobId(job.id);
      try {
        const result = await webRequest<HeartbeatCancelResult>('heartbeat.job.cancel', {
          session_id: sessionId,
          id: job.id,
          pause_schedule: pauseSchedule,
        });
        setToast(t(heartbeatCancelMessageKey(result.cancel_status)));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const result = await webRequest<{ deleted: boolean }>('heartbeat.job.delete', {
        session_id: sessionId,
        id: pendingDelete.id,
      });
      if (!result.deleted) {
        setDeleteError(t('heartbeat.toast.deleteConflict') ?? undefined);
        return;
      }
      setPendingDelete(null);
      const controller = new AbortController();
      await loadAll(controller.signal);
    } catch (err) {
      const webErr = err as WebError;
      if (webErr.code === 'CONFLICT') {
        setDeleteError(t('heartbeat.toast.deleteConflict'));
      } else {
        setDeleteError(webErr.message ?? String(err));
      }
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, sessionId, loadAll, t]);

  const submitDrawer = useCallback(
    async (value: HeartbeatTaskFormValue) => {
      if (!drawer) return;
      setDrawer({ ...drawer, form: value, submitting: true, error: null });
      const payload = {
        name: value.name.trim(),
        prompt: value.prompt.trim(),
        schedule: scheduleFormToDto(value.schedule),
        timezone: value.schedule.timezone,
        enabled: value.enabled,
        concurrency_policy: value.concurrencyPolicy,
        session_deleted_policy: value.sessionDeletedPolicy,
        max_runs: value.maxRuns,
      };
      try {
        if (drawer.mode === 'create') {
          await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.create', { session_id: sessionId, ...payload });
        } else {
          await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.update', {
            session_id: sessionId,
            id: drawer.jobId,
            patch: payload,
          });
        }
        setDrawer(null);
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setDrawer((prev) => (prev ? { ...prev, submitting: false, error: message } : prev));
      }
    },
    [drawer, sessionId, loadAll],
  );

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-overlay-cron-dialog" onClick={onClose}>
      <div
        className="flex h-full w-[520px] max-w-full flex-col bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-bold text-text-strong">{t('heartbeat.panel.title')}</h2>
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!meta}
              onClick={openCreateDrawer}
              className="rounded-full bg-cron-action px-4 py-1.5 text-sm font-bold text-cron-action-foreground hover:bg-cron-action-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {t('heartbeat.panel.create')}
            </button>
            <button onClick={onClose} className="text-text-muted hover:text-text">
              <X size={20} />
            </button>
          </div>
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
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    {job.status === 'running' && (
                      <button
                        type="button"
                        disabled={actingJobId === job.id}
                        onClick={() => void handleCancel(job, false)}
                        className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:opacity-60"
                      >
                        {t('heartbeat.panel.cancelRun')}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={actingJobId === job.id}
                      onClick={() => void handleRunNow(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:opacity-60"
                    >
                      {t('heartbeat.panel.runNow')}
                    </button>
                    <button
                      type="button"
                      disabled={actingJobId === job.id || job.status === 'completed' || job.status === 'expired'}
                      onClick={() => void handleToggle(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {t(job.enabled ? 'heartbeat.panel.pause' : 'heartbeat.panel.resume')}
                    </button>
                    <button
                      type="button"
                      onClick={() => openEditDrawer(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover"
                    >
                      {t('heartbeat.panel.edit')}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDeleteError(null);
                        setPendingDelete(job);
                      }}
                      className="rounded-full border border-red-300 px-3 py-1 text-xs text-red-500 hover:bg-red-50"
                    >
                      {t('heartbeat.panel.delete')}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {drawer && meta && (
          <div className="border-t border-border">
            <HeartbeatTaskDrawer
              mode={drawer.mode}
              initial={drawer.form}
              meta={meta}
              submitting={drawer.submitting}
              error={drawer.error}
              onSubmit={submitDrawer}
              onCancel={() => setDrawer(null)}
            />
          </div>
        )}
      </div>
      {toast && (
        <div className="pointer-events-none fixed bottom-6 right-6 z-50 rounded-md bg-text-strong px-4 py-2 text-sm text-card shadow-lg">
          {toast}
        </div>
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={t('heartbeat.panel.delete')}
          message={deleteError ?? t('heartbeat.panel.deleteConfirm', { name: pendingDelete.name })}
          loading={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
