import clsx from 'clsx';
import type { ReactNode } from 'react';

export type CardProps = {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
};

export function Card({ title, subtitle, actions, children, className, bodyClassName }: CardProps) {
  const hasHeader = Boolean(title || subtitle || actions);
  return (
    <section className={clsx('rounded-2xl border border-line bg-white p-4 shadow-card md:p-5', className)}>
      {hasHeader ? (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2 md:gap-3">
          <div className="min-w-0">
            {title ? <h2 className="text-[15px] font-semibold leading-6 text-ink">{title}</h2> : null}
            {subtitle ? <p className="mt-0.5 text-[12.5px] leading-5 text-muted">{subtitle}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}
