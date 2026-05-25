import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { ExtensionTemplateApi, ApiError } from '../../services/api';
import type { ExtensionConfigTemplate } from '../../types';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ExtensionTemplateModal } from './ExtensionTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';

export function ExtensionTemplatesPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [component, setComponent] = useState('');
  const [hookType, setHookType] = useState('');

  const { data, loading, error, reload } = useAsync(
    () =>
      ExtensionTemplateApi.list({
        page,
        page_size: pageSize,
        component: component || undefined,
        hook_type: hookType || undefined,
      }),
    [page, pageSize, component, hookType]
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ExtensionConfigTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<ExtensionConfigTemplate | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('extensionTemplate.title')}</div>
          <div className="page-subtitle">{t('extensionTemplate.subtitle')}</div>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="select !w-40"
            value={component}
            onChange={(e) => {
              setComponent(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t('extensionTemplate.component')}: {t('common.all')}</option>
            <option value="gateway">gateway</option>
            <option value="agent_server">agent_server</option>
          </select>
          <select
            className="select !w-44"
            value={hookType}
            onChange={(e) => {
              setHookType(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t('extensionTemplate.hookType')}: {t('common.all')}</option>
            <option value="pre_request">pre_request</option>
            <option value="post_request">post_request</option>
            <option value="error">error</option>
            <option value="schedule">schedule</option>
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
            + {t('extensionTemplate.new')}
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
                <th>name</th>
                <th>component</th>
                <th>hook_type</th>
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
                    <div className="text-[11px] text-muted mono">{row.template_id}</div>
                  </td>
                  <td><span className={`tag ${(row.component ?? '').toLowerCase()}`}>{row.component}</span></td>
                  <td><span className={`tag ${(row.hook_type ?? '').toLowerCase()}`}>{row.hook_type}</span></td>
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

      <ExtensionTemplateModal
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
        message={t('extensionTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await ExtensionTemplateApi.remove(delTarget.template_id);
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
