import type { AgentCatalogItem } from './types';

// The low-fidelity marketplace exports use these illustrated avatars. Keep the
// mapping at the presentation seam so the DTO/canonical model stays faithful
// to the backend contract. A real avatar returned by show/list always wins.
const FIXTURE_AVATARS: Record<string, string> = {
  'workplace-slim-coach': '/agent-management/avatar-cyan.svg',
  'content-creator': '/agent-management/avatar-purple.svg',
  'python-code-reviewer': '/agent-management/avatar-green.svg',
  'market-research-analyst': '/agent-management/avatar-orange.svg',
  'legal-assistant': '/agent-management/avatar-red.svg',
  'meeting-assistant': '/agent-management/avatar-yellow.svg',
  'business-assistant': '/agent-management/avatar-pink.svg',
  'ppt-expert': '/agent-management/avatar-purple.svg',
  'research-assistant': '/agent-management/avatar-cyan.svg',
  'document-expert': '/agent-management/avatar-yellow.svg',
  'architecture-expert': '/agent-management/avatar-pink.svg',
  'operations-expert': '/agent-management/avatar-red.svg',
};

export function getAgentAvatarUrl(item: Pick<AgentCatalogItem, 'id' | 'avatarUrl'>): string | null {
  return item.avatarUrl || FIXTURE_AVATARS[item.id] || null;
}
