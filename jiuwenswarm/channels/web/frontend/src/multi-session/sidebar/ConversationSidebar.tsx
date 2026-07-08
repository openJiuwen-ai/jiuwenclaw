import { useEffect, useMemo, useRef, useState } from 'react';
import { CircleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useChatStore, type ChatRuntime } from '../../stores/chatStore';
import { PROJECT_SESSION_PAGE_SIZE, useWorkspaceStore } from '../../stores';
import type { ProjectInfo, Session } from '../../types';
import {
  getConversationMenuItems,
  getProjectNewLabel,
  getProjectMenuItems,
  getProjectSessionMenuItems,
  getSessionActivityAt,
  getSessionIndicator,
  getTaskStatusLabel,
  sortSessionsForSidebar,
  type SidebarMenuAction,
  type SidebarMenuItem,
} from './sidebarModel';
import { ProjectCreateMenu } from './ProjectCreateMenu';
import { projectCreateErrorKey } from './projectCreateErrors';
import {
  isLikelyAbsolutePath,
  isProjectDirectoryPickerSupported,
  selectProjectDirectory,
} from '../../features/workspace/projectDirectoryPicker';
import './ConversationSidebar.css';
import addProjectIcon from '../../assets/work-mode/add-project.svg';
import collapseIcon from '../../assets/work-mode/collapse.svg';
import deleteIcon from '../../assets/work-mode/delete.svg';
import editIcon from '../../assets/work-mode/edit.svg';
import expandIcon from '../../assets/work-mode/expand.svg';
import folderFoldIcon from '../../assets/work-mode/folder-fold.svg';
import folderIcon from '../../assets/work-mode/folder.svg';
import moreIcon from '../../assets/work-mode/more-rimless.svg';
import newTaskIcon from '../../assets/work-mode/new-task.svg';
import pinIcon from '../../assets/work-mode/pin.svg';
import plusIcon from '../../assets/work-mode/plus.svg';
import statusProcessingIcon from '../../assets/work-mode/status-processing.svg';
import unpinIcon from '../../assets/work-mode/unpin.svg';

const UNREAD_KEY = 'jiuwenswarm_session_unread';
const RELATIVE_TIME_REFRESH_MS = 60_000;

export type NewConversationOptions = {
  preserveProject?: boolean;
  project?: Pick<ProjectInfo, 'project_id' | 'project_dir'>;
};

function isDefaultProject(project: ProjectInfo): boolean {
  return project.is_default || project.project_id === 'default';
}

interface ConversationSidebarProps {
  activeSessionId: string | null;
  onNew: (options?: NewConversationOptions) => void;
  onSelect: (session: Session) => void;
  onDelete: (session: Session) => void;
}

interface ConversationListItemProps {
  session: Session;
  runtime?: ChatRuntime;
  active: boolean;
  nested: boolean;
  unread: boolean;
  now: number;
  onSelect: () => void;
  onDelete: () => void;
  onPin: () => void;
  onRename: () => void;
  menuItems: SidebarMenuItem[];
}

export function getProcessingTransitions(
  previous: Record<string, boolean>,
  sessions: Session[],
  runtimes: Record<string, ChatRuntime>,
  activeSessionId: string | null,
): { snapshot: Record<string, boolean>; completedInBackground: string[] } {
  const snapshot: Record<string, boolean> = {};
  const completedInBackground: string[] = [];
  for (const session of sessions) {
    const sessionId = session.session_id;
    const processing = runtimes[sessionId]?.isProcessing ?? session.is_processing === true;
    snapshot[sessionId] = processing;
    if (previous[sessionId] && !processing && sessionId !== activeSessionId) {
      completedInBackground.push(sessionId);
    }
  }
  return { snapshot, completedInBackground };
}

export function formatRelativeTime(
  activityAt: number,
  now: number,
  language: string,
  translate: (key: string, options?: Record<string, unknown>) => string,
): string {
  const elapsed = Math.max(0, now - activityAt);
  if (elapsed < 60_000) return translate('time.justNow');
  if (elapsed < 3_600_000) return translate('time.minutesAgo', { count: Math.floor(elapsed / 60_000) });
  if (elapsed < 86_400_000) return translate('time.hoursAgo', { count: Math.floor(elapsed / 3_600_000) });
  if (elapsed < 604_800_000) return translate('time.daysAgo', { count: Math.floor(elapsed / 86_400_000) });
  return new Date(activityAt).toLocaleDateString(language, { month: 'short', day: 'numeric' });
}

