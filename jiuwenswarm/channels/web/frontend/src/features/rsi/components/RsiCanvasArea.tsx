/**
 * RSI 右栏画布区：子 Header + 提示 + 成本 + 画布（有向树渲染/节点/图例/缩放/交互）。
 * 树布局从左到右：根在左、子节点在右、同层兄弟纵向排开。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { RsiTaskGetResult, RsiTreeGetResult } from '../types';
import {
  legendDotClass,
  nodeDisplayName,
  formatCost,
  statusBadgeInfo,
  type NodeStatusKind,
  type NodeIconKind,
  nodeRuntimeKind,
  runtimeKindColorClass,
  runtimeIconKind,
  nodeRuntimeLabel,
  nodeScoreLines,
} from '../rsiPresentation';
import { layoutTree, type LayoutNode, type TreeLayout } from '../rsiTreeLayout';
import { useRsiStore } from '../rsiStore';
import { RsiSelectedInfo } from './RsiSelectedInfo';

interface RsiCanvasAreaProps {
  task: RsiTaskGetResult;
  tree: RsiTreeGetResult | null;
}

const LEGEND: Array<{ kind: NodeStatusKind; labelKey: string }> = [
  { kind: 'best-path', labelKey: 'rsi.detail.legendBestPath' },
  { kind: 'evaluated', labelKey: 'rsi.detail.legendEvaluated' },
  { kind: 'pending', labelKey: 'rsi.detail.legendPending' },
  { kind: 'pruned', labelKey: 'rsi.detail.legendPruned' },
];

// 运行中图标（24x24 圆形底 + 6 根放射短线，表示运行中）
// 节点上层黑色徽章图标：圆形黑底 + 白色状态图标（皇冠/对号/双箭头/时钟/减号）
const STATUS_ICON_PATHS: Record<NodeIconKind, ReactNode> = {
  // 小皇冠
  crown: <path d="M3 8l3.2 2.4L12 4l5.8 6.4L21 8l-1.5 9h-15L3 8z" fill="currentColor" />,
  // 对号
  check: (
    <path
      d="M5 12.5l4 4 10-10"
      stroke="currentColor"
      strokeWidth="2.2"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
  // 双箭头 >>
  'chevron-double': (
    <>
      <path
        d="M9 6l5 6-5 6"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M15 6l5 6-5 6"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),
  // 时钟
  clock: (
    <>
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" fill="none" />
      <path d="M12 8v4l3 2" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
    </>
  ),
  // 减号
  minus: <path d="M6 12h12" stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinecap="round" />,
};

// 节点上层左侧图标：最优路径为黑色填充小皇冠（无圆形黑底），其余为黑色圆底 + 白色图标。
function StatusBadge({ icon }: { icon: NodeIconKind }) {
  if (icon === 'crown') {
    return (
      <span className="rsi-node__badge rsi-node__badge--plain" aria-hidden>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" style={{ color: '#000' }}>
          {STATUS_ICON_PATHS.crown}
        </svg>
      </span>
    );
  }
  return (
    <span className="rsi-node__badge" aria-hidden>
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" style={{ color: '#fff' }}>
        {STATUS_ICON_PATHS[icon]}
      </svg>
    </span>
  );
}

function RunningIcon() {
  const cx = 12;
  const cy = 12;
  const r1 = 4.5;
  const r2 = 8.5;
  const lines = Array.from({ length: 6 }, (_, i) => {
    const a = (i * Math.PI) / 3;
    const cos = Math.cos(a);
    const sin = Math.sin(a);
    return { x1: cx + r1 * cos, y1: cy + r1 * sin, x2: cx + r2 * cos, y2: cy + r2 * sin };
  });
  return (
    <span className="rsi-canvas-statusbar__running" aria-hidden>
      <svg viewBox="0 0 24 24" width="24" height="24">
        {lines.map((l, i) => (
          <line key={i} x1={l.x1.toFixed(2)} y1={l.y1.toFixed(2)} x2={l.x2.toFixed(2)} y2={l.y2.toFixed(2)} />
        ))}
      </svg>
    </span>
  );
}

// 圆环成本图标（24x24，圆环 + ¥）
function CostRingIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <circle cx="12" cy="12" r="9" />
      <text x="12" y="16" textAnchor="middle" fontSize="11" fill="currentColor" stroke="none">
        ¥
      </text>
    </svg>
  );
}

interface RsiCanvasStatusBarProps {
  statusText: string;
  candidate: string | null;
  cost: number | null;
  progressPct: number;
  queued: boolean;
  onBack?: () => void;
}

function RsiCanvasStatusBar({ statusText, candidate, cost, progressPct, queued, onBack }: RsiCanvasStatusBarProps) {
  const { t } = useTranslation();
  return (
    <div
      className={'rsi-canvas-statusbar' + (onBack ? ' rsi-canvas-statusbar--fullscreen' : '')}
      data-queued={queued || undefined}
    >
      <div className="rsi-canvas-statusbar__main">
        {onBack && (
          <button type="button" className="rsi-canvas-statusbar__back" onClick={onBack} aria-label="back">
            <svg
              viewBox="0 0 16 16"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden
            >
              <path d="M10 3L5 8l5 5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
        <div className="rsi-canvas-statusbar__left">
          <span className="rsi-canvas-statusbar__item">
            <RunningIcon />
            <span className="rsi-canvas-statusbar__strong">{statusText}</span>
            {candidate && <span className="rsi-canvas-statusbar__weak">{candidate}</span>}
          </span>
          <span className="rsi-canvas-statusbar__divider" />
          <span className="rsi-canvas-statusbar__item">
            <CostRingIcon />
            <span className="rsi-canvas-statusbar__weak">
              {t('rsi.detail.estimatedCost', { cost: formatCost(cost) })}
            </span>
          </span>
          <span className="rsi-canvas-statusbar__divider" />
          <span className="rsi-canvas-statusbar__item">
            <span className="rsi-canvas-statusbar__weak">{t('rsi.detail.progress', { defaultValue: '进度' })}</span>
            <span className="rsi-canvas-statusbar__strong">{progressPct}%</span>
            <span className="rsi-canvas-statusbar__progress">
              <span className="rsi-canvas-statusbar__progress-fill" style={{ width: progressPct + '%' }} />
            </span>
          </span>
        </div>
      </div>
      <div className="rsi-legend" data-queued={queued || undefined}>
        {LEGEND.map((item) => (
          <div key={item.kind} className="rsi-legend__row">
            <span className={legendDotClass(item.kind)} />
            {t(item.labelKey)}
          </div>
        ))}
      </div>
    </div>
  );
}

const RSI_EDGE_R = 8;
const RSI_EXIT_OFF = 0;

// rounded orthogonal branch: from parent exit -> junction -> child, with arc corners
function rsiRoundedBranch(px: number, py: number, jx: number, cl: number, cy: number, r: number): string {
  const dy = cy - py;
  if (Math.abs(dy) < 1) return 'M ' + px + ' ' + py + ' H ' + cl;
  const rr = Math.min(r, Math.abs(dy) / 2, (jx - px) / 2, (cl - jx) / 2);
  if (rr < 1) return 'M ' + px + ' ' + py + ' H ' + jx + ' V ' + cy + ' H ' + cl;
  const sgn = dy > 0 ? 1 : -1;
  // corner1 (horizontal -> vertical) and corner2 (vertical -> horizontal-right) need opposite sweep flags
  const sweep1 = sgn > 0 ? 1 : 0;
  const sweep2 = sgn > 0 ? 0 : 1;
  const x1 = jx - rr;
  const y1 = py + sgn * rr;
  const y2 = cy - sgn * rr;
  const x2 = jx + rr;
  return (
    'M ' +
    px +
    ' ' +
    py +
    ' H ' +
    x1 +
    ' A ' +
    rr +
    ' ' +
    rr +
    ' 0 0 ' +
    sweep1 +
    ' ' +
    jx +
    ' ' +
    y1 +
    ' V ' +
    y2 +
    ' A ' +
    rr +
    ' ' +
    rr +
    ' 0 0 ' +
    sweep2 +
    ' ' +
    x2 +
    ' ' +
    cy +
    ' H ' +
    cl
  );
}

function rsiArrowD(cl: number, cy: number): string {
  return 'M ' + cl + ' ' + cy + ' L ' + (cl - 6) + ' ' + (cy - 3) + ' L ' + (cl - 6) + ' ' + (cy + 3) + ' Z';
}

function TreeEdges({ layout, onHoverChange }: { layout: TreeLayout; onHoverChange: (id: string | null) => void }) {
  const groups = useMemo(() => {
    const map = new Map<string, { parent: LayoutNode; children: LayoutNode[] }>();
    for (const e of layout.edges) {
      const key = e.from.node.node_id;
      let g = map.get(key);
      if (!g) {
        g = { parent: e.from, children: [] };
        map.set(key, g);
      }
      g.children.push(e.to);
    }
    return Array.from(map.values());
  }, [layout.edges]);

  return (
    <>
      {groups.map((g) => {
        const px = g.parent.cx + g.parent.width / 2;
        const py = g.parent.cy + RSI_EXIT_OFF;
        const jx = px + 40;
        const childLeft = (c: LayoutNode) => c.cx - c.width / 2;
        const ys = g.children.map((c) => c.cy + RSI_EXIT_OFF);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const spineMin = Math.min(minY, py);
        const spineMax = Math.max(maxY, py);
        const multi = spineMax - spineMin > 1;
        const hoverD =
          'M ' + px + ' ' + py + ' H ' + jx + (multi ? ' M ' + jx + ' ' + spineMin + ' V ' + spineMax : '');
        return (
          <g key={g.parent.node.node_id}>
            <circle cx={px} cy={py} r={4} className="rsi-tree-edge-dot" />
            {g.children.map((c) => {
              const cl = childLeft(c);
              return (
                <g key={c.node.node_id}>
                  <path
                    d={rsiRoundedBranch(px, py, jx, cl, c.cy + RSI_EXIT_OFF, RSI_EDGE_R)}
                    className="rsi-tree-edge"
                  />
                  <path d={rsiArrowD(cl, c.cy + RSI_EXIT_OFF)} className="rsi-tree-edge-arrow" />
                </g>
              );
            })}
            <path
              d={hoverD}
              fill="none"
              stroke="transparent"
              strokeWidth={16}
              style={{ pointerEvents: 'all', cursor: 'pointer' }}
              onMouseEnter={() => onHoverChange(g.parent.node.node_id)}
              onMouseLeave={() => onHoverChange(null)}
            />
          </g>
        );
      })}
    </>
  );
}

// 单个树节点卡片：上层(状态色 + 黑色徽章图标 + 名称 + 状态标签) + 下层(分数行/状态文本 + 展开/收起)
interface RsiNodeCardProps {
  name: string;
  ln: LayoutNode;
  taskRunning: boolean;
  selected: boolean;
  edgeHover: boolean;
  collapsed: boolean;
  scoreExpanded: boolean;
  onToggle: (id: string) => void;
  onToggleScore: (id: string) => void;
  onSelect: (id: string) => void;
}
function RsiNodeCard({
  name,
  ln,
  taskRunning,
  selected,
  edgeHover,
  collapsed,
  scoreExpanded,
  onToggle,
  onToggleScore,
  onSelect,
}: RsiNodeCardProps) {
  const kind = nodeRuntimeKind(ln.node.type, taskRunning);
  const label = nodeRuntimeLabel(kind);
  const icon = runtimeIconKind(kind);

  const scoreLines = nodeScoreLines(ln.node);
  // 折叠态最多 3 行，展开最多 5 行（超出滚动）
  const COLLAPSE_LIMIT = 3;
  const EXPAND_LIMIT = 5;
  const overLimit = scoreLines.length > COLLAPSE_LIMIT;
  const shown = scoreExpanded ? scoreLines.slice(0, EXPAND_LIMIT) : scoreLines.slice(0, COLLAPSE_LIMIT);

  return (
    <div
      className={`rsi-node${selected ? ' rsi-node--selected' : ''}${edgeHover ? ' rsi-node--edge-hover' : ''}`}
      style={{ left: ln.x, top: ln.y, width: ln.width }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(ln.node.node_id);
      }}
      data-testid="rsi-tree-node"
    >
      <div className={`rsi-node__bar ${runtimeKindColorClass(kind)}`}>
        <div className="rsi-node__bar-left">
          <StatusBadge icon={icon} />
          <span className="rsi-node__name">{name}</span>
        </div>
        <span className="rsi-node__status">{label}</span>
      </div>
      <div className="rsi-node__body">
        {kind === 'evaluating' ? (
          <div className="rsi-node__eval-text">{ln.node.summary ?? '正在分析实现'}</div>
        ) : kind === 'pending' ? (
          <div className="rsi-node__score-line">
            <span className="rsi-node__score-num">--</span>
            <span className="rsi-node__score-label">分数</span>
          </div>
        ) : scoreLines.length === 0 && ln.node.type === 'pruned' ? (
          <div className="rsi-node__eval-text">结果不通过</div>
        ) : (
          <>
            {shown.map((sl, i) => (
              <div className="rsi-node__score-line" key={i}>
                <span className="rsi-node__score-num">{sl.value}</span>
                <span className="rsi-node__score-label">{sl.label}</span>
              </div>
            ))}
            {overLimit && (
              <>
                <div className="rsi-node__score-sep" aria-hidden />
                <button
                  type="button"
                  className="rsi-node__score-toggle"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleScore(ln.node.node_id);
                  }}
                >
                  <span>{scoreExpanded ? '收起' : '展开'}</span>
                  <svg
                    viewBox="0 0 24 24"
                    width="12"
                    height="12"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden
                  >
                    {scoreExpanded ? (
                      <path d="M6 15l6-6 6 6" strokeLinecap="round" strokeLinejoin="round" />
                    ) : (
                      <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
                    )}
                  </svg>
                </button>
              </>
            )}
          </>
        )}
      </div>
      {ln.hasChildren && (
        <button
          type="button"
          className={`rsi-node__toggle${collapsed ? ' rsi-node__toggle--collapsed' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(ln.node.node_id);
          }}
          aria-label={collapsed ? 'expand' : 'collapse'}
        >
          {collapsed ? ln.childCount : '−'}
        </button>
      )}
    </div>
  );
}

export function RsiCanvasArea({ task, tree }: RsiCanvasAreaProps) {
  const { t } = useTranslation();
  const selectedNodeId = useRsiStore((s) => s.detail[task.task_id]?.selectedNodeId ?? null);
  const setSelectedNode = useRsiStore((s) => s.setSelectedNode);

  const [scale, setScale] = useState(0.8);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [scoreExpanded, setScoreExpanded] = useState<Set<string>>(new Set());
  const [hoveredEdgeParentId, setHoveredEdgeParentId] = useState<string | null>(null);
  const dragStart = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const fullscreenCanvasRef = useRef<HTMLDivElement>(null);

  const running = task.status === 'running';
  // 排队中（created/queued）：画布展示排队占位，不渲染演进树
  const queued = task.status === 'created' || task.status === 'queued';

  const layout = useMemo(() => {
    if (!tree) return null;
    const roots = tree.nodes.filter((n) => n.parent_id === null);
    return layoutTree(roots, tree.nodes, collapsed, running, scoreExpanded);
  }, [tree, collapsed, running, scoreExpanded]);

  // 切换实验时重置视口
  useEffect(() => {
    setScale(0.8);
    setTx(0);
    setTy(0);
  }, [task.task_id]);

  // 进入全屏时恢复 100% 视口；退出全屏回到普通视图默认 80%
  useEffect(() => {
    setScale(fullscreen ? 1 : 0.8);
    setTx(0);
    setTy(0);
  }, [fullscreen]);

  const clampScale = useCallback((s: number) => Math.min(2, Math.max(0.3, s)), []);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      setDragging(true);
      dragStart.current = { x: e.clientX, y: e.clientY, tx, ty };
    },
    [tx, ty],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging || !dragStart.current) return;
      const dx = e.clientX - dragStart.current.x;
      const dy = e.clientY - dragStart.current.y;
      setTx(dragStart.current.tx + dx);
      setTy(dragStart.current.ty + dy);
    },
    [dragging],
  );

  const handlePointerUp = useCallback(() => {
    setDragging(false);
    dragStart.current = null;
  }, []);

  // 非被动监听滚轮缩放（capture 阶段阻止页面滚动）
  // 主画布与全屏画布各需独立 ref，否则单 ref 会指向后渲染的全屏元素
  useEffect(() => {
    const attach = (el: HTMLDivElement | null) => {
      if (!el) return;
      const onWheel = (e: WheelEvent) => {
        // 鼠标落在可滚动节点内容（如分数多行 overflow）上且未到边界时，
        // 让浏览器默认纵向滚动该内容，画布不缩放；到边界才交给画布缩放。
        let node: HTMLElement | null = e.target instanceof HTMLElement ? e.target : null;
        while (node && node !== el) {
          if (node.scrollHeight > node.clientHeight + 1) {
            const atTop = node.scrollTop <= 0;
            const atBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 1;
            const reaching = e.deltaY > 0 ? atBottom : atTop;
            if (!reaching) return;
            break;
          }
          node = node.parentElement;
        }
        e.preventDefault();
        const rect = el.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const delta = e.deltaY < 0 ? 1.1 : 1 / 1.1;
        setScale((prev) => {
          const next = Math.min(2, Math.max(0.3, prev * delta));
          const k = next / prev;
          setTx((tVal) => cx - (cx - tVal) * k);
          setTy((yVal) => cy - (cy - yVal) * k);
          return next;
        });
      };
      el.addEventListener('wheel', onWheel, { passive: false });
      return () => el.removeEventListener('wheel', onWheel);
    };
    const cleanups = [attach(canvasRef.current), attach(fullscreenCanvasRef.current)];
    return () => cleanups.forEach((fn) => fn && fn());
  }, []);

  const zoomIn = useCallback(() => setScale((s) => clampScale(s * 1.2)), [clampScale]);
  const zoomOut = useCallback(() => setScale((s) => clampScale(s / 1.2)), [clampScale]);
  const zoomReset = useCallback(() => {
    setScale(fullscreen ? 1 : 0.8);
    setTx(0);
    setTy(0);
  }, [fullscreen]);
  // 静默消费 zoomReset：全屏弹窗关闭时重置视口
  void zoomReset;

  const toggleNode = useCallback((nodeId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }, []);
  const toggleScoreExpand = useCallback((nodeId: string) => {
    setScoreExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }, []);

  const title =
    task.scenario === 'artifact' && task.artifact_type === 'program'
      ? t('rsi.detail.programProcess')
      : t('rsi.detail.harnessProcess');

  // 状态条数据：运行态进度/成本来自 P2 推送（liveProgress），回退 task.progress/usage（§3.3/§3.4）
  const liveProgress = useRsiStore((s) => s.detail[task.task_id]?.liveProgress ?? null);
  const provisionalNode = tree?.nodes.find((n) => n.type === 'provisional') ?? null;
  const candidate = provisionalNode
    ? t('rsi.detail.candidate', { defaultValue: '候选' }) + provisionalNode.node_id
    : null;
  const statusInfo = statusBadgeInfo(task.status);
  const statusText = running
    ? provisionalNode
      ? t('rsi.detail.evaluating', { defaultValue: '正在评测' })
      : statusInfo.label
    : statusInfo.label;
  const cost = liveProgress?.usageCost ?? task.usage?.cost_estimate ?? null;
  const progressIter = liveProgress?.iteration ?? task.progress.iteration;
  const progressTotal = liveProgress?.total ?? task.progress.total_iterations;
  const progressPct = progressTotal > 0 ? Math.min(100, Math.round((progressIter / progressTotal) * 100)) : 0;

  return (
    <div className="rsi-canvas-area">
      <div className="rsi-canvas-area__subheader">
        <span className="rsi-canvas-area__title">{title}</span>
        <button type="button" className="rsi-canvas-area__expand" onClick={() => setFullscreen(true)}>
          <svg
            viewBox="0 0 16 16"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M9 2h5v5M7 14H2V9M14 2 9 7M2 14l5-5" />
          </svg>
          {t('rsi.detail.expandView', { defaultValue: '放大查看' })}
        </button>
      </div>
      <div className="rsi-canvas-area__hint">{t('rsi.detail.nodeHint')}</div>

      <div className="rsi-canvas-wrap">
        <RsiCanvasStatusBar
          statusText={statusText}
          candidate={candidate}
          cost={cost}
          progressPct={progressPct}
          queued={queued}
        />
        {queued ? (
          <div className="rsi-canvas-queued">
            {/* 待办任务图标 */}
            <svg
              viewBox="0 0 48 48"
              width="56"
              height="56"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              aria-hidden
            >
              <rect x="10" y="6" width="28" height="36" rx="4" />
              <path d="M18 6V3h12v3" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M16 20h6M16 28h6M16 36h6" strokeLinecap="round" />
              <path d="M28 22l3 3 5-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <div className="rsi-canvas-queued__text">
              本实验<span className="rsi-canvas-queued__strong">正在排队中</span>
            </div>
            <div className="rsi-canvas-queued__sub">下面还有 1 个任务正在优化中，请耐心等待</div>
          </div>
        ) : (
          <div
            ref={canvasRef}
            className={`rsi-canvas${dragging ? ' rsi-canvas--dragging' : ''}`}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
          >
            {layout ? (
              <div
                className="rsi-canvas__inner"
                style={{
                  transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
                  width: layout.width,
                  height: layout.height,
                }}
              >
                {/* 连线：父右 → 子左，水平贝塞尔 */}
                <svg className="rsi-tree-edges" width={layout.width} height={layout.height}>
                  <TreeEdges layout={layout} onHoverChange={setHoveredEdgeParentId} />
                </svg>

                {/* 节点：上层色条（左名称+右状态）+ 下层（图标+分数/摘要） */}
                {layout.nodes.map((ln: LayoutNode) => {
                  const selected = ln.node.node_id === selectedNodeId;
                  const name = nodeDisplayName(ln.node.type, ln.node.node_id, task.scenario, task.artifact_type);
                  return (
                    <RsiNodeCard
                      key={ln.node.node_id}
                      name={name}
                      ln={ln}
                      taskRunning={running}
                      selected={selected}
                      edgeHover={hoveredEdgeParentId === ln.node.node_id}
                      collapsed={collapsed.has(ln.node.node_id)}
                      scoreExpanded={scoreExpanded.has(ln.node.node_id)}
                      onToggle={toggleNode}
                      onToggleScore={toggleScoreExpand}
                      onSelect={setSelectedNode}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="rsi-loading">{t('rsi.list.loading', { defaultValue: '加载中…' })}</div>
            )}
          </div>
        )}

        {/* 缩放控制：+ / − / 定位（复位视口） */}
        <div className="rsi-zoom">
          <button type="button" className="rsi-zoom__btn" onClick={zoomIn} aria-label="zoom in">
            +
          </button>
          <button type="button" className="rsi-zoom__btn" onClick={zoomOut} aria-label="zoom out">
            −
          </button>
          <button
            type="button"
            className="rsi-zoom__btn"
            onClick={zoomReset}
            aria-label="locate"
            title={t('rsi.detail.locate', { defaultValue: '定位' })}
          >
            <svg
              viewBox="0 0 16 16"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden
            >
              <circle cx="8" cy="8" r="5" />
              <circle cx="8" cy="8" r="2" fill="currentColor" stroke="none" />
              <path d="M8 2V0.5M8 14V15.5M2 8H0.5M14 8H15.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {/* 节点选中信息浮层：覆盖画布右侧，点击节点才出现 */}
        {!fullscreen && <RsiSelectedInfo taskId={task.task_id} />}
      </div>

      {/* 全屏画布弹窗 */}
      <dialog
        className="rsi-fullscreen-canvas"
        open={fullscreen}
        onClose={() => setFullscreen(false)}
        onClick={(e) => {
          if (e.target === e.currentTarget) setFullscreen(false);
        }}
        data-testid="rsi-fullscreen-canvas"
      >
        <div className="rsi-fullscreen-canvas__inner">
          <RsiCanvasStatusBar
            statusText={statusText}
            candidate={candidate}
            cost={cost}
            progressPct={progressPct}
            queued={queued}
            onBack={() => setFullscreen(false)}
          />
          <div className="rsi-fullscreen-canvas__content">
            <div className="rsi-canvas-wrap rsi-canvas-wrap--fullscreen">
              <div
                ref={fullscreenCanvasRef}
                className={`rsi-canvas${dragging ? ' rsi-canvas--dragging' : ''}`}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onPointerLeave={handlePointerUp}
              >
                {layout ? (
                  <div
                    className="rsi-canvas__inner"
                    style={{
                      transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
                      width: layout.width,
                      height: layout.height,
                    }}
                  >
                    <svg className="rsi-tree-edges" width={layout.width} height={layout.height}>
                      <TreeEdges layout={layout} onHoverChange={setHoveredEdgeParentId} />
                    </svg>
                    {layout.nodes.map((ln: LayoutNode) => {
                      const selected = ln.node.node_id === selectedNodeId;
                      const name = nodeDisplayName(ln.node.type, ln.node.node_id, task.scenario, task.artifact_type);
                      return (
                        <RsiNodeCard
                          key={ln.node.node_id}
                          name={name}
                          ln={ln}
                          taskRunning={running}
                          selected={selected}
                          edgeHover={hoveredEdgeParentId === ln.node.node_id}
                          collapsed={collapsed.has(ln.node.node_id)}
                          scoreExpanded={scoreExpanded.has(ln.node.node_id)}
                          onToggle={toggleNode}
                          onToggleScore={toggleScoreExpand}
                          onSelect={setSelectedNode}
                        />
                      );
                    })}
                  </div>
                ) : (
                  <div className="rsi-loading">{t('rsi.list.loading', { defaultValue: '加载中…' })}</div>
                )}
              </div>
              <div className="rsi-zoom">
                <button type="button" className="rsi-zoom__btn" onClick={zoomIn} aria-label="zoom in">
                  +
                </button>
                <button type="button" className="rsi-zoom__btn" onClick={zoomOut} aria-label="zoom out">
                  −
                </button>
                <button
                  type="button"
                  className="rsi-zoom__btn"
                  onClick={zoomReset}
                  aria-label="locate"
                  title={t('rsi.detail.locate', { defaultValue: '定位' })}
                >
                  <svg
                    viewBox="0 0 16 16"
                    width="14"
                    height="14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    aria-hidden
                  >
                    <circle cx="8" cy="8" r="5" />
                    <circle cx="8" cy="8" r="2" fill="currentColor" stroke="none" />
                    <path d="M8 2V0.5M8 14V15.5M2 8H0.5M14 8H15.5" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
            </div>
            {fullscreen && <RsiSelectedInfo taskId={task.task_id} />}
          </div>
        </div>
      </dialog>
    </div>
  );
}
