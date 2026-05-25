import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, ServicePolicyApi } from '../../services/api';
import { useAsync } from '../../hooks/useAsync';
import type { ConfigEffectiveServicePolicy } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { toast } from '../../stores/uiStore';
import { safeStringify, truncate } from '../../utils/format';

interface FormState {
  service_id: string;
  priority: number;
  match_expr: string;
  template_ref: string;
  enabled: boolean;
  data: string;
}

const emptyForm: FormState = {
  service_id: '',
  priority: 0,
  match_expr: '',
  template_ref: '{}',
  enabled: true,
  data: '',
};

export function ServicePoliciesTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);

  const { data, loading, error, reload } = useAsync(
    () => ServicePolicyApi.list(instanceId, { page, page_size: pageSize }),
    [instanceId, page, pageSize]
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigEffectiveServicePolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigEffectiveServicePolicy | null>(null);
  const checkJson = useInvalidJsonChecker();

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        service_id: editing.service_id,
        priority: editing.priority,
        match_expr: editing.match_expr ?? '',
        template_ref: safeStringify(editing.template_ref ?? {}, 2),
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
    if (!form.service_id.trim()) { toast('warn', t('policies.service.serviceId')); return; }
    const refErr = checkJson(form.template_ref);
    const dataErr = checkJson(form.data);
    if (refErr) { toast('danger', refErr); return; }
    if (dataErr) { toast('danger', dataErr); return; }
    const body = {
      service_id: form.service_id.trim(),
      priority: form.priority,
      match_expr: form.match_expr.trim() || undefined,
      template_ref: tryParseJson(form.template_ref, {}) as Record<string, string>,
      enabled: form.enabled,
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
    };
    try {
      if (editing) {
        await ServicePolicyApi.update(instanceId, editing.id, body);
      } else {
        await ServicePolicyApi.create(instanceId, body);
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
      <div className="flex items-center justify-end gap-2">
        <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
        <button
          className="btn primary sm"
          onClick={() => { setEditing(null); setModalOpen(true); }}
        >
          + {t('policies.service.new')}
        </button>
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
                <th>{t('policies.service.serviceId')}</th>
                <th>{t('policies.global.priority')}</th>
                <th>{t('policies.service.matchExpr')}</th>
                <th>{t('policies.global.templateRef')}</th>
                <th>{t('common.enabled')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td className="mono text-xs">{row.id}</td>
                  <td className="mono text-xs">{row.service_id}</td>
                  <td className="mono text-xs">{row.priority}</td>
                  <td className="mono text-[11px] text-muted">{truncate(row.match_expr ?? '-', 30)}</td>
                  <td className="mono text-[11px] text-muted">
                    {truncate(safeStringify(row.template_ref, 0), 40)}
                  </td>
                  <td>
                    <span className={`pill ${row.enabled ? 'ok' : 'muted'} !text-[11px]`}>
                      {row.enabled ? t('common.enabled') : t('common.disabled')}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-1">
                      <button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>
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
        <Pagination page={page} pageSize={pageSize} total={data.total ?? data.items.length} onChange={setPage} />
      )}

      <Modal
        open={modalOpen}
        title={editing ? t('common.edit') : t('policies.service.new')}
        onClose={() => setModalOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" onClick={() => setModalOpen(false)}>{t('common.cancel')}</button>
            <button className="btn primary" onClick={submit}>{t('common.save')}</button>
          </>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label">{t('policies.service.serviceId')}</label>
            <input className="input" value={form.service_id} onChange={(e) => update('service_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('policies.global.priority')}</label>
            <input
              className="input"
              type="number"
              value={form.priority}
              onChange={(e) => update('priority', Number(e.target.value))}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('policies.service.matchExpr')}</label>
            <input className="input" value={form.match_expr} onChange={(e) => update('match_expr', e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('policies.global.templateRef')}
              hint={t('policies.global.templateRefHint')}
              value={form.template_ref}
              onChange={(v) => update('template_ref', v)}
              rows={6}
            />
          </div>
          <div>
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input type="checkbox" checked={form.enabled} onChange={(e) => update('enabled', e.target.checked)} />
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
            await ServicePolicyApi.remove(instanceId, delTarget.id);
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
