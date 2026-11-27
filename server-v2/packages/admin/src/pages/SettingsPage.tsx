import type { AdminOverviewRes, AdminSettingsReq, Settings } from '@classfix/shared';
import { RotateCcw, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Checkbox, Field, Input } from '../components/Field';
import { ErrorState, LoadingBlock } from '../components/States';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, toInt } from '../lib/format';
import { useResource } from '../lib/hooks';

type FormState = {
  autoPublish: boolean;
  newUserPoints: string;
  dailyGrant: string;
  maxDiagnosesPerDay: string;
  costVideo: string;
  costAudio: string;
  costCheck: string;
  costPreflight: string;
};

function formFrom(settings: Settings): FormState {
  return {
    autoPublish: Boolean(settings.autoPublish),
    newUserPoints: String(settings.newUserPoints),
    dailyGrant: String(settings.dailyGrant),
    maxDiagnosesPerDay: String(settings.maxDiagnosesPerDay),
    costVideo: String(settings.cost.video),
    costAudio: String(settings.cost.audio),
    costCheck: String(settings.cost.check),
    costPreflight: String(settings.cost.preflight),
  };
}

export function SettingsPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const { data, loading, error, reload } = useResource<AdminOverviewRes>(
    () => adminApi.overview(),
    [refreshKey],
  );
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data?.settings) setForm(formFrom(data.settings));
  }, [data]);

  function patch(partial: Partial<FormState>) {
    setForm((prev) => (prev ? { ...prev, ...partial } : prev));
  }

  async function save() {
    if (!form) return;
    const body: AdminSettingsReq = {
      autoPublish: form.autoPublish,
      newUserPoints: toInt(form.newUserPoints, 0, 1000, 0),
      dailyGrant: toInt(form.dailyGrant, 0, 100, 0),
      maxDiagnosesPerDay: toInt(form.maxDiagnosesPerDay, 1, 500, 1),
      cost: {
        video: toInt(form.costVideo, 0, 50, 0),
        audio: toInt(form.costAudio, 0, 50, 0),
        check: toInt(form.costCheck, 0, 50, 0),
        preflight: toInt(form.costPreflight, 0, 50, 0),
      },
    };
    setSaving(true);
    try {
      await adminApi.saveSettings(body);
      toast.success('设置已保存。');
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3.5 md:space-y-4">
      <Card
        title="设置"
        subtitle="保存后立即对客户端生效（积分成本随设置一起下发）"
        actions={
          <>
            <Button
              size="sm"
              icon={RotateCcw}
              disabled={!data?.settings}
              onClick={() => data?.settings && setForm(formFrom(data.settings))}
            >
              还原
            </Button>
            <Button size="sm" variant="primary" icon={Save} loading={saving} disabled={!form} onClick={() => void save()}>
              保存设置
            </Button>
          </>
        }
      >
        {error ? (
          <ErrorState message={error} onRetry={() => void reload()} />
        ) : loading && !form ? (
          <LoadingBlock />
        ) : !form ? (
          <LoadingBlock label="正在读取设置…" />
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl border border-line bg-brand-50/70 p-3">
              <Checkbox
                checked={form.autoPublish}
                onChange={(checked) => patch({ autoPublish: checked })}
                label="关闭人工复核：AI 归纳出的方案直接发布到客户端"
              />
              <p className="mt-2 text-[12.5px] leading-5 text-muted">
                默认关闭（AI 总结 → 人工复查）。打开后新方案会自动发布，规则包版本立即递增，客户端下次拉取就能看到；人工仍可随时下架。
              </p>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="新用户积分" hint="0～1000">
                <Input
                  type="number"
                  min={0}
                  max={1000}
                  inputMode="numeric"
                  value={form.newUserPoints}
                  onChange={(event) => patch({ newUserPoints: event.target.value })}
                />
              </Field>
              <Field label="每日赠送" hint="0～100">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  inputMode="numeric"
                  value={form.dailyGrant}
                  onChange={(event) => patch({ dailyGrant: event.target.value })}
                />
              </Field>
              <Field label="每人每日诊断上限" hint="1～500">
                <Input
                  type="number"
                  min={1}
                  max={500}
                  inputMode="numeric"
                  value={form.maxDiagnosesPerDay}
                  onChange={(event) => patch({ maxDiagnosesPerDay: event.target.value })}
                />
              </Field>
            </div>

            <div>
              <h3 className="mb-2 text-[13px] font-semibold text-ink">单次诊断扣费积分</h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Field label="视频诊断" hint="0～50">
                  <Input
                    type="number"
                    min={0}
                    max={50}
                    inputMode="numeric"
                    value={form.costVideo}
                    onChange={(event) => patch({ costVideo: event.target.value })}
                  />
                </Field>
                <Field label="声音诊断" hint="0～50">
                  <Input
                    type="number"
                    min={0}
                    max={50}
                    inputMode="numeric"
                    value={form.costAudio}
                    onChange={(event) => patch({ costAudio: event.target.value })}
                  />
                </Field>
                <Field label="课前巡检" hint="0～50">
                  <Input
                    type="number"
                    min={0}
                    max={50}
                    inputMode="numeric"
                    value={form.costCheck}
                    onChange={(event) => patch({ costCheck: event.target.value })}
                  />
                </Field>
                <Field label="课前巡检（preflight）" hint="0～50，独立于巡检">
                  <Input
                    type="number"
                    min={0}
                    max={50}
                    inputMode="numeric"
                    value={form.costPreflight}
                    onChange={(event) => patch({ costPreflight: event.target.value })}
                  />
                </Field>
              </div>
            </div>

            <p className="text-[12px] leading-5 text-muted">
              规则包发布在「规则包」页；这里只保存账号与积分相关配置。
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
