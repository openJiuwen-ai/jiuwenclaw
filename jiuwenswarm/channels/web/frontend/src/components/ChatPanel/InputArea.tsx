import { useState, useRef, useCallback, KeyboardEvent, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Square } from 'lucide-react';
import { useSpeechRecognition } from '../../hooks';

// import { stopAllTts } from '../../utils';
import { useChatStore, useSessionStore, useWorkspaceStore } from '../../stores';
import { AgentMode, Permission, type ProjectInfo } from '../../types';
import { NEW_CONVERSATION_ID } from '../../multi-session/state/newConversationLifecycle';
import { ProjectCreateMenu, type ProjectCreateMode } from '../../multi-session/sidebar/ProjectCreateMenu';
import { projectCreateErrorKey } from '../../multi-session/sidebar/projectCreateErrors';
import { AGENT_MODE_OPTIONS, PERMISSION_OPTIONS } from '../../config/chatConfig';
import clsx from 'clsx';
import { PermissionWarningDialog } from './PermissionWarningDialog';
import { ModelProviderIcon } from '../ModelProviderIcon';
import { getEvolutionPillLabel } from './evolution-status';
import { webRequest } from '../../services/webClient';
import sendIcon from '../../assets/send.svg';
import sendActiveIcon from '../../assets/send_active.svg';
import chatSkillIcon from '../../assets/skillIcon.svg';
import configIcon from '../../assets/sidebar/config.svg';
import workAddIcon from '../../assets/work-mode/add-project.svg';
import workArrowRightIcon from '../../assets/work-mode/arrow-right.svg';
import workCheckIcon from '../../assets/work-mode/check.svg';
import workCloseIcon from '../../assets/work-mode/close.svg';
import workFolderIcon from '../../assets/work-mode/folder.svg';
import workCollapseIcon from '../../assets/work-mode/collapse.svg';
import workExpandIcon from '../../assets/work-mode/expand.svg';
import workSearchIcon from '../../assets/work-mode/search.svg';

/** 输入栏下拉所需的最小技能数据结构（与 SkillPanel 中的 SkillItem 保持一致） */
type InputAreaSkillItem = {
  name: string;
  description: string;
  source: string;
  is_builtin?: boolean;
  is_builtin_source?: boolean;
  enabled?: boolean;
};

/** 已安装插件信息（用于判定技能是否已安装） */
type InputAreaInstalledPlugin = {
  plugin_name: string;
  marketplace: string;
  spec: string;
  version: string;
  installed_at: string;
  git_commit?: string | null;
  skills: string[];
};

function getProjectLabel(project: ProjectInfo | null, fallback: string): string {
  return project ? project.name : fallback;
}

interface InputAreaProps {
  onSubmit: (content: string) => void;
  onInterrupt: (newInput?: string) => void;
  onCancel: () => void;
  onSwitchMode: (mode: AgentMode) => void;
  isProcessing: boolean;
  autoFocusKey?: string | null;
  /** 跳转到技能管理页 */
  onNavigateToSkills?: () => void;
  permissionsEnabled: boolean;
  onSavePermission: (updates: Record<string, string>) => Promise<void>;
}


