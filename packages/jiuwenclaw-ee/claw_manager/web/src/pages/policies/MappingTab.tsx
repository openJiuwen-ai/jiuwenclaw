import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, MappingApi } from '../../services/api';
import { useAsync } from '../../hooks/useAsync';
import type {
  ConfigDefaultTemplateMapping,
  ConfigDefaultTemplateMappingCreateBody,
} from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { toast } from '../../stores/uiStore';
import { formatTime, safeStringify } from '../../utils/format';

const TEMPLATE_TYPES = [
  'default_model',
  'video_model',
  'audio_model',
  'vision_model',
  'skill_whitelist',
  'channel',
  'service_resource',
];

interface FormState {
  user_id: string;
  group_id: string;
  priority: number;
  template_id: string;
  template_type: string;
  enabled: boolean;
  data: string;
}

const emptyForm: FormState = {
  user_id: '',
  group_id: '',
  priority: 0,
  template_id: '',
  template_type: 'default_model',
  enabled: true,
  data: '',
};

export function MappingTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [templateType, setTemplateType] = useState('');

  const { data, loading, error, reload } = useAsync(
    () =>
      MappingApi.list(instanceId, {
        page,
        page_size: pageSize,
        template_type: templateType || undefined,
      }),
    [instanceId, page, pageSize, templateType]
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigDefaultTemplateMapping | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigDefaultTemplateMapping | null>(null);
  const checkJson = useInvalidJsonChecker();

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        user_id: editing.user_id ?? '',
        group_id: editing.group_id ?? '',
        priority: editing.priority,
        template_id: editing.template_id,
        template_type: editing.template_type,
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
    if (!form.template_id.trim() || !form.template_type.trim()) {
      toast('warn', t('policies.mapping.templateId'));
      return;
    }
    const dataErr = checkJson(form.data);
    if (dataErr) {
      toast('danger', dataErr);
      return;
    }
    const body: ConfigDefaultTemplateMappingCreateBody = {
      user_id: form.user_id.trim() || undefined,
      group_id: form.group_id.trim() || undefined,
      priority: form.priority,
      template_id: form.template_id.trim(),
      template_type: form.template_type.trim(),
      enabled: form.enabled,
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
    };
    try {
      if (editing) {
        await MappingApi.update(instanceId, editing.id, body);
      } else {
        await MappingApi.create(instanceId, body);
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <select
          className="select !w-48"
          value={templateType}
          onChange={(e) => {
            setTemplateType(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t('policies.mapping.templateType')}: {t('common.all')}</option>
          {TEMPLATE_TYPES.map((tp) => (
            <option key={tp} value={tp}>
              {tp}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-2">
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
            + {t('policies.mapping.new')}
          </button>
        </div>
      </div>

      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : !data || data.items.length === 0 ? (
          <Empty text={t('common.empty')} />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>id</th>
                <th>{t('policies.mapping.templateType')}</th>
                <th>{t('policies.mapping.templateId')}</th>
                <th>{t('policies.mapping.userId')}</th>
                <th>{t('policies.mapping.groupId')}</th>
                <th>{t('policies.mapping.priority')}</th>
                <th>{t('common.enabled')}</th>
                <th>updated</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td className="mono text-xs">{row.id}</td>
                  <td className="mono text-xs">{row.template_type}</td>
                  <td className="mono text-xs">{row.template_id}</td>
                  <td className="mono text-xs">{row.user_id ?? '-'}</td>
                  <td className="mono text-xs">{row.group_id ?? '-'}</td>
                  <td className="mono text-xs">{row.priority}</td>
                  <td>
                    <span className={`pill ${row.enabled ? 'ok' : 'muted'} !text-[11px]`}>
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

      {data && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={data.total ?? data.items.length}
          onChange={(p) => setPage(p)}
        />
      )}

      <Modal
        open={modalOpen}
        title={editing ? t('common.edit') : t('policies.mapping.new')}
        onClose={() => setModalOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" onClick={() => setModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" onClick={submit}>
              {t('common.save')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label">{t('policies.mapping.templateType')}</label>
            <select
              className="select"
              value={form.template_type}
              onChange={(e) => update('template_type', e.target.value)}
            >
              {TEMPLATE_TYPES.map((tp) => (
                <option key={tp} value={tp}>
                  {tp}
                </option>
              ))}
            </select>
            <div className="text-[11px] text-muted mt-1">{t('policies.mapping.templateTypeHint')}</div>
          </div>
          <div>
            <label className="label">{t('policies.mapping.templateId')}</label>
            <input
              className="input"
              value={form.template_id}
              onChange={(e) => update('template_id', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('policies.mapping.userId')}</label>
            <input className="input" value={form.user_id} onChange={(e) => update('user_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('policies.mapping.groupId')}</label>
            <input className="input" value={form.group_id} onChange={(e) => update('group_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('policies.mapping.priority')}</label>
            <input
              className="input"
              type="number"
              value={form.priority}
              onChange={(e) => update('priority', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 mt-5 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => update('enabled', e.target.checked)}
              />
              <span>{t('common.enabled')}</span>
            </label>
          </div>
          <div className="md:col-span-2">
            <JsonField label="data (JSON, 可选)" value={form.data} onChange={(v) => update('data', v)} rows={4} />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!delTarget}
        message={t('policies.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await MappingApi.remove(instanceId, delTarget.id);
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
