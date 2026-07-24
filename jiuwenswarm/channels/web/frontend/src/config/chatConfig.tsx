import { CircleAlert } from 'lucide-react';
import type { AgentMode, Permission } from '../types';

export interface ChatOptionDef<T extends string> {
  value: T;
  i18nKey: string;
  descriptionI18nKey?: string;
  icon: (props: { className?: string }) => JSX.Element;
  hidden?: boolean;
}

// ── 工作模式图标 ────────────────────────────────────────────────

function ClusterModeIcon({ className }: { className?: string }) {
  return <span className={`chat-config-icon chat-config-icon--cluster ${className ?? ''}`} aria-hidden="true" />;
}

function PlanModeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
    </svg>
  );
}

function FastModeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
    </svg>
  );
}

function AutoModeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
    </svg>
  );
}

// ── 权限图标 ────────────────────────────────────────────────────

function DefaultPermissionIcon({ className }: { className?: string }) {
  return <span className={`chat-config-icon chat-config-icon--permission ${className ?? ''}`} aria-hidden="true" />;
}

function SafeAccessPermissionIcon({ className }: { className?: string }) {
  return <CircleAlert className={className} aria-hidden="true" />;
}

// ── 工作模式选项 ────────────────────────────────────────────────
// MACRO lanes + opt-in Auto; auto_harness stays hidden.

export const AGENT_MODE_OPTIONS: ChatOptionDef<AgentMode>[] = [
  {
    value: 'agent.plan',
    i18nKey: 'chat.modePlan',
    icon: PlanModeIcon,
  },
  {
    value: 'agent.fast',
    i18nKey: 'chat.modeAgent',
    icon: FastModeIcon,
  },
  {
    value: 'team',
    i18nKey: 'chat.modeAgentTeam',
    descriptionI18nKey: 'chat.config.mode.clusterDesc',
    icon: ClusterModeIcon,
  },
  {
    value: 'auto',
    i18nKey: 'chat.modeAuto',
    icon: AutoModeIcon,
  },
  {
    value: 'auto_harness',
    i18nKey: 'chat.modeAutoHarness',
    descriptionI18nKey: 'chat.modeAutoHarnessDesc',
    icon: AutoModeIcon,
    hidden: true,
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
