import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceApi } from '../../services/api';
import type { InstanceSummary } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ListSearchInput } from '../../components/ListSearchInput';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import { formatTime, relativeTime } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { ApiError } from '../../services/api';
import { CreateInstanceModal } from './modal/CreateInstanceModal';
import { ProvisionLocalModal } from './modal/ProvisionLocalModal';

type ViewMode = 'brief' | 'list';

type InstanceSortField =
  | 'jiuwenclaw_name'
  | 'status'
  | 'last_heartbeat'
  | 'k8s_namespace'
  | 'updated_at';

const VIEW_MODE_STORAGE_KEY = 'claw_manager_instance_view';

const INSTANCE_STATUS_VALUES = ['online', 'pending', 'offline'] as const;

function readViewMode(): ViewMode {
  const saved = localStorage.getItem(VIEW_MODE_STORAGE_KEY);
  if (saved === 'list') return 'list';
  if (saved === 'brief' || saved === 'compact') return 'brief';
  return 'brief';
}

function InstanceTopoCard({
  instance,
  onChanged,
}: {
  instance: InstanceSummary;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [confirmDel, setConfirmDel] = useState(false);

  return (
    <div className="topo-group w-full min-w-0">
      <div className="topo-hero">
        <div className="topo-hero__title">
          <div className="brand-logo" aria-hidden>
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 17h6m-3-3v3M7 8h10M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z" />
            </svg>
          </div>
          <div>
            <div className="topo-hero__name">
              {instance.jiuwenclaw_name}
              <StatusBadge status={instance.status} />
            </div>
            <div className="topo-hero__id">{instance.jiuwenclaw_id}</div>
          </div>
        </div>
        <div className="topo-hero__meta">
          <span className="pill subtle muted">
            {t('topology.namespace')}: <span className="mono text-text">{instance.k8s_namespace}</span>
          </span>
          <div className="flex items-center gap-1">
            <button className="btn sm" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}>
              {t('topology.viewDetail')}
            </button>
            <button className="btn sm danger" onClick={() => setConfirmDel(true)}>
              {t('common.delete')}
            </button>
          </div>
        </div>
      </div>

      <div className="topo-gateway">
        <div className="topo-gateway__title">
          <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h18M3 6h18M3 18h18" />
          </svg>
          {t('topology.gateway')}
        </div>
        <div className="topo-gateway__meta">
          <span className="topo-gateway__meta-item">
            <span>{t('topology.lastHeartbeat')}</span>
            <span
              className="mono"
              title={instance.last_heartbeat ? formatTime(instance.last_heartbeat) : undefined}
            >
              {relativeTime(instance.last_heartbeat)}
            </span>
            <span aria-hidden>，</span>
            <span>{t('topology.lastUpdated')}</span>
            <span
              className="mono"
              title={instance.updated_at ? formatTime(instance.updated_at) : undefined}
            >
              {relativeTime(instance.updated_at)}
            </span>
          </span>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDel}
        message={t('topology.deleteConfirm')}
        danger
        onConfirm={async () => {
          try {
            await InstanceApi.remove(instance.jiuwenclaw_id);
            toast('success', t('success.deleted'));
            onChanged();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setConfirmDel(false)}
      />
    </div>
  );
}

function InstanceListTable({
  items,
  onChanged,
  sortBy,
  sortOrder,
  sortOptions,
  onSortChange,
  statusFilter,
  onStatusFilterChange,
}: {
  items: InstanceSummary[];
  onChanged: () => void;
  sortBy: InstanceSortField | '';
  sortOrder: 'asc' | 'desc';
  sortOptions: { value: ColumnSortValue; label: string }[];
  onSortChange: (field: InstanceSortField, value: ColumnSortValue) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [deleteTarget, setDeleteTarget] = useState<InstanceSummary | null>(null);

  const statusFilterOptions = useMemo(
    () => [
      { value: '', label: t('common.all') },
      ...INSTANCE_STATUS_VALUES.map((status) => ({ value: status, label: status })),
    ],
    [t],
  );

  return (
    <>
      <div className="card !p-0">
        <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th className="min-w-[12rem]">
                  <TableColumnSort
                    label={t('topology.instanceName')}
                    value={sortBy === 'jiuwenclaw_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('jiuwenclaw_name', value)}
                  />
                </th>
                <th className="whitespace-nowrap">
                  <div className="th-filter">
                    <span className="th-filter__label">{t('topology.instanceStatus')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('topology.instanceStatus')}
                      value={sortBy === 'status' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => onSortChange('status', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('topology.instanceStatus')}
                      value={statusFilter}
                      options={statusFilterOptions}
                      onChange={onStatusFilterChange}
                    />
                  </div>
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.lastHeartbeat')}
                    value={sortBy === 'last_heartbeat' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('last_heartbeat', value)}
                  />
                </th>
                <th className="whitespace-nowrap">
                  <TableColumnSort
                    label={t('topology.namespace')}
                    value={sortBy === 'k8s_namespace' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('k8s_namespace', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.modifiedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('updated_at', value)}
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
              ) : (
                items.map((instance) => (
                  <tr key={instance.jiuwenclaw_id}>
                    <td>
                      <div className="font-medium text-text-strong">{instance.jiuwenclaw_name}</div>
                      <div className="mono text-[11px] text-muted break-all">{instance.jiuwenclaw_id}</div>
                    </td>
                    <td>
                      <StatusBadge status={instance.status} />
                    </td>
                    <td className="mono text-[11px] whitespace-nowrap">{formatTime(instance.last_heartbeat)}</td>
                    <td className="mono text-[11px]">{instance.k8s_namespace || '-'}</td>
                    <td className="mono text-[11px] whitespace-nowrap">{formatTime(instance.updated_at)}</td>
                    <td>
                      <div className="flex items-center gap-1">
                        <button
                          className="btn sm"
                          onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}
                        >
                          {t('topology.viewDetail')}
                        </button>
                        <button className="btn sm danger" onClick={() => setDeleteTarget(instance)}>
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
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        message={t('topology.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await InstanceApi.remove(deleteTarget.jiuwenclaw_id);
            toast('success', t('success.deleted'));
            onChanged();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}

function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (mode: ViewMode) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="tabs-bar" role="group" aria-label={t('topology.viewBrief')}>
      <button
        type="button"
        className={`tab ${value === 'brief' ? 'active' : ''}`}
        title={t('topology.viewBrief')}
        aria-pressed={value === 'brief'}
        onClick={() => onChange('brief')}
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h10M4 18h16" />
        </svg>
      </button>
      <button
        type="button"
        className={`tab ${value === 'list' ? 'active' : ''}`}
        title={t('topology.viewDetailed')}
        aria-pressed={value === 'list'}
        onClick={() => onChange('list')}
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
        </svg>
      </button>
    </div>
  );
}

export function InstanceListPage() {
  const { t } = useTranslation();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sortBy, setSortBy] = useState<InstanceSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [viewMode, setViewMode] = useState<ViewMode>(() => readViewMode());
  const [createOpen, setCreateOpen] = useState(false);
  const [provisionOpen, setProvisionOpen] = useState(false);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleStatusFilterChange = (value: string) => {
    setStatusFilter(value);
    setPage(1);
  };

  const handleSortChange = (field: InstanceSortField, value: ColumnSortValue) => {
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

  const apiSortBy = viewMode === 'brief' ? 'updated_at' : sortBy || undefined;
  const apiSortOrder =
    viewMode === 'brief' ? 'desc' : sortBy ? sortOrder : undefined;

  const instances = useAsync(
    () =>
      InstanceApi.list({
        page,
        page_size: pageSize,
        status: statusFilter || undefined,
        search: searchQuery,
        sort_by: apiSortBy,
        sort_order: apiSortOrder,
      }),
    [statusFilter, page, pageSize, searchQuery, sortBy, sortOrder, viewMode]
  );

  const refresh = () => {
    void instances.reload();
  };

  const handleViewModeChange = (mode: ViewMode) => {
    setViewMode(mode);
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode);
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('topology.title')}>
              {t('topology.title')}
            </div>
            <div className="page-subtitle truncate" title={t('topology.subtitle')}>
              {t('topology.subtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('topology.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <ViewModeToggle value={viewMode} onChange={handleViewModeChange} />
            <button className="btn sm" onClick={refresh}>
              {t('common.refresh')}
            </button>
            <button className="btn sm" onClick={() => setProvisionOpen(true)}>
              {t('topology.provisionLocal')}
            </button>
            <button className="btn primary sm" onClick={() => setCreateOpen(true)}>
              + {t('topology.createInstance')}
            </button>
          </div>
        </div>

        <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
          {instances.loading ? (
            <div className="text-sm text-muted">{t('common.loading')}</div>
          ) : instances.error ? (
            <div className="card text-sm text-danger">
              {t('errors.loadFailed', { detail: instances.error })}
            </div>
          ) : viewMode === 'list' ? (
            <InstanceListTable
              items={instances.data?.items ?? []}
              onChanged={refresh}
              sortBy={sortBy}
              sortOrder={sortOrder}
              sortOptions={sortOptions}
              onSortChange={handleSortChange}
              statusFilter={statusFilter}
              onStatusFilterChange={handleStatusFilterChange}
            />
          ) : !instances.data || instances.data.items.length === 0 ? (
            <div className="card">
              <Empty text={t('overview.noInstances')} />
            </div>
          ) : (
            <div className="flex w-full min-w-0 flex-col gap-4">
              {instances.data.items.map((it) => (
                <InstanceTopoCard key={it.jiuwenclaw_id} instance={it} onChanged={refresh} />
              ))}
            </div>
          )}

          {instances.data && (
            <Pagination
              page={page}
              pageSize={pageSize}
              total={instances.data.total ?? instances.data.items.length}
              onChange={(p, ps) => {
                setPage(p);
                setPageSize(ps);
              }}
            />
          )}
        </div>
      </div>

      <CreateInstanceModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          refresh();
        }}
      />
      <ProvisionLocalModal
        open={provisionOpen}
        onClose={() => setProvisionOpen(false)}
        onProvisioned={() => {
          setProvisionOpen(false);
          refresh();
        }}
      />
    </>
  );
}
