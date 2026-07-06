import { CircleAlert } from 'lucide-react';
import type { AgentMode, Permission } from '../types';
import clusterIcon from '../assets/chat/cluster.svg';
import singleAgentIcon from '../assets/chat/single-agent.svg';
import permissionDefaultIcon from '../assets/chat/permission-default.svg';

export interface ChatOptionDef<T extends string> {
  value: T;
  i18nKey: string;
  descriptionI18nKey?: string;
  icon: (props: { className?: string }) => JSX.Element;
}

// ── 工作模式图标 ────────────────────────────────────────────────

function ClusterModeIcon({ className }: { className?: string }) {
  return <img src={clusterIcon} className={className} aria-hidden="true" />;
}

function SingleAgentModeIcon({ className }: { className?: string }) {
  return <img src={singleAgentIcon} className={className} aria-hidden="true" />;
}

// ── 权限图标 ────────────────────────────────────────────────────

function DefaultPermissionIcon({ className }: { className?: string }) {
  return <img src={permissionDefaultIcon} className={className} aria-hidden="true" />;
}

function SafeAccessPermissionIcon({ className }: { className?: string }) {
  return <CircleAlert className={className} aria-hidden="true" />;
}

// ── 工作模式选项 ────────────────────────────────────────────────
// 只暴露面向用户的 2 种模式；agent.fast / auto_harness 不在此列

export const AGENT_MODE_OPTIONS: ChatOptionDef<AgentMode>[] = [
  {
    value: 'team',
    i18nKey: 'chat.config.mode.cluster',
    descriptionI18nKey: 'chat.config.mode.clusterDesc',
    icon: ClusterModeIcon,
  },
  {
    value: 'agent.plan',
    i18nKey: 'chat.config.mode.singleAgent',
    icon: SingleAgentModeIcon,
  },
];

// ── 权限选项 ────────────────────────────────────────────────────

export const PERMISSION_OPTIONS: ChatOptionDef<Permission>[] = [
  {
    value: 'default',
    i18nKey: 'chat.config.permission.default',
    descriptionI18nKey: 'chat.config.permission.defaultDesc',
    icon: DefaultPermissionIcon,
  },
  {
    value: 'full_access',
    i18nKey: 'chat.config.permission.fullAccess',
    descriptionI18nKey: 'chat.config.permission.fullAccessDesc',
    icon: SafeAccessPermissionIcon,
  },
];
