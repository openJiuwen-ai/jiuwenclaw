import { createContext, useContext } from 'react';

export type EnterpriseContextSnapshot = {
  user: { user_id: string; display_name: string };
  org: { group_id: string; name: string };
  orgs: { group_id: string; name: string }[];
  gateway: { jiuwenclaw_id: string; jiuwenclaw_name: string; gateway_endpoint: string | null };
  gateways: { jiuwenclaw_id: string; jiuwenclaw_name: string; gateway_endpoint: string | null }[];
  agents: { template_id: string; template_name: string; resource_id?: string }[];
  selectedBot: string;
};

export type EnterpriseContextValue = EnterpriseContextSnapshot & {
  onOrgChange: (id: string) => void;
  onGatewayChange: (id: string) => void;
  onBotChange: (id: string) => void;
  onLogout: () => void;
};

export const EnterpriseContext = createContext<EnterpriseContextValue | null>(null);
export function useEnterpriseContext() {
  return useContext(EnterpriseContext);
}
