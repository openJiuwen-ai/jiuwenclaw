import { useCallback, useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { webRequest } from "../../services/webClient";

type SourceType = "skillnet" | "clawhub";

interface SourceManagerModalProps {
  open: boolean;
  sessionId: string;
  onClose: () => void;
  onSourceChange?: (source: SourceType) => void;
  currentSource?: SourceType;
  onNavigateToConfig?: () => void;
}

export function SourceManagerModal({
  open,
  sessionId,
  onClose,
  onSourceChange,
  currentSource = "skillnet",
  onNavigateToConfig,
}: SourceManagerModalProps) {
  const { t } = useTranslation();
  const [selectedSource, setSelectedSource] = useState<SourceType>(currentSource);
  const [clawhubToken, setClawhubToken] = useState("");
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenSaving, setTokenSaving] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [showToken, setShowToken] = useState(false);

  const withSession = useCallback(
    (params?: Record<string, unknown>) => ({
      ...(params || {}),
      session_id: sessionId,
    }),
    [sessionId]
  );

  const fetchClawhubToken = useCallback(async () => {
    setTokenLoading(true);
    try {
      const data = await webRequest<{ token?: string }>(
        "skills.clawhub.get_token",
        withSession()
      );
      const token = data.token || "";
      setClawhubToken(token);
      setHasToken(!!token);
    } catch (error) {
      console.error("Failed to load ClawHub token:", error);
      setClawhubToken("");
      setHasToken(false);
    } finally {
      setTokenLoading(false);
    }
  }, [withSession]);

  useEffect(() => {
    if (!open) return;
    setSelectedSource(currentSource);
    void fetchClawhubToken();
  }, [open, currentSource, fetchClawhubToken]);

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

  const handleSourceSelect = useCallback((source: SourceType) => {
    setSelectedSource(source);
    if (onSourceChange) {
      onSourceChange(source);
    }
  }, [onSourceChange]);

  const handleSaveToken = useCallback(async () => {
    const token = clawhubToken.trim();
    setTokenSaving(true);
    try {
      const data = await webRequest<{ success: boolean; detail?: string }>(
        "skills.clawhub.set_token",
        withSession({ token })
      );
      if (!data.success) {
        throw new Error(data.detail || t("skills.clawhub.errors.saveFailed"));
      }
      setHasToken(!!token);
    } catch (error) {
      console.error("Failed to save ClawHub token:", error);
    } finally {
      setTokenSaving(false);
    }
  }, [clawhubToken, t, withSession]);

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-label={t("sourceManager.closeAria")}
      />
      <div className="relative w-full max-w-xl overflow-hidden rounded-[8px] border border-border bg-card shadow-2xl animate-rise">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-panel">
          <div>
            <h3 className="text-base font-semibold text-text">{t("sourceManager.title")}</h3>
            <p className="text-xs text-text-muted">{t("sourceManager.subtitle")}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-text hover:text-text-strong transition-colors"
            aria-label={t("sourceManager.closeAria")}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-5 overflow-auto">
          <div className="mb-4">
            <div className="text-sm font-medium text-text mb-3">{t("sourceManager.selectSource")}</div>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => handleSourceSelect("skillnet")}
                className={`flex-1 py-3 px-4 rounded-lg border transition-all ${
                  selectedSource === "skillnet"
                    ? "border-[#191919] bg-[#EAEAEB] text-[#191919]"
                    : "border-border bg-card text-text-muted hover:border-gray-400"
                }`}
              >
                <div className="text-left">
                  <div className="font-medium">SkillNet</div>
                  <div className="text-xs opacity-70">{t("sourceManager.skillnetDesc")}</div>
                </div>
              </button>
              <button
                type="button"
                onClick={() => handleSourceSelect("clawhub")}
                className={`flex-1 py-3 px-4 rounded-lg border transition-all ${
                  selectedSource === "clawhub"
                    ? "border-[#191919] bg-[#EAEAEB] text-[#191919]"
                    : "border-border bg-card text-text-muted hover:border-gray-400"
                }`}
              >
                <div className="text-left">
                  <div className="font-medium">ClawHub</div>
                  <div className="text-xs opacity-70">{t("sourceManager.clawhubDesc")}</div>
                </div>
              </button>
            </div>
          </div>

          {selectedSource === "clawhub" && (
            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="text-sm font-medium text-text mb-3">{t("skills.clawhub.configTitle")}</div>
              {tokenLoading ? (
                <div className="text-sm text-text-muted">{t("common.loading")}</div>
              ) : (
                <>
                  <p className="text-xs text-text-muted mb-3">
                    {t("skills.clawhub.configDescription")}
                  </p>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-text mb-2">
                        {t("skills.clawhub.tokenLabel")}
                      </label>
                      <div className="relative">
                        <input
                          type={showToken ? "text" : "password"}
                          value={clawhubToken}
                          onChange={(e) => setClawhubToken(e.target.value)}
                          placeholder={t("skills.clawhub.tokenPlaceholder")}
                          className="w-full px-3 py-2 pr-10 rounded-md bg-card border border-border text-sm text-text placeholder:text-text-muted"
                        />
                        <button
                          type="button"
                          onClick={() => setShowToken(!showToken)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text transition-colors"
                          aria-label={showToken ? t("common.hide") : t("common.show")}
                        >
                          {showToken ? (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                            </svg>
                          ) : (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      {hasToken && (
                        <span className="text-xs text-green-600 flex items-center gap-1">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          {t("skills.clawhub.tokenConfigured")}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleSaveToken()}
                        disabled={tokenSaving || !clawhubToken.trim()}
                        className={`ml-auto w-[76px] h-[28px] rounded-[24px] text-sm transition-colors ${
                          tokenSaving || !clawhubToken.trim()
                            ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                            : "bg-[#191919] text-white hover:bg-gray-800"
                        }`}
                      >
                        {tokenSaving ? t("common.saving") : t("common.save")}
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {selectedSource === "skillnet" && (
            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="font-medium text-text mb-2">
                {t("sourceManager.skillnet.usageNoticeTitle")}
              </div>
              <ul className="list-disc pl-4 space-y-1 text-xs text-text-muted">
                <li>{t("sourceManager.skillnet.usageNotice1")}</li>
                <li>
                  <Trans
                    i18nKey="sourceManager.skillnet.usageNotice2"
                    components={{
                      strong: (
                        <strong className="font-semibold text-text" />
                      ),
                    }}
                  />
                </li>
                <li>
                  <Trans
                    i18nKey="sourceManager.skillnet.usageNotice3"
                    components={{
                      configLink: (
                        <button
                          type="button"
                          aria-label={t("sourceManager.skillnet.configPageLinkAria")}
                          onClick={() => onNavigateToConfig?.()}
                          className="inline p-0 m-0 align-baseline border-0 bg-transparent cursor-pointer font-medium text-accent underline decoration-accent/35 underline-offset-2 hover:text-accent-hover hover:decoration-accent/60"
                        />
                      ),
                    }}
                  />
                </li>
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
