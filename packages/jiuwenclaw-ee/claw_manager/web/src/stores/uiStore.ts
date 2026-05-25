import { create } from 'zustand';

export type Toast = {
  id: string;
  kind: 'info' | 'success' | 'warn' | 'danger';
  message: string;
};

interface UiState {
  toasts: Toast[];
  pushToast: (t: Omit<Toast, 'id'>) => void;
  dismissToast: (id: string) => void;
}

export const useUiStore = create<UiState>((set) => ({
  toasts: [],
  pushToast: (t) =>
    set((s) => {
      const id = `t-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
      const toast: Toast = { id, ...t };
      setTimeout(() => {
        useUiStore.getState().dismissToast(id);
      }, 4500);
      return { toasts: [...s.toasts, toast] };
    }),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));

export function toast(kind: Toast['kind'], message: string) {
  useUiStore.getState().pushToast({ kind, message });
}
