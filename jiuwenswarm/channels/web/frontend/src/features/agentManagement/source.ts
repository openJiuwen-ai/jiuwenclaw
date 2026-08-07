import { createFixtureAgentManagementClient, type FixtureOperation } from './fixture';
import { createLiveAgentManagementClient, type AgentManagementClient } from './client';
import { getAgentManagementLocale } from './locale';

let fixtureClient: AgentManagementClient | null = null;
let liveClient: AgentManagementClient | null = null;

export function resolveAgentManagementSource(): 'fixture' | 'live' {
  return import.meta.env.DEV && import.meta.env.VITE_AGENT_MANAGEMENT_SOURCE === 'fixture' ? 'fixture' : 'live';
}

export function createAgentManagementClient(): AgentManagementClient {
  if (resolveAgentManagementSource() === 'fixture') {
    if (fixtureClient) return fixtureClient;
    fixtureClient = createFixtureAgentManagementClient({
      faults: import.meta.env.DEV
        ? (() => {
            const candidate = import.meta.env.VITE_AGENT_MANAGEMENT_FIXTURE_ERROR || new URLSearchParams(window.location.search).get('agentFixtureError');
            const operations: FixtureOperation[] = ['list', 'detail', 'files', 'file', 'skills', 'create', 'install', 'uninstall'];
            const operation = candidate && operations.includes(candidate as FixtureOperation) ? candidate as FixtureOperation : null;
            return operation ? { [operation]: 'Fixture error requested by development configuration' } : undefined;
          })()
        : undefined,
      locale: getAgentManagementLocale,
    });
    return fixtureClient;
  }
  if (liveClient) return liveClient;
  liveClient = createLiveAgentManagementClient();
  return liveClient;
}
