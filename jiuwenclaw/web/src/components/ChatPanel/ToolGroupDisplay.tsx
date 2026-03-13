/**
 * ToolGroupDisplay 组件
 *
 * 展示工具执行实体：call 可单独显示，result 仅回填显示。
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';
import { useTranslation } from 'react-i18next';
import { ToolExecution } from '../../types';
import { formatToolArguments, formatToolResult } from '../../utils';
import clsx from 'clsx';

interface ToolGroupDisplayProps {
  executions: ToolExecution[];
}

export function ToolExecutionItem({ execution }: { execution: ToolExecution }) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(false);
  const { toolCall, result, status } = execution;
  const subtitle = toolCall.formatted_args || '';
  const hasResult = !!result;
  const isTimeout = status === 'timeout';
  const isError = status === 'error';
  const isSuccess = status === 'completed';
  const resultSummary = result
    ? (result.summary || `${result.success ? t('chatUi.toolResult.success') : t('chatUi.toolResult.failed')}`)
    : '';

  return (
    <div className="tool-pair-item animate-rise">
      <div className="tool-pair-header" onClick={() => setIsExpanded(!isExpanded)}>
        <span className={clsx(
          'tool-pair-icon',
          isSuccess ? 'success' : isError ? 'error' : isTimeout ? 'warning' : 'pending'
        )}>
          {isSuccess ? (
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          ) : isError ? (
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : isTimeout ? (
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M12 3C7.029 3 3 7.029 3 12s4.029 9 9 9 9-4.029 9-9-4.029-9-9-9z" />
            </svg>
          ) : (
            <svg className="w-3 h-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          )}
        </span>

        <span className="tool-pair-name">{toolCall.name}</span>
        {subtitle && <span className="tool-pair-summary">{subtitle}</span>}

        {hasResult && (
          <span className={clsx(
            'tool-pair-result-badge',
            result.success ? 'success' : 'error'
          )}>
            {resultSummary}
          </span>
        )}
        {!hasResult && isTimeout && (
          <span className="tool-pair-result-badge warning">
            {t('chatUi.toolResult.timeout')}
          </span>
        )}
        <span className="tool-pair-toggle">{isExpanded ? '▼' : '▶'}</span>
      </div>

      {isExpanded && (
        <div className="tool-pair-detail">
          {Object.keys(toolCall.arguments).length > 0 && (
            <div className="tool-pair-section">
              <div className="tool-pair-section-label">{t('chatUi.toolResult.arguments')}</div>
              <pre className="tool-pair-pre">{formatToolArguments(toolCall.arguments)}</pre>
            </div>
          )}
          {result && (
            <div className="tool-pair-section">
              <div className="tool-pair-section-label">{t('chatUi.toolResult.result')}</div>
              <pre className={clsx('tool-pair-pre', !result.success && 'error')}>
                {formatToolResult(result.result, 1000)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ToolGroupDisplay({ executions }: ToolGroupDisplayProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [userScrolled, setUserScrolled] = useState(false);
  const totalPairs = executions.length;
  const pendingCount = executions.filter((e) => e.status === 'pending').length;
  const timeoutCount = executions.filter((e) => e.status === 'timeout').length;

  useEffect(() => {
    if (!import.meta.env.DEV) {
      return;
    }
    if (totalPairs > 0) {
      console.debug('[ws][metrics] pendingToolPairs', {
        pendingToolPairs: pendingCount,
        timeoutToolPairs: timeoutCount,
        totalToolPairs: totalPairs,
      });
    }
  }, [pendingCount, timeoutCount, totalPairs]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setUserScrolled(!atBottom);
  }, []);

  const scrollInner = useCallback((smooth = true) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: smooth ? 'smooth' : 'instant',
    });
  }, []);

  useEffect(() => {
    if (!userScrolled) {
      // 自动跟随新增工具项时使用即时滚动，避免出现从顶部滑下的视觉效果
      scrollInner(false);
    }
  }, [executions.length, userScrolled, scrollInner]);

  const scrollToBottom = useCallback(() => {
    setUserScrolled(false);
    scrollInner(true);
  }, [scrollInner]);

  return (
    <div className="tool-group-container animate-rise">
      <div className="tool-group-header">
        <div className="tool-group-header-left">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
          </svg>
          <span>
            {t('chatUi.toolGroup.executed', { totalPairs })}
            {pendingCount > 0 && <span className="tool-group-pending"> ({t('chatUi.toolGroup.pending', { pendingCount })})</span>}
            {timeoutCount > 0 && <span className="tool-group-pending warning"> ({t('chatUi.toolGroup.timeout', { timeoutCount })})</span>}
          </span>
        </div>
      </div>

      <div ref={scrollRef} className="tool-group-scroll" onScroll={handleScroll}>
        {executions.map((execution) => (
          <ToolExecutionItem key={execution.toolCallId} execution={execution} />
        ))}
      </div>

      {userScrolled && (
        <button className="tool-group-scroll-btn" onClick={scrollToBottom}>
          {t('chatUi.toolGroup.latest')}
        </button>
      )}
    </div>
  );
}