export function InputArea({
  onSubmit,
  onInterrupt,
  onCancel,
  onSwitchMode,
  isProcessing,
  autoFocusKey = null,
  onNavigateToSkills,
  permissionsEnabled,
  onSavePermission,
}: InputAreaProps) {
  const [pendingVoiceText, setPendingVoiceText] = useState('');
  const [isModeMenuOpen, setIsModeMenuOpen] = useState(false);
  const [workMenuOpen, setWorkMenuOpen] = useState<'project' | null>(null);
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [projectNameDraft, setProjectNameDraft] = useState('');
  const [projectPathDraft, setProjectPathDraft] = useState('');
  const [projectPathError, setProjectPathError] = useState<string | null>(null);
  const [projectSearch, setProjectSearch] = useState('');
  const [projectCreateMode, setProjectCreateMode] = useState<ProjectCreateMode>('blank');
  const [menuDirection, setMenuDirection] = useState<'up' | 'down'>('up');
  const [hoveredOptionDesc, setHoveredOptionDesc] = useState<string | null>(null);
  const [modeMenuAnchor, setModeMenuAnchor] = useState<DOMRect | null>(null);
  const inputRef = useRef<HTMLDivElement>(null);
  /** 保存技能插入前的光标位置，用于在光标处插入 chip */
  const savedRangeRef = useRef<Range | null>(null);
  const modeMenuRef = useRef<HTMLDivElement>(null);
  const workMenuRef = useRef<HTMLDivElement>(null);
  const modeMenuPortalRef = useRef<HTMLDivElement>(null);
  const autoSendTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isComposingRef = useRef(false);
  // const activePointerIdRef = useRef<number | null>(null);
  const isVoicePressingRef = useRef(false);
  const { t } = useTranslation();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const isPaused = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.isPaused ?? false);
  const queuePaused = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.queuePaused ?? false);
  const inputValue = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.inputValue ?? '');
  const evolutionStatus = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.evolutionStatus ?? null);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent.plan');
  const currentSession = useSessionStore((s) => s.currentSession);
  const activeSession = useSessionStore((s) => {
    if (!activeSessionId || activeSessionId === NEW_CONVERSATION_ID) return null;
    if (s.currentSession?.session_id === activeSessionId) return s.currentSession;
    return s.sessions.find((session) => session.session_id === activeSessionId) ?? null;
  });
  const {
    projects,
    selectedProject,
    setSelectedProject,
    createProject,
  } = useWorkspaceStore();
  const loadedMsgLen = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages?.length ?? 0);
  const hasHistory = (currentSession?.message_count ?? 0) > 0 || loadedMsgLen > 0;
  const isInterruptible = isProcessing || isPaused;
  const isAgentMode = mode === 'agent.fast' || mode === 'agent.plan';
  const isTeamMode = mode === 'team';
  const isAutoHarnessMode = mode === 'auto_harness';
  const isWorkContextLocked = Boolean(activeSessionId && activeSessionId !== NEW_CONVERSATION_ID);
  const showWorkContextRow = activeSessionId === NEW_CONVERSATION_ID;

  const filteredProjects = useMemo(() => {
    const keyword = projectSearch.trim().toLowerCase();
    if (!keyword) return projects;
    return projects.filter((project) => (
      project.name.toLowerCase().includes(keyword)
      || project.project_path.toLowerCase().includes(keyword)
    ));
  }, [projectSearch, projects]);

  const displayedProject = useMemo<ProjectInfo | null>(() => {
    if (activeSession?.project_path) {
      const matched = projects.find((project) => project.project_path === activeSession.project_path);
      if (matched) return matched;
      const path = activeSession.project_path || '';
      return {
        project_id: path || 'default',
        project_path: path,
        name: path.split('/').filter(Boolean).pop() || t('multiSession.project.projects'),
        pinned: false,
        pin_order: 0,
        is_default: path === '',
        hidden: false,
        session_count: 0,
        last_message_at: null,
        last_user_message_at: null,
        created_at: 0,
      };
    }
    return selectedProject;
  }, [activeSession, projects, selectedProject]);

  const {
    isListening,
    // startListening,
    stopListening,
    // isSupported: speechSupported,
  } = useSpeechRecognition({
    language: 'cmn-Hans-CN',
    continuous: true,
    interimResults: true,
    silenceTimeoutMs: 8000,
    restartWhen: () => isVoicePressingRef.current,
    onResult: (text, isFinal) => {
      if (isFinal) {
        setPendingVoiceText((prev) => prev + text);
      }
    },
    onEnd: () => {
      autoSendTimeoutRef.current = setTimeout(() => {}, 100);
    },
    onError: (error) => {
      console.error('语音识别错误:', error);
    },
  });

  useEffect(() => {
    if (!isListening && pendingVoiceText) {
      const finalText = (inputValue + pendingVoiceText).trim();
      if (finalText) {
        const sid = useChatStore.getState().activeSessionId;
        if (sid) {
          useChatStore.getState().setInputValue(sid, finalText);
        }
        setPendingVoiceText('');

        setTimeout(() => {
          if (isTeamMode) {
            onSubmit(finalText);
          } else if (isInterruptible) {
            onInterrupt(finalText);
          } else {
            onSubmit(finalText);
          }
          if (sid) {
            useChatStore.getState().setInputValue(sid, '');
          }
        }, 150);
      }
    }
  }, [isListening, pendingVoiceText, inputValue, isInterruptible, isTeamMode, onSubmit, onInterrupt]);

  useEffect(() => {
    return () => {
      if (autoSendTimeoutRef.current) {
        clearTimeout(autoSendTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!isModeMenuOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (
        !modeMenuRef.current?.contains(event.target as Node) &&
        !modeMenuPortalRef.current?.contains(event.target as Node)
      ) {
        setIsModeMenuOpen(false);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [isModeMenuOpen]);

  useEffect(() => {
    if (!workMenuOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!workMenuRef.current?.contains(event.target as Node)) {
        setWorkMenuOpen(null);
      }
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setWorkMenuOpen(null);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [workMenuOpen]);

  useEffect(() => {
    if (autoFocusKey) {
      inputRef.current?.focus();
    }
  }, [autoFocusKey]);

  // 切会话时用 inputValue 填充 contenteditable（chip 位置丢失，仅恢复纯文本）
  useEffect(() => {
    if (!inputRef.current) return;
    const sid = useChatStore.getState().activeSessionId;
    if (!sid) return;
    const text = useChatStore.getState().runtimes[sid]?.inputValue ?? '';
    inputRef.current.textContent = text;
  }, [activeSessionId]);

  // 监听外部设置 inputValue 的事件（如编辑排队任务）
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { sessionId: string; value: string };
      const sid = useChatStore.getState().activeSessionId;
      if (detail.sessionId === sid && inputRef.current) {
        inputRef.current.textContent = detail.value;
        inputRef.current.focus();
        // 将光标移到末尾
        const range = document.createRange();
        range.selectNodeContents(inputRef.current);
        range.collapse(false);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
    };
    window.addEventListener('chat-input-sync', handler);
    return () => window.removeEventListener('chat-input-sync', handler);
  }, []);

  /** 从 contenteditable 提取纯文本（跳过 chip 节点，过滤零宽空格） */
  const extractPlainText = useCallback((): string => {
    const el = inputRef.current;
    if (!el) return '';
    let text = '';
    el.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent || '';
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const elem = node as HTMLElement;
        if (elem.getAttribute('contenteditable') === 'false') {
          // 跳过 chip
        } else {
          text += elem.textContent || '';
        }
      }
    });
    return text.replace(/\u200B/g, '');
  }, []);

  /** 从 contenteditable 提取富文本（chip 转成 {{skill:名称}} 标记，保留位置用于气泡交织渲染） */
  const extractRichContent = useCallback((): string => {
    const el = inputRef.current;
    if (!el) return '';
    let text = '';
    el.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent || '';
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const elem = node as HTMLElement;
        if (elem.getAttribute('contenteditable') === 'false' && elem.hasAttribute('data-skill')) {
          text += `{{skill:${elem.getAttribute('data-skill')}}}`;
        } else {
          text += elem.textContent || '';
        }
      }
    });
    return text.replace(/\u200B/g, '');
  }, []);

  const handleSubmit = useCallback(() => {
    // 用富文本（含 chip 标记）作为发送内容，气泡可交织渲染技能
    const richContent = extractRichContent();
    const trimmed = (richContent + pendingVoiceText).trim();
    if (!trimmed) return;

    if (isListening) {
      stopListening();
    }

    const sid = useChatStore.getState().activeSessionId;
    if (isTeamMode) {
      onSubmit(trimmed);
    } else if (queuePaused && isAgentMode && sid) {
      // 队列已暂停时，弹窗提示用户选择
      const queueLen = useChatStore.getState().getRuntime(sid)?.taskQueue.length ?? 0;
      const shouldClear = window.confirm(t('chat.queuePausedConfirm', { count: queueLen }));
      if (shouldClear) {
        // 清空队列并发送
        useChatStore.getState().clearTaskQueue(sid);
        useChatStore.getState().setQueuePaused(sid, false);
        onSubmit(trimmed);
      } else {
        // 保持队列，新消息加入队列
        useChatStore.getState().addToTaskQueue(sid, trimmed);
      }
    } else if (isInterruptible) {
      if (isAgentMode) {
        if (sid) {
          useChatStore.getState().addToTaskQueue(sid, trimmed);
        }
      } else {
        onInterrupt(trimmed);
      }
    } else {
      onSubmit(trimmed);
    }
    if (sid) {
      useChatStore.getState().setInputValue(sid, '');
    }
    setPendingVoiceText('');

    // 清空 contenteditable 内容
    if (inputRef.current) {
      inputRef.current.innerHTML = '';
    }
  }, [extractRichContent, pendingVoiceText, isInterruptible, isListening, onSubmit, onInterrupt, stopListening, isAgentMode, isTeamMode, queuePaused, t]);

  const trimmedDraft = (inputValue + pendingVoiceText).trim();
  const hasDraft = trimmedDraft.length > 0 || isListening;
  const showStop = isProcessing && !isPaused && !hasDraft;
  const canSubmit = hasDraft || showStop;

  const handleSendButtonClick = useCallback(() => {
    if (showStop) {
      onCancel();
      return;
    }

    handleSubmit();
  }, [handleSubmit, showStop, onCancel]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      if (isComposingRef.current || e.nativeEvent.isComposing) return;
      e.preventDefault();
      handleSubmit();
    },
    [handleSubmit]
  );

  /** contenteditable 输入时同步纯文本到 store + 联动 selectedSkills */
  const handleEditorInput = useCallback(() => {
    const sid = useChatStore.getState().activeSessionId;
    if (!sid) return;
    // 提取纯文本
    const text = extractPlainText();
    useChatStore.getState().setInputValue(sid, text);
    // 联动 selectedSkills：扫描 contenteditable 现有 chip，移除已不在的技能（backspace 删除等情况）
    const el = inputRef.current;
    if (el) {
      const existingSkills = new Set<string>();
      el.querySelectorAll('[data-skill]').forEach((chip) => {
        const name = chip.getAttribute('data-skill');
        if (name) existingSkills.add(name);
      });
      const store = useSessionStore.getState();
      const current = store.runtimes[sid]?.selectedSkills ?? [];
      current.forEach((skill) => {
        if (!existingSkills.has(skill)) {
          store.removeSelectedSkill(sid, skill);
        }
      });
    }
  }, [extractPlainText]);

  /** 保存当前光标位置（用于技能插入时定位） */
  const saveSelection = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    // 仅当光标在 contenteditable 内时保存
    if (inputRef.current && inputRef.current.contains(range.commonAncestorContainer)) {
      savedRangeRef.current = range.cloneRange();
    }
  }, []);

  /** 在光标处插入技能 chip（不可编辑原子节点） */
  const insertSkillChip = useCallback((skillName: string) => {
    const el = inputRef.current;
    if (!el) return;
    // 输入法合成中不插入
    if (isComposingRef.current) return;

    el.focus();
    const sel = window.getSelection();
    if (!sel) return;

    // 恢复保存的光标，否则用当前光标
    let range: Range;
    if (savedRangeRef.current && el.contains(savedRangeRef.current.commonAncestorContainer)) {
      range = savedRangeRef.current;
      sel.removeAllRanges();
      sel.addRange(range);
    } else if (sel.rangeCount > 0) {
      range = sel.getRangeAt(0);
    } else {
      // 无光标，追加到末尾
      range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
    }

    // 删除选中的内容（如有）
    range.deleteContents();

    // 创建 chip 节点
    const chip = document.createElement('span');
    chip.className = 'chat-input-chip-inline';
    chip.setAttribute('contenteditable', 'false');
    chip.setAttribute('data-skill', skillName);
    chip.innerHTML = `
      <span class="chat-input-chip-inline__icon" aria-hidden="true"></span>
      <span class="chat-input-chip-inline__label">${skillName}</span>
    `;
    // 删除按钮（覆盖在 icon 位置，悬浮时替换闪电）
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'chat-input-chip-inline__remove';
    removeBtn.setAttribute('aria-label', 'remove skill');
    removeBtn.innerHTML = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4"><path stroke-linecap="round" stroke-linejoin="round" d="M6 6l8 8M14 6l-8 8"/></svg>`;
    removeBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sid = useChatStore.getState().activeSessionId;
      // 从 DOM 移除 chip
      chip.remove();
      // 同步 selectedSkills
      if (sid) useSessionStore.getState().removeSelectedSkill(sid, skillName);
      // 同步纯文本
      if (sid) useChatStore.getState().setInputValue(sid, extractPlainText());
    });
    // 把 remove 按钮插入到 icon 容器内（覆盖闪电位置）
    const iconEl = chip.querySelector('.chat-input-chip-inline__icon');
    if (iconEl) {
      iconEl.appendChild(removeBtn);
    } else {
      chip.appendChild(removeBtn);
    }

    // 插入 chip
    range.insertNode(chip);

    // 在 chip 后插入零宽空格，方便光标定位
    const spacer = document.createTextNode('\u200B');
    chip.after(spacer);

    // 光标移到 spacer 后
    range.setStartAfter(spacer);
    range.setEndAfter(spacer);
    sel.removeAllRanges();
    sel.addRange(range);

    // 清除保存的光标
    savedRangeRef.current = null;

    // 同步纯文本到 store
    const sid = useChatStore.getState().activeSessionId;
    if (sid) useChatStore.getState().setInputValue(sid, extractPlainText());
  }, [extractPlainText]);

  /** 从 contenteditable 中移除指定技能的 chip 节点 */
  const removeSkillChip = useCallback((skillName: string) => {
    const el = inputRef.current;
    if (!el) return;
    const chips = el.querySelectorAll('[data-skill]');
    chips.forEach((chip) => {
      if (chip.getAttribute('data-skill') === skillName) {
        // 同时移除后面的零宽空格 spacer
        const next = chip.nextSibling;
        if (next && next.nodeType === Node.TEXT_NODE && next.textContent === '\u200B') {
          next.remove();
        }
        chip.remove();
      }
    });
    // 同步纯文本
    const sid = useChatStore.getState().activeSessionId;
    if (sid) useChatStore.getState().setInputValue(sid, extractPlainText());
  }, [extractPlainText]);

  // const handleVoiceStart = useCallback(() => {
  //   if (isListening) return;
  //   stopAllTts();
  //   startListening();
  // }, [isListening, startListening]);

  // const handleVoiceEnd = useCallback(() => {
  //   if (!isListening) return;
  //   stopListening();
  // }, [isListening, stopListening]);

  // const handleVoicePointerDown = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     // 仅响应主按钮按压，避免右键/多指导致状态抖动
  //     if (e.pointerType === 'mouse' && e.button !== 0) return;
  //     if (activePointerIdRef.current !== null) return;
  //     e.preventDefault();
  //     activePointerIdRef.current = e.pointerId;
  //     isVoicePressingRef.current = true;
  //     e.currentTarget.setPointerCapture(e.pointerId);
  //     handleVoiceStart();
  //   },
  //   [handleVoiceStart]
  // );

  // const handleVoicePointerUp = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     if (activePointerIdRef.current !== e.pointerId) return;
  //     e.preventDefault();
  //     activePointerIdRef.current = null;
  //     isVoicePressingRef.current = false;
  //     if (e.currentTarget.hasPointerCapture(e.pointerId)) {
  //       e.currentTarget.releasePointerCapture(e.pointerId);
  //     }
  //     handleVoiceEnd();
  //   },
  //   [handleVoiceEnd]
  // );

  // const handleVoicePointerCancel = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     if (activePointerIdRef.current !== e.pointerId) return;
  //     activePointerIdRef.current = null;
  //     isVoicePressingRef.current = false;
  //     if (e.currentTarget.hasPointerCapture(e.pointerId)) {
  //       e.currentTarget.releasePointerCapture(e.pointerId);
  //     }
  //     handleVoiceEnd();
  //   },
  //   [handleVoiceEnd]
  // );

  const handleModeSwitch = useCallback(async (targetMode: AgentMode) => {
    if (isProcessing || hasHistory || mode === targetMode) return;
    onSwitchMode(targetMode);
  }, [isProcessing, hasHistory, mode, onSwitchMode]);

  const handleModeSelect = useCallback(async (targetMode: AgentMode) => {
    setIsModeMenuOpen(false);
    await handleModeSwitch(targetMode);
  }, [handleModeSwitch]);

  useEffect(() => {
    setIsModeMenuOpen(false);
  }, [isProcessing, mode]);

  const handleAddProjectPath = useCallback(async () => {
    const name = projectNameDraft.trim();
    const projectPath = projectCreateMode === 'blank' ? '' : projectPathDraft.trim();
    if (!name || (projectCreateMode === 'existing' && !projectPath)) return;
    setProjectPathError(null);
    if (projectPath && (!projectPath.startsWith('/') || projectPath.startsWith('~/'))) {
      setProjectPathError(t('multiSession.project.absolutePathError'));
      return;
    }
    try {
      await createProject(name, projectPath);
      setProjectNameDraft('');
      setProjectPathDraft('');
      setWorkDialogOpen(false);
    } catch (error) {
      const errorKey = projectCreateErrorKey(error);
      setProjectPathError(errorKey ? t(errorKey) : error instanceof Error ? error.message : String(error));
    }
  }, [createProject, projectCreateMode, projectNameDraft, projectPathDraft, t]);

  const currentMode = AGENT_MODE_OPTIONS.find((item) => item.value === mode) ?? AGENT_MODE_OPTIONS[0];
  const evolutionLabel = getEvolutionPillLabel(mode, evolutionStatus, t);

  return (
    <div
      className={cx(
        'chat-input-container',
        showWorkContextRow && 'chat-input-container--work-home',
        (isModeMenuOpen || workMenuOpen) && 'chat-input-container--menu-open',
        isListening && 'chat-input-container--recording',
      )}
    >
      {isListening && (
        <div className="chat-input-recording-bar">
          <span className="chat-input-recording-dot" />
          <span>{t('chat.recording')}</span>
        </div>
      )}

      <div
        ref={inputRef}
        contentEditable
        suppressContentEditableWarning
        onInput={handleEditorInput}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => { isComposingRef.current = true; }}
        onCompositionEnd={() => { isComposingRef.current = false; }}
        onBlur={saveSelection}
        data-placeholder={
          isListening
            ? t('chat.placeholderVoice')
            : isTeamMode
              ? isInterruptible && !isPaused
              ? t('chat.placeholderTeamModeProcessing')
              : t('chat.placeholderTeamMode')
              : isAutoHarnessMode
                ? t('autoHarness.inputPlaceholder')
                : isAgentMode && isInterruptible
                  ? t('chat.placeholderProcessingQueue')
                  : isInterruptible
                    ? t('chat.placeholderProcessing')
                    : t('chat.placeholder')
        }
        className="chat-input-editor"
        data-testid="chat-input"
      />

      <div className="chat-input-toolbar">
        <div className="chat-input-toolbar-left">
          <div
            ref={modeMenuRef}
            className={clsx(
              'chat-mode-select',
              isModeMenuOpen && 'chat-mode-select--open',
            )}
          >
            <button
              type="button"
              className="chat-mode-select__trigger"
              onClick={() => {
                if (hasHistory || isProcessing) return;
                if (!isModeMenuOpen && modeMenuRef.current) {
                  const rect = modeMenuRef.current.getBoundingClientRect();
                  const spaceBelow = window.innerHeight - rect.bottom;
                  const dir = spaceBelow >= 120 ? 'down' : 'up';
                  setMenuDirection(dir);
                  setModeMenuAnchor(rect);
                }
                setIsModeMenuOpen((open) => !open);
              }}
              aria-haspopup="menu"
              aria-expanded={isModeMenuOpen}
              data-testid={`chat-mode-${currentMode.value}`}
              style={(hasHistory || isProcessing) ? { cursor: 'default' } : undefined}
            >
              <span className="chat-mode-select__value">
                <span className="chat-mode-select__icon" aria-hidden="true">
                  <currentMode.icon className="w-4 h-4" />
                </span>
                <span className="chat-mode-select__label">{t(currentMode.i18nKey)}</span>
              </span>
              {!hasHistory && !isProcessing && (
                <svg className="chat-mode-select__chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
                </svg>
              )}
            </button>

            {isModeMenuOpen && modeMenuAnchor && createPortal(
              <div
                ref={modeMenuPortalRef}
                className="chat-mode-select__menu"
                role="menu"
                style={menuDirection === 'up'
                  ? { position: 'fixed', bottom: window.innerHeight - modeMenuAnchor.top + 10, left: modeMenuAnchor.left, zIndex: 9999 }
                  : { position: 'fixed', top: modeMenuAnchor.bottom + 10, left: modeMenuAnchor.left, zIndex: 9999 }
                }
              >
                {AGENT_MODE_OPTIONS.map((m) => (
                  <button
                    type="button"
                    key={m.value}
                    onClick={() => void handleModeSelect(m.value)}
                    onMouseEnter={() => setHoveredOptionDesc(m.descriptionI18nKey ?? null)}
                    onMouseLeave={() => setHoveredOptionDesc(null)}
                    className={clsx(
                      'chat-mode-select__option',
                      mode === m.value && 'chat-mode-select__option--active',
                    )}
                    role="menuitemradio"
                    aria-checked={mode === m.value}
                    data-testid={`chat-mode-option-${m.value}`}
                  >
                    <span className="chat-mode-select__option-main">
                      <span className="chat-mode-select__icon" aria-hidden="true">
                        <m.icon className="w-4 h-4" />
                      </span>
                      <span className="chat-mode-select__label">{t(m.i18nKey)}</span>
                    </span>
                    {mode === m.value && (
                      <svg className="chat-mode-select__check" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>,
              document.body
            )}
            {isModeMenuOpen && hoveredOptionDesc && modeMenuAnchor && createPortal(
              <div
                className="chat-mode-option-tooltip"
                style={menuDirection === 'up'
                  ? { position: 'fixed', bottom: window.innerHeight - modeMenuAnchor.top + 10, left: modeMenuAnchor.left + 188, zIndex: 10000 }
                  : { position: 'fixed', top: modeMenuAnchor.bottom + 10, left: modeMenuAnchor.left + 188, zIndex: 10000 }
                }
              >
                {t(hoveredOptionDesc)}
              </div>,
              document.body
            )}
          </div>
          <PermissionSelector permissionsEnabled={permissionsEnabled} onSavePermission={onSavePermission} />

          {!isTeamMode && <SkillSelector
            onNavigateToSkills={onNavigateToSkills}
            onInsertSkill={insertSkillChip}
            onRemoveSkill={removeSkillChip}
          />}

          {evolutionLabel && (
            <div className="chat-input-evolution-pill" title={evolutionLabel}>
              <span className="chat-input-evolution-pill__dot" />
              <span className="chat-input-evolution-pill__label">{evolutionLabel}</span>
            </div>
          )}
        </div>

        <div className="chat-input-actions">
          {/* {speechSupported && (
            <button
              type="button"
              onPointerDown={handleVoicePointerDown}
              onPointerUp={handleVoicePointerUp}
              onPointerCancel={handleVoicePointerCancel}
              className={cx(
                'chat-input-btn',
                isListening && 'chat-input-btn--recording',
              )}
              title={t('chat.holdToSpeak')}
            >
              {isListening ? (
                <svg className="chat-input-btn-icon" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              ) : (
                <svg className="chat-input-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
                </svg>
              )}
            </button>
          )} */}

          <ModelSelector disabled={hasHistory || isProcessing} />

          <button
            type="button"
            onClick={handleSendButtonClick}
            disabled={!canSubmit}
            className={cx(
              'chat-input-btn chat-input-btn--send',
              showStop && 'chat-input-btn--stop',
              canSubmit ? 'chat-input-btn--send-active' : 'chat-input-btn--disabled',
            )}
            title={showStop ? t('chat.stop') : t('chat.send')}
            data-testid="chat-send"
          >
            {showStop ? (
              <Square className="chat-input-btn-icon" fill="currentColor" strokeWidth={1.8} aria-hidden="true" />
            ) : (
              <img
                className="chat-input-btn-icon chat-input-btn-icon--image"
                src={canSubmit ? sendActiveIcon : sendIcon}
                alt=""
                aria-hidden="true"
              />
            )}
          </button>
        </div>
      </div>

      {showWorkContextRow ? (
        <div ref={workMenuRef} className="chat-work-context-row">
          <div className={clsx('chat-work-select', workMenuOpen === 'project' && 'chat-work-select--open')}>
            <button
              type="button"
              className={clsx('chat-work-select__trigger', displayedProject && 'chat-work-select__trigger--selected')}
              onClick={() => !isWorkContextLocked && setWorkMenuOpen((open) => open === 'project' ? null : 'project')}
              disabled={isWorkContextLocked}
              title={displayedProject?.project_path || (isWorkContextLocked ? t('multiSession.project.lockedProjectTitle') : t('multiSession.project.chooseProjectDirectory'))}
            >
              <img className="chat-work-select__root-icon" src={workFolderIcon} alt="" aria-hidden="true" />
              <span>{getProjectLabel(displayedProject, t('multiSession.project.chooseProjectDirectory'))}</span>
              <img
                className="chat-work-select__chevron"
                src={workMenuOpen === 'project' ? workCollapseIcon : workExpandIcon}
                alt=""
                aria-hidden="true"
              />
            </button>
            {displayedProject && !isWorkContextLocked ? (
              <span className="chat-work-select__clear-wrap" aria-hidden="false">
                <span className="chat-work-select__clear-label">{t('multiSession.project.clearProject')}</span>
                <button
                  type="button"
                  className="chat-work-select__clear"
                  aria-label={t('multiSession.project.clearProject')}
                  onClick={() => {
                    setSelectedProject(null);
                    setWorkMenuOpen(null);
                  }}
                >
                  <img src={workCloseIcon} alt="" aria-hidden="true" />
                </button>
              </span>
            ) : null}
            {workMenuOpen === 'project' && !isWorkContextLocked ? (
              <div className={clsx('chat-work-select__menu', projects.length > 0 && 'chat-work-select__menu--projects')} role="menu">
                {projects.length === 0 ? (
                  <ProjectCreateMenu
                    onCreate={(mode) => {
                      setProjectCreateMode(mode);
                      setWorkDialogOpen(true);
                      setWorkMenuOpen(null);
                    }}
                    itemClassName="chat-work-select__option chat-work-select__option--compact"
                    blankIcon={<img src={workAddIcon} alt="" aria-hidden="true" />}
                    existingIcon={<img src={workFolderIcon} alt="" aria-hidden="true" />}
                  />
                ) : (
                  <>
                    <label className="chat-work-select__search-wrap">
                      <img src={workSearchIcon} alt="" aria-hidden="true" />
                      <input
                        className="chat-work-select__search"
                        value={projectSearch}
                        onChange={(event) => setProjectSearch(event.target.value)}
                        placeholder={t('multiSession.project.searchProject')}
                      />
                    </label>
                    {filteredProjects.map((project) => {
                      const active = selectedProject?.project_id === project.project_id;
                      return (
                        <button
                          type="button"
                          key={project.project_id}
                          className={clsx('chat-work-select__option', active && 'is-active')}
                          onClick={() => {
                            setSelectedProject(project);
                            setWorkMenuOpen(null);
                          }}
                          role="menuitemradio"
                          aria-checked={active}
                          title={project.project_path}
                        >
                          <img src={workFolderIcon} alt="" aria-hidden="true" />
                          <span>{project.name}</span>
                          {active ? <img className="chat-work-select__check" src={workCheckIcon} alt="" aria-hidden="true" /> : null}
                        </button>
                      );
                    })}
                    {filteredProjects.length === 0 ? (
                      <div className="chat-work-select__empty">{t('multiSession.project.noProjectMatches')}</div>
                    ) : null}
                    <ProjectAddSubmenu
                      onCreate={(mode) => {
                        setProjectCreateMode(mode);
                        setWorkDialogOpen(true);
                        setWorkMenuOpen(null);
                      }}
                    />
                  </>
                )}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {workDialogOpen ? (
        <div className="chat-work-dialog-backdrop" role="presentation">
          <form
            className="chat-work-dialog"
            onSubmit={(event) => {
              event.preventDefault();
              void handleAddProjectPath();
            }}
          >
            <button
              type="button"
              className="chat-work-dialog__close"
              aria-label={t('common.close')}
              onClick={() => {
                setProjectPathDraft('');
                setProjectNameDraft('');
                setProjectPathError(null);
                setWorkDialogOpen(false);
              }}
            >
              <img src={workCloseIcon} alt="" aria-hidden="true" />
            </button>
            <div className="chat-work-dialog__title">{t('multiSession.project.newProject')}</div>
            <input
              className="chat-work-dialog__input"
              value={projectNameDraft}
              onChange={(event) => setProjectNameDraft(event.target.value)}
              placeholder={t('multiSession.project.namePlaceholder')}
              autoFocus
            />
            {projectCreateMode === 'existing' ? (
              <input
                className="chat-work-dialog__input"
                value={projectPathDraft}
                onChange={(event) => setProjectPathDraft(event.target.value)}
                placeholder="/Users/name/work/project"
              />
            ) : null}
            <div className="chat-work-dialog__actions">
              <button
                type="button"
                onClick={() => {
                  setProjectPathDraft('');
                  setProjectNameDraft('');
                  setProjectPathError(null);
                  setWorkDialogOpen(false);
                }}
              >
                {t('multiSession.project.cancel')}
              </button>
              <button
                type="submit"
                disabled={!projectNameDraft.trim() || (projectCreateMode === 'existing' && !projectPathDraft.trim())}
              >
                {t('multiSession.project.confirm')}
              </button>
            </div>
            {projectPathError ? <div className="chat-work-dialog__error">{projectPathError}</div> : null}
          </form>
        </div>
      ) : null}
    </div>
  );
}

function ProjectAddSubmenu({ onCreate }: { onCreate: (mode: ProjectCreateMode) => void }) {
  const { t } = useTranslation();
  return (
    <div className="chat-work-select__add" role="none">
      <button
        type="button"
        className="chat-work-select__option chat-work-select__option--compact"
        role="menuitem"
        aria-haspopup="menu"
      >
        <img src={workAddIcon} alt="" aria-hidden="true" />
        <span>{t('multiSession.project.addNewProject')}</span>
        <img className="chat-work-select__arrow" src={workArrowRightIcon} alt="" aria-hidden="true" />
      </button>
      <div className="chat-work-select__submenu" role="menu">
        <ProjectCreateMenu
          onCreate={onCreate}
          itemClassName="chat-work-select__option chat-work-select__option--compact"
          blankIcon={<img src={workAddIcon} alt="" aria-hidden="true" />}
          existingIcon={<img src={workFolderIcon} alt="" aria-hidden="true" />}
        />
      </div>
    </div>
  );
}

function ModelSelector({ disabled = false }: { disabled?: boolean }) {
  const chatAvailableModels = useSessionStore((s) => s.chatAvailableModels);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const selectedModelName = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.selectedModelName ?? null);
  const setSelectedModelName = useSessionStore((s) => s.setSelectedModelName);
  const { t } = useTranslation();

  const [isOpen, setIsOpen] = useState(false);
  const [menuDirection, setMenuDirection] = useState<'up' | 'down'>('up');
  const [menuAnchor, setMenuAnchor] = useState<DOMRect | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuPortalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: PointerEvent) => {
      if (
        !menuRef.current?.contains(e.target as Node) &&
        !menuPortalRef.current?.contains(e.target as Node)
      ) setIsOpen(false);
    };
    document.addEventListener('pointerdown', handler);
    return () => document.removeEventListener('pointerdown', handler);
  }, [isOpen]);

  if (chatAvailableModels.length === 0) return null;

  const selectedModel =
    chatAvailableModels.find((m) => (m.alias || m.model_name) === selectedModelName) ??
    chatAvailableModels[0];

  const handleSelect = (modelKey: string) => {
    setIsOpen(false);
    if (activeSessionId) setSelectedModelName(activeSessionId, modelKey);
  };

  const handleAddModel = () => {
    setIsOpen(false);
    window.dispatchEvent(new CustomEvent<string>('jiuwen:nav', { detail: 'configpanel' }));
  };

  return (
    <div
      ref={menuRef}
      className={clsx('chat-mode-select', isOpen && 'chat-mode-select--open')}
    >
      <button
        type="button"
        className="chat-mode-select__trigger"
        title={t('chat.modelSelector.tooltip')}
        onClick={() => {
          if (disabled) return;
          if (!isOpen && menuRef.current) {
            const rect = menuRef.current.getBoundingClientRect();
            setMenuDirection(window.innerHeight - rect.bottom >= 200 ? 'down' : 'up');
            setMenuAnchor(rect);
          }
          setIsOpen((v) => !v);
        }}
        style={disabled ? { cursor: 'default' } : undefined}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        data-testid="chat-model-selector"
      >
        <span className="chat-mode-select__value">
          <span className="chat-mode-select__icon" aria-hidden="true">
            <ModelProviderIcon model={selectedModel} />
          </span>
          <span className="chat-mode-select__label">
            {selectedModel.alias || selectedModel.model_name}
          </span>
        </span>
        {!disabled && (
          <svg className="chat-mode-select__chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
          </svg>
        )}
      </button>

      {isOpen && menuAnchor && createPortal(
        <div
          ref={menuPortalRef}
          className="chat-mode-select__menu model-select__menu"
          role="menu"
          style={menuDirection === 'up'
            ? { position: 'fixed', bottom: window.innerHeight - menuAnchor.top + 10, left: menuAnchor.left, zIndex: 9999 }
            : { position: 'fixed', top: menuAnchor.bottom + 10, left: menuAnchor.left, zIndex: 9999 }
          }
        >
          <div className="model-select__section-header">{t('chat.modelSelector.configured')}</div>
          {chatAvailableModels.map((m, idx) => {
            const key = m.alias || m.model_name;
            const isActive = key === (selectedModel.alias || selectedModel.model_name);
            return (
              <button
                type="button"
                key={`${m.model_name}-${idx}`}
                onClick={() => handleSelect(key)}
                className={clsx(
                  'chat-mode-select__option',
                  isActive && 'chat-mode-select__option--active',
                )}
                role="menuitemradio"
                aria-checked={isActive}
              >
                <span className="chat-mode-select__option-main">
                  <span className="chat-mode-select__icon" aria-hidden="true">
                    <ModelProviderIcon model={m} />
                  </span>
                  <span className="chat-mode-select__label">{key}</span>
                </span>
                {isActive && (
                  <svg className="chat-mode-select__check" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                  </svg>
                )}
              </button>
            );
          })}
          <button
            type="button"
            className="model-select__add-btn"
            onClick={handleAddModel}
          >
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} width={14} height={14} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 4v12M4 10h12" />
            </svg>
            {t('chat.modelSelector.addModel')}
          </button>
        </div>,
        document.body
      )}
    </div>
  );
}

function PermissionSelector({
  disabled = false,
  permissionsEnabled,
  onSavePermission,
}: {
  disabled?: boolean;
  permissionsEnabled: boolean;
  onSavePermission: (updates: Record<string, string>) => Promise<void>;
}) {
  const { t } = useTranslation();

  const permission: Permission = permissionsEnabled ? 'default' : 'full_access';

  const [isOpen, setIsOpen] = useState(false);
  const [menuDirection, setMenuDirection] = useState<'up' | 'down'>('up');
  const [menuAnchor, setMenuAnchor] = useState<DOMRect | null>(null);
  const [pendingPermission, setPendingPermission] = useState<Permission | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuPortalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: PointerEvent) => {
      if (
        !menuRef.current?.contains(e.target as Node) &&
        !menuPortalRef.current?.contains(e.target as Node)
      ) setIsOpen(false);
    };
    document.addEventListener('pointerdown', handler);
    return () => document.removeEventListener('pointerdown', handler);
  }, [isOpen]);

  const handleSelect = useCallback((value: Permission) => {
    setIsOpen(false);
    if (value === permission) return;
    if (value === 'full_access') {
      setPendingPermission('full_access');
    } else {
      onSavePermission({ permissions_enabled: 'true' });
    }
  }, [permission, onSavePermission]);

  const handleConfirm = useCallback(() => {
    if (pendingPermission) {
      onSavePermission({ permissions_enabled: 'false' });
    }
    setPendingPermission(null);
  }, [pendingPermission, onSavePermission]);

  const currentPerm = PERMISSION_OPTIONS.find((o) => o.value === permission) ?? PERMISSION_OPTIONS[0];

  return (
    <>
      <div
        ref={menuRef}
        className={clsx('chat-mode-select', isOpen && 'chat-mode-select--open')}
      >
        <button
          type="button"
          className={clsx(
            'chat-mode-select__trigger',
            permission === 'full_access' && !disabled && 'chat-mode-select__trigger--danger',
          )}
          disabled={disabled}
          title={disabled ? t('chat.configLockedHistory') : undefined}
          onClick={() => {
            if (disabled) return;
            if (!isOpen && menuRef.current) {
              const rect = menuRef.current.getBoundingClientRect();
              setMenuDirection(window.innerHeight - rect.bottom >= 160 ? 'down' : 'up');
              setMenuAnchor(rect);
            }
            setIsOpen((v) => !v);
          }}
          aria-haspopup="menu"
          aria-expanded={isOpen}
        >
          <span className="chat-mode-select__value">
            <span className="chat-mode-select__icon" aria-hidden="true">
              <currentPerm.icon className="w-4 h-4" />
            </span>
            <span className="chat-mode-select__label">{t(currentPerm.i18nKey)}</span>
          </span>
          <svg className="chat-mode-select__chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
          </svg>
        </button>

        {isOpen && menuAnchor && createPortal(
          <div
            ref={menuPortalRef}
            className="chat-mode-select__menu perm-select__menu"
            role="menu"
            style={menuDirection === 'up'
              ? { position: 'fixed', bottom: window.innerHeight - menuAnchor.top + 10, left: menuAnchor.left, zIndex: 9999 }
              : { position: 'fixed', top: menuAnchor.bottom + 10, left: menuAnchor.left, zIndex: 9999 }
            }
          >
            {PERMISSION_OPTIONS.map((opt) => (
              <button
                type="button"
                key={opt.value}
                onClick={() => handleSelect(opt.value)}
                className={clsx(
                  'chat-mode-select__option',
                  'perm-select__option',
                  permission === opt.value && 'chat-mode-select__option--active',
                )}
                role="menuitemradio"
                aria-checked={permission === opt.value}
              >
                <span className="perm-select__option-main">
                  <span className="chat-mode-select__icon" aria-hidden="true">
                    <opt.icon className="w-4 h-4" />
                  </span>
                  <span className="perm-select__text">
                    <span className="chat-mode-select__label">{t(opt.i18nKey)}</span>
                    {opt.descriptionI18nKey && (
                      <span className="perm-select__desc">{t(opt.descriptionI18nKey)}</span>
                    )}
                  </span>
                </span>
                {permission === opt.value && (
                  <svg className="chat-mode-select__check" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                  </svg>
                )}
              </button>
            ))}
          </div>,
          document.body
        )}
      </div>

      {pendingPermission === 'full_access' && (
        <PermissionWarningDialog
          onConfirm={handleConfirm}
          onCancel={() => setPendingPermission(null)}
        />
      )}
    </>
  );
}

