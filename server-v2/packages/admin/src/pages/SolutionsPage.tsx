import type { AdminSolutionListRes, Solution } from '@classfix/shared';
import {
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ListChecks,
  Pencil,
  Plus,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Field, Input, Textarea } from '../components/Field';
import { Modal } from '../components/Modal';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { StatusTag, Tag } from '../components/Tag';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, splitKeys, splitLines } from '../lib/format';
import { useResource } from '../lib/hooks';

type EditorState = {
  mode: 'create' | 'edit';
  id?: string;
  title: string;
  kind: string;
  confidence: string;
  /** 一行一条检测项 key */
  appliesTo: string;
  /** 一行一条步骤 */
  steps: string;
  verify: string;
};

type ReviewState = {
  id: string;
  title: string;
  status: 'published' | 'rejected';
  note: string;
};

function editorFrom(item: Solution): EditorState {
  return {
    mode: 'edit',
    id: item.id,
    title: item.title,
    kind: item.kind ?? '',
    confidence: item.confidence ?? '',
    appliesTo: (item.appliesTo ?? []).join('\n'),
    steps: (item.steps ?? []).join('\n'),
    verify: item.verify ?? '',
  };
}

const EMPTY_EDITOR: EditorState = {
  mode: 'create',
  title: '',
  kind: '',
  confidence: 'medium',
  appliesTo: '',
  steps: '',
  verify: '',
};

