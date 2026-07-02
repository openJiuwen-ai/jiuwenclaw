import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, LogMaskingRuleApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useListSearch } from '../../../hooks/useListSearch';
import type { LogMaskingRule } from '../../../types';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal } from '../../../components/Modal';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { Switch } from '../../../components/Switch';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { HintTooltip } from '../../../components/HintTooltip';
import { toast } from '../../../stores/uiStore';
import { formatTime, truncate } from '../../../utils/format';

const SOURCE_OPTIONS = ['builtin', 'custom'] as const;

type LogMaskingSortField =
  | 'rule_name'
  | 'description'
  | 'pattern'
  | 'replacement'
  | 'priority'
  | 'updated_at';

/** 与 log_masking_rule 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  rule_name: 128,
  description: 512,
  pattern: 512,
  replacement: 64,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  rule_name: string;
  description: string;
  pattern: string;
  replacement: string;
  priority: number;
}

const emptyForm: FormState = {
  rule_name: '',
  description: '',
  pattern: '',
  replacement: '******',
  priority: 0,
};

export function LogMaskingTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [sortBy, setSortBy] = useState<LogMaskingSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LogMaskingRule | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [delTarget, setDelTarget] = useState<LogMaskingRule | null>(null);
  const [items, setItems] = useState<LogMaskingRule[]>([]);
  const [saving, setSaving] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: LogMaskingSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
  };

  const { data, loading, error, reload } = useAsync(
    () =>
      LogMaskingRuleApi.list(instanceId, {
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        source: sourceFilter || undefined,
        search: searchQuery,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [instanceId, enabledFilter, sourceFilter, searchQuery, sortBy, sortOrder],
  );

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  useEffect(() => {
    if (!modalOpen) return;
    if (editing) {
      setForm({
        rule_name: clipField(editing.rule_name, FIELD_MAX_LENGTH.rule_name),
        description: clipField(editing.description ?? '', FIELD_MAX_LENGTH.description),
        pattern: clipField(editing.pattern, FIELD_MAX_LENGTH.pattern),
        replacement: clipField(editing.replacement, FIELD_MAX_LENGTH.replacement),
        priority: editing.priority,
      });
    } else {
      setForm(emptyForm);
    }
  }, [modalOpen, editing]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('instanceConfig.logMasking.ruleName'), invalid: !form.rule_name.trim() },
      { label: t('instanceConfig.logMasking.pattern'), invalid: !form.pattern.trim() },
      { label: t('instanceConfig.logMasking.replacement'), invalid: !form.replacement.trim() },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceConfig.logMasking.fieldRequired', { field: missing.label }));
      return;
    }
    if (!Number.isFinite(form.priority)) {
      toast('warn', t('instanceConfig.logMasking.priorityInvalid'));
      return;
    }

    const body = {
      rule_name: form.rule_name.trim(),
      description: form.description.trim() || undefined,
      pattern: form.pattern.trim(),
      replacement: form.replacement.trim(),
      priority: form.priority,
      ...(!editing ? { enabled: true } : {}),
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

  const toggleEnabled = async (row: LogMaskingRule, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.rule_id === row.rule_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.rule_id);
    try {
      await LogMaskingRuleApi.update(instanceId, row.rule_id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.rule_id !== row.rule_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.rule_id === row.rule_id ? { ...item, enabled: previous } : item)),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('instanceConfig.logMasking.searchPlaceholder')}
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
            + {t('instanceConfig.logMasking.new')}
          </button>
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
                        label={t('instanceConfig.logMasking.ruleName')}
                        value={sortBy === 'rule_name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('rule_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceConfig.logMasking.ruleDescription')}
                        value={sortBy === 'description' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('description', value)}
                      />
                    </th>
                    <th>
                      <div className="th-filter">
                        <span className="th-filter__label inline-flex items-center gap-1">
                          {t('instanceConfig.logMasking.priority')}
                          <HintTooltip text={t('instanceConfig.logMasking.priorityHint')} />
                        </span>
                        <TableColumnSort
                          iconOnly
                          label={t('instanceConfig.logMasking.priority')}
                          value={sortBy === 'priority' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('priority', value)}
                        />
                      </div>
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceConfig.logMasking.pattern')}
                        value={sortBy === 'pattern' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('pattern', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceConfig.logMasking.replacement')}
                        value={sortBy === 'replacement' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('replacement', value)}
                      />
                    </th>
                    <th>
                      <TableColumnFilter
                        label={t('instanceConfig.logMasking.source')}
                        value={sourceFilter}
                        options={[
                          { value: '', label: t('common.all') },
                          ...SOURCE_OPTIONS.map((source) => ({
                            value: source,
                            label: t(`instanceConfig.logMasking.source_${source}`),
                          })),
                        ]}
                        onChange={setSourceFilter}
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
                        onChange={setEnabledFilter}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceConfig.logMasking.updatedAt')}
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
                      <td colSpan={9}>
                        <Empty text={t('common.empty')} />
                      </td>
                    </tr>
                  ) : (
                    items.map((row) => (
                      <tr key={row.rule_id}>
                        <td className="align-top">
                          <div className="text-text-strong font-medium break-words">{row.rule_name}</div>
                          <div className="text-[11px] text-muted mono break-all" title={row.rule_id}>
                            {row.rule_id}
                          </div>
                        </td>
                        <td className="text-[11px] text-muted max-w-[14rem]" title={row.description ?? undefined}>
                          {row.description ? truncate(row.description, 48) : '—'}
                        </td>
                        <td className="whitespace-nowrap">
                          <span className="pill accent mono text-[11px] tabular-nums">
                            {row.priority}
                          </span>
                        </td>
                        <td className="mono text-[11px] text-muted max-w-[16rem] break-all align-top" title={row.pattern}>
                          {truncate(row.pattern, 40)}
                        </td>
                        <td className="mono text-xs whitespace-nowrap">{row.replacement}</td>
                        <td className="whitespace-nowrap">
                          <span className={`tag ${row.source}`}>{t(`instanceConfig.logMasking.source_${row.source}`, row.source)}</span>
                        </td>
                        <td className="whitespace-nowrap">
                          <Switch
                            checked={row.enabled}
                            disabled={togglingId === row.rule_id}
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
          <div className="md:col-span-2 grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('instanceConfig.logMasking.ruleName')}</FieldLabel>
              <LimitedTextInput
                value={form.rule_name}
                maxLength={FIELD_MAX_LENGTH.rule_name}
                onChange={(v) => update('rule_name', v)}
              />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceConfig.logMasking.priority')}</FieldLabel>
                <HintTooltip text={t('instanceConfig.logMasking.priorityHint')} />
              </div>
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
            <FieldLabel>{t('instanceConfig.logMasking.ruleDescription')}</FieldLabel>
            <LimitedTextInput
              value={form.description}
              maxLength={FIELD_MAX_LENGTH.description}
              onChange={(v) => update('description', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel required>{t('instanceConfig.logMasking.pattern')}</FieldLabel>
            <LimitedTextInput
              className="mono text-xs"
              value={form.pattern}
              maxLength={FIELD_MAX_LENGTH.pattern}
              placeholder={t('instanceConfig.logMasking.patternPlaceholder')}
              onChange={(v) => update('pattern', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel required>{t('instanceConfig.logMasking.replacement')}</FieldLabel>
            <LimitedTextInput
              value={form.replacement}
              maxLength={FIELD_MAX_LENGTH.replacement}
              onChange={(v) => update('replacement', v)}
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
    </>
  );
}