function getSessionTitle(session: Session, fallback: string): string {
  return session.display_title?.trim() || session.title?.trim() || fallback;
}

const menuIconByAction: Record<SidebarMenuAction, string> = {
  pin: pinIcon,
  rename: editIcon,
  delete: deleteIcon,
};

function getMenuIcon(item: SidebarMenuItem): string {
  if (item.action === 'pin' && item.pinned) return unpinIcon;
  return menuIconByAction[item.action];
}

function SidebarMenu({
  items,
  onAction,
  disabledActions = [],
}: {
  items: SidebarMenuItem[];
  onAction: (action: SidebarMenuAction) => void;
  disabledActions?: SidebarMenuAction[];
}) {
  return (
    <div className="conversation-list-item__menu" role="menu">
      {items.map((item) => (
        <button
          key={item.action}
          type="button"
          className={`conversation-list-item__menu-item${item.danger ? ' conversation-list-item__menu-item--danger' : ''}`}
          disabled={disabledActions.includes(item.action)}
          onClick={() => onAction(item.action)}
          role="menuitem"
        >
          <img src={getMenuIcon(item)} alt="" aria-hidden="true" />
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  );
}

function loadUnreadSessions(): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(UNREAD_KEY) || '[]');
    if (!Array.isArray(value)) return new Set();
    return new Set(value.filter((id): id is string => typeof id === 'string'));
  } catch {
    return new Set();
  }
}

function ConversationListItem({
  session,
  runtime,
  active,
  nested,
  unread,
  now,
  onSelect,
  onDelete,
  onPin,
  onRename,
  menuItems,
}: ConversationListItemProps) {
  const { t, i18n } = useTranslation();
  const itemRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const title = getSessionTitle(session, t('multiSession.untitled'));
  const errorMessage = runtime?.error || runtime?.executionError || null;
  const indicator = getSessionIndicator(runtime, unread, session.is_processing === true, Boolean(errorMessage));
  const deleteDisabled = indicator === 'processing' || indicator === 'waiting';

  let status: React.ReactNode;
  if (indicator === 'waiting') {
    status = (
      <span className="conversation-list-item__status-waiting" title={getTaskStatusLabel(indicator, t)}>
        <span>{t('multiSession.status.waiting')}</span>
      </span>
    );
  } else if (indicator === 'processing') {
    status = <img className="conversation-list-item__status-icon" src={statusProcessingIcon} alt="" title={getTaskStatusLabel(indicator, t)} aria-hidden="true" />;
  } else if (indicator === 'unread') {
    status = <span className="conversation-list-item__status-dot" title={t('multiSession.completedUnread')} aria-hidden="true" />;
  } else if (indicator === 'error') {
    status = (
      <span title={errorMessage ?? getTaskStatusLabel(indicator, t)}>
        <CircleAlert className="conversation-list-item__status-error" size={14} strokeWidth={1.8} aria-hidden="true" />
      </span>
    );
  } else {
    status = (
      <span className="conversation-list-item__status-read" title={getTaskStatusLabel(indicator, t)}>
        <span>{formatRelativeTime(getSessionActivityAt(session), now, i18n.language, t)}</span>
      </span>
    );
  }

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!itemRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  return (
    <div ref={itemRef} className={`conversation-list-item${active ? ' is-active' : ''}${menuOpen ? ' is-menu-open' : ''}${nested ? ' conversation-list-item--nested' : ''}`}>
      <button type="button" className="conversation-list-item__main" onClick={onSelect} title={title}>
        <span className="conversation-list-item__title">{title}</span>
        <span className="conversation-list-item__meta">{status}</span>
      </button>
      <button
        type="button"
        className="conversation-list-item__actions"
        onClick={(event) => {
          event.stopPropagation();
          setMenuOpen((open) => !open);
        }}
        title={t('multiSession.moreActions')}
        aria-label={t('multiSession.moreActions')}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <img src={moreIcon} alt="" aria-hidden="true" />
      </button>
      <button
        type="button"
        className="conversation-list-item__pin-action"
        onClick={(event) => {
          event.stopPropagation();
          onPin();
        }}
        title={session.pinned ? t('multiSession.project.unpinConversation') : t('multiSession.project.pinConversation')}
        aria-label={session.pinned ? t('multiSession.project.unpinConversation') : t('multiSession.project.pinConversation')}
        data-tooltip={session.pinned ? t('multiSession.project.unpinConversation') : t('multiSession.project.pinConversation')}
      >
        <img src={session.pinned ? unpinIcon : pinIcon} alt="" aria-hidden="true" />
      </button>
      {menuOpen ? (
        <SidebarMenu
          items={menuItems}
          disabledActions={deleteDisabled ? ['delete'] : []}
          onAction={(action) => {
            setMenuOpen(false);
            switch (action) {
              case 'pin':
                onPin();
                break;
              case 'rename':
                onRename();
                break;
              case 'delete':
                onDelete();
                break;
            }
          }}
        />
      ) : null}
    </div>
  );
}

