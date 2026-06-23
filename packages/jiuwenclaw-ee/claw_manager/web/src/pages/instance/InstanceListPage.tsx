import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceApi } from '../../services/api';
import type { InstanceSummary } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { relativeTime } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { ApiError } from '../../services/api';
import { CreateInstanceModal } from './modal/CreateInstanceModal';
import { ProvisionLocalModal } from './modal/ProvisionLocalModal';

function InstanceTopoCard({
  instance,
  onChanged,
}: {
  instance: InstanceSummary;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [confirmDel, setConfirmDel] = useState(false);

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
          <div className="flex items-center gap-1">
            <button className="btn sm" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}>
              {t('topology.viewDetail')}
            </button>
            <button className="btn sm danger" onClick={() => setConfirmDel(true)}>
              {t('common.delete')}
            </button>
          </div>
        </div>
      </div>

      <div className="topo-gateway">
        <div className="topo-gateway__title">
          <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h18M3 6h18M3 18h18" />
          </svg>
          {t('topology.gateway')}
        </div>
        <div className="topo-gateway__meta">
          <span className="topo-gateway__meta-item">
            <span>{t('topology.lastHeartbeat')}</span>
            <span className="mono">{relativeTime(instance.last_heartbeat)}</span>
          </span>
        </div>
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

export function InstanceListPage() {
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
            <option value="online">online</option>
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
