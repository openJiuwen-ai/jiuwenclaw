import { useTranslation } from 'react-i18next';
import { formatTime, relativeTime, safeStringify } from '../../../utils/format';
import type { InstanceDetail } from '../../../types';

interface Props {
  instance: { data?: InstanceDetail | null };
  onOpenEdit: () => void;
  onRefresh: () => void;
}

export function InstanceDetailPanel({ instance, onOpenEdit, onRefresh }: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header justify-end">
        <button className="btn sm" onClick={onRefresh}>
          {t('common.refresh')}
        </button>
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
            <div className="text-muted">{t('topology.lastHeartbeat')}</div>
            <div className="mono">{relativeTime(instance.data?.last_heartbeat)}</div>
            <div className="text-muted">description</div>
            <div>{instance.data?.description ?? '-'}</div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-title">{t('instanceDetail.extraData')}</div>
            <button className="btn ghost sm" onClick={onOpenEdit}>
              {t('instanceDetail.editData')}
            </button>
          </div>
          <pre className="text-[11px] mono whitespace-pre-wrap break-words text-text max-h-48 overflow-auto">
            {safeStringify(instance.data?.data ?? {}, 2) || '-'}
          </pre>
        </div>
      </div>
    </div>
  );
}
