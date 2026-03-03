import { useCallback, useEffect, useMemo, useState } from "react";
import { webRequest } from "../../services/webClient";

type MarketplaceItem = {
  name: string;
  url: string;
  install_location?: string;
  last_updated?: string | null;
  enabled?: boolean;
};

type LoadState = "idle" | "loading" | "success" | "error";

interface SourceManagerModalProps {
  open: boolean;
  sessionId: string;
  onClose: () => void;
  onUpdated?: () => Promise<void> | void;
}

export function SourceManagerModal({
  open,
  sessionId,
  onClose,
  onUpdated,
}: SourceManagerModalProps) {
  const [marketplaces, setMarketplaces] = useState<MarketplaceItem[]>([]);
  const [listState, setListState] = useState<LoadState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [actionTarget, setActionTarget] = useState<string | null>(null);
  const [nameInput, setNameInput] = useState("");
  const [urlInput, setUrlInput] = useState("");

  const withSession = useCallback(
    (params?: Record<string, unknown>) => ({
      ...(params || {}),
      session_id: sessionId,
    }),
    [sessionId]
  );

  const sortedMarketplaces = useMemo(
    () => [...marketplaces].sort((a, b) => a.name.localeCompare(b.name)),
    [marketplaces]
  );

  const fetchMarketplaces = useCallback(async () => {
    setListState("loading");
    try {
      const data = await webRequest<{ marketplaces?: MarketplaceItem[] }>(
        "skills.marketplace.list",
        withSession()
      );
      setMarketplaces(data.marketplaces || []);
      setListState("success");
    } catch (error) {
      console.error("获取源列表失败:", error);
      setListState("error");
    }
  }, [withSession]);

  useEffect(() => {
    if (!open) return;
    setMessage(null);
    void fetchMarketplaces();
  }, [open, fetchMarketplaces]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  const runAfterUpdate = useCallback(async () => {
    await fetchMarketplaces();
    if (onUpdated) {
      await onUpdated();
    }
  }, [fetchMarketplaces, onUpdated]);

  const handleAddSource = useCallback(async () => {
    const name = nameInput.trim();
    const url = urlInput.trim();
    if (!name || !url) {
      setMessage("请先填写源名称和仓库 URL");
      return;
    }

    setActionTarget("add");
    setMessage(null);
    try {
      const data = await webRequest<{ success: boolean; detail?: string; message?: string }>(
        "skills.marketplace.add",
        withSession({ name, url })
      );
      if (!data.success) {
        throw new Error(data.detail || data.message || "添加源失败");
      }
      setNameInput("");
      setUrlInput("");
      setMessage(`已添加源：${name}（默认禁用）`);
      await runAfterUpdate();
    } catch (error) {
      console.error(error);
      setMessage("添加源失败，请检查名称或 URL");
    } finally {
      setActionTarget(null);
    }
  }, [nameInput, runAfterUpdate, urlInput, withSession]);

  const handleRemoveSource = useCallback(
    async (name: string) => {
      const confirmed = window.confirm(`确认删除源 ${name} ?`);
      if (!confirmed) return;

      setActionTarget(`remove:${name}`);
      setMessage(null);
      try {
        const data = await webRequest<{ success: boolean; detail?: string; message?: string }>(
          "skills.marketplace.remove",
          withSession({ name, remove_cache: true })
        );
        if (!data.success) {
          throw new Error(data.detail || data.message || "删除源失败");
        }
        setMessage(`已删除源：${name}`);
        await runAfterUpdate();
      } catch (error) {
        console.error(error);
        setMessage("删除源失败，请稍后重试");
      } finally {
        setActionTarget(null);
      }
    },
    [runAfterUpdate, withSession]
  );

  const handleToggleSource = useCallback(
    async (source: MarketplaceItem) => {
      const targetEnabled = !Boolean(source.enabled ?? true);
      setActionTarget(`toggle:${source.name}`);
      setMessage(null);
      try {
        const data = await webRequest<{ success: boolean; detail?: string; message?: string }>(
          "skills.marketplace.toggle",
          withSession({ name: source.name, enabled: targetEnabled })
        );
        if (!data.success) {
          throw new Error(data.detail || data.message || "切换源状态失败");
        }
        setMessage(targetEnabled ? `已启用源：${source.name}` : `已禁用源：${source.name}`);
        await runAfterUpdate();
      } catch (error) {
        console.error(error);
        setMessage(targetEnabled ? "启用源失败，请检查网络或仓库地址" : "禁用源失败，请稍后重试");
      } finally {
        setActionTarget(null);
      }
    },
    [runAfterUpdate, withSession]
  );

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-label="关闭源管理弹窗"
      />
      <div className="relative w-full max-w-4xl max-h-[88vh] overflow-hidden rounded-xl border border-border bg-card shadow-2xl animate-rise">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-panel">
          <div>
            <h3 className="text-base font-semibold text-text">源管理</h3>
            <p className="text-xs text-text-muted">添加、删除或启用/禁用技能源</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void fetchMarketplaces()}
              className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted hover:text-text hover:bg-card border border-border"
            >
              刷新
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-md text-sm bg-secondary text-text-muted hover:text-text hover:bg-card border border-border"
            >
              关闭
            </button>
          </div>
        </div>

        <div className="p-5 overflow-auto max-h-[calc(88vh-64px)]">
          <div className="rounded-lg border border-border bg-panel p-4">
            <div className="text-sm font-medium text-text mb-3">添加源</div>
            <div className="grid grid-cols-1 md:grid-cols-[180px_1fr_auto] gap-2">
              <input
                value={nameInput}
                onChange={(event) => setNameInput(event.target.value)}
                placeholder="源名称"
                className="px-3 py-2 rounded-md bg-card border border-border text-sm text-text placeholder:text-text-muted"
              />
              <input
                value={urlInput}
                onChange={(event) => setUrlInput(event.target.value)}
                placeholder="Git 仓库地址（https://...）"
                className="px-3 py-2 rounded-md bg-card border border-border text-sm text-text placeholder:text-text-muted"
              />
              <button
                type="button"
                onClick={() => void handleAddSource()}
                className={`px-3 py-2 rounded-md text-sm transition-colors ${
                  actionTarget === "add"
                    ? "bg-secondary text-text-muted cursor-not-allowed"
                    : "bg-accent text-white hover:bg-accent-hover"
                }`}
                disabled={actionTarget === "add"}
              >
                添加
              </button>
            </div>
          </div>

          {message && (
            <div className="mt-3 px-3 py-2 rounded-md bg-secondary text-sm text-text">
              {message}
            </div>
          )}

          <div className="mt-4 rounded-lg border border-border bg-panel p-4">
            <div className="text-sm font-medium text-text mb-2">
              已配置源（{sortedMarketplaces.length} 个）
            </div>
            {listState === "loading" && (
              <div className="text-sm text-text-muted">加载中...</div>
            )}
            {listState === "error" && (
              <div className="text-sm text-text-muted">源列表加载失败，请重试</div>
            )}
            {listState === "success" && sortedMarketplaces.length === 0 && (
              <div className="text-sm text-text-muted">暂无已配置源</div>
            )}
            {listState === "success" && sortedMarketplaces.length > 0 && (
              <div className="space-y-2">
                {sortedMarketplaces.map((source) => {
                  const enabled = Boolean(source.enabled ?? true);
                  const toggleLoading = actionTarget === `toggle:${source.name}`;
                  const removeLoading = actionTarget === `remove:${source.name}`;
                  return (
                    <div
                      key={source.name}
                      className="flex items-center justify-between p-3 rounded-md bg-secondary gap-3"
                    >
                      <div className="min-w-0">
                        <div className="text-sm text-text font-medium">{source.name}</div>
                        <div className="text-xs text-text-muted break-all">{source.url}</div>
                        {source.last_updated && (
                          <div className="text-xs text-text-muted mt-1">
                            更新于 {new Date(source.last_updated).toLocaleString()}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span
                          className={`px-2 py-1 text-xs rounded-full border ${
                            enabled
                              ? "bg-ok/15 text-ok border-ok/30"
                              : "bg-secondary text-text-muted border-border"
                          }`}
                        >
                          {enabled ? "已启用" : "已禁用"}
                        </span>
                        <button
                          type="button"
                          onClick={() => void handleToggleSource(source)}
                          className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                            toggleLoading
                              ? "bg-secondary text-text-muted cursor-not-allowed"
                              : enabled
                                ? "bg-secondary text-text hover:bg-card border border-border"
                                : "bg-accent text-white hover:bg-accent-hover"
                          }`}
                          disabled={toggleLoading || removeLoading}
                        >
                          {enabled ? "禁用" : "启用"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleRemoveSource(source.name)}
                          className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                            removeLoading
                              ? "bg-secondary text-text-muted cursor-not-allowed"
                              : "bg-danger text-white hover:bg-danger/90"
                          }`}
                          disabled={toggleLoading || removeLoading}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
