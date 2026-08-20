import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { TokenUsageTab } from './observability/TokenUsageTab';
import { TraceTab } from './observability/TraceTab';
import { AuditLogTab } from './observability/AuditLogTab';
import { AuditRulesTab } from './observability/AuditRulesTab';

export type ObservabilityTabKey = 'tokenUsage' | 'trace' | 'auditLog' | 'auditRules';

const TAB_ORDER: ObservabilityTabKey[] = ['tokenUsage', 'trace', 'auditLog', 'auditRules'];

export function ObservabilityPage() {
  const { t } = useTranslation();
  const { params, navigate } = useRouter();
  const tabParam = (params.get('tab') as ObservabilityTabKey | null) ?? 'tokenUsage';
  const activeTab: ObservabilityTabKey = TAB_ORDER.includes(tabParam) ? tabParam : 'tokenUsage';

  const [tab, setTab] = useState<ObservabilityTabKey>(activeTab);

  useEffect(() => {
    setTab(activeTab);
  }, [activeTab]);

  const tabs: { key: ObservabilityTabKey; label: string }[] = [
    { key: 'tokenUsage', label: t('observability.tabs.tokenUsage') },
    { key: 'trace', label: t('observability.tabs.trace') },
    { key: 'auditLog', label: t('observability.tabs.auditLog') },
  ];

  const selectTab = (key: ObservabilityTabKey) => {
    setTab(key);
    navigate(`/observability?tab=${key}`);
  };

  return (
    <div className="p-6 space-y-4">
      {tab !== 'auditRules' && (
        <>
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-semibold">{t('nav.observability')}</h1>
          </div>
          <div className="tabs-bar">
            {tabs.map((it) => (
              <button
                key={it.key}
                onClick={() => selectTab(it.key)}
                className={`tab ${tab === it.key ? 'active' : ''}`}
              >
                {it.label}
              </button>
            ))}
          </div>
        </>
      )}
      <div className="w-full min-w-0">
        {tab === 'tokenUsage' && <TokenUsageTab />}
        {tab === 'trace' && <TraceTab />}
        {tab === 'auditLog' && <AuditLogTab />}
        {tab === 'auditRules' && <AuditRulesTab />}
      </div>
    </div>
  );
}
