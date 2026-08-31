export type ApplicationPluginNavKey = `app:${string}`;
export type ApplicationPluginManagerNavKey = 'applicationPlugins';

export interface ApplicationPluginConfigProperty {
  type?: 'string' | 'boolean' | 'integer' | 'number' | 'array' | 'object';
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  secret?: boolean;
  format?: string;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  'x-group'?: string;
  'x-order'?: number;
  'x-visible-when'?: Record<string, unknown>;
}

export interface ApplicationPluginConfigSchema {
  type?: 'object';
  properties?: Record<string, ApplicationPluginConfigProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface ApplicationPluginContribution {
  plugin_id: string;
  plugin_version: string;
  description?: string;
  permissions?: string[];
  config_schema?: ApplicationPluginConfigSchema;
  enabled?: boolean;
  id: string;
  nav_key: string;
  title: string;
  title_i18n_key?: string;
  render_mode: 'bundled' | 'iframe' | 'none';
  component?: string;
  entry_url?: string;
  position: number;
}

export interface ApplicationPluginManifest {
  api_version: number;
  plugins: ApplicationPluginContribution[];
}

export interface ApplicationPluginSettingsPayload {
  plugin_id: string;
  enabled: boolean;
  config_schema: ApplicationPluginConfigSchema;
  values: Record<string, unknown>;
  configured_secrets: string[];
}
