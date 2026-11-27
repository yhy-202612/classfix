export type Tone = 'brand' | 'good' | 'warn' | 'bad' | 'neutral';

/** 与旧页面一致：ISO 时间截到分钟，T 换空格 */
export function fmtDateTime(value?: string | null): string {
  if (!value) return '—';
  const text = String(value);
  const head = text.slice(0, 16).replace('T', ' ');
  return head || '—';
}

export function truncate(value: string | null | undefined, max: number): string {
  const text = String(value ?? '');
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}

export function toNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** 数字输入收集成整数，并夹到旧页面的 min/max 范围 */
export function toInt(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = Math.round(toNumber(value, fallback));
  return Math.min(max, Math.max(min, parsed));
}

/** 「一行一条」的多行输入 → 数组 */
export function splitLines(value: string | null | undefined): string[] {
  return String(value ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 生效检测项：一行一条，也容忍逗号/空格分隔 */
export function splitKeys(value: string | null | undefined): string[] {
  return String(value ?? '')
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function formatStatValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? '开' : '关';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '0';
  return String(value ?? '—');
}

/** 服务端已经打过码；这里再做一次兜底，避免未打码的号码被直接展示 */
export function maskPhone(value?: string | null): string {
  const text = String(value ?? '').trim();
  if (!text) return '—';
  if (text.includes('*')) return text;
  if (text.length < 7) return text;
  return `${text.slice(0, 3)}****${text.slice(-4)}`;
}

export function statusMeta(status?: string): { label: string; tone: Tone } {
  switch (status) {
    case 'new':
      return { label: '待处理', tone: 'warn' };
    case 'reviewed':
      return { label: '已生成方案', tone: 'brand' };
    case 'adopted':
      return { label: '已采纳', tone: 'good' };
    case 'draft':
      return { label: '草稿', tone: 'warn' };
    case 'published':
      return { label: '已发布', tone: 'good' };
    case 'rejected':
      return { label: '已驳回', tone: 'bad' };
    case 'baseline':
      return { label: '离线基线', tone: 'neutral' };
    default:
      return { label: status || '未知', tone: 'neutral' };
  }
}

export function errorMessage(error: unknown, fallback = '操作失败，请稍后重试。'): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return fallback;
}
