export type UserWebMode = "personal" | "enterprise";
export const getUserWebMode = (): UserWebMode => {
  const injected = String(window.__JIUWEN_USER_WEB_MODE__ || "").toLowerCase();
  if (injected === "enterprise" || injected === "personal") return injected;
  const configured = String(import.meta.env.VITE_USER_WEB_MODE || "").toLowerCase();
  if (configured === "enterprise" || configured === "personal") return configured;
  return String(import.meta.env.VITE_ENABLE_USER_WEB_EMBEDDING).toLowerCase() === "true" ? "enterprise" : "personal";
};
export const isEnterpriseMode = (): boolean => getUserWebMode() === "enterprise";
export const ENTERPRISE_HIDDEN_NAV_ITEMS = ["channels", "agents", "teams", "extensions", "configpanel", "browserpanel", "updatepanel"] as const;
