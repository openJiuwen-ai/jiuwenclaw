import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import type { ServiceConfigTemplate } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Switch } from '../../components/Switch';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { ListSearchInput } from '../../components/ListSearchInput';
import { ServiceConfigTemplateModal } from './ServiceConfigTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';

type ServiceConfigTemplateSortField =
  | 'template_name'
  | 'description'
  | 'agent_image'
  | 'updated_at';

export function ServiceConfigTemplatesPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<ServiceConfigTemplateSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: ServiceConfigTemplateSortField, value: ColumnSortValue) => {
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
      ServiceConfigTemplateApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder]
  );

  const [items, setItems] = useState<ServiceConfigTemplate[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceConfigTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<ServiceConfigTemplate | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  const toggleEnabled = async (row: ServiceConfigTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await ServiceConfigTemplateApi.update(row.template_id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.template_id !== row.template_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.template_id === row.template_id ? { ...item, enabled: previous } : item)),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4 overflow-x-auto">
        <div className="page-header w-max min-w-full shrink-0">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('serviceConfigTemplate.title')}>
              {t('serviceConfigTemplate.title')}
            </div>
            <div className="page-subtitle truncate" title={t('serviceConfigTemplate.subtitle')}>
              {t('serviceConfigTemplate.subtitle')}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('serviceConfigTemplate.searchPlaceholder')}
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
            + {t('serviceConfigTemplate.new')}
          </button>
        </div>
      </div>

      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
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
                    label={t('serviceConfigTemplate.templateName')}
                    value={sortBy === 'template_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_name', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('serviceConfigTemplate.templateDescription')}
                    value={sortBy === 'description' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('description', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('serviceConfigTemplate.agentImage')}
                    value={sortBy === 'agent_image' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('agent_image', value)}
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
                    label={t('serviceConfigTemplate.updatedAt')}
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
                  <td colSpan={6}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : items.map((row) => (
                <tr key={row.template_id}>
                  <td className="align-top">
                    <div className="text-text-strong font-medium break-words">{row.template_name}</div>
                    <div className="text-[11px] text-muted mono break-all" title={row.template_id}>
                      {row.template_id}
                    </div>
                  </td>
                  <td className="text-[11px] text-muted max-w-[14rem]" title={row.description ?? undefined}>
                    {row.description ? truncate(row.description, 48) : '—'}
                  </td>
                  <td className="mono text-[11px] text-muted min-w-[10rem] max-w-[18rem] break-all align-top" title={row.agent_image}>
                    {row.agent_image}
                  </td>
                  <td className="whitespace-nowrap">
                    <Switch
                      checked={row.enabled}
                      disabled={togglingId === row.template_id}
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
              ))}
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
      </div>
      </div>

      <ServiceConfigTemplateModal
        open={modalOpen}
        template={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => {
          setModalOpen(false);
          void reload();
        }}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t('serviceConfigTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await ServiceConfigTemplateApi.remove(delTarget.template_id);
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
