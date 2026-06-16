import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { webRequest } from "../../services/webClient";

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

interface SkillEvolutionModalProps {
  open: boolean;
  sessionId: string;
  skillName: string | null;
  onClose: () => void;
  onSaved?: () => Promise<void> | void;
}

export function SkillEvolutionModal({
  open,
  sessionId,
  skillName,
  onClose,
  onSaved,
}: SkillEvolutionModalProps) {
  const { t, i18n } = useTranslation();
  const [entries, setEntries] = useState<EvolutionEntry[]>([]);
  const [listState, setListState] = useState<LoadState>("idle");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"success" | "error" | null>(null);
  const [formatError, setFormatError] = useState<string | null>(null);

  const withSession = useCallback(
    (params?: Record<string, unknown>) => ({
      ...(params || {}),
      session_id: sessionId,
    }),
    [sessionId]
  );

  const sortedEntries = useMemo(
    () =>
      [...entries].sort((a, b) => {
        const ta = a.timestamp || "";
        const tb = b.timestamp || "";
        return tb.localeCompare(ta);
      }),
    [entries]
  );

  const fetchEntries = useCallback(async () => {
    if (!skillName) return;
    setListState("loading");
    setMessage(null);
    setMessageType(null);
    setFormatError(null);
    try {
      const data = await webRequest<EvolutionGetResponse>(
        "skills.evolution.get",
        withSession({ name: skillName })
      );
      if (!data.exists) {
        setEntries([]);
        setListState("success");
        return;
      }
      if (data.valid === false) {
        setEntries([]);
        setFormatError(data.detail || t("skills.evolution.errors.invalidFile"));
        setListState("success");
        return;
      }
      setEntries(data.entries || []);
      setListState("success");
    } catch (error) {
      console.error(error);
      setListState("error");
    }
  }, [skillName, t, withSession]);

  useEffect(() => {
    if (!open || !skillName) return;
    void fetchEntries();
  }, [open, skillName, fetchEntries]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const handleChangeContent = useCallback((entryId: string, value: string) => {
    setEntries((prev) =>
      prev.map((entry) =>
        entry.id === entryId
          ? { ...entry, change: { ...entry.change, content: value } }
          : entry
      )
    );
  }, []);

  const handleDeleteEntry = useCallback(
    (entryId: string) => {
      const confirmed = window.confirm(t("skills.evolution.deleteConfirm"));
      if (!confirmed) return;
      setEntries((prev) => prev.filter((entry) => entry.id !== entryId));
    },
    [t]
  );

  const handleSave = useCallback(async () => {
    if (!skillName) return;
    setSaving(true);
    setMessage(null);
    setMessageType(null);
    try {
      const data = await webRequest<{
        success: boolean;
        detail?: string;
        message?: string;
      }>("skills.evolution.save", withSession({ name: skillName, entries }));
      if (!data.success) {
        throw new Error(data.detail || data.message || t("skills.evolution.errors.saveFailed"));
      }
      setMessage(t("skills.evolution.messages.saved"));
      setMessageType("success");
      if (onSaved) {
        await onSaved();
      }
    } catch (error) {
      console.error(error);
      setMessage(t("skills.evolution.errors.saveFailed"));
      setMessageType("error");
    } finally {
      setSaving(false);
    }
  }, [entries, onSaved, skillName, t, withSession]);

  if (!open || !skillName) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-label={t("skills.evolution.closeAria")}
      />
      <div className="relative w-full max-w-4xl max-h-[88vh] overflow-hidden rounded-[8px] border border-border bg-card shadow-2xl animate-rise">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-panel">
          <div>
            <h3 className="text-base font-semibold text-text">
              {t("skills.evolution.title", { name: skillName })}
            </h3>
            <p className="text-xs text-text-muted">{t("skills.evolution.subtitle")}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void fetchEntries()}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-text hover:text-text-strong transition-colors"
              title={t("common.refresh")}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-text hover:text-text-strong transition-colors"
              aria-label={t("skills.evolution.closeAria")}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <div className="p-5 overflow-auto max-h-[calc(88vh-64px)]">
          {message && (
            <div
              className={`mb-3 px-3 py-2 rounded-md text-sm ${
                messageType === "error"
                  ? "bg-secondary text-danger"
                  : "bg-secondary text-text"
              }`}
            >
              {message}
            </div>
          )}

          {formatError && (
            <div className="mb-3 px-3 py-2 rounded-md bg-secondary text-sm text-danger">
              {formatError}
            </div>
          )}

          {listState === "loading" && (
            <div className="flex items-center justify-center h-full text-text-muted">{t("common.loading")}</div>
          )}
          {listState === "error" && (
            <div className="text-sm text-text-muted">
              {t("skills.evolution.errors.loadFailed")}
            </div>
          )}
          {listState === "success" && !formatError && sortedEntries.length === 0 && (
            <div className="text-sm text-text-muted">
              {t("skills.evolution.empty")}
            </div>
          )}

          {listState === "success" && !formatError && sortedEntries.length > 0 && (
            <div className="space-y-3">
              {sortedEntries.map((entry) => (
                <div
                  key={entry.id}
                  className="rounded-lg border border-border bg-panel p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 text-xs text-text-muted space-y-1">
                      <div>
                        {t("skills.evolution.fields.id")}: {entry.id}
                      </div>
                      <div>
                        {t("skills.evolution.fields.source")}: {entry.source || "-"}
                      </div>
                      <div>
                        {t("skills.evolution.fields.section")}: {entry.change?.section || "-"}
                      </div>
                      <div>
                        {t("skills.evolution.fields.target")}: {entry.change?.target || "-"}
                      </div>
                      <div>
                        {t("skills.evolution.fields.applied")}: {String(Boolean(entry.applied))}
                      </div>
                      <div>
                        {t("skills.evolution.fields.timestamp")}:{" "}
                        {entry.timestamp
                          ? new Date(entry.timestamp).toLocaleString(i18n.language)
                          : "-"}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleDeleteEntry(entry.id)}
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-danger hover:text-danger/80 transition-colors"
                      title={t("skills.evolution.actions.delete")}
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>

                  <div className="mt-3">
                    <div className="text-sm font-medium text-text mb-2">
                      {t("skills.evolution.fields.content")}
                    </div>
                    <textarea
                      value={entry.change?.content || ""}
                      onChange={(event) => handleChangeContent(entry.id, event.target.value)}
                      className="w-full min-h-28 px-3 py-2 rounded-md bg-card border border-border text-sm text-text placeholder:text-text-muted"
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={handleSave}
              className={`w-[72px] h-[28px] rounded-[16px] text-sm transition-colors ${
                saving || !!formatError
                  ? "bg-gray-400 text-text-muted cursor-not-allowed"
                  : "bg-[#191919] text-white hover:bg-[#333333]"
              }`}
              disabled={saving || !!formatError}
            >
              {saving ? t("common.saving") : t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
