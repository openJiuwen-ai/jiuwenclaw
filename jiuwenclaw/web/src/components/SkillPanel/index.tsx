/**
 * SkillPanel 组件
 *
 * Skills 管理面板
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from 'react-i18next';
import { webRequest } from "../../services/webClient";
import { SourceManagerModal } from "../../features/SourceManagerModal";

type SkillItem = {
  name: string;
  description: string;
  source: string;
  version: string;
  author: string;
  tags: string[];
  allowed_tools: string[];
  marketplace?: string;
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
}

function getSourceLabel(source: string, t: (key: string) => string): string {
  if (source === "local") return t('skills.source.local');
  if (source === "project") return t('skills.source.project');
  return source || t('skills.source.unknown');
}

export function SkillPanel({ sessionId }: SkillPanelProps) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [plugins, setPlugins] = useState<InstalledPluginItem[]>([]);
  const [marketplaces, setMarketplaces] = useState<MarketplaceItem[]>([]);
  const [search, setSearch] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [actionTarget, setActionTarget] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"success" | "error" | null>(null);
  const [sourceModalOpen, setSourceModalOpen] = useState(false);
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

  const filteredSkills = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return skills;
    return skills.filter((skill) => {
      const haystack = [
        skill.name,
        skill.description,
        skill.author,
        skill.tags.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
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
      const [skillsData, pluginsData] = await Promise.all([
        webRequest<{ skills?: SkillItem[] }>(
          "skills.list",
          withSession(
            refreshMarketplaces ? { refresh_marketplaces: true } : undefined
          )
        ),
        webRequest<{ plugins?: InstalledPluginItem[] }>("skills.installed", withSession()),
      ]);
      setSkills(skillsData.skills || []);
      setPlugins(pluginsData.plugins || []);
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
        setSelectedSkill(data);
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

  const handleInstall = useCallback(
    async (skillName?: string) => {
      const marketplaceNames = marketplaces.map((m) => m.name).join(", ");
      const targetSkill = skillName
        ? skills.find((skill) => skill.name === skillName)
        : undefined;
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
      setMessage(null);
      setMessageType(null);
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          message?: string;
        }>("skills.install", withSession({ spec, force: false }));
        if (!data.success) {
          throw new Error(data.detail || data.message || t('skills.errors.installFailed'));
        }
        setMessage(t('skills.messages.installed', { spec }));
        setMessageType("success");
        await fetchSkills();
        if (selectedSkill) {
          await fetchSkillDetail(selectedSkill.name);
        }
      } catch (error) {
        console.error(error);
        setMessage(t('skills.errors.installFailedHint'));
        setMessageType("error");
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, fetchSkillDetail, selectedSkill, marketplaces, skills, withSession]
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
      setMessage(t('skills.messages.imported', { name: data.skill?.name || path }));
      setMessageType("success");
      await fetchSkills();
      if (data.skill?.name) {
        await fetchSkillDetail(data.skill.name);
      }
    } catch (error) {
      console.error(error);
      setMessage(t('skills.errors.importFailedHint'));
      setMessageType("error");
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
        setMessage(t('skills.messages.uninstalled', { pluginName }));
        setMessageType("success");
        await fetchSkills();
        if (selectedSkill) {
          await fetchSkillDetail(selectedSkill.name);
        }
      } catch (error) {
        console.error(error);
        setMessage(t('skills.errors.uninstallFailedHint'));
        setMessageType("error");
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, fetchSkillDetail, selectedSkill, t, withSession]
  );

  const renderActionButton = (skill: SkillItem) => {
    const plugin = installedSkillMap.get(skill.name);
    if (plugin) {
      const pluginName = plugin.plugin_name || skill.name;
      const isLoading = actionTarget === pluginName;
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleUninstall(pluginName);
          }}
          className={`px-3 py-1.5 rounded-md text-sm transition-colors whitespace-nowrap ${
            isLoading
              ? "bg-secondary text-text-muted cursor-not-allowed"
              : "bg-danger text-white hover:bg-danger/90"
          }`}
          disabled={isLoading}
        >
          {t('skills.actions.uninstall')}
        </button>
      );
    }

    if (skill.source !== "local" && skill.source !== "project") {
      const isLoading = Boolean(actionTarget?.startsWith(`${skill.name}@`));
      return (
        <button
          onClick={(event) => {
            event.stopPropagation();
            handleInstall(skill.name);
          }}
          className={`px-3 py-1.5 rounded-md text-sm transition-colors whitespace-nowrap ${
            isLoading
              ? "bg-secondary text-text-muted cursor-not-allowed"
              : "bg-accent text-white hover:bg-accent-hover"
          }`}
          disabled={isLoading}
        >
          {t('skills.actions.install')}
        </button>
      );
    }

    return (
      <button
        className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted cursor-not-allowed whitespace-nowrap"
        disabled
      >
        {t('skills.builtIn')}
      </button>
    );
  };

  const renderStatus = (skill: SkillItem) => {
    const plugin = installedSkillMap.get(skill.name);
    if (plugin) return t('skills.status.installed');
    if (skill.source !== "local" && skill.source !== "project") return t('skills.status.notInstalled');
    return t('skills.status.builtIn');
  };

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      <div className="card flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              {t('skills.title')}
            </h2>
            <p className="text-sm text-text-muted mt-1">
              {t('skills.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchSkills(true)}
              className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted hover:text-text hover:bg-card border border-border"
            >
              {t('common.refresh')}
            </button>
            <button
              onClick={handleImportLocal}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                actionTarget === "import_local"
                  ? "bg-secondary text-text-muted cursor-not-allowed"
                  : "bg-secondary text-text hover:bg-card border border-border"
              }`}
              disabled={actionTarget === "import_local"}
            >
              {t('skills.actions.importLocal')}
            </button>
            <button
              onClick={() => setSourceModalOpen(true)}
              className="px-3 py-1.5 rounded-md text-sm bg-accent text-white hover:bg-accent-hover"
            >
              {t('skills.actions.sourceManager')}
            </button>
          </div>
        </div>

        {message && (
          <div className={`mt-3 px-3 py-2 rounded-md bg-secondary text-sm ${
            messageType === "error" ? "text-danger" : "text-text"
          }`}>
            {message}
          </div>
        )}

        {selectedSkill ? (
          <div className="mt-4 flex-1 overflow-y-auto">
            <div className="flex items-center gap-2 mb-3">
              <button
                onClick={handleBackToList}
                className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted hover:text-text hover:bg-card border border-border"
              >
                {t('skills.actions.backToList')}
              </button>
              <div className="text-sm text-text-muted">
                {detailState === "loading" && t('skills.detailLoading')}
                {detailState === "error" && t('skills.detailError')}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-lg font-semibold text-text-strong">
                    {selectedSkill.name}
                  </div>
                  <div className="text-sm text-text-muted mt-1">
                    {selectedSkill.description || t('skills.noDescription')}
                  </div>
                  <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      {t('skills.sourceLabel')}: {getSourceLabel(selectedSkill.source, t)}
                    </span>
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      {t('skills.versionLabel')}: {selectedSkill.version || 'unknown'}
                    </span>
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      {t('skills.authorLabel')}: {selectedSkill.author || 'unknown'}
                    </span>
                  </div>
                </div>

                <div className="flex flex-col items-end gap-2">
                  {renderActionButton(selectedSkill)}
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
              <div className="flex-1 min-w-0">
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder={t('skills.searchPlaceholder')}
                  className="w-full px-3 py-2 rounded-md bg-panel border border-border text-sm text-text placeholder:text-text-muted"
                />
              </div>
              <div className="text-xs text-text-muted flex-shrink-0">
                {t('skills.totalCount', { count: filteredSkills.length })}
              </div>
            </div>

            <div className="mt-4 flex-1 min-h-0 overflow-y-auto space-y-3">
              {listState === "loading" && (
                <div className="text-sm text-text-muted">{t('common.loading')}</div>
              )}
              {listState === "error" && (
                <div className="text-sm text-text-muted">
                  {t('skills.listError')}
                </div>
              )}
              {listState === "success" && filteredSkills.length === 0 && (
                <div className="text-sm text-text-muted">{t('skills.noMatches')}</div>
              )}
                {listState === "success" &&
                filteredSkills.map((skill) => (
                  <button
                    key={skill.name}
                    onClick={() => handleOpenSkill(skill.name)}
                    className="w-full text-left p-4 rounded-lg border border-border bg-panel hover:bg-card transition-colors"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="text-base font-semibold text-text-strong">
                          {skill.name}
                        </div>
                        <div className="text-sm text-text-muted mt-1 line-clamp-3">
                          {skill.description || t('skills.noDescription')}
                        </div>
                        <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            {t('skills.sourceLabel')}: {getSourceLabel(skill.source, t)}
                          </span>
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            {t('skills.statusLabel')}: {renderStatus(skill)}
                          </span>
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-2 flex-shrink-0">
                        {renderActionButton(skill)}
                      </div>
                    </div>
                  </button>
                ))}
            </div>
          </div>
        )}
      </div>
      <SourceManagerModal
        open={sourceModalOpen}
        sessionId={sessionId}
        onClose={() => setSourceModalOpen(false)}
        onUpdated={async () => {
          await fetchSkills();
        }}
      />
    </div>
  );
}
