import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, MappingApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useListSearch } from '../../../hooks/useListSearch';
import type {
  ConfigDefaultTemplateMapping,
  ConfigDefaultTemplateMappingCreateBody,
} from '../../../types';
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
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { ListSearchInput } from '../../../components/ListSearchInput';
import {
  loadTemplateOptions,
  type TemplateOption,
} from '../../../components/TemplateRefEditor';
import { toast } from '../../../stores/uiStore';
import { formatTime, truncate } from '../../../utils/format';
import { TEMPLATE_REF_SLOTS } from '../../../utils/templateRef';

/** 与 Manager / Gateway 默认模板映射允许的 template_type 一致 */
const MAPPING_TEMPLATE_SLOTS = TEMPLATE_REF_SLOTS;

/** 与 config_default_template_mapping 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  policy_name: 128,
  policy_desc: 512,
  user_id: 512,
  group_id: 512,
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
  user_id: string;
  group_id: string;
  priority: number;
  template_id: string;
  template_type: string;
}

const emptyForm: FormState = {
  policy_name: '',
  policy_desc: '',
  user_id: '',
  group_id: '',
  priority: 0,
  template_id: '',
  template_type: 'default_model',
};

type MappingSortField =
  | 'policy_name'
  | 'policy_desc'
  | 'priority'
  | 'user_id'
  | 'group_id'
  | 'template_type'
  | 'template_id'
  | 'updated_at';

function templateOptionName(label: string | undefined, templateId: string): string {
  if (!label) return templateId;
  const suffix = ` (${templateId})`;
  if (label.endsWith(suffix)) return label.slice(0, -suffix.length);
  return label;
}

function slotLabel(t: (key: string, options?: { defaultValue?: string }) => string, slot: string): string {
  return t(`policies.templateRef.slots.${slot}`, { defaultValue: slot });
}

export function MappingTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [templateType, setTemplateType] = useState('');
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<MappingSortField | ''>('');
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

  const handleSortChange = (field: MappingSortField, value: ColumnSortValue) => {
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
      MappingApi.list(instanceId, {
        page,
        page_size: pageSize,
        template_type: templateType || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        search: searchQuery,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, page, pageSize, templateType, enabledFilter, searchQuery, sortBy, sortOrder]
  );

  const [items, setItems] = useState<ConfigDefaultTemplateMapping[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ConfigDefaultTemplateMapping | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<ConfigDefaultTemplateMapping | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);
  const [templateOptionsBySlot, setTemplateOptionsBySlot] = useState<Record<string, TemplateOption[]>>({});
  const [loadingTemplates, setLoadingTemplates] = useState(false);

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
        user_id: editing.user_id ?? '',
        group_id: editing.group_id ?? '',
        priority: editing.priority,
        template_id: editing.template_id,
        template_type: editing.template_type,
      });
    } else {
      setForm(emptyForm);
    }
  }, [modalOpen, editing]);

  useEffect(() => {
    let cancelled = false;
    setLoadingTemplates(true);
    void loadTemplateOptions()
      .then((opts) => {
        if (!cancelled) setTemplateOptionsBySlot(opts);
      })
      .catch(() => {
        if (!cancelled) setTemplateOptionsBySlot({});
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, [instanceId]);

  const currentTemplateOptions = templateOptionsBySlot[form.template_type] ?? [];
  const legacyTemplateId =
    form.template_id && !currentTemplateOptions.some((opt) => opt.template_id === form.template_id)
      ? form.template_id
      : '';
  const legacyTemplateType =
    form.template_type &&
    !(MAPPING_TEMPLATE_SLOTS as readonly string[]).includes(form.template_type)
      ? form.template_type
      : '';

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.policy_name.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.policyName') }));
      return;
    }
    if (!form.template_id.trim() || !form.template_type.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.mapping.template') }));
      return;
    }
    if (!Number.isInteger(form.priority)) {
      toast('warn', t('policies.fieldRequired', { field: t('policies.mapping.priority') }));
      return;
    }
    const body: ConfigDefaultTemplateMappingCreateBody = {
      policy_name: form.policy_name.trim(),
      policy_desc: form.policy_desc.trim() || undefined,
      user_id: form.user_id.trim() || undefined,
      group_id: form.group_id.trim() || undefined,
      priority: form.priority,
      template_id: form.template_id.trim(),
      template_type: form.template_type.trim(),
    };
    try {
      if (editing) {
        await MappingApi.update(instanceId, editing.id, body);
      } else {
        await MappingApi.create(instanceId, { ...body, enabled: true });
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      void reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  const toggleEnabled = async (row: ConfigDefaultTemplateMapping, enabled: boolean) => {
    if (togglingId !== null) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.id === row.id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.id);
    try {
      await MappingApi.update(instanceId, row.id, { enabled });
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
      <div className="page-header justify-end">
        <div className="flex items-center gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('policies.mapping.searchPlaceholder')}
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
            + {t('policies.mapping.new')}
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
                    label={t('policies.mapping.priority')}
                    value={sortBy === 'priority' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('priority', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.mapping.userId')}
                    value={sortBy === 'user_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('user_id', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.mapping.groupId')}
                    value={sortBy === 'group_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('group_id', value)}
                  />
                </th>
                <th>
                  <div className="th-filter">
                    <span className="th-filter__label">{t('policies.templateRef.slot')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('policies.templateRef.slot')}
                      value={sortBy === 'template_type' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('template_type', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('policies.templateRef.slot')}
                      value={templateType}
                      options={[
                        { value: '', label: t('common.all') },
                        ...MAPPING_TEMPLATE_SLOTS.map((slot) => ({
                          value: slot,
                          label: slotLabel(t, slot),
                        })),
                      ]}
                      onChange={(value) => {
                        setTemplateType(value);
                        setPage(1);
                      }}
                    />
                  </div>
                </th>
                <th>
                  <TableColumnSort
                    label={t('policies.mapping.template')}
                    value={sortBy === 'template_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_id', value)}
                  />
                </th>
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
                  <td colSpan={10}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : (
                items.map((row) => {
                  const templateOption = (templateOptionsBySlot[row.template_type] ?? []).find(
                    (opt) => opt.template_id === row.template_id,
                  );
                  const templateName = templateOptionName(templateOption?.label, row.template_id);
                  return (
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
                      <td
                        className="mono text-xs min-w-[10rem] max-w-[18rem] break-all align-top"
                        title={row.user_id ?? undefined}
                      >
                        {row.user_id || '—'}
                      </td>
                      <td
                        className="mono text-xs min-w-[10rem] max-w-[18rem] break-all align-top"
                        title={row.group_id ?? undefined}
                      >
                        {row.group_id || '—'}
                      </td>
                      <td className="text-xs max-w-[10rem] truncate" title={row.template_type}>
                        {truncate(slotLabel(t, row.template_type), 28)}
                      </td>
                      <td className="min-w-[12rem] max-w-[20rem] align-top">
                        <div
                          className="text-text-strong font-medium text-xs truncate"
                          title={templateOption?.label ?? templateName}
                        >
                          {templateName}
                        </div>
                        <div className="text-[11px] text-muted mono break-all" title={row.template_id}>
                          {row.template_id}
                        </div>
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
                  );
                })
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
              <FieldLabel required>{t('policies.mapping.priority')}</FieldLabel>
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
          <div>
            <FieldLabel>{t('policies.mapping.userId')}</FieldLabel>
            <LimitedTextInput
              value={form.user_id}
              maxLength={FIELD_MAX_LENGTH.user_id}
              onChange={(v) => update('user_id', v)}
            />
          </div>
          <div>
            <FieldLabel>{t('policies.mapping.groupId')}</FieldLabel>
            <LimitedTextInput
              value={form.group_id}
              maxLength={FIELD_MAX_LENGTH.group_id}
              onChange={(v) => update('group_id', v)}
            />
          </div>
          <div className="md:col-span-2 border-t border-[var(--border)] pt-3 mt-1 grid grid-cols-1 md:grid-cols-[1fr_3fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('policies.templateRef.slot')}</FieldLabel>
              <select
                className="select w-full"
                value={form.template_type}
                disabled={loadingTemplates}
                onChange={(e) => {
                  const nextSlot = e.target.value;
                  setForm((s) => {
                    const nextOptions = templateOptionsBySlot[nextSlot] ?? [];
                    const keepTemplateId = nextOptions.some((opt) => opt.template_id === s.template_id);
                    return {
                      ...s,
                      template_type: nextSlot,
                      template_id: keepTemplateId ? s.template_id : '',
                    };
                  });
                }}
              >
                {legacyTemplateType ? (
                  <option value={legacyTemplateType}>
                    {legacyTemplateType} ({t('policies.mapping.slotUnavailable')})
                  </option>
                ) : null}
                {MAPPING_TEMPLATE_SLOTS.map((slot) => (
                  <option key={slot} value={slot}>
                    {t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-0">
              <FieldLabel required>{t('policies.mapping.template')}</FieldLabel>
              <select
                className="select w-full"
                value={form.template_id}
                disabled={loadingTemplates}
                onChange={(e) => update('template_id', e.target.value)}
              >
                <option value="">
                  {loadingTemplates
                    ? t('policies.templateRef.loadingTemplates')
                    : t('policies.templateRef.pickTemplate')}
                </option>
                {legacyTemplateId ? (
                  <option value={legacyTemplateId}>
                    {legacyTemplateId} ({t('policies.mapping.templateUnavailable')})
                  </option>
                ) : null}
                {currentTemplateOptions.map((opt) => (
                  <option key={opt.template_id} value={opt.template_id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
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
