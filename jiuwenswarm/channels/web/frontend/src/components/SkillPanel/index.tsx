/**
 * SkillPanel 组件
 *
 * Skills 管理面板
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from 'react-i18next';
import { ChevronRight, Loader2, Music2 } from 'lucide-react';
import MoreIcon from '../../assets/work-mode/more-rimless.svg?react';
import NewConversationIcon from '../../assets/new_conversation.svg?react';
import { webRequest } from "../../services/webClient";
import { SourceManagerModal } from "../../features/SourceManagerModal";
import { SkillNetSearchModal } from "../../features/SkillNetSearchModal";
import { ClawHubSearchModal } from "../../features/ClawHubSearchModal";
import { TeamSkillsHubModal } from "../../features/TeamSkillsHubModal";
import { OnlineSkillSearchPanel } from "../../features/OnlineSkillSearchPanel";
import { normalizeSkillNetUrl } from "../../utils/skillNetUrl";
import { getSkillAvatar } from "../../utils/skillAvatar";
import { SkillGraphPanel, type SkillGraphPanelHandle } from "../SkillGraphPanel";
import { MarkdownRenderer } from "../MarkdownRenderer";
import { Switch } from "../Switch";
import { coordinateSymphonyEnabledChange } from "./symphonyGraphAction";

/** 刷新会 git pull marketplace，略放宽；普通进页单次 RPC 一般很快。 */
const SKILLS_FETCH_TIMEOUT_REFRESH_MS = 60_000;
const SKILLS_FETCH_TIMEOUT_NORMAL_MS = 30_000;
const SKILL_RETRIEVAL_RUNNING_POLL_MS = 10_000;
const SKILL_RETRIEVAL_IDLE_POLL_MS = 5 * 60_000;
const GRAPH_READING_MIN_VISIBLE_MS = 500;

type SkillItem = {
  name: string;
  /** 展示名（保留安装来源的原始大小写，如 ClawHub 的 Weather）；缺省回退到 name */
  display_name?: string;
  description: string;
  source: string;
  version: string;
  author: string;
  tags: string[];
  allowed_tools: string[];
  marketplace?: string;
  /** SkillNet 等安装来源 URL，与在线搜索 skill_url 对照「已安装」 */
  origin?: string;
  /** 是否为内置技能（不允许删除） */
  is_builtin?: boolean;
  /** 是否为内置技能的来源（源码中存在内置版本） */
  is_builtin_source?: boolean;
  /** 本地技能目录是否存在 evolutions.json */
  has_evolutions?: boolean;
  /** 是否启用 */
  enabled?: boolean;
  /** 是否已安装 */
  installed?: boolean;
  /** 技能文件路径（列表去重 / React key） */
  path?: string;
  /** 技能类型：skill | swarm_skill | multimodal_skill */
  skill_type?: string;
};

type InstalledPluginItem = {
  plugin_name: string;
  marketplace: string;
  spec: string;
  version: string;
  installed_at: string;
  git_commit?: string | null;
  skills: (string | { name: string; version?: string | null })[];
};

type MarketplaceItem = {
  name: string;
  url: string;
  install_location: string;
  last_updated?: string | null;
};

type SkillDetail = SkillItem & {
  content: string;
  file_path: string;
};

type SkillVersion = {
  version: string;
  is_default: boolean;
  source: string;
  available: boolean;
  created_at: string;
  updated_at: string;
};

type SkillVersionsListResponse = {
  success: boolean;
  name: string;
  default_version: string | null;
  versions: SkillVersion[];
};

type SkillFileEntry = {
  path: string;
  type: 'file' | 'directory';
  size: number | null;
  mime_type: string | null;
};

type SkillFilesListResponse = {
  name: string;
  files: SkillFileEntry[];
};

type SkillFilePreview = {
  name: string;
  path: string;
  type: 'file';
  mime_type: string;
  size: number;
  encoding?: string;
  content?: string;
  download_url?: string;
};

type SkillRebuildResponse = {
  success: boolean;
  result_type: 'followup';
  action: string;
  followup_prompt: string;
  skill_name: string;
  rebuild_target: {
    version: string | null;
    is_default: boolean;
    skill_dir: string;
    content_root: string | null;
    swap_workspace: boolean;
  };
};

type EvolutionChange = {
  section?: string;
  action?: string;
  content: string;
  target?: string;
};

type EvolutionEntry = {
  id: string;
  source?: string;
  timestamp?: string;
  context?: string;
  change: EvolutionChange;
  applied?: boolean;
};

type EvolutionGetResponse = {
  exists: boolean;
  valid?: boolean;
  detail?: string;
  entries?: EvolutionEntry[];
};

type LoadState = "idle" | "loading" | "success" | "error";

type SkillRetrievalStatus = {
  enabled?: boolean;
  index_exists?: boolean;
  fresh?: boolean;
  installed_count?: number;
  installed_enabled_count?: number;
  indexed_count?: number;
  built_at?: string;
  index_dir?: string;
  build_status?: string;
  build_stage?: string;
  build_message?: string;
  build_error?: string;
  build_progress?: number;
  build_started_at?: string;
  build_finished_at?: string;
  build_elapsed_seconds?: number;
  build_cancel_requested?: boolean;
  build_logs?: SkillRetrievalBuildLog[];
};

type SkillRetrievalBuildLog = {
  time?: string;
  stage?: string;
  status?: string;
  message?: string;
};

type SkillRetrievalTreeResponse = {
  success?: boolean;
  result?: string;
  nodes?: SkillIndexNode[];
  branch_count?: number;
  leaf_count?: number;
  index_dir?: string;
};

type SkillIndexNode = {
  cid: string;
  parent_cid?: string;
  type?: "branch" | "leaf" | string;
  label?: string;
  description?: string;
  select_when?: string;
  dont_select_when?: string;
  source_description?: string;
  worker_id?: string;
  skill_name?: string;
  category?: string;
  keywords?: string[];
  examples?: string[];
};

type SkillIndexTreeNode = SkillIndexNode & {
  children: SkillIndexTreeNode[];
};

interface SkillPanelProps {
  sessionId: string;
  isConnected: boolean;
  symphonyEnabled: boolean;
  onSymphonyEnabledChange: (enabled: boolean) => Promise<boolean>;
  onNavigateToConfig?: () => void;
  /** 当前是否处于激活状态（左边栏选中技能） */
  isActive?: boolean;
}

/** 与后端一致：tags/allowed_tools 可能是逗号分隔字符串，统一为 string[] */
function coerceStringList(val: unknown): string[] {
  if (val == null) return [];
  if (Array.isArray(val)) {
    return val.map((x) => String(x).trim()).filter(Boolean);
  }
  if (typeof val === "string") {
    const s = val.trim();
    if (!s) return [];
    return s.includes(",")
      ? s.split(",").map((p) => p.trim()).filter(Boolean)
      : [s];
  }
  return [String(val)];
}

function normalizeSkillItem<T extends SkillItem>(raw: T): T {
  return {
    ...raw,
    tags: coerceStringList(raw.tags),
    allowed_tools: coerceStringList(raw.allowed_tools),
  };
}

function buildSkillIndexTree(nodes: SkillIndexNode[]): SkillIndexTreeNode[] {
  const map = new Map<string, SkillIndexTreeNode>();
  nodes.forEach((node) => {
    const cid = String(node.cid || "").trim();
    if (!cid) return;
    map.set(cid, { ...node, cid, children: [] });
  });

  const roots: SkillIndexTreeNode[] = [];
  map.forEach((node) => {
    const parentCid = String(node.parent_cid || "").trim();
    const parent = parentCid ? map.get(parentCid) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  });

  const sortNodes = (items: SkillIndexTreeNode[]) => {
    items.sort((a, b) => {
      const aType = a.type === "leaf" ? 1 : 0;
      const bType = b.type === "leaf" ? 1 : 0;
      if (aType !== bType) return aType - bType;
      return getSkillIndexNodeLabel(a).localeCompare(getSkillIndexNodeLabel(b));
    });
    items.forEach((item) => sortNodes(item.children));
  };
  sortNodes(roots);
  return roots;
}

function getSkillIndexNodeLabel(node: SkillIndexNode): string {
  return String(node.label || node.worker_id || node.cid || "").trim() || "node";
}

function getSkillIndexSkillName(node: SkillIndexNode): string {
  return String(node.skill_name || node.worker_id || node.label || "").trim();
}

function getSkillIndexNodeClassName(disabledLeaf: boolean, selected: boolean): string {
  if (disabledLeaf) {
    return selected
      ? "border-zinc-400/40 bg-zinc-500/10 text-text-muted"
      : "border-transparent text-text-muted opacity-75 hover:bg-secondary/50";
  }
  if (selected) {
    return "border-accent/40 bg-accent/10 text-accent";
  }
  return "border-transparent text-text hover:bg-secondary/60";
}

function getSkillIndexNodeBadgeClassName(disabledLeaf: boolean, isLeaf: boolean): string {
  if (disabledLeaf) {
    return "border-zinc-400/25 bg-zinc-500/10 text-text-muted";
  }
  if (isLeaf) {
    return "border-emerald-500/25 bg-emerald-500/10 text-emerald-600";
  }
  return "border-sky-500/25 bg-sky-500/10 text-sky-600";
}

function findSkillIndexNode(nodes: SkillIndexNode[], cid: string | null): SkillIndexNode | null {
  if (!cid) return null;
  return nodes.find((node) => node.cid === cid) || null;
}

type SkillIndexBuildPhaseState = "done" | "active" | "pending" | "failed" | "cancelled";

type SkillIndexBuildPhase = {
  key: string;
  title: string;
  detail: string;
  state: SkillIndexBuildPhaseState;
};

function getSkillIndexBuildStageLabel(
  stage: string | undefined,
  t: (key: string, options?: Record<string, unknown>) => string
): string {
  const key = String(stage || "").trim();
  if (!key) return t('skills.retrieval.buildStageUnknown');
  const known: Record<string, string> = {
    queued: 'queued',
    scan: 'scan',
    llm_check: 'llmCheck',
    build: 'buildTree',
    publish: 'publish',
    reuse: 'reuse',
    success: 'success',
    failed: 'failed',
    timeout: 'timeout',
    llm_config: 'llmConfig',
    cancelled: 'cancelled',
    interrupted: 'interrupted',
  };
  const mapped = known[key];
  return mapped ? t(`skills.retrieval.buildStages.${mapped}`) : key;
}

function getSkillIndexBuildPhaseState(
  phaseKey: string,
  currentStage: string,
  buildStatus: string
): SkillIndexBuildPhaseState {
  const order = ["queued", "scan", "llm_check", "build", "publish", "success"];
  const normalizedStage = order.includes(currentStage)
    ? currentStage
    : currentStage === "llm_config"
    ? "llm_check"
    : ["failed", "timeout", "interrupted", "cancelled"].includes(currentStage)
    ? "build"
    : buildStatus === "success"
    ? "success"
    : "queued";
  const currentIndex = order.indexOf(normalizedStage);
  const phaseIndex = order.indexOf(phaseKey);
  if (buildStatus === "failed") {
    if (phaseKey === normalizedStage) return "failed";
    if (phaseIndex < currentIndex) return "done";
    return "pending";
  }
  if (buildStatus === "cancelled") {
    if (phaseKey === normalizedStage) return "cancelled";
    if (phaseIndex < currentIndex) return "done";
    return "pending";
  }
  if (buildStatus === "success") return "done";
  if (phaseIndex < currentIndex) return "done";
  if (phaseIndex === currentIndex) return "active";
  return "pending";
}

function buildSkillIndexBuildPhases(
  status: SkillRetrievalStatus | null,
  t: (key: string, options?: Record<string, unknown>) => string
): SkillIndexBuildPhase[] {
  const buildStatus = String(status?.build_status || "idle");
  const currentStage = String(status?.build_stage || (buildStatus === "success" ? "success" : "queued"));
  const installedCount = status?.installed_count ?? status?.installed_enabled_count ?? 0;
  const indexedCount = status?.indexed_count ?? 0;
  const base = [
    {
      key: "queued",
      title: t('skills.retrieval.buildPipeline.queued.title'),
      detail: t('skills.retrieval.buildPipeline.queued.detail'),
    },
    {
      key: "scan",
      title: t('skills.retrieval.buildPipeline.scan.title'),
      detail: t('skills.retrieval.buildPipeline.scan.detail', { count: installedCount }),
    },
    {
      key: "llm_check",
      title: t('skills.retrieval.buildPipeline.llmCheck.title'),
      detail: t('skills.retrieval.buildPipeline.llmCheck.detail'),
    },
    {
      key: "build",
      title: t('skills.retrieval.buildPipeline.build.title'),
      detail: t('skills.retrieval.buildPipeline.build.detail'),
    },
    {
      key: "publish",
      title: t('skills.retrieval.buildPipeline.publish.title'),
      detail: t('skills.retrieval.buildPipeline.publish.detail'),
    },
    {
      key: "success",
      title: t('skills.retrieval.buildPipeline.success.title'),
      detail: t('skills.retrieval.buildPipeline.success.detail', { count: indexedCount || installedCount }),
    },
  ];
  return base.map((phase) => ({
    ...phase,
    state: getSkillIndexBuildPhaseState(phase.key, currentStage, buildStatus),
  }));
}

function getBuildPhaseClass(state: SkillIndexBuildPhaseState): string {
  if (state === "done") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-600";
  if (state === "active") return "border-sky-500/40 bg-sky-500/10 text-sky-600";
  if (state === "failed") return "border-red-500/35 bg-red-500/10 text-red-600";
  if (state === "cancelled") return "border-amber-500/35 bg-amber-500/10 text-amber-600";
  return "border-border bg-secondary/30 text-text-muted";
}

