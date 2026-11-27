import type { AdminCatalogRes, CatalogItem } from '@classfix/shared';
import { Archive, Database, FileJson, Upload } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Field, Textarea } from '../components/Field';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { Tag } from '../components/Tag';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, fmtDateTime } from '../lib/format';
import { useResource } from '../lib/hooks';

type BaselineEntry = { key: string; title: string; action: string; origin: string };

function normalizeEntries(raw: unknown[]): BaselineEntry[] {
  return raw
    .map((entry) => {
      const source = (entry ?? {}) as Record<string, unknown>;
      return {
        key: String(source.key ?? '').trim(),
        title: String(source.title ?? '').trim(),
        action: String(source.action ?? '').trim(),
        origin: String(source.origin ?? 'offline').trim() || 'offline',
      };
    })
    .filter((entry) => entry.title);
}

export function CatalogPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const { data, loading, error, reload } = useResource<AdminCatalogRes>(() => adminApi.catalog(), [refreshKey]);
  const [raw, setRaw] = useState('');
  const [importing, setImporting] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const items = useMemo(() => data?.items ?? [], [data]);

  async function importJson() {
    const text = raw.trim();
    if (!text) {
      toast.error('请先粘贴基线 JSON。');
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      toast.error('JSON 解析失败，请检查格式。');
      return;
    }
    const list = Array.isArray(parsed) ? parsed : ((parsed as { items?: unknown }).items ?? null);
    if (!Array.isArray(list) || !list.length) {
      toast.error('JSON 里没有 items 数组。');
      return;
    }
    const entries = normalizeEntries(list);
    if (!entries.length) {
      toast.error('每条基线都需要 title 字段。');
      return;
    }
    setImporting(true);
    try {
      const result = await adminApi.saveCatalog(entries);
      toast.success(`已导入 ${result.count} 条基线。`);
      setRaw('');
      await reload();
    } catch (err) {
      toast.error(`导入失败：${errorMessage(err)}`);
    } finally {
      setImporting(false);
    }
  }

  async function importAsSolutions() {
    setArchiving(true);
    try {
      const result = await adminApi.importBaseline();
      toast.success(
        result.created.length
          ? `已归档 ${result.created.length} 条离线方案（状态=离线基线，不进规则包）。`
          : '没有新增（都已归档过）。',
      );
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setArchiving(false);
    }
  }

  async function readFile(file: File) {
    try {
      const text = await file.text();
      setRaw(text);
      toast.info(`已读取 ${file.name}，确认后点「导入基线」。`);
    } catch {
      toast.error('读取文件失败，请重试或直接粘贴 JSON。');
    }
  }

  return (
    <div className="space-y-3.5 md:space-y-4">
      <Card
        title="离线方案基线"
        subtitle="客户端已覆盖的方案，会注入诊断提示词，避免 AI 重复上报"
        actions={
          <Tag tone="neutral">
            <Database size={12} className="mr-1" />
            {data ? `${items.length} 条` : '—'}
          </Tag>
        }
      >
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : loading && !data ? (
          <LoadingBlock />
        ) : items.length ? (
          <ul className="divide-y divide-line">
            {items.map((item: CatalogItem) => (
              <li key={`${item.key}-${item.title}`} className="py-2.5 first:pt-0 last:pb-0">
                <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                  <span className="break-words">{item.title}</span>
                  <Tag tone="neutral">{item.origin || 'offline'}</Tag>
                  {item.key ? <span className="font-mono text-[11.5px] text-muted">{item.key}</span> : null}
                </h3>
                {item.action ? (
                  <p className="mt-0.5 text-[12.5px] leading-5 text-muted">{item.action}</p>
                ) : null}
                {item.at ? <p className="text-[11.5px] text-muted">{fmtDateTime(item.at)}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={Database}
            title="还没有导入基线"
            description="导入后诊断提示词会自动带上「这些已覆盖，不必重复」。"
          />
        )}
        <p className="mt-3 text-[12px] text-muted">
          {items.length
            ? `已导入 ${items.length} 条（命中后会告诉模型这些不用再提）。`
            : '还没有导入基线，在下方粘贴 catalog.json 内容后导入。'}
        </p>
      </Card>

      <Card
        title="导入基线 JSON"
        subtitle="支持 [ {...} ] 数组，或 { items: [ {...} ] }；字段：key / title / action / origin"
      >
        <div className="space-y-3">
          <Field label="基线 JSON">
            <Textarea
              value={raw}
              rows={8}
              spellCheck={false}
              className="font-mono text-[12px]"
              placeholder={'[\n  { "key": "audio", "title": "投影仪没声音", "action": "重新选择输出设备", "origin": "offline" }\n]'}
              onChange={(event) => setRaw(event.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" icon={Upload} loading={importing} onClick={() => void importJson()}>
              导入基线
            </Button>
            <Button icon={FileJson} onClick={() => fileRef.current?.click()}>
              选择 JSON 文件
            </Button>
            <Button icon={Archive} loading={archiving} onClick={() => void importAsSolutions()}>
              导入为方案
            </Button>
            {raw ? (
              <Button variant="ghost" onClick={() => setRaw('')}>
                清空
              </Button>
            ) : null}
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void readFile(file);
                event.target.value = '';
              }}
            />
          </div>
          <p className="text-[12px] leading-5 text-muted">
            「导入基线」= 覆盖离线基线列表，会注入诊断提示词；「导入为方案」= 把基线归档进方案库（状态=离线基线，不进规则包）。
          </p>
        </div>
      </Card>
    </div>
  );
}
