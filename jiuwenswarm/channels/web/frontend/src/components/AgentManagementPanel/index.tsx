import { ChevronDown } from 'lucide-react';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import SearchIcon from '../../assets/agent-management/agent-search.svg?react';
import { CatalogPage, PAGE_SIZE } from './CatalogPage';
import { AgentEditor } from './AgentEditor';
import { DefinitionDetailPage } from './DefinitionDetailPage';
import { AgentUploadDialog } from './AgentUploadDialog';
import { PendingConnectorModals, usePendingConnectorFlow } from '../ConnectorMarket/usePendingConnectorFlow';
import { useConnectorStore } from '../../stores/connectorStore';
import {
  AgentInstallPendingError,
  AgentManagementError,
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
  onCreateViaChat?: () => void;
};

const EMPTY_DRAFT: AgentDraft = {
  id: '',
  name: '',
  description: '',
  persona: '',
  tagIds: [],
  customTags: [],
  skillRefs: [],
  mcpRefs: [],
  suggestedPrompts: [],
};

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function getFriendlyErrorMessage(error: unknown, fallback: string, translate: (key: string, options?: Record<string, unknown>) => string): string {
  const message = typeof error === 'string' ? error : getErrorMessage(error, fallback);
  const code = error && typeof error === 'object' && 'code' in error
    ? String((error as { code?: unknown }).code || '')
    : '';
  if (code === 'agent_detail_empty') return translate('agentManagement.states.detailError');
  const normalizedMessage = message.trim();
  if (code === 'REQUEST_TIMEOUT') return translate('network.requestTimeout');
  if (code === 'WS_NOT_READY') return translate('network.connectionUnavailable');
  if (code === 'WS_DISCONNECTED') return translate('network.connectionClosed');
  if (code === 'REQUEST_ABORTED') return translate('network.requestAborted');
  if (/^agent_template package already exists in (?:local|built_in|resources):/i.test(normalizedMessage)) {
    return translate('agentManagement.states.duplicateName');
  }
  if (/^agent_template package not found:/i.test(normalizedMessage)) {
    return translate('agentManagement.states.agentUnavailable');
  }
  if (/^agent_template package (?:missing\/corrupt manifest\.json|wrong package_type|conflict):/i.test(normalizedMessage)) {
    return translate('agentManagement.states.agentDefinitionUnavailable');
  }
  if (/^(?:skill not found:|invalid skill name:|missing or invalid skills$)/i.test(normalizedMessage)) {
    return translate('agentManagement.states.skillUnavailable');
  }
  if (/^(?:mcp .* not found|invalid mcp name:|missing or invalid mcps$)/i.test(normalizedMessage)) {
    return translate('agentManagement.states.mcpUnavailable');
  }
  if (/^(?:invalid quick input:|missing or invalid quickInputs$)/i.test(normalizedMessage)) {
    return translate('agentManagement.states.promptInvalid');
  }
  if (/^(?:invalid tag|missing or invalid tags$)/i.test(normalizedMessage)) {
    return translate('agentManagement.states.tagInvalid');
  }
  const invalidField = /^missing or invalid (name|description|persona)$/i.exec(normalizedMessage)?.[1];
  if (invalidField) return translate(`agentManagement.form.errors.${invalidField}Required`);
  if (/^invalid params$/i.test(normalizedMessage)) {
    return translate('agentManagement.states.formInvalid');
  }
  if (/^file not found:/i.test(normalizedMessage)) {
    return translate('agentManagement.files.fileUnavailable');
  }
  if (/^file too large:/i.test(normalizedMessage)) {
    return translate('agentManagement.files.fileTooLarge');
  }
  if (/^file not previewable:/i.test(normalizedMessage)) {
    return translate('agentManagement.files.notPreviewable');
  }
  const connector = /^connector not connected:\s*(.+)$/i.exec(normalizedMessage)?.[1];
  if (connector) return translate('agentManagement.states.connectorUnavailableNamed', { connector });
  if (error instanceof AgentManagementError) {
    return fallback;
  }
  return message;
}

function deriveAgentId(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug.length >= 3 ? slug.slice(0, 50) : `agent-${Date.now().toString(36)}`;
}

export function AgentManagementPanel({ onUseAgent, onUsePrompt, onCreateViaChat }: AgentManagementPanelProps) {
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
  const [uploadError, setUploadError] = useState<string | null>(null);
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
      dispatch({ type: 'catalog.error', message: formatActionError(error, t('agentManagement.states.loadError')) });
    }
  }, [client, formatActionError, t]);

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
        dispatch({ type: 'detail.error', message: formatActionError(error, t('agentManagement.states.detailError')) });
      }
    },
    [client, formatActionError, t, view],
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
        dispatch({ type: 'files.error', message: formatActionError(error, t('agentManagement.files.loadError')) });
        return null;
      }
    },
    [client, formatActionError, t],
  );

  const handleTabChange = (tab: 'content' | 'files') => {
    setDetailTab(tab);
    const canPreviewFiles = state.detail?.source === 'local' || state.detail?.installed === true;
    if (tab === 'files' && canPreviewFiles && selectedId && state.filesStatus === 'idle') {
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
      dispatch({ type: 'file.error', message: formatActionError(error, t('agentManagement.files.readError')) });
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
    setCreateMenuOpen(false);
    setDraft(EMPTY_DRAFT);
    setCreateError(null);
    setActionError(null);
    setActionNotice(null);
    setView('create');
    if (state.skillsStatus === 'idle') void loadSkills();
    void loadMcps();
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
      setCreateError(formatActionError(error, t('agentManagement.form.saveError')));
    } finally {
      setSaving(false);
    }
  };

  const openUpload = () => {
    setCreateMenuOpen(false);
    setActionError(null);
    setActionNotice(null);
    setUploadError(null);
    setUploadDialogOpen(true);
  };

  const handleUpload = async (path: string) => {
    setActionError(null);
    setActionNotice(null);
    setUploadError(null);
    try {
      const result = await client.importAgentTemplate(path);
      await loadCatalog();
      setUploadDialogOpen(false);
      setUploadError(null);
      setMineQuery('');
      setMinePage(1);
      setView('mine');
      setActionNotice(t('agentManagement.states.uploadSuccess', { id: result.id }));
    } catch (error) {
      setUploadError(formatActionError(error, t('agentManagement.states.uploadError')));
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

  const uploadDialog = uploadDialogOpen ? (
    <AgentUploadDialog
      error={uploadError}
      onCancel={() => {
        setUploadDialogOpen(false);
        setUploadError(null);
      }}
      onConfirm={handleUpload}
    />
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
              (state.detail?.source === 'local' || state.detail?.installed === true) &&
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
            onReloadMcps={loadMcps}
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
            <SearchIcon aria-hidden="true" />
            <span className="sr-only">{t('agentManagement.searchLabel')}</span>
            <input
              type="search"
              name="agent-management-search"
              autoComplete="off"
              disabled={connectorFlowId !== null}
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
                  <button type="button" role="menuitem" onClick={() => { setCreateMenuOpen(false); onCreateViaChat?.(); }}>
                    {t('agentManagement.actions.createByChat')}
                  </button>
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
