import type { AgentMode, Permission } from '../types';

export interface ChatOptionDef<T extends string> {
  value: T;
  i18nKey: string;
  descriptionI18nKey?: string;
  icon: (props: { className?: string }) => JSX.Element;
}

// ── 工作模式图标 ────────────────────────────────────────────────

function ClusterModeIcon({ className }: { className?: string }) {
  // Outer hexagon + 3 inner hexagons, all stroke-only
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.1} strokeLinejoin="round" aria-hidden="true">
      <path d="M13.794 3.533L9.374 0.986C8.627 0.559 7.707 0.559 6.961 0.986L2.541 3.533C1.794 3.959 1.334 4.759 1.334 5.619V10.713C1.334 11.573 1.794 12.373 2.541 12.799L6.961 15.346C7.334 15.559 7.747 15.666 8.167 15.666C8.587 15.666 9.001 15.559 9.374 15.346L13.794 12.799C14.541 12.373 15.001 11.573 15.001 10.713V5.619C15.001 4.759 14.541 3.959 13.794 3.533Z" />
      <polygon points="8.167,4.873 9.401,5.586 9.401,7.006 8.167,7.719 6.934,7.006 6.934,5.586" />
      <polygon points="6.434,7.873 7.667,8.586 7.667,10.006 6.434,10.719 5.201,10.006 5.201,8.586" />
      <polygon points="9.894,7.873 11.127,8.586 11.127,10.006 9.894,10.719 8.661,10.006 8.661,8.586" />
    </svg>
  );
}

function SingleAgentModeIcon({ className }: { className?: string }) {
  // Two nested pointy-top hexagons (corner pointing up)
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.2} aria-hidden="true">
      <polygon points="8,1 14.06,4.5 14.06,11.5 8,15 1.94,11.5 1.94,4.5" />
      <polygon points="8,4.5 11.03,6.25 11.03,9.75 8,11.5 4.97,9.75 4.97,6.25" />
    </svg>
  );
}

// ── 权限图标 ────────────────────────────────────────────────────

function DefaultPermissionIcon({ className }: { className?: string }) {
  // Shield outline with upright key inside
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 2.5L20 6V12C20 17 12 21.5 12 21.5S4 17 4 12V6Z" />
      <circle cx="12" cy="9" r="2.8" />
      <line x1="12" y1="11.8" x2="12" y2="18" strokeLinecap="round" />
      <line x1="12" y1="14.5" x2="14.5" y2="14.5" strokeLinecap="round" />
      <line x1="12" y1="16.5" x2="14" y2="16.5" strokeLinecap="round" />
    </svg>
  );
}

function SafeAccessPermissionIcon({ className }: { className?: string }) {
  // Circle with exclamation mark inside
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <line x1="12" y1="7" x2="12" y2="13.5" strokeLinecap="round" />
      <circle cx="12" cy="16.5" r="1" fill="currentColor" strokeWidth={0} />
    </svg>
  );
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
