/**
 * 环境变量工具
 *
 * 用于在前端配置后端 API/WS 地址
 */
function normalizeBase(input: string): string {
  return input.replace(/\/+$/, "");
}

export function getApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE as string | undefined;
  if (!raw) return "";
  return normalizeBase(raw);
}

export function getWsBase(): string {
  const raw = import.meta.env.VITE_WS_BASE as string | undefined;
  if (raw) return normalizeBase(raw);
  const apiBase = getApiBase();
  if (!apiBase) return "";
  return apiBase.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

export type WebTransport = "websocket" | "http";

/** 运行时由 app_web.py 注入 window.__JIUWEN_WEB_TRANSPORT__；未注入回退构建期值。默认 websocket。`a2` 与 `http` 同义。 */
export function getWebTransport(): WebTransport {
  const injected = String(window.__JIUWEN_WEB_TRANSPORT__ ?? "").trim().toLowerCase();
  if (injected && !injected.startsWith("__")) {
    if (injected === "http" || injected === "a2") {
      return "http";
    }
    return "websocket";
  }
  const raw = String(
    import.meta.env.VITE_WEB_TRANSPORT ?? import.meta.env.VITE_TRANSPORT ?? "websocket"
  )
    .trim()
    .toLowerCase();
  if (raw === "http" || raw === "a2") {
    return "http";
  }
  return "websocket";
}

/** Gateway A2 前缀，默认同源 `/gateway-api/v1`，避免与 Manager `/api/v1` 冲突。 */
export function getGatewayHttpBase(): string {
  const raw = import.meta.env.VITE_GATEWAY_HTTP_BASE ?? import.meta.env.VITE_WEB_HTTP_BASE;
  if (!raw) {
    return "/gateway-api/v1";
  }
  const normalized = normalizeBase(raw);
  if (normalized.endsWith("/api/v1")) {
    return normalized;
  }
  return `${normalized}/api/v1`;
}