export function SolutionsPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const { data, loading, error, reload } = useResource<AdminSolutionListRes>(
    () => adminApi.solutions(),
    [refreshKey],
  );

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [review, setReview] = useState<ReviewState | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const items = useMemo(() => data?.items ?? [], [data]);

  async function runGenerateAll() {
    setBatchRunning(true);
    try {
      const result = await adminApi.generateAllSolutions();
      toast.success(
        `已生成 ${result.created.length} 条方案${result.failed.length ? `，失败 ${result.failed.length} 条` : ''}。`,
      );
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBatchRunning(false);
    }
  }

  async function submitEditor() {
    if (!editor) return;
    const title = editor.title.trim();
    if (!title) {
      toast.error('方案名称不能为空。');
      return;
    }
    setSaving(true);
    try {
      await adminApi.saveSolution({
        ...(editor.id ? { id: editor.id } : {}),
        kind: editor.kind.trim(),
        title,
        appliesTo: splitKeys(editor.appliesTo),
        steps: splitLines(editor.steps),
        verify: editor.verify.trim(),
        confidence: editor.confidence.trim(),
      });
      toast.success(editor.mode === 'edit' ? '方案已保存。' : '方案已创建。');
      setEditor(null);
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function submitReview() {
    if (!review) return;
    setReviewSaving(true);
    try {
      await adminApi.reviewSolution({
        id: review.id,
        status: review.status,
        note: review.note.trim(),
      });
      toast.success(review.status === 'published' ? '方案已发布，规则包已更新。' : '方案已驳回。');
      setReview(null);
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setReviewSaving(false);
    }
  }

  return (
    <div className="space-y-3.5 md:space-y-4">
      <Card
        title="方案库"
        subtitle="含已归档的离线基线；发布后规则包版本立即递增"
        actions={
          <>
            <Button size="sm" variant="primary" icon={Sparkles} loading={batchRunning} onClick={() => void runGenerateAll()}>
              批量生成
            </Button>
            <Button size="sm" icon={Plus} onClick={() => setEditor({ ...EMPTY_EDITOR })}>
              新建方案
            </Button>
          </>
        }
      >
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : loading && !data ? (
          <LoadingBlock />
        ) : items.length ? (
          <ul className="divide-y divide-line">
            {items.map((item) => {
              const folded = Boolean(collapsed[item.id]);
              return (
                <li key={item.id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                        <span className="break-words">{item.title}</span>
                        <StatusTag status={item.status} />
                      </h3>
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
                        <span>{item.kind || '通用'}</span>
                        <span>·</span>
                        <span>置信度 {item.confidence || 'medium'}</span>
                        <span>·</span>
                        <span className="font-mono">{(item.appliesTo ?? []).join(' / ') || '未指定检测项'}</span>
                        <span>·</span>
                        <span>
                          {item.steps?.length ?? 0} 步
                        </span>
                        {item.origin ? (
                          <>
                            <span>·</span>
                            <span>来源 {item.origin}</span>
                          </>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Button size="sm" icon={Pencil} onClick={() => setEditor(editorFrom(item))}>
                        编辑
                      </Button>
                      {item.status === 'published' ? (
                        <Button
                          size="sm"
                          variant="danger"
                          icon={XCircle}
                          onClick={() => setReview({ id: item.id, title: item.title, status: 'rejected', note: '' })}
                        >
                          下架
                        </Button>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="primary"
                            icon={CheckCircle2}
                            onClick={() => setReview({ id: item.id, title: item.title, status: 'published', note: '' })}
                          >
                            采纳并发布
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            icon={XCircle}
                            onClick={() => setReview({ id: item.id, title: item.title, status: 'rejected', note: '' })}
                          >
                            驳回
                          </Button>
                        </>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={folded ? ChevronDown : ChevronUp}
                        onClick={() => setCollapsed((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
                      >
                        {folded ? '展开' : '收起'}
                      </Button>
                    </div>
                  </div>

                  {!folded ? (
                    <>
                      {item.steps?.length ? (
                        <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-[13px] leading-6 text-ink">
                          {item.steps.map((step, index) => (
                            <li key={`${item.id}-step-${index}`}>{step}</li>
                          ))}
                        </ol>
                      ) : (
                        <p className="mt-2 text-[12.5px] text-muted">没有填写步骤。</p>
                      )}
                      {item.verify ? (
                        <p className="mt-1.5 text-[12.5px] leading-5 text-muted">
                          <span className="font-medium text-ink">验证：</span>
                          {item.verify}
                        </p>
                      ) : null}
                      {item.reviewNote ? (
                        <p className="mt-1 text-[12px] text-muted">复核备注：{item.reviewNote}</p>
                      ) : null}
                    </>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyState
            icon={BookOpen}
            title="还没有方案"
            description="可以在「问题库」里逐条生成，也可以点右上角「批量生成」，或把离线基线导入方案库。"
          />
        )}
      </Card>

      {/* 编辑 / 新建 */}
      <Modal
        open={Boolean(editor)}
        onClose={() => setEditor(null)}
        title={editor?.mode === 'edit' ? '编辑方案' : '新建方案'}
        description="检测项与步骤均为「一行一条」，保存后立即生效；发布状态需另外复核。"
        wide
        footer={
          <>
            <Button onClick={() => setEditor(null)}>取消</Button>
            <Button variant="primary" loading={saving} onClick={() => void submitEditor()}>
              保存
            </Button>
          </>
        }
      >
        {editor ? (
          <>
            <Field label="方案名称">
              <Input
                value={editor.title}
                maxLength={80}
                placeholder="例如：投影仪没有声音时的处置步骤"
                onChange={(event) => setEditor({ ...editor, title: event.target.value })}
              />
            </Field>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="类型 kind" hint="audio / check / video / preflight，留空表示通用">
                <Input
                  value={editor.kind}
                  maxLength={20}
                  list="cf-solution-kinds"
                  placeholder="通用"
                  onChange={(event) => setEditor({ ...editor, kind: event.target.value })}
                />
              </Field>
              <Field label="置信度 confidence" hint="high / medium / low">
                <Input
                  value={editor.confidence}
                  maxLength={12}
                  list="cf-solution-confidence"
                  placeholder="medium"
                  onChange={(event) => setEditor({ ...editor, confidence: event.target.value })}
                />
              </Field>
            </div>
            <datalist id="cf-solution-kinds">
              <option value="audio" />
              <option value="check" />
              <option value="video" />
              <option value="preflight" />
            </datalist>
            <datalist id="cf-solution-confidence">
              <option value="high" />
              <option value="medium" />
              <option value="low" />
            </datalist>
            <Field
              label="生效检测项 appliesTo"
              hint="一行一条，常用：audio_devices / powerpoint_volume / display / videofile；留空 = 所有检测都提示"
            >
              <Textarea
                value={editor.appliesTo}
                rows={3}
                placeholder={'audio_devices\npowerpoint_volume'}
                onChange={(event) => setEditor({ ...editor, appliesTo: event.target.value })}
              />
            </Field>
            <Field label="步骤 steps" hint="一行一条，建议 3～6 步，动词开头">
              <Textarea
                value={editor.steps}
                rows={6}
                placeholder={'检查投影仪音频线是否插在 Audio Out\n在 Windows 声音设置里重新选择输出设备'}
                onChange={(event) => setEditor({ ...editor, steps: event.target.value })}
              />
            </Field>
            <Field label="验证方法 verify" hint="怎么确认问题已经解决">
              <Textarea
                value={editor.verify}
                rows={2}
                placeholder="播放测试音频，能听到提示音即修复"
                onChange={(event) => setEditor({ ...editor, verify: event.target.value })}
              />
            </Field>
          </>
        ) : null}
      </Modal>

      {/* 复核 */}
      <Modal
        open={Boolean(review)}
        onClose={() => setReview(null)}
        title={review?.status === 'published' ? '采纳并发布' : '下架 / 驳回'}
        description={
          review?.status === 'published'
            ? '发布后规则包版本会立即递增，客户端下次拉取即可看到。'
            : '驳回/下架后客户端将不再收到这条方案。'
        }
        footer={
          <>
            <Button onClick={() => setReview(null)}>取消</Button>
            <Button
              variant={review?.status === 'published' ? 'primary' : 'danger'}
              loading={reviewSaving}
              onClick={() => void submitReview()}
            >
              确认
            </Button>
          </>
        }
      >
        {review ? (
          <>
            <p className="flex items-start gap-2 rounded-xl border border-line bg-brand-50/70 px-3 py-2 text-[13px] text-ink">
              <ListChecks size={15} className="mt-0.5 shrink-0 text-brand" />
              <span className="break-words">{review.title}</span>
              <Tag tone={review.status === 'published' ? 'good' : 'bad'} className="ml-auto shrink-0">
                {review.status === 'published' ? '发布' : '驳回'}
              </Tag>
            </p>
            <Field label="复核备注（可选，会记录在方案上）">
              <Textarea
                rows={2}
                maxLength={200}
                value={review.note}
                placeholder="例如：步骤已核对，与线下流程一致"
                onChange={(event) => setReview({ ...review, note: event.target.value })}
              />
            </Field>
          </>
        ) : null}
      </Modal>
    </div>
  );
}
