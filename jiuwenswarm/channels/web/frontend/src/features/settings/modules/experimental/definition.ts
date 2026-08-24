import { settingsNavigationIcons } from '../../../../assets/settings';
import type { SettingsModuleDefinition } from '../../registry/types';
import { A2UISetting, ExternalCliSettingsItem, ProactiveLimitsSetting } from './ExperimentalSettings';

export const experimentalModule: SettingsModuleDefinition = {
  id: 'experimental',
  titleKey: 'settingsPanel.categories.experimental',
  descriptionKey: 'settingsPanel.moduleDescriptions.experimental',
  icon: settingsNavigationIcons.experimental,
  source: 'config',
  sections: [
    {
      id: 'external-cli-agents',
      titleKey: 'settingsPanel.experimental.externalCliAgents',
      descriptionKey: 'settingsPanel.experimental.externalCliAgentsDescription',
      items: [{ id: 'external-cli-agents', component: 'custom', render: ExternalCliSettingsItem }],
    },
    {
      id: 'a2ui',
      titleKey: 'settingsPanel.experimental.a2ui',
      descriptionKey: 'settingsPanel.experimental.a2uiDescription',
      items: [{ id: 'a2ui', component: 'custom', render: A2UISetting }],
    },
    {
      id: 'proactive-recommendation',
      titleKey: 'settingsPanel.experimental.proactive',
      descriptionKey: 'settingsPanel.experimental.proactiveDescription',
      items: [
        {
          id: 'proactive-recommendation',
          component: 'switch',
          key: 'proactive_recommendation_enabled',
          subItems: {
            show: 'always',
            disabled: 'when-parent-unchecked',
            items: [{ id: 'proactive-limits', component: 'custom', render: ProactiveLimitsSetting }],
          },
        },
      ],
    },
  ],
};
