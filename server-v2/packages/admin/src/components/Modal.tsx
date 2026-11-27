import clsx from 'clsx';
import { X } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';

export type ModalProps = {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
};

export function Modal({ open, title, description, onClose, children, footer, wide = false }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-ink/40 p-0 backdrop-blur-[2px] md:items-center md:p-6"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className={clsx(
          'max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-line bg-white p-4 shadow-xl md:rounded-2xl md:p-5',
          wide ? 'md:max-w-2xl' : 'md:max-w-lg',
        )}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold leading-6 text-ink">{title}</h2>
            {description ? <p className="mt-0.5 text-[12.5px] leading-5 text-muted">{description}</p> : null}
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="rounded-lg p-1 text-muted transition hover:bg-brand-100 hover:text-ink"
          >
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3">{children}</div>
        {footer ? <div className="mt-4 flex flex-wrap justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  );
}
