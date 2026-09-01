import type { EnterpriseContextSnapshot } from '../../services/enterpriseContext';
import { parseRuntimeScope } from '../../services/runtimeScope';
import type { EnterpriseAuthProvider } from '../types';

const DEFAULTS = {
  userId: 'debug-user',
  displayName: 'Debug User',
  groupId: 'debug-group',
  groupName: 'Debug Organization',
  gatewayId: 'debug-gateway',
  gatewayName: 'Debug Gateway',
  botId: 'debug-agent',
  botName: 'Debug Agent',
} as const;

export function buildSimulatedEnterpriseContext(search = ''): EnterpriseContextSnapshot {
  const preferred = parseRuntimeScope(search);
  const user = { user_id: preferred.userId || DEFAULTS.userId, display_name: DEFAULTS.displayName };
  const org = { group_id: preferred.groupId || DEFAULTS.groupId, name: DEFAULTS.groupName };
  const gateway = {
    jiuwenclaw_id: preferred.gatewayId || DEFAULTS.gatewayId,
    jiuwenclaw_name: DEFAULTS.gatewayName,
    gateway_endpoint: null,
  };
  const agent = {
    template_id: preferred.botId || DEFAULTS.botId,
    template_name: DEFAULTS.botName,
    resource_id: preferred.botId || DEFAULTS.botId,
  };
  return { user, org, orgs: [org], gateway, gateways: [gateway], agents: [agent], selectedBot: agent.resource_id };
}

function entryPath(): string {
  return window.location.pathname.startsWith('/chat') ? '/chat/' : '/';
}

export const simulatedAuthProvider: EnterpriseAuthProvider = {
  id: 'simulate',
  startupMessage: '【登录认证模拟调试模式已开启】使用默认用户、组织、组网和 Agent 候选值',
  isAuthenticated: () => true,
  redirectToLogin: () => window.location.replace(entryPath()),
  getCurrentUser: async () => buildSimulatedEnterpriseContext(window.location.search).user,
  listOrganizations: async () => buildSimulatedEnterpriseContext(window.location.search).orgs,
  listGateways: async () => buildSimulatedEnterpriseContext(window.location.search).gateways,
  listAgents: async () => buildSimulatedEnterpriseContext(window.location.search).agents,
  async logout() {
    window.location.replace(entryPath());
  },
};
