import { useTranslation } from 'react-i18next';
import { SettingRow } from '../../components';
import { useSettingsServices } from '../../services/SettingsServicesProvider';

export function ConnectionStatusSetting() {
  const { t } = useTranslation();
  const { connectionState } = useSettingsServices();
  const connectionKey =
    connectionState === 'ready'
      ? 'connected'
      : connectionState === 'connecting' || connectionState === 'reconnecting'
        ? 'connecting'
        : 'disconnected';
  return (
    <SettingRow
      title={t('settingsPanel.general.connection')}
      description={t('settingsPanel.general.connectionDescription')}
      meta={
        <span className={`settings-general__connection settings-general__connection--${connectionKey}`} role="status">
          {t(`settingsPanel.general.connectionStatus.${connectionKey}`)}
        </span>
      }
    />
  );
}
