// RSI 纯展示函数：状态文案 / 节点状态色 / 分数格式化等。
// 与数据层解耦，便于组件复用与单测。

import type { RsiNodeType, RsiTaskStatus, RsiTreeNode } from './types';

// 节点类型 → 展示用状态色类名（对应 rsi.css 的 bar--* 与图例 dot）
export type NodeStatusKind = 'best-path' | 'evaluated' | 'pending' | 'pruned';

// 节点 type → 状态色映射（对齐样式概要：最优路径/已评测/待评测/已剪枝）
// adopted/root → best-path；rejected → evaluated；provisional → pending；pruned → pruned
export function nodeTypeToStatusKind(type: RsiNodeType): NodeStatusKind {
  switch (type) {
    case 'root':
    case 'adopted':
      return 'best-path';
    case 'rejected':
      return 'evaluated';
    case 'provisional':
      return 'pending';
    case 'pruned':
      return 'pruned';
  }
}

export function statusKindClass(kind: NodeStatusKind): string {
  return `rsi-node__bar--${kind}`;
}

export function legendDotClass(kind: NodeStatusKind): string {
  return `rsi-legend__dot rsi-node__bar--${kind}`;
}

// 任务状态 → 中文标签 key（i18n key 后缀）
const STATUS_LABEL_KEY: Record<RsiTaskStatus, string> = {
  created: 'statusCreated',
  queued: 'statusQueued',
  running: 'statusRunning',
  completed: 'statusCompleted',
  failed: 'statusFailed',
  paused: 'statusPaused',
  terminated: 'statusTerminated',
};

export function statusLabelKey(status: RsiTaskStatus): string {
  return STATUS_LABEL_KEY[status] ?? 'statusCreated';
}

// 运行态操作按钮映射：根据当前状态返回可执行动作集
export type RsiActionKind = 'start' | 'pause' | 'resume' | 'terminate' | 'install' | 'download' | 'config';

export function actionsForStatus(status: RsiTaskStatus, scenario: 'harness' | 'artifact'): RsiActionKind[] {
  const actions: RsiActionKind[] = ['config'];
  switch (status) {
    case 'running':
      actions.push('pause');
      break;
    case 'paused':
      actions.push('resume', 'terminate');
      break;
    case 'completed':
      actions.push('install', 'download');
      break;
    case 'failed':
    case 'terminated':
      break;
    case 'created':
      actions.push('start');
      break;
    case 'queued':
      break;
  }
  // 产物优化无"安装插件"（仅 harness 插件可安装）
  if (scenario === 'artifact') {
    const idx = actions.indexOf('install');
    if (idx >= 0) actions.splice(idx, 1);
  }
  return actions;
}

// 分数格式化：保留 1 位小数，null 显示 —
export function formatScore(score: number | null, digits = 1): string {
  if (score == null || Number.isNaN(score)) return '--';
  return score.toFixed(digits);
}

// 提升百分比：↑ 5.2% / ↓ 2.1%，null 显示空串
export function formatGain(gain: number | null): { text: string; kind: 'up' | 'down' | 'none' } {
  if (gain == null || Number.isNaN(gain)) return { text: '', kind: 'none' };
  const pct = gain * 100;
  if (pct >= 0) return { text: `${pct.toFixed(1)}% ↑`, kind: 'up' };
  return { text: `${Math.abs(pct).toFixed(1)}% ↓`, kind: 'down' };
}

// token 用量格式化：万 tokens
export function formatTokens(tokens: { input: number; output: number; cache_hit: number }): string {
  const total = tokens.input + tokens.output + tokens.cache_hit;
  const wan = total / 10000;
  if (wan >= 1) return `${wan.toFixed(1)} 万tokens`;
  return `${total}tokens`;
}

// token 用量格式化（K 单位）：示例 123K tokens（对齐样式概要）
export function formatTokensK(tokens: { input: number; output: number; cache_hit: number }): string {
  const total = tokens.input + tokens.output + tokens.cache_hit;
  if (total >= 1000) return Math.round(total / 1000) + 'K tokens';
  return total + ' tokens';
}

// 费用格式化：元
export function formatCost(cost: number | null): string {
  if (cost == null) return '--';
  return cost.toFixed(2);
}

