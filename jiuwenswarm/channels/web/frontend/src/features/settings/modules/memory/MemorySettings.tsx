import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, CollapsibleText } from '../../../../components/ui';
import { Form, FormDialog, useForm } from '../../../../components/form';
import { SettingRow } from '../../components';
import type { SettingsCustomItemProps } from '../../registry/types';
import { getLocalizedMemoryDescription } from '../../services/settingsContract';
import { useSettingsServices } from '../../services/SettingsServicesProvider';
import { useSettingsSource } from '../../services/SettingsSourceProvider';

function MemoryDescriptionDialog({
  value,
  onClose,
  onSave,
}: {
  value: string;
  onClose: () => void;
  onSave: (value: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const form = useForm({ initialValues: { description: value } });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const submit = async () => {
    const result = form.validate();
    if (!result.valid) return;
    setSaving(true);
    setSaveError('');
    try {
      await onSave(result.values.description);
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : t('settingsPanel.feedback.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormDialog
      open
      title={t('settingsPanel.memory.editRuleTitle')}
      submitting={saving}
      confirmLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      onConfirm={() => void submit()}
      onCancel={onClose}
    >
      <Form
        form={form}
        optionalText={t('common.optional')}
        items={[
          {
            name: 'description',
            label: t('settingsPanel.fields.memory_forbidden_description.title'),
            helpTips: t('settingsPanel.fields.memory_forbidden_description.description'),
            component: 'textarea',
            rows: 5,
            placeholder: t('settingsPanel.fields.memory_forbidden_description.placeholder'),
          },
        ]}
      />
      {saveError ? (
        <div className="settings-page__error" role="alert">
          {saveError}
        </div>
      ) : null}
    </FormDialog>
  );
}

export function MemoryRuleSetting({ disabled }: SettingsCustomItemProps) {
  const { t, i18n } = useTranslation();
  const { isConnected } = useSettingsServices();
  const { values, save } = useSettingsSource();
  const [editing, setEditing] = useState(false);
  const description = getLocalizedMemoryDescription(values.memory_forbidden_description, i18n.language);
  return (
    <>
      <SettingRow
        title={t('settingsPanel.fields.memory_forbidden_description.title')}
        description={
          <CollapsibleText maxLines={3} expandLabel={t('common.expand')} collapseLabel={t('common.collapse')}>
            {description || t('settingsPanel.common.notConfigured')}
          </CollapsibleText>
        }
        controlPlacement="top"
      >
        <Button disabled={disabled || !isConnected} onClick={() => setEditing(true)}>
          {t('common.modify')}
        </Button>
      </SettingRow>
      {editing ? (
        <MemoryDescriptionDialog
          value={description}
          onClose={() => setEditing(false)}
          onSave={async (next) => {
            await save({ memory_forbidden_description: next }, 'memory-forbidden-description');
          }}
        />
      ) : null}
    </>
  );
}
