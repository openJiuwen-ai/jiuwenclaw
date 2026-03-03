/**
 * SkillPanel 组件
 *
 * Skills 管理面板
 */
import { useCallback, useEffect, useMemo, useState } from "react";
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

function getSourceLabel(source: string): string {
  if (source === "local") return "本地";
  if (source === "project") return "项目";
  return source || "未知";
}

export function SkillPanel({ sessionId }: SkillPanelProps) {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [plugins, setPlugins] = useState<InstalledPluginItem[]>([]);
  const [marketplaces, setMarketplaces] = useState<MarketplaceItem[]>([]);
  const [search, setSearch] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("idle");
  const [detailState, setDetailState] = useState<LoadState>("idle");
  const [actionTarget, setActionTarget] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
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
      console.error("获取 marketplaces 失败:", error);
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
        ? `可用 marketplace: ${marketplaceNames}`
        : "默认 marketplace: anthropics";
      const spec = window.prompt(
        `请输入插件规格 (plugin@marketplace)\n${hint}`,
        defaultSpec
      );
      if (!spec) return;

      setActionTarget(spec);
      setMessage(null);
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          message?: string;
        }>("skills.install", withSession({ spec, force: false }));
        if (!data.success) {
          throw new Error(data.detail || data.message || "安装失败");
        }
        setMessage(`已安装：${spec}`);
        await fetchSkills();
        if (selectedSkill) {
          await fetchSkillDetail(selectedSkill.name);
        }
      } catch (error) {
        console.error(error);
        setMessage("安装失败，请检查插件规格或网络");
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, fetchSkillDetail, selectedSkill, marketplaces, skills, withSession]
  );

  const handleImportLocal = useCallback(async () => {
    const path = window.prompt(
      "请输入服务端本地 skill 路径（SKILL.md 或目录）"
    );
    if (!path) return;

    setActionTarget("import_local");
    setMessage(null);
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
        throw new Error(data.detail || data.message || "导入失败");
      }
      setMessage(`已导入：${data.skill?.name || path}`);
      await fetchSkills();
      if (data.skill?.name) {
        await fetchSkillDetail(data.skill.name);
      }
    } catch (error) {
      console.error(error);
      setMessage("导入失败，请检查路径或权限");
    } finally {
      setActionTarget(null);
    }
  }, [fetchSkills, fetchSkillDetail, withSession]);

  const handleUninstall = useCallback(
    async (pluginName: string) => {
      if (!pluginName) return;
      const confirmed = window.confirm(`确认卸载 ${pluginName} ?`);
      if (!confirmed) return;

      setActionTarget(pluginName);
      setMessage(null);
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          message?: string;
        }>("skills.uninstall", withSession({
          name: pluginName,
        }));
        if (!data.success) {
          throw new Error(data.detail || data.message || "卸载失败");
        }
        setMessage(`已卸载：${pluginName}`);
        await fetchSkills();
        if (selectedSkill) {
          await fetchSkillDetail(selectedSkill.name);
        }
      } catch (error) {
        console.error(error);
        setMessage("卸载失败，请稍后重试");
      } finally {
        setActionTarget(null);
      }
    },
    [fetchSkills, fetchSkillDetail, selectedSkill, withSession]
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
          卸载
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
          安装
        </button>
      );
    }

    return (
      <button
        className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted cursor-not-allowed whitespace-nowrap"
        disabled
      >
        内置
      </button>
    );
  };

  const renderStatus = (skill: SkillItem) => {
    const plugin = installedSkillMap.get(skill.name);
    if (plugin) return "已安装";
    if (skill.source !== "local" && skill.source !== "project") return "未安装";
    return "内置";
  };

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      <div className="card flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              技能管理
            </h2>
            <p className="text-sm text-text-muted mt-1">
              管理并安装智能体可用的技能。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchSkills(true)}
              className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted hover:text-text hover:bg-card border border-border"
            >
              刷新
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
              导入本地技能
            </button>
            <button
              onClick={() => setSourceModalOpen(true)}
              className="px-3 py-1.5 rounded-md text-sm bg-accent text-white hover:bg-accent-hover"
            >
              源管理
            </button>
          </div>
        </div>

        {message && (
          <div className="mt-3 px-3 py-2 rounded-md bg-secondary text-sm text-text">
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
                返回列表
              </button>
              <div className="text-sm text-text-muted">
                {detailState === "loading" && "加载详情中..."}
                {detailState === "error" && "加载详情失败"}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-lg font-semibold text-text-strong">
                    {selectedSkill.name}
                  </div>
                  <div className="text-sm text-text-muted mt-1">
                    {selectedSkill.description || "暂无描述"}
                  </div>
                  <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      来源：{getSourceLabel(selectedSkill.source)}
                    </span>
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      版本：{selectedSkill.version || "unknown"}
                    </span>
                    <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                      作者：{selectedSkill.author || "unknown"}
                    </span>
                  </div>
                </div>

                <div className="flex flex-col items-end gap-2">
                  {renderActionButton(selectedSkill)}
                </div>
              </div>

              <div className="mt-4">
                <div className="text-sm font-medium text-text mb-2">
                  允许工具
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
                    <span className="text-text-muted">无限制</span>
                  )}
                </div>
              </div>

              <div className="mt-4">
                <div className="text-sm font-medium text-text mb-2">
                  内容预览
                </div>
                <div className="text-sm text-text whitespace-pre-wrap bg-secondary border border-border rounded-md p-3">
                  {selectedSkill.content || "暂无内容"}
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
                  placeholder="搜索技能名称、描述或标签"
                  className="w-full px-3 py-2 rounded-md bg-panel border border-border text-sm text-text placeholder:text-text-muted"
                />
              </div>
              <div className="text-xs text-text-muted flex-shrink-0">
                共 {filteredSkills.length} 个
              </div>
            </div>

            <div className="mt-4 flex-1 min-h-0 overflow-y-auto space-y-3">
              {listState === "loading" && (
                <div className="text-sm text-text-muted">加载中...</div>
              )}
              {listState === "error" && (
                <div className="text-sm text-text-muted">
                  获取技能失败，请检查后端服务
                </div>
              )}
              {listState === "success" && filteredSkills.length === 0 && (
                <div className="text-sm text-text-muted">暂无匹配的技能</div>
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
                          {skill.description || "暂无描述"}
                        </div>
                        <div className="flex flex-wrap gap-2 mt-3 text-xs text-text-muted">
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            来源：{getSourceLabel(skill.source)}
                          </span>
                          <span className="px-2 py-1 rounded-full bg-secondary border border-border">
                            状态：{renderStatus(skill)}
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