// 日期时间格式化：ISO 字符串 → 本地 'YYYY-MM-DD HH:mm:ss'（对齐样式概要示例）
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '--';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    [d.getFullYear(), pad(d.getMonth() + 1), pad(d.getDate())].join('-') +
    ' ' +
    [pad(d.getHours()), pad(d.getMinutes()), pad(d.getSeconds())].join(':')
  );
}

// 场景显示名（带产物子类型分隔符）
export function scenarioLabel(scenario: 'harness' | 'artifact'): string {
  return scenario === 'harness' ? 'Harness优化' : '产物优化';
}
export function artifactTypeLabel(type: 'paper' | 'program'): string {
  return type === 'paper' ? '论文' : '程序';
}
// 完整类型标签：Harness优化 / 产物优化|程序 / 产物优化|论文
export function typeDisplayLabel(scenario: 'harness' | 'artifact', artifactType: 'paper' | 'program' | null): string {
  if (scenario === 'harness') return 'Harness优化';
  return '产物优化|' + artifactTypeLabel(artifactType ?? 'paper');
}

// 状态徽章信息：标签 + 图标类型 + 背景色
export type StatusBadgeKind = 'queued' | 'running' | 'completed' | 'failed';

export function statusBadgeInfo(status: RsiTaskStatus): {
  label: string;
  kind: StatusBadgeKind | null;
} {
  switch (status) {
    case 'queued':
    case 'created':
      return { label: '排队中', kind: 'queued' };
    case 'running':
    case 'paused':
      return { label: '优化中', kind: 'running' };
    case 'completed':
      return { label: '已完成', kind: 'completed' };
    case 'failed':
    case 'terminated':
      return { label: '任务失败', kind: 'failed' };
  }
}

// 节点显示名称：根→基线+场景名；被采纳→快照+节点ID；其余→节点ID
export function nodeDisplayName(
  type: RsiNodeType,
  nodeId: string,
  scenario: 'harness' | 'artifact',
  artifactType: 'paper' | 'program' | null,
): string {
  if (type === 'root') {
    if (scenario === 'harness') return '基线Harness';
    if (artifactType === 'paper') return '基线论文';
    return '基线程序';
  }
  if (type === 'adopted') {
    return '快照' + nodeId;
  }
  return nodeId;
}

// 节点状态标签（上层右侧）：基线/当前最优/已评测/评测中/剪枝
export function nodeStatusLabel(type: RsiNodeType): string {
  switch (type) {
    case 'root':
      return '基线';
    case 'adopted':
      return '当前最优';
    case 'rejected':
      return '已评测';
    case 'provisional':
      return '评测中';
    case 'pruned':
      return '剪枝';
  }
}

// ── 运行态展示：区分「评测中」与「待评测」（同色 #FCE7AE，图标/文案不同）──
// provisional + 任务运行中 → 评测中；provisional + 任务非运行 → 待评测。
// 后端若给节点增加独立评测状态字段，可在 nodeRuntimeKind 内替换该启发式。
export type NodeRuntimeKind = 'best-path' | 'evaluated' | 'evaluating' | 'pending' | 'pruned';

export type NodeIconKind = 'crown' | 'check' | 'chevron-double' | 'clock' | 'minus';

// 节点 type + 任务运行态 → 运行态 kind（决定图标与右侧标签）
export function nodeRuntimeKind(type: RsiNodeType, taskRunning: boolean): NodeRuntimeKind {
  switch (type) {
    case 'root':
    case 'adopted':
      return 'best-path';
    case 'rejected':
      return 'evaluated';
    case 'provisional':
      return taskRunning ? 'evaluating' : 'pending';
    case 'pruned':
      return 'pruned';
  }
}

// 运行态 kind → 上层背景色 class（评测中复用 pending 色板）
export function runtimeKindColorClass(kind: NodeRuntimeKind): string {
  const color = kind === 'evaluating' ? 'pending' : kind;
  return `rsi-node__bar--${color}`;
}

// 运行态 kind → 上层左侧黑色徽章内的图标
export function runtimeIconKind(kind: NodeRuntimeKind): NodeIconKind {
  switch (kind) {
    case 'best-path':
      return 'crown';
    case 'evaluated':
      return 'check';
    case 'evaluating':
      return 'chevron-double';
    case 'pending':
      return 'clock';
    case 'pruned':
      return 'minus';
  }
}

