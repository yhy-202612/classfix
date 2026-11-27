import clsx from 'clsx';
import { AlertTriangle, Inbox, Loader2, RotateCw } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from './Button';

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={clsx('animate-spin text-brand', className)} />;
}

export function LoadingBlock({ label = '加载中…', className }: { label?: string; className?: string }) {
  return (
    <div className={clsx('flex items-center justify-center gap-2 py-8 text-[13px] text-muted', className)}>
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line bg-brand-50/60 px-4 py-8 text-center">
      <Icon size={22} className="text-brand-300" />
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? <p className="max-w-xl text-[12.5px] leading-5 text-muted">{description}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  retryLabel = '重试',
}: {
  message: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#f6d9d9] bg-[#fdecec]/60 px-3 py-2.5">
      <p className="flex min-w-0 items-start gap-2 text-[13px] leading-5 text-bad">
        <AlertTriangle size={15} className="mt-0.5 shrink-0" />
        <span className="break-words">{message}</span>
      </p>
      {onRetry ? (
        <Button size="sm" icon={RotateCw} onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}
