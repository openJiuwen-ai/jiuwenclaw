import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, ChannelApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import type { ChannelConfig } from '../../../types';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal } from '../../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../../components/JsonField';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';

interface FormState {
  channel_id: string;
  channel_name: string;
  channel_type: string;
  bot_id: string;
  status: string;
  config: string;
}

const emptyForm: FormState = {
  channel_id: '',
  channel_name: '',
  channel_type: 'web',
  bot_id: 'default',
  status: 'active',
  config: '{}',
};

export function ChannelTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [channelType, setChannelType] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ChannelConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const checkJson = useInvalidJsonChecker();

  const { data, loading, error, reload } = useAsync(
    () =>
      ChannelApi.list(instanceId, {
        channel_type: channelType || undefined,
        status: statusFilter || undefined,
      }),
    [instanceId, channelType, statusFilter]
  );

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.channel_id.trim()) {
      toast('warn', t('instanceConfig.channel.channelId'));
      return;
    }
    if (!form.channel_name.trim()) {
      toast('warn', t('instanceConfig.channel.channelName'));
      return;
    }
    const configErr = checkJson(form.config);
    if (configErr) {
      toast('danger', configErr);
      return;
    }
    setSaving(true);
    try {
      await ChannelApi.register(instanceId, {
        channel_id: form.channel_id.trim(),
        channel_name: form.channel_name.trim(),
        channel_type: form.channel_type.trim(),
        bot_id: form.bot_id.trim(),
        status: form.status,
        config: form.config.trim() ? (tryParseJson(form.config, {}) as Record<string, unknown>) : undefined,
      });
      toast('success', t('success.saved'));
      setModalOpen(false);
      setForm(emptyForm);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async (row: ChannelConfig, activate: boolean) => {
    try {
      if (activate) {
        await ChannelApi.activate(instanceId, row.channel_id);
      } else {
        await ChannelApi.deactivate(instanceId, row.channel_id);
      }
      toast('success', t('success.saved'));
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          className="input !w-36"
          placeholder={t('instanceConfig.channel.filterType')}
          value={channelType}
          onChange={(e) => setChannelType(e.target.value)}
        />
        <select
          className="select !w-32"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">{t('common.all')}</option>
          <option value="active">{t('instanceConfig.channel.statusActive')}</option>
          <option value="inactive">{t('instanceConfig.channel.statusInactive')}</option>
        </select>
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        <button
          className="btn primary sm"
          onClick={() => {
            setForm(emptyForm);
            setModalOpen(true);
          }}
        >
          + {t('instanceConfig.channel.register')}
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
                <th>{t('instanceConfig.channel.channelId')}</th>
                <th>{t('instanceConfig.channel.channelName')}</th>
                <th>{t('instanceConfig.channel.channelType')}</th>
                <th>bot_id</th>
                <th>{t('common.enabled')}</th>
                <th>updated</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.channel_id}>
                  <td className="mono text-xs">{row.channel_id}</td>
                  <td>{row.channel_name}</td>
                  <td><span className="tag">{row.channel_type}</span></td>
                  <td className="mono text-xs">{row.bot_id}</td>
                  <td>
                    <span className={`pill sm ${row.status === 'active' ? 'ok' : 'muted'}`}>
                      <span className={`statusDot ${row.status === 'active' ? 'ok' : 'muted'}`} />
                      {row.status}
                    </span>
                  </td>
                  <td className="mono text-[11px] text-muted">{formatTime(row.updated_at)}</td>
                  <td>
                    <div className="flex items-center gap-1">
                      {row.status !== 'active' ? (
                        <button className="btn sm ghost" onClick={() => void toggleStatus(row, true)}>
                          {t('instanceConfig.channel.activate')}
                        </button>
                      ) : (
                        <button className="btn sm ghost" onClick={() => void toggleStatus(row, false)}>
                          {t('instanceConfig.channel.deactivate')}
                        </button>
                      )}
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
        title={t('instanceConfig.channel.register')}
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
          <div>
            <label className="label">{t('instanceConfig.channel.channelId')}</label>
            <input className="input" value={form.channel_id} onChange={(e) => update('channel_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('instanceConfig.channel.channelName')}</label>
            <input className="input" value={form.channel_name} onChange={(e) => update('channel_name', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('instanceConfig.channel.channelType')}</label>
            <input className="input" value={form.channel_type} onChange={(e) => update('channel_type', e.target.value)} />
          </div>
          <div>
            <label className="label">bot_id</label>
            <input className="input" value={form.bot_id} onChange={(e) => update('bot_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('instanceConfig.channel.initialStatus')}</label>
            <select className="select" value={form.status} onChange={(e) => update('status', e.target.value)}>
              <option value="active">{t('instanceConfig.channel.statusActive')}</option>
              <option value="inactive">{t('instanceConfig.channel.statusInactive')}</option>
            </select>
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('instanceConfig.channel.config')}
              value={form.config}
              onChange={(v) => update('config', v)}
              rows={6}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        message={t('instanceConfig.channel.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await ChannelApi.remove(instanceId, delTarget.channel_id);
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
