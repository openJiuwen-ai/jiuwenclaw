import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { webRequest } from '../../services/webClient';
import { PlatformPageLayout, PlatformEmpty } from '../AvatarPlatform/PlatformPageLayout';
import type { ReportDeepLink } from '../../utils/reportDeepLink';
import {
  migrateLegacyReadStateIfNeeded,
  persistReportReadState,
  type ReportReadState,
} from '../../utils/reportReadState';
import '../AvatarPlatform/AvatarPlatform.css';

interface ReportSection {
  name: string;
  content: string;
}

interface Report {
  id: string;
  mission_id: string;
  avatar_id: string;
  avatar_persona: string;
  created_at: string;
  title: string;
  summary: string;
  sections: ReportSection[];
  metrics: Record<string, unknown>;
}

interface Mission {
  id: string;
  avatar_id: string;
  trigger_id: string | null;
  status: string;
  started_at: string;
  completed_at: string | null;
  prompt: string;
  result_summary: string | null;
  run_id?: string | null;
  session_id?: string | null;
}

interface AvatarInfo {
  id: string;
  name: string;
}

const STATUS_CLASS: Record<string, string> = {
  completed: 'avatar-platform__status--completed',
  running: 'avatar-platform__status--task-running',
  failed: 'avatar-platform__status--failed',
  pending: 'avatar-platform__status--pending',
  cancelled: 'avatar-platform__status--cancelled',
};

const STATUS_ORDER: Record<string, number> = {
  running: 0,
  pending: 1,
  completed: 2,
  failed: 3,
  cancelled: 4,
};

type ReadState = ReportReadState;

function emptyReadState(): ReadState {
  return { missions: {}, reports: {} };
}

const ACTIVE_MISSION_STATUSES = new Set(['pending', 'running']);

function isMissionActive(m: Mission): boolean {
  return ACTIVE_MISSION_STATUSES.has(m.status);
}

function isMissionUnread(m: Mission, state: ReadState): boolean {
  if (isMissionActive(m)) return false;
  const rec = state.missions[m.id];
  if (!rec) return true;
  return rec.status !== m.status;
}

function isReportUnread(r: Report, state: ReadState): boolean {
  return !state.reports[r.id];
}

function getMissionReadBadge(m: Mission, state: ReadState): 'unread' | 'updated' | null {
  if (isMissionActive(m)) return null;
  const rec = state.missions[m.id];
  if (!rec) return 'unread';
  if (rec.status !== m.status) return 'updated';
  return null;
}

