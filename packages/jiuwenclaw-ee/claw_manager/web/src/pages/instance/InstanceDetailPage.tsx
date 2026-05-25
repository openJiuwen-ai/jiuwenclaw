import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { InstanceApi, ApiError } from '../../services/api';
import { StatusBadge } from '../../components/StatusBadge';
import { Empty } from '../../components/Empty';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { formatTime, relativeTime, safeStringify, truncate } from '../../utils/format';
import { toast } from '../../stores/uiStore';

interface Props {
  instanceId: string;
}

export function InstanceDetailPage({ instanceId }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);
  const services = useAsync(() => InstanceApi.servicesStatus(instanceId), [instanceId]);

  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState('');
  const checkJson = useInvalidJsonChecker();

  const handleOpenEdit = () => {
    setEditText(safeStringify(instance.data?.data ?? {}, 2));
    setEditOpen(true);
  };

  const submitData = async () => {
    const err = checkJson(editText);
    if (err) {
      toast('danger', err);
      return;
    }
    try {
      await InstanceApi.patch(instanceId, tryParseJson(editText, {}));
      toast('success', t('success.saved'));
      setEditOpen(false);
      void instance.reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div className="flex items-center gap-3">
          <button className="btn ghost sm" onClick={() => navigate('/topology')}>
            ← {t('instanceDetail.back')}
          </button>
          <div>
            <div className="page-title">{instance.data?.jiuwenclaw_name ?? '…'}</div>
            <div className="text-[11px] text-muted mono">{instanceId}</div>
          </div>
          {instance.data?.status && <StatusBadge status={instance.data.status} />}
        </div>
        <div className="flex items-center gap-2">
          <button className="btn sm" onClick={() => navigate(`/instances/${instanceId}/policies`)}>
            {t('topology.managePolicies')}
          </button>
          <button
            className="btn sm"
            onClick={() => {
              void instance.reload();
              void services.reload();
            }}
          >
            {t('common.refresh')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="card">
          <div className="card-header">
            <div className="card-title">K8S</div>
          </div>
          <div className="text-xs grid grid-cols-[6em_1fr] gap-y-2 gap-x-2 mono">
            <div className="text-muted">master</div>
            <div className="truncate" title={instance.data?.k8s_master_host ?? ''}>
              {instance.data?.k8s_master_host ?? '-'}
            </div>
            <div className="text-muted">auth_type</div>
            <div>{instance.data?.k8s_auth_type ?? '-'}</div>
            <div className="text-muted">namespace</div>
            <div>{instance.data?.k8s_namespace ?? '-'}</div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">Meta</div>
          </div>
          <div className="text-xs grid grid-cols-[6em_1fr] gap-y-2 gap-x-2">
            <div className="text-muted">group</div>
            <div className="mono">{instance.data?.group_id ?? '-'}</div>
            <div className="text-muted">space</div>
            <div className="mono">{instance.data?.space_id ?? '-'}</div>
            <div className="text-muted">created</div>
            <div className="mono">{formatTime(instance.data?.created_at)}</div>
            <div className="text-muted">description</div>
            <div>{instance.data?.description ?? '-'}</div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">{t('instanceDetail.extraData')}</div>
            <button className="btn ghost sm" onClick={handleOpenEdit}>
              {t('instanceDetail.editData')}
            </button>
          </div>
          <pre className="text-[11px] mono whitespace-pre-wrap break-words text-text max-h-48 overflow-auto">
            {safeStringify(instance.data?.data ?? {}, 2) || '-'}
          </pre>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-title">{t('instanceDetail.services')}</div>
        </div>
        {services.loading ? (
          <div className="text-sm text-muted">{t('common.loading')}</div>
        ) : services.error ? (
          <div className="text-sm text-danger">{t('errors.loadFailed', { detail: services.error })}</div>
        ) : !services.data || services.data.items.length === 0 ? (
          <Empty text={t('topology.noServices')} />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>service_id</th>
                <th>type</th>
                <th>role</th>
                <th>{t('topology.instanceStatus')}</th>
                <th>{t('topology.endpoint')}</th>
                <th>{t('topology.version')}</th>
                <th>{t('topology.lastHeartbeat')}</th>
              </tr>
            </thead>
            <tbody>
              {services.data.items.map((s) => (
                <tr key={s.service_id}>
                  <td className="mono text-xs text-text-strong">{s.service_id}</td>
                  <td><span className={`tag ${(s.service_type ?? '').toLowerCase()}`}>{s.service_type}</span></td>
                  <td className="mono text-xs">{s.component_role}</td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="mono text-[11px] text-muted" title={s.endpoint ?? ''}>{truncate(s.endpoint ?? '-', 40)}</td>
                  <td className="mono text-xs">{s.version ?? '-'}</td>
                  <td className="mono text-xs">{relativeTime(s.last_heartbeat)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal
        open={editOpen}
        title={t('instanceDetail.editData')}
        onClose={() => setEditOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" onClick={() => setEditOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" onClick={submitData}>
              {t('common.save')}
            </button>
          </>
        }
      >
        <JsonField label="instance_info.data" value={editText} onChange={setEditText} rows={14} />
      </Modal>
    </div>
  );
}
