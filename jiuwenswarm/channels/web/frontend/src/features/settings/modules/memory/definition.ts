import { settingsNavigationIcons } from '../../../../assets/settings';
import type { SettingsModuleDefinition } from '../../registry/types';
import { MemoryRuleSetting } from './MemorySettings';

export const memoryModule: SettingsModuleDefinition = {
  id: 'memory',
  titleKey: 'settingsPanel.categories.memory',
  descriptionKey: 'settingsPanel.moduleDescriptions.memory',
  icon: settingsNavigationIcons.memory,
  source: 'config',
  sections: [
    {
      id: 'memory-filtering',
      titleKey: 'settingsPanel.memory.filtering',
      items: [
        {
          id: 'memory-forbidden',
          component: 'switch',
          key: 'memory_forbidden_enabled',
          subItems: {
            show: 'always',
            disabled: 'when-parent-unchecked',
            items: [{ id: 'memory-forbidden-rule', component: 'custom', render: MemoryRuleSetting }],
          },
        },
      ],
    },
    {
      id: 'context-engine',
      titleKey: 'settingsPanel.memory.context',
      items: [{ id: 'context-engine', component: 'switch', key: 'context_engine_enabled' }],
    },
  ],
};
