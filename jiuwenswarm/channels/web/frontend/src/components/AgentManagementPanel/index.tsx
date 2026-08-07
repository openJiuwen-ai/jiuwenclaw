import { ChevronDown, Plus, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CatalogPage, PAGE_SIZE } from './CatalogPage';
import { AgentEditor } from './AgentEditor';
import { DefinitionDetailPage } from './DefinitionDetailPage';
import {
  createAgentManagementClient,
  type AgentCatalogItem,
  type AgentDraft,
  type AgentManagementClient,
  type DefinitionFileEntry,
  agentManagementReducer,
  buildCatalogViewModel,
  initialAgentManagementState,
  isPreviewableFile,
  mergeAgentDetailWithCatalog,
} from '../../features/agentManagement';
import './agentManagement.css';

type PanelView = 'catalog' | 'mine' | 'detail' | 'create';

type AgentManagementPanelProps = {
  onUseAgent?: (id: string) => void;
};

const EMPTY_DRAFT: AgentDraft = {
  id: '',
  name: '',
  description: '',
  persona: '',
  skillRefs: [],
};

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function findFirstPreviewableFile(entries: DefinitionFileEntry[]): string | null {
  const preferred = entries.find(entry => entry.kind === 'file' && entry.relativePath.toLowerCase().startsWith('persona/') && entry.previewable);
  if (preferred) return preferred.relativePath;
  for (const entry of entries) {
    if (entry.kind === 'file' && entry.previewable) return entry.relativePath;
    const nested = entry.children ? findFirstPreviewableFile(entry.children) : null;
    if (nested) return nested;
  }
  return null;
}

export function AgentManagementPanel({ onUseAgent }: AgentManagementPanelProps) {
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
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const catalogRef = useRef<AgentCatalogItem[]>([]);
  const catalogRevisionRef = useRef(0);
  const detailRevisionRef = useRef(0);
  const filesRevisionRef = useRef(0);
  const fileRevisionRef = useRef(0);

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

  const handleInstall = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      const result = await client.installDefinition(id);
      if (result.kind === 'auth_required') {
        throw new Error(t('agentManagement.states.authRequired'));
      }
      await loadCatalog();
      if (selectedId === id && view === 'detail') await openDetail(id);
    } catch (error) {
      setActionError(getErrorMessage(error, t('agentManagement.states.actionError')));
    } finally {
      setBusyId(null);
    }
  };

  const handleUninstall = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      await client.uninstallDefinition(id);
      await loadCatalog();
      if (selectedId === id && view === 'detail') await openDetail(id);
    } catch (error) {
      setActionError(getErrorMessage(error, t('agentManagement.states.actionError')));
    } finally {
      setBusyId(null);
    }
  };

  const handleUse = (id: string) => {
    const item = catalogRef.current.find((candidate) => candidate.id === id);
    if (!item?.installed || item.enabled === false) return;
    onUseAgent?.(id);
  };

  const openCreate = () => {
    setDraft(EMPTY_DRAFT);
    setCreateError(null);
    setCreateMenuOpen(false);
    setView('create');
    if (state.skillsStatus === 'idle') void loadSkills();
  };

  const handleCreate = async () => {
    setSaving(true);
    setCreateError(null);
    try {
      await client.createAgent(draft);
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

  const goBackToCatalog = () => setView(detailOrigin);

  if (view === 'detail') {
    return (
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
          onInstall={handleInstall}
          onUninstall={handleUninstall}
        />
      </main>
    );
  }

  if (view === 'create') {
    return (
      <main className="agent-management-panel agent-management-panel--create" data-source={client.source}>
        <AgentEditor
          draft={draft}
          skillOptions={state.skillOptions}
          skillsStatus={state.skillsStatus}
          saving={saving}
          error={createError}
          onChange={setDraft}
          onReloadSkills={loadSkills}
          onCancel={() => setView('mine')}
          onSave={handleCreate}
        />
      </main>
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
                <Plus size={16} aria-hidden="true" />
                {t('agentManagement.actions.create')}
                <ChevronDown size={15} aria-hidden="true" />
              </button>
              {createMenuOpen ? (
                <div className="agent-management-create-menu__popover" role="menu">
                  <button type="button" role="menuitem" onClick={openCreate}>
                    {t('agentManagement.actions.createFirst')}
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
        onInstall={handleInstall}
        onUninstall={handleUninstall}
        onCreate={openCreate}
      />
    </main>
  );
}
