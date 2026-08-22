import { ChevronDown, Search, Upload } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CatalogPage, PAGE_SIZE } from './CatalogPage';
import { AgentEditor } from './AgentEditor';
import { DefinitionDetailPage } from './DefinitionDetailPage';
import { PendingConnectorModals, usePendingConnectorFlow } from '../ConnectorMarket/usePendingConnectorFlow';
import { useConnectorStore } from '../../stores/connectorStore';
import {
  AgentInstallPendingError,
  createAgentManagementClient,
  type AgentCatalogItem,
  type AgentDraft,
  type AgentManagementClient,
  type DefinitionFileEntry,
  type McpOption,
  type RequestStatus,
  agentManagementReducer,
  buildCatalogViewModel,
  findFirstPreviewableFile,
  initialAgentManagementState,
  isPreviewableFile,
  mergeAgentDetailWithCatalog,
} from '../../features/agentManagement';
import './agentManagement.css';

type PanelView = 'catalog' | 'mine' | 'detail' | 'create';

type AgentManagementPanelProps = {
  onUseAgent?: (id: string) => void;
  onUsePrompt?: (id: string, prompt: string) => void;
};

const EMPTY_DRAFT: AgentDraft = {
  id: '',
  name: '',
  description: '',
  persona: '',
  tagIds: [],
  skillRefs: [],
  mcpRefs: [],
  suggestedPrompts: [],
};

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function getFriendlyErrorMessage(error: unknown, fallback: string, translate: (key: string, options?: Record<string, unknown>) => string): string {
  const message = typeof error === 'string' ? error : getErrorMessage(error, fallback);
  const connector = /^connector not connected:\s*(.+)$/i.exec(message.trim())?.[1];
  return connector ? translate('agentManagement.states.connectorUnavailableNamed', { connector }) : message;
}

function deriveAgentId(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug.length >= 3 ? slug.slice(0, 50) : `agent-${Date.now().toString(36)}`;
}

