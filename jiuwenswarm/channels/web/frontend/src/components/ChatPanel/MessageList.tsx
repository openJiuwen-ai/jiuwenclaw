import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import clsx from 'clsx';
import { LoaderCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Message, ToolExecution } from '../../types';
import { MessageItem } from './MessageItem';
import { ToolGroupDisplay } from './ToolGroupDisplay';
import { useNow, formatDurationPrecise } from './chatTimelineClock';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import { useChatStore, useSessionStore } from '../../stores';
import type { ReasoningSegment } from '../../stores/chatStore';
import {
  buildTimelineItems,
  buildRenderItems,
  buildTurnWorkMeta,
  buildLiveCompletedStreaks,
  buildStreakInputSignature,
  isSettlingForStreak,
  streakMapFingerprint,
  formatStreakSummaryLabel,
  messageHasDeliverable,
  filterDeliverableExecutions,
  completedWorkDurationMs,
  REASONING_COLLAPSE_DELAY_MS,
  STREAK_FOLD_TRANSITION_DELAY_MS,
  type LiveWorkStreak,
} from '../../features/chatTimeline/buildTurnTimeline';

const EMPTY_REASONING: ReasoningSegment[] = [];

interface MessageListProps {
  messages: Message[];
  renderAfterMessage?: (message: Message) => ReactNode;
}

interface ChatTimelineListProps {
  messages: Message[];
  executions?: ToolExecution[];
  reasoningSegments?: ReasoningSegment[];
  /**
   * 历史文件/分享图等静态时间线：强制按「已完成」折叠，
   * 不依赖当前会话的 isProcessing / store 思考段。
   */
  staticTimeline?: boolean;
  mode?: string;
  disableA2UIInteraction?: boolean;
  renderAfterMessage?: (message: Message) => ReactNode;
}

function formatElapsedCoarse(ms: number): string {
  const whole = Math.floor(Math.max(0, ms) / 1000);
  if (whole < 60) {
    return `${whole}s`;
  }
  const minutes = Math.floor(whole / 60);
  const seconds = whole % 60;
  return `${minutes}m${seconds.toString().padStart(2, '0')}s`;
}

function TurnElapsed({
  startMs,
  endMs,
  isLastTurn,
  teamLayout,
}: {
  startMs: number;
  endMs: number;
  isLastTurn: boolean;
  teamLayout: boolean;
}) {
  const { t } = useTranslation();
  const isProcessing = useChatStore((s) => s.runtimes[s.activeSessionId ?? '']?.isProcessing ?? false);
  const active = isLastTurn && isProcessing;
  const now = useNow(active);
  const end = active ? now : endMs;
  const elapsed = Math.max(0, end - startMs);
  if (!active && elapsed <= 0) {
    return null;
  }
  return (
    <div className={clsx('turn-elapsed', teamLayout && 'turn-elapsed--team', active && 'is-active')}>
      {active && (
        <LoaderCircle className="turn-elapsed__spinner" size={12} strokeWidth={2.2} aria-hidden="true" />
      )}
      <span className="turn-elapsed__label">
        {active ? t('chatUi.turnRunning') : t('chatUi.turnElapsed')}
      </span>
      <span className="turn-elapsed__value">
        {active ? formatElapsedCoarse(elapsed) : formatDurationPrecise(elapsed)}
      </span>
    </div>
  );
}

