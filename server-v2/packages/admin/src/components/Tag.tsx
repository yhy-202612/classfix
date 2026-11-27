import clsx from 'clsx';
import type { ReactNode } from 'react';
import { statusMeta, type Tone } from '../lib/format';

const TONE_CLASS: Record<Tone, string> = {
  brand: 'bg-brand-100 text-brand',
  good: 'bg-[#e7f6ee] text-good',
  warn: 'bg-[#fdf2e2] text-warn',
  bad: 'bg-[#fdecec] text-bad',
  neutral: 'border border-line bg-brand-50 text-muted',
};

export function Tag({
  tone = 'brand',
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] font-semibold',
        TONE_CLASS[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusTag({ status }: { status?: string }) {
  const meta = statusMeta(status);
  return <Tag tone={meta.tone}>{meta.label}</Tag>;
}
