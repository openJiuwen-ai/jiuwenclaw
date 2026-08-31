import { FormEvent, useEffect, useMemo, useState } from 'react';
import { LoaderCircle, Power, Save, Settings2, X } from 'lucide-react';

import { fetchApplicationPluginSettings, setApplicationPluginEnabled, updateApplicationPluginSettings } from './api';
import {
  applicationPluginSecretMask,
  applicationPluginSettingsToDraft,
  isApplicationPluginSettingVisible,
  serializeApplicationPluginDraft,
  type ApplicationPluginDraftValue,
} from './configuration';
import type { ApplicationPluginContribution, ApplicationPluginSettingsPayload } from './types';

export function ApplicationPluginConfiguration({
  contribution,
  onManifestChanged,
}: {
  contribution: ApplicationPluginContribution;
  onManifestChanged: () => void;
}) {
  const [settings, setSettings] = useState<ApplicationPluginSettingsPayload | null>(null);
  const [draft, setDraft] = useState<Record<string, ApplicationPluginDraftValue>>({});
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const properties = settings?.config_schema.properties || contribution.config_schema?.properties || {};
  const orderedFields = useMemo(
    () => Object.entries(properties).sort((left, right) => (left[1]['x-order'] ?? 1000) - (right[1]['x-order'] ?? 1000) || left[0].localeCompare(right[0])),
    [properties],
  );
  const visibleFields = useMemo(() => orderedFields.filter(([, definition]) => isApplicationPluginSettingVisible(definition, draft)), [draft, orderedFields]);

  const applySettings = (payload: ApplicationPluginSettingsPayload) => {
    setSettings(payload);
    setDraft(applicationPluginSettingsToDraft(
      payload.values,
      payload.config_schema.properties || {},
      payload.configured_secrets,
      payload.configured_secret_lengths,
    ));
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    void fetchApplicationPluginSettings(contribution.plugin_id)
      .then(payload => {
        if (active) applySettings(payload);
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '无法读取插件设置');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [contribution.plugin_id]);

  const enabled = settings?.enabled ?? contribution.enabled !== false;
  const configuredSecrets = new Set(settings?.configured_secrets || []);
  const configuredSecretLengths = settings?.configured_secret_lengths || {};

  const toggleEnabled = async () => {
    if (saving || loading) return;
    setSaving(true);
    setError('');
    try {
      applySettings(await setApplicationPluginEnabled(contribution.plugin_id, !enabled));
      onManifestChanged();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : '无法更新插件状态');
    } finally {
      setSaving(false);
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const values = serializeApplicationPluginDraft(
        draft,
        properties,
        settings?.configured_secrets,
        settings?.configured_secret_lengths,
      );
      applySettings(await updateApplicationPluginSettings(contribution.plugin_id, values, []));
      setExpanded(false);
      onManifestChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '无法保存插件设置');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="application-plugin-configuration">
      <div className="application-plugin-configuration__summary">
        <div>
          <span>{contribution.description || '该插件未提供说明。'}</span>
          {error && <small>{error}</small>}
        </div>
        <div className="application-plugin-configuration__actions">
          <button type="button" className={enabled ? 'is-enabled' : ''} disabled={loading || saving} onClick={() => void toggleEnabled()}>
            {loading || saving ? <LoaderCircle className="is-spinning" aria-hidden /> : <Power aria-hidden />}
            {enabled ? '禁用' : '启用'}
          </button>
          {orderedFields.length > 0 && (
            <button type="button" disabled={loading || saving} onClick={() => setExpanded(value => !value)}>
              {expanded ? <X aria-hidden /> : <Settings2 aria-hidden />}
              {expanded ? '收起' : '设置'}
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <form className="application-plugin-configuration__form" onSubmit={event => void save(event)}>
          {visibleFields.map(([key, definition], index) => {
            const group = definition['x-group'] || '设置';
            const previousGroup = index > 0 ? visibleFields[index - 1][1]['x-group'] || '设置' : '';
            const showGroup = group !== previousGroup;
            const secret = definition.secret === true || definition.format === 'password';
            const configured = secret && configuredSecrets.has(key);
            const secretMask = applicationPluginSecretMask(configuredSecretLengths[key]);
            const label = definition.title || key;
            return (
              <div className="application-plugin-configuration__field-wrap" key={key}>
                {showGroup && <h3>{group}</h3>}
                {definition.type === 'boolean' ? (
                  <label className="application-plugin-configuration__checkbox">
                    <input
                      type="checkbox"
                      checked={draft[key] === true}
                      onChange={event => setDraft(current => ({ ...current, [key]: event.target.checked }))}
                    />
                    <span>{label}</span>
                  </label>
                ) : (
                  <label className="application-plugin-configuration__field">
                    <span>{label}</span>
                    {definition.enum ? (
                      <select value={String(draft[key] ?? '')} onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))}>
                        {definition.enum.map(option => (
                          <option value={String(option)} key={String(option)}>
                            {String(option)}
                          </option>
                        ))}
                      </select>
                    ) : definition.type === 'array' || definition.type === 'object' ? (
                      <textarea value={String(draft[key] ?? '')} onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))} />
                    ) : (
                      <input
                        type={secret ? 'password' : definition.type === 'integer' || definition.type === 'number' ? 'number' : 'text'}
                        value={String(draft[key] ?? '')}
                        min={definition.minimum}
                        max={definition.maximum}
                        placeholder={configured ? secretMask : ''}
                        onFocus={() => {
                          if (configured && draft[key] === secretMask) {
                            setDraft(current => ({ ...current, [key]: '' }));
                          }
                        }}
                        onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))}
                      />
                    )}
                    {definition.description && <small>{definition.description}</small>}
                  </label>
                )}
              </div>
            );
          })}
          <footer>
            <button type="button" onClick={() => setExpanded(false)}>
              取消
            </button>
            <button type="submit" className="is-primary" disabled={saving}>
              {saving ? <LoaderCircle className="is-spinning" aria-hidden /> : <Save aria-hidden />}
              {saving ? '保存中' : '保存设置'}
            </button>
          </footer>
        </form>
      )}
    </div>
  );
}
