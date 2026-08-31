import { createContext, useContext } from 'react';

export type EnterpriseUser = { user_id: string; display_name: string };
export type EnterpriseOrg = { group_id: string; name: string };
export type EnterpriseGateway = {
  jiuwenclaw_id: string;
  jiuwenclaw_name: string;
  gateway_endpoint: string | null;
};
export type EnterpriseAgent = { template_id: string; template_name: string; resource_id?: string };

export type EnterpriseContextSnapshot = {
  user: EnterpriseUser;
  org: EnterpriseOrg;
  orgs: EnterpriseOrg[];
  gateway: EnterpriseGateway;
  gateways: EnterpriseGateway[];
  agents: EnterpriseAgent[];
  selectedBot: string;
};

export type EnterpriseContextValue = EnterpriseContextSnapshot & {
  contextError: string;
  contextSwitching: boolean;
  onOrgChange: (id: string) => void;
  onGatewayChange: (id: string) => void;
  onBotChange: (id: string) => void;
  onLogout: () => void;
};

export const EnterpriseContext = createContext<EnterpriseContextValue | null>(null);
export function useEnterpriseContext() {
  return useContext(EnterpriseContext);
}
