/**
 * ExtensionsHubPanel Component
 *
 * Unified panel combining ExtensionsPanel and HarnessPackagePanel
 * with tab switching at the top.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExtensionsPanel } from '../ExtensionsPanel';
import { HarnessPackagePanel } from '../HarnessPackagePanel';
import './ExtensionsHubPanel.css';

type HubTabKey = 'rails' | 'harnesspkg';

interface ExtensionsHubPanelProps {
  sessionId: string;
  isConnected: boolean;
}

export function ExtensionsHubPanel({ sessionId, isConnected }: ExtensionsHubPanelProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<HubTabKey>('harnesspkg');

  return (
    <div className="extensions-hub-panel">
      {/* Tab Header */}
      <div className="extensions-hub-panel__header">
        <div className="extensions-hub-panel__tabs">
          <button
            type="button"
            onClick={() => setActiveTab('harnesspkg')}
            className={`extensions-hub-panel__tab ${activeTab === 'harnesspkg' ? 'extensions-hub-panel__tab--active' : ''}`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
            </svg>
            <span>{t('nav.harnesspkg', 'Plugins')}</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('rails')}
            className={`extensions-hub-panel__tab ${activeTab === 'rails' ? 'extensions-hub-panel__tab--active' : ''}`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m6-6H6" />
              <circle cx="12" cy="12" r="9" />
            </svg>
            <span>{t('nav.rails', 'Extensions')}</span>
          </button>
        </div>
      </div>

      {/* Tab Content */}
      <div className="extensions-hub-panel__content">
        {activeTab === 'rails' && (
          <ExtensionsPanel isConnected={isConnected} />
        )}
        {activeTab === 'harnesspkg' && (
          <HarnessPackagePanel sessionId={sessionId} />
        )}
      </div>
    </div>
  );
}