function CompletedWorkChip({
  variant,
  thinkingCount = 0,
  toolCount = 0,
  durationMs = 0,
  expanded,
  onToggle,
  showAvatar,
  teamLayout,
}: {
  variant: 'turn' | 'streak';
  thinkingCount?: number;
  toolCount?: number;
  durationMs?: number;
  expanded: boolean;
  onToggle: () => void;
  showAvatar: boolean;
  teamLayout: boolean;
}) {
  const { t } = useTranslation();
  const label =
    variant === 'turn'
      ? t('chatUi.workCompleted', {
          duration: formatDurationPrecise(Math.max(0, durationMs)),
        })
      : formatStreakSummaryLabel(t, thinkingCount, toolCount);

  const chip = (
    <button
      type="button"
      className={clsx(
        'completed-work-chip',
        variant === 'streak' && 'completed-work-chip--streak',
        expanded && 'is-expanded'
      )}
      onClick={onToggle}
      aria-expanded={expanded}
    >
      <span className="completed-work-chip__icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="10" cy="10" r="6.5" />
          <path d="m7.2 10.1 1.8 1.8 3.8-3.8" />
        </svg>
      </span>
      <span className="completed-work-chip__label">{label}</span>
      <span className={clsx('tool-tree-item__disclosure', expanded && 'is-open')} aria-hidden="true">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path strokeLinecap="round" strokeLinejoin="round" d="m8 6 4 4-4 4" />
        </svg>
      </span>
    </button>
  );

  if (teamLayout) {
    return (
      <div
        className={clsx(
          'completed-work-row',
          'completed-work-row--team',
          variant === 'streak' && 'completed-work-row--nested'
        )}
      >
        <div className="pt-0.5">{showAvatar ? <TeamMemberAvatar member="team_leader" /> : null}</div>
        {chip}
      </div>
    );
  }

  return (
    <div
      className={clsx(
        'completed-work-row',
        variant === 'streak' && 'completed-work-row--nested'
      )}
    >
      <div className="completed-work-row__avatar">
        {showAvatar ? <TeamMemberAvatar member="team_leader" /> : null}
      </div>
      {chip}
    </div>
  );
}

function ReasoningSegmentBlock({
  segment,
  showAvatar,
  teamLayout,
}: {
  segment: ReasoningSegment;
  showAvatar: boolean;
  teamLayout: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(!segment.closed);
  const userToggledRef = useRef(false);
  const prevClosedRef = useRef(segment.closed);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!prevClosedRef.current && segment.closed && !userToggledRef.current) {
      const timer = window.setTimeout(() => {
        if (!userToggledRef.current) {
          setOpen(false);
        }
      }, REASONING_COLLAPSE_DELAY_MS);
      prevClosedRef.current = segment.closed;
      return () => window.clearTimeout(timer);
    }
    prevClosedRef.current = segment.closed;
    return undefined;
  }, [segment.closed]);

  const body = segment.text.replace(/\n{3,}/g, '\n\n').trim();

  useEffect(() => {
    if (!open || segment.closed) {
      return;
    }
    const el = bodyRef.current;
    if (!el) {
      return;
    }
    el.scrollTop = el.scrollHeight;
  }, [open, segment.closed, body]);

  if (!body) {
    return null;
  }
  const running = !segment.closed;

  const content = (
    <div className="min-w-0 reasoning-panel">
      <button
        type="button"
        className="tool-tree__header"
        onClick={() => {
          userToggledRef.current = true;
          setOpen((current) => !current);
        }}
        aria-expanded={open}
      >
        <span className="tool-tree__header-line">
          <span className="tool-tree__cat-icon" aria-hidden="true">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 3.2a4.4 4.4 0 0 0-2.6 7.95v1.6a.9.9 0 0 0 .9.9h3.4a.9.9 0 0 0 .9-.9v-1.6A4.4 4.4 0 0 0 10 3.2z" />
              <path d="M8.3 16.2h3.4" />
            </svg>
          </span>
          <span className={clsx('tool-tree__header-line-text', running && 'is-running')}>
            {running ? t('chatUi.reasoning.thinking') : t('chatUi.reasoning.thought')}
          </span>
          <span className={clsx('tool-tree-item__disclosure', open && 'is-open')} aria-hidden="true">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path strokeLinecap="round" strokeLinejoin="round" d="m8 6 4 4-4 4" />
            </svg>
          </span>
        </span>
      </button>
      <div className={clsx('reasoning-panel__collapse', open && 'is-open')}>
        <div className="reasoning-panel__collapse-inner">
          <div ref={bodyRef} className="reasoning-panel__body">
            {body}
          </div>
        </div>
      </div>
    </div>
  );

  if (teamLayout) {
    return (
      <div className="reasoning-row reasoning-row--team" data-testid="reasoning-block">
        <div className="pt-0.5">{showAvatar ? <TeamMemberAvatar member="team_leader" /> : null}</div>
        {content}
      </div>
    );
  }

  return (
    <div className="reasoning-row" data-testid="reasoning-block">
      <div className="reasoning-row__avatar">
        {showAvatar ? <TeamMemberAvatar member="team_leader" /> : null}
      </div>
      {content}
    </div>
  );
}

