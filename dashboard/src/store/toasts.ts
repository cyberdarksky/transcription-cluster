import { create } from 'zustand';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastItem {
  id: string;
  message: string;
  type: ToastType;
  dismissing: boolean;
}

interface ToastStore {
  toasts: ToastItem[];
  add: (message: string, type?: ToastType, duration?: number) => void;
  dismiss: (id: string) => void;
}

let _id = 0;

export const useToastStore = create<ToastStore>((set, get) => ({
  toasts: [],

  add: (message, type = 'info', duration = 4000) => {
    const id = String(++_id);
    set(s => ({
      toasts: [...s.toasts, { id, message, type, dismissing: false }],
    }));

    // After duration, trigger exit animation then remove
    const dismissTimer = setTimeout(() => get().dismiss(id), duration);
    return () => clearTimeout(dismissTimer);
  },

  dismiss: (id) => {
    // Mark as dismissing for exit animation
    set(s => ({
      toasts: s.toasts.map(t => t.id === id ? { ...t, dismissing: true } : t),
    }));
    // Remove after animation completes
    setTimeout(() => {
      set(s => ({ toasts: s.toasts.filter(t => t.id !== id) }));
    }, 220);
  },
}));

// Convenience hook
export function useToast() {
  const add = useToastStore(s => s.add);
  return {
    success: (msg: string) => add(msg, 'success'),
    error:   (msg: string) => add(msg, 'error'),
    warning: (msg: string) => add(msg, 'warning'),
    info:    (msg: string) => add(msg, 'info'),
  };
}
