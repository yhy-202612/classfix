import clsx from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { LogOut, RefreshCw, ShieldCheck } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from './Button';

export type NavItem<K extends string = string> = {
  key: K;
  label: string;
  icon: LucideIcon;
  description?: string;
};

export type LayoutProps<K extends string = string> = {
  nav: Array<NavItem<K>>;
  active: K;
  onNavigate: (key: K) => void;
  onRefresh: () => void;
  onLogout: () => void;
  refreshing?: boolean;
  children: ReactNode;
};

export function Layout<K extends string>({
  nav,
  active,
  onNavigate,
  onRefresh,
  onLogout,
  refreshing = false,
  children,
}: LayoutProps<K>) {
  const current = nav.find((item) => item.key === active);

  return (
    <div className="min-h-screen bg-brand-50">
      <div className="md:flex">
        {/* 桌面端左侧导航 */}
        <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-white md:flex">
          <div className="flex items-center gap-2.5 border-b border-line px-4 py-4">
            <span className="flex size-9 items-center justify-center rounded-xl bg-brand text-white">
              <ShieldCheck size={18} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-bold leading-5 text-ink">ClassFix 云端后台</span>
              <span className="block text-[11.5px] leading-4 text-muted">Admin Console</span>
            </span>
          </div>

          <nav className="flex-1 space-y-1 overflow-y-auto px-2.5 py-3">
            {nav.map((item) => {
              const Icon = item.icon;
              const isActive = item.key === active;
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => onNavigate(item.key)}
                  aria-current={isActive ? 'page' : undefined}
                  className={clsx(
                    'flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition',
                    isActive
                      ? 'bg-brand text-white shadow-sm'
                      : 'text-ink hover:bg-brand-100 hover:text-brand',
                  )}
                >
                  <Icon size={16} className={clsx('shrink-0', isActive ? 'text-white' : 'text-brand')} />
                  <span className="min-w-0 flex-1 truncate font-medium">{item.label}</span>
                </button>
              );
            })}
          </nav>

          <div className="border-t border-line px-2.5 py-3">
            <button
              type="button"
              onClick={onLogout}
              className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-muted transition hover:bg-brand-100 hover:text-bad"
            >
              <LogOut size={16} className="shrink-0" />
              <span className="font-medium">退出登录</span>
            </button>
          </div>
        </aside>

        <div className="min-w-0 flex-1 md:pl-60">
          {/* 顶部标题栏 */}
          <header className="sticky top-0 z-20 border-b border-line bg-white/95 backdrop-blur">
            <div className="mx-auto flex max-w-6xl items-center gap-2 px-3 py-2.5 md:px-6 md:py-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand text-white md:hidden">
                <ShieldCheck size={16} />
              </span>
              <div className="min-w-0 flex-1">
                <h1 className="truncate text-[15px] font-bold leading-5 text-ink md:text-base">
                  {current?.label ?? '云端后台'}
                </h1>
                <p className="truncate text-[11.5px] leading-4 text-muted">
                  {current?.description ?? 'ClassFix 管理后台'}
                </p>
              </div>
              <span className="hidden items-center gap-1.5 rounded-full bg-[#e7f6ee] px-2.5 py-1 text-[12px] font-semibold text-good sm:flex">
                <span className="size-1.5 rounded-full bg-good" />
                已登录
              </span>
              <Button size="sm" variant="secondary" icon={RefreshCw} loading={refreshing} onClick={onRefresh}>
                <span className="hidden sm:inline">刷新</span>
              </Button>
              <Button size="sm" variant="secondary" icon={LogOut} onClick={onLogout}>
                <span className="hidden sm:inline">退出</span>
              </Button>
            </div>

            {/* 移动端横向滚动 tab */}
            <nav className="no-scrollbar flex gap-1.5 overflow-x-auto px-3 pb-2 md:hidden">
              {nav.map((item) => {
                const Icon = item.icon;
                const isActive = item.key === active;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => onNavigate(item.key)}
                    aria-current={isActive ? 'page' : undefined}
                    className={clsx(
                      'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] transition',
                      isActive
                        ? 'border-brand bg-brand text-white'
                        : 'border-line bg-white text-ink hover:bg-brand-100',
                    )}
                  >
                    <Icon size={14} className="shrink-0" />
                    <span className="whitespace-nowrap font-medium">{item.label}</span>
                  </button>
                );
              })}
            </nav>
          </header>

          <main className="mx-auto max-w-6xl space-y-3.5 px-3 py-3.5 md:space-y-4 md:px-6 md:py-5">{children}</main>
        </div>
      </div>
    </div>
  );
}
