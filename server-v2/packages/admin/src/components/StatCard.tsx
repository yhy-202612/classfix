import clsx from 'clsx';
import type { ReactNode } from 'react';

export function StatCard({
  label,
  value,
  highlight = false,
}: {
  label: ReactNode;
  value: ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-xl border border-line bg-[#f8fbff] px-3 py-2.5">
      <p
        className={clsx(
          'text-[22px] font-bold leading-7 tracking-tight',
          highlight ? 'text-good' : 'text-brand',
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-[12px] leading-4 text-muted">{label}</p>
    </div>
  );
}
