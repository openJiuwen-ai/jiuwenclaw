import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, LoggingApi } from '../../../services/api';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';
import type { LogLevel } from '../../../types';

interface FormState {
  level: LogLevel;
  console_level: LogLevel;
  gateway: LogLevel;
  channel: LogLevel;
  agent_server: LogLevel;
  full: LogLevel;
}

type FormField = keyof FormState;

const LOG_LEVELS: LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'NOTSET'];

const DEFAULT_FORM: FormState = {
  level: 'INFO',
  console_level: 'INFO',
  gateway: 'INFO',
  channel: 'INFO',
  agent_server: 'INFO',
  full: 'INFO',
};

function toFormState(data: Partial<FormState>): FormState {
  return {
    level: data.level ?? DEFAULT_FORM.level,
    console_level: data.console_level ?? DEFAULT_FORM.console_level,
    gateway: data.gateway ?? DEFAULT_FORM.gateway,
    channel: data.channel ?? DEFAULT_FORM.channel,
    agent_server: data.agent_server ?? DEFAULT_FORM.agent_server,
    full: data.full ?? DEFAULT_FORM.full,
  };
}

interface Props {
  instanceId: string;
}

export function LoggingTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasRemoteConfig, setHasRemoteConfig] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await LoggingApi.get(instanceId);
      setForm(
        toFormState({
          level: data.level,
          console_level: data.console_level ?? undefined,
          gateway: data.gateway ?? undefined,
          channel: data.channel ?? undefined,
          agent_server: data.agent_server ?? undefined,
          full: data.full ?? undefined,
        })
      );
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setForm(DEFAULT_FORM);
        setHasRemoteConfig(false);
        setUpdatedAt(undefined);
      } else {
        setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const update = (key: FormField, value: LogLevel) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const data = await LoggingApi.upsert(instanceId, { ...form });
      setHasRemoteConfig(true);
      setUpdatedAt(data.updated_at);
      toast('success', t('success.saved'));
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    } finally {
      setSaving(false);
    }
  };

  const removeConfig = async () => {
    try {
      await LoggingApi.remove(instanceId);
      setForm(DEFAULT_FORM);
      setHasRemoteConfig(false);
      setUpdatedAt(undefined);
      toast('success', t('instanceConfig.logging.deleted'));
    } catch (e) {
      toast(
        'danger',
        t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    }
  };

  const fields: { key: FormField; labelKey: string; hintKey?: string }[] = [
    { key: 'level', labelKey: 'level', hintKey: 'levelHint' },
    { key: 'console_level', labelKey: 'consoleLevel', hintKey: 'consoleLevelHint' },
    { key: 'gateway', labelKey: 'gateway' },
    { key: 'channel', labelKey: 'channel' },
    { key: 'agent_server', labelKey: 'agentServer' },
    { key: 'full', labelKey: 'full', hintKey: 'fullHint' },
  ];

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  if (loadError) {
    return (
      <div className="p-4 text-sm text-danger">
        {t('errors.loadFailed', { detail: loadError })}
        <button className="btn sm ml-2" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        {hasRemoteConfig && (
          <>
            <span className="pill sm ok">
              <span className="statusDot ok" />
              {t('instanceConfig.logging.managed')}
            </span>
            {updatedAt && (
              <span className="text-[11px] text-muted mono">{formatTime(updatedAt)}</span>
            )}
          </>
        )}
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        {hasRemoteConfig && (
          <button className="btn sm danger" onClick={() => setDeleteOpen(true)}>
            {t('instanceConfig.logging.resetToYaml')}
          </button>
        )}
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
        {fields.map(({ key, labelKey, hintKey }) => (
          <div key={key}>
            <label className="label">
              {t(`instanceConfig.logging.${labelKey}`)}
            </label>
            {hintKey && (
              <p className="text-[11px] text-muted mb-1">{t(`instanceConfig.logging.${hintKey}`)}</p>
            )}
            <select
              className="select w-full"
              value={form[key]}
              onChange={(e) => update(key, e.target.value as LogLevel)}
            >
              {LOG_LEVELS.map((lv) => (
                <option key={lv} value={lv}>
                  {lv}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteOpen}
        message={t('instanceConfig.logging.deleteConfirm')}
        danger
        onConfirm={removeConfig}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}
