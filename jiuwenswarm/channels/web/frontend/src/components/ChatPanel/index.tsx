/**
 * ChatPanel 组件
 *
 * 聊天面板，包含消息列表和输入区域
 */

import React, { useRef, useEffect, useLayoutEffect, useCallback, useMemo } from 'react';
import { ArrowRight, LoaderCircle, Share2, Sparkles } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore, useTodoStore } from '../../stores';
import { AgentMode, Message, UserAnswer } from '../../types';
import { MessageList } from './MessageList';
import { ContextCompressionLines } from './MessageItem';
import { InputArea } from './InputArea';
import { SubtaskProgress } from './SubtaskProgress';
import { InlineQuestionCard } from './InlineQuestionCard';
import { HistoryPagerBar } from './HistoryPagerBar';
import { HarnessProgressBar } from './HarnessProgressBar';
import { AgentTeamActivityCard } from './TeamEventGroupDisplay';
import { isTeamActivityMessage, parseTeamEventMessage } from './teamEventUtils';
import { isTeamLeaderMember } from '../../utils/teamMemberAvatar';
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
  onNewSession: () => Promise<void>;
  onUserAnswer: (requestId: string, answers: UserAnswer[]) => void;
  onExportShare?: () => void | Promise<void>;
  isExportingShare?: boolean;
  canExportShare?: boolean;
  /** 自会话管理恢复历史后出现；支持分页加载更早消息 */
  historyPager?: ChatHistoryPagerProps | null;
  /** 右侧面板展开状态：展开时隐藏对话框上方的活跃成员 */
  teamAreaExpanded?: boolean;
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
  const { interruptResult } = useChatStore();
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
  const { messages } = useChatStore();
  const {
    mode,
    teamHistoryMessages,
    teamMemberExecutionEvents,
    teamTaskEvents,
    teamTasks,
    teamMembers,
  } = useSessionStore();
  const { todos } = useTodoStore();
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
        我是<span className="chat-welcome__brand">JiuwenSwarm</span>，很高兴认识你!
      </>
    );
  }

  return (
    <>
      Hi, I&apos;m <span className="chat-welcome__brand">JiuwenSwarm</span>. Nice to meet you!
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
  onNewSession,
  onUserAnswer,
  onExportShare,
  isExportingShare = false,
  canExportShare = false,
  historyPager = null,
  teamAreaExpanded = false,
}: ChatPanelProps) {
  const { t } = useTranslation();
  const {
    messages,
    isThinking,
    toolExecutionOrder,
    contextCompressionRuntime,
    contextCompressionSummary,
  } = useChatStore();
  const { mode } = useSessionStore();
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
  const shouldShowShareExport = Boolean(onExportShare && hasConversation);
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
      {/* HarnessProgressBar - sticky header, doesn't scroll with messages */}
      <div className="sticky top-0 z-10 px-3 pt-2 bg-bg/95 backdrop-blur-sm">
        {shouldShowShareExport && (
          <div className="mb-2 flex justify-end">
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
          </div>
        )}
        <HarnessProgressBar />
      </div>
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
              <h2 className="chat-welcome__heading"><WelcomeHeading /></h2>
              <p className="chat-welcome__subtext">
                {t('chat.welcomeSubtext')}
              </p>
              <div className="chat-welcome__composer">
                <ActiveTeamGroupEntry isProcessing={isProcessing} teamAreaExpanded={teamAreaExpanded} />
                <InterruptResultBubble />
                <InputArea
                  onSubmit={handleSendMessage}
                  onInterrupt={onInterrupt}
                  onCancel={onCancel}
                  onSwitchMode={onSwitchMode}
                  isProcessing={isProcessing}
                  onNewSession={onNewSession}
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
          <InterruptResultBubble />
          <InputArea
            onSubmit={handleSendMessage}
            onInterrupt={onInterrupt}
            onCancel={onCancel}
            onSwitchMode={onSwitchMode}
            isProcessing={isProcessing}
            onNewSession={onNewSession}
          />
        </div>
      )}
    </div>
  );
}
