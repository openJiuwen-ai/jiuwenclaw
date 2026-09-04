/**
 * RSI 优化结果摘要条（rsi-stage 上半区）。
 * 数据源对齐接口契约：
 *   - score / baseline：P2 推送 liveProgress → task.progress → report.best_score/baseline（§3.3/§8.1）
 *   - usage.tokens：rsi.usage.get → task.usage（§3.4/§8.2）
 *   - metrics.iterations / eval_passed / eval_total / pruned_count：rsi.report.get（§8.1）
 *   - pruned_count 仅 harness 优化有值；产物优化为 null（§14），不渲染剪枝列
 * 后端产物预览图接口 ready 后替换占位缩略图。
 */
import { useTranslation } from 'react-i18next';
import optimizeImage from '../../../assets/rsi/rsi-optimize.svg';
import type { RsiTaskGetResult, RsiReportGetResult, RsiUsageGetResult } from '../types';
import { formatScore, formatGain, formatTokensK } from '../rsiPresentation';
import { useRsiStore } from '../rsiStore';

interface RsiResultSummaryProps {
  task: RsiTaskGetResult;
  report: RsiReportGetResult | null;
  usage: RsiUsageGetResult | null;
}

export function RsiResultSummary({ task, report, usage }: RsiResultSummaryProps) {
  const { t } = useTranslation();
  const liveProgress = useRsiStore((s) => (s.selectedTaskId ? s.detail[s.selectedTaskId]?.liveProgress : null));

  // 分数优先取运行时推送，回退 task.progress/report
  const score = liveProgress?.score ?? task.progress?.score ?? report?.best_score ?? null;
  const baseline = liveProgress?.baseline ?? task.progress?.baseline ?? report?.baseline ?? null;
  const gain = score != null && baseline != null && baseline > 0 ? (score - baseline) / baseline : null;
  const gainFmt = formatGain(gain);
  const bestName = task.best_artifact?.name
    ?? report?.best_artifact?.name
    ?? task.best_artifact?.artifact_id
    ?? report?.best_artifact?.artifact_id
    ?? null;
  const queued = task.status === 'CREATED' || task.status === 'QUEUED';

  const evalPassed = queued ? null : (report?.metrics.eval_passed ?? null);
  const evalTotal = queued ? null : (report?.metrics.eval_total ?? null);
  const prunedCount = queued ? null : (report?.metrics.pruned_count ?? null);
  const iterations = queued
    ? null
    : (liveProgress?.iteration ?? report?.metrics.iterations ?? task.progress?.iteration ?? null);
  const tokenUsage = usage?.usage ?? task.usage ?? null;

  // 指标列顺序：基线分数 → 用量 → 迭代次数 → 组合评测 →（剪枝，仅 harness）
  const metrics: Array<{ key: string; value: string; label: string }> = [
    { key: 'baseline', value: formatScore(baseline), label: t('rsi.detail.baselineScore') },
    { key: 'usage', value: tokenUsage ? formatTokensK(tokenUsage.tokens) : '--', label: t('rsi.detail.usage') },
    { key: 'iterations', value: iterations != null ? String(iterations) : '--', label: t('rsi.detail.iterations') },
    {
      key: 'eval',
      value: evalPassed != null && evalTotal != null ? `${evalPassed}/${evalTotal}` : '--',
      label: t('rsi.detail.evalCount'),
    },
  ];
  // 剪枝仅 harness 优化展示（产物优化 pruned_count 为 null，§14）
  if (prunedCount != null) {
    metrics.push({
      key: 'pruned',
      value: prunedCount != null ? String(prunedCount) : '--',
      label: t('rsi.detail.pruned'),
    });
  }

  return (
    <div className="rsi-result">
      <div className="rsi-result__header">{t('rsi.detail.optimizationResult')}</div>
      <div className="rsi-result__body">
        {/* 产物预览缩略图（64x64） */}
        <img className="rsi-result__thumb" src={optimizeImage} alt="" aria-hidden />
        {/* 优化分数 + 当前最优产物（沿用 rsi-score / rsi-best 组合） */}
        <div className="rsi-result__score-best">
          <div className="rsi-score" data-testid="rsi-score">
            {formatScore(score)}
            {gainFmt.kind !== 'none' && (
              <span
                className={
                  'rsi-score__delta ' + (gainFmt.kind === 'up' ? 'rsi-score__delta--up' : 'rsi-score__delta--down')
                }
              >
                {gainFmt.text}
              </span>
            )}
          </div>
          <div className="rsi-best">
            {t('rsi.detail.bestArtifact')}：{bestName ?? '当前暂无产物'}
          </div>
        </div>
        {metrics.map((m) => (
          <div key={m.key} className={'rsi-result__metric' + (m.key === 'usage' ? ' rsi-result__metric--usage' : '')}>
            <div className="rsi-result__metric-value">{m.value}</div>
            <div className="rsi-result__metric-label">{m.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
