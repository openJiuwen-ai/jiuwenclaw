import { settingsNavigationIcons } from '../../../../assets/settings';
import type { SettingsModuleDefinition } from '../../registry/types';
import { SecuritySettings } from './SecuritySettings';

export const securityModule: SettingsModuleDefinition = {
  id: 'security',
  titleKey: 'settingsPanel.categories.security',
  descriptionKey: 'settingsPanel.moduleDescriptions.security',
  icon: settingsNavigationIcons.security,
  source: 'config',
  sections: [
    {
      id: 'global-permissions',
      titleKey: 'settingsPanel.security.guardrails',
      descriptionKey: 'settingsPanel.security.guardrailsDescription',
      items: [{ id: 'permissions-enabled', component: 'switch', key: 'permissions_enabled' }],
    },
    {
      id: 'tool-permissions',
      items: [{ id: 'security-settings', component: 'custom', render: SecuritySettings }],
    },
  ],
};
