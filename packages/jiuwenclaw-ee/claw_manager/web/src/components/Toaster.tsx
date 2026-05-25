import { useUiStore } from '../stores/uiStore';

export function Toaster() {
  const toasts = useUiStore((s) => s.toasts);
  const dismissToast = useUiStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-16 right-6 z-[80] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          onClick={() => dismissToast(t.id)}
          className={`pill animate-rise cursor-pointer ${
            t.kind === 'success' ? 'ok' : t.kind === 'warn' ? 'warn' : t.kind === 'danger' ? 'danger' : 'accent'
          }`}
          role="status"
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
