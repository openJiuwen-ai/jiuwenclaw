import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, LogMaskingRuleApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import type { LogMaskingRule } from '../../../types';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal } from '../../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../../components/JsonField';
import { toast } from '../../../stores/uiStore';
import { formatTime, safeStringify, truncate } from '../../../utils/format';

interface FormState {
  rule_name: string;
  description: string;
  pattern: string;
  replacement: string;
  priority: number;
  enabled: boolean;
  data: string;
}

const emptyForm: FormState = {
  rule_name: '',
  description: '',
  pattern: '',
  replacement: '******',
  priority: 0,
  enabled: true,
  data: '',
};

export function LogMaskingTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LogMaskingRule | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<LogMaskingRule | null>(null);
  const [saving, setSaving] = useState(false);
  const checkJson = useInvalidJsonChecker();

  const { data, loading, error, reload } = useAsync(
    () =>
      LogMaskingRuleApi.list(instanceId, {
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
      }),
    [instanceId, enabledFilter]
  );

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        rule_name: editing.rule_name,
        description: editing.description ?? '',
        pattern: editing.pattern,
        replacement: editing.replacement,
        priority: editing.priority,
        enabled: editing.enabled,
        data: safeStringify(editing.data ?? {}, 2),
      });
    } else {
      setForm(emptyForm);
    }
  }, [modalOpen, editing]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.rule_name.trim()) {
      toast('warn', t('instanceConfig.logMasking.ruleName'));
      return;
    }
    if (!form.pattern.trim()) {
      toast('warn', t('instanceConfig.logMasking.pattern'));
      return;
    }
    const dataErr = checkJson(form.data);
    if (dataErr) {
      toast('danger', dataErr);
      return;
    }
    const body = {
      rule_name: form.rule_name.trim(),
      description: form.description.trim() || undefined,
      pattern: form.pattern.trim(),
      replacement: form.replacement.trim() || undefined,
      priority: form.priority,
      enabled: form.enabled,
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
    };
    setSaving(true);
    try {
      if (editing) {
        await LogMaskingRuleApi.update(instanceId, editing.rule_id, body);
      } else {
        await LogMaskingRuleApi.create(instanceId, body);
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="select !w-32"
          value={enabledFilter}
          onChange={(e) => setEnabledFilter(e.target.value)}
        >
          <option value="">{t('common.all')}</option>
          <option value="true">{t('common.enabled')}</option>
          <option value="false">{t('common.disabled')}</option>
        </select>
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        <button
          className="btn primary sm"
          onClick={() => {
            setEditing(null);
            setModalOpen(true);
          }}
        >
          + {t('instanceConfig.logMasking.new')}
        </button>
      </div>

      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : items.length === 0 ? (
          <Empty text={t('common.empty')} />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t('instanceConfig.logMasking.ruleName')}</th>
                <th>{t('instanceConfig.logMasking.pattern')}</th>
                <th>{t('instanceConfig.logMasking.replacement')}</th>
                <th>{t('instanceConfig.logMasking.priority')}</th>
                <th>{t('instanceConfig.logMasking.source')}</th>
                <th>{t('common.enabled')}</th>
                <th>updated</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.rule_id}>
                  <td>
                    <div className="font-medium">{row.rule_name}</div>
                    <div className="text-[11px] text-muted mono">{row.rule_id}</div>
                  </td>
                  <td className="mono text-[11px] text-muted" title={row.pattern}>
                    {truncate(row.pattern, 40)}
                  </td>
                  <td className="mono text-xs">{row.replacement}</td>
                  <td className="mono text-xs">{row.priority}</td>
                  <td><span className="tag">{row.source}</span></td>
                  <td>
                    <span className={`pill sm ${row.enabled ? 'ok' : 'muted'}`}>
                      <span className={`statusDot ${row.enabled ? 'ok' : 'muted'}`} />
                      {row.enabled ? t('common.enabled') : t('common.disabled')}
                    </span>
                  </td>
                  <td className="mono text-[11px] text-muted">{formatTime(row.updated_at)}</td>
                  <td>
                    <div className="flex items-center gap-1">
                      <button
                        className="btn sm ghost"
                        onClick={() => {
                          setEditing(row);
                          setModalOpen(true);
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      <button className="btn sm danger" onClick={() => setDelTarget(row)}>
                        {t('common.delete')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal
        open={modalOpen}
        title={editing ? t('instanceConfig.logMasking.edit') : t('instanceConfig.logMasking.new')}
        onClose={() => setModalOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" onClick={() => setModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" onClick={submit} disabled={saving}>
              {saving ? t('common.loading') : t('common.save')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.logMasking.ruleName')}</label>
            <input className="input" value={form.rule_name} onChange={(e) => update('rule_name', e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('common.detail')}</label>
            <input className="input" value={form.description} onChange={(e) => update('description', e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.logMasking.pattern')}</label>
            <input className="input mono text-xs" value={form.pattern} onChange={(e) => update('pattern', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('instanceConfig.logMasking.replacement')}</label>
            <input className="input" value={form.replacement} onChange={(e) => update('replacement', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('instanceConfig.logMasking.priority')}</label>
            <input
              className="input"
              type="number"
              value={form.priority}
              onChange={(e) => update('priority', Number(e.target.value))}
            />
          </div>
          <div className="md:col-span-2">
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => update('enabled', e.target.checked)}
              />
              <span>{t('common.enabled')}</span>
            </label>
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('instanceConfig.logMasking.data')}
              value={form.data}
              onChange={(v) => update('data', v)}
              rows={4}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        message={t('instanceConfig.logMasking.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await LogMaskingRuleApi.remove(instanceId, delTarget.rule_id);
            toast('success', t('success.deleted'));
            void reload();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </div>
  );
}