function ProjectEntityRow({
  title,
  isExpanded,
  isPinned,
  hideActions = false,
  onToggle,
  onNew,
  onPin,
  onRename,
  onRemove,
  newLabel,
}: {
  title: string;
  isExpanded: boolean;
  isPinned?: boolean;
  hideActions?: boolean;
  onToggle: () => void;
  onNew: () => void;
  onPin: () => void;
  onRename: () => void;
  onRemove: () => void;
  newLabel?: string;
}) {
  const { t } = useTranslation();
  const rowRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!rowRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [menuOpen]);

  return (
    <div ref={rowRef} className={`conversation-entity-row${menuOpen ? ' is-menu-open' : ''}`}>
      <button type="button" className="conversation-entity-row__main" onClick={onToggle} title={title}>
        <span className="conversation-entity-row__icon">
          <img src={isExpanded ? folderFoldIcon : folderIcon} alt="" aria-hidden="true" />
        </span>
        <span className="conversation-entity-row__text">
          <span className="conversation-entity-row__title">{title}</span>
        </span>
        <img className="conversation-entity-row__chevron" src={isExpanded ? collapseIcon : expandIcon} alt="" aria-hidden="true" />
        {isPinned ? <img src={pinIcon} className="conversation-entity-row__pin" alt="" aria-hidden="true" /> : null}
      </button>
      <button
        type="button"
        className="conversation-entity-row__plus"
        onClick={(event) => {
          event.stopPropagation();
          onNew();
        }}
        title={newLabel || t('multiSession.project.newConversation')}
        aria-label={newLabel || t('multiSession.project.newConversation')}
        data-tooltip={newLabel || t('multiSession.project.newConversation')}
      >
        <img src={plusIcon} alt="" aria-hidden="true" />
      </button>
      {hideActions ? null : (
        <button
          type="button"
          className="conversation-list-item__actions"
          onClick={(event) => {
            event.stopPropagation();
            setMenuOpen((open) => !open);
          }}
          title={t('multiSession.moreActions')}
          aria-label={t('multiSession.moreActions')}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <img src={moreIcon} alt="" aria-hidden="true" />
        </button>
      )}
      {!hideActions && menuOpen ? (
        <SidebarMenu
          items={getProjectMenuItems(Boolean(isPinned), t)}
          onAction={(action) => {
            setMenuOpen(false);
            switch (action) {
              case 'pin':
                onPin();
                break;
              case 'rename':
                onRename();
                break;
              case 'delete':
                onRemove();
                break;
            }
          }}
        />
      ) : null}
    </div>
  );
}

