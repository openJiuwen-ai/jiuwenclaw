import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { webRequest } from "../../services/webClient";

export type PermissionsToolsEditorProps = {
  isConnected: boolean;
};

type PermLevel = "allow" | "ask" | "deny";

type ToolRow = {
  name: string;
  shortDescription: string;
  level: PermLevel;
  configured: boolean;
};

function normalizeLevel(value: unknown): PermLevel | null {
  if (typeof value === "string") {
    const l = value.trim().toLowerCase();
    if (l === "allow" || l === "ask" || l === "deny") return l;
    return null;
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const star = (value as Record<string, unknown>)["*"];
    if (typeof star === "string") {
      const l = star.trim().toLowerCase();
      if (l === "allow" || l === "ask" || l === "deny") return l;
    }
  }
  return null;
}

function parseToolsListFromPayload(data: Record<string, unknown>): ToolRow[] {
  const tools = data.tools;
  if (!Array.isArray(tools)) return [];
  const out: ToolRow[] = [];
  for (const item of tools) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const name = String(row.name ?? "").trim();
    if (!name) continue;
    out.push({
      name,
      shortDescription: String(row.short_description ?? "").trim(),
      level: normalizeLevel(row.level) ?? "ask",
      configured: row.configured === true,
    });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export function PermissionsToolsEditor({ isConnected }: PermissionsToolsEditorProps) {
  const { t } = useTranslation();
  const [tools, setTools] = useState<ToolRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newLevel, setNewLevel] = useState<PermLevel>("ask");

  const load = useCallback(async () => {
    if (!isConnected) return;
    setLoading(true);
    setError(null);
    try {
      const data = await webRequest<Record<string, unknown>>("permissions.tools.list", {});
      setTools(parseToolsListFromPayload(data));
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("config.permissionsTools.loadFailed");
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [isConnected, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleLevelChange = async (tool: string, level: PermLevel) => {
    if (!isConnected || !tool) return;
    setBusyKey(tool);
    setError(null);
    try {
      await webRequest<Record<string, unknown>>("permissions.tools.update", {
        tool,
        level,
      });
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("config.permissionsTools.saveFailed");
      setError(msg);
    } finally {
      setBusyKey(null);
    }
  };

  const handleDelete = async (tool: string) => {
    if (!isConnected || !tool) return;
    if (!window.confirm(t("config.permissionsTools.deleteConfirm", { tool }))) return;
    setBusyKey(tool);
    setError(null);
    try {
      await webRequest<Record<string, unknown>>("permissions.tools.delete", { tool });
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("config.permissionsTools.saveFailed");
      setError(msg);
    } finally {
      setBusyKey(null);
    }
  };

  const handleAdd = async () => {
    const name = newName.trim();
    if (!isConnected || !name) return;
    setBusyKey("__add__");
    setError(null);
    try {
      await webRequest<Record<string, unknown>>("permissions.tools.update", {
        tool: name,
        level: newLevel,
      });
      setNewName("");
      setNewLevel("ask");
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("config.permissionsTools.saveFailed");
      setError(msg);
    } finally {
      setBusyKey(null);
    }
  };

  const levelSelectClass =
    "rounded-md border border-border bg-bg px-2 py-1.5 text-[13px] outline-none focus:border-accent min-w-[5.5rem]";

  return (
    <div className="border-t border-border px-4 py-4 bg-secondary/10 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-text">{t("config.permissionsTools.title")}</p>
          <p className="text-[11px] text-text-muted mt-0.5">{t("config.permissionsTools.subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={!isConnected || loading}
          className="btn !px-2.5 !py-1 text-xs disabled:opacity-50"
        >
          {loading ? t("config.permissionsTools.refreshing") : t("config.permissionsTools.refresh")}
        </button>
      </div>

      {!isConnected ? (
        <p className="text-xs text-amber-600 dark:text-amber-400">{t("config.permissionsTools.needConnection")}</p>
      ) : null}

      {error ? (
        <p className="text-xs text-danger break-words" role="alert">
          {error}
        </p>
      ) : null}

      {loading && tools.length === 0 ? (
        <p className="text-xs text-text-muted">{t("config.permissionsTools.loadingList")}</p>
      ) : tools.length === 0 ? (
        <p className="text-xs text-text-muted">{t("config.permissionsTools.empty")}</p>
      ) : (
        <div className="rounded-md border border-border/80 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-secondary/40 text-text-muted text-left">
                <th className="px-3 py-2 font-medium w-[18%]">{t("config.permissionsTools.colTool")}</th>
                <th className="px-3 py-2 font-medium">{t("config.permissionsTools.colShortDescription")}</th>
                <th className="px-3 py-2 font-medium w-[7.5rem]">{t("config.permissionsTools.colLevel")}</th>
                <th className="px-3 py-2 font-medium w-[4rem] text-right">{t("config.permissionsTools.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((row) => (
                <tr key={row.name} className="border-t border-border even:bg-secondary/10">
                  <td className="px-3 py-2 align-middle">
                    <span className="mono text-[13px] text-text break-all">{row.name}</span>
                    {!row.configured ? (
                      <span className="block text-[10px] text-text-muted mt-0.5">
                        {t("config.permissionsTools.usesDefault")}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 align-middle text-text-muted break-words">
                    {row.shortDescription && !row.shortDescription.includes("暂无简短说明")
                      ? row.shortDescription
                      : t("config.permissionsTools.noDescription")}
                  </td>
                  <td className="px-3 py-2 align-middle">
                    <select
                      className={levelSelectClass}
                      value={row.level}
                      disabled={!isConnected || busyKey === row.name}
                      onChange={(e) => {
                        const v = e.target.value as PermLevel;
                        void handleLevelChange(row.name, v);
                      }}
                    >
                      <option value="allow">{t("config.permissionsTools.levelAllow")}</option>
                      <option value="ask">{t("config.permissionsTools.levelAsk")}</option>
                      <option value="deny">{t("config.permissionsTools.levelDeny")}</option>
                    </select>
                  </td>
                  <td className="px-3 py-2 align-middle text-right">
                    {row.configured ? (
                      <button
                        type="button"
                        onClick={() => void handleDelete(row.name)}
                        disabled={!isConnected || busyKey === row.name}
                        className="text-danger hover:underline disabled:opacity-50 text-[11px]"
                      >
                        {t("config.permissionsTools.delete")}
                      </button>
                    ) : (
                      <span className="text-text-muted text-[11px]">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="rounded-md border border-dashed border-border/80 px-3 py-3 space-y-2 bg-bg/40">
        <p className="text-[11px] font-medium text-text-muted">{t("config.permissionsTools.addTitle")}</p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-[8rem]">
            <label className="block text-[10px] text-text-muted mb-1">{t("config.permissionsTools.colTool")}</label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("config.permissionsTools.toolPlaceholder")}
              disabled={!isConnected || busyKey === "__add__"}
              className="w-full rounded-md border border-border bg-bg px-2 py-1.5 text-[13px] outline-none focus:border-accent mono"
            />
          </div>
          <div>
            <label className="block text-[10px] text-text-muted mb-1">{t("config.permissionsTools.colLevel")}</label>
            <select
              className={levelSelectClass}
              value={newLevel}
              onChange={(e) => setNewLevel(e.target.value as PermLevel)}
              disabled={!isConnected || busyKey === "__add__"}
            >
              <option value="allow">{t("config.permissionsTools.levelAllow")}</option>
              <option value="ask">{t("config.permissionsTools.levelAsk")}</option>
              <option value="deny">{t("config.permissionsTools.levelDeny")}</option>
            </select>
          </div>
          <button
            type="button"
            onClick={() => void handleAdd()}
            disabled={!isConnected || !newName.trim() || busyKey === "__add__"}
            className="btn !px-3 !py-1.5 text-xs disabled:opacity-50"
          >
            {busyKey === "__add__" ? t("common.saving") : t("config.permissionsTools.add")}
          </button>
        </div>
      </div>
    </div>
  );
}
