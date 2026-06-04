/**
 * SkillPanel 组件
 *
 * Skills 管理面板
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from 'react-i18next';
import { webRequest } from "../../services/webClient";
import { SourceManagerModal } from "../../features/SourceManagerModal";
import { SkillNetSearchModal } from "../../features/SkillNetSearchModal";
import { ClawHubSearchModal } from "../../features/ClawHubSearchModal";
import { TeamSkillsHubModal } from "../../features/TeamSkillsHubModal";
import { SkillEvolutionModal } from "../../features/SkillEvolutionModal";
import { normalizeSkillNetUrl } from "../../utils/skillNetUrl";
import { Switch } from "../Switch";

/** 刷新会 git pull marketplace，略放宽；普通进页单次 RPC 一般很快。 */
const SKILLS_FETCH_TIMEOUT_REFRESH_MS = 60_000;
const SKILLS_FETCH_TIMEOUT_NORMAL_MS = 30_000;

type SkillItem = {
  name: string;
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
};

type InstalledPluginItem = {
  plugin_name: string;
  marketplace: string;
  spec: string;
  version: string;
  installed_at: string;
  git_commit?: string | null;
  skills: string[];
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

type LoadState = "idle" | "loading" | "success" | "error";

interface SkillPanelProps {
  sessionId: string;
  onNavigateToConfig?: () => void;
}

function getSourceLabel(source: string, t: (key: string) => string, isBuiltinSource?: boolean): string {
  if (isBuiltinSource) return t('skills.source.builtin');
  if (source === "local") return t('skills.source.local');
  if (source === "project") return t('skills.source.project');
  if (source === "builtin") return t('skills.source.builtin');
  return source || t('skills.source.unknown');
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

export function SkillPanel({ sessionId, onNavigateToConfig }: SkillPanelProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<"my" | "marketplace">("my");
  const [mySkillsSubTab, setMySkillsSubTab] = useState<"all" | "enabled" | "disabled">("all");
  const [marketplaceSubTab, setMarketplaceSubTab] = useState<"builtin" | "swarmskills" | "online">("builtin");
  const [onlineSource, setOnlineSource] = useState<"skillnet" | "clawhub">("skillnet");
  const [searchTrigger, setSearchTrigger] = useState(0);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [plugins, setPlugins] = useState<InstalledPluginItem[]>([]);
  const [marketplaces, setMarketplaces] = useState<MarketplaceItem[]>([]);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const searchDebounceRef = useRef<number | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [actionTarget, setActionTarget] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"success" | "error" | "loading" | null>(null);
  const messageTimerRef = useRef<number | null>(null);
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");

  useEffect(() => {
    return () => {
      if (messageTimerRef.current !== null) {
        window.clearTimeout(messageTimerRef.current);
      }
      if (searchDebounceRef.current !== null) {
        window.clearTimeout(searchDebounceRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (searchDebounceRef.current !== null) {
      window.clearTimeout(searchDebounceRef.current);
    }
    searchDebounceRef.current = window.setTimeout(() => {
      setDebouncedSearch(search);
      searchDebounceRef.current = null;
    }, 500);
  }, [search]);

  const showMessage = useCallback((type: "success" | "error", text: string) => {
    if (messageTimerRef.current !== null) {
      window.clearTimeout(messageTimerRef.current);
    }
    const displayText = type === "success" ? `√ ${text}` : text;
    setMessage(displayText);
    setMessageType(type);
    messageTimerRef.current = window.setTimeout(() => {
      setMessage(null);
      setMessageType(null);
      messageTimerRef.current = null;
    }, 3000);
  }, []);
  const [sourceModalOpen, setSourceModalOpen] = useState(false);
  const [skillNetModalOpen, setSkillNetModalOpen] = useState(false);
  const [clawHubModalOpen, setClawHubModalOpen] = useState(false);
  const [teamSkillsHubModalOpen, setTeamSkillsHubModalOpen] = useState(false);
  const [evolutionModalOpen, setEvolutionModalOpen] = useState(false);
  const [evolutionSkillName, setEvolutionSkillName] = useState<string | null>(null);
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
        if (!map.has(skill)) {
          map.set(skill, plugin);
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
        skill.is_builtin === true || 
        skill.is_builtin_source === true
      );
    }
    const keyword = search.trim().toLowerCase();
    if (!keyword) return result;
    return result.filter((skill) => {
      const haystack = [
        skill.name,
        skill.description,
        skill.author,
        coerceStringList(skill.tags).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [skills, search, activeTab, installedSkillMap]);

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
    return filtered.sort((a, b) => {
      const aSkillNet = a.source === "skillnet" ? 1 : 0;
      const bSkillNet = b.source === "skillnet" ? 1 : 0;
      if (aSkillNet !== bSkillNet) {
        return bSkillNet - aSkillNet;
      }
      return a.name.localeCompare(b.name);
    });
  }, [filteredSkills, activeTab, installedSkillMap]);

  const builtinSkills = useMemo(() => {
    let filtered = skills.filter((skill) => skill.is_builtin === true);
    if (search.trim()) {
      const searchLower = search.toLowerCase();
      filtered = filtered.filter(
        (skill) =>
          skill.name.toLowerCase().includes(searchLower) ||
          (skill.description && skill.description.toLowerCase().includes(searchLower))
      );
    }
    return filtered;
  }, [skills, search]);

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
        withSession({
          with_installed: true,
          ...(refreshMarketplaces ? { refresh_marketplaces: true } : {}),
        }),
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
        setDetailState("success");
      } catch (error) {
        console.error(error);
        setDetailState("error");
      }
    },
    [withSession]
  );

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

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

  const handleOpenEvolution = useCallback((skillName: string) => {
    setEvolutionSkillName(skillName);
    setEvolutionModalOpen(true);
  }, []);

  const handleCloseEvolution = useCallback(() => {
    setEvolutionModalOpen(false);
    setEvolutionSkillName(null);
  }, []);

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

  const handleImportLocal = useCallback(async () => {
    const path = window.prompt(
      t('skills.importPrompt')
    );
    if (!path) return;

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
        path,
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

  const avatarColors = [
    "bg-red-500",
    "bg-orange-500",
    "bg-amber-500",
    "bg-yellow-500",
    "bg-lime-500",
    "bg-green-500",
    "bg-emerald-500",
    "bg-teal-500",
    "bg-cyan-500",
    "bg-sky-500",
    "bg-blue-500",
    "bg-indigo-500",
    "bg-violet-500",
    "bg-purple-500",
    "bg-fuchsia-500",
    "bg-pink-500",
    "bg-rose-500",
  ];

  const getSkillAvatar = (name: string) => {
    const firstChar = name.charAt(0).toUpperCase();
    const colorIndex = name.charCodeAt(0) % avatarColors.length;
    return { firstChar, color: avatarColors[colorIndex] };
  };

  const renderActionButton = (skill: SkillItem) => {
    const plugin = installedSkillMap.get(skill.name);

    // 未安装到用户目录的内置技能（来自内置目录，需要安装）
    // 判断条件：is_builtin_source 为 true 且不在已安装列表中
    const isInstalled = installedSkillMap.has(skill.name) || skill.source === "local";
    if (skill.is_builtin_source && !isInstalled) {
      const isLoading = actionTarget === `${skill.name}@builtin`;
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleInstall(skill.name);
          }}
          className="skill-action-btn"
          disabled={isLoading}
        >
          {isLoading ? t('skills.actions.installing') : t('skills.actions.install')}
        </button>
      );
    }

    // 用户本地导入的技能（source="local"）允许删除
    if (skill.source === "local") {
      const isLoading = actionTarget === skill.name;
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleUninstall(skill.name);
          }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm whitespace-nowrap hover:bg-secondary transition-colors"
          disabled={isLoading}
          style={{ color: '#191919' }}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} style={{ color: '#191919' }}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          {t('skills.actions.uninstall')}
        </button>
      );
    }

    // Marketplace 安装的技能
    if (plugin) {
      const pluginName = plugin.plugin_name || skill.name;
      const isLoading = actionTarget === pluginName;
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleUninstall(pluginName);
          }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm whitespace-nowrap hover:bg-secondary transition-colors"
          disabled={isLoading}
          style={{ color: '#191919' }}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} style={{ color: '#191919' }}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          {t('skills.actions.uninstall')}
        </button>
      );
    }

    // Marketplace 中未安装的技能显示安装按钮
    if (skill.source !== "project") {
      const isLoading = Boolean(actionTarget?.startsWith(`${skill.name}@`));
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleInstall(skill.name);
          }}
          className="skill-action-btn"
          disabled={isLoading}
        >
          {isLoading ? t('skills.actions.installing') : t('skills.actions.install')}
        </button>
      );
    }

    // 已安装到用户目录的内置技能（从内置目录复制过来的）
    // 这种情况下 source 可能是 "project"，但 is_builtin_source 为 true
    // 只对已安装的内置技能显示卸载按钮
    if (skill.is_builtin_source && isInstalled) {
      const isLoading = actionTarget === skill.name;
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleUninstall(skill.name);
          }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm whitespace-nowrap hover:bg-secondary transition-colors"
          disabled={isLoading}
          style={{ color: '#191919' }}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} style={{ color: '#191919' }}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          {t('skills.actions.uninstall')}
        </button>
      );
    }

    // 默认显示内置（兜底）
    return (
      <button
        className="px-4 py-2 rounded-2xl text-sm text-text-muted cursor-not-allowed whitespace-nowrap border border-gray-300"
        disabled
      >
        {t('skills.builtIn')}
      </button>
    );
  };

  const renderStatus = (skill: SkillItem) => {
    if (installedSkillMap.has(skill.name)) return t('skills.status.installed');
    if (skill.source === "local") return t('skills.status.installed');
    if (skill.is_builtin) {
      return t('skills.status.notInstalled');
    }
    if (skill.source !== "project") return t('skills.status.notInstalled');
    return t('skills.status.builtIn');
  };

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

  const renderEvolutionButton = (skill: SkillItem) => {
    const disabled = !skill.has_evolutions;
    if (disabled) {
      return null;
    }
    return (
      <button
        onClick={(event) => {
          event.stopPropagation();
          handleOpenEvolution(skill.name);
        }}
        className="px-4 py-2 rounded-2xl transition-colors whitespace-nowrap hover:opacity-80"
        style={{ color: "#0067d1", fontSize: "12px" }}
      >
        {t('skills.actions.viewEvolution')}
      </button>
    );
  };

  const cleanMessage = message?.replace("√", "") || "";
  return (
    <>
      {message && messageType === "success" && (
        <div className="fixed top-4 right-4 z-[9999] rounded-[4px] text-sm text-black shadow-lg flex items-center gap-3 px-4" style={{ backgroundColor: "#d5f2dc", width: "564px", height: "40px" }}>
          <span className="w-4 h-4 rounded-full bg-[#1a991d] flex items-center justify-center flex-shrink-0">
            <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          </span>
          {cleanMessage}
          <button
            type="button"
            onClick={() => setMessage(null)}
            className="ml-auto w-6 h-6 flex items-center justify-center hover:bg-white/30 rounded-full transition-colors"
          >
            <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <div className="card flex-1 flex flex-col min-h-0 overflow-hidden">
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
              className="flex items-center gap-1.5 px-1 py-1.5 rounded-lg text-sm text-text-muted hover:text-text hover:bg-secondary/50 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
              </svg>
              {t('skills.actions.sourceManager')}
            </button>
            <button
              onClick={() => fetchSkills(true)}
              className="flex items-center gap-1.5 px-1 py-1.5 rounded-lg text-sm text-text-muted hover:text-text hover:bg-secondary/50 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {t('common.refresh')}
            </button>
            <button
              onClick={handleImportLocal}
              className={`flex items-center gap-1.5 px-1 py-1.5 rounded-lg text-sm transition-colors ${
                actionTarget === "import_local"
                  ? "text-text-muted cursor-not-allowed"
                  : "text-text-muted hover:text-text hover:bg-secondary/50"
              }`}
              disabled={actionTarget === "import_local"}
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v9m0 0l-3-3m3 3l3-3" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 15v4a2 2 0 002 2h10a2 2 0 002-2v-4" />
              </svg>
              {t('skills.actions.importLocal')}
            </button>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab("my")}
              className={`px-4 text-sm font-medium transition-colors ${
                activeTab === "my"
                  ? "rounded-[8px] bg-secondary h-8 text-text"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {t('skills.tabs.mySkills')}
            </button>
            <button
              onClick={() => setActiveTab("marketplace")}
              className={`px-4 text-sm font-medium transition-colors ${
                activeTab === "marketplace"
                  ? "rounded-[8px] bg-secondary h-8 text-text"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {t('skills.tabs.marketplace')}
            </button>
          </div>
          <div className="flex items-center gap-1 border border-border rounded-lg p-1">
            <button
              onClick={() => setViewMode("list")}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === "list"
                  ? "bg-secondary text-text"
                  : "text-text-muted hover:text-text"
              }`}
              title={t('skills.viewMode.list')}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode("grid")}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === "grid"
                  ? "bg-secondary text-text"
                  : "text-text-muted hover:text-text"
              }`}
              title={t('skills.viewMode.grid')}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" />
              </svg>
            </button>
          </div>
        </div>

        {activeTab === "marketplace" ? (
          <>
            <div className="mt-4 flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    setMarketplaceSubTab("builtin");
                    setDebouncedSearch(search);
                    setSearchTrigger((prev) => prev + 1);
                  }}
                  className={`px-4 text-sm font-medium transition-colors ${
                    marketplaceSubTab === "builtin"
                      ? "rounded-[8px] bg-secondary h-8 text-text"
                      : "text-text-muted hover:text-text"
                  }`}
                >
                  {t('skills.marketplaceTabs.builtin')}
                </button>
              <button
                onClick={() => {
                  setMarketplaceSubTab("swarmskills");
                  setDebouncedSearch(search);
                  setSearchTrigger((prev) => prev + 1);
                }}
                className={`px-4 text-sm font-medium transition-colors ${
                  marketplaceSubTab === "swarmskills"
                    ? "rounded-[8px] bg-secondary h-8 text-text"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {t('skills.swarmskills.title')}
              </button>
              <button
                onClick={() => {
                  setMarketplaceSubTab("online");
                  setDebouncedSearch(search);
                  setSearchTrigger((prev) => prev + 1);
                }}
                className={`px-4 text-sm font-medium transition-colors ${
                  marketplaceSubTab === "online"
                    ? "rounded-[8px] bg-secondary h-8 text-text"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {t('skills.onlineSearch.title')}
              </button>
              </div>
              <div className="flex-1">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={
                    marketplaceSubTab === "builtin"
                      ? t("skills.searchPlaceholder")
                      : marketplaceSubTab === "swarmskills"
                      ? t("skills.swarmskills.searchPlaceholder")
                      : onlineSource === "skillnet"
                      ? t("skills.skillNet.searchPlaceholder")
                      : t("skills.clawhub.searchPlaceholder")
                  }
                  className="w-full px-3 py-1.5 rounded-lg text-sm bg-secondary border border-border text-text placeholder:text-text-muted"
                />
              </div>
            </div>

            <div className={`mt-4 flex-1 min-h-0 overflow-y-auto ${viewMode === "grid" && marketplaceSubTab === "builtin" ? "flex flex-wrap gap-4 content-start" : "space-y-3"}`}>
              {marketplaceSubTab === "builtin" && (
                <>
                  {builtinSkills.length === 0 ? (
                    <div className="text-sm text-text-muted">{t('skills.noMatches')}</div>
                  ) : (
                    builtinSkills.map((skill) => {
                      const avatar = getSkillAvatar(skill.name);
                      const isDisabled = skill.enabled === false;
                      const isToggling = actionTarget === `toggle:${skill.name}`;
                      const isInstalled = installedSkillMap.has(skill.name) || skill.source === "local";
                      const isInstalling = actionTarget === `${skill.name}@builtin`;
                      return (
                        <div
                          key={skill.name}
                          onClick={() => handleOpenSkill(skill.name)}
                          className={`text-left border border-border bg-panel hover:bg-card transition-colors cursor-pointer ${viewMode === "grid" ? "rounded-[8px] p-4 flex flex-col" : "w-full rounded-lg p-4"}`}
                          style={viewMode === "grid" ? { width: "496px", height: "168px", flexShrink: 0 } : undefined}
                        >
                          {viewMode === "list" ? (
                            <div className="flex items-center justify-between gap-4">
                              <div className="flex items-center gap-3 min-w-0 flex-1">
                                <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold`}>
                                  {avatar.firstChar}
                                </div>
                                <div className="min-w-0">
                                  <div className="text-base font-semibold text-text-strong">
                                    {skill.name}
                                  </div>
                                  <div className="text-sm text-text-muted mt-1 line-clamp-3">
                                    {skill.description || t('skills.noDescription')}
                                  </div>
                                </div>
                              </div>
                              <div className="flex items-center gap-4 flex-shrink-0">
                                {skill.is_builtin_source && !isInstalled ? (
                                  <button
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      handleInstall(skill.name);
                                    }}
                                    className="px-3 py-1 text-sm rounded-full border border-black bg-white text-black hover:bg-gray-100 transition-colors"
                                    style={{ width: '76px', height: '28px' }}
                                    disabled={isInstalling}
                                  >
                                    {isInstalling ? t('skills.actions.installing') : t('skills.actions.install')}
                                  </button>
                                ) : (
                                  <Switch
                                    checked={!isDisabled}
                                    onChange={() => toggleSkillDisabled(skill.name)}
                                    title={isDisabled ? t('skills.mySkillsTabs.all') : t('skills.mySkillsTabs.disabled')}
                                    disabled={isToggling}
                                  />
                                )}
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="flex items-start gap-3 flex-shrink-0">
                                <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold text-sm`}>
                                  {avatar.firstChar}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="text-sm font-semibold text-text-strong truncate">
                                    {skill.name}
                                  </div>
                                  <div className="text-xs text-text-muted mt-1 line-clamp-2">
                                    {skill.description || t('skills.noDescription')}
                                  </div>
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-1.5 mt-2 flex-shrink-0 text-xs text-text-muted">
                                <span className="px-2 py-0.5 rounded-full bg-secondary border border-border truncate">
                                  {t('skills.sourceLabel')}: {getSourceLabel(skill.source, t, skill.is_builtin_source)}
                                </span>
                              </div>
                              <div className="flex items-center mt-auto pt-2 gap-2 flex-shrink-0" style={{ width: "100%" }}>
                                <div className="flex gap-1.5 flex-1">
                                  {renderEvolutionButton(skill)}
                                </div>
                                <div className="flex-shrink-0 ml-auto">
                                  {renderActionButton(skill)}
                                </div>
                              </div>
                            </>
                          )}
                        </div>
                      );
                    })
                  )}
                </>
              )}

              {marketplaceSubTab === "swarmskills" && (
                <div className="h-full" key={`swarmskills-${searchTrigger}`}>
                  <TeamSkillsHubModal
                    open={true}
                    embedded={true}
                    sessionId={sessionId}
                    externalSearchQuery={debouncedSearch}
                    installedSkillNames={installedSkillNames}
                    viewMode={viewMode}
                    onClose={() => {}}
                    onInstalled={(_skillName: string) => {
                      void fetchSkills();
                    }}
                  />
                </div>
              )}

              {marketplaceSubTab === "online" && onlineSource === "skillnet" && (
                <div className="h-full" key={`skillnet-${searchTrigger}`}>
                  <SkillNetSearchModal
                    open={true}
                    embedded={true}
                    sessionId={sessionId}
                    externalSearchQuery={debouncedSearch}
                    installedSkillNames={installedSkillNames}
                    installedSkillOrigins={new Set()}
                    viewMode={viewMode}
                    onClose={() => {}}
                    onInstalled={(_skillName: string) => {
                      void fetchSkills();
                    }}
                  />
                </div>
              )}

              {marketplaceSubTab === "online" && onlineSource === "clawhub" && (
                <div className="h-full" key={`clawhub-${searchTrigger}`}>
                  <ClawHubSearchModal
                    open={true}
                    embedded={true}
                    sessionId={sessionId}
                    externalSearchQuery={debouncedSearch}
                    installedSkillNames={installedSkillNames}
                    installedSkillOrigins={installedSkillOrigins}
                    viewMode={viewMode}
                    onClose={() => {}}
                    onInstalled={(_skillName: string) => {
                      void fetchSkills();
                    }}
                  />
                </div>
              )}
            </div>
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
              <div className="mt-4 flex-1 overflow-y-auto">
                <div className="text-sm text-text-muted mb-3">
                  {detailState === "loading" && t('skills.detailLoading')}
                  {detailState === "error" && t('skills.detailError')}
                </div>

                <div className="rounded-lg border border-border bg-panel p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3">
                      <button
                        onClick={handleBackToList}
                        className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center text-text-muted hover:text-text hover:bg-secondary/50 transition-colors"
                      >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                        </svg>
                      </button>
                      <div className={`w-10 h-10 rounded-lg ${getSkillAvatar(selectedSkill.name).color} flex items-center justify-center flex-shrink-0 text-white font-semibold`}>
                        {getSkillAvatar(selectedSkill.name).firstChar}
                      </div>
                      <div>
                        <div className="text-lg font-semibold text-text-strong">
                          {selectedSkill.name}
                        </div>
                        <div className="text-sm text-text-muted mt-1">
                          {selectedSkill.description || t('skills.noDescription')}
                        </div>
                        <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            {t('skills.sourceLabel')}: {getSourceLabel(selectedSkill.source, t, selectedSkill.is_builtin_source)}
                          </span>
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            {t('skills.versionLabel')}: {selectedSkill.version || 'unknown'}
                          </span>
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            {t('skills.authorLabel')}: {selectedSkill.author || 'unknown'}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-2">
                      <div className="flex items-center gap-4">
                        <div className="flex items-center gap-2">
                          <span className="text-sm whitespace-nowrap" style={{ color: '#191919' }}>{selectedSkill.enabled === false ? t('skills.mySkillsTabs.disabled') : t('skills.mySkillsTabs.enabled')}</span>
                          <Switch
                            checked={selectedSkill.enabled !== false}
                            onChange={() => toggleSkillDisabled(selectedSkill.name)}
                            title={selectedSkill.enabled === false ? t('skills.mySkillsTabs.all') : t('skills.mySkillsTabs.disabled')}
                            disabled={actionTarget === `toggle:${selectedSkill.name}`}
                          />
                        </div>
                        {renderActionButton(selectedSkill)}
                      </div>
                      {renderEvolutionButton(selectedSkill)}
                    </div>
                  </div>

                  <div className="mt-4">
                    <div className="text-sm font-medium text-text mb-2">
                      {t('skills.allowedTools')}
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs text-text-muted">
                      {selectedSkill.allowed_tools?.length ? (
                        selectedSkill.allowed_tools.map((tool) => (
                          <span
                            key={tool}
                            className="px-2 py-1 rounded-full bg-secondary border border-border"
                          >
                            {tool}
                          </span>
                        ))
                      ) : (
                        <span className="text-text-muted">{t('skills.unlimited')}</span>
                      )}
                    </div>
                  </div>

                  <div className="mt-4">
                    <div className="text-sm font-medium text-text mb-2">
                      {t('skills.contentPreview')}
                    </div>
                    <div className="text-sm text-text whitespace-pre-wrap bg-secondary border border-border rounded-md p-3">
                      {selectedSkill.content || t('skills.noContent')}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-4 flex flex-col flex-1 min-h-0">
                <div className="flex items-center gap-3 flex-shrink-0">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setMySkillsSubTab("all")}
                      className={`px-4 text-sm font-medium transition-colors ${
                        mySkillsSubTab === "all"
                          ? "rounded-[8px] bg-secondary h-8 text-text"
                          : "text-text-muted hover:text-text"
                      }`}
                    >
                      {t('skills.mySkillsTabs.all')}
                    </button>
                    <button
                      onClick={() => setMySkillsSubTab("enabled")}
                      className={`px-4 text-sm font-medium transition-colors ${
                        mySkillsSubTab === "enabled"
                          ? "rounded-[8px] bg-secondary h-8 text-text"
                          : "text-text-muted hover:text-text"
                      }`}
                    >
                      {t('skills.mySkillsTabs.enabled')}
                    </button>
                    <button
                      onClick={() => setMySkillsSubTab("disabled")}
                      className={`px-4 text-sm font-medium transition-colors ${
                        mySkillsSubTab === "disabled"
                          ? "rounded-[8px] bg-secondary h-8 text-text"
                          : "text-text-muted hover:text-text"
                      }`}
                    >
                      {t('skills.mySkillsTabs.disabled')}
                    </button>
                  </div>
                  <div className="flex-1 min-w-0">
                    <input
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder={t('skills.searchPlaceholder')}
                      className="w-full px-3 py-2 rounded-md bg-panel border border-border text-sm text-text placeholder:text-text-muted"
                    />
                  </div>
                  <div className="text-xs text-text-muted flex-shrink-0">
                    {t('skills.totalCount', { count: getMySkillsFiltered().length })}
                  </div>
                </div>

                <div className={`mt-4 flex-1 min-h-0 overflow-y-auto ${viewMode === "grid" ? "flex flex-wrap gap-4 content-start" : "space-y-3"}`}>
                  {listState === "loading" && (
                    <div className="text-sm text-text-muted">{t('common.loading')}</div>
                  )}
                  {listState === "error" && (
                    <div className="text-sm text-text-muted">
                      {t('skills.listError')}
                    </div>
                  )}
                  {listState === "success" && getMySkillsFiltered().length === 0 && (
                    <div className="text-sm text-text-muted">
                      {mySkillsSubTab === "disabled" ? t('skills.noDisabledSkills') : 
                       mySkillsSubTab === "enabled" ? t('skills.noEnabledSkills') :
                       t('skills.noMatches')}
                    </div>
                  )}
                  {listState === "success" &&
                    getMySkillsFiltered().map((skill) => {
                      const avatar = getSkillAvatar(skill.name);
                      const isDisabled = skill.enabled === false;
                      const isToggling = actionTarget === `toggle:${skill.name}`;
                      return (
                        <div
                          key={skill.name}
                          onClick={() => handleOpenSkill(skill.name)}
                          className={`text-left border border-border bg-panel hover:bg-card transition-colors cursor-pointer ${viewMode === "grid" ? "rounded-[8px] p-4 flex flex-col" : "w-full rounded-lg p-4"}`}
                          style={viewMode === "grid" ? { width: "496px", height: "168px", flexShrink: 0 } : undefined}
                        >
                          {viewMode === "list" ? (
                            <div className="flex items-center justify-between gap-4">
                              <div className="flex items-center gap-3 min-w-0 flex-1">
                                <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold`}>
                                  {avatar.firstChar}
                                </div>
                                <div className="min-w-0">
                                  <div className="text-base font-semibold text-text-strong">
                                    {skill.name}
                                  </div>
                                  <div className="text-sm text-text-muted mt-1 line-clamp-3">
                                    {skill.description || t('skills.noDescription')}
                                  </div>
                                  <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                                      {t('skills.sourceLabel')}: {getSourceLabel(skill.source, t, skill.is_builtin_source)}
                                    </span>
                                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                                      {t('skills.statusLabel')}: {renderStatus(skill)}
                                    </span>
                                  </div>
                                </div>
                              </div>
                              <div className="flex flex-col items-end gap-2 flex-shrink-0">
                                {renderEvolutionButton(skill)}
                                <div className="flex items-center gap-2">
                                  <Switch
                                    checked={!isDisabled}
                                    onChange={() => toggleSkillDisabled(skill.name)}
                                    title={isDisabled ? t('skills.mySkillsTabs.all') : t('skills.mySkillsTabs.disabled')}
                                    disabled={isToggling}
                                  />
                                </div>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="flex items-start gap-3 flex-shrink-0">
                                <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold text-sm`}>
                                  {avatar.firstChar}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="text-sm font-semibold text-text-strong truncate">
                                    {skill.name}
                                  </div>
                                  <div className="text-xs text-text-muted mt-1 line-clamp-2">
                                    {skill.description || t('skills.noDescription')}
                                  </div>
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-1.5 mt-2 flex-shrink-0 text-xs text-text-muted">
                                <span className="px-2 py-0.5 rounded-full bg-secondary border border-border truncate">
                                  {t('skills.sourceLabel')}: {getSourceLabel(skill.source, t, skill.is_builtin_source)}
                                </span>
                                <span className="px-2 py-0.5 rounded-full bg-secondary border border-border truncate">
                                  {t('skills.statusLabel')}: {renderStatus(skill)}
                                </span>
                              </div>
                              <div className="flex items-center mt-auto pt-2 gap-2 flex-shrink-0" style={{ width: "100%" }}>
                                <div className="flex gap-1.5 flex-1">
                                  {renderEvolutionButton(skill)}
                                </div>
                                <div className="flex items-center gap-2">
                                  <Switch
                                    checked={!isDisabled}
                                    onChange={() => toggleSkillDisabled(skill.name)}
                                    title={isDisabled ? t('skills.mySkillsTabs.all') : t('skills.mySkillsTabs.disabled')}
                                    disabled={isToggling}
                                  />
                                </div>
                              </div>
                            </>
                          )}
                        </div>
                      );
                    })}
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>
      <SourceManagerModal
        open={sourceModalOpen}
        sessionId={sessionId}
        onClose={() => setSourceModalOpen(false)}
        currentSource={onlineSource}
        onSourceChange={(source) => setOnlineSource(source)}
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
      <SkillEvolutionModal
        open={evolutionModalOpen}
        sessionId={sessionId}
        skillName={evolutionSkillName}
        onClose={handleCloseEvolution}
        onSaved={async () => {
          await fetchSkills();
          if (selectedSkill) {
            await fetchSkillDetail(selectedSkill.name);
          }
        }}
      />
    </div>
      </>
    );
}
