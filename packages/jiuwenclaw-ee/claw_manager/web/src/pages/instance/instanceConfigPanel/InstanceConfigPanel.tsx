import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChannelTab } from './ChannelTab';
import { LogMaskingTab } from './LogMaskingTab';
import { PermissionsTab } from './PermissionsTab';

type ConfigTabKey = 'channel' | 'logMasking' | 'permissions';

interface Props {
  instanceId: string;
}

export function InstanceConfigPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<ConfigTabKey>('channel');

  const tabs: { key: ConfigTabKey; label: string }[] = [
    { key: 'channel', label: t('instanceConfig.tabs.channel') },
    { key: 'logMasking', label: t('instanceConfig.tabs.logMasking') },
    { key: 'permissions', label: t('instanceConfig.tabs.permissions') },
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
        {tab === 'channel' && <ChannelTab instanceId={instanceId} />}
        {tab === 'logMasking' && <LogMaskingTab instanceId={instanceId} />}
        {tab === 'permissions' && <PermissionsTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
