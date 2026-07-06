/**
 * HistoryPanel —— Web Pod 本地会话历史浏览（只读）。
 *
 * 列表态：GET /api/sessions，展示标题 / 预览 / 时间 / 消息数。
 * 详情态：GET /api/sessions/{id}，复用 MessageItem 渲染完整对话。
 * 数据源为 Web Pod 本地 SQLite，独立于 WS（AgentServer）链路。
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import {
  fetchSessionDetail,
  fetchSessions,
  type HistoryDetail,
  type HistoryMessage,
  type HistorySession,
} from '../../services/api';
import type { Message } from '../../types';
import { formatRelativeTime } from '../../utils';
import { MessageItem } from '../ChatPanel/MessageItem';

function toMessage(msg: HistoryMessage, sessionId: string, index: number): Message {
  return {
    id: msg.request_id || `${sessionId}-${index}`,
    role: msg.role,
    content: msg.content,
    timestamp: new Date(msg.timestamp * 1000).toISOString(),
  };
}

const BTN =
  'px-3 py-1.5 rounded-md border border-border text-sm text-text hover:bg-secondary transition-colors';

export function HistoryPanel() {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [errorList, setErrorList] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setLoadingList(true);
    setErrorList(null);
    try {
      setSessions(await fetchSessions());
    } catch (e) {
      setErrorList(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const openDetail = useCallback(
    async (id: string) => {
      setSelectedId(id);
      setDetail(null);
      setErrorDetail(null);
      setLoadingDetail(true);
      try {
        const d = await fetchSessionDetail(id);
        if (d === null) {
          setErrorDetail(t('history.notFound'));
        } else {
          setDetail(d);
        }
      } catch (e) {
        setErrorDetail(e instanceof Error ? e.message : String(e));
      } finally {
        setLoadingDetail(false);
      }
    },
    [t],
  );

  const closeDetail = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setErrorDetail(null);
  }, []);

  // ---- 详情态 ----
  if (selectedId) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-3 p-3 border-b border-border">
          <button onClick={closeDetail} className={BTN}>
            {t('history.back')}
          </button>
          <div className="font-medium text-text truncate">
            {detail?.title || selectedId}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {loadingDetail && <div className="text-text-muted">{t('history.loading')}</div>}
          {errorDetail && <div className="text-danger">{errorDetail}</div>}
          {detail && detail.messages.length === 0 && (
            <div className="text-text-muted">{t('history.emptyMessages')}</div>
          )}
          {detail &&
            detail.messages.map((m, i) => (
              <MessageItem key={`${selectedId}-${i}`} message={toMessage(m, selectedId, i)} />
            ))}
        </div>
      </div>
    );
  }

  // ---- 列表态 ----
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between p-3 border-b border-border">
        <div>
          <div className="text-lg font-semibold text-text">{t('history.title')}</div>
          <div className="text-xs text-text-muted">{t('history.subtitle')}</div>
        </div>
        <button onClick={loadList} className={BTN}>
          {t('history.refresh')}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {loadingList && <div className="text-text-muted">{t('history.loading')}</div>}
        {errorList && <div className="text-danger">{errorList}</div>}
        {!loadingList && !errorList && sessions.length === 0 && (
          <div className="text-text-muted">{t('history.empty')}</div>
        )}
        {sessions.map((s) => (
          <button
            key={s.session_id}
            onClick={() => openDetail(s.session_id)}
            className={clsx(
              'w-full text-left rounded-lg border border-border bg-card p-3',
              'hover:border-border-hover hover:shadow-sm transition-colors',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="font-medium text-text truncate">{s.title || s.session_id}</div>
              <div className="text-xs text-text-muted shrink-0">
                {formatRelativeTime(new Date(s.updated_at * 1000).toISOString())}
              </div>
            </div>
            {s.last_preview && (
              <div className="text-sm text-text-muted truncate mt-1">{s.last_preview}</div>
            )}
            <div className="text-xs text-text-muted mt-1">
              {t('history.messageCount', { count: s.message_count })}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
