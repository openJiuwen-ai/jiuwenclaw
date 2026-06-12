import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { SkillWhitelistTemplateApi, ApiError } from '../../services/api';
import type { SkillWhitelistTemplate } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Switch } from '../../components/Switch';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import { SkillWhitelistTemplateModal } from './SkillWhitelistTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';

export function SkillWhitelistTemplatesPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 700);
  const [enabledFilter, setEnabledFilter] = useState<string>('');

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const { data, loading, error, reload } = useAsync(
    () =>
      SkillWhitelistTemplateApi.list({
        page,
        page_size: pageSize,
        search: debouncedSearch.trim() || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
      }),
    [page, pageSize, debouncedSearch, enabledFilter]
  );

  const [items, setItems] = useState<SkillWhitelistTemplate[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<SkillWhitelistTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<SkillWhitelistTemplate | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  const toggleEnabled = async (row: SkillWhitelistTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await SkillWhitelistTemplateApi.update(row.template_id, { enabled });
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
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('skillWhitelistTemplate.title')}</div>
          <div className="page-subtitle">{t('skillWhitelistTemplate.subtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <input
            className="input !w-[38rem]"
            placeholder={t('skillWhitelistTemplate.searchPlaceholder')}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
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
            + {t('skillWhitelistTemplate.new')}
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
                <th>{t('skillWhitelistTemplate.templateName')}</th>
                <th>{t('skillWhitelistTemplate.templateDescription')}</th>
                <th>{t('skillWhitelistTemplate.skillSource')}</th>
                <th>{t('skillWhitelistTemplate.skillId')}</th>
                <th>{t('skillWhitelistTemplate.skillVersion')}</th>
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
                <th>{t('skillWhitelistTemplate.updatedAt')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : items.map((row) => (
                <tr key={row.template_id}>
                  <td>
                    <div className="text-text-strong font-medium">{row.template_name}</div>
                    <div className="text-[11px] text-muted mono">{row.template_id}</div>
                  </td>
                  <td className="text-[11px] text-muted" title={row.description ?? undefined}>
                    {row.description ? truncate(row.description, 48) : '—'}
                  </td>
                  <td className="mono text-[11px] text-muted" title={row.skill_source}>
                    {truncate(row.skill_source, 36)}
                  </td>
                  <td className="mono text-xs text-text-strong">{row.skill_id}</td>
                  <td className="mono text-xs">{row.skill_version}</td>
                  <td>
                    <Switch
                      checked={row.enabled}
                      disabled={togglingId === row.template_id}
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

      <SkillWhitelistTemplateModal
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
        message={t('skillWhitelistTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await SkillWhitelistTemplateApi.remove(delTarget.template_id);
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
