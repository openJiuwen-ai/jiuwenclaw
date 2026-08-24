import { settingsNavigationIcons } from '../../../../assets/settings';
import type { SettingsModuleDefinition } from '../../registry/types';
import { ChannelsModule } from './ChannelsModule';

export const channelsModule: SettingsModuleDefinition = {
  id: 'channels',
  titleKey: 'settingsPanel.categories.channels',
  icon: settingsNavigationIcons.channels,
  sections: [{ id: 'channels', items: [{ id: 'channels-panel', component: 'custom', render: ChannelsModule }] }],
};
