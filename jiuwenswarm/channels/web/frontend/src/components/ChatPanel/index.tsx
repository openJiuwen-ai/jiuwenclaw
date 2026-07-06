/**
 * ChatPanel 组件
 *
 * 聊天面板，包含消息列表和输入区域
 */

import React, { useRef, useEffect, useLayoutEffect, useCallback, useMemo, useState } from 'react';
import { ArrowRight, LoaderCircle, Share2, Sparkles } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore, useTodoStore } from '../../stores';
import { AgentMode, Message, UserAnswer } from '../../types';
import { MessageList } from './MessageList';
import { ContextCompressionLines } from './MessageItem';
import { InputArea } from './InputArea';
import chatIcon from '../../assets/chat.svg';
import expandIcon from '../../assets/expand.svg';
import lineUpIcon from '../../assets/lineUp.svg';
import loadSendIcon from '../../assets/load-send.svg';
import editIcon from '../../assets/edit.svg';
import deleteIcon from '../../assets/delete.svg';
import moveIcon from '../../assets/move.svg';
import restartIcon from '../../assets/restart.svg';
import { SubtaskProgress } from './SubtaskProgress';
import { InlineQuestionCard } from './InlineQuestionCard';
import { HistoryPagerBar } from './HistoryPagerBar';
import { HarnessProgressBar } from './HarnessProgressBar';
import { AgentTeamActivityCard } from './TeamEventGroupDisplay';
import { isTeamActivityMessage, parseTeamEventMessage } from './teamEventUtils';
import { isTeamLeaderMember } from '../../utils/teamMemberAvatar';
import welcomeBanner from '../../assets/jiuwen-xiaobanner.png';
import './ChatPanel.css';

export interface ChatHistoryPagerProps {
  loadedPages: number;
  totalPages: number;
  loadingMore: boolean;
  onLoadMore: () => void | Promise<void>;
}

interface ChatPanelProps {
  onSendMessage: (content: string) => void;
  onInterrupt: (newInput?: string) => void;
  onCancel: () => void;
  onSwitchMode: (mode: AgentMode) => void;
  isProcessing: boolean;
  onUserAnswer: (requestId: string, answers: UserAnswer[]) => void;
  onExportShare?: () => void | Promise<void>;
  isExportingShare?: boolean;
  canExportShare?: boolean;
  sessionTitle?: string;
  /** 自会话管理恢复历史后出现；支持分页加载更早消息 */
  historyPager?: ChatHistoryPagerProps | null;
  /** 右侧面板展开状态：展开时隐藏对话框上方的活跃成员 */
  teamAreaExpanded?: boolean;
  autoFocusKey?: string | null;
  /** 跳转到技能管理页 */
  onNavigateToSkills?: () => void;
  /** 切换右侧紧缩面板展开状态 */
  onToggleTeamArea?: (expanded: boolean) => void;
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start animate-rise">
      <div className="chat-bubble assistant chat-reading-indicator">
        <div className="chat-reading-indicator__dots">
          <span />
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}

function SuggestionCard({ text, onClick }: { text: string; onClick: () => void }) {
  return (
    <button className="chat-suggestion-card" onClick={onClick}>
      <Sparkles className="chat-suggestion-card__icon" strokeWidth={2} />
      <span className="chat-suggestion-card__text">{text}</span>
      <ArrowRight className="chat-suggestion-card__arrow" strokeWidth={2} />
    </button>
  );
}

function InterruptResultBubble() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const interruptResult = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.interruptResult ?? null);
  const message = interruptResult?.message?.trim();

  if (!message || interruptResult?.success) {
    return null;
  }

  return (
    <div
      className="chat-interrupt-bubble chat-interrupt-bubble--error"
      role="alert"
    >
      {message}
    </div>
  );
}

