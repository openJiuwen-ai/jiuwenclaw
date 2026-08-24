import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Form, FormDialog, useForm } from '../../../../components/form';
import { Button, Loading, Select } from '../../../../components/ui';
import { settingsActionIcons, settingsEmptyBoxIllustration } from '../../../../assets/settings';
import { SettingsConfirmDialog, SettingsSection, SettingRow } from '../../components';
import { normalizePermissionLevel, type PermissionLevel } from '../../services/settingsContract';
import { useSettingsServices } from '../../services/SettingsServicesProvider';

function parseTools(payload: Record<string, unknown>): Record<string, PermissionLevel> {
  const tools = payload.tools;
  if (!tools || typeof tools !== 'object' || Array.isArray(tools)) return {};
  return Object.fromEntries(
    Object.entries(tools as Record<string, unknown>).flatMap(([tool, value]) => {
      const raw = value && typeof value === 'object' ? (value as Record<string, unknown>)['*'] : value;
      const level = normalizePermissionLevel(raw);
      return level ? [[tool, level]] : [];
    }),
  );
}

function AddToolDialog({
  existing,
  onClose,
  onAdd,
}: {
  existing: Record<string, PermissionLevel>;
  onClose: () => void;
  onAdd: (tool: string, level: PermissionLevel) => Promise<void>;
}) {
  const { t } = useTranslation();
  const form = useForm({ initialValues: { tool: '', level: 'ask' } });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    const result = form.validate();
    if (!result.valid) return;
    const tool = result.values.tool.trim();
    if (!tool || Object.keys(existing).some((item) => item.trim() === tool)) {
      setError(!tool ? t('settingsPanel.validation.required') : t('settingsPanel.security.toolAlreadyExists'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onAdd(tool, result.values.level as PermissionLevel);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t('config.permissionsTools.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormDialog
      open
      title={t('settingsPanel.security.addTool')}
      submitting={saving}
      confirmLabel={t('common.confirm')}
      cancelLabel={t('common.cancel')}
      onConfirm={() => void submit()}
      onCancel={onClose}
    >
      <Form
        form={form}
        optionalText={t('common.optional')}
        rules={{
          tool: [{ validator: (value) => (String(value).trim() ? undefined : t('settingsPanel.validation.required')) }],
        }}
        items={[
          {
            name: 'tool',
            label: t('config.permissionsTools.colTool'),
            component: 'input',
            placeholder: t('settingsPanel.security.toolNamePlaceholder'),
            required: true,
          },
          {
            name: 'level',
            label: t('config.permissionsTools.colLevel'),
            component: 'select',
            options: ['allow', 'ask', 'deny'].map((value) => ({
              value,
              label: t(`settingsPanel.security.permissionLevels.${value}`),
            })),
            required: true,
          },
        ]}
      />
      {error ? (
        <div className="settings-page__error" role="alert">
          {error}
        </div>
      ) : null}
    </FormDialog>
  );
}

export function SecuritySettings() {
  const { t } = useTranslation();
  const { isConnected, request, saveQueue } = useSettingsServices();
  const [tools, setTools] = useState<Record<string, PermissionLevel>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [removingTool, setRemovingTool] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState('');
  const loadRequestId = useRef(0);

  const load = useCallback(async () => {
    if (!isConnected) {
      setLoading(false);
      return;
    }
    const currentRequestId = ++loadRequestId.current;
    setLoading(true);
    setError('');
    try {
      const payload = await request<Record<string, unknown>>('permissions.tools.get', {});
      if (currentRequestId === loadRequestId.current) setTools(parseTools(payload));
    } catch (loadError) {
      if (currentRequestId === loadRequestId.current) {
        setError(loadError instanceof Error ? loadError.message : t('config.permissionsTools.loadFailed'));
      }
    } finally {
      if (currentRequestId === loadRequestId.current) setLoading(false);
    }
  }, [isConnected, request, t]);

  useEffect(() => {
    void load();
    return () => {
      loadRequestId.current += 1;
    };
  }, [load]);

  const update = async (tool: string, level: PermissionLevel) => {
    const result = await saveQueue.enqueue(`permissions.${tool}`, () =>
      request<Record<string, unknown>>('permissions.tools.update', { tool, level }),
    );
    setTools(parseTools(result));
  };
  const add = async (tool: string, level: PermissionLevel) => {
    const result = await saveQueue.enqueue(`permissions.${tool}.add`, () =>
      request<Record<string, unknown>>('permissions.tools.update', { tool, level }),
    );
    setTools(parseTools(result));
  };
  const remove = async () => {
    if (!removingTool || removing) return;
    setRemoving(true);
    setRemoveError('');
    try {
      const result = await saveQueue.enqueue(`permissions.${removingTool}.delete`, () =>
        request<Record<string, unknown>>('permissions.tools.delete', { tool: removingTool }),
      );
      setTools(parseTools(result));
      setRemovingTool(null);
    } catch (removeFailure) {
      setRemoveError(
        removeFailure instanceof Error ? removeFailure.message : t('config.permissionsTools.saveFailed'),
      );
    } finally {
      setRemoving(false);
    }
  };

  return (
    <SettingsSection
      title={t('settingsPanel.security.toolPermissions')}
      description={t('settingsPanel.security.toolPermissionsDescription')}
      action={
        <>
          <Button disabled={!isConnected || loading} onClick={() => void load()}>
            {t('config.permissionsTools.refresh')}
          </Button>
          <Button
            variant="primary"
            disabled={!isConnected || loading || Boolean(error)}
            onClick={() => setAdding(true)}
          >
            {t('settingsPanel.security.add')}
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="settings-page__loading">
          <Loading aria-label={t('common.loading')} />
        </div>
      ) : (
        <>
          {error ? (
            <div className="settings-page__error" role="alert">
              {error}
            </div>
          ) : null}
          {Object.entries(tools)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([tool, level]) => (
              <SettingRow key={tool} title={tool}>
                <Select
                  aria-label={tool}
                  value={level}
                  disabled={!isConnected}
                  options={['allow', 'ask', 'deny'].map((value) => ({
                    value,
                    label: t(`settingsPanel.security.permissionLevels.${value}`),
                  }))}
                  onChange={(next) => void update(tool, next as PermissionLevel).catch(() => undefined)}
                />
                <Button
                  variant="quiet"
                  icon={<settingsActionIcons.delete aria-hidden />}
                  aria-label={t('common.delete')}
                  title={t('common.delete')}
                  disabled={!isConnected}
                  onClick={() => {
                    setRemoveError('');
                    setRemovingTool(tool);
                  }}
                />
              </SettingRow>
            ))}
          {!error && !Object.keys(tools).length ? (
            <div className="settings-security__empty">
              <img src={settingsEmptyBoxIllustration} alt="" aria-hidden />
              <strong>{t('settingsPanel.security.noTools')}</strong>
              <p>{t('settingsPanel.security.noToolsDescription')}</p>
              <Button variant="primary" disabled={!isConnected} onClick={() => setAdding(true)}>
                {t('settingsPanel.security.addTool')}
              </Button>
            </div>
          ) : null}
          {adding ? <AddToolDialog existing={tools} onClose={() => setAdding(false)} onAdd={add} /> : null}
          <SettingsConfirmDialog
            open={removingTool !== null}
            title={t('settingsPanel.security.deleteToolTitle')}
            message={t('settingsPanel.security.deleteToolConfirm', { tool: removingTool ?? '' })}
            confirming={removing}
            error={removeError}
            onCancel={() => {
              if (!removing) setRemovingTool(null);
            }}
            onConfirm={() => void remove()}
          />
        </>
      )}
    </SettingsSection>
  );
}
