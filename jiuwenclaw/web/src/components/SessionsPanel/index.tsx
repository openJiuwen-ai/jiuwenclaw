import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileViewer } from '../AgentPanel/FileViewer';
import { containsIgnoredDirectory } from '../../features/fileTreeFilters';
import { webRequest } from '../../services/webClient';

interface SessionsPanelProps {
  currentSessionId: string;
}

interface SessionListResponse {
  sessions?: unknown[];
}

interface SessionFileItem {
  name: string;
  path: string;
  isMarkdown: boolean;
  isDirectory: boolean;
  depth: number;
}

interface ListFilesResponse {
  files?: unknown[];
}

function formatDateTime(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function isPlausibleDate(date: Date): boolean {
  const year = date.getFullYear();
  return year >= 2020 && year <= 2100;
}

function parseSessionDisplayLabel(sessionId: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (!sessionId) return t('sessions.unknownSession');

  // 处理以 sess_、cron_、feishu_、xiaoyi_、dingtalk_ 开头的会话ID
  const prefixes = ['sess_', 'cron_', 'feishu_', 'xiaoyi_', 'dingtalk_'];
  const prefixMap: Record<string, string> = {
    'sess_': t('sessions.prefixes.session'),
    'cron_': t('sessions.prefixes.cron'),
    'feishu_': t('sessions.prefixes.feishu'),
    'xiaoyi_': t('sessions.prefixes.xiaoyi'),
    'dingtalk_': t('sessions.prefixes.dingtalk')
  };
  
  for (const prefix of prefixes) {
    if (sessionId.startsWith(prefix)) {
      const parts = sessionId.split('_');
      const hexTs = parts[1] ?? '';
      if (/^[0-9a-fA-F]+$/.test(hexTs)) {
        const ms = Number.parseInt(hexTs, 16);
        if (Number.isFinite(ms)) {
          const date = new Date(ms);
          if (!Number.isNaN(date.getTime()) && isPlausibleDate(date)) {
            return `${prefixMap[prefix]}-${formatDateTime(date)}`;
          }
        }
      }
      return `${prefixMap[prefix]}-${t('sessions.unknownTime')}`;
    }
  }

  if (sessionId.startsWith('heartbeat_')) {
    const rawBody = sessionId.slice('heartbeat_'.length);
    return rawBody ? `${t('sessions.prefixes.heartbeat')}-${rawBody}` : t('sessions.prefixes.heartbeat');
  }

  // 解析会话ID中可能包含的时间戳格式，如 YYYYMMDD_HHMMSS_xxxx
  const timestampRegex = /(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/;
  const match = sessionId.match(timestampRegex);
  if (match) {
    const [, year, month, day, hours, minutes, seconds] = match;
    const date = new Date(`${year}-${month}-${day} ${hours}:${minutes}:${seconds}`);
    if (!Number.isNaN(date.getTime()) && isPlausibleDate(date)) {
      const prefix = sessionId.includes('_') ? sessionId.split('_')[0] : t('sessions.prefixes.unknown');
      return `${prefix}-${formatDateTime(date)}`;
    }
  }

  const prefix = sessionId.includes('_') ? sessionId.split('_')[0] : t('sessions.prefixes.unknown');
  return `${prefix}-${t('sessions.unknownTime')}`;
}

function toSessionIds(raw: unknown[]): string[] {
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

function toSessionFiles(raw: unknown[]): SessionFileItem[] {
  const rows: SessionFileItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const name = rec.name;
    const path = rec.path;
    const isMarkdown = rec.isMarkdown;
    const isDirectory = rec.isDirectory;
    if (
      typeof name !== 'string' ||
      typeof path !== 'string' ||
      typeof isMarkdown !== 'boolean' ||
      typeof isDirectory !== 'boolean'
    ) {
      continue;
    }
    rows.push({ name, path, isMarkdown, isDirectory, depth: 0 });
  }
  return rows;
}

export function SessionsPanel({ currentSessionId }: SessionsPanelProps) {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<string[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [files, setFiles] = useState<SessionFileItem[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<SessionFileItem | null>(null);

  const loadSessions = async () => {
    setLoadingSessions(true);
    try {
      const payload = await webRequest<SessionListResponse>('session.list', { limit: 20 });
      const rows = Array.isArray(payload?.sessions) ? toSessionIds(payload.sessions) : [];
      setSessions(rows);
      setSessionsError(null);
      setSelectedSessionId((prev) => {
        if (prev && rows.includes(prev)) return prev;
        if (currentSessionId && rows.includes(currentSessionId)) return currentSessionId;
        return rows[0] ?? null;
      });
    } catch (error) {
      console.error('Failed to load sessions:', error);
      setSessions([]);
      setSessionsError(t('sessions.errors.loadSessions'));
      setSelectedSessionId(null);
    } finally {
      setLoadingSessions(false);
    }
  };

  useEffect(() => {
    void loadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t]);

  useEffect(() => {
    if (!selectedSessionId) {
      setFiles([]);
      setFilesError(null);
      setSelectedFile(null);
      return;
    }
    const loadFiles = async () => {
      setLoadingFiles(true);
      setFilesError(null);
      setSelectedFile(null);
      try {
        const fetchDirEntries = async (dir: string, depth: number): Promise<SessionFileItem[]> => {
          const encodedDir = encodeURIComponent(dir);
          const resp = await fetch(`/file-api/list-files?dir=${encodedDir}`);
          if (!resp.ok) {
            const text = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${text.substring(0, 120)}`);
          }
          const payload = (await resp.json()) as ListFilesResponse;
          const rows = Array.isArray(payload?.files) ? toSessionFiles(payload.files) : [];
          const withDepth = rows.map((item) => ({ ...item, depth }));
          const result: SessionFileItem[] = [];
          for (const item of withDepth) {
            if (containsIgnoredDirectory(item.path)) {
              continue;
            }
            result.push(item);
            if (!item.isDirectory) continue;
            const children = await fetchDirEntries(item.path, depth + 1);
            result.push(...children);
          }
          return result;
        };

        const rootDir = `workspace/session/${selectedSessionId}`;
        const rows = await fetchDirEntries(rootDir, 0);
        setFiles(rows);
      } catch (error) {
        console.error('Failed to load session files:', error);
        setFiles([]);
        setFilesError(t('sessions.errors.loadFiles'));
      } finally {
        setLoadingFiles(false);
      }
    };
    void loadFiles();
  }, [selectedSessionId, t]);

  const handleDeleteSession = async (sessionId: string) => {
    const displayLabel = parseSessionDisplayLabel(sessionId, t);
    const confirmed = window.confirm(t('sessions.deleteConfirm', { session: displayLabel }));
    if (!confirmed) return;

    setDeletingSessionId(sessionId);
    try {
      await webRequest('session.delete', { session_id: sessionId });
      await loadSessions();
      if (selectedSessionId === sessionId) {
        setSelectedFile(null);
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
      setSessionsError(t('sessions.errors.deleteSession', { sessionId }));
    } finally {
      setDeletingSessionId(null);
    }
  };

  const selectedSessionLabel = useMemo(
    () => (selectedSessionId ? parseSessionDisplayLabel(selectedSessionId, t) : t('sessions.noneSelected')),
    [selectedSessionId, t]
  );

  return (
    <div className="flex-1 min-h-0">
      <div className="card w-full h-full flex flex-col">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold">{t('sessions.title')}</h2>
            <p className="text-sm text-text-muted mt-1">{t('sessions.subtitle')}</p>
          </div>
          <button
            type="button"
            onClick={() => void loadSessions()}
            disabled={loadingSessions}
            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loadingSessions ? t('common.refreshing') : t('common.refresh')}
          </button>
        </div>

        {sessionsError ? (
          <div className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {sessionsError}
          </div>
        ) : null}

        <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,1fr)_minmax(0,4fr)] gap-4">
          <div className="rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col min-h-0">
            <div className="px-4 py-3 bg-secondary/30 border-b border-border">
              <div>
                <h3 className="text-sm font-medium text-text">{t('sessions.history')}</h3>
                <p className="text-xs text-text-muted mt-1 mono">
                  {t('sessions.count', { count: sessions.length })}
                </p>
              </div>
            </div>
            <div className="flex-1 overflow-auto p-2 space-y-1">
              {!loadingSessions && sessions.length === 0 ? (
                <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('sessions.empty')}</div>
              ) : (
                sessions.map((sessionId) => (
                  <div key={sessionId} className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
                    <button
                      type="button"
                      className={`w-full min-w-0 text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                        selectedSessionId === sessionId
                          ? 'border-[var(--border-accent)] bg-accent-subtle text-text'
                          : 'border-transparent hover:bg-secondary/40 text-text-muted hover:text-text'
                      }`}
                      onClick={() => setSelectedSessionId(sessionId)}
                      title={`${parseSessionDisplayLabel(sessionId, t)} (${sessionId})`}
                    >
                      <span className="truncate block">{parseSessionDisplayLabel(sessionId, t)}</span>
                    </button>
                    <button
                      type="button"
                      title={t('sessions.delete')}
                      className="shrink-0 p-1.5 rounded-md text-text-muted hover:text-danger hover:bg-danger-subtle transition-colors disabled:opacity-50"
                      disabled={deletingSessionId === sessionId}
                      onClick={() => void handleDeleteSession(sessionId)}
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                      </svg>
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm grid grid-cols-[minmax(0,1fr)_minmax(0,3fr)] min-h-0">
            <div className="border-r border-border flex flex-col min-h-0">
              <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                <div>
                  <h3 className="text-sm font-medium text-text">{t('sessions.files')}</h3>
                  <p className="text-xs text-text-muted mt-1 truncate" title={selectedSessionLabel}>
                    {selectedSessionLabel}
                  </p>
                </div>
              </div>
              <div className="flex-1 overflow-auto p-2">
                {!selectedSessionId ? (
                  <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('sessions.selectFirst')}</div>
                ) : loadingFiles ? (
                  <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('sessions.loadingFiles')}</div>
                ) : filesError ? (
                  <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">{filesError}</div>
                ) : files.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('sessions.emptyFiles')}</div>
                ) : (
                  <div className="space-y-1">
                    {files.map((file) => {
                      const canPreview = !file.isDirectory;
                      return (
                        <button
                          key={file.path}
                          type="button"
                          className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                            canPreview && selectedFile?.path === file.path
                              ? 'border-[var(--border-accent)] bg-accent-subtle text-text'
                              : 'border-transparent text-text-muted'
                          } ${canPreview ? 'hover:bg-secondary/40 hover:text-text' : 'cursor-default'}`}
                          onClick={() => {
                            if (!canPreview) return;
                            setSelectedFile(file);
                          }}
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span
                              className="truncate block"
                              style={{ paddingLeft: `${file.depth * 16}px` }}
                            >
                              {file.name}
                            </span>
                            {file.isDirectory ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-secondary/50 text-text-muted">
                                {t('sessions.folder')}
                              </span>
                            ) : !file.isMarkdown ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-secondary/50 text-text-muted">
                                {t('sessions.notPreviewable')}
                              </span>
                            ) : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
            <div className="flex-1 min-h-0">
              {selectedFile ? (
                <FileViewer filePath={selectedFile.path} fileName={selectedFile.name} />
              ) : (
                <div className="h-full flex items-center justify-center text-text-muted">
                  {t('sessions.selectFile')}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
