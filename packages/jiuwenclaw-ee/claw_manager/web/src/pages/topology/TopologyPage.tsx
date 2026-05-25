import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceApi } from '../../services/api';
import type { InstanceSummary, ServiceStatusItem, ServiceStatusList } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { relativeTime, truncate } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { ApiError } from '../../services/api';
import { CreateInstanceModal } from './CreateInstanceModal';
import { ProvisionLocalModal } from './ProvisionLocalModal';

function classifyServices(items: ServiceStatusItem[]) {
  const gateways: ServiceStatusItem[] = [];
  const agentServers: ServiceStatusItem[] = [];
  const others: ServiceStatusItem[] = [];
  for (const s of items) {
    const t = (s.service_type || '').toLowerCase();
    if (t === 'gateway' || t.includes('gateway')) gateways.push(s);
    else if (t === 'agent_server' || t.includes('agent')) agentServers.push(s);
    else others.push(s);
  }
  return { gateways, agentServers, others };
}

function InstanceTopoCard({
  instance,
  onChanged,
}: {
  instance: InstanceSummary;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [services, setServices] = useState<ServiceStatusList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await InstanceApi.servicesStatus(instance.jiuwenclaw_id);
      setServices(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance.jiuwenclaw_id]);

  const items = services?.items ?? [];
  const { gateways, agentServers, others } = classifyServices(items);

  const onlineCount = items.filter((s) => (s.status ?? '').toLowerCase() === 'online').length;
  const totalCount = items.length;

  return (
    <div className="topo-group">
      <div className="topo-hero">
        <div className="topo-hero__title">
          <div className="brand-logo" aria-hidden>
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 17h6m-3-3v3M7 8h10M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z" />
            </svg>
          </div>
          <div>
            <div className="topo-hero__name">
              {instance.jiuwenclaw_name}
              <StatusBadge status={instance.status} />
            </div>
            <div className="topo-hero__id">{instance.jiuwenclaw_id}</div>
          </div>
        </div>
        <div className="topo-hero__meta">
          <span className="pill subtle muted">
            {t('topology.namespace')}: <span className="mono text-text">{instance.k8s_namespace}</span>
          </span>
          <span className="pill subtle muted">
            {t('topology.group')}: <span className="mono text-text">{instance.group_id}</span>
          </span>
          <span className="pill subtle muted">
            svc <span className="mono text-text">{onlineCount}/{totalCount}</span>
          </span>
          <div className="flex items-center gap-1">
            <button className="btn sm" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}>
              {t('topology.viewDetail')}
            </button>
            <button
              className="btn sm"
              onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}/policies`)}
            >
              {t('topology.managePolicies')}
            </button>
            <button className="btn ghost sm" onClick={() => void refresh()} title={t('common.refresh')}>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.7}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992V4.356M19.5 8.25a8.25 8.25 0 11-1.99-5.25M3 12h0" />
              </svg>
            </button>
            <button className="btn sm danger" onClick={() => setConfirmDel(true)}>
              {t('common.delete')}
            </button>
          </div>
        </div>
      </div>

      {/* Gateway 层 */}
      <div>
        {gateways.length === 0 && agentServers.length === 0 && others.length === 0 ? (
          loading ? (
            <div className="text-xs text-muted py-3">{t('common.loading')}</div>
          ) : (
            <Empty text={error ?? t('topology.noServices')} />
          )
        ) : (
          <div className="flex flex-col gap-4">
            {gateways.length > 0 ? (
              gateways.map((g) => (
                <div key={g.service_id} className="flex flex-col gap-2">
                  <div className="topo-gateway">
                    <div className="topo-gateway__title">
                      <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h18M3 6h18M3 18h18" />
                      </svg>
                      {t('topology.gateway')} · <span className="mono text-[12px]">{g.service_id}</span>
                    </div>
                    <div className="topo-gateway__meta">
                      {g.endpoint && (
                        <span className="topo-gateway__meta-item">
                          <span>endpoint</span><span className="mono">{truncate(g.endpoint, 56)}</span>
                        </span>
                      )}
                      {g.version && (
                        <span className="topo-gateway__meta-item">
                          <span>ver</span><span className="mono">{g.version}</span>
                        </span>
                      )}
                      <span className="topo-gateway__meta-item">
                        <span>heartbeat</span><span className="mono">{relativeTime(g.last_heartbeat)}</span>
                      </span>
                    </div>
                    <StatusBadge status={g.status} />
                  </div>

                  {/* AgentServer 子节点 */}
                  {agentServers.length > 0 && (
                    <div className="topo-services">
                      {agentServers.map((a) => (
                        <ServiceMini key={a.service_id} item={a} kind="agentServer" />
                      ))}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="pill warn w-fit">
                {t('topology.noServices')}
              </div>
            )}

            {gateways.length === 0 && agentServers.length > 0 && (
              <div className="topo-services">
                {agentServers.map((a) => (
                  <ServiceMini key={a.service_id} item={a} kind="agentServer" />
                ))}
              </div>
            )}

            {others.length > 0 && (
              <div className="mt-1">
                <div className="section-title mb-2 text-[11px]">
                  <span className="section-title__bar" />
                  {t('topology.otherService')}
                  <span className="section-title__count">{others.length}</span>
                </div>
                <div className="topo-services">
                  {others.map((o) => (
                    <ServiceMini key={o.service_id} item={o} kind="other" />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDel}
        message={t('topology.deleteConfirm')}
        danger
        onConfirm={async () => {
          try {
            await InstanceApi.remove(instance.jiuwenclaw_id);
            toast('success', t('success.deleted'));
            onChanged();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setConfirmDel(false)}
      />
    </div>
  );
}

function ServiceMini({ item, kind }: { item: ServiceStatusItem; kind: 'agentServer' | 'other' }) {
  const { t } = useTranslation();
  const s = (item.status ?? '').toLowerCase();
  const state = ['online', 'active', 'ready', 'running'].includes(s)
    ? 'online'
    : ['pending', 'restarting', 'starting'].includes(s)
      ? 'warn'
      : 'offline';
  const serviceTypeClass = (item.service_type ?? '').toLowerCase().replace(/[^a-z_]/g, '');
  return (
    <div className="topo-service" data-state={state}>
      <div className="flex items-center justify-between gap-2">
        <div className="topo-service__title" title={item.service_id}>
          {kind === 'agentServer' ? '🤖 ' : '🔧 '}
          {item.service_id}
        </div>
        <StatusBadge status={item.status} />
      </div>
      <div className="topo-service__meta">
        <span className={`tag ${serviceTypeClass}`}>{item.service_type}</span>
        {item.version && <span className="mono">v{item.version}</span>}
      </div>
      {item.endpoint && (
        <div className="text-[11px] text-muted mono truncate" title={item.endpoint}>
          {item.endpoint}
        </div>
      )}
      <div className="text-[11px] text-muted">
        {t('topology.lastHeartbeat')}: <span className="mono">{relativeTime(item.last_heartbeat)}</span>
      </div>
    </div>
  );
}

export function TopologyPage() {
  const { t } = useTranslation();
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [createOpen, setCreateOpen] = useState(false);
  const [provisionOpen, setProvisionOpen] = useState(false);

  const instances = useAsync(
    () => InstanceApi.list({ page: 1, page_size: 100, status: statusFilter || undefined }),
    [statusFilter]
  );

  const refresh = () => {
    void instances.reload();
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('topology.title')}</div>
          <div className="page-subtitle">{t('topology.subtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="select !w-auto"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">{t('common.all')}</option>
            <option value="active">active</option>
            <option value="pending">pending</option>
            <option value="offline">offline</option>
          </select>
          <button className="btn sm" onClick={refresh}>
            {t('common.refresh')}
          </button>
          <button className="btn sm" onClick={() => setProvisionOpen(true)}>
            {t('topology.provisionLocal')}
          </button>
          <button className="btn primary sm" onClick={() => setCreateOpen(true)}>
            + {t('topology.createInstance')}
          </button>
        </div>
      </div>

      {instances.loading ? (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      ) : instances.error ? (
        <div className="card text-sm text-danger">
          {t('errors.loadFailed', { detail: instances.error })}
        </div>
      ) : !instances.data || instances.data.items.length === 0 ? (
        <div className="card">
          <Empty text={t('overview.noInstances')} />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {instances.data.items.map((it) => (
            <InstanceTopoCard key={it.jiuwenclaw_id} instance={it} onChanged={refresh} />
          ))}
        </div>
      )}

      <CreateInstanceModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          refresh();
        }}
      />
      <ProvisionLocalModal
        open={provisionOpen}
        onClose={() => setProvisionOpen(false)}
        onProvisioned={() => {
          setProvisionOpen(false);
          refresh();
        }}
      />
    </div>
  );
}
