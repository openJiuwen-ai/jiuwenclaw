/**
 * ToolGroupDisplay 组件
 *
 * 以轻量折叠列表展示工具调用状态，行内可展开查看参数和结果。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { ToolExecution } from '../../types';
import { formatToolArguments, formatToolResult } from '../../utils';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import { SkillTreePath } from './SkillTreePath';

interface ToolGroupDisplayProps {
  executions: ToolExecution[];
  notices?: string[];
  showAvatar?: boolean;
  teamLayout?: boolean;
  collapseSkillTreeWhenContentStarts?: boolean;
  viewedSkillIds?: string[];
}

interface ToolDetailModalProps {
  execution: ToolExecution;
  onClose: () => void;
}

type ToolStatusTone = 'success' | 'warning' | 'error' | 'pending';

function ToolStatusIcon({
  tone,
  className,
}: {
  tone: ToolStatusTone;
  className?: string;
}) {
  return (
    <span className={clsx('tool-status-icon', `is-${tone}`, className)}>
      {tone === 'success' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="10" cy="10" r="6.8" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M7.2 10.15 9.1 12.05l3.7-4.05" />
        </svg>
      ) : tone === 'error' || tone === 'warning' ? (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="10" cy="10" r="6.8" />
          <path strokeLinecap="round" d="M10 6.4v4.5" />
          <circle cx="10" cy="13.65" r="0.75" fill="currentColor" stroke="none" />
        </svg>
      ) : (
        <svg className="tool-status-icon__spinner" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="10" cy="10" r="6.8" opacity="0.22" />
          <path strokeLinecap="round" d="M10 3.2A6.8 6.8 0 0 1 16.8 10" />
        </svg>
      )}
    </span>
  );
}

function isToolResultSuccessful(result?: ToolExecution['result']) {
  return Boolean(result?.success && !result.result.includes('success=False'));
}

function isExecutionSuccessful(execution: ToolExecution) {
  return execution.status === 'completed' && isToolResultSuccessful(execution.result);
}

function getExecutionTone(execution: ToolExecution): ToolStatusTone {
  if (isExecutionSuccessful(execution)) {
    return 'success';
  }
  if (execution.status === 'timeout') {
    return 'warning';
  }
  if (execution.status === 'error' || execution.result) {
    return 'error';
  }
  return 'pending';
}

function getExecutionLabel(execution: ToolExecution, sessionCompletedLabel: string) {
  if (execution.toolCall.name === 'session') {
    return execution.toolCall.formatted_args || sessionCompletedLabel;
  }

  return execution.toolCall.name;
}

function isSkillToolName(name: string): boolean {
  const normalized = name.trim().toLowerCase();
  const compact = normalized.replace(/[\s-]+/g, '_');
  return (
    compact === 'skill_tool' ||
    compact.endsWith('.skill_tool') ||
    compact.endsWith('/skill_tool') ||
    compact.endsWith(':skill_tool')
  );
}

function addViewedSkillName(out: Set<string>, value: unknown) {
  if (typeof value !== 'string') {
    return;
  }
  const skillName = value.trim();
  if (skillName) {
    out.add(skillName);
  }
}

function addViewedSkillNameFromArgs(out: Set<string>, args: Record<string, unknown> | null | undefined) {
  if (!args) {
    return;
  }
  addViewedSkillName(out, args.skill_name);
  addViewedSkillName(out, args.skillName);
}

function addViewedSkillNameFromText(out: Set<string>, value: string | undefined) {
  const text = String(value || '').trim();
  if (!text) {
    return;
  }

  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      addViewedSkillNameFromArgs(out, parsed as Record<string, unknown>);
      return;
    }
  } catch {
    // formatted_args is often a display string, not JSON.
  }

  const match = text.match(/["']?skill[_-]?name["']?\s*[:=]\s*["']?([^"',}\]\s]+)/i);
  addViewedSkillName(out, match?.[1]);
}

export function collectViewedSkillIds(executions: ToolExecution[]): string[] {
  const out = new Set<string>();
  executions.forEach((execution) => {
    if (!isSkillToolName(execution.toolCall.name)) {
      return;
    }
    addViewedSkillNameFromArgs(out, execution.toolCall.arguments);
    addViewedSkillNameFromText(out, execution.toolCall.formatted_args);
  });
  return Array.from(out);
}

function ToolDetailModal({ execution, onClose }: ToolDetailModalProps) {
  const { t } = useTranslation();
  const { toolCall, result, status } = execution;
  const isTimeout = status === 'timeout';
  const modalTone = getExecutionTone(execution);
  const resultSuccess = isToolResultSuccessful(result);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      <div
        className="relative w-full max-w-2xl max-h-[85vh] overflow-hidden rounded-xl animate-rise"
        style={{
          backgroundColor: 'var(--card)',
          boxShadow: 'var(--shadow-xl)',
        }}
      >
        <div
          className="px-6 py-4 flex items-center justify-between"
          style={{
            backgroundColor: 'var(--panel-strong)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="flex items-center gap-4">
            <ToolStatusIcon tone={modalTone} className="tool-status-icon--lg" />

            <div>
              <h2
                className="text-lg font-semibold font-mono"
                style={{ color: 'var(--text-strong)' }}
              >
                {toolCall.name}
              </h2>
              {toolCall.formatted_args && (
                <p
                  className="text-sm font-mono mt-1"
                  style={{ color: 'var(--muted)' }}
                >
                  {toolCall.formatted_args}
                </p>
              )}
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-lg transition-colors"
            style={{ color: 'var(--muted)' }}
            onMouseEnter={(event) => {
              event.currentTarget.style.backgroundColor = 'var(--bg-hover)';
              event.currentTarget.style.color = 'var(--text)';
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.backgroundColor = 'transparent';
              event.currentTarget.style.color = 'var(--muted)';
            }}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div
          className="px-6 py-5 overflow-y-auto"
          style={{ maxHeight: '60vh' }}
        >
          {Object.keys(toolCall.arguments).length > 0 && (
            <div className="mb-6">
              <div
                className="flex items-center gap-2 mb-3"
                style={{ color: 'var(--text-strong)' }}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
                <span className="text-sm font-semibold">{t('chatUi.toolResult.arguments')}</span>
              </div>
              <pre
                className="p-4 rounded-lg overflow-auto whitespace-pre-wrap break-all"
                style={{
                  fontFamily: 'var(--mono)',
                  fontSize: 'var(--font-size-sm)',
                  lineHeight: '1.5',
                  backgroundColor: 'var(--bg-elevated)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)',
                  wordBreak: 'break-word',
                }}
              >
                {formatToolArguments(toolCall.arguments)}
              </pre>
            </div>
          )}

          {result && (
            <div>
              <div
                className="flex items-center gap-2 mb-3"
                style={{
                  color: resultSuccess
                    ? 'var(--ok)'
                    : 'var(--danger)',
                }}
              >
                <ToolStatusIcon tone={resultSuccess ? 'success' : 'error'} />
                <span className="text-sm font-semibold">
                  {t('chatUi.toolResult.result')}
                  {!resultSuccess && (
                    <span
                      className="ml-2 px-2 py-0.5 rounded text-xs font-medium"
                      style={{
                        backgroundColor: 'var(--danger-subtle)',
                        color: 'var(--danger)',
                      }}
                    >
                      {t('chatUi.toolResult.failed')}
                    </span>
                  )}
                </span>
              </div>
              {result.skillTree && (
                <SkillTreePath tree={result.skillTree} stepIntervalMs={0} />
              )}
              {(!result.skillTree || result.result) && (
                <pre
                  className={clsx(
                    'p-4 rounded-lg overflow-auto whitespace-pre-wrap break-all',
                    result.skillTree && 'mt-4'
                  )}
                  style={{
                    fontFamily: 'var(--mono)',
                    fontSize: 'var(--font-size-sm)',
                    lineHeight: '1.5',
                    backgroundColor: 'var(--bg-elevated)',
                    border: '1px solid var(--border)',
                    color: resultSuccess
                      ? 'var(--text)'
                      : 'var(--danger)',
                    wordBreak: 'break-word',
                  }}
                >
                  {formatToolResult(result.result)}
                </pre>
              )}
            </div>
          )}

          {!result && isTimeout && (
            <div
              className="flex items-center gap-3 p-4 rounded-lg"
              style={{
                backgroundColor: 'var(--warn-subtle)',
                border: '1px solid var(--warn)',
                color: 'var(--warn)',
              }}
            >
              <ToolStatusIcon tone="warning" />
              <span className="font-medium">{t('chatUi.toolResult.timeout')}</span>
            </div>
          )}

          {!result && !isTimeout && (
            <div
              className="flex items-center gap-3 p-4 rounded-lg"
              style={{
                backgroundColor: 'var(--accent-subtle)',
                border: '1px solid var(--accent)',
                color: 'var(--accent)',
              }}
            >
              <ToolStatusIcon tone="pending" />
              <span className="font-medium">{t('chatUi.toolResult.running')}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ToolExecutionRow({ execution }: { execution: ToolExecution }) {
  const { t } = useTranslation();
  const [showModal, setShowModal] = useState(false);
  const { toolCall, status } = execution;
  const rowTone = getExecutionTone(execution);

  return (
    <>
      <div
        className="tool-tree-item"
        data-testid={`tool-execution-${toolCall.id}`}
        data-tool-name={toolCall.name}
        data-tool-status={status}
      >
        <button
          type="button"
          className="tool-tree-item__button"
          onClick={() => setShowModal(true)}
        >
          <ToolStatusIcon tone={rowTone} className="tool-tree-item__status" />

          <span className="tool-tree-item__main">
            <span className="tool-tree-item__name">
              {getExecutionLabel(execution, t('chatUi.toolGroup.sessionCompleted'))}
            </span>
          </span>
        </button>
      </div>

      {showModal && (
        <ToolDetailModal execution={execution} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}

export function ToolGroupDisplay({
  executions,
  notices = [],
  showAvatar = true,
  teamLayout = false,
  collapseSkillTreeWhenContentStarts = false,
  viewedSkillIds: turnViewedSkillIds = [],
}: ToolGroupDisplayProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [groupOpen, setGroupOpen] = useState(true);
  const [userScrolled, setUserScrolled] = useState(false);
  const visibleExecutions = teamLayout
    ? executions.filter((execution) => !execution.toolCall.memberName)
    : executions;
  const totalPairs = visibleExecutions.length;
  const hasPending = visibleExecutions.some((execution) => execution.status === 'pending');

  useEffect(() => {
    if (hasPending) {
      setGroupOpen(true);
    }
  }, [hasPending, visibleExecutions.length]);

  const handleScroll = useCallback(() => {
    const element = scrollRef.current;
    if (!element) {
      return;
    }
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    setUserScrolled(!atBottom);
  }, []);

  const scrollInner = useCallback((smooth = true) => {
    const element = scrollRef.current;
    if (!element) {
      return;
    }
    element.scrollTo({
      top: element.scrollHeight,
      behavior: smooth ? 'smooth' : 'instant',
    });
  }, []);

  useEffect(() => {
    if (groupOpen && !userScrolled) {
      scrollInner(false);
    }
  }, [visibleExecutions.length, groupOpen, userScrolled, scrollInner]);

  const scrollToBottom = useCallback(() => {
    setUserScrolled(false);
    scrollInner(true);
  }, [scrollInner]);

  const headerLabel = t('chatUi.toolGroup.executed', { totalPairs });
  const skillTreeExecutions = visibleExecutions.filter(
    (execution) => execution.result?.skillTree
  );
  const skillTrees = skillTreeExecutions
    .map((execution) => execution.result?.skillTree)
    .filter((tree): tree is NonNullable<typeof tree> => Boolean(tree));
  const viewedSkillIds = Array.from(new Set([
    ...turnViewedSkillIds,
    ...collectViewedSkillIds(executions),
  ]));
  if (visibleExecutions.length === 0) {
    return null;
  }

  return (
    <div
      className={clsx(
        'tool-group-frame animate-rise',
        teamLayout && 'tool-group-frame--team'
      )}
      data-testid="tool-group"
    >
      <div className="pt-0.5">
        {showAvatar ? (
          <TeamMemberAvatar member="team_leader" />
        ) : null}
      </div>
      <div className="min-w-0">
        <div className="tool-tree">
          {notices.length > 0 && (
            <div className="tool-tree__notices">
              {notices.map((notice) => (
                <div key={notice} className="tool-tree__notice">
                  {notice}
                </div>
              ))}
            </div>
          )}
          <button
            type="button"
            className="tool-tree__header"
            onClick={() => setGroupOpen((current) => !current)}
            aria-expanded={groupOpen}
          >
            <span className="tool-tree__header-text">
              <span className="tool-tree__header-title-row">
                <span className="tool-tree__header-title">{headerLabel}</span>
                <span className={clsx('tool-tree__chevron', groupOpen && 'is-open')} aria-hidden="true">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m6.5 8 3.5 4 3.5-4" />
                  </svg>
                </span>
              </span>
            </span>
          </button>

          {groupOpen && (
            <>
              <div ref={scrollRef} className="tool-tree__list" onScroll={handleScroll}>
                {visibleExecutions.map((execution) => (
                  <ToolExecutionRow key={execution.toolCallId} execution={execution} />
                ))}
              </div>

              {userScrolled && (
                <button type="button" className="tool-tree__latest" onClick={scrollToBottom}>
                  {t('chatUi.toolGroup.latest')}
                </button>
              )}
            </>
          )}
        </div>

        {skillTrees.length > 0 && (
          <SkillTreePath
            trees={skillTrees}
            viewedSkillIds={viewedSkillIds}
            autoCollapse={collapseSkillTreeWhenContentStarts}
          />
        )}
      </div>
    </div>
  );
}
