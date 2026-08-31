import type { ApplicationPluginSettingsPayload } from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = `Application plugin request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === 'string' && payload.detail.trim()) detail = payload.detail;
    } catch {
      // Keep the status-based error when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function pluginUrl(pluginId: string, suffix: string): string {
  return `/api/application-plugins/${encodeURIComponent(pluginId)}/${suffix}`;
}

export function fetchApplicationPluginSettings(pluginId: string): Promise<ApplicationPluginSettingsPayload> {
  return request(pluginUrl(pluginId, 'settings'));
}

export function updateApplicationPluginSettings(
  pluginId: string,
  values: Record<string, unknown>,
  clearSecrets: string[],
): Promise<ApplicationPluginSettingsPayload> {
  return request(pluginUrl(pluginId, 'settings'), {
    method: 'PUT',
    body: JSON.stringify({ values, clear_secrets: clearSecrets }),
  });
}

export function setApplicationPluginEnabled(pluginId: string, enabled: boolean): Promise<ApplicationPluginSettingsPayload> {
  return request(pluginUrl(pluginId, 'enabled'), {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}
