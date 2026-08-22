import type { AgentCatalogItem } from './types';

// The current backend catalog does not provide avatar URLs for built-ins.
// Keep the high-fidelity illustrated avatars at the presentation seam until
// the backend supplies an authoritative asset for each template.
const DESIGN_AVATARS: Record<string, string> = {
  'health-life-advisor': '/agent-management/avatar-cyan.svg',
  'personal-finance-expert': '/agent-management/avatar-pink.svg',
  'system-architect': '/agent-management/avatar-yellow.svg',
};

export function getAgentAvatarUrl(item: Pick<AgentCatalogItem, 'id' | 'avatarUrl'>): string | null {
  return item.avatarUrl || DESIGN_AVATARS[item.id] || null;
}