/** 输入栏右侧的「技能」下拉，展示已安装技能（结构与技能页卡片保持一致） */
function SkillSelector({ onNavigateToSkills, onInsertSkill, onRemoveSkill }: {
  onNavigateToSkills?: () => void;
  onInsertSkill?: (skillName: string) => void;
  onRemoveSkill?: (skillName: string) => void;
}) {
  const { t } = useTranslation();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const selectedSkills = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.selectedSkills ?? []);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [skills, setSkills] = useState<InputAreaSkillItem[]>([]);
  const [plugins, setPlugins] = useState<InputAreaInstalledPlugin[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const menuRef = useRef<HTMLDivElement>(null);

  const avatarColors = [
    'bg-blue-500',
    'bg-indigo-500',
    'bg-violet-500',
    'bg-purple-500',
    'bg-fuchsia-500',
    'bg-pink-500',
    'bg-rose-500',
  ];

  const getSkillAvatar = (name: string) => {
    const firstChar = name.charAt(0).toUpperCase();
    const colorIndex = name.charCodeAt(0) % avatarColors.length;
    return { firstChar, color: avatarColors[colorIndex] };
  };

  const installedSkillMap = useMemo(() => {
    const map = new Map<string, InputAreaInstalledPlugin>();
    plugins.forEach((plugin) => {
      plugin.skills.forEach((skillName) => {
        if (!map.has(skillName)) map.set(skillName, plugin);
      });
    });
    return map;
  }, [plugins]);

  const isSkillInstalled = useCallback(
    (skill: InputAreaSkillItem): boolean =>
      installedSkillMap.has(skill.name) ||
      skill.source === 'local' ||
      skill.source === 'project',
    [installedSkillMap],
  );

  const installedSkills = useMemo(
    () => skills.filter(isSkillInstalled),
    [skills, isSkillInstalled],
  );

  // 按名称/描述过滤
  const filteredSkills = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return installedSkills;
    return installedSkills.filter((s) => {
      const name = s.name.toLowerCase();
      const desc = (s.description || '').toLowerCase();
      return name.includes(q) || desc.includes(q);
    });
  }, [installedSkills, searchQuery]);

  const fetchInstalledSkills = useCallback(async () => {
    if (!activeSessionId) return;
    setLoading(true);
    setErrorMessage(null);
    try {
      const data = await webRequest<{
        skills?: InputAreaSkillItem[];
        plugins?: InputAreaInstalledPlugin[];
      }>(
        'skills.list',
        { with_installed: true },
        { timeoutMs: 30_000 },
      );
      setSkills(data.skills || []);
      setPlugins(data.plugins || []);
    } catch (err) {
      console.error('Failed to load installed skills:', err);
      setErrorMessage(t('skills.listError'));
    } finally {
      setLoading(false);
    }
  }, [activeSessionId, t]);

  useEffect(() => {
    if (isOpen) {
      void fetchInstalledSkills();
    } else {
      // 关闭时清空搜索词
      setSearchQuery('');
    }
  }, [isOpen, fetchInstalledSkills]);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!isOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen]);

  const handleOpenSkillsPage = useCallback(() => {
    setIsOpen(false);
    onNavigateToSkills?.();
  }, [onNavigateToSkills]);

  // 点击技能项：已选则移除，未选则追加；保持下拉开启，便于多选
  const handleToggleSkill = useCallback((skillName: string) => {
    const sid = useChatStore.getState().activeSessionId;
    if (!sid) return;
    const store = useSessionStore.getState();
    if (selectedSkills.includes(skillName)) {
      store.removeSelectedSkill(sid, skillName);
      onRemoveSkill?.(skillName);
    } else {
      store.addSelectedSkill(sid, skillName);
      onInsertSkill?.(skillName);
    }
  }, [selectedSkills, onInsertSkill, onRemoveSkill]);

  return (
    <div
      ref={menuRef}
      className={clsx('chat-skill-select', isOpen && 'chat-skill-select--open')}
    >
      <button
        type="button"
        className="chat-skill-select__trigger"
        onClick={() => setIsOpen((open) => !open)}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        title={t('chat.skillsToggle')}
        data-testid="chat-skills-trigger"
      >
        <span className="chat-mode-select__value">
          <span className="chat-mode-select__icon" aria-hidden="true">
            <img src={chatSkillIcon} alt="" className="w-[14px] h-[14px]" />
          </span>
          <span className="chat-mode-select__label">{t('chat.skills')}</span>
        </span>
        <svg className="chat-mode-select__chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
        </svg>
      </button>

      {isOpen && (
        <div className="chat-skill-select__menu" role="menu">
          {/* 顶部搜索框 */}
          <div className="chat-skill-select__search">
            <svg className="chat-skill-select__search-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM17.5 17.5l-3.7-3.7" />
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('chat.skillsSearchPlaceholder')}
              className="chat-skill-select__search-input"
              data-testid="chat-skills-search"
            />
          </div>

          {loading && (
            <div className="chat-skill-select__state">{t('skills.detailLoading')}</div>
          )}
          {!loading && errorMessage && (
            <div className="chat-skill-select__state">{errorMessage}</div>
          )}
          {!loading && !errorMessage && installedSkills.length === 0 && (
            <div className="chat-skill-select__state">{t('chat.noInstalledSkills')}</div>
          )}
          {!loading && !errorMessage && installedSkills.length > 0 && filteredSkills.length === 0 && (
            <div className="chat-skill-select__state">{t('skills.noMatches')}</div>
          )}
          {!loading && !errorMessage && filteredSkills.length > 0 && (
            <>
              <div className="chat-skill-select__list">
                {filteredSkills.map((skill) => {
                  const avatar = getSkillAvatar(skill.name);
                  const isSelected = selectedSkills.includes(skill.name);
                  return (
                    <button
                      type="button"
                      key={skill.name}
                      onClick={() => handleToggleSkill(skill.name)}
                      className={clsx(
                        'chat-skill-select__item',
                        isSelected && 'chat-skill-select__item--selected',
                      )}
                      aria-pressed={isSelected}
                      title={isSelected ? t('chat.skillsRemove') : t('chat.skillsAdd')}
                    >
                      <div className={`chat-skill-select__avatar ${avatar.color}`}>
                        {avatar.firstChar}
                      </div>
                      <div className="chat-skill-select__item-main">
                        <div className="chat-skill-select__item-name">{skill.name}</div>
                        <div className="chat-skill-select__item-desc">
                          {skill.description || t('skills.noDescription')}
                        </div>
                      </div>
                      {isSelected && (
                        <svg className="chat-skill-select__item-check" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                        </svg>
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {/* 底部「技能管理」入口 */}
          <div className="chat-skill-select__footer">
            <button
              type="button"
              onClick={handleOpenSkillsPage}
              className="chat-skill-select__manage-btn"
              data-testid="chat-skills-manage"
            >
              <img src={configIcon} alt="" className="chat-skill-select__manage-icon" />
              <span>{t('chat.skillsManage')}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function cx(...classes: (string | boolean | undefined | null)[]) {
  return classes.filter(Boolean).join(' ');
}