function PathInputDialog({
  title,
  initialValue = '',
  placeholder = '/Users/name/work/project',
  error,
  onCancel,
  onSubmit,
}: {
  title: string;
  initialValue?: string;
  placeholder?: string;
  error?: string | null;
  onCancel: () => void;
  onSubmit: (value: string) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initialValue);

  return (
    <div className="conversation-path-dialog-backdrop" role="presentation">
      <form
        className="conversation-path-dialog"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = value.trim();
          if (trimmed) onSubmit(trimmed);
        }}
      >
        <div className="conversation-path-dialog__title">{title}</div>
        <input
          className="conversation-path-dialog__input"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={placeholder}
          maxLength={200}
          autoFocus
        />
        {error ? <div className="conversation-path-dialog__error">{error}</div> : null}
        <div className="conversation-path-dialog__actions">
          <button type="button" onClick={onCancel}>{t('multiSession.project.cancel')}</button>
          <button type="submit" disabled={!value.trim()}>{t('multiSession.project.confirm')}</button>
        </div>
      </form>
    </div>
  );
}

function ProjectAddMenu({
  onCreateBlank,
  onSelectExisting,
}: {
  onCreateBlank: () => void;
  onSelectExisting: () => void;
}) {
  return (
    <div className="conversation-sidebar__add-menu" role="menu">
      <ProjectCreateMenu
        onCreate={(mode) => {
          switch (mode) {
            case 'blank':
              onCreateBlank();
              break;
            case 'existing':
              onSelectExisting();
              break;
          }
        }}
        itemClassName="conversation-list-item__menu-item"
        blankIcon={<img src={addProjectIcon} alt="" aria-hidden="true" />}
        existingIcon={<img src={folderIcon} alt="" aria-hidden="true" />}
      />
    </div>
  );
}

function ProjectCreateDialog({
  mode,
  error,
  initial,
  onCancel,
  onSubmit,
}: {
  mode: 'blank' | 'existing';
  error?: string | null;
  initial?: { name?: string; path?: string } | null;
  onCancel: () => void;
  onSubmit: (name: string, projectDir: string) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [projectDir, setProjectDir] = useState('');
  const canSubmit = Boolean(name.trim() && (mode === 'blank' || projectDir.trim()));

  useEffect(() => {
    setName(initial?.name || '');
    setProjectDir(initial?.path || '');
  }, [initial]);

  return (
    <div className="conversation-path-dialog-backdrop" role="presentation">
      <form
        className="conversation-path-dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) onSubmit(name.trim(), mode === 'blank' ? '' : projectDir.trim());
        }}
      >
        <div className="conversation-path-dialog__title">{t('multiSession.project.newProject')}</div>
        <input
          className="conversation-path-dialog__input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t('multiSession.project.namePlaceholder')}
          autoFocus
        />
        {mode === 'existing' ? (
          <input
            className="conversation-path-dialog__input"
            value={projectDir}
            onChange={(event) => setProjectDir(event.target.value)}
            placeholder="/Users/name/work/project"
          />
        ) : null}
        {error ? <div className="conversation-path-dialog__error">{error}</div> : null}
        <div className="conversation-path-dialog__actions">
          <button type="button" onClick={onCancel}>{t('multiSession.project.cancel')}</button>
          <button type="submit" disabled={!canSubmit}>{t('multiSession.project.confirm')}</button>
        </div>
      </form>
    </div>
  );
}

function ProjectDeleteDialog({
  project,
  error,
  deleting,
  onCancel,
  onDelete,
}: {
  project: ProjectInfo;
  error?: string | null;
  deleting: boolean;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="conversation-path-dialog-backdrop" role="presentation">
      <div className="conversation-path-dialog" role="dialog" aria-modal="true" aria-labelledby="project-delete-title">
        <div id="project-delete-title" className="conversation-path-dialog__title">{t('multiSession.project.deleteProject')}</div>
        <div className="conversation-path-dialog__message">
          {t('multiSession.project.deleteProjectDescription', { projectName: project.name })}
        </div>
        {error ? <div className="conversation-path-dialog__error">{error}</div> : null}
        <div className="conversation-path-dialog__actions">
          <button type="button" onClick={onCancel} disabled={deleting}>{t('multiSession.project.cancel')}</button>
          <button type="button" onClick={onDelete} disabled={deleting}>{t('multiSession.delete')}</button>
        </div>
      </div>
    </div>
  );
}

type RenameTarget =
  | { kind: 'project'; id: string; value: string }
  | { kind: 'session'; id: string; value: string };

