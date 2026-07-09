import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import { PlatformPageLayout, PlatformEmpty } from '../AvatarPlatform/PlatformPageLayout';
import '../AvatarPlatform/AvatarPlatform.css';

interface UsageStats {
  active_days: number;
  total_duration_seconds: number;
  used_today: boolean;
  today_tasks: number;
  completed_tasks: number;
  total_tasks: number;
  first_task_date: string | null;
  last_task_date: string | null;
}

interface DurationParts {
  value: string;
  unitKey: 'stats.unitHours' | 'stats.unitMinutes' | 'stats.unitSeconds';
  sub?: string;
}

function splitDuration(totalSeconds: number): DurationParts {
  if (totalSeconds <= 0) {
    return { value: '0', unitKey: 'stats.unitMinutes' };
  }
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return {
      value: String(hours),
      unitKey: 'stats.unitHours',
      sub: minutes > 0 ? `${minutes}` : undefined,
    };
  }
  if (minutes > 0) {
    return { value: String(minutes), unitKey: 'stats.unitMinutes' };
  }
  return { value: String(seconds), unitKey: 'stats.unitSeconds' };
}

function formatDateLabel(iso: string | null): string {
  if (!iso) return '—';
  const parts = iso.split('-');
  if (parts.length !== 3) return iso;
  return `${parts[0]}/${parts[1]}/${parts[2]}`;
}

function IconCalendar() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden>
      <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M3 9h18M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function IconClock() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 7v5l3 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function IconToday() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="12" r="3" fill="currentColor" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden>
      <path
        d="M7 12.5l3 3 7-7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function StatsSkeleton() {
  return (
    <div className="stats-page">
      <div className="stats-page__banner stats-page__banner--skeleton" />
      <div className="stats-page__grid">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="stats-card stats-card--skeleton" />
        ))}
      </div>
    </div>
  );
}

export function StatsPanel() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStats = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const res = await webRequest<{ stats?: UsageStats }>('missions.stats', {});
      setStats(res.stats ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStats(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const hasData = (stats?.total_tasks ?? 0) > 0 || (stats?.active_days ?? 0) > 0;
  const duration = stats ? splitDuration(stats.total_duration_seconds) : null;

  const toolbar = (
    <div className="stats-page__toolbar">
      <button
        type="button"
        className="stats-page__refresh"
        disabled={loading || refreshing}
        onClick={() => void loadStats(true)}
      >
        {refreshing ? t('stats.refreshing') : t('stats.refresh')}
      </button>
    </div>
  );

  return (
    <PlatformPageLayout title={t('stats.title')} subtitle={t('stats.subtitle')} toolbar={toolbar}>
      {loading && <StatsSkeleton />}

      {!loading && error && <PlatformEmpty title={t('stats.error')} description={error} />}

      {!loading && !error && !hasData && (
        <PlatformEmpty title={t('stats.emptyTitle')} description={t('stats.emptyDesc')} />
      )}

      {!loading && !error && stats && hasData && duration && (
        <div className="stats-page">
          <div className="stats-page__banner">
            <div className="stats-page__banner-main">
              <p className="stats-page__banner-label">{t('stats.summaryLabel')}</p>
              <p className="stats-page__banner-value">
                {t('stats.summaryActiveDays', { count: stats.active_days })}
              </p>
              {(stats.first_task_date || stats.last_task_date) && (
                <p className="stats-page__banner-range">
                  {t('stats.dateRange', {
                    first: formatDateLabel(stats.first_task_date),
                    last: formatDateLabel(stats.last_task_date),
                  })}
                </p>
              )}
            </div>
            <div
              className={`stats-page__today-pill${stats.used_today ? ' stats-page__today-pill--active' : ''}`}
            >
              <span className="stats-page__today-dot" />
              {stats.used_today ? t('stats.usedTodayYes') : t('stats.usedTodayNo')}
            </div>
          </div>

          <div className="stats-page__grid">
            <article className="stats-card stats-card--days">
              <div className="stats-card__icon">
                <IconCalendar />
              </div>
              <div className="stats-card__body">
                <span className="stats-card__value">{stats.active_days}</span>
                <span className="stats-card__unit">{t('stats.unitDays')}</span>
                <span className="stats-card__label">{t('stats.activeDays')}</span>
                <span className="stats-card__hint">{t('stats.activeDaysHint')}</span>
              </div>
            </article>

            <article className="stats-card stats-card--duration">
              <div className="stats-card__icon">
                <IconClock />
              </div>
              <div className="stats-card__body">
                <span className="stats-card__value">{duration.value}</span>
                <span className="stats-card__unit">{t(duration.unitKey)}</span>
                {duration.sub && (
                  <span className="stats-card__sub">
                    {t('stats.durationSubMinutes', { minutes: duration.sub })}
                  </span>
                )}
                <span className="stats-card__label">{t('stats.totalDuration')}</span>
              </div>
            </article>

            <article
              className={`stats-card stats-card--today${stats.used_today ? ' stats-card--today-active' : ''}`}
            >
              <div className="stats-card__icon">
                <IconToday />
              </div>
              <div className="stats-card__body">
                <span className="stats-card__value">{stats.today_tasks}</span>
                <span className="stats-card__unit">{t('stats.unitTasks')}</span>
                <span className="stats-card__label">{t('stats.usedToday')}</span>
                <span className="stats-card__hint">
                  {stats.used_today
                    ? t('stats.todayTasks', { count: stats.today_tasks })
                    : t('stats.todayIdleHint')}
                </span>
              </div>
            </article>

            <article className="stats-card stats-card--completed">
              <div className="stats-card__icon">
                <IconCheck />
              </div>
              <div className="stats-card__body">
                <span className="stats-card__value">{stats.completed_tasks}</span>
                <span className="stats-card__unit">{t('stats.unitTasks')}</span>
                <span className="stats-card__label">{t('stats.completedTasks')}</span>
                <span className="stats-card__hint">
                  {t('stats.dispatchedTotal', { count: stats.total_tasks })}
                </span>
              </div>
            </article>
          </div>

          <p className="stats-page__footnote">{t('stats.footerNote')}</p>
        </div>
      )}
    </PlatformPageLayout>
  );
}