function ActiveTeamGroupEntry({ isProcessing, teamAreaExpanded }: { isProcessing: boolean; teamAreaExpanded?: boolean }) {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const messages = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages ?? []);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent.plan');
  const teamHistoryMessages = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamHistoryMessages ?? []);
  const teamMemberExecutionEvents = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamMemberExecutionEvents ?? []);
  const teamTaskEvents = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamTaskEvents ?? []);
  const teamTasks = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamTasks ?? []);
  const teamMembers = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamMembers ?? []);
  const todos = useTodoStore((s) => s.runtimes[activeSessionId ?? '']?.todos ?? []);
  const activeTeamMessages = useMemo(
    () => getActiveTeamMessages(teamHistoryMessages, messages),
    [teamHistoryMessages, messages]
  );
  const hasVisibleMembers = teamMembers.some(
    (m) => m.member_id && m.member_id !== 'user' && !isTeamLeaderMember(m.member_id)
  );

  if (mode !== 'team' || !hasVisibleMembers || teamAreaExpanded) {
    return null;
  }

  return (
    <AgentTeamActivityCard
      messages={activeTeamMessages}
      isProcessing={isProcessing}
      tasks={teamTasks}
      taskEvents={teamTaskEvents}
      todos={todos}
      executionEvents={teamMemberExecutionEvents}
    />
  );
}

