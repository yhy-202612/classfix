import type { AdminOverviewRes, Rules, RuleItem } from '@classfix/shared';
import { Package, Send } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { Tag } from '../components/Tag';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, fmtDateTime } from '../lib/format';
import { useResource } from '../lib/hooks';

export function RulesPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const { data, reload } = useResource<AdminOverviewRes>(() => adminApi.overview(), [refreshKey]);
  const [result, setResult] = useState<Rules | null>(null);
  const [publishing, setPublishing] = useState(false);

  async function publish() {
    setPublishing(true);
    try {
      const response = await adminApi.publish();
      setResult(response.rules);
      toast.success(`规则包已发布，版本 ${response.rules.version}，共 ${response.rules.items.length} 条。`);
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setPublishing(false);
    }
  }

  const currentVersion = data?.stats.rulesVersion;
  const published = data?.stats.solutionsPublished;

  return (
    <div className="space-y-3.5 md:space-y-4">
      <Card
        title="规则包"
        subtitle="把已发布的方案打包成客户端可拉取的规则集，发布后版本号递增"
        actions={
          <Button variant="primary" icon={Send} loading={publishing} onClick={() => void publish()}>
            发布规则包
          </Button>
        }
      >
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          <div className="rounded-xl border border-line bg-[#f8fbff] px-3 py-2.5">
            <p className="text-[22px] font-bold leading-7 text-brand">{currentVersion ?? '—'}</p>
            <p className="text-[12px] text-muted">当前规则版本</p>
          </div>
          <div className="rounded-xl border border-line bg-[#f8fbff] px-3 py-2.5">
            <p className="text-[22px] font-bold leading-7 text-brand">{published ?? '—'}</p>
            <p className="text-[12px] text-muted">已发布方案数</p>
          </div>
          <div className="rounded-xl border border-line bg-[#f8fbff] px-3 py-2.5">
            <p className="text-[22px] font-bold leading-7 text-brand">{result ? result.items.length : '—'}</p>
            <p className="text-[12px] text-muted">本次打包条数</p>
          </div>
        </div>
        <p className="mt-3 text-[12.5px] leading-5 text-muted">
          规则包只包含「已发布」的方案；离线基线（baseline）不会进入规则包。客户端用 ETag 增量拉取，重复发布会重置缓存。
        </p>
      </Card>

      <Card
        title="本次发布结果"
        subtitle={result ? `版本 ${result.version} · 发布 ${fmtDateTime(result.publishedAt)}` : '还没有发布操作'}
        actions={result ? <Tag tone="good">共 {result.items.length} 条</Tag> : null}
      >
        {result ? (
          result.items.length ? (
            <ul className="divide-y divide-line">
              {result.items.map((item: RuleItem, index) => (
                <li key={item.id || `rule-${index}`} className="py-2.5 first:pt-0 last:pb-0">
                  <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                    <span className="font-mono text-[11.5px] text-muted">#{index + 1}</span>
                    <span className="break-words">{item.title}</span>
                  </h3>
                  <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
                    <span>
                      kind <span className="font-mono text-ink">{item.match?.kind || '—'}</span>
                    </span>
                    <span>·</span>
                    <span className="flex flex-wrap items-center gap-1">
                      checkKeys
                      {(item.match?.checkKeys ?? []).length ? (
                        (item.match?.checkKeys ?? []).map((key) => (
                          <span key={key} className="rounded bg-brand-100 px-1.5 py-0.5 font-mono text-[11.5px] text-brand">
                            {key}
                          </span>
                        ))
                      ) : (
                        <span className="text-ink">—（所有检测）</span>
                      )}
                    </span>
                  </p>
                  {item.steps?.length ? (
                    <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[12.5px] leading-6 text-ink">
                      {item.steps.map((step, stepIndex) => (
                        <li key={`${item.id}-${stepIndex}`}>{step}</li>
                      ))}
                    </ol>
                  ) : (
                    <p className="mt-1 text-[12.5px] text-muted">没有步骤。</p>
                  )}
                  {item.verify ? (
                    <p className="mt-1 text-[12px] text-muted">验证：{item.verify}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={Package}
              title="规则包里没有条目"
              description="先到「方案库」把方案设为已发布，再发布规则包。"
            />
          )
        ) : (
          <EmptyState
            icon={Package}
            title="还没有本次发布记录"
            description="点右上角「发布规则包」后，这里会列出打包进规则包的条目（match.kind / checkKeys / 标题 / 步骤）。"
          />
        )}
      </Card>
    </div>
  );
}
