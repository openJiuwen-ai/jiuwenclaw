import { settingsNavigationIcons } from '../../../../assets/settings';
import type { SettingsModuleDefinition } from '../../registry/types';
import { ModelsSettings } from './ModelsSettings';

export const modelsModule: SettingsModuleDefinition = {
  id: 'models',
  titleKey: 'settingsPanel.categories.models',
  descriptionKey: 'settingsPanel.moduleDescriptions.models',
  icon: settingsNavigationIcons.models,
  source: 'config',
  sections: [
    {
      id: 'free-models',
      titleKey: 'settingsPanel.models.freeModels',
      descriptionKey: 'settingsPanel.models.freeModelsDescription',
      items: [{ id: 'enable-free-models', component: 'switch', key: 'enable_free_models' }],
    },
    {
      id: 'model-manager',
      items: [{ id: 'model-manager', component: 'custom', render: ModelsSettings }],
    },
  ],
};
