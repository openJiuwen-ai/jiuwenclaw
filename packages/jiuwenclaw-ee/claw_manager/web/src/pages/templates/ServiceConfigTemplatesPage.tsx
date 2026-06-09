import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import type { ServiceConfigTemplate } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ServiceConfigTemplateModal } from './ServiceConfigTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';

export function ServiceConfigTemplatesPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [namespace, setNamespace] = useState('');
  const [enabledFilter, setEnabledFilter] = useState<string>('');

  const { data, loading, error, reload } = useAsync(
    () =>
      ServiceConfigTemplateApi.list({
        page,
        page_size: pageSize,
        namespace: namespace || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
      }),
    [page, pageSize, namespace, enabledFilter]
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceConfigTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<ServiceConfigTemplate | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('serviceConfigTemplate.title')}</div>
          <div className="page-subtitle">{t('serviceConfigTemplate.subtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <input
            className="input !w-40"
            placeholder={t('serviceConfigTemplate.filterNamespace')}
            value={namespace}
            onChange={(e) => {
              setNamespace(e.target.value);
              setPage(1);
            }}
          />
          <select
            className="select !w-32"
            value={enabledFilter}
            onChange={(e) => {
              setEnabledFilter(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t('common.all')}</option>
            <option value="true">{t('common.enabled')}</option>
            <option value="false">{t('common.disabled')}</option>
          </select>
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
                <th>{t('serviceConfigTemplate.templateName')}</th>
                <th>{t('serviceConfigTemplate.agentImage')}</th>
                <th>{t('serviceConfigTemplate.namespace')}</th>
                <th>{t('serviceConfigTemplate.poolRange')}</th>
                <th>{t('common.enabled')}</th>
                <th>updated</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.template_id}>
                  <td>
                    <div className="text-text-strong font-medium">{row.template_name}</div>
                    {row.description && (
                      <div className="text-[11px] text-muted">{row.description}</div>
                    )}
                    <div className="text-[11px] text-muted mono">{row.template_id}</div>
                  </td>
                  <td className="mono text-[11px] text-muted" title={row.agent_image}>
                    {truncate(row.agent_image, 32)}
                  </td>
                  <td><span className="tag">{row.namespace}</span></td>
                  <td className="mono text-xs">
                    {row.min_idle_services} / {row.max_services}
                  </td>
                  <td>
                    <span className={`pill sm ${row.enabled ? 'ok' : 'muted'}`}>
                      <span className={`statusDot ${row.enabled ? 'ok' : 'muted'}`} />
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
    </div>
  );
}
