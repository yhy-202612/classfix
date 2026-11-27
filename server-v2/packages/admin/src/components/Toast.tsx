import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type ToastTone = 'error' | 'success' | 'info';

type ToastItem = { id: number; tone: ToastTone; message: string };

type ToastApi = {
  push: (message: string, tone?: ToastTone) => void;
  error: (message: string) => void;
  success: (message: string) => void;
  info: (message: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast 必须在 ToastProvider 内使用。');
  return ctx;
}

const TONE_STYLE: Record<ToastTone, { wrap: string; icon: ReactNode }> = {
  error: {
    wrap: 'border-l-[3px] border-l-bad',
    icon: <AlertTriangle size={16} className="mt-0.5 shrink-0 text-bad" />,
  },
  success: {
    wrap: 'border-l-[3px] border-l-good',
    icon: <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-good" />,
  },
  info: {
    wrap: 'border-l-[3px] border-l-brand',
    icon: <Info size={16} className="mt-0.5 shrink-0 text-brand" />,
  },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const push = useCallback(
    (message: string, tone: ToastTone = 'info') => {
      const id = Date.now() + Math.floor(Math.random() * 1000);
      setItems((prev) => [...prev.slice(-2), { id, tone, message }]);
      window.setTimeout(() => remove(id), 4500);
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      error: (message: string) => push(message, 'error'),
      success: (message: string) => push(message, 'success'),
      info: (message: string) => push(message, 'info'),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-3 bottom-3 z-50 flex flex-col items-stretch gap-2 md:inset-x-auto md:right-6 md:bottom-6 md:w-[360px]"
        role="status"
        aria-live="polite"
      >
        {items.map((item) => {
          const style = TONE_STYLE[item.tone];
          return (
            <div
              key={item.id}
              className={clsx(
                'pointer-events-auto flex items-start gap-2 rounded-xl border border-line bg-white px-3 py-2.5 shadow-card',
                style.wrap,
              )}
            >
              {style.icon}
              <p className="min-w-0 flex-1 break-words text-[13px] leading-5 text-ink">{item.message}</p>
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() => remove(item.id)}
                className="rounded p-0.5 text-muted transition hover:bg-brand-100 hover:text-ink"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
