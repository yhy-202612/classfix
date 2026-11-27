import type { AdminIssueListRes, Issue, IssueStatus } from '@classfix/shared';
import { Bug, ChevronDown, ChevronUp, Lightbulb, ListFilter } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Select } from '../components/Field';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { StatusTag, Tag } from '../components/Tag';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, fmtDateTime } from '../lib/format';
import { useResource } from '../lib/hooks';

const FILTERS: Array<{ value: '' | IssueStatus; label: string }> = [
  { value: '', label: '全部' },
  { value: 'new', label: '待处理' },
  { value: 'reviewed', label: '已生成方案' },
  { value: 'adopted', label: '已采纳' },
];

export function IssuesPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const [status, setStatus] = useState<'' | IssueStatus>('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyId, setBusyId] = useState('');

  const load = useCallback(() => adminApi.issues(status || undefined), [status]);
  const { data, loading, error, reload } = useResource<AdminIssueListRes>(load, [load, refreshKey]);

  /** 按 count 倒序（服务端已排序，这里再保证一次） */
  const items = useMemo(() => [...(data?.items ?? [])].sort((a, b) => b.count - a.count), [data]);

  async function generate(issue: Issue) {
    setBusyId(issue.id);
    try {
      await adminApi.generateSolution(issue.id);
      toast.success('已生成方案，请到「方案库」复核。');
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId('');
    }
  }

  return (
    <Card
      title="问题库"
      subtitle="AI 诊断里提出、但离线流程未覆盖的问题（按出现次数倒序）"
      actions={
        <div className="flex items-center gap-2">
          <span className="hidden items-center gap-1.5 text-[12.5px] text-muted sm:flex">
            <ListFilter size={14} />
            筛选
          </span>
          <Select
            aria-label="按状态筛选问题"
            className="w-[150px]"
            value={status}
            onChange={(event) => setStatus(event.target.value as '' | IssueStatus)}
          >
            {FILTERS.map((filter) => (
              <option key={filter.value || 'all'} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </Select>
        </div>
      }
    >
      {error ? (
        <ErrorState message={error} onRetry={() => void reload()} />
      ) : loading && !data ? (
        <LoadingBlock />
      ) : items.length ? (
        <ul className="divide-y divide-line">
          {items.map((item) => {
            const open = Boolean(expanded[item.id]);
            return (
              <li key={item.id} className="py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                      <span className="break-words">{item.title}</span>
                      <StatusTag status={item.status} />
                    </h3>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
                      <span>{item.kind}</span>
                      <span>·</span>
                      <span>出现 {item.count} 次</span>
                      {item.checkKey ? (
                        <>
                          <span>·</span>
                          <span>
                            检测项 <span className="font-mono text-ink">{item.checkKey}</span>
                          </span>
                        </>
                      ) : null}
                    </p>
                    <p className="mt-0.5 text-[12px] text-muted">
                      首次 {fmtDateTime(item.firstSeen)} · 最近 {fmtDateTime(item.lastSeen)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      size="sm"
                      variant="primary"
                      icon={Lightbulb}
                      loading={busyId === item.id}
                      onClick={() => void generate(item)}
                    >
                      AI 生成方案
                    </Button>
                    <Button
                      size="sm"
                      icon={open ? ChevronUp : ChevronDown}
                      onClick={() => setExpanded((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
                    >
                      {open ? '收起片段' : `片段 ${item.samples.length}`}
                    </Button>
                  </div>
                </div>

                {open ? (
                  <div className="mt-2 space-y-1.5 rounded-xl border border-line bg-brand-50/70 p-2.5">
                    {item.samples.length ? (
                      item.samples.map((sample, index) => (
                        <div
                          key={`${item.id}-${index}`}
                          className="border-b border-line/70 pb-1.5 last:border-b-0 last:pb-0"
                        >
                          <p className="text-[12.5px] leading-5 text-ink">
                            <span className="text-muted">依据：</span>
                            {sample.evidence || '—'}
                          </p>
                          <p className="text-[12.5px] leading-5 text-ink">
                            <span className="text-muted">处置：</span>
                            {sample.action || '—'}
                          </p>
                          {sample.at ? <p className="text-[11.5px] text-muted">{fmtDateTime(sample.at)}</p> : null}
                        </div>
                      ))
                    ) : (
                      <p className="text-[12.5px] text-muted">这条问题还没有采集到片段。</p>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          icon={Bug}
          title={status ? '该状态下没有问题' : '还没有问题记录'}
          description={
            status
              ? '换个筛选条件看看，或切回「全部」。'
              : '客户端用积分做 AI 诊断后，AI 提到的新问题会自动汇总到这里。'
          }
          action={
            status ? (
              <Button size="sm" onClick={() => setStatus('')}>
                查看全部
              </Button>
            ) : null
          }
        />
      )}

      {items.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-muted">
          <Tag tone="neutral">共 {items.length} 条</Tag>
          <span>生成方案后到「方案库」复核发布；批量生成在「方案库」页顶部。</span>
        </div>
      ) : null}
    </Card>
  );
}
