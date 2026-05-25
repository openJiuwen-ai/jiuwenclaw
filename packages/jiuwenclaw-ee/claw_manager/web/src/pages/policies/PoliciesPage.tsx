import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { InstanceApi } from '../../services/api';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { MappingTab } from './MappingTab';
import { GlobalPoliciesTab } from './GlobalPoliciesTab';
import { ServicePoliciesTab } from './ServicePoliciesTab';
import { AgentPoliciesTab } from './AgentPoliciesTab';

type TabKey = 'mapping' | 'global' | 'service' | 'agent';

interface Props {
  instanceId: string;
}

export function PoliciesPage({ instanceId }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [tab, setTab] = useState<TabKey>('mapping');

  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'mapping', label: t('policies.tabs.mapping') },
    { key: 'global', label: t('policies.tabs.global') },
    { key: 'service', label: t('policies.tabs.service') },
    { key: 'agent', label: t('policies.tabs.agent') },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div className="flex items-center gap-3">
          <button className="btn ghost sm" onClick={() => navigate(`/instances/${instanceId}`)}>
            ← {t('instanceDetail.title')}
          </button>
          <div>
            <div className="page-title">
              {t('policies.title')}
              {instance.data?.jiuwenclaw_name && (
                <span className="ml-3 text-sm text-muted font-normal">
                  / {instance.data.jiuwenclaw_name}
                </span>
              )}
            </div>
            <div className="page-subtitle">{t('policies.subtitle')}</div>
          </div>
          {instance.data?.status && <StatusBadge status={instance.data.status} />}
        </div>
      </div>

      <div className="tabs-bar">
        {tabs.map((it) => (
          <button
            key={it.key}
            onClick={() => setTab(it.key)}
            className={`tab ${tab === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>

      <div>
        {tab === 'mapping' && <MappingTab instanceId={instanceId} />}
        {tab === 'global' && <GlobalPoliciesTab instanceId={instanceId} />}
        {tab === 'service' && <ServicePoliciesTab instanceId={instanceId} />}
        {tab === 'agent' && <AgentPoliciesTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
