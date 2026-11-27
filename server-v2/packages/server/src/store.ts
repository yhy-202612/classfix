import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import type { Settings } from '@classfix/shared';
import { CONFIG_FILE, DATA_DIR, DB_FILE } from './env';

/* ---------------------------------------------------------------- 文件读写 */

export function readJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
  } catch {
    return fallback;
  }
}

/** 原子写：先写临时文件再 rename，避免进程被杀时留下半个 JSON。 */
export function writeJsonAtomic(file: string, value: unknown): void {
  const temp = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(value, null, 1), 'utf8');
  fs.renameSync(temp, file);
}

/* ---------------------------------------------------------------- 配置 */

export type AppConfig = {
  secret?: string;
  adminPassword?: string;
  aiKey?: string;
  aiBase?: string;
  aiModel?: string;
  aiTimeout?: number;
  otpKey?: string;
  otpSecret?: string;
  otpBase?: string;
};

fs.mkdirSync(DATA_DIR, { recursive: true });

export const config: AppConfig = readJson<AppConfig>(CONFIG_FILE, {});

if (!config.secret) {
  config.secret = crypto.randomBytes(32).toString('hex');
  try {
    writeJsonAtomic(CONFIG_FILE, config);
  } catch (error) {
    console.error('[config] 无法保存密钥', (error as Error).message);
  }
}

/* ---------------------------------------------------------------- 数据 */

export type UserRecord = {
  id: string;
  phone: string;
  maskedPhone?: string;
  points: number;
  dailyGranted?: string;
  createdAt: string;
  lastLoginAt?: string;
  banned?: boolean;
};

export type LedgerEntry = {
  uid: string;
  delta: number;
  reason: string;
  at: string;
  meta?: unknown;
};

export type DiagnosisRecord = {
  id: string;
  uid: string;
  kind: string;
  cost: number;
  at: string;
  advice: string;
  steps: string[];
  verify: string;
  issueIds: string[];
};

export type IssueRecord = {
  id: string;
  fingerprint: string;
  kind: string;
  title: string;
  checkKey: string;
  count: number;
  firstSeen: string;
  lastSeen: string;
  status: 'new' | 'reviewed' | 'adopted';
  samples: Array<{ evidence: string; action: string; at: string }>;
};

export type SolutionRecord = {
  id: string;
  kind?: string;
  title: string;
  appliesTo: string[];
  steps: string[];
  verify: string;
  confidence?: string;
  status: 'draft' | 'published' | 'rejected' | 'baseline';
  source?: string;
  origin?: string;
  fromIssues?: string[];
  createdAt?: string;
  publishedAt?: string;
  reviewedAt?: string;
  reviewNote?: string;
};

export type CatalogRecord = { key: string; title: string; action: string; origin: string; at: string };

export type RuleItemRecord = {
  id: string;
  match: { kind: string; checkKeys: string[] };
  title: string;
  steps: string[];
  verify: string;
};

export type Db = {
  users: Record<string, UserRecord>;
  sessions: Record<string, unknown>;
  ledger: LedgerEntry[];
  diagnoses: DiagnosisRecord[];
  issues: Record<string, IssueRecord>;
  solutions: Record<string, SolutionRecord>;
  catalog: CatalogRecord[];
  settings: Settings;
  rules: { version: number; publishedAt: string; items: RuleItemRecord[] };
};

export const defaultSettings = (): Settings => ({
  autoPublish: false,
  newUserPoints: 10,
  dailyGrant: 5,
  cost: { audio: 1, check: 1, video: 2, preflight: 1 },
  dailyQuotaPerApp: 300,
  maxDiagnosesPerDay: 40,
});

const defaults = (): Db => ({
  users: {},
  sessions: {},
  ledger: [],
  diagnoses: [],
  issues: {},
  solutions: {},
  // 离线流程已经覆盖的方案基线：告诉模型「这些不用再提」，也供后端人工维护。
  catalog: [],
  settings: defaultSettings(),
  rules: { version: 0, publishedAt: '', items: [] },
});

export const db: Db = Object.assign(defaults(), readJson<Partial<Db>>(DB_FILE, {}));
export const settings: Settings = Object.assign(defaultSettings(), db.settings || {});
db.settings = settings;

/* ---------------------------------------------------------------- 持久化（400ms 合并写） */

let dirty = false;
let timer: NodeJS.Timeout | null = null;

export function persist(): void {
  dirty = true;
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    if (!dirty) return;
    dirty = false;
    try {
      writeJsonAtomic(DB_FILE, db);
    } catch (error) {
      console.error('[db] 写入失败', (error as Error).message);
    }
  }, 400);
}

/** 进程退出前把未落盘的改动刷掉（旧版没有，属于纯增益，不影响响应体）。 */
export function flushSync(): void {
  if (!dirty) return;
  dirty = false;
  try {
    writeJsonAtomic(DB_FILE, db);
  } catch (error) {
    console.error('[db] 退出前写入失败', (error as Error).message);
  }
}
