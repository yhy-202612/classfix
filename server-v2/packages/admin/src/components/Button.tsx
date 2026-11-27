import clsx from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { Loader2 } from 'lucide-react';
import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';
type Size = 'sm' | 'md';

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: LucideIcon;
  full?: boolean;
};

const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'border-brand bg-brand text-white hover:border-brand-dark hover:bg-brand-dark',
  secondary: 'border-line bg-white text-ink hover:border-brand-300 hover:bg-brand-100',
  danger: 'border-line bg-white text-bad hover:border-red-200 hover:bg-red-50',
  ghost: 'border-transparent bg-transparent text-muted hover:bg-brand-100 hover:text-brand',
};

const SIZE_CLASS: Record<Size, string> = {
  sm: 'gap-1.5 px-2.5 py-1.5 text-[13px]',
  md: 'gap-2 px-3.5 py-2 text-sm',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon: Icon,
  full = false,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  const iconSize = size === 'sm' ? 14 : 16;
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center rounded-lg border font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-60',
        VARIANT_CLASS[variant],
        SIZE_CLASS[size],
        full && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 size={iconSize} className="animate-spin" />
      ) : Icon ? (
        <Icon size={iconSize} />
      ) : null}
      {children}
    </button>
  );
}