function SkillIndexBuildProgressPanel({
  status,
  progress,
  logs,
  t,
}: {
  status: SkillRetrievalStatus | null;
  progress: number;
  logs: SkillRetrievalBuildLog[];
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const phases = buildSkillIndexBuildPhases(status, t);
  const stageLabel = getSkillIndexBuildStageLabel(status?.build_stage, t);
  const isError = status?.build_status === "failed";
  const showPipeline = status?.build_status !== "success";
  return (
    <div className="mt-4 rounded-lg border border-border bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[220px]">
          <div className="text-sm font-medium text-text-strong">
            {t('skills.retrieval.buildMonitorTitle')}
          </div>
          <div className="mt-1 text-xs text-text-muted">
            {t('skills.retrieval.buildMonitorSubtitle', { stage: stageLabel })}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="rounded-md border border-border bg-secondary/40 px-3 py-2">
            <div className="text-text-muted">{t('skills.retrieval.buildMetric.progress')}</div>
            <div className="mt-1 font-medium text-text-strong">{progress}%</div>
          </div>
          <div className="rounded-md border border-border bg-secondary/40 px-3 py-2">
            <div className="text-text-muted">{t('skills.retrieval.buildMetric.skills')}</div>
            <div className="mt-1 font-medium text-text-strong">
              {status?.installed_count ?? status?.installed_enabled_count ?? 0}
            </div>
          </div>
          <div className="rounded-md border border-border bg-secondary/40 px-3 py-2">
            <div className="text-text-muted">{t('skills.retrieval.buildMetric.indexed')}</div>
            <div className="mt-1 font-medium text-text-strong">{status?.indexed_count ?? 0}</div>
          </div>
        </div>
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-secondary">
        <div
          className={`h-full rounded-full  ${isError ? "bg-red-500" : "bg-emerald-500"}`}
          style={{ width: `${progress}%` }}
        />
      </div>

      {showPipeline ? (
        <div className="mt-4 grid gap-4">
          <div className="rounded-md border border-border bg-secondary/30 p-3">
            <div className="mb-3 text-xs font-medium uppercase tracking-wide text-text-muted">
              {t('skills.retrieval.buildPipelineTitle')}
            </div>
            <div className="space-y-2">
              {phases.map((phase, index) => (
                <div key={phase.key} className={`rounded-md border px-3 py-2 ${getBuildPhaseClass(phase.state)}`}>
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-current text-[10px]">
                      {index + 1}
                    </span>
                    <span className="min-w-0 truncate text-xs font-medium">{phase.title}</span>
                    <span className="ml-auto text-[10px] uppercase opacity-70">
                      {t(`skills.retrieval.buildPhaseState.${phase.state}`)}
                    </span>
                  </div>
                  <div className="mt-1 pl-7 text-[11px] leading-5 opacity-80">{phase.detail}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
      {status?.build_message ? (
        <div className="mt-3 rounded-md border border-border bg-secondary/30 px-3 py-2 text-xs text-text-muted">
          {status.build_message}
        </div>
      ) : null}
      {status?.build_error ? (
        <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-red-500/20 bg-red-500/5 p-2 text-xs text-red-600">
          {status.build_error}
        </pre>
      ) : null}
      {logs.length > 0 ? (
        <div className="mt-3 grid gap-1 text-[11px] text-text-muted">
          {logs.slice(-5).map((log, index) => (
            <div key={`${log.time || index}-${log.stage || ""}`} className="flex min-w-0 gap-2">
              <span className="shrink-0 font-mono text-text-muted/70">[{log.stage || "-"}]</span>
              <span className="min-w-0 truncate">{log.message || log.status || ""}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SkillIndexTreeView({
  roots,
  selectedCid,
  onSelect,
  emptyText,
  branchLabel,
  skillLabel,
  disabledSkillNames,
  disabledSkillLabel,
}: {
  roots: SkillIndexTreeNode[];
  selectedCid: string | null;
  onSelect: (cid: string) => void;
  emptyText: string;
  branchLabel: string;
  skillLabel: string;
  disabledSkillNames: Set<string>;
  disabledSkillLabel: string;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const next: Record<string, boolean> = {};
    const walk = (items: SkillIndexTreeNode[], depth: number) => {
      items.forEach((item) => {
        if (item.children.length > 0 && depth < 2) {
          next[item.cid] = true;
        }
        walk(item.children, depth + 1);
      });
    };
    walk(roots, 0);
    setExpanded(next);
  }, [roots]);

  const renderNode = (node: SkillIndexTreeNode, depth: number): ReactNode => {
    const hasChildren = node.children.length > 0;
    const isExpanded = expanded[node.cid] ?? false;
    const selected = selectedCid === node.cid;
    const isLeaf = node.type === "leaf";
    const disabledLeaf = isLeaf && disabledSkillNames.has(getSkillIndexSkillName(node));
    return (
      <div key={node.cid}>
        <div
          role="treeitem"
          aria-selected={selected}
          aria-expanded={hasChildren ? isExpanded : undefined}
          className={`flex items-center gap-1 rounded-md border text-xs  ${
            getSkillIndexNodeClassName(disabledLeaf, selected)
          }`}
          style={{ paddingLeft: `${8 + depth * 14}px` }}
        >
          <button
            type="button"
            onClick={() => {
              if (hasChildren) {
                setExpanded((prev) => ({ ...prev, [node.cid]: !isExpanded }));
              }
            }}
            className={`h-7 w-5 shrink-0 flex items-center justify-center rounded ${
              hasChildren ? "text-text-muted hover:text-text" : "text-text-muted/50 cursor-default"
            }`}
            aria-label={hasChildren ? (isExpanded ? "Collapse" : "Expand") : undefined}
          >
            {hasChildren ? (
              <ChevronRight
                className={`h-3 w-3  ${isExpanded ? "rotate-90" : ""}`}
                strokeWidth={2}
              />
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-current opacity-50" />
            )}
          </button>
          <button
            type="button"
            onClick={() => onSelect(node.cid)}
            className="min-w-0 flex-1 min-h-7 py-1 flex items-center gap-2 text-left"
            title={getSkillIndexNodeLabel(node)}
          >
            <span
              className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] leading-none ${
                getSkillIndexNodeBadgeClassName(disabledLeaf, isLeaf)
              }`}
            >
              {isLeaf ? skillLabel : branchLabel}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate">{getSkillIndexNodeLabel(node)}</span>
              {disabledLeaf ? (
                <span className="block truncate text-[10px] leading-4 text-text-muted">
                  {disabledSkillLabel}
                </span>
              ) : null}
            </span>
          </button>
        </div>
        {hasChildren && isExpanded ? (
          <div className="mt-1 space-y-1">
            {node.children.map((child) => renderNode(child, depth + 1))}
          </div>
        ) : null}
      </div>
    );
  };

  if (roots.length === 0) {
    return <div className="text-sm text-text-muted">{emptyText}</div>;
  }

  return <div className="space-y-1" role="tree">{roots.map((node) => renderNode(node, 0))}</div>;
}

interface MarketplacePluginItem {
  asset_id: string;
  name: string;
  display_name?: string | null;
  short_desc?: string | null;
  detail_desc?: string | null;
  icon_uri?: string | null;
  publisher_name: string;
  tags?: string[] | null;
  plugin_type?: string | null;
  category_id?: string | null;
  category_name?: string | null;
  latest_version?: string | null;
  install_count: number;
  like_count: number;
  view_count: number;
  moderation_status?: string | null;
}

interface HubPluginListResponse {
  code: number;
  message: string;
  data: {
    page: number;
    page_size: number;
    total: number;
    items: MarketplacePluginItem[];
  };
}

const MARKETPLACE_CATEGORIES = ["all", "software-development", "office-productivity", "content-creation", "multimodal-media", "data-science-research", "compliance-legal", "lifestyle-health", "finance-wealth"] as const;
const SKILL_TYPES = ["skill", "team", "multimodal"] as const;

/**
 * 将技能内容中的图片路径转换为 /file-api/raw-file 可访问的 URL。
 * 处理两种情况：
 * 1. 相对路径（references/img_00.png）→ 直接拼接技能目录
 * 2. 后端改写的 /file-api/download?token=xxx → 解码 token 获取 relative_path
 * skillFilePath 为 SKILL.md 的绝对路径，用于推导技能所在目录。
 */
function transformSkillContentImages(content: string, skillFilePath: string): string {
  if (!content || !skillFilePath) return content;
  const lastSlash = Math.max(skillFilePath.lastIndexOf('/'), skillFilePath.lastIndexOf('\\'));
  const skillDir = lastSlash >= 0 ? skillFilePath.substring(0, lastSlash).replace(/\\/g, '/') : '';

  const toRawFileUrl = (relativePath: string): string => {
    const fullPath = skillDir ? `${skillDir}/${relativePath}`.replace(/\\/g, '/') : relativePath;
    return `/file-api/raw-file?path=${encodeURIComponent(fullPath)}`;
  };

  return content.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, imgPath: string) => {
    // 跳过外部 URL 和 data URI
    if (/^(https?:|data:|file:\/\/\/)/.test(imgPath)) return match;

    // 处理后端改写的 /file-api/download?token=xxx URL
    if (imgPath.startsWith('/file-api/download?token=')) {
      try {
        const token = new URL(imgPath, 'http://localhost').searchParams.get('token') || '';
        const payloadB64 = token.split('.')[0];
        const payload = JSON.parse(atob(payloadB64));
        if (payload.relative_path) {
          return `![${alt}](${toRawFileUrl(payload.relative_path)})`;
        }
      } catch {
        // 解码失败，保留原 URL
      }
      return match;
    }

    // 处理相对路径
    if (imgPath.startsWith('/file-api/')) return match;
    return `![${alt}](${toRawFileUrl(imgPath)})`;
  });
}

export function SkillPanel({
  sessionId,
  isConnected,
  symphonyEnabled,
  onSymphonyEnabledChange,
  onNavigateToConfig,
  isActive = false,
}: SkillPanelProps) {
  const { t, i18n } = useTranslation();
  const [activeTab, setActiveTab] = useState<"my" | "marketplace" | "index" | "graph">("my");
  const [mySkillsSubTab, setMySkillsSubTab] = useState<"all" | "enabled" | "disabled">("enabled");
  const [mySkillsPublishFilter, setMySkillsPublishFilter] = useState<"all" | "published" | "unpublished">("all");
  const [marketplaceCategory, setMarketplaceCategory] = useState<"all" | "software-development" | "office-productivity" | "content-creation" | "multimodal-media" | "data-science-research" | "compliance-legal" | "lifestyle-health" | "finance-wealth">("all");
  const [marketplaceSubTab, setMarketplaceSubTab] = useState<"skillhub" | "swarmskills" | "online">("skillhub");
  const [searchTrigger, setSearchTrigger] = useState(0);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const searchDebounceRef = useRef<number | null>(null);
  const [skillType, setSkillType] = useState<"skill" | "team" | "multimodal" | null>(() => {
    const stored = localStorage.getItem('skillPanel.skillType');
    if (stored === "skill" || stored === "team" || stored === "multimodal") return stored;
    return null;
  });
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [plugins, setPlugins] = useState<InstalledPluginItem[]>([]);
  const [marketplaces, setMarketplaces] = useState<MarketplaceItem[]>([]);
  const [hubSkills, setHubSkills] = useState<MarketplacePluginItem[]>([]);
  const [hubLoading, setHubLoading] = useState(false);
  const [search, setSearch] = useState("");
  const prevIsActiveRef = useRef(isActive);
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [actionTarget, setActionTarget] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"success" | "error" | "loading" | null>(null);
  const messageTimerRef = useRef<number | null>(null);
  const retrievalPollRef = useRef<number | null>(null);
  const retrievalDiscoveryPollRef = useRef<number | null>(null);
  const retrievalStatusRequestRef = useRef(0);
  const skillGraphPanelRef = useRef<SkillGraphPanelHandle | null>(null);
  const graphReadingStartedAtRef = useRef<number | null>(null);
  const graphReadingTimerRef = useRef<number | null>(null);
  const evolutionSaveTimerRef = useRef<number | null>(null);
  const [graphReading, setGraphReading] = useState(false);
  const [symphonyEnabledDraft, setSymphonyEnabledDraft] = useState(symphonyEnabled);
  const [symphonySaving, setSymphonySaving] = useState(false);
  const [symphonySaveError, setSymphonySaveError] = useState<string | null>(null);
  const [graphActionError, setGraphActionError] = useState<string | null>(null);
  const [openMenuSkillName, setOpenMenuSkillName] = useState<string | null>(null);
  const [pinnedSkillNames, setPinnedSkillNames] = useState<Set<string>>(new Set());
  const [retrievalStatus, setRetrievalStatus] = useState<SkillRetrievalStatus | null>(null);
  const [retrievalTree, setRetrievalTree] = useState("");
  const [retrievalTreeNodes, setRetrievalTreeNodes] = useState<SkillIndexNode[]>([]);
  const [retrievalTreeCounts, setRetrievalTreeCounts] = useState({ branches: 0, skills: 0 });
  const [selectedTreeNodeCid, setSelectedTreeNodeCid] = useState<string | null>(null);
  const [retrievalShowExistingIndexFailureNotice, setRetrievalShowExistingIndexFailureNotice] = useState(false);
  const [retrievalLoading, setRetrievalLoading] = useState<"idle" | "status" | "tree" | "build" | "cancel">("idle");

  // 持久化技能类型胶囊选中状态
  useEffect(() => {
    localStorage.setItem('skillPanel.skillType', skillType ?? '');
  }, [skillType]);

  useEffect(() => {
    return () => {
      if (messageTimerRef.current !== null) {
        window.clearTimeout(messageTimerRef.current);
      }
      if (retrievalPollRef.current !== null) {
        window.clearInterval(retrievalPollRef.current);
      }
      if (retrievalDiscoveryPollRef.current !== null) {
        window.clearInterval(retrievalDiscoveryPollRef.current);
      }
      if (graphReadingTimerRef.current !== null) {
        window.clearTimeout(graphReadingTimerRef.current);
      }
      if (searchDebounceRef.current !== null) {
        window.clearTimeout(searchDebounceRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!symphonySaving) {
      setSymphonyEnabledDraft(symphonyEnabled);
    }
  }, [symphonyEnabled, symphonySaving]);

  const clearGraphActionError = useCallback(() => {
    setGraphActionError(null);
  }, []);

  const updateSymphonyEnabled = useCallback(async (enabled: boolean) => {
    if (!isConnected || symphonySaving || enabled === symphonyEnabledDraft) return;
    setSymphonyEnabledDraft(enabled);
    setSymphonySaving(true);
    setSymphonySaveError(null);
    const result = await coordinateSymphonyEnabledChange({
      enabled,
      save: onSymphonyEnabledChange,
      getGraphPanel: () => skillGraphPanelRef.current,
      request: webRequest,
      refreshFailedMessage: t('skills.graph.errors.refreshFailed'),
      cancelFailedMessage: t('skills.graph.errors.cancelFailed'),
      onGraphActionStart: clearGraphActionError,
    });
    if (result.configSaveFailed) {
      setSymphonyEnabledDraft(symphonyEnabled);
      setSymphonySaveError(t('skills.graph.orchestration.saveFailed'));
      setSymphonySaving(false);
      return;
    }
    if (!result.appliedWithoutRestart) {
      setSymphonySaving(false);
      return;
    }
    if (result.graphActionError) {
      setGraphActionError(result.graphActionError);
    }
    setSymphonySaving(false);
  }, [clearGraphActionError, isConnected, onSymphonyEnabledChange, symphonyEnabled, symphonyEnabledDraft, symphonySaving, t]);

  const updateGraphReading = useCallback((reading: boolean) => {
    if (graphReadingTimerRef.current !== null) {
      window.clearTimeout(graphReadingTimerRef.current);
      graphReadingTimerRef.current = null;
    }
    if (reading) {
      graphReadingStartedAtRef.current = Date.now();
      setGraphReading(true);
      return;
    }
    const startedAt = graphReadingStartedAtRef.current;
    graphReadingStartedAtRef.current = null;
    const elapsed = startedAt == null ? GRAPH_READING_MIN_VISIBLE_MS : Date.now() - startedAt;
    const delay = Math.max(0, GRAPH_READING_MIN_VISIBLE_MS - elapsed);
    if (delay === 0) {
      setGraphReading(false);
      return;
    }
    graphReadingTimerRef.current = window.setTimeout(() => {
      graphReadingTimerRef.current = null;
      setGraphReading(false);
    }, delay);
  }, []);

  const showMessage = useCallback((type: "success" | "error" | "loading", text: string) => {
    if (messageTimerRef.current !== null) {
      window.clearTimeout(messageTimerRef.current);
      messageTimerRef.current = null;
    }
    const displayText = type === "success" ? `√ ${text}` : text;
    setMessage(displayText);
    setMessageType(type);
    // loading 持续到下一次消息；错误信息显示时间更长（8秒）
    if (type === "loading") {
      return;
    }
    const duration = type === "error" ? 8000 : 3000;
    messageTimerRef.current = window.setTimeout(() => {
      setMessage(null);
      setMessageType(null);
      messageTimerRef.current = null;
    }, duration);
  }, []);
  const [sourceModalOpen, setSourceModalOpen] = useState(false);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [uploadSkillModalOpen, setUploadSkillModalOpen] = useState(false);
  const [docToSkillModalOpen, setDocToSkillModalOpen] = useState(false);
  const [uploadSkillPath, setUploadSkillPath] = useState("");
  const [docToSkillPath, setDocToSkillPath] = useState("");
  const [docToSkillSource, setDocToSkillSource] = useState<"local" | "link">("local");
  const [docToSkillLink, setDocToSkillLink] = useState("");
  const [docToSkillDesc, setDocToSkillDesc] = useState("");
  const [docToSkillTooltip, setDocToSkillTooltip] = useState<{ left: number; top: number } | null>(null);
  const [publishFilterOpen, setPublishFilterOpen] = useState(false);
  const [enableFilterOpen, setEnableFilterOpen] = useState(false);
  const [skillNetModalOpen, setSkillNetModalOpen] = useState(false);
  const [clawHubModalOpen, setClawHubModalOpen] = useState(false);
  const [teamSkillsHubModalOpen, setTeamSkillsHubModalOpen] = useState(false);
  const [synthesizeTooltip, setSynthesizeTooltip] = useState<{ left: number; top: number } | null>(null);
  const [goTryTooltip, setGoTryTooltip] = useState<{ left: number; top: number } | null>(null);
  const [detailMenuOpen, setDetailMenuOpen] = useState(false);
  const [publishDrawerOpen, setPublishDrawerOpen] = useState(false);
  const [publishName, setPublishName] = useState("");
  const [publishSkillName, setPublishSkillName] = useState("");
  const [publishVersion, setPublishVersion] = useState("");
  const [publishDisplayName, setPublishDisplayName] = useState("");
  const [publishSha256, setPublishSha256] = useState("");
  const [publishNoticeVisible, setPublishNoticeVisible] = useState(true);
  const [detailTab, setDetailTab] = useState<"content" | "files" | "experience">("content");
  const [evolutionEntries, setEvolutionEntries] = useState<EvolutionEntry[]>([]);
  const [evolutionListState, setEvolutionListState] = useState<LoadState>("idle");
  const [, setEvolutionSaving] = useState(false);
  const [evolutionMessage, setEvolutionMessage] = useState<string | null>(null);
  const [evolutionMessageType, setEvolutionMessageType] = useState<"success" | "error" | null>(null);
  const [evolutionFormatError, setEvolutionFormatError] = useState<string | null>(null);
  const [skillVersions, setSkillVersions] = useState<SkillVersion[]>([]);
  const [skillVersionsDefault, setSkillVersionsDefault] = useState<string | null>(null);
  const [versionsLoadState, setVersionsLoadState] = useState<LoadState>("idle");
  const [skillFiles, setSkillFiles] = useState<SkillFileEntry[]>([]);
  const [filesLoadState, setFilesLoadState] = useState<LoadState>("idle");
  const [filePreview, setFilePreview] = useState<SkillFilePreview | null>(null);
  const [filePreviewPath, setFilePreviewPath] = useState<string | null>(null);
  const [expandedFileFolders, setExpandedFileFolders] = useState<Set<string>>(new Set([""]));
  const [rebuildLoading, setRebuildLoading] = useState(false);
  const withSession = useCallback(
    (params?: Record<string, unknown>) => ({
      ...(params || {}),
      session_id: sessionId,
    }),
    [sessionId]
  );

  const installedSkillMap = useMemo(() => {
    const map = new Map<string, InstalledPluginItem>();
    plugins.forEach((plugin) => {
      plugin.skills.forEach((skill) => {
        const skillName = typeof skill === 'string' ? skill : skill.name;
        if (!map.has(skillName)) {
          map.set(skillName, plugin);
        }
      });
    });
    return map;
  }, [plugins]);

  const installedSkillNames = useMemo(
    () => new Set(installedSkillMap.keys()),
    [installedSkillMap]
  );

  /** 已安装技能的来源 URL（规范化），与 SkillNet 搜索结果的 skill_url 匹配 */
  const installedSkillOrigins = useMemo(() => {
    const set = new Set<string>();
    for (const s of skills) {
      const o = s.origin?.trim();
      if (o) {
        set.add(normalizeSkillNetUrl(o));
      }
    }
    return set;
  }, [skills]);

  const filteredSkills = useMemo(() => {
    let result = skills;
    if (activeTab === "my") {
      result = result.filter((skill) => 
        installedSkillMap.has(skill.name) || 
        skill.source === "local" || 
        skill.source === "project" ||
        skill.is_builtin === true || 
        skill.is_builtin_source === true
      );
    }
    if (skillType === "team") {
      result = result.filter((skill) => skill.skill_type === "swarm_skill");
    } else if (skillType === "multimodal") {
      result = result.filter((skill) => skill.skill_type === "multimodal_skill");
    } else if (skillType === "skill") {
      result = result.filter((skill) => !skill.skill_type || skill.skill_type === "skill");
    }
    const keyword = search.trim().toLowerCase();
    if (!keyword) return result;
    return result.filter((skill) => {
      const haystack = [
        skill.name,
        skill.display_name,
        skill.description,
        skill.author,
        coerceStringList(skill.tags).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [skills, search, activeTab, installedSkillMap, skillType]);

  const visibleSkills = useMemo(() => {
    let filtered = [...filteredSkills];
    if (activeTab === "my") {
      filtered = filtered.filter((skill) => {
        if (skill.is_builtin_source && !installedSkillMap.has(skill.name) && skill.source !== "local") {
          return false;
        }
        return true;
      });
    }
    // 同名去重（防御 frontmatter/目录不一致导致的重复项）；保留先出现的条目
    const seen = new Set<string>();
    filtered = filtered.filter((skill) => {
      const key = skill.name;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return filtered.sort((a, b) => {
      const aSkillNet = a.source === "skillnet" ? 1 : 0;
      const bSkillNet = b.source === "skillnet" ? 1 : 0;
      if (aSkillNet !== bSkillNet) {
        return bSkillNet - aSkillNet;
      }
      return a.name.localeCompare(b.name);
    });
  }, [filteredSkills, activeTab, installedSkillMap]);

  const fetchHubSkills = useCallback(async (category: string, searchKeyword?: string) => {
    setHubLoading(true);
    try {
      const params = new URLSearchParams({
        plugin_type: 'skill',
        page: '1',
        page_size: '100',
        order_by: 'install_count',
        desc: 'true',
      });
      if (category !== 'all') {
        params.set('category_id', category);
      }
      if (searchKeyword?.trim()) {
        params.set('search_keyword', searchKeyword.trim());
      }
      const resp = await fetch(`/skillhub-api/plugins?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json: HubPluginListResponse = await resp.json();
      if (json.code !== 200) throw new Error(json.message || 'API error');
      setHubSkills(json.data.items);
    } catch (error) {
      console.error('Failed to fetch SkillHub skills:', error);
      setHubSkills([]);
    } finally {
      setHubLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'marketplace' && marketplaceSubTab === 'skillhub') {
      fetchHubSkills(marketplaceCategory, search);
    }
  }, [activeTab, marketplaceSubTab, marketplaceCategory, search, fetchHubSkills]);

  useEffect(() => {
    if (searchDebounceRef.current !== null) {
      window.clearTimeout(searchDebounceRef.current);
    }
    searchDebounceRef.current = window.setTimeout(() => {
      setDebouncedSearch(search);
      searchDebounceRef.current = null;
    }, 300);
    return () => {
      if (searchDebounceRef.current !== null) {
        window.clearTimeout(searchDebounceRef.current);
      }
    };
  }, [search]);

  const fetchMarketplaces = useCallback(async () => {
    try {
      const data = await webRequest<{ marketplaces?: MarketplaceItem[] }>(
        "skills.marketplace.list",
        withSession()
      );
      setMarketplaces(data.marketplaces || []);
    } catch (error) {
      console.error('Failed to load marketplaces:', error);
    }
  }, []);

  const fetchSkills = useCallback(async (refreshMarketplaces = false) => {
    setListState("loading");
    try {
      const data = await webRequest<{
        skills?: SkillItem[];
        plugins?: InstalledPluginItem[];
      }>(
        "skills.list",
        {
          with_installed: true,
          ...(refreshMarketplaces ? { refresh_marketplaces: true } : {}),
        },
        {
          timeoutMs: refreshMarketplaces
            ? SKILLS_FETCH_TIMEOUT_REFRESH_MS
            : SKILLS_FETCH_TIMEOUT_NORMAL_MS,
        }
      );
      setSkills((data.skills || []).map(normalizeSkillItem));
      setPlugins(data.plugins || []);
      setListState("success");

      fetchMarketplaces();
    } catch (error) {
      console.error(error);
      setListState("error");
    }
  }, [fetchMarketplaces, withSession]);

  const fetchSkillDetail = useCallback(
    async (skillName: string) => {
      setDetailState("loading");
      try {
        const data = await webRequest<SkillDetail>(
          "skills.get",
          withSession({ name: skillName })
        );
        setSelectedSkill(normalizeSkillItem(data));
        setDetailTab("content");
        setDetailState("success");
        setFilePreview(null);
        setFilePreviewPath(null);
      } catch (error) {
        console.error(error);
        setDetailState("error");
      }
    },
    [withSession]
  );

  const fetchSkillVersions = useCallback(
    async (skillName: string) => {
      setVersionsLoadState("loading");
      try {
        const data = await webRequest<SkillVersionsListResponse>(
          "skills.versions.list",
          withSession({ name: skillName })
        );
        setSkillVersions(data.versions || []);
        setSkillVersionsDefault(data.default_version);
        setVersionsLoadState("success");
      } catch (error) {
        console.error(error);
        setVersionsLoadState("error");
      }
    },
    [withSession]
  );

  const fetchSkillFiles = useCallback(
    async (skillName: string) => {
      setFilesLoadState("loading");
      try {
        const data = await webRequest<SkillFilesListResponse>(
          "skills.files.list",
          withSession({ name: skillName })
        );
        setSkillFiles(data.files || []);
        setFilesLoadState("success");
      } catch (error) {
        console.error(error);
        setFilesLoadState("error");
      }
    },
    [withSession]
  );

  const fetchFilePreview = useCallback(
    async (skillName: string, filePath: string) => {
      try {
        const data = await webRequest<SkillFilePreview>(
          "skills.files.get",
          withSession({ name: skillName, path: filePath })
        );
        setFilePreview(data);
        setFilePreviewPath(filePath);
      } catch (error) {
        console.error(error);
        setFilePreview(null);
      }
    },
    [withSession]
  );

  // ---- 文件树构建 ----
  interface FileTreeNode {
    name: string;
    path: string;
    type: 'file' | 'directory';
    size: number | null;
    mime_type: string | null;
    children: FileTreeNode[];
  }

  const skillFileTree = useMemo(() => {
    const root: FileTreeNode = { name: '', path: '', type: 'directory', size: null, mime_type: null, children: [] };
    const dirMap = new Map<string, FileTreeNode>();
    dirMap.set('', root);
    for (const entry of skillFiles) {
      const parts = entry.path.split('/').filter(Boolean);
      let currentPath = '';
      let currentNode = root;
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const isLast = i === parts.length - 1;
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        if (isLast && entry.type === 'file') {
          currentNode.children.push({ name: part, path: entry.path, type: 'file', size: entry.size, mime_type: entry.mime_type, children: [] });
        } else {
          if (!dirMap.has(currentPath)) {
            const dirNode: FileTreeNode = { name: part, path: currentPath, type: 'directory', size: null, mime_type: null, children: [] };
            dirMap.set(currentPath, dirNode);
            currentNode.children.push(dirNode);
          }
          currentNode = dirMap.get(currentPath)!;
        }
      }
    }
    const sortNode = (node: FileTreeNode) => {
      node.children.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      node.children.forEach(sortNode);
    };
    sortNode(root);
    return root;
  }, [skillFiles]);

  const isFilePreviewable = useCallback((entry: { type: string; mime_type: string | null; name: string }) => {
    if (entry.type !== 'file') return false;
    const mime = entry.mime_type || '';
    if (mime.startsWith('text/')) return true;
    const previewableMimes = ['application/json', 'application/xml', 'application/javascript', 'application/x-yaml'];
    if (previewableMimes.includes(mime)) return true;
    const ext = entry.name.split('.').pop()?.toLowerCase() || '';
    return ['md', 'txt', 'json', 'yaml', 'yml', 'xml', 'js', 'ts', 'py', 'sh', 'csv', 'ini', 'toml', 'css', 'html'].includes(ext);
  }, []);

  const toggleFileFolder = useCallback((path: string) => {
    setExpandedFileFolders(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }, []);

  const renderFileTree = useCallback((node: FileTreeNode, depth: number): JSX.Element | null => {
    if (node.type === 'file') {
      const previewable = isFilePreviewable(node);
      const selected = filePreviewPath === node.path;
      return (
        <button key={node.path} type="button"
          onClick={() => { if (previewable && selectedSkill) fetchFilePreview(selectedSkill.name, node.path); }}
          className={`w-full min-h-9 flex items-center gap-2 rounded-lg px-2 text-left text-sm border ${selected ? 'bg-accent-subtle text-text border-[var(--color-border-accent)]' : previewable ? 'text-text-muted hover:bg-secondary/40 hover:text-text border-transparent' : 'text-text-muted/60 border-transparent cursor-not-allowed'}`}
          style={{ paddingLeft: `${depth * 14 + 8}px` }} title={node.name}>
          <span className="w-4 h-4" />
          <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3.75h7.5l4.5 4.5v12a1.5 1.5 0 01-1.5 1.5h-10.5a1.5 1.5 0 01-1.5-1.5v-15a1.5 1.5 0 011.5-1.5zM14.25 3.75v4.5h4.5" /></svg>
          <span className="flex-1 min-w-0 truncate">{node.name}</span>
          {!previewable ? <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-secondary/40">不可预览</span> : null}
        </button>
      );
    }
    const isExpanded = expandedFileFolders.has(node.path);
    const hasChildren = node.children.length > 0;
    return (
      <div key={node.path || 'root'}>
        {node.path !== '' ? (
          <button type="button" onClick={() => toggleFileFolder(node.path)}
            className="w-full min-h-9 flex items-center gap-2 rounded-lg px-2 text-left text-sm text-text-muted hover:bg-secondary/40 hover:text-text"
            style={{ paddingLeft: `${depth * 14 + 8}px` }} title={node.name}>
            <span className="w-4 h-4 flex items-center justify-center text-text-muted/80">
              {hasChildren ? <svg className={`w-3 h-3 ${isExpanded ? 'rotate-90' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M9 6l6 6-6 6" /></svg> : null}
            </span>
            <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h4.5l1.5 2.25h10.5v8.25A2.25 2.25 0 0118 19.5H6A2.25 2.25 0 013.75 17.25V6.75z" /></svg>
            <span className="flex-1 min-w-0 truncate">{node.name}</span>
          </button>
        ) : null}
        {isExpanded || node.path === '' ? (
          <div>{node.children.map(child => renderFileTree(child, node.path === '' ? 0 : depth + 1))}</div>
        ) : null}
      </div>
    );
  }, [expandedFileFolders, filePreviewPath, selectedSkill, isFilePreviewable, toggleFileFolder, fetchFilePreview]);

  const handleRebuild = useCallback(
    async (skillName: string, version: string | null) => {
      setRebuildLoading(true);
      showMessage("loading", "正在重建技能，请耐心等待");
      try {
        const data = await webRequest<SkillRebuildResponse>(
          "skills.rebuild",
          withSession({ name: skillName, version }),
          { timeoutMs: 5 * 60_000 }
        );
        if (data.success) {
          showMessage("success", "技能重建完成");
          fetchSkillDetail(skillName);
        }
      } catch (error) {
        console.error(error);
        showMessage("error", `重建失败: ${error}`);
      } finally {
        setRebuildLoading(false);
      }
    },
    [withSession, fetchSkillDetail, showMessage]
  );

  const fetchRetrievalStatus = useCallback(async (options?: { silent?: boolean }) => {
    const requestId = ++retrievalStatusRequestRef.current;
    if (!options?.silent) {
      setRetrievalLoading((current) => (current === "idle" ? "status" : current));
    }
    try {
      const data = await webRequest<SkillRetrievalStatus>(
        "skills.retrieval.status",
        withSession()
      );
      if (requestId === retrievalStatusRequestRef.current) {
        setRetrievalStatus(data);
      }
    } catch (error) {
      console.error('Failed to load skill retrieval status:', error);
    } finally {
      if (!options?.silent) {
        setRetrievalLoading((current) => (current === "status" ? "idle" : current));
      }
    }
  }, [withSession]);

  const fetchRetrievalTree = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setRetrievalLoading((current) => (current === "idle" ? "tree" : current));
    }
    try {
      const data = await webRequest<SkillRetrievalTreeResponse>(
        "skills.retrieval.tree",
        withSession({ language: i18n.language || "cn" })
      );
      const nodes = Array.isArray(data.nodes) ? data.nodes : [];
      setRetrievalTree(data.result || "");
      setRetrievalTreeNodes(nodes);
      setRetrievalTreeCounts({
        branches: typeof data.branch_count === "number"
          ? data.branch_count
          : nodes.filter((node) => node.type !== "leaf").length,
        skills: typeof data.leaf_count === "number"
          ? data.leaf_count
          : nodes.filter((node) => node.type === "leaf").length,
      });
      setSelectedTreeNodeCid((current) => {
        if (current && nodes.some((node) => node.cid === current)) {
          return current;
        }
        return nodes[0]?.cid || null;
      });
    } catch (error) {
      console.error('Failed to load skill retrieval tree:', error);
      setRetrievalTree(error instanceof Error ? error.message : String(error));
      setRetrievalTreeNodes([]);
      setRetrievalTreeCounts({ branches: 0, skills: 0 });
      setSelectedTreeNodeCid(null);
    } finally {
      if (!options?.silent) {
        setRetrievalLoading((current) => (current === "tree" ? "idle" : current));
      }
    }
  }, [i18n.language, withSession]);

  // 当左边栏切换到技能页面时，或切换到"我的技能"页签时，调用 list 接口
  useEffect(() => {
    const prevIsActive = prevIsActiveRef.current;

    // 场景1：从其他页面切换到技能页面（isActive 变为 true）
    if (isActive && !prevIsActive) {
      fetchSkills();
    }

    // 场景2：在技能页面内切换到"我的技能"页签（isActive 保持 true，activeTab 变化）
    if (isActive && prevIsActive && activeTab === "my") {
      fetchSkills();
    }

    // 更新 ref
    prevIsActiveRef.current = isActive;
  }, [isActive, activeTab, fetchSkills]);

  useEffect(() => {
    fetchRetrievalStatus();
  }, [fetchRetrievalStatus]);

  useEffect(() => {
    if (retrievalStatus?.build_status === "running") {
      setRetrievalShowExistingIndexFailureNotice(false);
    }
  }, [retrievalStatus?.build_status]);

  useEffect(() => {
    if (!isActive || activeTab !== "index") return;
    setRetrievalShowExistingIndexFailureNotice(true);
    void fetchRetrievalStatus();
    void fetchRetrievalTree();
  }, [activeTab, fetchRetrievalStatus, fetchRetrievalTree, isActive]);

  useEffect(() => {
    const disabled = retrievalStatus?.enabled === false;
    const running = retrievalStatus?.build_status === "running";
    if (activeTab !== "index" || disabled || !running) {
      if (retrievalPollRef.current !== null) {
        window.clearInterval(retrievalPollRef.current);
        retrievalPollRef.current = null;
      }
      return;
    }
    if (retrievalPollRef.current !== null) return;
    retrievalPollRef.current = window.setInterval(() => {
      void fetchRetrievalStatus({ silent: true });
    }, SKILL_RETRIEVAL_RUNNING_POLL_MS);
    return () => {
      if (retrievalPollRef.current !== null) {
        window.clearInterval(retrievalPollRef.current);
        retrievalPollRef.current = null;
      }
    };
  }, [activeTab, fetchRetrievalStatus, fetchRetrievalTree, retrievalStatus?.build_status, retrievalStatus?.enabled]);

  useEffect(() => {
    const disabled = retrievalStatus?.enabled === false;
    const running = retrievalStatus?.build_status === "running";
    if (activeTab !== "index" || disabled || running) {
      if (retrievalDiscoveryPollRef.current !== null) {
        window.clearInterval(retrievalDiscoveryPollRef.current);
        retrievalDiscoveryPollRef.current = null;
      }
      return;
    }
    if (retrievalDiscoveryPollRef.current !== null) return;
    retrievalDiscoveryPollRef.current = window.setInterval(() => {
      void fetchRetrievalStatus({ silent: true });
    }, SKILL_RETRIEVAL_IDLE_POLL_MS);
    return () => {
      if (retrievalDiscoveryPollRef.current !== null) {
        window.clearInterval(retrievalDiscoveryPollRef.current);
        retrievalDiscoveryPollRef.current = null;
      }
    };
  }, [activeTab, fetchRetrievalStatus, retrievalStatus?.build_status, retrievalStatus?.enabled]);

  useEffect(() => {
    if (activeTab !== "index") return;
    if (retrievalStatus?.build_status === "success" || (retrievalStatus?.index_exists && retrievalStatus?.fresh)) {
      void fetchRetrievalTree();
    }
  }, [
    activeTab,
    fetchRetrievalTree,
    retrievalStatus?.build_status,
    retrievalStatus?.fresh,
    retrievalStatus?.index_exists,
  ]);

  const handleBuildRetrievalIndex = useCallback(async (force = false) => {
    setRetrievalShowExistingIndexFailureNotice(false);
    setRetrievalLoading("build");
    try {
      await webRequest<{ success: boolean; result?: string }>(
        "skills.retrieval.index_build",
        withSession({ force, source: "web" }),
        { timeoutMs: 30_000 }
      );
      await fetchRetrievalStatus();
      await fetchRetrievalTree();
    } catch (error) {
      console.error(error);
    } finally {
      setRetrievalLoading("idle");
    }
  }, [fetchRetrievalStatus, fetchRetrievalTree, withSession]);

  const handleCancelRetrievalBuild = useCallback(async () => {
    setRetrievalLoading("cancel");
    try {
      const result = await webRequest<{ success: boolean; result?: string; build_status?: string }>(
        "skills.retrieval.index_cancel",
        withSession(),
        { timeoutMs: 30_000 }
      );
      if (result.success) {
        setRetrievalStatus((current) => current
          ? {
              ...current,
              build_status: "cancelled",
              build_stage: "cancelled",
              build_message: result.result || current.build_message,
              build_progress: 1,
            }
          : current);
      } else {
        await fetchRetrievalStatus();
      }
    } catch (error) {
      console.error(error);
    } finally {
      setRetrievalLoading("idle");
    }
  }, [fetchRetrievalStatus, withSession]);

  const handleOpenSkill = useCallback(
    (skillName: string) => {
      fetchSkillDetail(skillName);
    },
    [fetchSkillDetail]
  );

  const handleBackToList = useCallback(() => {
    setSelectedSkill(null);
    setDetailState("idle");
  }, []);

  // 新建会话并将技能选中到输入框
  const handleGoToChat = useCallback((skillName: string) => {
    window.dispatchEvent(new CustomEvent('jiuwen:new-conversation', {
      detail: { skillName }
    }));
  }, []);

  // 新建会话：skill-creator（所有 Skill Creator 统一入口）chip + "帮我修改这个技能" + 该技能 chip
  const handleEditSkill = useCallback((skillName: string, skillType?: string) => {
    window.dispatchEvent(new CustomEvent('jiuwen:new-conversation', {
      detail: {
        skillName: 'skill-creator',
        suffixText: '帮我修改这个技能',
        secondSkillName: skillName,
        metadata: {
          scene: 'edit_skill',
          target_skill: skillName,
          ...(skillType ? { target_skill_type: skillType } : {}),
        }
      }
    }));
  }, []);

  // 通过聊天创建：新建会话，选中 skill-creator（统一入口）并在 chip 后追加创建提示文字
  const handleCreateViaChat = useCallback(() => {
    window.dispatchEvent(new CustomEvent('jiuwen:new-conversation', {
      detail: {
        skillName: 'skill-creator',
        suffixText: '请帮我创建一个可以实现xxx功能的技能/团队技能/多模态技能',
        metadata: { scene: 'create_skill' }
      }
    }));
  }, []);

  // ---- 技能经验（内联展示） ----
  const sortedEvolutionEntries = useMemo(
    () =>
      [...evolutionEntries].sort((a, b) => {
        const ta = a.timestamp || "";
        const tb = b.timestamp || "";
        return tb.localeCompare(ta);
      }),
    [evolutionEntries]
  );

  const fetchEvolutionEntries = useCallback(async () => {
    if (!selectedSkill) return;
    setEvolutionListState("loading");
    setEvolutionMessage(null);
    setEvolutionMessageType(null);
    setEvolutionFormatError(null);
    try {
      const data = await webRequest<EvolutionGetResponse>(
        "skills.evolution.get",
        withSession({ name: selectedSkill.name })
      );
      if (!data.exists) {
        setEvolutionEntries([]);
        setEvolutionListState("success");
        return;
      }
      if (data.valid === false) {
        setEvolutionEntries([]);
        setEvolutionFormatError(data.detail || t("skills.evolution.errors.invalidFile"));
        setEvolutionListState("success");
        return;
      }
      setEvolutionEntries(data.entries || []);
      setEvolutionListState("success");
    } catch (error) {
      console.error(error);
      setEvolutionListState("error");
    }
  }, [selectedSkill, t, withSession]);

  useEffect(() => {
    if (detailTab === "experience" && selectedSkill?.has_evolutions) {
      void fetchEvolutionEntries();
    }
  }, [detailTab, selectedSkill, fetchEvolutionEntries]);

  const handleEvolutionContentChange = useCallback((entryId: string, value: string) => {
    setEvolutionEntries((prev) =>
      prev.map((entry) =>
        entry.id === entryId
          ? { ...entry, change: { ...entry.change, content: value } }
          : entry
      )
    );
  }, []);

  const handleEvolutionDeleteEntry = useCallback(
    (entryId: string) => {
      const confirmed = window.confirm(t("skills.evolution.deleteConfirm"));
      if (!confirmed) return;
      setEvolutionEntries((prev) => prev.filter((entry) => entry.id !== entryId));
    },
    [t]
  );

  // 自动保存（带防抖）
  const saveEvolutionEntries = useCallback(async (entries: EvolutionEntry[]) => {
    if (!selectedSkill) return;
    setEvolutionSaving(true);
    try {
      const data = await webRequest<{
        success: boolean;
        detail?: string;
        message?: string;
      }>("skills.evolution.save", withSession({ name: selectedSkill.name, entries }));
      if (!data.success) {
        throw new Error(data.detail || data.message || t("skills.evolution.errors.saveFailed"));
      }
      await fetchSkills();
    } catch (error) {
      console.error(error);
      setEvolutionMessage(t("skills.evolution.errors.saveFailed"));
      setEvolutionMessageType("error");
    } finally {
      setEvolutionSaving(false);
    }
  }, [selectedSkill, t, withSession, fetchSkills]);

  // 防抖保存：监听 evolutionEntries 变化
  useEffect(() => {
    // 只在技能经验页签且有数据时触发
    if (detailTab !== "experience" || !selectedSkill?.has_evolutions || evolutionEntries.length === 0) {
      return;
    }
    // 清除之前的计时器
    if (evolutionSaveTimerRef.current) {
      clearTimeout(evolutionSaveTimerRef.current);
    }
    // 设置新的防抖计时器
    evolutionSaveTimerRef.current = window.setTimeout(() => {
      saveEvolutionEntries(evolutionEntries);
    }, 500);
    // 清理函数
    return () => {
      if (evolutionSaveTimerRef.current) {
        clearTimeout(evolutionSaveTimerRef.current);
      }
    };
  }, [evolutionEntries, detailTab, selectedSkill, saveEvolutionEntries]);

  const handleInstall = useCallback(
    async (skillName?: string) => {
      const targetSkill = skillName
        ? skills.find((skill) => skill.name === skillName)
        : undefined;

      // 内置技能的安装：自动使用 builtin marketplace，不需要用户输入
      if (targetSkill?.is_builtin && targetSkill?.is_builtin_source) {
        const spec = `${skillName}@builtin`;
        setActionTarget(spec);
        setMessage(t('skills.messages.installing', { name: skillName }));
        setMessageType("loading");
        try {
          const data = await webRequest<{
            success: boolean;
            detail?: string;
            message?: string;
          }>("skills.install", withSession({ spec, force: false }));
          if (!data.success) {
            throw new Error(data.detail || data.message || t('skills.errors.installFailed'));
          }
          showMessage("success", t('skills.messages.installed', { spec: skillName }));
          await fetchSkills();
          if (selectedSkill) {
            await fetchSkillDetail(selectedSkill.name);
          }
        } catch (error) {
          console.error(error);
          const errorMessage = error instanceof Error ? error.message : String(error);
          showMessage("error", errorMessage || t('skills.errors.installFailedHint'));
        } finally {
          setActionTarget(null);
        }
        return;
      }

      // 其他技能的安装：提示用户输入 spec
      const marketplaceNames = marketplaces.map((m) => m.name).join(", ");
      const preferredMarketplace =
        targetSkill?.marketplace ||
        (targetSkill &&
        targetSkill.source !== "local" &&
        targetSkill.source !== "project"
          ? targetSkill.source
          : undefined) ||
        marketplaces[0]?.name ||
        "anthropics";
      const defaultSpec = skillName
        ? `${skillName}@${preferredMarketplace}`
        : "plugin-name@anthropics";
      const hint = marketplaceNames
        ? t('skills.marketplacesAvailable', { names: marketplaceNames })
        : t('skills.marketplacesDefault');
      const spec = window.prompt(
        `${t('skills.installPrompt')}\n${hint}`,
        defaultSpec
      );
      if (!spec) return;

      setActionTarget(spec);
      setMessage(t('skills.messages.installing', { name: spec }));
      setMessageType("loading");
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          message?: string;
        }>("skills.install", withSession({ spec, force: false }));
        if (!data.success) {
          throw new Error(data.detail || data.message || t('skills.errors.installFailed'));
        }
        showMessage("success", t('skills.messages.installed', { spec: skillName || spec.split('@')[0] }));
        await fetchSkills();
        if (selectedSkill) {
          await fetchSkillDetail(selectedSkill.name);
        }
      } catch (error) {
        console.error(error);
        showMessage("error", t('skills.errors.installFailedHint'));
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, fetchSkillDetail, selectedSkill, marketplaces, skills, withSession, t]
  );

  const handleImportLocal = useCallback(async (path: string) => {
    if (!path.trim()) return;

    setActionTarget("import_local");
    setMessage(null);
    setMessageType(null);
    try {
      const data = await webRequest<{
        success: boolean;
        detail?: string;
        message?: string;
        skill?: { name?: string };
      }>("skills.import_local", withSession({
        path: path.trim(),
        force: false,
      }));
      if (!data.success) {
        throw new Error(data.detail || data.message || t('skills.errors.importFailed'));
      }
      showMessage("success", t('skills.messages.imported', { name: data.skill?.name || path }));
      await fetchSkills();
      if (data.skill?.name) {
        await fetchSkillDetail(data.skill.name);
      }
    } catch (error) {
      console.error(error);
      const errorMessage = error instanceof Error ? error.message : String(error);
      showMessage("error", errorMessage || t('skills.errors.importFailedHint'));
    } finally {
      setActionTarget(null);
    }
  }, [fetchSkills, fetchSkillDetail, t, withSession]);

  const handleUninstall = useCallback(
    async (pluginName: string) => {
      if (!pluginName) return;
      const confirmed = window.confirm(t('skills.uninstallConfirm', { pluginName }));
      if (!confirmed) return;

      setActionTarget(pluginName);
      setMessage(null);
      setMessageType(null);
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          message?: string;
        }>("skills.uninstall", withSession({
          name: pluginName,
        }));
        if (!data.success) {
          throw new Error(data.detail || data.message || t('skills.errors.uninstallFailed'));
        }
        showMessage("success", t('skills.messages.uninstalled', { pluginName }));
        await fetchSkills();
        handleBackToList();
      } catch (error) {
        console.error(error);
        const errorMessage = error instanceof Error ? error.message : String(error);
        showMessage("error", errorMessage || t('skills.errors.uninstallFailedHint'));
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, handleBackToList, t, withSession]
  );

  const isSkillInstalled = (skill: SkillItem): boolean => {
    return installedSkillMap.has(skill.name) || skill.source === "local" || skill.source === "project";
  };

  const getMySkillsFiltered = useCallback(() => {
    let filtered = visibleSkills;
    switch (mySkillsSubTab) {
      case "enabled":
        filtered = visibleSkills.filter(s => isSkillInstalled(s) && s.enabled !== false);
        break;
      case "disabled":
        filtered = visibleSkills.filter(s => s.enabled === false);
        break;
      default:
        break;
    }
    return filtered;
  }, [visibleSkills, mySkillsSubTab, installedSkillMap]);

  const toggleSkillDisabled = async (skillName: string) => {
    const skill = skills.find(s => s.name === skillName);
    const newEnabled = skill?.enabled === false ? true : false;
    
    const toggleKey = `toggle:${skillName}`;
    setActionTarget(toggleKey);
    
    try {
      const result = await webRequest<{
        success: boolean;
        name: string;
        enabled: boolean;
        detail?: string;
      }>(
        "skills.toggle",
        withSession({ name: skillName, enabled: newEnabled })
      );
      
      if (!result.success) {
        throw new Error(result.detail || 'Failed to toggle skill');
      }
      
      setSkills((prev) => 
        prev.map(s => 
          s.name === skillName ? { ...s, enabled: newEnabled } : s
        )
      );
      
      if (selectedSkill && selectedSkill.name === skillName) {
        setSelectedSkill({ ...selectedSkill, enabled: newEnabled });
      }
    } catch (error) {
      console.error('Failed to toggle skill enabled:', error);
      showMessage('error', t('skills.setEnabledError'));
    } finally {
      setActionTarget(null);
    }
  };

  /** 判断技能是否为技能包（技能名与所属插件名一致） */
  const isSkillPackage = useCallback((skill: SkillItem): boolean => {
    const plugin = installedSkillMap.get(skill.name);
    return Boolean(plugin && plugin.plugin_name === skill.name && plugin.skills.length > 1);
  }, [installedSkillMap]);

  const togglePinSkill = useCallback((skillName: string) => {
    setPinnedSkillNames((prev) => {
      const next = new Set(prev);
      if (next.has(skillName)) {
        next.delete(skillName);
      } else {
        next.add(skillName);
      }
      return next;
    });
  }, []);

  const renderSkillTypeCapsules = () => {
    // "我的技能"下不展示"多模态"胶囊
    const types = activeTab === "my" ? SKILL_TYPES.filter((t) => t !== "multimodal") : SKILL_TYPES;
    return (
      <div className="flex items-center gap-2">
        {types.map((type) => {
          // 技能包（skill）后端尚未适配，置灰不可点击
          const isDisabled = type === "skill";
          return (
            <button
              key={type}
              type="button"
              disabled={isDisabled}
              onClick={() => {
                if (isDisabled) return;
                setSkillType(prev => prev === type ? null : type);
              }}
              className={`h-7 px-4 rounded-[8px] text-sm whitespace-nowrap border ${
                isDisabled
                  ? "border-border/50 text-text-muted/40 cursor-not-allowed bg-secondary/30"
                  : skillType === type
                    ? "border-[var(--color-skill-capsule-active)] text-[var(--color-skill-capsule-active)]"
                    : "border-border text-text-muted hover:text-text"
              }`}
            >
              {t(`skills.skillTypes.${type}`)}
            </button>
          );
        })}
      </div>
    );
  };

  const cleanMessage = message?.replace("√", "") || "";
  const retrievalTreeRoots = useMemo(
    () => buildSkillIndexTree(retrievalTreeNodes),
    [retrievalTreeNodes]
  );
  const disabledSkillNames = useMemo(
    () => new Set(skills.filter((skill) => skill.enabled === false).map((skill) => skill.name)),
    [skills]
  );
  const selectedTreeNode = useMemo(
    () => findSkillIndexNode(retrievalTreeNodes, selectedTreeNodeCid),
    [retrievalTreeNodes, selectedTreeNodeCid]
  );
  const retrievalUsingExistingAfterFailure = Boolean(
    retrievalStatus
      && retrievalShowExistingIndexFailureNotice
      && retrievalStatus.enabled !== false
      && retrievalStatus.build_status === "failed"
      && retrievalStatus.index_exists
      && retrievalStatus.fresh
  );
  const retrievalUsingExistingAfterCancellation = Boolean(
    retrievalStatus
      && retrievalStatus.enabled !== false
      && retrievalStatus.build_status === "cancelled"
      && retrievalStatus.index_exists
      && retrievalStatus.fresh
  );
  const retrievalUsingExistingAfterInterruptedBuild = (
    retrievalUsingExistingAfterFailure
    || retrievalUsingExistingAfterCancellation
  );
  const retrievalStatusText = retrievalStatus
    ? retrievalStatus.enabled === false
      ? t('skills.retrieval.disabled')
      : retrievalStatus.build_status === "running"
      ? t('skills.retrieval.building')
      : retrievalStatus.build_status === "failed" && !retrievalUsingExistingAfterFailure
      ? t('skills.retrieval.buildFailed')
      : retrievalStatus.build_status === "cancelled"
      ? t('skills.retrieval.cancelled')
      : retrievalStatus.index_exists
      ? retrievalStatus.fresh
        ? t('skills.retrieval.ready')
        : t('skills.retrieval.stale')
      : t('skills.retrieval.missing')
    : t('common.loading');
  const retrievalLastBuildMessage = retrievalUsingExistingAfterFailure
    ? t('skills.retrieval.lastBuildFailedUsingExisting')
    : retrievalUsingExistingAfterCancellation
    ? t('skills.retrieval.lastBuildCancelledUsingExisting')
    : "";
  const retrievalBuildRunning = retrievalStatus?.build_status === "running";
  const retrievalBuildProgress = Math.round(Math.max(0, Math.min(1, retrievalStatus?.build_progress ?? 0)) * 100);
  const retrievalBuildLogs = Array.isArray(retrievalStatus?.build_logs)
    ? retrievalStatus.build_logs.slice(-12)
    : [];
  const retrievalHasBuildInfo = Boolean(
    retrievalStatus
      && retrievalStatus.enabled !== false
      && !retrievalUsingExistingAfterInterruptedBuild
      && (
        retrievalBuildRunning
        || ["success", "failed", "cancelled"].includes(String(retrievalStatus.build_status || ""))
        || retrievalBuildLogs.length > 0
      )
  );
  return (
    <>
      {message && messageType === "success" && (
        <div className="fixed top-4 right-4 z-[9999] rounded-[4px] text-sm text-text shadow-lg flex items-center gap-3 px-4" style={{ backgroundColor: "var(--color-feedback-success-toast)", width: "564px", height: "40px" }}>
          <span className="w-4 h-4 rounded-full bg-[var(--color-feedback-success-indicator)] flex items-center justify-center flex-shrink-0">
            <svg className="w-3 h-3 text-text-inverse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          </span>
          {cleanMessage}
          <button
            type="button"
            onClick={() => setMessage(null)}
            className="ml-auto w-6 h-6 flex items-center justify-center hover:bg-card/30 rounded-full "
          >
            <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      {message && messageType === "loading" && (
        <div className="fixed top-4 right-4 z-[9999] rounded-[4px] text-sm text-text shadow-lg flex items-center gap-3 px-4 bg-card border border-border" style={{ width: "564px", height: "40px" }}>
          <span className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin flex-shrink-0" />
          {cleanMessage}
        </div>
      )}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <div className="card flex-1 flex flex-col min-h-0 overflow-hidden">
          {!(activeTab === "my" && selectedSkill) && (
          <>
          <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">
              {t('skills.title')}
            </h2>
            <p className="text-sm text-text-muted mt-1">
              {t('skills.subtitle')}
            </p>
          </div>
          <div className="flex items-center">
            <button
              onClick={() => setSourceModalOpen(true)}
              className="flex items-center gap-1.5 px-1 py-1.5 rounded-lg text-sm text-text-muted hover:text-text hover:bg-secondary/50 "
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
              </svg>
              {t('skills.actions.sourceManager')}
            </button>
            <button
              onClick={() => {
                if (activeTab === "index") {
                  void fetchRetrievalStatus();
                  void fetchRetrievalTree();
                } else if (activeTab === "graph") {
                  const started = skillGraphPanelRef.current?.refresh() ?? false;
                  if (started) {
                    updateGraphReading(true);
                  }
                } else if (activeTab === "my" || activeTab === "marketplace") {
                  setSearch("");
                  fetchSkills(true);
                }
              }}
              className={`flex items-center gap-1.5 pl-[18px] pr-[24px] py-1.5 rounded-lg text-sm text-text-muted  ${
                activeTab === "graph" && graphReading
                  ? "cursor-not-allowed opacity-70"
                  : "hover:text-text hover:bg-secondary/50"
              }`}
              disabled={activeTab === "graph" && graphReading}
            >
              <svg className={`w-4 h-4 ${activeTab === "graph" && graphReading ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
                <path d="M21 3v5h-5" />
              </svg>
              {activeTab === "graph" && graphReading ? "正在读取技能总谱" : t('common.refresh')}
            </button>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab("marketplace")}
              className={`px-4 py-2 text-sm border-b-2 ${
                activeTab === "marketplace"
                  ? "text-text font-bold"
                  : "border-transparent text-text-muted hover:text-text font-medium"
              }`}
              style={activeTab === "marketplace" ? { borderColor: 'var(--color-text-primary)' } : undefined}
            >
              {t('skills.tabs.marketplace')}
            </button>
            <button
              onClick={() => setActiveTab("my")}
              className={`px-4 py-2 text-sm border-b-2 ${
                activeTab === "my"
                  ? "text-text font-bold"
                  : "border-transparent text-text-muted hover:text-text font-medium"
              }`}
              style={activeTab === "my" ? { borderColor: 'var(--color-text-primary)' } : undefined}
            >
              {t('skills.tabs.mySkills')}
            </button>
            <button
              onClick={() => setActiveTab("graph")}
              className={`px-4 py-2 text-sm border-b-2 ${
                activeTab === "graph"
                  ? "text-text font-bold"
                  : "border-transparent text-text-muted hover:text-text font-medium"
              }`}
              style={activeTab === "graph" ? { borderColor: 'var(--color-text-primary)' } : undefined}
            >
              {t('skills.tabs.skillGraph')}
            </button>
            <button
              onClick={() => setActiveTab("index")}
              className={`px-4 py-2 text-sm border-b-2 ${
                activeTab === "index"
                  ? "text-text font-bold"
                  : "border-transparent text-text-muted hover:text-text font-medium"
              }`}
              style={activeTab === "index" ? { borderColor: 'var(--color-text-primary)' } : undefined}
            >
              {t('skills.tabs.skillIndex')}
            </button>
          </div>
          <div className="flex items-center gap-3">
            {activeTab === "my" && (
              <>
                {/* 已发布/未发布筛选 */}
                <div className="relative" style={{ width: mySkillsPublishFilter === "all" ? '40px' : '55px' }}>
                  <button
                    onClick={() => { setPublishFilterOpen((v) => !v); setEnableFilterOpen(false); }}
                    className="flex items-center justify-between w-full h-[32px] text-xs text-text bg-transparent"
                  >
                    <span className="truncate">
                      {mySkillsPublishFilter === "published" ? t('skills.publishFilter.published') :
                       mySkillsPublishFilter === "unpublished" ? t('skills.publishFilter.unpublished') :
                       t('skills.publishFilter.all')}
                    </span>
                    <svg className={`shrink-0 w-3.5 h-3.5 transition-transform ${publishFilterOpen ? 'rotate-180' : ''} text-text-muted`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {publishFilterOpen && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setPublishFilterOpen(false)} />
                      <div className="absolute right-0 top-full mt-1 z-50 min-w-[120px] rounded-lg border border-border bg-panel shadow-lg py-1">
                        <button
                          onClick={() => { setMySkillsPublishFilter("all"); setPublishFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsPublishFilter === "all" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.publishFilter.all')}
                        </button>
                        <button
                          onClick={() => { setMySkillsPublishFilter("published"); setPublishFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsPublishFilter === "published" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.publishFilter.published')}
                        </button>
                        <button
                          onClick={() => { setMySkillsPublishFilter("unpublished"); setPublishFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsPublishFilter === "unpublished" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.publishFilter.unpublished')}
                        </button>
                      </div>
                    </>
                  )}
                </div>
                {/* 启用/禁用筛选 */}
                <div className="relative" style={{ width: '40px' }}>
                  <button
                    onClick={() => { setEnableFilterOpen((v) => !v); setPublishFilterOpen(false); }}
                    className="flex items-center justify-between w-full h-[32px] text-xs text-text bg-transparent"
                  >
                    <span className="truncate">
                      {mySkillsSubTab === "enabled" ? t('skills.mySkillsTabs.enabled') :
                       mySkillsSubTab === "disabled" ? t('skills.mySkillsTabs.disabled') :
                       t('skills.mySkillsTabs.all')}
                    </span>
                    <svg className={`shrink-0 w-3.5 h-3.5 transition-transform ${enableFilterOpen ? 'rotate-180' : ''} text-text-muted`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {enableFilterOpen && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setEnableFilterOpen(false)} />
                      <div className="absolute right-0 top-full mt-1 z-50 min-w-[120px] rounded-lg border border-border bg-panel shadow-lg py-1">
                        <button
                          onClick={() => { setMySkillsSubTab("all"); setEnableFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsSubTab === "all" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.mySkillsTabs.all')}
                        </button>
                        <button
                          onClick={() => { setMySkillsSubTab("enabled"); setEnableFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsSubTab === "enabled" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.mySkillsTabs.enabled')}
                        </button>
                        <button
                          onClick={() => { setMySkillsSubTab("disabled"); setEnableFilterOpen(false); }}
                          className={`flex items-center w-full px-3 py-2 text-sm text-left hover:bg-secondary ${mySkillsSubTab === "disabled" ? "text-[#1476ff]" : "text-text-muted"}`}
                        >
                          {t('skills.mySkillsTabs.disabled')}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </>
            )}
            {(activeTab === "my" || activeTab === "marketplace") && (
              <div className="relative" style={{ width: '360px' }}>
                <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
                </svg>
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('skills.searchPlaceholder')}
                  className="w-full pl-8 pr-3 py-1.5 rounded-[6px] border border-border text-sm text-text placeholder:text-text-muted"
                />
              </div>
            )}
            {activeTab === "my" && (
              <div className="relative">
                <button
                  onClick={() => setCreateMenuOpen((v) => !v)}
                  className="flex items-center justify-center gap-1 h-8 w-[96px] rounded-[16px] text-sm text-text-inverse bg-[#191919] hover:opacity-80"
                >
                  {t('skills.actions.create')}
                  <svg className={`w-3.5 h-3.5 transition-transform ${createMenuOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {createMenuOpen && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setCreateMenuOpen(false)} />
                    <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-lg border border-border bg-panel shadow-lg py-1">
                      <button
                        onClick={() => {
                          setCreateMenuOpen(false);
                          setUploadSkillModalOpen(true);
                        }}
                        disabled={actionTarget === "import_local"}
                        className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary disabled:opacity-60 disabled:cursor-not-allowed"
                      >
                        {t('skills.actions.uploadLocalSkill')}
                      </button>
                      <button
                        onClick={() => {
                          setCreateMenuOpen(false);
                          setDocToSkillModalOpen(true);
                        }}
                        className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                      >
                        {t('skills.actions.documentToSkill')}
                      </button>
                      <button
                        onClick={() => {
                          setCreateMenuOpen(false);
                          handleCreateViaChat();
                        }}
                        className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                      >
                        {t('skills.actions.createViaChat')}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
          </>
          )}

        {activeTab === "index" ? (
          <div className="mt-4 flex flex-col flex-1 min-h-0 gap-4 overflow-y-auto pr-2">
            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-[220px]">
                  <div className="text-sm font-medium text-text-strong">
                    {t('skills.retrieval.title')}
                  </div>
                  <div className="text-xs text-text-muted mt-1">
                    {retrievalStatusText}
                    {retrievalStatus?.indexed_count != null
                      ? ` · ${t('skills.retrieval.indexedCount', { count: retrievalStatus.indexed_count })}`
                      : ""}
                    {(retrievalStatus?.installed_count ?? retrievalStatus?.installed_enabled_count) != null
                      ? ` · ${t('skills.retrieval.installedCount', {
                          count: retrievalStatus?.installed_count ?? retrievalStatus?.installed_enabled_count,
                        })}`
                      : ""}
                  </div>
                  {retrievalLastBuildMessage ? (
                    <div className="mt-1 text-xs text-amber-600">
                      {retrievalLastBuildMessage}
                    </div>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => void handleBuildRetrievalIndex(false)}
                    className="px-3 py-1.5 rounded-lg text-sm border border-border hover:bg-secondary  disabled:opacity-60"
                    disabled={retrievalLoading === "build" || retrievalBuildRunning || retrievalStatus?.enabled === false}
                  >
                    {retrievalLoading === "build"
                      ? t('skills.retrieval.building')
                      : t('skills.retrieval.build')}
                  </button>
                  {retrievalStatus?.index_exists ? (
                    <button
                      onClick={() => void handleBuildRetrievalIndex(true)}
                      className="px-3 py-1.5 rounded-lg text-sm border border-border hover:bg-secondary  disabled:opacity-60"
                      disabled={retrievalLoading === "build" || retrievalBuildRunning || retrievalStatus?.enabled === false}
                    >
                      {retrievalLoading === "build"
                        ? t('skills.retrieval.building')
                        : t('skills.retrieval.fullRebuild')}
                    </button>
                  ) : null}
                  {retrievalBuildRunning ? (
                    <button
                      onClick={handleCancelRetrievalBuild}
                      className="px-3 py-1.5 rounded-lg text-sm border border-border hover:bg-secondary  disabled:opacity-60"
                      disabled={retrievalLoading === "cancel"}
                    >
                      {retrievalLoading === "cancel"
                        ? t('skills.retrieval.cancelling')
                        : t('skills.retrieval.cancel')}
                    </button>
                  ) : null}
                  <button
                    onClick={() => {
                      setRetrievalShowExistingIndexFailureNotice(true);
                      void fetchRetrievalStatus();
                      void fetchRetrievalTree();
                    }}
                    className="px-3 py-1.5 rounded-lg text-sm border border-border hover:bg-secondary  disabled:opacity-60"
                    disabled={retrievalLoading === "tree" || retrievalLoading === "status"}
                  >
                    {retrievalLoading === "tree" || retrievalLoading === "status"
                      ? t('common.refreshing')
                      : t('common.refresh')}
                  </button>
                </div>
              </div>
              {retrievalHasBuildInfo ? (
                <SkillIndexBuildProgressPanel
                  status={retrievalStatus}
                  progress={retrievalBuildProgress}
                  logs={retrievalBuildLogs}
                  t={t}
                />
              ) : null}
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(320px,1fr)_minmax(320px,0.9fr)]">
              <div className="rounded-lg border border-border bg-panel p-4 min-h-[420px] flex flex-col">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-text-strong">
                      {t('skills.retrieval.treeTitle')}
                    </div>
                    <div className="text-xs text-text-muted mt-1">
                      {retrievalTreeNodes.length > 0
                        ? t('skills.retrieval.treeCount', {
                            branches: retrievalTreeCounts.branches,
                            skills: retrievalTreeCounts.skills,
                          })
                        : retrievalLoading === "tree"
                        ? t('common.loading')
                        : t('skills.retrieval.noTree')}
                    </div>
                  </div>
                </div>
                <div className="flex-1 min-h-[320px] overflow-auto rounded-md border border-border bg-secondary/40 p-2">
                  {retrievalTreeNodes.length > 0 ? (
                    <SkillIndexTreeView
                      roots={retrievalTreeRoots}
                      selectedCid={selectedTreeNodeCid}
                      onSelect={setSelectedTreeNodeCid}
                      emptyText={t('skills.retrieval.noTree')}
                      branchLabel={t('skills.retrieval.nodeTypes.branch')}
                      skillLabel={t('skills.retrieval.nodeTypes.skill')}
                      disabledSkillNames={disabledSkillNames}
                      disabledSkillLabel={t('skills.retrieval.disabledSkill')}
                    />
                  ) : (
                    <MarkdownRenderer
                      content={
                        retrievalTree
                        || (retrievalLoading === "tree" ? t('common.loading') : t('skills.retrieval.noTree'))
                      }
                      className="chat-markdown text-xs text-text-muted"
                    />
                  )}
                </div>
              </div>
              <div className="rounded-lg border border-border bg-panel p-4 min-h-[420px] flex flex-col">
                <div className="text-sm font-medium text-text-strong mb-3">
                  {t('skills.retrieval.nodeDetails')}
                </div>
                {selectedTreeNode ? (
                  <div className="flex-1 min-h-0 overflow-auto">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-base font-semibold text-text-strong break-words">
                          {getSkillIndexNodeLabel(selectedTreeNode)}
                        </div>
                        <div className="mt-1 text-xs text-text-muted break-all">
                          {selectedTreeNode.cid}
                        </div>
                      </div>
                      <span
                        className={`shrink-0 rounded border px-2 py-1 text-xs ${
                          selectedTreeNode.type === "leaf"
                            ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-600"
                            : "border-sky-500/25 bg-sky-500/10 text-sky-600"
                        }`}
                      >
                        {selectedTreeNode.type === "leaf"
                          ? t('skills.retrieval.nodeTypes.skill')
                          : t('skills.retrieval.nodeTypes.branch')}
                      </span>
                    </div>

                    <dl className="mt-4 space-y-3 text-sm">
                      <div>
                        <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeDescription')}</dt>
                        <dd className="mt-1 whitespace-pre-wrap text-text">
                          {selectedTreeNode.description || t('skills.noDescription')}
                        </dd>
                      </div>
                      {selectedTreeNode.select_when ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeSelectWhen')}</dt>
                          <dd className="mt-1 whitespace-pre-wrap text-text">{selectedTreeNode.select_when}</dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.dont_select_when ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeDontSelectWhen')}</dt>
                          <dd className="mt-1 whitespace-pre-wrap text-text">{selectedTreeNode.dont_select_when}</dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.source_description ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeSourceDescription')}</dt>
                          <dd className="mt-1 whitespace-pre-wrap text-text">{selectedTreeNode.source_description}</dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.worker_id ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeWorkerId')}</dt>
                          <dd className="mt-1 break-all font-mono text-xs text-text">{selectedTreeNode.worker_id}</dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.category ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeCategory')}</dt>
                          <dd className="mt-1 whitespace-pre-wrap text-text">{selectedTreeNode.category}</dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.keywords?.length ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeKeywords')}</dt>
                          <dd className="mt-2 flex flex-wrap gap-1.5">
                            {selectedTreeNode.keywords.slice(0, 24).map((keyword) => (
                              <span key={keyword} className="rounded border border-border bg-secondary px-2 py-0.5 text-xs text-text-muted">
                                {keyword}
                              </span>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                      {selectedTreeNode.examples?.length ? (
                        <div>
                          <dt className="text-xs text-text-muted">{t('skills.retrieval.nodeExamples')}</dt>
                          <dd className="mt-1 space-y-1">
                            {selectedTreeNode.examples.slice(0, 5).map((example) => (
                              <div key={example} className="whitespace-pre-wrap rounded border border-border bg-secondary px-2 py-1 text-xs text-text">
                                {example}
                              </div>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                    </dl>
                  </div>
                ) : (
                  <div className="flex-1 min-h-[220px] rounded-md border border-dashed border-border bg-secondary/30 p-4 text-sm text-text-muted">
                    {t('skills.retrieval.selectNodeHint')}
                  </div>
                )}
              </div>
            </div>
          </div>
          ) : null}

        {activeTab === "graph" ? (
          <div data-testid="skill-panel-graph-view" className="mt-4 flex flex-1 min-h-0 flex-col gap-3">
            <div data-testid="skill-panel-graph-orchestration-card" className="flex flex-none flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-panel p-4">
              <div className="min-w-[240px] flex-1">
                <div className="flex items-start gap-2">
                  <Music2 size={28} className="mt-1 flex-shrink-0 text-accent" aria-hidden="true" />
                  <div className="min-w-0">
                    <p data-testid="skill-panel-graph-definition" className="text-xs leading-5 text-text-muted">
                      {t('skills.graph.orchestration.graphDefinition')}
                    </p>
                    <p data-testid="skill-panel-graph-orchestration-description" className="text-xs leading-5 text-text-muted">
                      {t('skills.graph.orchestration.description')}
                    </p>
                  </div>
                </div>
                {symphonySaveError ? (
                  <p className="mt-1 text-xs leading-5 text-danger" role="alert">
                    {symphonySaveError}
                  </p>
                ) : null}
              </div>
              <div className="flex flex-shrink-0 items-center gap-2">
                {symphonySaving ? (
                  <>
                    <Loader2 size={16} className="animate-spin text-text-muted" aria-hidden="true" />
                    <span className="text-xs text-text-muted">
                      {t('skills.graph.orchestration.saving')}
                    </span>
                  </>
                ) : null}
                <Switch
                  checked={symphonyEnabledDraft}
                  onChange={(enabled) => void updateSymphonyEnabled(enabled)}
                  disabled={!isConnected || symphonySaving}
                  title={t(
                    isConnected
                      ? 'skills.graph.orchestration.toggleLabel'
                      : 'skills.graph.orchestration.connectionRequired',
                  )}
                />
              </div>
            </div>
            <div className="flex-1 min-h-0">
              <SkillGraphPanel
                ref={skillGraphPanelRef}
                onReadingChange={updateGraphReading}
                externalError={graphActionError}
                onExternalErrorClear={clearGraphActionError}
              />
            </div>
          </div>
        ) : null}

        {activeTab === "marketplace" ? (
          <>
            <div className="mt-4 flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setMarketplaceSubTab("skillhub");
                  setSearchTrigger((prev) => prev + 1);
                }}
                className={`px-4 text-sm font-medium ${
                  marketplaceSubTab === "skillhub"
                    ? "rounded-[8px] bg-secondary h-8 text-text"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {t('skills.marketplaceTabs.skillhub', { defaultValue: 'SkillHub' })}
              </button>
              <button
                type="button"
                onClick={() => {
                  setMarketplaceSubTab("swarmskills");
                  setDebouncedSearch(search);
                  setSearchTrigger((prev) => prev + 1);
                }}
                className={`px-4 text-sm font-medium ${
                  marketplaceSubTab === "swarmskills"
                    ? "rounded-[8px] bg-secondary h-8 text-text"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {t('skills.swarmskills.title')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setMarketplaceSubTab("online");
                  setDebouncedSearch(search);
                  setSearchTrigger((prev) => prev + 1);
                }}
                className={`px-4 text-sm font-medium ${
                  marketplaceSubTab === "online"
                    ? "rounded-[8px] bg-secondary h-8 text-text"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {t('skills.onlineSearch.title')}
              </button>
            </div>

            {marketplaceSubTab === "skillhub" ? (
            <>
            <div className="mt-3 flex items-center gap-2">
              {MARKETPLACE_CATEGORIES.map((cat, idx) => (
                <span key={cat} className="flex items-center gap-2">
                  {idx > 0 && <span className="text-text-muted/40">|</span>}
                  <button
                    onClick={() => setMarketplaceCategory(cat)}
                    className={`px-1 text-sm ${
                      marketplaceCategory === cat
                        ? "text-text font-bold"
                        : "text-text-muted hover:text-text"
                    }`}
                  >
                    {t(`skills.marketplaceCategories.${cat}`)}
                  </button>
                </span>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              {renderSkillTypeCapsules()}
            </div>

            <div className="mt-4 flex-1 min-h-0 overflow-y-auto grid justify-center items-start gap-4" style={{ gridTemplateColumns: 'repeat(2, 616px)' }}>
                {hubLoading && (
                  <div className="col-span-2 flex items-center justify-center h-full text-text-muted">{t('common.loading')}</div>
                )}
                {!hubLoading && hubSkills.length === 0 && (
                  <div className="col-span-2 text-sm text-text-muted">{t('skills.noMatches')}</div>
                )}
                {!hubLoading && hubSkills.length > 0 && (
                    hubSkills.map((skill) => {
                      const avatar = getSkillAvatar(skill.name);
                      const displayName = skill.display_name || skill.name;
                      return (
                        <div
                          key={skill.asset_id}
                          className="group relative text-left border border-border bg-panel hover:bg-card cursor-pointer rounded-[8px] pt-6 pb-4 px-4 flex flex-col min-w-0 overflow-visible"
                          style={{ height: "156px", width: '616px' }}
                        >
                          {/* 上盒子：头像 + 名称 + 来源 */}
                          <div className="flex items-center gap-3 flex-shrink-0">
                            <div className={`w-9 h-9 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-text-inverse font-semibold text-sm`}>
                              {avatar.firstChar}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="text-sm font-semibold text-text-strong truncate leading-5">
                                  {displayName}
                                </span>
                              </div>
                              <div className="mt-1">
                                <span className="px-2 h-5 inline-flex items-center rounded bg-secondary border border-border truncate text-xs text-text-muted">
                                  {t('skills.sourceLabel')}: SkillHub
                                </span>
                              </div>
                            </div>
                            {/* 悬浮操作按钮 */}
                            <div className="shrink-0">
                              {(() => {
                                const isInstalled = installedSkillMap.has(skill.name);
                                if (isInstalled) {
                                  return (
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleGoToChat(skill.name); }}
                                      onMouseEnter={(e) => {
                                        const rect = e.currentTarget.getBoundingClientRect();
                                        setGoTryTooltip({ left: rect.left + rect.width / 2, top: rect.top });
                                      }}
                                      onMouseLeave={() => setGoTryTooltip(null)}
                                      className="w-8 h-8 flex items-center justify-center rounded-[8px] hover:bg-[#F0F7FF] text-text-muted hover:text-[#1476FF] transition-colors"
                                    >
                                      <NewConversationIcon aria-hidden width="20" height="20" />
                                    </button>
                                  );
                                }
                                return (
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleInstall(skill.name); }}
                                    className="w-8 h-8 flex items-center justify-center rounded-[8px] hover:bg-[#F0F7FF] text-text-muted hover:text-[#1476FF] transition-colors"
                                  >
                                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
                                    </svg>
                                  </button>
                                );
                              })()}
                            </div>
                          </div>
                          {/* 下盒子：描述 */}
                          <div className="text-xs text-text-muted mt-4 line-clamp-2">
                            {skill.short_desc || skill.detail_desc || t('skills.noDescription')}
                          </div>
                        </div>
                      );
                    })
                  )}
            </div>
            </>
            ) : null}

            {marketplaceSubTab === "swarmskills" ? (
              <div className="mt-4 h-full flex-1 min-h-0" key={`swarmskills-${searchTrigger}`}>
                <TeamSkillsHubModal
                  open={true}
                  embedded={true}
                  sessionId={sessionId}
                  externalSearchQuery={debouncedSearch}
                  installedSkillNames={installedSkillNames}
                  onClose={() => {}}
                  onInstalled={(_skillName: string) => {
                    void fetchSkills();
                  }}
                />
              </div>
            ) : null}

            {marketplaceSubTab === "online" ? (
              <div className="mt-4 h-full flex-1 min-h-0" key={`online-${searchTrigger}`}>
                <OnlineSkillSearchPanel
                  sessionId={sessionId}
                  externalSearchQuery={debouncedSearch}
                  installedSkillNames={installedSkillNames}
                  installedSkillOrigins={installedSkillOrigins}
                  onInstalled={(_skillName: string) => {
                    void fetchSkills();
                  }}
                />
              </div>
            ) : null}
          </>
        ) : null}

        {activeTab === "my" ? (
          <>
            {message && messageType === "error" && (
              <div className="mt-3 px-3 py-2 rounded-md bg-secondary text-sm text-danger">
                {message}
              </div>
            )}
            {selectedSkill ? (
              <div className="mt-4 flex-1 flex flex-col overflow-y-auto" style={{ paddingLeft: '96px', paddingRight: '96px' }}>
                {/* 加载/错误状态 */}
                {detailState === "loading" && (
                  <div className="text-sm text-text-muted mb-3">{t('skills.detailLoading')}</div>
                )}
                {detailState === "error" && (
                  <div className="text-sm text-text-muted mb-3">{t('skills.detailError')}</div>
                )}

                {/* 面包屑 */}
                <div className="flex items-center gap-1.5 text-sm text-text-muted mb-4">
                  <span>{t('skills.title')}</span>
                  <span className="text-text-muted/40">/</span>
                  <span>{t('skills.tabs.mySkills')}</span>
                  <span className="text-text-muted/40">/</span>
                  <span className="text-text truncate">{selectedSkill.name}</span>
                </div>

                {/* 顶部：返回按钮 + 头像/名称/演进icon + 来源tag + 操作按钮 */}
                <div className="flex items-center justify-between gap-4 mb-6">
                  <div className="flex items-center gap-3 min-w-0">
                    <button
                      onClick={handleBackToList}
                      className="flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-text-muted hover:text-text hover:bg-secondary/50"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                        <path d="m12 19-7-7 7-7" />
                        <path d="M19 12H5" />
                      </svg>
                    </button>
                    <div className={`w-9 h-9 rounded-lg ${getSkillAvatar(selectedSkill.name).color} flex items-center justify-center flex-shrink-0 text-text-inverse font-semibold text-sm`}>
                      {getSkillAvatar(selectedSkill.name).firstChar}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-lg font-semibold text-text-strong truncate">
                          {selectedSkill.name}
                        </span>
                        {selectedSkill.has_evolutions ? (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDetailTab("experience");
                            }}
                            className="relative shrink-0 w-5 h-5 flex items-center justify-center text-text-muted hover:text-text"
                            title={t('skills.actions.viewEvolution')}
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M11.68 2.009A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673c-.824-.85-1.678-1.731-2.21-3.348"/><circle cx="18" cy="5" r="3"/></svg>
                          </button>
                        ) : null}
                      </div>
                      <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                        {selectedSkill.skill_type === 'swarm_skill' ? (
                          <span className="px-2 h-5 inline-flex items-center rounded bg-accent/10 border border-border text-xs text-text-link">
                            团队技能
                          </span>
                        ) : selectedSkill.skill_type === 'multimodal_skill' ? (
                          <span className="px-2 h-5 inline-flex items-center rounded border border-border text-xs" style={{ backgroundColor: '#D5F2DC', color: '#029931' }}>
                            多模态
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>

                  {/* 右侧操作按钮 */}
                  <div className="flex items-center gap-6 flex-shrink-0">
                    {/* ... 菜单：编辑/卸载 */}
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => setDetailMenuOpen((v) => !v)}
                        className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
                      >
                        <MoreIcon aria-hidden />
                      </button>
                      {detailMenuOpen ? (
                        <>
                          <div className="fixed inset-0 z-40" onClick={() => setDetailMenuOpen(false)} />
                          <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-lg border border-border bg-panel shadow-lg py-1">
                            <button
                              onClick={() => {
                                setDetailMenuOpen(false);
                                handleEditSkill(selectedSkill.name, selectedSkill.skill_type);
                              }}
                              className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                            >
                              {t('skills.actions.edit')}
                            </button>
                            <button
                              onClick={() => {
                                setDetailMenuOpen(false);
                                const plugin = installedSkillMap.get(selectedSkill.name);
                                handleUninstall(plugin?.plugin_name || selectedSkill.name);
                              }}
                              className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                            >
                              {t('skills.actions.uninstall')}
                            </button>
                          </div>
                        </>
                      ) : null}
                    </div>
                    {/* 启用开关 + 文字 */}
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={selectedSkill.enabled !== false}
                        onChange={() => toggleSkillDisabled(selectedSkill.name)}
                        disabled={actionTarget === `toggle:${selectedSkill.name}`}
                      />
                      <span className="text-sm text-text-muted whitespace-nowrap">
                        {selectedSkill.enabled !== false ? t('skills.enable') : t('skills.disable')}
                      </span>
                    </div>
                    {/* 去试试 */}
                    <button
                      onClick={() => handleGoToChat(selectedSkill.name)}
                      className="flex items-center justify-center rounded-[16px] text-sm text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap"
                      style={{ height: '32px', padding: '0 24px' }}
                    >
                      {t('skills.actions.goTry')}
                    </button>
                    {/* 发布 */}
                    <button
                      onClick={() => setPublishDrawerOpen(true)}
                      className="flex items-center justify-center rounded-[16px] text-sm text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap"
                      style={{ height: '32px', padding: '0 24px' }}
                    >
                      {t('skills.actions.publish')}
                    </button>
                  </div>
                </div>

                {/* 基本信息 */}
                <div className="mb-6">
                  <div className="text-sm font-semibold text-text mb-2">
                    {t('skills.detail.basicInfo')}
                  </div>
                  <div className="text-sm text-text-muted">
                    {selectedSkill.description || t('skills.noDescription')}
                  </div>
                </div>

                {/* 版本管理 */}
                <div className="mb-6">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="text-sm font-semibold text-text">
                      {t('skills.detail.versionManage')}
                    </div>
                    <button
                      onClick={() => fetchSkillVersions(selectedSkill.name)}
                      disabled={versionsLoadState === 'loading'}
                      className="flex items-center justify-center w-5 h-5 text-text-muted hover:text-text disabled:opacity-50"
                      title={t('common.refresh')}
                    >
                      <svg className={`w-4 h-4 ${versionsLoadState === 'loading' ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.582m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </button>
                  </div>
                  {versionsLoadState === 'loading' ? (
                    <div className="text-sm text-text-muted">{t('common.loading', { defaultValue: '加载中…' })}</div>
                  ) : versionsLoadState === 'error' ? (
                    <div className="text-sm text-text-muted">{t('skills.detail.versionsLoadFailed', { defaultValue: '版本加载失败' })}</div>
                  ) : skillVersions.length === 0 ? (
                    <div className="text-sm text-text-muted">
                      {selectedSkill.version ? `v${selectedSkill.version}` : (t('skills.detail.noVersions', { defaultValue: '暂无版本记录' }))}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 flex-wrap">
                      <select
                        value={selectedSkill.version || skillVersionsDefault || ''}
                        onChange={(e) => {
                          const ver = e.target.value;
                          if (ver) fetchSkillDetail(selectedSkill.name);
                        }}
                        className="appearance-none rounded-[6px] border border-border bg-panel text-sm text-text outline-none focus:outline-none focus:ring-0 focus:border-border"
                        style={{ width: '360px', height: '28px', paddingLeft: '12px', paddingRight: '12px' }}
                      >
                        {skillVersions.map((v) => (
                          <option key={v.version} value={v.version}>
                            {v.version}{v.is_default ? ` (${'默认'})` : ''}{!v.available ? ` (${'不可用'})` : ''}
                          </option>
                        ))}
                      </select>
                      {selectedSkill.has_evolutions ? (
                        <button
                          onClick={() => handleRebuild(selectedSkill.name, selectedSkill.version || null)}
                          disabled={rebuildLoading}
                          className="flex items-center justify-center rounded-[6px] text-xs text-text-muted border border-border hover:bg-secondary whitespace-nowrap disabled:opacity-50"
                          style={{ height: '28px', padding: '0 12px' }}
                        >
                          {rebuildLoading ? (t('common.processing', { defaultValue: '处理中…' })) : (t('skills.actions.rebuild', { defaultValue: '重建' }))}
                        </button>
                      ) : null}
                    </div>
                  )}
                </div>

                {/* 三个页签 */}
                <div className="flex-1 flex flex-col min-h-0">
                  <div className="flex items-center mb-4 flex-shrink-0">
                    <div className="flex items-center gap-8 flex-1 border-b border-border">
                      <button
                        onClick={() => setDetailTab("content")}
                        className={`pb-2 text-sm ${
                          detailTab === "content"
                            ? "text-text font-semibold border-b-2 border-text"
                            : "text-text-muted hover:text-text"
                        }`}
                      >
                        {t('skills.detail.tabs.contentDetail')}
                      </button>
                      <button
                        onClick={() => {
                          setDetailTab("files");
                          fetchSkillFiles(selectedSkill.name);
                        }}
                        className={`pb-2 text-sm ${
                          detailTab === "files"
                            ? "text-text font-semibold border-b-2 border-text"
                            : "text-text-muted hover:text-text"
                        }`}
                      >
                        {t('skills.detail.tabs.filePreview')}
                      </button>
                      {selectedSkill.has_evolutions ? (
                        <button
                          onClick={() => setDetailTab("experience")}
                          className={`pb-2 text-sm ${
                            detailTab === "experience"
                              ? "text-text font-semibold border-b-2 border-text"
                              : "text-text-muted hover:text-text"
                          }`}
                        >
                          {t('skills.detail.tabs.skillExperience')}
                        </button>
                      ) : null}
                    </div>

                    {/* 合成新版本按钮（仅技能经验页签时显示） */}
                    {detailTab === "experience" && selectedSkill.has_evolutions ? (
                      <button
                        onClick={() => handleRebuild(selectedSkill.name, selectedSkill.version || null)}
                        disabled={rebuildLoading}
                        onMouseEnter={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          setSynthesizeTooltip({ left: rect.left + rect.width / 2, top: rect.top });
                        }}
                        onMouseLeave={() => setSynthesizeTooltip(null)}
                        className="mb-1 flex items-center justify-center rounded-[16px] text-xs font-medium text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap disabled:opacity-50"
                        style={{ width: '118px', height: '32px' }}
                      >
                        {rebuildLoading ? (t('common.processing', { defaultValue: '处理中…' })) : (t('skills.actions.synthesizeNewVersion'))}
                      </button>
                    ) : null}
                  </div>

                  {/* 内容详情 */}
                  {detailTab === "content" && (
                    <div className="flex-1 min-h-0 overflow-y-auto text-sm text-text bg-secondary border border-border rounded-md p-3">
                      {selectedSkill.content ? (
                        <MarkdownRenderer content={transformSkillContentImages(selectedSkill.content, selectedSkill.file_path)} className="chat-text chat-markdown" />
                      ) : (
                        t('skills.noContent')
                      )}
                    </div>
                  )}

                  {/* 文件预览 */}
                  {detailTab === "files" && (
                    <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,3fr)_minmax(0,7fr)] gap-3">
                      <div className="rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col min-h-0">
                        <div className="px-3 py-2 bg-secondary/30 border-b border-border flex items-center justify-between">
                          <span className="text-sm font-medium text-text">{t('skills.detail.fileTree', { defaultValue: '文件列表' })}</span>
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] mono px-2 py-0.5 rounded-full border border-border bg-secondary/60">{skillFiles.filter(f => f.type === 'file').length} 文件</span>
                            <button type="button" onClick={() => selectedSkill && fetchSkillFiles(selectedSkill.name)} className="text-xs text-text-muted hover:text-text px-2 py-0.5 rounded border border-border hover:bg-secondary">{t('common.refresh', { defaultValue: '刷新' })}</button>
                          </div>
                        </div>
                        <div className="flex-1 overflow-auto p-2">
                          {filesLoadState === 'loading' ? (
                            <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('common.loading', { defaultValue: '加载中…' })}</div>
                          ) : filesLoadState === 'error' ? (
                            <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('skills.detail.filesLoadFailed', { defaultValue: '文件列表加载失败' })}</div>
                          ) : skillFiles.length === 0 ? (
                            <div className="h-full flex items-center justify-center text-sm text-text-muted">{t('skills.detail.noFiles', { defaultValue: '暂无文件' })}</div>
                          ) : (
                            <div className="space-y-0.5">{skillFileTree.children.map(child => renderFileTree(child, 0))}</div>
                          )}
                        </div>
                      </div>
                      <div className="rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col min-h-0">
                        {filePreview ? (
                          <>
                            <div className="px-3 py-2 bg-secondary/30 border-b border-border flex items-center justify-between flex-shrink-0">
                              <span className="text-sm font-medium text-text truncate">{filePreview.path.split('/').pop()}</span>
                              <div className="flex items-center gap-2 flex-shrink-0">
                                <span className="text-[10px] mono px-2 py-0.5 rounded-full border border-border bg-secondary/60">{filePreview.mime_type || ''}</span>
                                {filePreview.size != null ? <span className="text-[10px] mono px-2 py-0.5 rounded-full border border-border bg-secondary/60">{filePreview.size > 1024 ? `${Math.ceil(filePreview.size / 1024)} KB` : `${filePreview.size} B`}</span> : null}
                                {filePreview.download_url ? <a href={filePreview.download_url} download className="text-xs text-text-link hover:underline">{t('common.download', { defaultValue: '下载' })}</a> : null}
                              </div>
                            </div>
                            {filePreview.content != null ? (
                              <pre className="flex-1 min-h-0 overflow-auto text-xs text-text p-3 whitespace-pre-wrap font-mono">{filePreview.content}</pre>
                            ) : (
                              <div className="flex-1 flex items-center justify-center text-sm text-text-muted">{filePreview.download_url ? (t('skills.detail.binaryFileDownload', { defaultValue: '此文件无法文本预览，请点击下载' })) : (t('skills.detail.noPreview', { defaultValue: '无法预览此文件' }))}</div>
                            )}
                          </>
                        ) : (
                          <>
                            <div className="px-3 py-2 bg-secondary/30 border-b border-border flex items-center gap-3 flex-shrink-0">
                              <span className="h-8 w-8 rounded-lg border border-border bg-card flex items-center justify-center text-text-muted flex-shrink-0">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-6 w-6"><path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" /></svg>
                              </span>
                              <div>
                                <h4 className="text-sm font-medium text-text">{t('skills.detail.contentPreview', { defaultValue: '内容预览' })}</h4>
                                <p className="text-xs text-text-muted mt-0.5">{t('skills.detail.selectFileToPreview', { defaultValue: '请选择左侧文件进行预览' })}</p>
                              </div>
                            </div>
                            <div className="flex-1 min-h-0 flex items-center justify-center">
                              <div className="text-center text-text-muted"><div className="mb-2 text-sm">{t('skills.detail.selectFileToPreview', { defaultValue: '请选择左侧文件进行预览' })}</div></div>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  {/* 技能经验 */}
                  {detailTab === "experience" && selectedSkill.has_evolutions && (
                    <div className="flex-1 min-h-0 overflow-y-auto">
                      {evolutionMessage && (
                        <div
                          className={`mb-3 px-3 py-2 rounded-md text-sm ${
                            evolutionMessageType === "error"
                              ? "bg-secondary text-danger"
                              : "bg-secondary text-text"
                          }`}
                        >
                          {evolutionMessage}
                        </div>
                      )}

                      {evolutionFormatError && (
                        <div className="mb-3 px-3 py-2 rounded-md bg-secondary text-sm text-danger">
                          {evolutionFormatError}
                        </div>
                      )}

                      {evolutionListState === "loading" && (
                        <div className="flex items-center justify-center text-text-muted">{t('common.loading')}</div>
                      )}
                      {evolutionListState === "error" && (
                        <div className="text-sm text-text-muted">
                          {t('skills.evolution.errors.loadFailed')}
                        </div>
                      )}
                      {evolutionListState === "success" && !evolutionFormatError && sortedEvolutionEntries.length === 0 && (
                        <div className="text-sm text-text-muted">
                          {t('skills.evolution.empty')}
                        </div>
                      )}

                      {evolutionListState === "success" && !evolutionFormatError && sortedEvolutionEntries.length > 0 && (
                        <div className="space-y-4">
                          {sortedEvolutionEntries.map((entry) => (
                            <div
                              key={entry.id}
                              className="border border-border py-4 px-4"
                              style={{ backgroundColor: '#FAFAFA', borderRadius: '8px' }}
                            >
                              <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0 text-xs space-y-1 w-[90%]">
                                <div className="grid grid-cols-3 gap-4">
                                  <div>
                                    <span style={{ color: '#777777' }}>{t('skills.evolution.fields.section')}:</span>
                                    <span className="ml-1" style={{ color: '#191919' }}>{entry.change?.section || "-"}</span>
                                  </div>
                                  <div>
                                    <span style={{ color: '#777777' }}>{t('skills.evolution.fields.target')}:</span>
                                    <span className="ml-1" style={{ color: '#191919' }}>{entry.change?.target || "-"}</span>
                                  </div>
                                  <div>
                                    <span style={{ color: '#777777' }}>{t('skills.evolution.fields.timestamp')}:</span>
                                    <span className="ml-1" style={{ color: '#191919' }}>
                                      {entry.timestamp ? new Date(entry.timestamp).toLocaleString(i18n.language) : "-"}
                                    </span>
                                  </div>
                                </div>
                              </div>
                                <button
                                  type="button"
                                  onClick={() => handleEvolutionDeleteEntry(entry.id)}
                                  className="w-7 h-7 flex items-center justify-center rounded-lg hover:opacity-80"
                                  style={{ color: '#191919' }}
                                  title={t('skills.evolution.actions.delete')}
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                  </svg>
                                </button>
                              </div>

                              <div className="mt-3">
                                <textarea
                                  value={entry.change?.content || ""}
                                  onChange={(event) => handleEvolutionContentChange(entry.id, event.target.value)}
                                  className="w-full min-h-28 px-3 py-2 rounded-md bg-card border border-border text-sm text-text placeholder:text-text-muted"
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="mt-4 flex flex-col flex-1 min-h-0">
                <div className="flex items-center gap-2 flex-shrink-0">
                  {renderSkillTypeCapsules()}
                </div>

                {listState === "success" && getMySkillsFiltered().length === 0 ? (
                  <div className="mt-4 text-sm text-text-muted">
                    {mySkillsSubTab === "disabled" ? t('skills.noDisabledSkills') :
                     mySkillsSubTab === "enabled" ? t('skills.noEnabledSkills') :
                     t('skills.noMatches')}
                  </div>
                ) : (
                  <div className="mt-4 flex-1 min-h-0 overflow-y-auto grid justify-center items-start gap-4" style={{ gridTemplateColumns: 'repeat(2, 616px)' }}>
                    {listState === "loading" && (
                      <div className="col-span-2 flex items-center justify-center h-full text-text-muted">{t('common.loading')}</div>
                    )}
                    {listState === "error" && (
                      <div className="col-span-2 text-sm text-text-muted">
                        {t('skills.listError')}
                      </div>
                    )}
                  {listState === "success" &&
                    getMySkillsFiltered().map((skill) => {
                      const avatar = getSkillAvatar(skill.name);
                      const isDisabled = skill.enabled === false;
                      const isToggling = actionTarget === `toggle:${skill.name}`;
                      const isPackage = isSkillPackage(skill);
                      const isPinned = pinnedSkillNames.has(skill.name);
                      const isMenuOpen = openMenuSkillName === skill.name;
                      const listKey = skill.path || `${skill.source || "local"}:${skill.name}`;
                      return (
                        <div
                          key={listKey}
                          onClick={() => handleOpenSkill(skill.name)}
                          className="group relative text-left border border-border bg-panel hover:bg-card cursor-pointer rounded-[8px] pt-6 pb-4 px-4 flex flex-col min-w-0 overflow-visible"
                          style={{ height: "156px", width: '616px' }}
                        >
                          {/* 上盒子：头像 + 名称/演进 + 来源 + 悬浮按钮 */}
                          <div className="flex items-center gap-3 min-w-0">
                            <div className={`w-9 h-9 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-text-inverse font-semibold text-sm`}>
                              {avatar.firstChar}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="text-sm font-semibold text-text-strong truncate leading-5">
                                  {skill.name}
                                </span>
                                {skill.has_evolutions ? (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleOpenSkill(skill.name);
                                      setDetailTab("experience");
                                    }}
                                    className="relative shrink-0 w-5 h-5 flex items-center justify-center text-text-muted hover:text-text"
                                    title={t('skills.actions.viewEvolution')}
                                  >
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" className="lucide lucide-bell-dot-icon lucide-bell-dot"><path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M11.68 2.009A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673c-.824-.85-1.678-1.731-2.21-3.348"/><circle cx="18" cy="5" r="3"/></svg>
                                  </button>
                                ) : null}
                              </div>
                              <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                                {skill.skill_type === 'swarm_skill' ? (
                                  <span className="px-2 h-5 inline-flex items-center rounded bg-accent/10 border border-border truncate text-xs text-text-link">
                                    团队技能
                                  </span>
                                ) : skill.skill_type === 'multimodal_skill' ? (
                                  <span className="px-2 h-5 inline-flex items-center rounded border border-border truncate text-xs" style={{ backgroundColor: '#D5F2DC', color: '#029931' }}>
                                    多模态
                                  </span>
                                ) : null}
                                {activeTab === "my" ? (
                                  <span className="px-2 h-5 inline-flex items-center rounded bg-secondary border border-border truncate text-xs text-text-muted">
                                    未发布
                                  </span>
                                ) : null}
                              </div>
                            </div>
                            {/* 悬浮按钮 + 始终显示的启用开关 */}
                            <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                              <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                <div className="relative">
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setOpenMenuSkillName(isMenuOpen ? null : skill.name);
                                    }}
                                    className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
                                  >
                                    <MoreIcon aria-hidden />
                                  </button>
                                  {isMenuOpen ? (
                                    <>
                                      <div className="fixed inset-0 z-40" onClick={(e) => { e.stopPropagation(); setOpenMenuSkillName(null); }} />
                                      <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-lg border border-border bg-panel shadow-lg py-1">
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            togglePinSkill(skill.name);
                                            setOpenMenuSkillName(null);
                                          }}
                                          className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                                        >
                                          {isPinned
                                            ? (isPackage ? t('skills.actions.unpinSkillPackage') : t('skills.actions.unpinSkill'))
                                            : (isPackage ? t('skills.actions.pinSkillPackage') : t('skills.actions.pinSkill'))}
                                        </button>
                                        {!isPackage ? (
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              setOpenMenuSkillName(null);
                                              handleEditSkill(skill.name, skill.skill_type);
                                            }}
                                            className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                                          >
                                            {t('skills.actions.edit')}
                                          </button>
                                        ) : null}
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setOpenMenuSkillName(null);
                                            const plugin = installedSkillMap.get(skill.name);
                                            handleUninstall(plugin?.plugin_name || skill.name);
                                          }}
                                          className="flex items-center w-full px-3 py-2 text-sm text-left text-text hover:bg-secondary"
                                        >
                                          {t('skills.actions.uninstall')}
                                        </button>
                                      </div>
                                    </>
                                  ) : null}
                                </div>
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleGoToChat(skill.name);
                                  }}
                                  onMouseEnter={(e) => {
                                    const rect = e.currentTarget.getBoundingClientRect();
                                    setGoTryTooltip({ left: rect.left + rect.width / 2, top: rect.top });
                                  }}
                                  onMouseLeave={() => setGoTryTooltip(null)}
                                  className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
                                >
                                  <NewConversationIcon aria-hidden width="16" height="16" />
                                </button>
                              </div>
                              <Switch
                                checked={!isDisabled}
                                onChange={() => toggleSkillDisabled(skill.name)}
                                disabled={isToggling}
                              />
                            </div>
                          </div>
                          {/* 下盒子：描述 */}
                          <div className="text-xs text-text-muted mt-4 line-clamp-2">
                            {skill.description || t('skills.noDescription')}
                          </div>
                        </div>
                      );
                    })}
                </div>
            )}
              </div>
            )}
          </>
        ) : null}
      </div>
      <SourceManagerModal
        open={sourceModalOpen}
        sessionId={sessionId}
        onClose={() => setSourceModalOpen(false)}
        onNavigateToConfig={() => {
          setSourceModalOpen(false);
          onNavigateToConfig?.();
        }}
      />
      <SkillNetSearchModal
        open={skillNetModalOpen}
        sessionId={sessionId}
        installedSkillNames={installedSkillNames}
        installedSkillOrigins={installedSkillOrigins}
        onClose={() => setSkillNetModalOpen(false)}
        onInstalled={async () => {
          await fetchSkills();
        }}
        onNavigateToConfig={() => {
          setSkillNetModalOpen(false);
          onNavigateToConfig?.();
        }}
      />
      <ClawHubSearchModal
        open={clawHubModalOpen}
        sessionId={sessionId}
        installedSkillNames={installedSkillNames}
        installedSkillOrigins={installedSkillOrigins}
        onClose={() => setClawHubModalOpen(false)}
        onInstalled={async () => {
          await fetchSkills();
        }}
      />
      <TeamSkillsHubModal
        open={teamSkillsHubModalOpen}
        sessionId={sessionId}
        installedSkillNames={installedSkillNames}
        onClose={() => setTeamSkillsHubModalOpen(false)}
        onInstalled={async () => {
          await fetchSkills();
        }}
      />
      {/* 上传技能弹窗 */}
      {uploadSkillModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 bg-black/60"
            onClick={() => { setUploadSkillModalOpen(false); setUploadSkillPath(""); }}
            aria-label={t('skills.uploadSkillModal.cancel')}
          />
          <div
            className="relative overflow-hidden rounded-[8px] border border-border bg-card shadow-2xl animate-rise flex flex-col"
            style={{ width: '550px' }}
          >
            {/* 头部 */}
            <div className="flex items-center justify-between gap-3 px-5 py-3 bg-panel">
              <span className="text-lg font-semibold text-text-strong">
                {t('skills.uploadSkillModal.title')}
              </span>
              <button
                type="button"
                onClick={() => { setUploadSkillModalOpen(false); setUploadSkillPath(""); }}
                className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {/* 提示行 */}
            <div className="px-5 pt-3">
              <div
                className="flex items-start gap-1.5 rounded-[8px] px-3 py-2 text-xs text-text"
                style={{ backgroundColor: '#DEECFF', width: '502px' }}
              >
                <svg className="w-3.5 h-3.5 shrink-0 mt-0.5 text-[#1476FF]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <circle cx="12" cy="12" r="10" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4M12 16h.01" />
                </svg>
                <span className="leading-4">{t('skills.uploadSkillModal.notice')}</span>
              </div>
            </div>
            {/* 文件上传拖动框 */}
            <div className="px-5 pt-3 pb-5">
              <label
                onDragOver={(e) => { e.preventDefault(); }}
                onDrop={(e) => {
                  e.preventDefault();
                  const file = e.dataTransfer.files[0];
                  if (file) {
                    // Electron 环境下 file.path 可获取完整路径
                    const path = (file as File & { path?: string }).path || file.name;
                    setUploadSkillPath(path);
                  }
                }}
                className="flex flex-col items-center justify-center gap-2 rounded-[12px] border border-dashed border-border cursor-pointer hover:bg-[#EEEEEE]"
                style={{ width: '502px', height: '160px', backgroundColor: '#F5F5F5' }}
              >
                <svg className="w-10 h-10 text-text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                <span className="text-sm text-text-muted">
                  {uploadSkillPath.trim()
                    ? uploadSkillPath
                    : t('skills.uploadSkillModal.dropHint')}
                </span>
                <input
                  type="file"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      const path = (file as File & { path?: string }).path || file.name;
                      setUploadSkillPath(path);
                    }
                  }}
                />
              </label>
            </div>
            {/* 底部按钮 */}
            <div className="flex items-center justify-end gap-3 px-5 py-3 bg-panel">
              <button
                type="button"
                onClick={() => { setUploadSkillModalOpen(false); setUploadSkillPath(""); }}
                className="flex items-center justify-center rounded-[16px] text-sm text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap"
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.uploadSkillModal.cancel')}
              </button>
              <button
                type="button"
                disabled={!uploadSkillPath.trim() || actionTarget === "import_local"}
                onClick={() => {
                  const path = uploadSkillPath;
                  setUploadSkillModalOpen(false);
                  setUploadSkillPath("");
                  handleImportLocal(path);
                }}
                className={`flex items-center justify-center rounded-[16px] text-sm whitespace-nowrap transition-colors ${
                  !uploadSkillPath.trim() || actionTarget === "import_local"
                    ? 'bg-[#E0E0E0] text-[#999999] cursor-not-allowed'
                    : 'text-text-inverse bg-[#191919] hover:opacity-80'
                }`}
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.uploadSkillModal.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* 知识转技能弹窗 */}
      {docToSkillModalOpen && (() => {
        const isDocConfirmDisabled = docToSkillSource === "local" ? !docToSkillPath.trim() : !docToSkillLink.trim();
        return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 bg-black/60"
            onClick={() => { setDocToSkillModalOpen(false); setDocToSkillPath(""); setDocToSkillLink(""); setDocToSkillDesc(""); }}
            aria-label={t('skills.docToSkillModal.cancel')}
          />
          <div
            className="relative overflow-hidden rounded-[8px] border border-border bg-card shadow-2xl animate-rise flex flex-col"
            style={{ width: '550px' }}
          >
            {/* 头部 */}
            <div className="flex items-center justify-between gap-3 px-5 pt-3 pb-0 bg-panel">
              <span className="text-lg font-semibold text-text-strong">
                {t('skills.docToSkillModal.title')}
              </span>
              <button
                type="button"
                onClick={() => { setDocToSkillModalOpen(false); setDocToSkillPath(""); setDocToSkillLink(""); setDocToSkillDesc(""); }}
                className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {/* 副标题 */}
            <div className="px-5">
              <span className="text-xs text-text-muted">{t('skills.docToSkillModal.subtitle')}</span>
            </div>
            {/* 来源 */}
            <div className="px-5 pt-4">
              <span className="block text-sm font-medium text-text mb-2">
                {t('skills.docToSkillModal.sourceLabel')}
              </span>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    checked={docToSkillSource === "local"}
                    onChange={() => setDocToSkillSource("local")}
                    className="w-3.5 h-3.5 accent-[#1476ff]"
                  />
                  <span className="text-sm text-text">{t('skills.docToSkillModal.sourceLocal')}</span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    checked={docToSkillSource === "link"}
                    onChange={() => setDocToSkillSource("link")}
                    className="w-3.5 h-3.5 accent-[#1476ff]"
                  />
                  <span className="text-sm text-text">{t('skills.docToSkillModal.sourceLink')}</span>
                </label>
              </div>
            </div>
            {/* 本地上传 */}
            {docToSkillSource === "local" && (
              <div className="px-5 pt-3">
                <label
                  onDragOver={(e) => { e.preventDefault(); }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const file = e.dataTransfer.files[0];
                    if (file) {
                      const path = (file as File & { path?: string }).path || file.name;
                      setDocToSkillPath(path);
                    }
                  }}
                  className="flex flex-col items-center justify-center gap-2 rounded-[12px] border border-dashed border-border cursor-pointer hover:bg-[#EEEEEE]"
                  style={{ width: '502px', height: '160px', backgroundColor: '#F5F5F5' }}
                >
                  <svg className="w-10 h-10 text-text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                  </svg>
                  <span className="text-sm text-text-muted whitespace-pre-line text-center">
                    {docToSkillPath.trim()
                      ? docToSkillPath
                      : t('skills.docToSkillModal.dropHint')}
                  </span>
                  <input
                    type="file"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        const path = (file as File & { path?: string }).path || file.name;
                        setDocToSkillPath(path);
                      }
                    }}
                  />
                </label>
              </div>
            )}
            {/* 链接 */}
            {docToSkillSource === "link" && (
              <div className="px-5 pt-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <span className="text-sm font-medium text-text">
                    {t('skills.docToSkillModal.linkLabel')}
                  </span>
                  <span
                    onMouseEnter={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      setDocToSkillTooltip({ left: rect.left + rect.width / 2, top: rect.top });
                    }}
                    onMouseLeave={() => setDocToSkillTooltip(null)}
                    className="w-4 h-4 flex items-center justify-center text-text-muted cursor-help"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                      <circle cx="12" cy="12" r="10" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01" />
                    </svg>
                  </span>
                </div>
                <input
                  type="text"
                  value={docToSkillLink}
                  onChange={(e) => setDocToSkillLink(e.target.value)}
                  placeholder={t('skills.docToSkillModal.linkPlaceholder')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                  style={{ maxWidth: '502px' }}
                />
              </div>
            )}
            {/* 技能描述 */}
            <div className="px-5 pt-4">
              <span className="block text-sm font-medium text-text mb-1.5">
                {t('skills.docToSkillModal.descLabel')}
              </span>
              <input
                type="text"
                value={docToSkillDesc}
                onChange={(e) => setDocToSkillDesc(e.target.value)}
                placeholder={t('skills.docToSkillModal.descPlaceholder')}
                className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                style={{ maxWidth: '502px' }}
              />
            </div>
            {/* 底部按钮 */}
            <div className="flex items-center justify-end gap-3 px-5 pt-4 pb-4 bg-panel">
              <button
                type="button"
                onClick={() => { setDocToSkillModalOpen(false); setDocToSkillPath(""); setDocToSkillLink(""); setDocToSkillDesc(""); }}
                className="flex items-center justify-center rounded-[16px] text-sm text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap"
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.docToSkillModal.cancel')}
              </button>
              <button
                type="button"
                disabled={isDocConfirmDisabled}
                onClick={() => {
                  const path = docToSkillSource === "local" ? docToSkillPath : docToSkillLink;
                  setDocToSkillModalOpen(false);
                  setDocToSkillPath("");
                  setDocToSkillLink("");
                  setDocToSkillDesc("");
                  handleImportLocal(path);
                }}
                className={`flex items-center justify-center rounded-[16px] text-sm whitespace-nowrap transition-colors ${
                  isDocConfirmDisabled
                    ? 'bg-[#E0E0E0] text-[#999999] cursor-not-allowed'
                    : 'text-text-inverse bg-[#191919] hover:opacity-80'
                }`}
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.docToSkillModal.confirm')}
              </button>
            </div>
          </div>
        </div>
        );
      })()}
      {synthesizeTooltip && (
        <div
          className="fixed whitespace-nowrap rounded-[8px] h-[50px] flex items-center px-2.5 text-xs text-text bg-[var(--color-surface-popover)] border border-border shadow-lg pointer-events-none z-[9999]"
          style={{
            left: synthesizeTooltip.left,
            top: synthesizeTooltip.top - 50 - 6,
            transform: 'translateX(-50%)',
          }}
        >
          {t('skills.actions.synthesizeTooltip')}
        </div>
      )}
      {goTryTooltip && (
        <div
          className="fixed whitespace-nowrap rounded-[8px] h-[50px] flex items-center px-2.5 text-xs text-text bg-[var(--color-surface-popover)] border border-border shadow-lg pointer-events-none z-[9999]"
          style={{
            left: goTryTooltip.left,
            top: goTryTooltip.top - 50 - 6,
            transform: 'translateX(-50%)',
          }}
        >
          {t('skills.actions.goTry')}
        </div>
      )}
      {docToSkillTooltip && (
        <div
          className="fixed whitespace-nowrap rounded-[8px] h-[50px] flex items-center px-2.5 text-xs text-text bg-[var(--color-surface-popover)] border border-border shadow-lg pointer-events-none z-[9999]"
          style={{
            left: docToSkillTooltip.left,
            top: docToSkillTooltip.top - 50 - 6,
            transform: 'translateX(-50%)',
          }}
        >
          {t('skills.docToSkillModal.linkTooltip')}
        </div>
      )}
      {/* 发布技能右侧弹窗 */}
      {publishDrawerOpen && selectedSkill && (() => {
        const isPublishDisabled = !publishName || !publishSkillName || !publishVersion || !publishDisplayName || !publishSha256;
        return (
        <>
          <div
            className="fixed inset-0 z-[9998] bg-black/30"
            onClick={() => setPublishDrawerOpen(false)}
          />
          <div
            className="fixed top-0 right-0 bottom-0 z-[9999] bg-panel border-l border-border shadow-2xl flex flex-col"
            style={{ width: '550px' }}
          >
            {/* 头部（无分割线） */}
            <div className="flex items-center justify-between px-6 pt-4 pb-2 flex-shrink-0">
              <span className="text-base font-semibold text-text-strong">
                {t('skills.publishForm.title')}
              </span>
              <button
                type="button"
                onClick={() => setPublishDrawerOpen(false)}
                className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary text-text-muted hover:text-text"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {/* 提示行（标题下方，可关闭） */}
            {publishNoticeVisible && (
              <div
                className="mx-6 mb-2 flex items-center gap-1.5 rounded-[6px] px-3 text-xs text-text flex-shrink-0"
                style={{ backgroundColor: '#DEECFF', height: '34px' }}
              >
                <svg className="w-3.5 h-3.5 shrink-0 text-[#1476FF]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <circle cx="12" cy="12" r="10" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4M12 16h.01" />
                </svg>
                <span>{t('skills.publishForm.noticeText')}</span>
                <a
                  href={t('skills.publishForm.noticeUrl')}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-0.5 text-[#1476FF] hover:underline"
                >
                  {t('skills.publishForm.noticeView')}
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M14 5h5v5M19 5l-9 9M19 14v5a1 1 0 01-1 1H6a1 1 0 01-1-1V7a1 1 0 011-1h5" />
                  </svg>
                </a>
                <button
                  type="button"
                  onClick={() => setPublishNoticeVisible(false)}
                  className="ml-auto w-5 h-5 flex items-center justify-center rounded hover:bg-[#1476FF]/10 text-text-muted hover:text-text"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}
            {/* 表单内容 */}
            <div className="flex-1 overflow-y-auto px-6 py-2 space-y-4">
              {/* 名称（下拉框） */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.name')}
                </label>
                <select
                  value={publishName}
                  onChange={(e) => setPublishName(e.target.value)}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                >
                  <option value="">{t('skills.publishForm.placeholderSelect')}</option>
                  {skills.map((s) => (
                    <option key={s.name} value={s.name}>{s.name}</option>
                  ))}
                </select>
              </div>
              {/* 技能名 */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.skillName')}
                </label>
                <input
                  type="text"
                  value={publishSkillName}
                  onChange={(e) => setPublishSkillName(e.target.value)}
                  placeholder={t('skills.publishForm.placeholderSelect')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                />
              </div>
              {/* 版本号 */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.version')}
                </label>
                <input
                  type="text"
                  value={publishVersion}
                  onChange={(e) => setPublishVersion(e.target.value)}
                  placeholder={t('skills.publishForm.placeholderSelect')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                />
              </div>
              {/* 显示名 */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.displayName')}
                </label>
                <input
                  type="text"
                  value={publishDisplayName}
                  onChange={(e) => setPublishDisplayName(e.target.value)}
                  placeholder={t('skills.publishForm.placeholderSelect')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                />
              </div>
              {/* 描述（可选） */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.descriptionOptional')}
                </label>
                <textarea
                  defaultValue={selectedSkill.description || ""}
                  placeholder={t('skills.publishForm.placeholderSelect')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text min-h-[72px]"
                />
              </div>
              {/* 标签（可选） */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.tagsOptional')}
                </label>
                <input
                  type="text"
                  defaultValue={coerceStringList(selectedSkill.tags).join(", ")}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text"
                />
              </div>
              {/* Skill图标（可选）- 图片上传框 */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.skillIconOptional')}
                </label>
                <div
                  className="flex items-center justify-center rounded-[6px] border border-dashed border-border bg-secondary/30 cursor-pointer hover:bg-secondary/50"
                  style={{ width: '100px', height: '100px' }}
                >
                  <svg className="w-8 h-8 text-text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3.75 3.75h16.5a1.5 1.5 0 011.5 1.5v13.5a1.5 1.5 0 01-1.5 1.5H3.75a1.5 1.5 0 01-1.5-1.5V5.25a1.5 1.5 0 011.5-1.5z" />
                  </svg>
                </div>
                <span className="block mt-1.5 text-xs text-text-muted">
                  {t('skills.publishForm.skillIconHint')}
                </span>
              </div>
              {/* SHA-256 校验 */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.sha256')}
                </label>
                <input
                  type="text"
                  value={publishSha256}
                  onChange={(e) => setPublishSha256(e.target.value)}
                  placeholder={t('skills.publishForm.placeholderSha256')}
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text font-mono"
                />
              </div>
              {/* 版本说明（Swarm Skill，可选） */}
              <div>
                <label className="block text-sm font-medium text-text mb-1.5">
                  {t('skills.publishForm.versionNoteOptional')}
                </label>
                <textarea
                  className="w-full px-3 py-2 rounded-[6px] border border-border bg-panel text-sm text-text min-h-[72px]"
                />
              </div>
            </div>
            {/* 底部按钮 */}
            <div className="flex items-center justify-end gap-3 px-6 pb-4 pt-2 flex-shrink-0">
              <button
                type="button"
                onClick={() => setPublishDrawerOpen(false)}
                className="flex items-center justify-center rounded-[16px] text-sm text-[#191919] bg-white border border-[#191919] hover:bg-secondary/30 whitespace-nowrap"
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.publishForm.cancel')}
              </button>
              <button
                type="button"
                disabled={isPublishDisabled}
                onClick={() => setPublishDrawerOpen(false)}
                className={`flex items-center justify-center rounded-[16px] text-sm whitespace-nowrap transition-colors ${
                  isPublishDisabled
                    ? 'bg-[#E0E0E0] text-[#999999] cursor-not-allowed'
                    : 'text-text-inverse bg-[#191919] hover:opacity-80'
                }`}
                style={{ height: '32px', padding: '0 32px' }}
              >
                {t('skills.publishForm.publish')}
              </button>
            </div>
          </div>
        </>
        );
      })()}
    </div>
      </>
    );
}
