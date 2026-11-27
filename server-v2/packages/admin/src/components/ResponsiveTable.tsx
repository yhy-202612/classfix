import clsx from 'clsx';
import type { ReactNode } from 'react';
import { EmptyState } from './States';

export type Column<T> = {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
  /** 移动端卡片里的主标题字段（不显示标签） */
  primary?: boolean;
};

export type ResponsiveTableProps<T> = {
  columns: Array<Column<T>>;
  rows: T[];
  getKey: (row: T) => string;
  actions?: (row: T) => ReactNode;
  empty?: ReactNode;
  /** 桌面端表格最小宽度，避免列被压得太窄 */
  minWidthClass?: string;
};

/**
 * 桌面端渲染表格，宽度 < 768px 时渲染成卡片列表（同一份列定义）。
 */
export function ResponsiveTable<T>({
  columns,
  rows,
  getKey,
  actions,
  empty,
  minWidthClass = 'min-w-[720px]',
}: ResponsiveTableProps<T>) {
  if (!rows.length) return <>{empty ?? <EmptyState title="暂无数据" />}</>;

  return (
    <>
      <div className="hidden w-full overflow-x-auto md:block">
        <table className={clsx('w-full border-collapse text-left text-[13px]', minWidthClass)}>
          <thead>
            <tr className="border-b border-line text-[12px] font-semibold uppercase tracking-wide text-muted">
              {columns.map((column) => (
                <th key={column.key} className={clsx('whitespace-nowrap px-2 py-2', column.className)}>
                  {column.header}
                </th>
              ))}
              {actions ? <th className="whitespace-nowrap px-2 py-2 text-right">操作</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={getKey(row)} className="border-b border-line/70 last:border-b-0 hover:bg-brand-50/70">
                {columns.map((column) => (
                  <td key={column.key} className={clsx('px-2 py-2.5 align-top text-ink', column.className)}>
                    {column.render(row)}
                  </td>
                ))}
                {actions ? (
                  <td className="px-2 py-2.5 text-right align-top">
                    <div className="flex flex-wrap justify-end gap-1.5">{actions(row)}</div>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="space-y-2.5 md:hidden">
        {rows.map((row) => {
          const primary = columns.find((column) => column.primary) ?? columns[0];
          const rest = columns.filter((column) => column !== primary);
          return (
            <li key={getKey(row)} className="rounded-xl border border-line bg-white p-3">
              <div className="text-sm font-semibold text-ink">{primary.render(row)}</div>
              <dl className="mt-1.5 space-y-1">
                {rest.map((column) => (
                  <div key={column.key} className="flex items-start justify-between gap-3 text-[12.5px]">
                    <dt className="shrink-0 text-muted">{column.header}</dt>
                    <dd className="min-w-0 break-words text-right text-ink">{column.render(row)}</dd>
                  </div>
                ))}
              </dl>
              {actions ? (
                <div className="mt-2 flex flex-wrap gap-1.5 border-t border-line/70 pt-2">{actions(row)}</div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}
