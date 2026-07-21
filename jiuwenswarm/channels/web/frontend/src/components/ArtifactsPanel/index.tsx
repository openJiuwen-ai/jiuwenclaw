import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { Download, FileText, Info, LoaderCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useChatStore, useSessionStore } from '../../stores';
import type { FileDownloadItem, Message } from '../../types';
import type { TeamMemberExecutionEvent, TeamTask } from '../../stores/sessionStore';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { getMemberDisplayName } from '../teamArea/shared';

type ArtifactSource = 'message' | 'team_execution' | 'team_task';

export interface ArtifactItem {
  id: string;
  name: string;
  size?: number;
  mimeType?: string;
  downloadUrl?: string;
  downloadToken?: string;
  path?: string;
  source: ArtifactSource;
  sourceMember?: string;
  timestamp?: number;
}

type PreviewState =
  | { status: 'idle'; content: string; error?: string }
  | { status: 'loading'; content: string; error?: string }
  | { status: 'ready'; content: string; error?: string }
  | { status: 'error'; content: string; error: string };

type DownloadCapableWindow = Window & {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => Promise<boolean> | boolean;
    };
  };
};

const MARKDOWN_EXTENSIONS = new Set(['md', 'markdown']);

function formatFileSize(bytes?: number): string {
  if (bytes == null || !Number.isFinite(bytes)) return '-';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, index);
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function getFileExtension(name: string): string {
  const ext = name.split('.').pop()?.trim().toLowerCase() || '';
  return ext === name.toLowerCase() ? '' : ext;
}

function getFileTypeLabel(item: ArtifactItem): string {
  const ext = getFileExtension(item.name);
  if (ext) return ext.toUpperCase();
  if (item.mimeType) return item.mimeType;
  return 'FILE';
}

function isMarkdownArtifact(item: ArtifactItem): boolean {
  return MARKDOWN_EXTENSIONS.has(getFileExtension(item.name));
}

