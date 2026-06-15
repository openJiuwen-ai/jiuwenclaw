import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MappingTab } from './MappingTab';
import { GlobalPoliciesTab } from './GlobalPoliciesTab';
import { ServicePoliciesTab } from './ServicePoliciesTab';
import { AgentPoliciesTab } from './AgentPoliciesTab';

type PolicyTabKey = 'mapping' | 'global' | 'service' | 'agent';

interface Props {
  instanceId: string;
}

export function InstancePoliciesPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<PolicyTabKey>('global');

  const tabs: { key: PolicyTabKey; label: string }[] = [
    { key: 'global', label: t('policies.tabs.global') },
    { key: 'service', label: t('policies.tabs.service') },
    { key: 'agent', label: t('policies.tabs.agent') },
    { key: 'mapping', label: t('policies.tabs.mapping') },
  ];

  return (
    <div className="flex flex-col gap-4">
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
        {tab === 'global' && <GlobalPoliciesTab instanceId={instanceId} />}
        {tab === 'service' && <ServicePoliciesTab instanceId={instanceId} />}
        {tab === 'agent' && <AgentPoliciesTab instanceId={instanceId} />}
        {tab === 'mapping' && <MappingTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