export function AgentManagementPanel({ onUseAgent, onUsePrompt }: AgentManagementPanelProps) {
  const { t } = useTranslation();
  const client = useMemo<AgentManagementClient>(() => createAgentManagementClient(), []);
  const [state, dispatch] = useReducer(agentManagementReducer, initialAgentManagementState);
  const [view, setView] = useState<PanelView>('catalog');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<'content' | 'files'>('content');
  const [query, setQuery] = useState('');
  const [mineQuery, setMineQuery] = useState('');
  const [category, setCategory] = useState('');
  const [catalogPage, setCatalogPage] = useState(1);
  const [minePage, setMinePage] = useState(1);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [detailOrigin, setDetailOrigin] = useState<'catalog' | 'mine'>('catalog');
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [connectorFlowId, setConnectorFlowId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [mcpOptions, setMcpOptions] = useState<McpOption[]>([]);
  const [mcpStatus, setMcpStatus] = useState<RequestStatus>('idle');
  const catalogRef = useRef<AgentCatalogItem[]>([]);
  const catalogRevisionRef = useRef(0);
  const detailRevisionRef = useRef(0);
  const filesRevisionRef = useRef(0);
  const fileRevisionRef = useRef(0);
  const installFlowTargetRef = useRef<string | null>(null);
  const reconnectFlowTargetRef = useRef<string | null>(null);
  const connectorError = useConnectorStore(state => state.error);
  const clearConnectorError = useConnectorStore(state => state.clearError);
  const formatActionError = useCallback(
    (error: unknown, fallback: string) => getFriendlyErrorMessage(error, fallback, t),
    [t],
  );

  const catalogView = useMemo(
    () => buildCatalogViewModel(state.catalog, { scope: 'catalog', category, query, page: catalogPage, pageSize: PAGE_SIZE }),
    [state.catalog, category, query, catalogPage],
  );
  const mineView = useMemo(
    () => buildCatalogViewModel(state.catalog, { scope: 'mine', category: '', query: mineQuery, page: minePage, pageSize: PAGE_SIZE }),
    [state.catalog, mineQuery, minePage],
  );

  const loadCatalog = useCallback(async () => {
    const revision = ++catalogRevisionRef.current;
    dispatch({ type: 'catalog.loading' });
    try {
      const catalog = await client.listCatalog();
      if (revision !== catalogRevisionRef.current) return;
      catalogRef.current = catalog;
      dispatch({ type: 'catalog.loaded', catalog });
    } catch (error) {
      if (revision !== catalogRevisionRef.current) return;
      dispatch({ type: 'catalog.error', message: getErrorMessage(error, t('agentManagement.states.loadError')) });
    }
  }, [client, t]);

  const loadSkills = useCallback(async () => {
    dispatch({ type: 'skills.loading' });
    try {
      const options = await client.listSkillOptions();
      dispatch({ type: 'skills.loaded', options });
    } catch {
      dispatch({ type: 'skills.error' });
    }
  }, [client]);

  const loadMcps = useCallback(async () => {
    setMcpStatus('loading');
    try {
      const options = await client.listMcpOptions();
      setMcpOptions(options);
      setMcpStatus('success');
    } catch {
      setMcpStatus('error');
    }
  }, [client]);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  const openDetail = useCallback(
    async (id: string) => {
      const revision = ++detailRevisionRef.current;
      if (view !== 'detail') {
        setDetailOrigin(view === 'mine' ? 'mine' : 'catalog');
      }
      setActionError(null);
      setSelectedId(id);
      setDetailTab('content');
      setView('detail');
      filesRevisionRef.current += 1;
      fileRevisionRef.current += 1;
      dispatch({ type: 'detail.loading' });
      try {
        const detail = await client.getDefinition(id);
        if (revision !== detailRevisionRef.current) return;
        dispatch({
          type: 'detail.loaded',
          detail: mergeAgentDetailWithCatalog(
            detail,
            catalogRef.current.find(item => item.id === id),
          ),
        });
      } catch (error) {
        if (revision !== detailRevisionRef.current) return;
        dispatch({ type: 'detail.error', message: getErrorMessage(error, t('agentManagement.states.detailError')) });
      }
    },
    [client, t, view],
  );

  const loadFiles = useCallback(
    async (id: string): Promise<DefinitionFileEntry[] | null> => {
      const revision = ++filesRevisionRef.current;
      dispatch({ type: 'files.loading' });
      try {
        const files = await client.getDefinitionFiles(id);
        if (revision !== filesRevisionRef.current) return null;
        dispatch({ type: 'files.loaded', files });
        return files;
      } catch (error) {
        if (revision !== filesRevisionRef.current) return null;
        dispatch({ type: 'files.error', message: getErrorMessage(error, t('agentManagement.files.loadError')) });
        return null;
      }
    },
    [client, t],
  );

  const handleTabChange = (tab: 'content' | 'files') => {
    if (tab === 'files' && state.detail?.installed !== true) return;
    setDetailTab(tab);
    if (tab === 'files' && selectedId && state.filesStatus === 'idle') {
      void loadFiles(selectedId).then(files => {
        const firstPreviewableFile = files ? findFirstPreviewableFile(files) : null;
        if (firstPreviewableFile) void handleSelectFile(firstPreviewableFile);
      });
    }
  };

  const handleSelectFile = async (relativePath: string) => {
    const revision = ++fileRevisionRef.current;
    if (!selectedId || !isPreviewableFile(relativePath)) {
      dispatch({ type: 'file.unsupported', relativePath });
      return;
    }
    dispatch({ type: 'file.loading', relativePath });
    try {
      const content = await client.getDefinitionFile(selectedId, relativePath);
      if (revision !== fileRevisionRef.current || content.relativePath !== relativePath) return;
      dispatch({ type: 'file.loaded', content });
    } catch (error) {
      if (revision !== fileRevisionRef.current) return;
      dispatch({ type: 'file.error', message: getErrorMessage(error, t('agentManagement.files.readError')) });
    }
  };

  const refreshAfterAction = useCallback(
    async (id: string) => {
      await loadCatalog();
      if (selectedId === id && view === 'detail') await openDetail(id);
    },
    [loadCatalog, openDetail, selectedId, view],
  );

  const retryInstallAfterConnect = useCallback(
    async (id: string) => {
      setActionError(null);
      try {
        await client.installDefinition(id);
        await refreshAfterAction(id);
      } catch (error) {
        setActionError(formatActionError(error, t('agentManagement.states.actionError')));
      } finally {
        setBusyId(null);
      }
    },
    [client, refreshAfterAction, t],
  );

  const refreshAfterReconnect = useCallback(
    async (id: string) => {
      setActionError(null);
      try {
        await refreshAfterAction(id);
      } catch (error) {
        setActionError(formatActionError(error, t('agentManagement.states.actionError')));
      } finally {
        setBusyId(null);
      }
    },
    [refreshAfterAction, t],
  );

  const installFlow = usePendingConnectorFlow(() => {
    const id = installFlowTargetRef.current;
    installFlowTargetRef.current = null;
    setConnectorFlowId(null);
    if (id) void retryInstallAfterConnect(id);
  });

  const reconnectFlow = usePendingConnectorFlow(() => {
    const id = reconnectFlowTargetRef.current;
    reconnectFlowTargetRef.current = null;
    setConnectorFlowId(null);
    if (id) void refreshAfterReconnect(id);
  });

  useEffect(() => {
    if (!connectorFlowId) return;
    const flowActive =
      installFlow.active ||
      reconnectFlow.active ||
      Boolean(installFlow.tokenTarget || installFlow.authTarget || reconnectFlow.tokenTarget || reconnectFlow.authTarget);
    if (flowActive) return;

    const id = installFlowTargetRef.current || reconnectFlowTargetRef.current;
    if (connectorError) setActionError(formatActionError(connectorError, t('agentManagement.states.actionError')));
    installFlowTargetRef.current = null;
    reconnectFlowTargetRef.current = null;
    setConnectorFlowId(null);
    setBusyId(current => (current === id ? null : current));
  }, [connectorError, connectorFlowId, formatActionError, installFlow.active, installFlow.authTarget, installFlow.tokenTarget, reconnectFlow.active, reconnectFlow.authTarget, reconnectFlow.tokenTarget, t]);

  const handleInstall = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    setActionNotice(null);
    clearConnectorError();
    try {
      const result = await client.installDefinition(id);
      if (result.kind === 'auth_required') {
        throw new Error(t('agentManagement.states.authRequired'));
      }
      await refreshAfterAction(id);
    } catch (error) {
      if (error instanceof AgentInstallPendingError) {
        installFlowTargetRef.current = id;
        setConnectorFlowId(id);
        installFlow.start(error.pendingConnectors);
        return;
      }
      setActionError(formatActionError(error, t('agentManagement.states.actionError')));
    } finally {
      if (installFlowTargetRef.current !== id) setBusyId(null);
    }
  };

  const handleUninstall = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await client.uninstallDefinition(id);
      await refreshAfterAction(id);
      if (result.notice) setActionNotice(result.notice);
    } catch (error) {
      setActionError(formatActionError(error, t('agentManagement.states.actionError')));
    } finally {
      setBusyId(null);
    }
  };

  const handleUse = (id: string) => {
    const item = catalogRef.current.find(candidate => candidate.id === id);
    if (!item?.installed || item.connectionState !== 'connected' || item.enabled === false) return;
    onUseAgent?.(id);
  };

  const handleReconnect = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    setActionNotice(null);
    clearConnectorError();
    try {
      const detail = state.detail?.id === id ? state.detail : await client.getDefinition(id);
      if (detail.pendingConnectors.length === 0) {
        setActionError(t('agentManagement.states.connectionUnavailable'));
        return;
      }
      reconnectFlowTargetRef.current = id;
      setConnectorFlowId(id);
      reconnectFlow.start(detail.pendingConnectors);
    } catch (error) {
      setActionError(formatActionError(error, t('agentManagement.states.actionError')));
    } finally {
      if (reconnectFlowTargetRef.current !== id) setBusyId(null);
    }
  };

  const openCreate = () => {
    setDraft(EMPTY_DRAFT);
    setCreateError(null);
    setActionError(null);
    setActionNotice(null);
    setCreateMenuOpen(false);
    setView('create');
    if (state.skillsStatus === 'idle') void loadSkills();
    if (mcpStatus === 'idle') void loadMcps();
  };

  const handleCreate = async () => {
    setSaving(true);
    setCreateError(null);
    setActionError(null);
    setActionNotice(null);
    try {
      await client.createAgent({ ...draft, id: draft.id || deriveAgentId(draft.name) });
      await loadCatalog();
      setMineQuery('');
      setMinePage(1);
      setView('mine');
    } catch (error) {
      setCreateError(getErrorMessage(error, t('agentManagement.form.saveError')));
    } finally {
      setSaving(false);
    }
  };

  const openUpload = () => {
    setCreateMenuOpen(false);
    setUploadFile(null);
    setActionError(null);
    setActionNotice(null);
    setUploadDialogOpen(true);
  };

  const handleUpload = async () => {
    if (!uploadFile) return;
    setUploading(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await client.importAgentTemplate(uploadFile);
      await loadCatalog();
      setUploadDialogOpen(false);
      setUploadFile(null);
      setMineQuery('');
      setMinePage(1);
      setView('mine');
      setActionNotice(t('agentManagement.states.uploadSuccess', { id: result.id }));
    } catch (error) {
      setActionError(formatActionError(error, t('agentManagement.states.uploadError')));
    } finally {
      setUploading(false);
    }
  };

  const goBackToCatalog = () => {
    setActionError(null);
    setActionNotice(null);
    setView(detailOrigin);
  };

  const pendingConnectorModals = (
    <>
      <PendingConnectorModals flow={installFlow} />
      <PendingConnectorModals flow={reconnectFlow} />
    </>
  );

  const uploadDialog = uploadDialogOpen ? createPortal(
    <div className="agent-management-selection-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !uploading) setUploadDialogOpen(false); }}>
      <section className="agent-management-upload-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-upload-dialog-title">
        <header>
          <h2 id="agent-upload-dialog-title">{t('agentManagement.actions.createByUpload')}</h2>
          <button type="button" onClick={() => setUploadDialogOpen(false)} aria-label={t('common.close')} disabled={uploading}>×</button>
        </header>
        <p>{t('agentManagement.form.uploadHint')}</p>
        <label className="agent-management-upload-picker">
          <Upload size={20} aria-hidden="true" />
          <span>{uploadFile ? uploadFile.name : t('agentManagement.form.uploadPlaceholder')}</span>
          <small>{t('agentManagement.form.uploadFormats')}</small>
          <input type="file" accept=".zip,.tar,.tar.gz" onChange={event => setUploadFile(event.target.files?.[0] || null)} disabled={uploading} />
        </label>
        <footer>
          <button type="button" className="agent-management-button agent-management-button--secondary" onClick={() => setUploadDialogOpen(false)} disabled={uploading}>{t('common.cancel')}</button>
          <button type="button" className="agent-management-button agent-management-button--primary" onClick={handleUpload} disabled={!uploadFile || uploading}>{uploading ? t('agentManagement.actions.uploading') : t('common.confirm')}</button>
        </footer>
      </section>
    </div>,
    document.body,
  ) : null;

  if (view === 'detail') {
    return (
      <>
        <main className="agent-management-panel agent-management-panel--detail" data-source={client.source}>
          <DefinitionDetailPage
            detail={state.detail}
            detailStatus={state.detailStatus}
            detailError={state.detailError}
            detailTab={detailTab}
            files={state.files}
            filesStatus={state.filesStatus}
            filesError={state.filesError}
            selectedFilePath={state.selectedFilePath}
            fileContent={state.fileContent}
            fileStatus={state.fileStatus}
            fileError={state.fileError}
            actionError={actionError}
            actionNotice={actionNotice}
            busy={busyId === selectedId}
            onBack={goBackToCatalog}
            onRetry={() => selectedId && void openDetail(selectedId)}
            onTabChange={handleTabChange}
            onRetryFiles={() =>
              selectedId &&
              void loadFiles(selectedId).then(files => {
                const firstPreviewableFile = files ? findFirstPreviewableFile(files) : null;
                if (firstPreviewableFile) void handleSelectFile(firstPreviewableFile);
              })
            }
            onSelectFile={handleSelectFile}
            onUse={handleUse}
            onUsePrompt={onUsePrompt}
            onReconnect={handleReconnect}
            onInstall={handleInstall}
            onUninstall={handleUninstall}
          />
        </main>
        {pendingConnectorModals}
        {uploadDialog}
      </>
    );
  }

  if (view === 'create') {
    return (
      <>
        <main className="agent-management-panel agent-management-panel--create" data-source={client.source}>
          <AgentEditor
            draft={draft}
            skillOptions={state.skillOptions}
            skillsStatus={state.skillsStatus}
            mcpOptions={mcpOptions}
            mcpStatus={mcpStatus}
            saving={saving}
            error={createError}
            onChange={setDraft}
            onReloadSkills={loadSkills}
            onCancel={() => {
              setActionError(null);
              setActionNotice(null);
              setView('mine');
            }}
            onSave={handleCreate}
          />
        </main>
        {pendingConnectorModals}
        {uploadDialog}
      </>
    );
  }

  const isMine = view === 'mine';
  return (
    <main className={`agent-management-panel agent-management-panel--${isMine ? 'mine' : 'catalog'}`} data-source={client.source}>
      <header className="agent-management-header">
        <div>
          <h1>{t('agentManagement.title')}</h1>
          <p>{t('agentManagement.subtitle')}</p>
        </div>
      </header>
      <div className="agent-management-primary-row">
        <nav className="agent-management-primary-tabs" role="tablist" aria-label={t('agentManagement.tabsLabel')}>
          <button
            type="button"
            role="tab"
            aria-selected={!isMine}
            className={!isMine ? 'is-active' : ''}
            onClick={() => {
              setCreateMenuOpen(false);
              setActionError(null);
              setActionNotice(null);
              setView('catalog');
            }}
          >
            {t('agentManagement.tabs.catalog')}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isMine}
            className={isMine ? 'is-active' : ''}
            onClick={() => {
              setCreateMenuOpen(false);
              setActionError(null);
              setActionNotice(null);
              setView('mine');
            }}
          >
            {t('agentManagement.tabs.mine')}
          </button>
        </nav>
        <div className="agent-management-primary-actions">
          <label className="agent-management-search">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">{t('agentManagement.searchLabel')}</span>
            <input
              type="search"
              value={isMine ? mineQuery : query}
              onChange={event => (isMine ? (setMineQuery(event.target.value), setMinePage(1)) : (setQuery(event.target.value), setCatalogPage(1)))}
              placeholder={t(isMine ? 'agentManagement.searchMine' : 'agentManagement.searchCatalog')}
            />
          </label>
          {isMine ? (
            <div className="agent-management-create-menu">
              <button
                type="button"
                className="agent-management-button agent-management-button--primary agent-management-create"
                aria-haspopup="menu"
                aria-expanded={createMenuOpen}
                onClick={() => setCreateMenuOpen(open => !open)}
              >
                {t('agentManagement.actions.create')}
                <ChevronDown size={15} aria-hidden="true" />
              </button>
              {createMenuOpen ? (
                <div className="agent-management-create-menu__popover" role="menu">
                  <button type="button" role="menuitem" onClick={openCreate}>
                    {t('agentManagement.actions.createFirst')}
                  </button>
                  <span className="agent-management-create-menu__disabled" role="menuitem" aria-disabled="true" title={t('agentManagement.states.featureUnavailable')}>
                    {t('agentManagement.actions.createByChat')}
                  </span>
                  <button type="button" role="menuitem" onClick={openUpload}>
                    {t('agentManagement.actions.createByUpload')}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {actionError ? (
        <div className="agent-management-inline-error" role="alert">
          {actionError}
        </div>
      ) : null}
      {actionNotice ? (
        <div className="agent-management-inline-notice" role="status">
          {actionNotice}
        </div>
      ) : null}
      <CatalogPage
        scope={isMine ? 'mine' : 'catalog'}
        items={isMine ? mineView.items : catalogView.items}
        totalItems={isMine ? mineView.totalItems : catalogView.totalItems}
        page={isMine ? mineView.page : catalogView.page}
        totalPages={isMine ? mineView.totalPages : catalogView.totalPages}
        query={isMine ? mineQuery : query}
        category={category}
        status={state.catalogStatus}
        error={state.catalogError}
        busyId={busyId}
        onCategoryChange={value => {
          setCategory(value);
          setCatalogPage(1);
        }}
        onPageChange={value => (isMine ? setMinePage(value) : setCatalogPage(value))}
        onRetry={loadCatalog}
        onOpen={openDetail}
        onUse={handleUse}
        onReconnect={handleReconnect}
        onInstall={handleInstall}
        onUninstall={handleUninstall}
        onCreate={openCreate}
      />
      {pendingConnectorModals}
      {uploadDialog}
    </main>
  );
}