/** 兼容历史脏数据：早期 summary 可能存成 Python dict 的字符串。 */
function extractText(raw: string | null | undefined): string {
  if (!raw) return '';
  const s = raw.trim();
  if (s.startsWith('{') && s.includes("'output'")) {
    const m = s.match(/'output':\s*'([\s\S]*?)',\s*'result_type'/);
    if (m) {
      return m[1]
        .replace(/\\n/g, '\n')
        .replace(/\\t/g, '\t')
        .replace(/\\'/g, "'")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, '\\');
    }
  }
  return raw;
}

/** Markdown 压成一行纯文本用作卡片预览。 */
function toPreview(md: string, max = 160): string {
  const text = md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`>#]|\|/g, ' ')
    .replace(/^[-+]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function formatDuration(start: string, end: string | null): string | null {
  if (!end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? `${min}m ${rem}s` : `${min}m`;
}

export interface ReportPanelProps {
  deepLink?: ReportDeepLink | null;
}

export function ReportPanel({ deepLink }: ReportPanelProps) {
  const { t } = useTranslation();
  const [reports, setReports] = useState<Report[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [avatars, setAvatars] = useState<AvatarInfo[]>([]);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [selectedMission, setSelectedMission] = useState<Mission | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'missions' | 'reports'>('missions');

  const [filterAvatar, setFilterAvatar] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [filterRead, setFilterRead] = useState<'' | 'unread' | 'read'>('');
  const [searchText, setSearchText] = useState<string>('');
  const [readState, setReadState] = useState<ReadState>(emptyReadState);
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const [deleting, setDeleting] = useState<Record<string, boolean>>({});

  const fetchData = useCallback(async () => {
    try {
      const [r, m, a] = await Promise.all([
        webRequest<{ reports?: Report[] }>('reports.list', { limit: 200 }),
        webRequest<{ missions?: Mission[] }>('missions.list', { limit: 200 }),
        webRequest<{ avatars?: AvatarInfo[] }>('avatars.list'),
      ]);
      setReports(r?.reports || []);
      setMissions(m?.missions || []);
      setAvatars((a?.avatars || []).map((av: { id: string; name: string }) => ({ id: av.id, name: av.name })));
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    void migrateLegacyReadStateIfNeeded().then(setReadState);
  }, []);

  useEffect(() => {
    if (!deepLink) return;
    if (deepLink.avatarId) setFilterAvatar(deepLink.avatarId);
    if (deepLink.filterRead) setFilterRead(deepLink.filterRead);
    setActiveTab('missions');
  }, [deepLink?.avatarId, deepLink?.filterRead, deepLink?.navToken]);

  const saveReadState = useCallback((state: ReadState) => {
    void persistReportReadState(state).catch(() => {
      /* ignore */
    });
  }, []);

  useEffect(() => {
    const hasRunning = missions.some((m) => m.status === 'running' || m.status === 'pending');
    if (!hasRunning) return;
    const id = window.setInterval(fetchData, 8000);
    return () => window.clearInterval(id);
  }, [missions, fetchData]);

  const markMissionRead = useCallback((mission: Mission) => {
    setReadState((prev) => {
      const next: ReadState = {
        ...prev,
        missions: {
          ...prev.missions,
          [mission.id]: { viewedAt: new Date().toISOString(), status: mission.status },
        },
      };
      saveReadState(next);
      return next;
    });
  }, [saveReadState]);

  const markReportRead = useCallback((report: Report) => {
    setReadState((prev) => {
      const next: ReadState = {
        ...prev,
        reports: {
          ...prev.reports,
          [report.id]: { viewedAt: new Date().toISOString() },
        },
      };
      saveReadState(next);
      return next;
    });
  }, [saveReadState]);

  useEffect(() => {
    if (selectedMission) markMissionRead(selectedMission);
  }, [selectedMission, markMissionRead]);

  useEffect(() => {
    if (selectedReport) markReportRead(selectedReport);
  }, [selectedReport, markReportRead]);

  const markAllRead = useCallback(() => {
    setReadState((prev) => {
      const next: ReadState = {
        missions: { ...prev.missions },
        reports: { ...prev.reports },
      };
      if (activeTab === 'missions') {
        for (const m of missions) {
          next.missions[m.id] = { viewedAt: new Date().toISOString(), status: m.status };
        }
      } else {
        for (const r of reports) {
          next.reports[r.id] = { viewedAt: new Date().toISOString() };
        }
      }
      saveReadState(next);
      return next;
    });
  }, [activeTab, missions, reports, saveReadState]);

  const avatarMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const a of avatars) map[a.id] = a.name;
    return map;
  }, [avatars]);

  const unreadMissionCount = useMemo(
    () => missions.filter((m) => isMissionUnread(m, readState)).length,
    [missions, readState],
  );
  const unreadReportCount = useMemo(
    () => reports.filter((r) => isReportUnread(r, readState)).length,
    [reports, readState],
  );

  const filteredMissions = useMemo(() => {
    let list = [...missions];
    if (filterAvatar) list = list.filter((m) => m.avatar_id === filterAvatar);
    if (filterStatus) list = list.filter((m) => m.status === filterStatus);
    if (filterRead === 'unread') list = list.filter((m) => isMissionUnread(m, readState));
    if (filterRead === 'read') list = list.filter((m) => !isMissionUnread(m, readState));
    if (searchText.trim()) {
      const q = searchText.trim().toLowerCase();
      list = list.filter(
        (m) =>
          m.prompt.toLowerCase().includes(q) ||
          (m.result_summary || '').toLowerCase().includes(q) ||
          m.id.toLowerCase().includes(q),
      );
    }
    list.sort((a, b) => {
      const ua = isMissionUnread(a, readState) ? 0 : 1;
      const ub = isMissionUnread(b, readState) ? 0 : 1;
      if (ua !== ub) return ua - ub;
      const oa = STATUS_ORDER[a.status] ?? 9;
      const ob = STATUS_ORDER[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return new Date(b.started_at).getTime() - new Date(a.started_at).getTime();
    });
    return list;
  }, [missions, filterAvatar, filterStatus, filterRead, searchText, readState]);

  const filteredReports = useMemo(() => {
    let list = [...reports];
    if (filterAvatar) list = list.filter((r) => r.avatar_id === filterAvatar);
    if (filterRead === 'unread') list = list.filter((r) => isReportUnread(r, readState));
    if (filterRead === 'read') list = list.filter((r) => !isReportUnread(r, readState));
    if (searchText.trim()) {
      const q = searchText.trim().toLowerCase();
      list = list.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          r.summary.toLowerCase().includes(q) ||
          r.id.toLowerCase().includes(q),
      );
    }
    list.sort((a, b) => {
      const ua = isReportUnread(a, readState) ? 0 : 1;
      const ub = isReportUnread(b, readState) ? 0 : 1;
      if (ua !== ub) return ua - ub;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
    return list;
  }, [reports, filterAvatar, filterRead, searchText, readState]);

  const stats = useMemo(() => {
    const total = missions.length;
    const running = missions.filter((m) => m.status === 'running').length;
    const failed = missions.filter((m) => m.status === 'failed').length;
    const reportCount = reports.length;
    const unread = activeTab === 'missions' ? unreadMissionCount : unreadReportCount;
    return { total, running, failed, reportCount, unread };
  }, [missions, reports, activeTab, unreadMissionCount, unreadReportCount]);

  const statusLabel = (status: string) => {
    const key = `report.status.${status}`;
    const known = t(key, { defaultValue: '' });
    if (known) return known;
    const fallback: Record<string, string> = {
      completed: '已完成',
      running: '执行中',
      failed: '失败',
      pending: '等待中',
      cancelled: '已取消',
    };
    return fallback[status] || status;
  };

  const handleCancel = useCallback(
    async (missionId: string) => {
      setCancelling((prev) => ({ ...prev, [missionId]: true }));
      try {
        const result = await webRequest<{ cancelled: boolean; interrupt_sent: boolean }>('missions.cancel', {
          mission_id: missionId,
        });
        if (result?.cancelled) {
          await fetchData();
        } else {
          alert(t('report.cancelFailedHint', '无法取消该任务（可能已完成或不在运行）'));
        }
      } catch {
        alert(t('report.cancelFailedHint', '取消任务失败'));
      } finally {
        setCancelling((prev) => ({ ...prev, [missionId]: false }));
      }
    },
    [fetchData, t],
  );

  const handleDelete = useCallback(
    async (missionId: string) => {
      setDeleting((prev) => ({ ...prev, [missionId]: true }));
      try {
        const result = await webRequest<{ success?: boolean }>('missions.delete', {
          mission_id: missionId,
        });
        if (result?.success) {
          if (selectedMission?.id === missionId) {
            setSelectedMission(null);
          }
          setReadState((prev) => {
            const next: ReadState = {
              missions: { ...prev.missions },
              reports: { ...prev.reports },
            };
            delete next.missions[missionId];
            saveReadState(next);
            return next;
          });
          await fetchData();
        } else {
          alert(t('report.deleteFailedHint', '删除执行记录失败'));
        }
      } catch {
        alert(t('report.deleteFailedHint', '删除执行记录失败'));
      } finally {
        setDeleting((prev) => ({ ...prev, [missionId]: false }));
      }
    },
    [fetchData, selectedMission, t],
  );

  const getUniqueStatuses = useMemo(() => {
    const set = new Set(missions.map((m) => m.status));
    return Array.from(set).sort((a, b) => (STATUS_ORDER[a] ?? 9) - (STATUS_ORDER[b] ?? 9));
  }, [missions]);

  if (selectedReport) {
    const summary = extractText(selectedReport.summary);
    const runId = selectedReport.metrics?.run_id as string | undefined;
    const triggerId = selectedReport.metrics?.trigger_id as string | undefined;
    return (
      <PlatformPageLayout
        title={selectedReport.title || t('report.title', '执行报告')}
        subtitle={`${t('report.generatedAt', '生成于')} ${formatDateTime(selectedReport.created_at)}`}
        toolbar={
          <button
            type="button"
            className="avatar-platform__btn avatar-platform__btn--ghost"
            onClick={() => setSelectedReport(null)}
          >
            &larr; {t('report.back', '返回列表')}
          </button>
        }
      >
        <div className="report-detail">
          {(triggerId || runId) && (
            <div className="report-meta">
              {triggerId && (
                <span className="report-meta__item">
                  <span className="report-meta__label">{t('report.trigger', '触发器')}</span>
                  <code className="report-meta__value">{triggerId}</code>
                </span>
              )}
              {runId && (
                <span className="report-meta__item">
                  <span className="report-meta__label">{t('report.runId', '运行 ID')}</span>
                  <code className="report-meta__value">{runId}</code>
                </span>
              )}
            </div>
          )}

          {summary && (
            <section className="report-card report-card--accent">
              <h3 className="report-card__title">{t('report.summary', '摘要')}</h3>
              <div className="report-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
              </div>
            </section>
          )}

          {selectedReport.sections?.map((s, i) => (
            <section key={i} className="report-card">
              <h3 className="report-card__title">{s.name}</h3>
              <div className="report-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{extractText(s.content)}</ReactMarkdown>
              </div>
            </section>
          ))}
        </div>
      </PlatformPageLayout>
    );
  }

  if (selectedMission) {
    const dur = formatDuration(selectedMission.started_at, selectedMission.completed_at);
    const avatarName = avatarMap[selectedMission.avatar_id] || selectedMission.avatar_id;
    const relatedReports = reports.filter((r) => r.mission_id === selectedMission.id);
    return (
      <PlatformPageLayout
        title={`${t('report.missionDetail', '执行详情')}: ${selectedMission.id}`}
        subtitle={avatarName}
        toolbar={
          <div className="report-detail__toolbar">
            <button
              type="button"
              className="avatar-platform__btn avatar-platform__btn--ghost"
              onClick={() => setSelectedMission(null)}
            >
              &larr; {t('report.back', '返回列表')}
            </button>
            <button
              type="button"
              className="avatar-platform__btn avatar-platform__btn--danger"
              disabled={deleting[selectedMission.id]}
              onClick={() => {
                if (window.confirm(t('report.confirmDeleteMission', '确认删除该执行记录？关联报告不会被删除。'))) {
                  void handleDelete(selectedMission.id);
                }
              }}
            >
              {deleting[selectedMission.id] ? '...' : t('report.deleteBtn', '删除')}
            </button>
          </div>
        }
      >
        <div className="mission-detail">
          <div className="report-meta">
            <span className="report-meta__item">
              <span className="report-meta__label">{t('report.runId', '运行 ID')}</span>
              <code className="report-meta__value">{selectedMission.run_id || '-'}</code>
            </span>
            <span className="report-meta__item">
              <span className="report-meta__label">{t('report.sessionId', '会话 ID')}</span>
              <code className="report-meta__value">{selectedMission.session_id || '-'}</code>
            </span>
            {selectedMission.trigger_id && (
              <span className="report-meta__item">
                <span className="report-meta__label">{t('report.trigger', '触发器')}</span>
                <code className="report-meta__value">{selectedMission.trigger_id}</code>
              </span>
            )}
          </div>

          <div className="mission-detail__status-bar">
            <span className={`avatar-platform__status ${STATUS_CLASS[selectedMission.status] || 'avatar-platform__status--pending'}`}>
              {statusLabel(selectedMission.status)}
            </span>
            <span className="mission-detail__time">
              {formatDateTime(selectedMission.started_at)}
              {dur && <span className="report-mission__dur"> &middot; {dur}</span>}
            </span>
            {selectedMission.completed_at && (
              <span className="mission-detail__time-end">
                {t('report.completedAt', '完成于')}: {formatDateTime(selectedMission.completed_at)}
              </span>
            )}
          </div>

          <section className="report-card">
            <h3 className="report-card__title">{t('report.prompt', '触发 Prompt')}</h3>
            <pre className="mission-detail__pre">{selectedMission.prompt}</pre>
          </section>

          {selectedMission.result_summary && (
            <section className="report-card">
              <h3 className="report-card__title">{t('report.resultSummary', '执行结果')}</h3>
              <div className="report-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{extractText(selectedMission.result_summary)}</ReactMarkdown>
              </div>
            </section>
          )}

          {relatedReports.length > 0 && (
            <section className="report-card">
              <h3 className="report-card__title">{t('report.relatedReports', '关联报告')}</h3>
              <div className="report-grid" style={{ gridTemplateColumns: '1fr' }}>
                {relatedReports.map((rr) => (
                  <button
                    key={rr.id}
                    type="button"
                    className={`report-tile ${isReportUnread(rr, readState) ? 'report-tile--unread' : ''}`}
                    onClick={() => {
                      setSelectedMission(null);
                      setSelectedReport(rr);
                    }}
                  >
                    <div className="report-tile__head">
                      <h4 className="report-tile__title">
                        {rr.title}
                        {isReportUnread(rr, readState) && (
                          <span className="report-unread-badge">{t('report.unread', '未读')}</span>
                        )}
                      </h4>
                      <span className="report-tile__date">{formatDateTime(rr.created_at)}</span>
                    </div>
                    <p className="report-tile__preview">{toPreview(extractText(rr.summary))}</p>
                    <span className="report-tile__more">{t('report.viewDetail', '查看详情')} &rarr;</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </PlatformPageLayout>
    );
  }

  return (
    <PlatformPageLayout
      title={t('report.title', '执行报告')}
      subtitle={t('report.pageSubtitle', '分身完成任务后自动生成结构化报告，汇总检视结果、测试数据或开发进展。')}
    >
      {loading ? (
        <div className="avatar-platform__loading">{t('report.loading', '加载中...')}</div>
      ) : (
        <div className="report-page">
          <div className="report-stats">
            <div className="report-stat report-stat--total">
              <span className="report-stat__value">{stats.total}</span>
              <span className="report-stat__label">{t('report.statTotal', '总执行')}</span>
            </div>
            <div className="report-stat report-stat--running">
              <span className="report-stat__value">{stats.running}</span>
              <span className="report-stat__label">{t('report.statRunning', '运行中')}</span>
            </div>
            <div className="report-stat report-stat--failed">
              <span className="report-stat__value">{stats.failed}</span>
              <span className="report-stat__label">{t('report.statFailed', '失败')}</span>
            </div>
            <div className="report-stat report-stat--reports">
              <span className="report-stat__value">{stats.reportCount}</span>
              <span className="report-stat__label">{t('report.statReports', '报告')}</span>
            </div>
            {stats.unread > 0 && (
              <div className="report-stat report-stat--unread">
                <span className="report-stat__value">{stats.unread}</span>
                <span className="report-stat__label">{t('report.statUnread', '未读')}</span>
              </div>
            )}
          </div>

          <div className="report-filters">
            <select
              className="report-filters__select"
              value={filterAvatar}
              onChange={(e) => setFilterAvatar(e.target.value)}
            >
              <option value="">{t('report.allAvatars', '全部分身')}</option>
              {avatars.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>

            {activeTab === 'missions' && (
              <select
                className="report-filters__select"
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
              >
                <option value="">{t('report.allStatus', '全部状态')}</option>
                {getUniqueStatuses.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </select>
            )}

            <select
              className="report-filters__select"
              value={filterRead}
              onChange={(e) => setFilterRead(e.target.value as '' | 'unread' | 'read')}
            >
              <option value="">{t('report.allRead', '全部')}</option>
              <option value="unread">{t('report.filterUnread', '仅未读')}</option>
              <option value="read">{t('report.filterRead', '仅已读')}</option>
            </select>

            <input
              className="report-filters__input"
              type="text"
              placeholder={t('report.searchPlaceholder', '搜索 prompt / 摘要...')}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />

            {(activeTab === 'missions' ? unreadMissionCount : unreadReportCount) > 0 && (
              <button
                type="button"
                className="report-filters__mark-read"
                onClick={markAllRead}
              >
                {t('report.markAllRead', '全部标为已读')}
              </button>
            )}

            <div className="report-filters__tabs">
              <button
                type="button"
                className={`report-filters__tab ${activeTab === 'missions' ? 'report-filters__tab--active' : ''}`}
                onClick={() => setActiveTab('missions')}
              >
                {t('report.tabMissions', '执行记录')}
                {unreadMissionCount > 0 && (
                  <span className="report-filters__tab-badge">{unreadMissionCount}</span>
                )}
              </button>
              <button
                type="button"
                className={`report-filters__tab ${activeTab === 'reports' ? 'report-filters__tab--active' : ''}`}
                onClick={() => setActiveTab('reports')}
              >
                {t('report.tabReports', '报告')}
                {unreadReportCount > 0 && (
                  <span className="report-filters__tab-badge">{unreadReportCount}</span>
                )}
              </button>
            </div>
          </div>

          <div className="report-content">
            {activeTab === 'missions' && (
              <div className="report-content__list">
                {filteredMissions.length === 0 ? (
                  <PlatformEmpty
                    title={t('report.noMissions', '暂无执行记录')}
                    description={t('report.noMissionsHint', '触发器驱动分身完成任务后，执行记录将出现在此处。')}
                  />
                ) : (
                  <div className="report-missions report-missions--scroll">
                    {filteredMissions.map((m) => {
                      const dur = formatDuration(m.started_at, m.completed_at);
                      const isActive = m.status === 'running' || m.status === 'pending';
                      const avatarName = avatarMap[m.avatar_id] || m.avatar_id;
                      const readBadge = getMissionReadBadge(m, readState);
                      return (
                        <div
                          key={m.id}
                          className={`report-mission report-mission--clickable ${isActive ? 'report-mission--active' : ''} ${readBadge ? 'report-mission--unread' : ''}`}
                          onClick={() => setSelectedMission(m)}
                        >
                          <div className="report-mission__header">
                            <span className={`avatar-platform__status ${STATUS_CLASS[m.status] || 'avatar-platform__status--pending'}`}>
                              {statusLabel(m.status)}
                            </span>
                            {readBadge && (
                              <span className="report-unread-badge">
                                {readBadge === 'updated'
                                  ? t('report.updated', '有更新')
                                  : t('report.unread', '未读')}
                              </span>
                            )}
                            <span className="report-mission__avatar">{avatarName}</span>
                            <div className="report-mission__actions">
                              {isActive && (
                                <button
                                  type="button"
                                  className="report-mission__cancel-btn"
                                  disabled={cancelling[m.id]}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (window.confirm(t('report.confirmCancel', '确认打断该任务？'))) {
                                      void handleCancel(m.id);
                                    }
                                  }}
                                >
                                  {cancelling[m.id] ? '...' : t('report.cancelBtn', '打断')}
                                </button>
                              )}
                              <button
                                type="button"
                                className="report-mission__delete-btn"
                                disabled={deleting[m.id]}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (window.confirm(t('report.confirmDeleteMission', '确认删除该执行记录？关联报告不会被删除。'))) {
                                    void handleDelete(m.id);
                                  }
                                }}
                              >
                                {deleting[m.id] ? '...' : t('report.deleteBtn', '删除')}
                              </button>
                            </div>
                          </div>
                          <div className="report-mission__body">
                            <span className="report-mission__prompt" title={m.prompt}>
                              {m.prompt}
                            </span>
                          </div>
                          <div className="report-mission__footer">
                            <span className="report-mission__time">
                              {formatDateTime(m.started_at)}
                              {dur && <span className="report-mission__dur"> &middot; {dur}</span>}
                            </span>
                            <span className="report-mission__info">{t('report.clickDetail', '点击查看详情')}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {activeTab === 'reports' && (
              <div className="report-content__list">
                {filteredReports.length === 0 ? (
                  <PlatformEmpty
                    title={t('report.noReports', '暂无执行报告')}
                    description={t('report.noReportsHint', '当触发器驱动分身完成任务后，报告将自动出现在此处。')}
                  />
                ) : (
                  <div className="report-grid">
                    {filteredReports.map((r) => (
                      <button
                        type="button"
                        key={r.id}
                        className={`report-tile ${isReportUnread(r, readState) ? 'report-tile--unread' : ''}`}
                        onClick={() => setSelectedReport(r)}
                      >
                        <div className="report-tile__head">
                          <h4 className="report-tile__title">
                            {r.title || t('report.title', '执行报告')}
                            {isReportUnread(r, readState) && (
                              <span className="report-unread-badge">{t('report.unread', '未读')}</span>
                            )}
                          </h4>
                          <span className="report-tile__date">{formatDateTime(r.created_at)}</span>
                        </div>
                        <p className="report-tile__preview">{toPreview(extractText(r.summary))}</p>
                        <span className="report-tile__more">{t('report.viewDetail', '查看详情')} &rarr;</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </PlatformPageLayout>
  );
}