export function ConversationSidebar({
  activeSessionId,
  onNew,
  onSelect,
  onDelete,
}: ConversationSidebarProps) {
  const { t } = useTranslation();
  const runtimes = useChatStore((state) => state.runtimes);
  const [relativeTimeNow, setRelativeTimeNow] = useState(Date.now);
  const [unreadSessions, setUnreadSessions] = useState(loadUnreadSessions);
  const [pathDialogOpen, setPathDialogOpen] = useState(false);
  const [projectCreateMode, setProjectCreateMode] = useState<'blank' | 'existing'>('existing');
  const [pathDialogError, setPathDialogError] = useState<string | null>(null);
  const [pathDialogInitial, setPathDialogInitial] = useState<{ name?: string; path?: string } | null>(null);
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [pinError, setPinError] = useState<string | null>(null);
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<ProjectInfo | null>(null);
  const [deleteProjectBusy, setDeleteProjectBusy] = useState(false);
  const [deleteProjectError, setDeleteProjectError] = useState<string | null>(null);
  const [projectAddMenuOpen, setProjectAddMenuOpen] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);
  const previousProcessing = useRef<Record<string, boolean>>({});
  const {
    projects,
    projectSessions,
    projectSessionTotals,
    sessionVisibility,
    pinnedSessions,
    expandedProjectIds,
    setSelectedProject,
    toggleProjectExpanded,
    createProject,
    renameProject,
    pinProject,
    removeProject,
    loadProjectSessions,
    showMoreSessions,
    collapseSessions,
    pinSession,
    renameSession,
  } = useWorkspaceStore();

  useEffect(() => {
    const timer = window.setInterval(() => setRelativeTimeNow(Date.now()), RELATIVE_TIME_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    setUnreadSessions((current) => {
      if (!current.has(activeSessionId)) return current;
      const next = new Set(current);
      next.delete(activeSessionId);
      return next;
    });
  }, [activeSessionId]);

  useEffect(() => {
    localStorage.setItem(UNREAD_KEY, JSON.stringify([...unreadSessions]));
  }, [unreadSessions]);

  useEffect(() => {
    if (!projectAddMenuOpen) return;
    const close = (event: MouseEvent) => {
      if (!addMenuRef.current?.contains(event.target as Node)) setProjectAddMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setProjectAddMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [projectAddMenuOpen]);

  const projectIdSnapshot = useMemo(() => projects.map((project) => project.project_id).join('\0'), [projects]);

  useEffect(() => {
    for (const projectId of projectIdSnapshot.split('\0')) {
      if (projectId && (expandedProjectIds[projectId] ?? true)) {
        void loadProjectSessions(projectId);
      }
    }
  }, [expandedProjectIds, loadProjectSessions, projectIdSnapshot]);

  const projectByDir = useMemo(() => {
    const byDir = new Map<string, ProjectInfo>();
    for (const project of projects) {
      if (project.project_dir) byDir.set(project.project_dir, project);
    }
    return byDir;
  }, [projects]);
  const defaultProject = useMemo(
    () => projects.find(isDefaultProject),
    [projects],
  );

  const pinnedProjects = useMemo(() => projects.filter((project) => project.pinned && !isDefaultProject(project)), [projects]);
  const regularProjects = useMemo(() => projects.filter((project) => !project.pinned && !isDefaultProject(project)), [projects]);
  const sortedProjectSessions = useMemo(() => {
    const sorted: Record<string, Session[]> = {};
    for (const [projectId, list] of Object.entries(projectSessions)) {
      sorted[projectId] = sortSessionsForSidebar(list);
    }
    return sorted;
  }, [projectSessions]);
  const conversationSessions = useMemo(() => {
    if (defaultProject) return sortedProjectSessions[defaultProject.project_id] || [];
    return [];
  }, [defaultProject, sortedProjectSessions]);
  const orderedPinnedSessions = useMemo(() => sortSessionsForSidebar(pinnedSessions), [pinnedSessions]);
  const observedSidebarSessions = useMemo(() => {
    const byId = new Map<string, Session>();
    for (const session of orderedPinnedSessions) {
      byId.set(session.session_id, session);
    }
    for (const list of Object.values(sortedProjectSessions)) {
      for (const session of list) {
        byId.set(session.session_id, session);
      }
    }
    return Array.from(byId.values());
  }, [orderedPinnedSessions, sortedProjectSessions]);

  useEffect(() => {
    const { snapshot, completedInBackground } = getProcessingTransitions(
      previousProcessing.current,
      observedSidebarSessions,
      runtimes,
      activeSessionId,
    );
    previousProcessing.current = snapshot;
    if (completedInBackground.length > 0) {
      setUnreadSessions((current) => new Set([...current, ...completedInBackground]));
    }
  }, [activeSessionId, observedSidebarSessions, runtimes]);

  async function handlePinSession(session: Session) {
    setPinError(null);
    try {
      await pinSession(session.session_id, !session.pinned);
    } catch (error) {
      setPinError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleRenameSession(sessionId: string, title: string) {
    await renameSession(sessionId, title);
  }

  async function handleRenameSubmit(value: string) {
    if (!renameTarget) return;
    setRenameError(null);
    setPinError(null);
    try {
      if (renameTarget.kind === 'project') {
        await renameProject(renameTarget.id, value);
      } else {
        await handleRenameSession(renameTarget.id, value);
      }
      setRenameTarget(null);
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleCreateProject(name: string, projectDir: string) {
    setPathDialogError(null);
    if (projectDir && (!isLikelyAbsolutePath(projectDir) || projectDir.startsWith('~/'))) {
      setPathDialogError(t('multiSession.project.absolutePathError'));
      return;
    }
    try {
      await createProject(name, projectDir);
      setPathDialogInitial(null);
      setPathDialogOpen(false);
    } catch (error) {
      const errorKey = projectCreateErrorKey(error);
      setPathDialogError(errorKey ? t(errorKey) : error instanceof Error ? error.message : String(error));
    }
  }

  async function handleSelectExistingProjectDirectory() {
    setPathDialogError(null);
    if (!isProjectDirectoryPickerSupported()) {
      setProjectCreateMode('existing');
      setPathDialogInitial(null);
      setPathDialogOpen(true);
      return;
    }

    const result = await selectProjectDirectory();
    if (!result.ok) {
      if (result.reason === 'cancelled') return;
      setProjectCreateMode('existing');
      setPathDialogInitial(null);
      setPathDialogOpen(true);
      setPathDialogError(
        result.reason === 'unsupported'
          ? t('multiSession.project.directoryPickerUnsupported')
          : result.message || t('multiSession.project.directoryPickerFailed'),
      );
      return;
    }
    try {
      await createProject(result.name, result.path);
      setPathDialogInitial(null);
      setPathDialogOpen(false);
    } catch (error) {
      const errorKey = projectCreateErrorKey(error);
      setPathDialogError(errorKey ? t(errorKey) : error instanceof Error ? error.message : String(error));
    }
  }

  async function handleRemoveProject() {
    if (!deleteProjectTarget || isDefaultProject(deleteProjectTarget)) return;
    setDeleteProjectBusy(true);
    setDeleteProjectError(null);
    try {
      await removeProject(deleteProjectTarget.project_id);
      setDeleteProjectTarget(null);
    } catch (error) {
      setDeleteProjectError(error instanceof Error ? error.message : String(error));
    } finally {
      setDeleteProjectBusy(false);
    }
  }

  function renderSession(session: Session, options: { nested?: boolean; projectMenu?: boolean } = {}) {
    const nested = options.nested === true;
    const projectMenu = options.projectMenu === true;
    return (
      <ConversationListItem
        key={session.session_id}
        session={session}
        runtime={runtimes[session.session_id]}
        active={activeSessionId === session.session_id}
        nested={nested}
        unread={unreadSessions.has(session.session_id)}
        now={relativeTimeNow}
        onSelect={() => onSelect(session)}
        onDelete={() => onDelete(session)}
        onPin={() => void handlePinSession(session)}
        menuItems={projectMenu
          ? getProjectSessionMenuItems(Boolean(session.pinned), t)
          : getConversationMenuItems(Boolean(session.pinned), t)}
        onRename={() => setRenameTarget({
          kind: 'session',
          id: session.session_id,
          value: getSessionTitle(session, t('multiSession.untitled')),
        })}
      />
    );
  }

  function getSessionProject(session: Session): ProjectInfo | undefined {
    if (session.project_dir === '') return defaultProject;
    return session.project_dir ? projectByDir.get(session.project_dir) : undefined;
  }

  function renderSessionPagination(projectId: string, nested = false) {
    const visibleCount = sessionVisibility[projectId]?.visibleCount ?? PROJECT_SESSION_PAGE_SIZE;
    const renderedCount = projectSessions[projectId]?.length ?? 0;
    const total = projectSessionTotals[projectId] ?? projectSessions[projectId]?.length ?? 0;
    const canShowMore = total > renderedCount;
    const canCollapse = visibleCount > PROJECT_SESSION_PAGE_SIZE;

    if (!canShowMore && !canCollapse) return null;

    return (
      <div className={`conversation-sidebar__pagination${nested ? ' conversation-sidebar__pagination--nested' : ''}`}>
        {canShowMore ? (
          <button
            type="button"
            className="conversation-sidebar__pagination-button"
            onClick={() => { void showMoreSessions(projectId); }}
          >
            {t('multiSession.showMore')}
          </button>
        ) : null}
        {canCollapse ? (
          <button
            type="button"
            className="conversation-sidebar__pagination-button"
            onClick={() => { void collapseSessions(projectId); }}
          >
            {t('multiSession.collapse')}
          </button>
        ) : null}
      </div>
    );
  }

  function renderProject(project: ProjectInfo) {
    const sessionsForProject = sortedProjectSessions[project.project_id] || [];
    const expanded = expandedProjectIds[project.project_id] ?? true;
    return (
      <div key={project.project_id} className="conversation-sidebar__group">
        <ProjectEntityRow
          title={project.name}
          isExpanded={expanded}
          isPinned={project.pinned}
          hideActions={isDefaultProject(project)}
          newLabel={getProjectNewLabel(project.name, t)}
          onToggle={() => toggleProjectExpanded(project.project_id)}
          onNew={() => {
            setSelectedProject(project);
            onNew({ preserveProject: true, project });
          }}
          onPin={() => {
            if (isDefaultProject(project)) return;
            void pinProject(project.project_id, !project.pinned);
          }}
          onRename={() => {
            if (isDefaultProject(project)) return;
            setRenameError(null);
            setRenameTarget({ kind: 'project', id: project.project_id, value: project.name });
          }}
          onRemove={() => {
            if (isDefaultProject(project)) return;
            setDeleteProjectError(null);
            setDeleteProjectTarget(project);
          }}
        />
        {expanded ? (
          <div className="conversation-sidebar__group-list">
            {sessionsForProject.length > 0 ? sessionsForProject.map((session) => renderSession(session, { nested: true, projectMenu: true })) : (
              <div className="conversation-sidebar__empty">{t('multiSession.project.noConversations')}</div>
            )}
            {renderSessionPagination(project.project_id, true)}
          </div>
        ) : null}
      </div>
    );
  }

  const hasPinnedSection = pinnedProjects.length > 0 || orderedPinnedSessions.length > 0;

  return (
    <aside className="conversation-sidebar" aria-label={t('multiSession.conversations')}>
      <div className="conversation-sidebar__title">{t('multiSession.title')}</div>
      <button type="button" className="conversation-sidebar__new" onClick={() => {
        setSelectedProject(null);
        setPinError(null);
        onNew();
      }}>
        <img src={newTaskIcon} alt="" aria-hidden="true" />
        <span>{t('multiSession.newConversation')}</span>
      </button>
      <div className="conversation-sidebar__body">
        {hasPinnedSection ? (
          <div className="conversation-sidebar__pinned-block">
            <div className="conversation-sidebar__section-heading conversation-sidebar__section-heading--pinned">
              <span className="conversation-sidebar__label">{t('multiSession.project.pinned')}</span>
            </div>
            <div className="conversation-sidebar__pinned-list">
              {orderedPinnedSessions.map((session) => {
                const project = getSessionProject(session);
                return renderSession(session, { projectMenu: Boolean(project && !isDefaultProject(project)) });
              })}
              {pinnedProjects.map((project) => renderProject(project))}
            </div>
          </div>
        ) : null}
        {pinError ? (
          <div className="conversation-sidebar__error" role="alert">
            {t('multiSession.project.pinFailed')}: {pinError}
          </div>
        ) : null}
        {pathDialogError && !pathDialogOpen ? (
          <div className="conversation-sidebar__error" role="alert">
            {pathDialogError}
          </div>
        ) : null}
        <div className="conversation-sidebar__section-heading" ref={addMenuRef}>
          <span className="conversation-sidebar__label">{t('multiSession.project.projects')}</span>
          <div className="conversation-sidebar__section-actions">
            <button
              type="button"
              className="conversation-sidebar__section-action"
              onClick={() => {
                setProjectAddMenuOpen((open) => !open);
              }}
              title={t('multiSession.project.newProject')}
              aria-label={t('multiSession.project.newProject')}
              aria-haspopup="menu"
              aria-expanded={projectAddMenuOpen}
            >
              <img src={plusIcon} alt="" aria-hidden="true" />
            </button>
          </div>
          {projectAddMenuOpen ? (
            <ProjectAddMenu
              onCreateBlank={() => {
                setProjectAddMenuOpen(false);
                setProjectCreateMode('blank');
                setPathDialogError(null);
                setPathDialogInitial(null);
                setPathDialogOpen(true);
              }}
              onSelectExisting={() => {
                setProjectAddMenuOpen(false);
                void handleSelectExistingProjectDirectory();
              }}
            />
          ) : null}
        </div>
        <div className="conversation-sidebar__list">
          {regularProjects.length === 0 ? (
            <div className="conversation-sidebar__empty">{t('multiSession.project.noProjects')}</div>
          ) : null}
          {regularProjects.map((project) => renderProject(project))}
        </div>
        <div className="conversation-sidebar__group">
          <div className="conversation-sidebar__section-heading">
            <span className="conversation-sidebar__label">{t('multiSession.conversations')}</span>
            <button
              type="button"
              className="conversation-sidebar__section-new"
              onClick={() => {
                setSelectedProject(null);
                setPinError(null);
                onNew();
              }}
              title={t('multiSession.project.newConversation')}
              aria-label={t('multiSession.project.newConversation')}
              data-tooltip={t('multiSession.project.newConversation')}
            >
              <img src={plusIcon} alt="" aria-hidden="true" />
            </button>
          </div>
          <div className="conversation-sidebar__group-list">
            {conversationSessions.length > 0 ? conversationSessions.map((session) => renderSession(session)) : (
              <div className="conversation-sidebar__empty">{t('multiSession.project.noConversations')}</div>
            )}
            {defaultProject ? renderSessionPagination(defaultProject.project_id, false) : null}
          </div>
        </div>
      </div>
      {pathDialogOpen ? (
        <ProjectCreateDialog
          mode={projectCreateMode}
          error={pathDialogError}
          initial={pathDialogInitial}
          onCancel={() => {
            setPathDialogError(null);
            setPathDialogInitial(null);
            setPathDialogOpen(false);
          }}
          onSubmit={(name, projectDir) => void handleCreateProject(name, projectDir)}
        />
      ) : null}
      {renameTarget ? (
        <PathInputDialog
          title={t('multiSession.project.rename')}
          initialValue={renameTarget.value}
          placeholder={t('multiSession.project.renamePlaceholder')}
          error={renameError}
          onCancel={() => {
            setRenameError(null);
            setPinError(null);
            setRenameTarget(null);
          }}
          onSubmit={(value) => void handleRenameSubmit(value)}
        />
      ) : null}
      {deleteProjectTarget ? (
        <ProjectDeleteDialog
          project={deleteProjectTarget}
          deleting={deleteProjectBusy}
          error={deleteProjectError}
          onCancel={() => {
            setDeleteProjectError(null);
            setDeleteProjectTarget(null);
          }}
          onDelete={() => { void handleRemoveProject(); }}
        />
      ) : null}
    </aside>
  );
}
