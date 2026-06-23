import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentPolicyApi, ApiError, ServicePolicyApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useListSearch } from '../../../hooks/useListSearch';
import type { ConfigEffectiveAgentPolicy, ConfigEffectiveServicePolicy } from '../../../types';
import { Empty } from '../../../components/Empty';
import { Pagination } from '../../../components/Pagination';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal } from '../../../components/Modal';
import { Switch } from '../../../components/Switch';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';
import { JsonHoverPreview } from '../../../components/JsonHoverPreview';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { TemplateRefEditor } from '../../../components/TemplateRefEditor';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { toast } from '../../../stores/uiStore';
import { formatTime, truncate } from '../../../utils/format';
import { validateMatchExprModel, parseMatchExpr } from '../../../utils/matchExpr';
import {
  findSingleValueTemplateRefViolation,
  normalizeTemplateRefFromApi,
  type TemplateRefMap,
} from '../../../utils/templateRef';

/** 与 config_effective_agent_policy 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  policy_name: 128,
  policy_desc: 512,
  agent_id: 512,
} as const;

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  policy_name: string;
  policy_desc: string;
  agent_id: string;
  service_policy_id: string;
  priority: number;
  match_expr: string;
  template_ref: TemplateRefMap;
  send_file_allowed: boolean;
}

const emptyForm: FormState = {
  policy_name: '',
  policy_desc: '',
  agent_id: '',
  service_policy_id: '',
  priority: 0,
  match_expr: '',
  template_ref: {},
  send_file_allowed: false,
};

type AgentSortField =
  | 'policy_name'
  | 'policy_desc'
  | 'service_policy_id'
  | 'priority'
  | 'match_expr'
  | 'agent_id'
  | 'updated_at';

export function AgentPoliciesTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sendFileAllowedFilter, setSendFileAllowedFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<AgentSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const { searchInput, setSearchInput, searchQuery } = useListSearch();

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: AgentSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const { data, loading, error, reload } = useAsync(
    () =>
      AgentPolicyApi.list(instanceId, {
        page,
        page_size: pageSize,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        send_file_allowed:
          sendFileAllowedFilter === '' ? undefined : sendFileAllowedFilter === 'true',
        search: searchQuery,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, page, pageSize, enabledFilter, sendFileAllowedFilter, searchQuery, sortBy, sortOrder]
  );

  const [items, setItems] = useState<ConfigEffectiveAgentPolicy[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigEffectiveAgentPolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigEffectiveAgentPolicy | null>(null);
  const [togglingEnabledId, setTogglingEnabledId] = useState<number | null>(null);
  const [togglingSendFileId, setTogglingSendFileId] = useState<number | null>(null);
  const [servicePolicyOptions, setServicePolicyOptions] = useState<ConfigEffectiveServicePolicy[]>([]);
  const [loadingServicePolicies, setLoadingServicePolicies] = useState(false);

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        policy_name: editing.policy_name ?? '',
        policy_desc: editing.policy_desc ?? '',
        agent_id: editing.agent_id,
        service_policy_id: editing.service_policy_id,
        priority: editing.priority,
        match_expr: editing.match_expr ?? '',
        template_ref: normalizeTemplateRefFromApi(editing.template_ref),
        send_file_allowed: editing.send_file_allowed ?? false,
      });
    } else {
      setForm(emptyForm);
    }
  }, [modalOpen, editing]);

  useEffect(() => {
    let cancelled = false;
    setLoadingServicePolicies(true);
    void ServicePolicyApi.list(instanceId, { page: 1, page_size: 200 })
      .then((res) => {
        if (!cancelled) setServicePolicyOptions(res.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setServicePolicyOptions([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingServicePolicies(false);
      });
    return () => {
      cancelled = true;
    };
  }, [instanceId]);

  const servicePolicyById = useMemo(
    () => new Map(servicePolicyOptions.map((sp) => [sp.policy_id, sp])),
    [servicePolicyOptions],
  );

  const servicePolicyOptionIds = new Set(servicePolicyOptions.map((sp) => sp.policy_id));
  const legacyServicePolicyId =
    form.service_policy_id && !servicePolicyOptionIds.has(form.service_policy_id)
      ? form.service_policy_id
      : '';

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.policy_name.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.policyName') }));
      return;
    }
    if (!form.agent_id.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.agent.agentId') }));
      return;
    }
    if (!form.service_policy_id.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.agent.servicePolicyId') }));
      return;
    }
    if (!Number.isInteger(form.priority)) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.global.priority') }));
      return;
    }
    const matchExprErr = validateMatchExprModel(parseMatchExpr(form.match_expr));
    if (matchExprErr) {
      toast('warn', t('policies.matchExpr.invalid'));
      return;
    }
    const singleValueViolation = findSingleValueTemplateRefViolation(form.template_ref);
    if (singleValueViolation) {
      toast('warn', t('policies.templateRef.singleValueOnly', {
        slot: t(`policies.templateRef.slots.${singleValueViolation}`, {
          defaultValue: singleValueViolation,
        }),
      }));
      return;
    }
    const body = {
      policy_name: form.policy_name.trim(),
      policy_desc: form.policy_desc.trim() || undefined,
      agent_id: form.agent_id.trim(),
      service_policy_id: form.service_policy_id.trim(),
      priority: form.priority,
      match_expr: form.match_expr.trim() || undefined,
      template_ref: form.template_ref,
      send_file_allowed: form.send_file_allowed,
    };
    try {
      if (editing) {
        await AgentPolicyApi.update(instanceId, editing.id, body);
      } else {
        await AgentPolicyApi.create(instanceId, { ...body, enabled: true });
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  const toggleEnabled = async (row: ConfigEffectiveAgentPolicy, enabled: boolean) => {
    if (togglingEnabledId !== null) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.id === row.id ? { ...item, enabled } : item)),
    );
    setTogglingEnabledId(row.id);
    try {
      await AgentPolicyApi.update(instanceId, row.id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.id !== row.id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.id === row.id ? { ...item, enabled: previous } : item)),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingEnabledId(null);
    }
  };

  const toggleSendFileAllowed = async (row: ConfigEffectiveAgentPolicy, sendFileAllowed: boolean) => {
    if (togglingSendFileId !== null) return;
    const previous = row.send_file_allowed;
    setItems((list) =>
      list.map((item) =>
        item.id === row.id ? { ...item, send_file_allowed: sendFileAllowed } : item,
      ),
    );
    setTogglingSendFileId(row.id);
    try {
      await AgentPolicyApi.update(instanceId, row.id, { send_file_allowed: sendFileAllowed });
      if (
        sendFileAllowedFilter !== '' &&
        sendFileAllowed !== (sendFileAllowedFilter === 'true')
      ) {
        setItems((list) => list.filter((item) => item.id !== row.id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) =>
          item.id === row.id ? { ...item, send_file_allowed: previous } : item,
        ),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingSendFileId(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header justify-end">
        <div className="flex items-center gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('policies.agent.searchPlaceholder')}
          />
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
            + {t('policies.agent.new')}
          </button>
        </div>
      </div>

      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>
                  <TableColumnSort
                    label={t('policies.policyName')}
                    value={sortBy === 'policy_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('policy_name', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.policyDesc')}
                    value={sortBy === 'policy_desc' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('policy_desc', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.agent.servicePolicyId')}
                    value={sortBy === 'service_policy_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('service_policy_id', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.global.priority')}
                    value={sortBy === 'priority' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('priority', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.service.matchExpr')}
                    value={sortBy === 'match_expr' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('match_expr', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.agent.agentId')}
                    value={sortBy === 'agent_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('agent_id', value)}
                  />
                </th>
                <th>
                  <TableColumnFilter
                    label={t('policies.agent.sendFileAllowed')}
                    value={sendFileAllowedFilter}
                    options={[
                      { value: '', label: t('common.all') },
                      { value: 'true', label: t('common.yes') },
                      { value: 'false', label: t('common.no') },
                    ]}
                    onChange={(value) => {
                      setSendFileAllowedFilter(value);
                      setPage(1);
                    }}
                  />
                </th>
                <th>{t('policies.global.templateRef')}</th>
                <th>
                  <TableColumnFilter
                    label={t('common.enabled')}
                    value={enabledFilter}
                    options={[
                      { value: '', label: t('common.all') },
                      { value: 'true', label: t('common.enabled') },
                      { value: 'false', label: t('common.disabled') },
                    ]}
                    onChange={(value) => {
                      setEnabledFilter(value);
                      setPage(1);
                    }}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.global.updatedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('updated_at', value)}
                  />
                </th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={11}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : (
                items.map((row) => {
                  const linkedServicePolicy = servicePolicyById.get(row.service_policy_id);
                  return (
                  <tr key={row.id}>
                    <td>
                      <div className="text-text-strong font-medium">
                        {row.policy_name || '—'}
                      </div>
                      <div className="text-[11px] text-muted mono" title={row.policy_id}>
                        {row.policy_id}
                      </div>
                    </td>
                    <td className="text-[11px] text-muted" title={row.policy_desc ?? undefined}>
                      {row.policy_desc ? truncate(row.policy_desc, 48) : '—'}
                    </td>
                    <td>
                      <div className="text-text-strong font-medium">
                        {linkedServicePolicy?.policy_name ||
                          (row.service_policy_id
                            ? t('policies.agent.servicePolicyUnavailable')
                            : '—')}
                      </div>
                      <div className="text-[11px] text-muted mono" title={row.service_policy_id}>
                        {row.service_policy_id}
                      </div>
                    </td>
                    <td>
                      <span className="pill accent mono text-[11px] tabular-nums">
                        {row.priority}
                      </span>
                    </td>
                    <td className="mono text-[11px] text-muted max-w-[12rem]" title={row.match_expr ?? undefined}>
                      {row.match_expr ? truncate(row.match_expr, 36) : '—'}
                    </td>
                    <td className="mono text-xs" title={row.agent_id}>
                      {truncate(row.agent_id, 32)}
                    </td>
                    <td>
                      <Switch
                        checked={row.send_file_allowed}
                        disabled={togglingSendFileId === row.id}
                        aria-label={t('policies.agent.sendFileAllowed')}
                        onChange={(sendFileAllowed) => void toggleSendFileAllowed(row, sendFileAllowed)}
                      />
                    </td>
                    <td className="max-w-[18rem]">
                      <JsonHoverPreview value={row.template_ref} />
                    </td>
                    <td>
                      <Switch
                        checked={row.enabled}
                        disabled={togglingEnabledId === row.id}
                        aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                        onChange={(enabled) => void toggleEnabled(row, enabled)}
                      />
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
                  );
                })
              )}
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
          {editing ? (
            <div className="md:col-span-2">
              <FieldLabel>{t('policies.policyId')}</FieldLabel>
              <input
                className="input !bg-[var(--bg-muted)] !text-muted cursor-not-allowed opacity-100"
                value={editing.policy_id}
                readOnly
                disabled
              />
            </div>
          ) : null}
          <div className="md:col-span-2 grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('policies.policyName')}</FieldLabel>
              <LimitedTextInput
                value={form.policy_name}
                maxLength={FIELD_MAX_LENGTH.policy_name}
                onChange={(v) => update('policy_name', v)}
              />
            </div>
            <div className="min-w-0">
              <FieldLabel required>{t('policies.global.priority')}</FieldLabel>
              <input
                className="input w-full"
                type="number"
                step={1}
                value={Number.isInteger(form.priority) ? form.priority : ''}
                onChange={(e) => {
                  const raw = e.target.value;
                  update('priority', raw === '' ? NaN : Number(raw));
                }}
              />
            </div>
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('policies.policyDesc')}</FieldLabel>
            <LimitedTextInput
              value={form.policy_desc}
              maxLength={FIELD_MAX_LENGTH.policy_desc}
              onChange={(v) => update('policy_desc', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel required>{t('policies.agent.servicePolicyId')}</FieldLabel>
            <select
              className="select w-full"
              value={form.service_policy_id}
              disabled={loadingServicePolicies}
              onChange={(e) => update('service_policy_id', e.target.value)}
            >
              <option value="">
                {loadingServicePolicies
                  ? t('policies.agent.loadingServicePolicies')
                  : t('policies.agent.pickServicePolicy')}
              </option>
              {legacyServicePolicyId ? (
                <option value={legacyServicePolicyId}>
                  {legacyServicePolicyId} ({t('policies.agent.servicePolicyUnavailable')})
                </option>
              ) : null}
              {servicePolicyOptions.map((sp) => (
                <option key={sp.policy_id} value={sp.policy_id}>
                  {sp.policy_id} · {sp.policy_name || '—'}
                </option>
              ))}
            </select>
          </div>
          <div className="md:col-span-2 border-b border-[var(--border)] pb-3 mb-1">
            <FieldLabel>{t('policies.service.matchExpr')}</FieldLabel>
            <MatchExprEditor
              key={editing ? String(editing.id) : 'new'}
              value={form.match_expr}
              onChange={(v) => update('match_expr', v)}
            />
          </div>
          <div className="md:col-span-2 grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('policies.agent.agentId')}</FieldLabel>
              <LimitedTextInput
                value={form.agent_id}
                maxLength={FIELD_MAX_LENGTH.agent_id}
                onChange={(v) => update('agent_id', v)}
              />
              <div className="text-[11px] text-muted mt-1">{t('policies.agent.agentIdHint')}</div>
            </div>
            <div className="min-w-0">
              <FieldLabel>{t('policies.agent.sendFileAllowed')}</FieldLabel>
              <div className="flex h-10 items-center">
                <Switch
                  checked={form.send_file_allowed}
                  aria-label={t('policies.agent.sendFileAllowed')}
                  onChange={(v) => update('send_file_allowed', v)}
                />
              </div>
            </div>
          </div>
          <div className="md:col-span-2">
            <TemplateRefEditor
              key={editing ? String(editing.id) : 'new'}
              label={t('policies.global.templateRef')}
              value={form.template_ref}
              onChange={(v) => update('template_ref', v)}
            />
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
