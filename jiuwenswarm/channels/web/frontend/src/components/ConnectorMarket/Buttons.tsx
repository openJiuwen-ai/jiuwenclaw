// 详情页共用的视觉部件，PluginDetailPage.tsx / McpDetailPage.tsx 都要用——之前是各自内联
// 一份几乎一样的 JSX，容易改一处漏一处（见 state-model-rectification.md §5 的教训），提出来共用。
//
// 2026-08-15：DetailToggleSwitch（全局启用/禁用开关）已删除，插件/MCP 都不再有这个状态维度，
// 见 state-model-rectification-v2-remove-global-toggle.md。

export function PillButton({
  icon,
  label,
  onClick,
  disabled,
}: {
  icon?: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-8 items-center justify-center gap-1 rounded-full border border-[color:var(--color-chat-supporting-text)] bg-card px-4 text-[13px] text-text disabled:opacity-60"
    >
      {icon}
      {label}
    </button>
  );
}

export function DetailLinkButton({
  icon,
  label,
  onClick,
  danger,
  disabled,
}: {
  icon?: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1 text-[13px] text-text disabled:opacity-60 ${danger ? 'hover:text-danger' : 'hover:text-[color:var(--color-chat-accent)]'}`}
    >
      {icon}
      {label}
    </button>
  );
}