export function ChatTimelineList({
  messages,
  executions = [],
  reasoningSegments: reasoningSegmentsProp,
  staticTimeline = false,
  mode = 'default',
  disableA2UIInteraction = false,
  renderAfterMessage,
}: ChatTimelineListProps) {
  const isTeamMode = mode === 'team';
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const storeIsProcessing = useChatStore((s) => s.runtimes[s.activeSessionId ?? '']?.isProcessing ?? false);
  const isLoadingHistory = useChatStore((s) => s.runtimes[s.activeSessionId ?? '']?.isLoadingHistory ?? false);
  const storeReasoningSegments = useChatStore(
    (s) => s.runtimes[s.activeSessionId ?? '']?.reasoningSegments ?? EMPTY_REASONING
  );
  const isProcessing = staticTimeline ? false : storeIsProcessing;
  const reasoningSegments = reasoningSegmentsProp ?? (staticTimeline ? EMPTY_REASONING : storeReasoningSegments);
  const renderItems = useMemo(
    () => buildRenderItems(buildTimelineItems(messages, executions, reasoningSegments), isTeamMode, isProcessing),
    [messages, executions, reasoningSegments, isTeamMode, isProcessing]
  );
  const settlingForStreak = isSettlingForStreak(renderItems, Date.now());
  const settleNow = useNow(settlingForStreak);
  const streakNowMs = settlingForStreak ? settleNow : Date.now();
  const turnWorkMeta = useMemo(
    () => buildTurnWorkMeta(renderItems, isProcessing),
    [renderItems, isProcessing]
  );
  const streakInputSig = useMemo(
    () => buildStreakInputSignature(renderItems, streakNowMs),
    [renderItems, streakNowMs]
  );
  const streakCacheRef = useRef<{ sig: string; map: Map<string, LiveWorkStreak> }>({
    sig: '',
    map: new Map(),
  });
  if (streakCacheRef.current.sig !== streakInputSig) {
    streakCacheRef.current = {
      sig: streakInputSig,
      map: buildLiveCompletedStreaks(renderItems, streakNowMs),
    };
  }
  const liveStreaksByFirstKey = streakCacheRef.current.map;
  const liveStreakFp = useMemo(
    () => streakMapFingerprint(liveStreaksByFirstKey),
    [liveStreaksByFirstKey]
  );
  const [displayedStreaksByFirstKey, setDisplayedStreaksByFirstKey] = useState<Map<string, LiveWorkStreak>>(
    () => new Map()
  );
  const displayedStreakFpRef = useRef('');
  const suppressStreakTransitionRef = useRef(true);
  const streaksForRender = staticTimeline ? liveStreaksByFirstKey : displayedStreaksByFirstKey;
  const liveStreakByItemKey = useMemo(() => {
    const map = new Map<string, LiveWorkStreak>();
    for (const streak of streaksForRender.values()) {
      for (const key of streak.keys) {
        map.set(key, streak);
      }
    }
    return map;
  }, [streaksForRender]);
  const [expandedTurns, setExpandedTurns] = useState<Record<number, boolean>>({});
  const [expandedStreaks, setExpandedStreaks] = useState<Record<string, boolean>>({});
  const chipAnchoredTurns = useRef<Set<number>>(new Set());
  chipAnchoredTurns.current = new Set();

  useEffect(() => {
    setExpandedTurns({});
    setExpandedStreaks({});
    suppressStreakTransitionRef.current = true;
    displayedStreakFpRef.current = '';
    setDisplayedStreaksByFirstKey(new Map());
  }, [activeSessionId]);

  const wasLoadingHistoryRef = useRef(false);
  useEffect(() => {
    if (staticTimeline) {
      return;
    }
    if (isLoadingHistory) {
      wasLoadingHistoryRef.current = true;
      return;
    }
    if (wasLoadingHistoryRef.current) {
      wasLoadingHistoryRef.current = false;
      setExpandedTurns({});
      setExpandedStreaks({});
      suppressStreakTransitionRef.current = true;
      displayedStreakFpRef.current = '';
      setDisplayedStreaksByFirstKey(new Map());
    }
  }, [staticTimeline, isLoadingHistory]);

  useEffect(() => {
    if (staticTimeline) {
      return;
    }
    if (liveStreakFp === displayedStreakFpRef.current) {
      return;
    }
    const nextMap = liveStreaksByFirstKey;
    if (suppressStreakTransitionRef.current) {
      displayedStreakFpRef.current = liveStreakFp;
      suppressStreakTransitionRef.current = false;
      setDisplayedStreaksByFirstKey(nextMap);
      return;
    }
    const timer = window.setTimeout(() => {
      displayedStreakFpRef.current = liveStreakFp;
      setDisplayedStreaksByFirstKey(nextMap);
    }, STREAK_FOLD_TRANSITION_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [liveStreakFp, liveStreaksByFirstKey, staticTimeline]);

  if (renderItems.length === 0) {
    return null;
  }

  const toggleTurn = (turnId: number) => {
    setExpandedTurns((prev) => ({ ...prev, [turnId]: !prev[turnId] }));
  };

  const toggleStreak = (streakId: string) => {
    setExpandedStreaks((prev) => ({ ...prev, [streakId]: !prev[streakId] }));
  };

  return (
    <div className="chat-timeline">
      {renderItems.map((item) => {
        if (item.type === 'message') {
          const meta = item.turnId >= 0 ? turnWorkMeta.get(item.turnId) : undefined;
          const turnFoldable = Boolean(meta?.completed && meta.hasWork && item.hideMeta);
          const turnOpen = !turnFoldable || Boolean(expandedTurns[item.turnId]);

          if (turnFoldable) {
            const hasDeliverable = messageHasDeliverable(item.message);
            return (
              <Fragment key={item.key}>
                {/* 折叠态：交付物与代码变更卡需留在文档流内，不能放进被 absolute 隐藏的 collapse */}
                {!turnOpen && hasDeliverable ? (
                  <>
                    <MessageItem
                      message={{ ...item.message, content: '' }}
                      showAvatar={false}
                      hideMeta
                      disableA2UIInteraction={disableA2UIInteraction}
                      enableAssistantAvatar={!isTeamMode}
                    />
                    {renderAfterMessage?.(item.message)}
                  </>
                ) : null}
                <div className={clsx('timeline-collapse', turnOpen && 'is-open')}>
                  <div className="timeline-collapse-inner">
                    <MessageItem
                      message={item.message}
                      showAvatar={item.showAvatar}
                      hideMeta={item.hideMeta}
                      disableA2UIInteraction={disableA2UIInteraction}
                      enableAssistantAvatar={!isTeamMode}
                    />
                    {turnOpen ? renderAfterMessage?.(item.message) : null}
                  </div>
                </div>
              </Fragment>
            );
          }

          return (
            <Fragment key={item.key}>
              <MessageItem
                message={item.message}
                showAvatar={item.showAvatar}
                hideMeta={item.hideMeta}
                disableA2UIInteraction={disableA2UIInteraction}
                enableAssistantAvatar={!isTeamMode}
              />
              {renderAfterMessage?.(item.message)}
            </Fragment>
          );
        }

        if (item.type === 'reasoning' || item.type === 'toolGroup') {
          const meta = turnWorkMeta.get(item.turnId);
          const turnFoldable = Boolean(meta?.completed && meta.hasWork);
          const turnOpen = !turnFoldable || Boolean(expandedTurns[item.turnId]);
          const streak = liveStreakByItemKey.get(item.key);
          const streakOpen = !streak || Boolean(expandedStreaks[streak.id]);
          const contentOpen = turnOpen && streakOpen;
          const isTurnAnchor =
            Boolean(meta) &&
            (meta!.firstWorkKey === item.key ||
              (!meta!.firstWorkKey && !chipAnchoredTurns.current.has(item.turnId)));
          if (isTurnAnchor && meta) {
            chipAnchoredTurns.current.add(item.turnId);
          }

          const nodes: ReactNode[] = [];

          if (turnFoldable && isTurnAnchor && meta) {
            nodes.push(
              <CompletedWorkChip
                key={`completed-work-${item.turnId}`}
                variant="turn"
                durationMs={completedWorkDurationMs(meta)}
                expanded={turnOpen}
                onToggle={() => toggleTurn(item.turnId)}
                showAvatar={meta.showAvatar}
                teamLayout={isTeamMode}
              />
            );
          }

          // 轮次展开后才露出 streak chip；内容仍可按 streak 再折一层
          if (turnOpen && streak && streak.firstKey === item.key) {
            nodes.push(
              <CompletedWorkChip
                key={streak.id}
                variant="streak"
                thinkingCount={streak.thinkingCount}
                toolCount={streak.toolCount}
                expanded={streakOpen}
                onToggle={() => toggleStreak(streak.id)}
                showAvatar={turnFoldable ? false : streak.showAvatar}
                teamLayout={isTeamMode}
              />
            );
          }

          // 折叠时交付物仍可见（不参与收起动画）
          if (!contentOpen && item.type === 'toolGroup') {
            const deliverables = filterDeliverableExecutions(item.executions);
            if (deliverables.length > 0) {
              nodes.push(
                <ToolGroupDisplay
                  key={`${item.key}-deliverable`}
                  executions={deliverables}
                  notices={[]}
                  showAvatar={false}
                  teamLayout={isTeamMode}
                  collapseSkillTreeWhenContentStarts={false}
                  viewedSkillIds={[]}
                />
              );
            }
          }

          const hideAvatar = Boolean(
            (turnFoldable && turnOpen) || (streak && streakOpen)
          );

          const body =
            item.type === 'reasoning' ? (
              <ReasoningSegmentBlock
                segment={item.segment}
                showAvatar={hideAvatar ? false : item.showAvatar}
                teamLayout={isTeamMode}
              />
            ) : (
              <ToolGroupDisplay
                executions={item.executions}
                notices={item.notices}
                showAvatar={hideAvatar ? false : item.showAvatar}
                teamLayout={isTeamMode}
                collapseSkillTreeWhenContentStarts={item.collapseSkillTreeWhenContentStarts}
                viewedSkillIds={item.viewedSkillIds}
              />
            );

          // 可折叠时内容常驻 DOM，用与思考相同的 grid 高度过渡
          if (turnFoldable || streak) {
            nodes.push(
              <div
                key={`${item.key}-collapse`}
                className={clsx('timeline-collapse', contentOpen && 'is-open')}
              >
                <div className="timeline-collapse-inner">{body}</div>
              </div>
            );
          } else {
            nodes.push(<Fragment key={item.key}>{body}</Fragment>);
          }

          return nodes.length === 1 ? (
            nodes[0]
          ) : (
            <Fragment key={`work-${item.key}`}>{nodes}</Fragment>
          );
        }

        if (item.type === 'turnSummary') {
          const meta = turnWorkMeta.get(item.turnId);
          if (meta?.completed && meta.hasWork) {
            return null;
          }
          return (
            <TurnElapsed
              key={item.key}
              startMs={item.startMs}
              endMs={item.endMs}
              isLastTurn={item.isLastTurn}
              teamLayout={isTeamMode}
            />
          );
        }

        return null;
      })}
    </div>
  );
}

export function MessageList({ messages, renderAfterMessage }: MessageListProps) {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const toolExecutions = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.toolExecutions ?? new Map());
  const toolExecutionOrder = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.toolExecutionOrder ?? []);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent');
  const executions = useMemo(
    () => toolExecutionOrder
      .map((toolCallId) => toolExecutions.get(toolCallId))
      .filter((item): item is NonNullable<typeof item> => !!item),
    [toolExecutions, toolExecutionOrder]
  );

  return (
    <ChatTimelineList
      messages={messages}
      executions={executions}
      mode={mode}
      renderAfterMessage={renderAfterMessage}
    />
  );
}
