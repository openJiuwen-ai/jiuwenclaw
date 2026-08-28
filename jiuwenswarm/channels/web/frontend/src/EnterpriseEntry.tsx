import { type ReactNode, useEffect, useState } from 'react';
import { isEnterpriseMode } from './edition';
import { EnterpriseContext, type EnterpriseContextSnapshot, type EnterpriseContextValue } from './services/enterpriseContext';
import { getRuntimeScope } from './services/runtimeScope';

const ACCESS_KEY = 'openjiuwen_access_token';
const CONTEXT_READY_MESSAGE = 'jiuwenswarm:enterprise-context-ready';
const CONTEXT_SNAPSHOT_MESSAGE = 'jiuwenswarm:enterprise-context-snapshot';
const CONTEXT_CHANGE_MESSAGE = 'jiuwenswarm:enterprise-context-change';
const CONTEXT_LOGOUT_MESSAGE = 'jiuwenswarm:enterprise-context-logout';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isContextSnapshot(value: unknown): value is EnterpriseContextSnapshot {
  if (!isRecord(value) || !isRecord(value.user) || !isRecord(value.org) || !isRecord(value.gateway)) {
    return false;
  }
  return (
    typeof value.user.user_id === 'string' &&
    typeof value.user.display_name === 'string' &&
    typeof value.org.group_id === 'string' &&
    typeof value.org.name === 'string' &&
    Array.isArray(value.orgs) &&
    typeof value.gateway.jiuwenclaw_id === 'string' &&
    typeof value.gateway.jiuwenclaw_name === 'string' &&
    Array.isArray(value.gateways) &&
    Array.isArray(value.agents) &&
    typeof value.selectedBot === 'string'
  );
}

function hasManagerRuntimeContext(): boolean {
  const scope = getRuntimeScope();
  return Boolean(scope.userId && scope.groupId && scope.botId && scope.gatewayId);
}

function canEnterEnterpriseUserWeb(): boolean {
  if (typeof localStorage === 'undefined') {
    return false;
  }
  return Boolean(localStorage.getItem(ACCESS_KEY)) && hasManagerRuntimeContext();
}

export function EnterpriseEntry({ children }: { children: ReactNode }) {
  const [context, setContext] = useState<EnterpriseContextSnapshot | null>(null);

  useEffect(() => {
    if (!isEnterpriseMode() || typeof window.addEventListener !== 'function' || window.parent === window) {
      return;
    }

    const handleMessage = (event: MessageEvent) => {
      if (event.source !== window.parent || event.origin !== window.location.origin) return;
      if (!isRecord(event.data) || event.data.type !== CONTEXT_SNAPSHOT_MESSAGE) return;
      if (isContextSnapshot(event.data.context)) setContext(event.data.context);
    };

    window.addEventListener('message', handleMessage);
    window.parent.postMessage({ type: CONTEXT_READY_MESSAGE }, window.location.origin);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  if (!isEnterpriseMode() || canEnterEnterpriseUserWeb()) {
    if (!context) return <>{children}</>;

    const requestChange = (field: 'group_id' | 'gateway_id' | 'bot_id', value: string) => {
      window.parent.postMessage({ type: CONTEXT_CHANGE_MESSAGE, field, value }, window.location.origin);
    };
    const contextValue: EnterpriseContextValue = {
      ...context,
      onOrgChange: id => requestChange('group_id', id),
      onGatewayChange: id => requestChange('gateway_id', id),
      onBotChange: id => requestChange('bot_id', id),
      onLogout: () => window.parent.postMessage({ type: CONTEXT_LOGOUT_MESSAGE }, window.location.origin),
    };
    return <EnterpriseContext.Provider value={contextValue}>{children}</EnterpriseContext.Provider>;
  }

  return (
    <div className="enterprise-entry">
      <div className="enterprise-entry__glow" />
      <div className="enterprise-entry__card">
        <div className="enterprise-entry__brand">
          JIUWEN<span>CLAW</span>
        </div>
        <div className="enterprise-entry__eyebrow">ENTERPRISE WORKSPACE</div>
        <h1>请从 Manager Web 进入</h1>
        <p>企业模式由 Manager Web 统一完成登录及组网、组织和 Bot 选择。</p>
      </div>
    </div>
  );
}
