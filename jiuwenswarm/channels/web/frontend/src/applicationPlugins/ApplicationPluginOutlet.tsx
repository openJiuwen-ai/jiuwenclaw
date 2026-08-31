import type { ComponentType } from 'react';

import type { ApplicationPluginContribution } from './types';
import './applicationPlugins.css';

type BundledPluginModule = {
  applicationPluginId?: string;
  default?: ComponentType;
};

const bundledModules = import.meta.glob<BundledPluginModule>('../../../../../extensions/*/frontend/index.tsx', { eager: true });

const bundledComponents = new Map<string, ComponentType>();
for (const module of Object.values(bundledModules)) {
  if (module.applicationPluginId && module.default) {
    bundledComponents.set(module.applicationPluginId, module.default);
  }
}

function iframePermissions(permissions: string[] = []): string {
  const supported = new Set(permissions);
  return [supported.has('camera') ? 'camera' : '', supported.has('microphone') ? 'microphone' : '', supported.has('display_capture') ? 'display-capture' : '']
    .filter(Boolean)
    .join('; ');
}

export function ApplicationPluginOutlet({ contribution }: { contribution: ApplicationPluginContribution }) {
  if (contribution.render_mode === 'none') return null;
  if (contribution.render_mode === 'iframe') {
    if (!contribution.entry_url) return null;
    return (
      <iframe
        className="application-plugin-frame"
        src={contribution.entry_url}
        title={contribution.title}
        allow={iframePermissions(contribution.permissions)}
      />
    );
  }

  const Component = bundledComponents.get(contribution.plugin_id);
  return Component ? <Component /> : null;
}
