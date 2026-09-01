import { useEffect, useState } from 'react';

import { fetchApplicationPlugins } from './manifest';
import type { ApplicationPluginContribution } from './types';

export function useApplicationPlugins(isGatewayConnected: boolean): ApplicationPluginContribution[] {
  const [plugins, setPlugins] = useState<ApplicationPluginContribution[]>([]);

  useEffect(() => {
    if (!isGatewayConnected) {
      setPlugins([]);
      return;
    }
    const controller = new AbortController();
    void fetchApplicationPlugins(controller.signal)
      .then(setPlugins)
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.warn('Application plugin discovery failed:', error);
      });
    return () => controller.abort();
  }, [isGatewayConnected]);

  return plugins;
}
