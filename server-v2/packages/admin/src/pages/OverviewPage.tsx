import type { AdminOverviewRes, AdminStats } from '@classfix/shared';
import { Gauge, RefreshCw, Settings2, Stethoscope } from 'lucide-react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { StatCard } from '../components/StatCard';
import { Tag } from '../components/Tag';
import { adminApi } from '../lib/api';
import { fmtDateTime, formatStatValue, truncate } from '../lib/format';
import { useResource } from '../lib/hooks';

const STAT_ITEMS: Array<{ key: keyof AdminStats; label: string }> = [
  { key: 'users', label: '用户' },
  { key: 'diagnoses', label: '诊断总数' },
  { key: 'diagnosesToday', label: '今日诊断' },
  { key: 'issues', label: '问题数' },
  { key: 'issuesNew', label: '新问题' },
  { key: 'solutions', label: '方案数' },
  { key: 'solutionsDraft', label: '草稿方案' },
  { key: 'solutionsPublished', label: '已发布方案' },
  { key: 'solutionsBaseline', label: '基线归档' },
  { key: 'rulesVersion', label: '规则版本' },
  { key: 'catalog', label: '离线基线数' },
  { key: 'autoPublish', label: '自动发布' },
];

export function OverviewPage({ refreshKey }: { refreshKey: number }) {
  const { data, loading, error, reload } = useResource<AdminOverviewRes>(
    () => adminApi.overview(),
    [refreshKey],
  );

  const stats = data?.stats;
  const settings = data?.settings;

  return (
    <div className="space-y-3.5 md:space-y-4">
      <Card
        title="概览"
        subtitle="云端累计数据，进入页面或点「刷新」时更新"
        actions={
          <Button size="sm" icon={RefreshCw} loading={loading && Boolean(data)} onClick={() => void reload()}>
            刷新
          </Button>
        }
      >
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : !stats ? (
          <LoadingBlock />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
              {STAT_ITEMS.map(({ key, label }) => (
                <StatCard
                  key={key}
                  label={label}
                  value={formatStatValue(stats[key])}
                  highlight={key === 'autoPublish' ? stats.autoPublish : false}
                />
              ))}
            </div>
            <p className="mt-3 flex items-center gap-1.5 text-[12.5px] text-muted">
              <Gauge size={14} className="shrink-0 text-brand" />
              {stats.autoPublish
                ? '当前：AI 归纳后自动发布，不需要人工复核。'
                : '当前：AI 归纳后等人工复核（默认）。'}
            </p>
          </>
        )}
      </Card>

      <Card
        title="设置摘要"
        subtitle="完整修改请到「设置」页"
        actions={
          <Tag tone="neutral">
            <Settings2 size={12} className="mr-1" />
            只读
          </Tag>
        }
      >
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : !settings ? (
          <LoadingBlock />
        ) : (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">自动发布</dt>
              <dd>
                <Tag tone={settings.autoPublish ? 'good' : 'neutral'}>
                  {settings.autoPublish ? '已开启' : '已关闭'}
                </Tag>
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">新用户积分</dt>
              <dd className="font-semibold text-ink">{settings.newUserPoints}</dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">每日赠送</dt>
              <dd className="font-semibold text-ink">{settings.dailyGrant}</dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">每人每日诊断上限</dt>
              <dd className="font-semibold text-ink">{settings.maxDiagnosesPerDay}</dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">视频 / 声音成本</dt>
              <dd className="font-semibold text-ink">
                {settings.cost.video} / {settings.cost.audio}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-line/70 pb-1.5">
              <dt className="text-muted">巡检 / 课前巡检成本</dt>
              <dd className="font-semibold text-ink">
                {settings.cost.check} / {settings.cost.preflight}
              </dd>
            </div>
          </dl>
        )}
      </Card>

      <Card title="最近诊断" subtitle="最近 10 条诊断的建议摘要（前 120 字）">
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : !data ? (
          <LoadingBlock />
        ) : data.recent.length ? (
          <ul className="divide-y divide-line">
            {data.recent.map((item) => (
              <li key={item.id} className="py-2.5 first:pt-0 last:pb-0">
                <p className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                  <Stethoscope size={13} className="shrink-0 text-brand" />
                  <span className="font-mono">{fmtDateTime(item.at)}</span>
                  <Tag tone="brand">{item.kind}</Tag>
                </p>
                <p className="mt-1 break-words text-[13px] leading-6 text-ink">{truncate(item.advice, 120)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={Stethoscope}
            title="暂无诊断记录"
            description="客户端登录后用积分做 AI 诊断，结果会汇总到这里。"
          />
        )}
      </Card>
    </div>
  );
}
