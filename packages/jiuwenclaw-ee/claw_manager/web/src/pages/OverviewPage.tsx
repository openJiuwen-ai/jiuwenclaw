import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceApi, SystemApi } from '../services/api';
import { useAsync } from '../hooks/useAsync';
import { StatusBadge } from '../components/StatusBadge';
import { useRouter } from '../router';

function Icon({ d }: { d: string }) {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

export function OverviewPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();

  const health = useAsync(() => SystemApi.health(), []);
  const wsStatus = useAsync(() => SystemApi.managerWsStatus(), []);
  const instances = useAsync(() => InstanceApi.list({ page: 1, page_size: 200 }), []);

  const instanceTotal = instances.data?.total ?? 0;
  const wsRegistered = wsStatus.data?.registered_jiuwenclaw_ids?.length ?? 0;

  const statusDist = useMemo(() => {
    const items = instances.data?.items ?? [];
    const map = { ok: 0, warn: 0, danger: 0, muted: 0 };
    for (const it of items) {
      const s = (it.status ?? '').toLowerCase();
      if (['online', 'ready', 'running'].includes(s) || s === 'active') map.ok += 1;
      else if (['pending', 'restarting', 'starting'].includes(s)) map.warn += 1;
      else if (['offline', 'failed', 'error'].includes(s)) map.danger += 1;
      else map.muted += 1;
    }
    return { ...map, total: items.length };
  }, [instances.data]);

  const seg = (n: number) => (statusDist.total ? `${(n / statusDist.total) * 100}%` : '0%');

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('overview.title')}</div>
          <div className="page-subtitle">{t('overview.subtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn sm"
            onClick={() => {
              void health.reload();
              void wsStatus.reload();
              void instances.reload();
            }}
          >
            {t('common.refresh')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="card kpi-card">
          <div className="kpi-card__head">
            <span className="kpi-card__label">{t('overview.managerHealth')}</span>
            <span className={`kpi-card__icon ${health.data?.status === 'ok' ? 'ok' : 'warn'}`}>
              <Icon d="M9 12.75l2.25 2.25 4.5-4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </span>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge
              status={health.error ? 'offline' : health.data?.status === 'ok' ? 'ok' : 'pending'}
              label={health.error ? 'offline' : (health.data?.status ?? '-')}
            />
            <span className="text-[11px] text-muted mono">{t('overview.managerHealth')}</span>
          </div>
        </div>

        <div className="card kpi-card">
          <div className="kpi-card__head">
            <span className="kpi-card__label">{t('overview.managerWs')}</span>
            <span className={`kpi-card__icon ${wsStatus.data?.running ? 'ok' : 'warn'}`}>
              <Icon d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z" />
            </span>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge
              status={wsStatus.data?.running ? 'ok' : 'offline'}
              label={wsStatus.data?.running ? 'running' : 'offline'}
            />
            {wsStatus.data?.running && (
              <span className="text-[11px] text-muted mono">
                {wsStatus.data?.host}:{wsStatus.data?.port}
              </span>
            )}
          </div>
          <div className="kpi-card__meta">
            <span>{t('overview.wsPid')}: <span className="mono text-text">{wsStatus.data?.pid ?? '-'}</span></span>
          </div>
        </div>

        <button
          className="card kpi-card text-left"
          onClick={() => navigate('/topology')}
          aria-label={t('overview.totalInstances')}
        >
          <div className="kpi-card__head">
            <span className="kpi-card__label">{t('overview.totalInstances')}</span>
            <span className="kpi-card__icon">
              <Icon d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zm0 9.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zm9.75-9.75A2.25 2.25 0 0115.75 3.75H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25A2.25 2.25 0 0113.5 8.25V6zm0 9.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </span>
          </div>
          <div className="kpi-card__value">
            {instanceTotal}
            <span className="unit">instance{instanceTotal === 1 ? '' : 's'}</span>
          </div>
          <div className="kpi-card__meta">
            <span>{t('overview.registeredOnWs')}: <span className="mono text-text">{wsRegistered}</span></span>
          </div>
        </button>

        <div className="card kpi-card">
          <div className="kpi-card__head">
            <span className="kpi-card__label">{t('overview.activeServices')}</span>
            <span className="kpi-card__icon ok">
              <Icon d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
            </span>
          </div>
          <div className="kpi-card__value">
            {statusDist.ok}
            <span className="unit">/ {statusDist.total} 在线</span>
          </div>
          <div className="kpi-card__meta">
            <div className="stat-bar w-full">
              <div className="stat-bar__seg ok" style={{ width: seg(statusDist.ok) }} title={`online ${statusDist.ok}`} />
              <div className="stat-bar__seg warn" style={{ width: seg(statusDist.warn) }} title={`warn ${statusDist.warn}`} />
              <div className="stat-bar__seg danger" style={{ width: seg(statusDist.danger) }} title={`offline ${statusDist.danger}`} />
              <div className="stat-bar__seg muted" style={{ width: seg(statusDist.muted) }} title={`other ${statusDist.muted}`} />
            </div>
          </div>
          <div className="kpi-card__meta">
            <span className="flex items-center gap-1"><span className="statusDot ok" />{statusDist.ok}</span>
            <span className="flex items-center gap-1"><span className="statusDot warn" />{statusDist.warn}</span>
            <span className="flex items-center gap-1"><span className="statusDot" />{statusDist.danger}</span>
            <span className="flex items-center gap-1"><span className="statusDot muted" />{statusDist.muted}</span>
          </div>
        </div>
      </div>

      <div className="card !p-0">
        <div className="card-header" style={{ padding: '16px 18px 8px' }}>
          <div className="section-title">
            <span className="section-title__bar" />
            {t('nav.topology')}
            <span className="section-title__count">{instances.data?.items?.length ?? 0}</span>
          </div>
          <button className="btn ghost sm" onClick={() => navigate('/topology')}>
            {t('common.view')} →
          </button>
        </div>
        {instances.data && instances.data.items.length > 0 ? (
          <table className="table">
            <thead>
              <tr>
                <th>name</th>
                <th>{t('topology.instanceStatus')}</th>
                <th>{t('topology.namespace')}</th>
                <th>{t('topology.group')}</th>
              </tr>
            </thead>
            <tbody>
              {instances.data.items.slice(0, 8).map((it) => (
                <tr
                  key={it.jiuwenclaw_id}
                  className="row-clickable"
                  onClick={() => navigate(`/instances/${it.jiuwenclaw_id}`)}
                >
                  <td>
                    <div className="text-text-strong font-medium">{it.jiuwenclaw_name}</div>
                    <div className="text-[11px] text-muted mono">{it.jiuwenclaw_id}</div>
                  </td>
                  <td><StatusBadge status={it.status} /></td>
                  <td className="mono text-xs">{it.k8s_namespace}</td>
                  <td><span className="tag">{it.group_id}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-sm text-muted px-4 py-6">{t('overview.noInstances')}</div>
        )}
      </div>
    </div>
  );
}