function decodeBase64UrlUtf8(value: string): string | null {
  try {
    const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
    const binary = window.atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

function getTokenPayload(token?: string): Record<string, unknown> | null {
  if (!token) return null;
  const payloadPart = token.split('.')[0];
  if (!payloadPart) return null;
  const decoded = decodeBase64UrlUtf8(payloadPart);
  if (!decoded) return null;
  try {
    const payload = JSON.parse(decoded);
    return payload && typeof payload === 'object' ? payload as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function extractTokenFromDownloadUrl(downloadUrl?: string): string | undefined {
  if (!downloadUrl) return undefined;
  try {
    const url = new URL(downloadUrl, window.location.origin);
    return url.searchParams.get('token') || undefined;
  } catch {
    return undefined;
  }
}

function resolveArtifactPath(downloadToken?: string, downloadUrl?: string): string | undefined {
  const token = downloadToken || extractTokenFromDownloadUrl(downloadUrl);
  const payload = getTokenPayload(token);
  const path = payload?.path;
  return typeof path === 'string' && path.trim() ? path : undefined;
}

function normalizeDownloadUrl(downloadUrl?: string, downloadToken?: string): string | undefined {
  if (downloadUrl) return downloadUrl;
  if (downloadToken) return `/file-api/download?token=${encodeURIComponent(downloadToken)}`;
  return undefined;
}

function messageTime(message: Message): number {
  const parsed = Date.parse(message.timestamp);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fileItemToArtifact(file: FileDownloadItem, message: Message, index: number): ArtifactItem {
  const downloadToken = file.download_token || extractTokenFromDownloadUrl(file.download_url);
  return {
    id: `message:${message.id}:${file.name}:${index}`,
    name: file.name || `artifact-${index + 1}`,
    size: file.size,
    mimeType: file.mime_type,
    downloadUrl: normalizeDownloadUrl(file.download_url, downloadToken),
    downloadToken,
    path: resolveArtifactPath(downloadToken, file.download_url),
    source: 'message',
    timestamp: messageTime(message),
  };
}

function executionFileToArtifact(
  file: NonNullable<TeamMemberExecutionEvent['files']>[number],
  event: TeamMemberExecutionEvent,
  index: number
): ArtifactItem {
  const downloadToken = extractTokenFromDownloadUrl(file.download_url);
  return {
    id: `team-execution:${event.id}:${file.name}:${index}`,
    name: file.name || `artifact-${index + 1}`,
    size: file.size,
    mimeType: file.mime_type,
    downloadUrl: normalizeDownloadUrl(file.download_url, downloadToken),
    downloadToken,
    path: resolveArtifactPath(downloadToken, file.download_url),
    source: 'team_execution',
    sourceMember: event.member_id,
    timestamp: event.timestamp,
  };
}

function taskFileToArtifact(task: TeamTask, filePath: string, index: number): ArtifactItem {
  const normalizedPath = filePath.trim();
  const name = normalizedPath.split(/[\\/]/).pop() || normalizedPath || `artifact-${index + 1}`;
  return {
    id: `team-task:${task.task_id}:${normalizedPath}:${index}`,
    name,
    path: normalizedPath || undefined,
    source: 'team_task',
    sourceMember: task.assignee,
    timestamp: task.timestamp,
  };
}

function buildArtifacts(
  messages: Message[],
  executionEvents: TeamMemberExecutionEvent[],
  teamTasks: TeamTask[]
): ArtifactItem[] {
  const artifacts: ArtifactItem[] = [];

  messages.forEach((message) => {
    message.fileItems?.forEach((file, index) => {
      artifacts.push(fileItemToArtifact(file, message, index));
    });
  });

  executionEvents.forEach((event) => {
    event.files?.forEach((file, index) => {
      artifacts.push(executionFileToArtifact(file, event, index));
    });
  });

  teamTasks.forEach((task) => {
    task.files?.forEach((filePath, index) => {
      artifacts.push(taskFileToArtifact(task, filePath, index));
    });
  });

  const deduped = new Map<string, ArtifactItem>();
  artifacts.forEach((artifact) => {
    const key = [
      artifact.path || '',
      artifact.downloadUrl || '',
      artifact.name,
      artifact.sourceMember || '',
    ].join('\n');
    const existing = deduped.get(key);
    if (!existing || (artifact.timestamp || 0) >= (existing.timestamp || 0)) {
      deduped.set(key, artifact);
    }
  });

  return Array.from(deduped.values()).sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
}

function isHtmlFallback(contentType: string, text: string): boolean {
  const normalizedType = contentType.toLowerCase();
  const start = text.slice(0, 500).trim().toLowerCase();
  return (
    normalizedType.includes('text/html') ||
    start.startsWith('<!doctype html') ||
    start.startsWith('<html') ||
    (start.includes('<div id="root"') && start.includes('/src/'))
  );
}

async function fetchMarkdownPreview(item: ArtifactItem): Promise<string> {
  const urls: string[] = [];
  if (item.path) {
    urls.push(`/file-api/file-content?path=${encodeURIComponent(item.path)}&encoding=auto`);
  }
  if (item.downloadUrl) {
    urls.push(item.downloadUrl);
  }
  if (urls.length === 0) {
    throw new Error('missing_readable_path');
  }

  let lastError: unknown = null;
  for (const url of urls) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      const text = await response.text();
      if (!response.ok) {
        lastError = new Error(`HTTP ${response.status}`);
        continue;
      }
      if (isHtmlFallback(response.headers.get('content-type') || '', text)) {
        lastError = new Error('html_fallback');
        continue;
      }
      return text;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError instanceof Error ? lastError : new Error('preview_failed');
}

export function useSessionArtifacts(): ArtifactItem[] {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const messages = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages ?? []);
  const executionEvents = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamMemberExecutionEvents ?? []);
  const teamTasks = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamTasks ?? []);

  return useMemo(
    () => buildArtifacts(messages, executionEvents, teamTasks),
    [executionEvents, messages, teamTasks]
  );
}

export function useSessionArtifactsCount(): number {
  return useSessionArtifacts().length;
}

export function ArtifactsPanel({ className }: { className?: string }) {
  const { t } = useTranslation();
  const artifacts = useSessionArtifacts();
  const [selectedId, setSelectedId] = useState<string>('');
  const [previewState, setPreviewState] = useState<PreviewState>({ status: 'idle', content: '' });
  const [listWidth, setListWidth] = useState(320);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedId) || artifacts[0] || null;
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    if (!selectedArtifact) {
      setSelectedId('');
      setPreviewState({ status: 'idle', content: '' });
      return;
    }
    if (selectedArtifact.id !== selectedId) {
      setSelectedId(selectedArtifact.id);
    }
  }, [selectedArtifact, selectedId]);

  useEffect(() => {
    if (!selectedArtifact) return;
    if (!isMarkdownArtifact(selectedArtifact)) {
      setPreviewState({ status: 'idle', content: '' });
      return;
    }

    let cancelled = false;
    setPreviewState({ status: 'loading', content: '' });
    void fetchMarkdownPreview(selectedArtifact)
      .then((content) => {
        if (!cancelled) {
          setPreviewState({ status: 'ready', content });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          const key = error instanceof Error && error.message === 'missing_readable_path'
            ? 'artifacts.previewMissingPath'
            : 'artifacts.previewFailed';
          setPreviewState({ status: 'error', content: '', error: t(key) });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedArtifact, t]);

  const handleDownload = async (artifact: ArtifactItem) => {
    const downloadUrl = artifact.downloadUrl || (artifact.path ? `/file-api/file-content?path=${encodeURIComponent(artifact.path)}` : '');
    if (!downloadUrl) return;

    const pywebviewApi = (window as DownloadCapableWindow).pywebview?.api;
    if (pywebviewApi?.download_file) {
      const success = await pywebviewApi.download_file(downloadUrl, artifact.name || 'download');
      if (!success) {
        console.error('Download failed via pywebview API');
      }
      return;
    }

    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = artifact.name || '';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleDividerMouseDown = (event: ReactMouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragStateRef.current = { startX: event.clientX, startWidth: listWidth };

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const state = dragStateRef.current;
      if (!state) return;
      setListWidth(Math.min(520, Math.max(240, state.startWidth + moveEvent.clientX - state.startX)));
    };
    const handleMouseUp = () => {
      dragStateRef.current = null;
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  };

  return (
    <section className={clsx('flex min-h-0 min-w-0 flex-1 overflow-hidden bg-card', className)}>
      <aside
        className="flex shrink-0 flex-col overflow-hidden border-border bg-card"
        style={{ width: listWidth }}
      >
        <div className="shrink-0 px-4 py-3">
          <div className="text-sm font-semibold text-text">{t('artifacts.title')}</div>
          <div className="mt-1 text-xs text-text-muted">
            {t('artifacts.count', { count: artifacts.length })}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {artifacts.length === 0 ? (
            <div className="flex h-full items-center justify-center px-5 text-center text-sm text-text-muted">
              {t('artifacts.empty')}
            </div>
          ) : (
            <div className="space-y-2">
              {artifacts.map((artifact) => {
                const selected = selectedArtifact?.id === artifact.id;
                return (
                  <button
                    key={artifact.id}
                    type="button"
                    className={clsx(
                      'w-full rounded-md border px-3 py-2.5 text-left ',
                      selected
                        ? 'border-accent bg-accent-subtle'
                        : 'border-border bg-card hover:border-border-hover hover:bg-secondary'
                    )}
                    onClick={() => setSelectedId(artifact.id)}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary text-text-muted">
                        <FileText size={15} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-text">{artifact.name}</span>
                        <span className="mt-1 flex min-w-0 items-center gap-2 text-xs text-text-muted">
                          <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px]">
                            {getFileTypeLabel(artifact)}
                          </span>
                          <span className="shrink-0">{formatFileSize(artifact.size)}</span>
                          {artifact.sourceMember && (
                            <span className="truncate">{getMemberDisplayName(artifact.sourceMember)}</span>
                          )}
                        </span>
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      <div
        className="resize-divider"
        onMouseDown={handleDividerMouseDown}
      />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-card">
        {selectedArtifact ? (
          <>
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-5 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-text">{selectedArtifact.name}</div>
                <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-text-muted">
                  <span>{getFileTypeLabel(selectedArtifact)}</span>
                  <span>|</span>
                  <span>{formatFileSize(selectedArtifact.size)}</span>
                  {selectedArtifact.sourceMember && (
                    <>
                      <span>|</span>
                      <span className="truncate">{getMemberDisplayName(selectedArtifact.sourceMember)}</span>
                    </>
                  )}
                </div>
              </div>
              <button
                type="button"
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-muted  hover:bg-secondary hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
                title={t('artifacts.download')}
                aria-label={t('artifacts.download')}
                disabled={!selectedArtifact.downloadUrl && !selectedArtifact.path}
                onClick={() => {
                  void handleDownload(selectedArtifact);
                }}
              >
                <Download size={16} />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              {!isMarkdownArtifact(selectedArtifact) ? (
                <PreviewNotice title={t('artifacts.previewUnsupported')} />
              ) : previewState.status === 'loading' ? (
                <div className="flex h-full items-center justify-center gap-2 text-sm text-text-muted">
                  <LoaderCircle size={16} className="animate-spin" />
                  {t('common.loading')}
                </div>
              ) : previewState.status === 'error' ? (
                <PreviewNotice title={previewState.error} />
              ) : (
                <MarkdownRenderer
                  content={previewState.content}
                  className="chat-text chat-markdown max-w-none"
                  testId="artifact-markdown-preview"
                />
              )}
            </div>
          </>
        ) : (
          <PreviewNotice title={t('artifacts.selectArtifact')} fill />
        )}
      </div>
    </section>
  );
}

function PreviewNotice({ title, fill = false }: { title: string; fill?: boolean }) {
  return (
    <div className={clsx('flex items-center justify-center text-sm text-text-muted', fill ? 'h-full' : 'min-h-[240px]')}>
      <div className="flex items-center gap-2 rounded-md border border-border bg-secondary px-3 py-2">
        <Info size={15} />
        <span>{title}</span>
      </div>
    </div>
  );
}