// 运行态 kind → 上层右侧状态标签
export function nodeRuntimeLabel(kind: NodeRuntimeKind): string {
  switch (kind) {
    case 'best-path':
      return '最优路径';
    case 'evaluated':
      return '已评测';
    case 'evaluating':
      return '评测中';
    case 'pending':
      return '待评测';
    case 'pruned':
      return '剪枝';
  }
}

// 节点下层分数行：score(分数) + extra.potential_score(潜力分) + extra.other_score(其他分) + 其余 *_score
// 数据驱动：当前仅有 score 时只显示 1 行；后端在 extra 补充分数后自动多行并触发展开。
export interface NodeScoreLine {
  value: string;
  label: string;
}

export function nodeScoreLines(node: RsiTreeNode): NodeScoreLine[] {
  const lines: NodeScoreLine[] = [];
  if (node.score != null) lines.push({ value: formatScore(node.score), label: '分数' });
  const extra = node.extra;
  if (extra && typeof extra === 'object') {
    const potential = extra['potential_score'];
    if (typeof potential === 'number') lines.push({ value: formatScore(potential), label: '潜力分' });
    const other = extra['other_score'];
    if (typeof other === 'number') lines.push({ value: formatScore(other), label: '其他分' });
    for (const [k, v] of Object.entries(extra)) {
      if (k === 'potential_score' || k === 'other_score') continue;
      if (k.endsWith('_score') && typeof v === 'number') {
        lines.push({ value: formatScore(v), label: k.replace(/_score$/, '') });
      }
    }
  }
  return lines;
}

// 节点尺寸估算（布局与组件共用，保证布局高度=渲染高度）
// 上层高度固定 32；下层高度随内容行数变化；评测中宽度最大 280。
export const RSI_NODE_BAR_H = 32;
export const RSI_SCORE_LINE_H = 26; // 分数行行高
export const RSI_EVAL_LINE_H = 24; // 评测中/文本行高
export const RSI_BODY_PAD = 8; // 下层上下 padding 合计
export const RSI_NODE_MIN_W = 180;
export const RSI_NODE_MAX_W = 280;
export const RSI_SCORE_TOGGLE_H = 20; // 展开按钮(分隔符+行)高度

export interface NodeMetrics {
  width: number;
  barH: number;
  bodyH: number;
}

// 评测中文本行数估算（按估算宽度每行可容纳字符数，最多 4 行）
function evalTextRows(text: string, width: number): number {
  const charsPerLine = Math.max(1, Math.floor((width - 24) / 14));
  return Math.min(4, Math.max(1, Math.ceil(text.length / charsPerLine)));
}

// 节点宽高估算：barH(32) + bodyH(随内容)；评测中宽度自适应(<=280)。
export function nodeMetrics(node: RsiTreeNode, kind: NodeRuntimeKind, scoreExpanded: boolean): NodeMetrics {
  const barH = RSI_NODE_BAR_H;
  let width = RSI_NODE_MIN_W;
  let bodyH = RSI_BODY_PAD;
  if (kind === 'evaluating') {
    const text = node.summary ?? '正在分析实现';
    width = Math.min(RSI_NODE_MAX_W, Math.max(RSI_NODE_MIN_W, text.length * 14 + 32));
    bodyH = RSI_BODY_PAD + evalTextRows(text, width) * RSI_EVAL_LINE_H;
  } else if (kind === 'best-path' || kind === 'evaluated') {
    const linesArr = nodeScoreLines(node);
    const shown = scoreExpanded ? Math.min(linesArr.length, 5) : Math.min(linesArr.length, 3);
    bodyH = RSI_BODY_PAD + shown * RSI_SCORE_LINE_H;
    if (linesArr.length > 3) bodyH += RSI_SCORE_TOGGLE_H;
  } else if (kind === 'pending') {
    bodyH = RSI_BODY_PAD + RSI_SCORE_LINE_H;
  } else if (kind === 'pruned') {
    bodyH = node.score == null ? RSI_BODY_PAD + RSI_EVAL_LINE_H : RSI_BODY_PAD + RSI_SCORE_LINE_H;
  }
  return { width, barH, bodyH };
}

// 节点总高度
export function nodeHeight(node: RsiTreeNode, kind: NodeRuntimeKind, scoreExpanded: boolean): number {
  const m = nodeMetrics(node, kind, scoreExpanded);
  return m.barH + m.bodyH;
}