/** 单 Agent 模式的消息队列卡片，展示在输入框上方 */
function AgentActivityCard({ isProcessing: _isProcessing, onSendTask }: { isProcessing: boolean; onSendTask?: (content: string) => void }) {
  const [expanded, setExpanded] = useState(true);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const { t } = useTranslation();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent.plan');
  const taskQueue = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.taskQueue ?? []);
  const queuePaused = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.queuePaused ?? false);
  const removeFromTaskQueue = useChatStore((s) => s.removeFromTaskQueue);
  const reorderTaskQueue = useChatStore((s) => s.reorderTaskQueue);
  const setQueuePaused = useChatStore((s) => s.setQueuePaused);
  const setInputValue = useChatStore((s) => s.setInputValue);

  const isAgentMode = mode === 'agent.fast' || mode === 'agent.plan';

  // 有等待任务时自动展开
  useEffect(() => {
    if (taskQueue.length > 0) {
      setExpanded(true);
    }
  }, [taskQueue.length]);

  if (!isAgentMode || taskQueue.length === 0) {
    return null;
  }

  const handleResume = (e: React.MouseEvent) => {
    e.stopPropagation();
    const sid = useChatStore.getState().activeSessionId;
    if (!sid) return;
    setQueuePaused(sid, false);
    // 触发下一条队列任务
    const runtime = useChatStore.getState().getRuntime(sid);
    const nextTask = runtime?.taskQueue[0];
    if (nextTask) {
      removeFromTaskQueue(sid, nextTask.id);
      onSendTask?.(nextTask.content);
    }
  };

  const handleRemoveTask = (e: React.MouseEvent, taskId: string) => {
    e.stopPropagation();
    const sid = useChatStore.getState().activeSessionId;
    if (sid) {
      removeFromTaskQueue(sid, taskId);
    }
  };

  const handleEditTask = (e: React.MouseEvent, taskId: string, content: string) => {
    e.stopPropagation();
    const sid = useChatStore.getState().activeSessionId;
    if (sid) {
      setInputValue(sid, content);
      removeFromTaskQueue(sid, taskId);
      window.dispatchEvent(new CustomEvent('chat-input-sync', { detail: { sessionId: sid, value: content } }));
    }
  };

  const handleSendTask = (e: React.MouseEvent, taskId: string, content: string) => {
    e.stopPropagation();
    const sid = useChatStore.getState().activeSessionId;
    if (sid) {
      removeFromTaskQueue(sid, taskId);
    }
    onSendTask?.(content);
  };

  const handleDragStart = (index: number) => {
    setDragIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    setDragOverIndex(index);
  };

  const handleDrop = (index: number) => {
    if (dragIndex === null || dragIndex === index) {
      setDragIndex(null);
      setDragOverIndex(null);
      return;
    }
    const sid = useChatStore.getState().activeSessionId;
    if (sid) {
      reorderTaskQueue(sid, dragIndex, index);
    }
    setDragIndex(null);
    setDragOverIndex(null);
  };

  const handleDragEnd = () => {
    setDragIndex(null);
    setDragOverIndex(null);
  };

  return (
    <div className="chat-active-team-group animate-rise">
      <div className="team-event-group team-event-group--activity">
        <button
          type="button"
          className="team-event-group-summary"
          onClick={() => setExpanded(prev => !prev)}
          aria-expanded={expanded}
        >
          <span className="team-event-group-summary__main">
            <span className="team-event-group-summary__title">{t('chatUi.messageQueue')}</span>
            {queuePaused && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', marginLeft: '8px' }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#f5a623', flexShrink: 0 }} />
                <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{t('chat.paused')}</span>
              </span>
            )}
          </span>
          {queuePaused && (
            <span
              role="button"
              tabIndex={0}
              className="team-event-group-summary__activity"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', marginLeft: 'auto', justifyContent: 'end', flexShrink: 0, cursor: 'pointer' }}
              onClick={handleResume}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); handleResume(e as unknown as React.MouseEvent); } }}
            >
              <img src={restartIcon} alt="" className="w-3.5 h-3.5" />
              {t('chat.resume')}
            </span>
          )}
        </button>
        {expanded && (
          <div className="team-event-group-list team-event-group-list--activity">
            {taskQueue.map((task, index) => (
              <div
                key={task.id}
                className="team-event-group-row team-event-group-row--activity"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '8px',
                  opacity: dragIndex === index ? 0.4 : 1,
                  background: dragOverIndex === index ? 'var(--bg-hover)' : 'transparent',
                  transition: 'opacity 0.15s ease, background 0.15s ease',
                }}
                onDragOver={(e) => handleDragOver(e, index)}
                onDrop={() => handleDrop(index)}
                onDragEnd={handleDragEnd}
              >
                <div className="team-event-group-row__main" style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                  {/* 拖动图标：所有任务可拖，悬浮显示 */}
                  <img
                    src={moveIcon}
                    alt=""
                    draggable
                    onDragStart={() => handleDragStart(index)}
                    className="queue-drag-handle"
                    title={t('chat.dragTask')}
                  />
                  <div className="team-event-group-row__avatar" style={{ display: 'flex', alignItems: 'center' }}>
                    <img src={lineUpIcon} alt="" className="w-4 h-4" />
                  </div>
                  <span className="team-event-group-row__member" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {task.content}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                  <button
                    type="button"
                    className="chat-input-task-action chat-input-task-action--send"
                    title={t('chat.sendTask')}
                    onClick={(e) => handleSendTask(e, task.id, task.content)}
                  >
                    <img src={loadSendIcon} alt="" className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    className="chat-input-task-action chat-input-task-action--edit"
                    title={t('chat.editTask')}
                    onClick={(e) => handleEditTask(e, task.id, task.content)}
                  >
                    <img src={editIcon} alt="" className="w-3 h-3" />
                  </button>
                  <button
                    type="button"
                    className="chat-input-task-action chat-input-task-action--delete"
                    title={t('chat.removeTask')}
                    onClick={(e) => handleRemoveTask(e, task.id)}
                  >
                    <img src={deleteIcon} alt="" className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function getActiveTeamMessages(historyMessages: Message[], messages: Message[]): Message[] {
  const seen = new Set<string>();
  return [...historyMessages, ...messages]
    .filter(isTeamActivityMessage)
    .filter((message) => {
      const key = getTeamMessageIdentity(message);
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function getTeamMessageIdentity(message: Message): string {
  const event = parseTeamEventMessage(message);
  if (!event) {
    return message.id || `${message.timestamp}:${message.content}`;
  }
  return [
    'team',
    event.type,
    event.messageId,
    event.fromMember,
    event.toMember || '',
    event.timestamp || '',
    event.content,
  ].join(':');
}

function WelcomeHeading() {
  const { i18n } = useTranslation();
  const isZh = i18n.language.startsWith('zh');

  if (isZh) {
    return (
      <>
        JiuwenSwarm 轻松解决工作每个问题！
      </>
    );
  }

  return (
    <>
      JiuwenSwarm makes work easier!
    </>
  );
}

function getShareExportTitle(
  t: TFunction,
  isExportingShare: boolean,
  canExportShare: boolean
): string {
  if (isExportingShare) {
    return t('share.exporting');
  }
  if (!canExportShare) {
    return t('share.exportUnavailable');
  }
  return t('share.export');
}

export function ChatPanel({
  onSendMessage,
  onInterrupt,
  onCancel,
  onSwitchMode,
  isProcessing,
  onUserAnswer,
  onExportShare,
  isExportingShare = false,
  canExportShare = false,
  sessionTitle,
  historyPager = null,
  teamAreaExpanded = false,
  autoFocusKey = null,
  onNavigateToSkills,
  onToggleTeamArea,
}: ChatPanelProps) {
  const { t } = useTranslation();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const messages = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages ?? []);
  const isThinking = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.isThinking ?? false);
  const toolExecutionOrder = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.toolExecutionOrder ?? []);
  const contextCompressionRuntime = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.contextCompressionRuntime);
  const contextCompressionSummary = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.contextCompressionSummary);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent.plan');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prependScrollSnapRef = useRef<{ sh: number; st: number } | null>(null);
  const wasHistoryLoadingRef = useRef(false);
  const suppressNextScrollToEndRef = useRef(false);
  const [isSending, setIsSending] = React.useState(false);
  const hasTimelineContent = messages.length > 0 || toolExecutionOrder.length > 0;
  const hasConversation = Boolean(historyPager || hasTimelineContent);
  const chatContentClassName = hasConversation
    ? `chat-content${mode === 'team' ? ' chat-content--team' : ''}`
    : 'chat-content chat-content--welcome';
  const suggestions = [
    t('chat.welcomeSuggestions.journey'),
    t('chat.welcomeSuggestions.skills'),
  ];
  const shouldShowChatHeader = hasConversation;
  const shareExportTitle = getShareExportTitle(t, isExportingShare, canExportShare);

  // 跟踪用户是否正在查看历史消息（不在底部）
  const userScrolledUpRef = useRef(false);

  // 检测用户滚动位置
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    
    // 检查是否在底部（有 40px 的阈值）
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    userScrolledUpRef.current = !atBottom;
    
    // 当滚动到顶部且有更多历史消息时，加载更多
    if (el.scrollTop <= 8 && historyPager && historyPager.loadedPages < historyPager.totalPages && !historyPager.loadingMore) {
      void historyPager.onLoadMore();
    }
  }, [historyPager]);

  // 检测鼠标滚轮事件，即使没有滚动条也能触发加载更多
  const handleWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    // 只有向上滚动时才触发
    if (e.deltaY < 0 && historyPager && historyPager.loadedPages < historyPager.totalPages && !historyPager.loadingMore) {
      // 检查是否已经在顶部（没有滚动条时 scrollTop 始终为 0）
      const el = scrollContainerRef.current;
      if (el && el.scrollTop <= 8) {
        void historyPager.onLoadMore();
      }
    }
  }, [historyPager]);

  useEffect(() => {
    if (suppressNextScrollToEndRef.current) {
      suppressNextScrollToEndRef.current = false;
      return;
    }
    
    // 只有当用户在底部时才自动滚动
    if (!userScrolledUpRef.current) {
      messagesEndRef.current?.scrollIntoView({
        behavior: historyPager?.loadedPages === 1 ? 'auto' : 'smooth',
      });
    }
  }, [messages, isThinking, contextCompressionRuntime, contextCompressionSummary, historyPager]);

  useLayoutEffect(() => {
    if (!historyPager) {
      wasHistoryLoadingRef.current = false;
      prependScrollSnapRef.current = null;
      return;
    }
    const el = scrollContainerRef.current;
    if (!el) return;

    if (historyPager.loadingMore) {
      if (!wasHistoryLoadingRef.current) {
        prependScrollSnapRef.current = { sh: el.scrollHeight, st: el.scrollTop };
      }
      wasHistoryLoadingRef.current = true;
      return;
    }

    if (wasHistoryLoadingRef.current && prependScrollSnapRef.current) {
      const snap = prependScrollSnapRef.current;
      const delta = el.scrollHeight - snap.sh;
      if (delta > 0) {
        el.scrollTop = snap.st + delta;
        suppressNextScrollToEndRef.current = true;
      }
      prependScrollSnapRef.current = null;
    }
    wasHistoryLoadingRef.current = false;
  }, [historyPager, messages.length]);

  // 包装发送消息函数，添加滚动逻辑
  const handleSendMessage = useCallback((content: string) => {
    setIsSending(true);
    onSendMessage(content);
  }, [onSendMessage]);

  // 当发送消息时强制滚动到底部
  useEffect(() => {
    if (isSending) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
      userScrolledUpRef.current = false;
      setIsSending(false);
    }
  }, [isSending]);

  const handleSuggestion = useCallback(
    (text: string) => handleSendMessage(text),
    [handleSendMessage],
  );
  return (
    <div className="chat-panel-shell flex flex-col h-full" data-testid="chat-panel">
      {shouldShowChatHeader && (
        <div className="chat-panel-header">
          <div className="chat-panel-header__title" title={sessionTitle}>
            {sessionTitle}
          </div>
          <div className="chat-panel-header__actions">
            <button
              type="button"
              className={`icon-btn share-export-btn ${isExportingShare ? 'share-export-btn--loading' : ''}`}
              data-testid="share-export"
              title={shareExportTitle}
              aria-label={shareExportTitle}
              aria-busy={isExportingShare}
              disabled={!canExportShare || isExportingShare}
              onClick={() => {
                void onExportShare?.();
              }}
            >
              {isExportingShare ? (
                <>
                  <LoaderCircle className="share-export-btn__spinner" size={16} strokeWidth={2} />
                  <span className="share-export-btn__label">{t('share.generating')}</span>
                </>
              ) : (
                <Share2 size={16} strokeWidth={2} />
              )}
            </button>
            <button
              type="button"
              className={`chat-header-icon-btn ${!teamAreaExpanded ? 'chat-header-icon-btn--active' : ''}`}
              onClick={() => onToggleTeamArea?.(false)}
            >
              <img src={chatIcon} alt="" className="chat-header-icon-btn__icon" />
            </button>
            <button
              type="button"
              className={`chat-header-icon-btn ${teamAreaExpanded ? 'chat-header-icon-btn--active' : ''}`}
              onClick={() => onToggleTeamArea?.(true)}
            >
              <img src={expandIcon} alt="" className="chat-header-icon-btn__icon" />
            </button>
          </div>
        </div>
      )}
      <div ref={scrollContainerRef} className="chat-scroll flex-1 overflow-y-auto" onScroll={handleScroll} onWheel={handleWheel}>
        <div className={chatContentClassName}>
          {hasConversation ? (
            <>
              {historyPager && (
                <HistoryPagerBar
                  loadedPages={historyPager.loadedPages}
                  totalPages={historyPager.totalPages}
                  loadingMore={historyPager.loadingMore}
                  onLoadMore={historyPager.onLoadMore}
                />
              )}
              <div className="chat-harness-entry">
                <HarnessProgressBar />
              </div>
              {hasTimelineContent ? (
                <>
                  <MessageList messages={messages} />
                  <SubtaskProgress />
                  {/* 内联审批卡片（演进审批 & 权限审批共用） */}
                  <InlineQuestionCard onSubmit={onUserAnswer} />
                  {/* 思考中指示器 */}
                  {isThinking && <ThinkingIndicator />}
                  <ContextCompressionLines
                    runtime={contextCompressionRuntime}
                    summary={contextCompressionSummary}
                  />
                </>
              ) : (
                <div className="flex items-center justify-center h-32">
                  <div className="text-text-muted text-sm">
                    {t('connection.loadingConfig')}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="chat-welcome">
              <img className="chat-welcome__banner" src={welcomeBanner} alt={t('chat.welcomeLogoAlt')} />
              <h2 className="chat-welcome__heading"><WelcomeHeading /></h2>
              <div className="chat-welcome__composer">
                <ActiveTeamGroupEntry isProcessing={isProcessing} teamAreaExpanded={teamAreaExpanded} />
                <AgentActivityCard isProcessing={isProcessing} onSendTask={handleSendMessage} />
                <InterruptResultBubble />
                <InputArea
                  onSubmit={handleSendMessage}
                  onInterrupt={onInterrupt}
                  onCancel={onCancel}
                  onSwitchMode={onSwitchMode}
                  isProcessing={isProcessing}
                  autoFocusKey={autoFocusKey}
                  onNavigateToSkills={onNavigateToSkills}
                />
              </div>
              <div className="chat-suggestions">
                {suggestions.map((text) => (
                  <SuggestionCard key={text} text={text} onClick={() => handleSuggestion(text)} />
                ))}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {hasConversation && (
        <div className="chat-compose">
          <ActiveTeamGroupEntry isProcessing={isProcessing} teamAreaExpanded={teamAreaExpanded} />
          <AgentActivityCard isProcessing={isProcessing} onSendTask={handleSendMessage} />
          <InterruptResultBubble />
          <InputArea
            onSubmit={handleSendMessage}
            onInterrupt={onInterrupt}
            onCancel={onCancel}
            onSwitchMode={onSwitchMode}
            isProcessing={isProcessing}
            autoFocusKey={autoFocusKey}
            onNavigateToSkills={onNavigateToSkills}
          />
        </div>
      )}
    </div>
  );
}
