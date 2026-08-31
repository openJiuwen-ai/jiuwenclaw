import type { ApplicationPluginConfigProperty } from './types';

export type ApplicationPluginDraftValue = string | boolean;
export const APPLICATION_PLUGIN_SECRET_MASK = '******';

function isSecretProperty(definition: ApplicationPluginConfigProperty): boolean {
  return definition.secret === true || definition.format === 'password';
}

export function applicationPluginSettingsToDraft(
  values: Record<string, unknown>,
  properties: Record<string, ApplicationPluginConfigProperty>,
  configuredSecrets: string[] = [],
): Record<string, ApplicationPluginDraftValue> {
  const configured = new Set(configuredSecrets);
  return Object.fromEntries(
    Object.entries(properties).map(([key, definition]) => {
      if (isSecretProperty(definition) && configured.has(key)) {
        return [key, APPLICATION_PLUGIN_SECRET_MASK];
      }
      const value = values[key] ?? definition.default ?? '';
      if (definition.type === 'boolean') return [key, value === true];
      if (definition.type === 'array' || definition.type === 'object') {
        return [key, JSON.stringify(value, null, 2)];
      }
      return [key, value == null ? '' : String(value)];
    }),
  );
}

export function serializeApplicationPluginDraft(
  draft: Record<string, ApplicationPluginDraftValue>,
  properties: Record<string, ApplicationPluginConfigProperty>,
  configuredSecrets: string[] = [],
): Record<string, unknown> {
  const configured = new Set(configuredSecrets);
  return Object.fromEntries(
    Object.entries(properties).map(([key, definition]) => {
      const value = draft[key];
      if (isSecretProperty(definition) && configured.has(key) && value === APPLICATION_PLUGIN_SECRET_MASK) {
        return [key, ''];
      }
      if (definition.type === 'boolean') return [key, value === true];
      if (definition.type === 'integer') {
        if (String(value).trim() === '') throw new Error(`${definition.title || key} 不能为空`);
        return [key, Number.parseInt(String(value), 10)];
      }
      if (definition.type === 'number') {
        if (String(value).trim() === '') throw new Error(`${definition.title || key} 不能为空`);
        return [key, Number(String(value))];
      }
      if (definition.type === 'array' || definition.type === 'object') {
        try {
          return [key, JSON.parse(String(value))];
        } catch {
          throw new Error(`${definition.title || key} 必须是有效的 JSON`);
        }
      }
      return [key, String(value ?? '')];
    }),
  );
}

export function isApplicationPluginSettingVisible(definition: ApplicationPluginConfigProperty, draft: Record<string, ApplicationPluginDraftValue>): boolean {
  const condition = definition['x-visible-when'];
  if (!condition) return true;
  return Object.entries(condition).every(([key, expected]) => draft[key] === expected);
}
