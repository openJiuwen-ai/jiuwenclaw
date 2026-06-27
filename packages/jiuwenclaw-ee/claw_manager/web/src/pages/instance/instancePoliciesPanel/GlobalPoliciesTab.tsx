import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, GlobalPolicyApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useListSearch } from '../../../hooks/useListSearch';
import type { ConfigEffectiveGlobalPolicy } from '../../../types';
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
import { toast } from '../../../stores/uiStore';
import { formatTime, truncate } from '../../../utils/format';
import {
  findSingleValueTemplateRefViolation,
  hasTemplateRefContent,
  normalizeTemplateRefFromApi,
  type TemplateRefMap,
} from '../../../utils/templateRef';

/** 与 config_effective_global_policy 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  policy_name: 128,
  policy_desc: 512,
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
  priority: number;
  template_ref: TemplateRefMap;
}

const emptyForm: FormState = {
  policy_name: '',
  policy_desc: '',
  priority: 0,
  template_ref: {},
};

type GlobalSortField = 'policy_name' | 'policy_desc' | 'priority' | 'updated_at';

export function GlobalPoliciesTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<GlobalSortField | ''>('');
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

  const handleSortChange = (field: GlobalSortField, value: ColumnSortValue) => {
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
      GlobalPolicyApi.list(instanceId, {
        page,
        page_size: pageSize,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        search: searchQuery,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, page, pageSize, enabledFilter, searchQuery, sortBy, sortOrder]
  );

  const [items, setItems] = useState<ConfigEffectiveGlobalPolicy[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigEffectiveGlobalPolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigEffectiveGlobalPolicy | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);

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
        priority: editing.priority,
        template_ref: normalizeTemplateRefFromApi(editing.template_ref),
      });
    } else {
      setForm(emptyForm);
    }
  }, [modalOpen, editing]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      {
        label: t('policies.policyName'),
        invalid: !form.policy_name.trim(),
      },
      {
        label: t('policies.global.priority'),
        invalid: !Number.isInteger(form.priority),
      },
      {
        label: t('policies.global.templateRef'),
        invalid: !hasTemplateRefContent(form.template_ref),
      },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('policies.fieldRequired', { field: missing.label }));
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
      priority: form.priority,
      template_ref: form.template_ref,
    };
    try {
      if (editing) {
        await GlobalPolicyApi.update(instanceId, editing.id, body);
      } else {
        await GlobalPolicyApi.create(instanceId, { ...body, enabled: true });
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  const toggleEnabled = async (row: ConfigEffectiveGlobalPolicy, enabled: boolean) => {
    if (togglingId !== null) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.id === row.id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.id);
    try {
      await GlobalPolicyApi.update(instanceId, row.id, { enabled });
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
      setTogglingId(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start justify-end gap-y-3">
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('policies.global.searchPlaceholder')}
            className="basis-full sm:basis-auto"
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
            + {t('policies.global.new')}
          </button>
        </div>
      </div>

      <div className="card !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : (
          <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
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
                    label={t('policies.global.priority')}
                    value={sortBy === 'priority' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('priority', value)}
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
                <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : (
                items.map((row) => (
                    <tr key={row.id}>
                      <td className="align-top">
                        <div className="text-text-strong font-medium break-words">
                          {row.policy_name || '—'}
                        </div>
                        <div className="text-[11px] text-muted mono break-all" title={row.policy_id}>
                          {row.policy_id}
                        </div>
                      </td>
                      <td className="text-[11px] text-muted max-w-[14rem]" title={row.policy_desc ?? undefined}>
                        {row.policy_desc ? truncate(row.policy_desc, 48) : '—'}
                      </td>
                      <td className="whitespace-nowrap">
                        <span className="pill accent mono text-[11px] tabular-nums">
                          {row.priority}
                        </span>
                      </td>
                      <td className="min-w-[12rem] max-w-[20rem] align-top">
                        <JsonHoverPreview value={row.template_ref} />
                      </td>
                      <td className="whitespace-nowrap">
                        <Switch
                          checked={row.enabled}
                          disabled={togglingId === row.id}
                          aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                          onChange={(enabled) => void toggleEnabled(row, enabled)}
                        />
                      </td>
                      <td className="mono text-[11px] text-muted whitespace-nowrap">{formatTime(row.updated_at)}</td>
                      <td className="whitespace-nowrap min-w-[9.5rem]">
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
                ))
              )}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {data && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={data.total ?? data.items.length}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      )}

      <Modal
        open={modalOpen}
        title={editing ? t('common.edit') : t('policies.global.new')}
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
          <div className="md:col-span-2 border-t border-[var(--border)] pt-3 mt-1">
            <TemplateRefEditor
              key={editing ? String(editing.id) : 'new'}
              label={t('policies.global.templateRef')}
              required
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
            await GlobalPolicyApi.remove(instanceId, delTarget.id);
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
