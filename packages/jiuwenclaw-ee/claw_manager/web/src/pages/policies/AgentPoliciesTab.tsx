import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentPolicyApi, ApiError } from '../../services/api';
import { useAsync } from '../../hooks/useAsync';
import type { ConfigEffectiveAgentPolicy } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { toast } from '../../stores/uiStore';
import { formatTime, safeStringify, truncate } from '../../utils/format';

interface FormState {
  agent_id: string;
  service_policy_id: number;
  priority: number;
  match_expr: string;
  template_ref: string;
  send_file_allowed: boolean;
  enabled: boolean;
  data: string;
}

const emptyForm: FormState = {
  agent_id: '',
  service_policy_id: 0,
  priority: 0,
  match_expr: '',
  template_ref: '{}',
  send_file_allowed: false,
  enabled: true,
  data: '',
};

export function AgentPoliciesTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [filterServicePolicyId, setFilterServicePolicyId] = useState<string>('');

  const { data, loading, error, reload } = useAsync(
    () =>
      AgentPolicyApi.list(instanceId, {
        page,
        page_size: pageSize,
        service_policy_id: filterServicePolicyId ? Number(filterServicePolicyId) : undefined,
      }),
    [instanceId, page, pageSize, filterServicePolicyId]
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigEffectiveAgentPolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigEffectiveAgentPolicy | null>(null);
  const checkJson = useInvalidJsonChecker();

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        agent_id: editing.agent_id,
        service_policy_id: editing.service_policy_id,
        priority: editing.priority,
        match_expr: editing.match_expr ?? '',
        template_ref: safeStringify(editing.template_ref ?? {}, 2),
        send_file_allowed: editing.send_file_allowed ?? false,
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
    if (!form.agent_id.trim()) { toast('warn', t('policies.agent.agentId')); return; }
    if (!form.service_policy_id) { toast('warn', t('policies.agent.servicePolicyId')); return; }
    const refErr = checkJson(form.template_ref);
    const dataErr = checkJson(form.data);
    if (refErr) { toast('danger', refErr); return; }
    if (dataErr) { toast('danger', dataErr); return; }
    const body = {
      agent_id: form.agent_id.trim(),
      service_policy_id: form.service_policy_id,
      priority: form.priority,
      match_expr: form.match_expr.trim() || undefined,
      template_ref: tryParseJson(form.template_ref, {}) as Record<string, string>,
      send_file_allowed: form.send_file_allowed,
      enabled: form.enabled,
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
    };
    try {
      if (editing) {
        await AgentPolicyApi.update(instanceId, editing.id, body);
      } else {
        await AgentPolicyApi.create(instanceId, body);
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
        <input
          className="input !w-52"
          placeholder={`filter ${t('policies.agent.servicePolicyId')}`}
          value={filterServicePolicyId}
          onChange={(e) => {
            setFilterServicePolicyId(e.target.value);
            setPage(1);
          }}
        />
        <div className="flex items-center gap-2">
          <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
          <button
            className="btn primary sm"
            onClick={() => { setEditing(null); setModalOpen(true); }}
          >
            + {t('policies.agent.new')}
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
                <th>{t('policies.agent.agentId')}</th>
                <th>{t('policies.agent.servicePolicyId')}</th>
                <th>{t('policies.global.priority')}</th>
                <th>{t('policies.service.matchExpr')}</th>
                <th>{t('policies.agent.sendFileAllowed')}</th>
                <th>{t('common.enabled')}</th>
                <th>updated</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td className="mono text-xs">{row.id}</td>
                  <td className="mono text-xs">{row.agent_id}</td>
                  <td className="mono text-xs">{row.service_policy_id}</td>
                  <td className="mono text-xs">{row.priority}</td>
                  <td className="mono text-[11px] text-muted">{truncate(row.match_expr ?? '-', 30)}</td>
                  <td>
                    <span className={`pill ${row.send_file_allowed ? 'ok' : 'muted'} !text-[11px]`}>
                      {row.send_file_allowed ? t('common.yes') : t('common.no')}
                    </span>
                  </td>
                  <td>
                    <span className={`pill ${row.enabled ? 'ok' : 'muted'} !text-[11px]`}>
                      {row.enabled ? t('common.enabled') : t('common.disabled')}
                    </span>
                  </td>
                  <td className="mono text-[11px] text-muted">{formatTime(row.updated_at)}</td>
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
        title={editing ? t('common.edit') : t('policies.agent.new')}
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
            <label className="label">{t('policies.agent.agentId')}</label>
            <input className="input" value={form.agent_id} onChange={(e) => update('agent_id', e.target.value)} />
          </div>
          <div>
            <label className="label">{t('policies.agent.servicePolicyId')}</label>
            <input
              className="input"
              type="number"
              value={form.service_policy_id}
              onChange={(e) => update('service_policy_id', Number(e.target.value))}
            />
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
          <div>
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 mt-5 w-fit hover:bg-bg-hover">
              <input type="checkbox" checked={form.send_file_allowed} onChange={(e) => update('send_file_allowed', e.target.checked)} />
              <span>{t('policies.agent.sendFileAllowed')}</span>
            </label>
          </div>
          <div>
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 mt-5 w-fit hover:bg-bg-hover">
              <input type="checkbox" checked={form.enabled} onChange={(e) => update('enabled', e.target.checked)} />
              <span>{t('common.enabled')}</span>
            </label>
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
            await AgentPolicyApi.remove(instanceId, delTarget.id);
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
