interface StatusBadgeProps {
  status?: string | null;
  label?: string;
}

function statusKind(status?: string | null): 'ok' | 'warn' | 'danger' | 'muted' {
  if (!status) return 'muted';
  const s = status.toLowerCase();
  if (['active', 'online', 'ready', 'ok', 'running'].includes(s)) return 'ok';
  if (['pending', 'restarting', 'starting'].includes(s)) return 'warn';
  if (['offline', 'failed', 'error', 'unreachable', 'shutdown'].includes(s)) return 'danger';
  return 'muted';
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const kind = statusKind(status);
  const dotClass = kind === 'ok' ? 'ok' : kind === 'warn' ? 'warn' : kind === 'muted' ? 'muted' : '';
  return (
    <span className={`pill ${kind}`} title={status ?? ''}>
      <span className={`statusDot ${dotClass}`} />
      <span className="mono text-[11px] uppercase tracking-wider">{label ?? status ?? '-'}</span>
    </span>
  );
}
