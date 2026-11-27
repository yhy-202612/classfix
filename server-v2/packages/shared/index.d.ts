/**
 * ClassFix 云端 API 契约（前后端共用）。
 * 这里的字段顺序/命名与旧版 server.js 的响应体保持一致 —— 客户端 1.6.x 依赖这些结构。
 */

export type Ok<T> = T & { ok: true };
export type Fail = { ok: false; error: string } & Record<string, unknown>;

/* ---------------------------------------------------------------- 用户 / 设置 */

export type PublicUser = {
  id: string;
  /** 已经是打码手机号（138****1234） */
  phone: string;
  points: number;
  dailyGranted: string;
  createdAt: string;
  banned: boolean;
};

export type CostTable = {
  audio: number;
  check: number;
  video: number;
  preflight: number;
};

export type Settings = {
  autoPublish: boolean;
  newUserPoints: number;
  dailyGrant: number;
  cost: CostTable;
  dailyQuotaPerApp: number;
  maxDiagnosesPerDay: number;
};

export type HealthRes = Ok<{
  service: 'classfix-api';
  version: number;
  users: number;
  rules: number;
  hasAi: boolean;
  hasOtp: boolean;
  autoPublish: boolean;
  time: string;
}>;

/* ---------------------------------------------------------------- 登录 / 我的 */

export type AuthCodeReq = { phone: string };
export type AuthCodeRes = Ok<{
  masked: string;
  expiresIn: number;
  retryAfter: number;
  debugCode: string;
}>;

export type AuthLoginReq = { phone: string; code: string };
export type AuthLoginRes = Ok<{
  created: boolean;
  token: string;
  user: PublicUser;
  settings: Pick<Settings, 'cost' | 'dailyGrant' | 'newUserPoints'>;
}>;

export type MeRes = Ok<{
  user: PublicUser;
  grantedToday: number;
  cost: CostTable;
  dailyGrant: number;
  diagnosesToday: number;
  limitPerDay: number;
}>;

/* ---------------------------------------------------------------- 诊断 / 上报 */

export type DiagnoseKind = 'audio' | 'check' | 'video' | 'preflight' | string;

export type DiagnoseReq = {
  kind: DiagnoseKind;
  /** 客户端采集的检测数据（结构由客户端定义，服务端原样转发给模型） */
  evidence: Record<string, unknown>;
};

export type IssueBrief = { id: string; title: string; count: number; status?: string };

export type DiagnoseRes = Ok<{
  advice: string;
  steps: string[];
  verify: string;
  issues: IssueBrief[];
  points: number;
  cost: number;
  diagnosisId: string;
}>;

export type ReportReq = {
  kind: DiagnoseKind;
  items: Array<{ title: string; checkKey?: string; evidence?: string; action?: string }>;
};
export type ReportRes = Ok<{ stored: Array<{ id: string; title: string; count: number }> }>;

/* ---------------------------------------------------------------- 问题 / 方案 / 规则 */

export type IssueStatus = 'new' | 'reviewed' | 'adopted';

export type Issue = {
  id: string;
  fingerprint: string;
  kind: string;
  title: string;
  checkKey: string;
  count: number;
  firstSeen: string;
  lastSeen: string;
  status: IssueStatus;
  samples: Array<{ evidence: string; action: string; at: string }>;
};

export type SolutionStatus = 'draft' | 'published' | 'rejected' | 'baseline';

export type Solution = {
  id: string;
  kind?: string;
  title: string;
  appliesTo: string[];
  steps: string[];
  verify: string;
  confidence?: string;
  source?: string;
  status: SolutionStatus;
  origin?: string;
  fromIssues?: string[];
  createdAt?: string;
  publishedAt?: string;
  reviewedAt?: string;
  reviewNote?: string;
};

export type RuleItem = {
  id: string;
  match: { kind: string; checkKeys: string[] };
  title: string;
  steps: string[];
  verify: string;
};

export type Rules = {
  version: number;
  publishedAt: string;
  items: RuleItem[];
};
export type RulesRes = Ok<Rules>;

export type SolutionsRes = Ok<{ version: number; items: Solution[] }>;

export type CatalogItem = {
  key: string;
  title: string;
  action: string;
  origin: string;
  at: string;
};

/* ---------------------------------------------------------------- 管理端 */

export type AdminLoginReq = { password: string };
export type AdminLoginRes = Ok<{ token: string }>;

export type AdminStats = {
  users: number;
  diagnoses: number;
  diagnosesToday: number;
  issues: number;
  issuesNew: number;
  solutions: number;
  solutionsDraft: number;
  solutionsPublished: number;
  solutionsBaseline: number;
  rulesVersion: number;
  catalog: number;
  autoPublish: boolean;
};

export type AdminOverviewRes = Ok<{
  stats: AdminStats;
  settings: Settings;
  recent: Array<{ id: string; kind: string; at: string; advice: string }>;
}>;

export type AdminIssueListRes = Ok<{ items: Issue[] }>;
export type AdminSolutionListRes = Ok<{ items: Solution[] }>;
export type AdminCatalogRes = Ok<{ items: CatalogItem[] }>;

export type AdminUser = PublicUser & { lastLoginAt: string; diagnoses: number };
export type AdminUserListRes = Ok<{ items: AdminUser[] }>;

export type AdminSettingsReq = Partial<Pick<Settings, 'autoPublish' | 'newUserPoints' | 'dailyGrant' | 'maxDiagnosesPerDay'>> & {
  cost?: Partial<CostTable>;
};
export type AdminSettingsRes = Ok<{ settings: Settings }>;

export type SolutionSaveReq = Partial<Pick<Solution, 'kind' | 'title' | 'appliesTo' | 'steps' | 'verify' | 'confidence'>> & { id?: string };
export type SolutionSaveRes = Ok<{ item: Solution }>;

export type ReviewReq = { id: string; status: 'draft' | 'published' | 'rejected'; note?: string };
export type ReviewRes = Ok<{ item: Solution; rulesVersion: number }>;

export type GenerateReq = { issueId: string };
export type GenerateRes = Ok<{ item: Solution; rulesVersion: number }>;
export type GenerateAllRes = Ok<{ created: string[]; failed: Array<{ issueId: string; error: string }>; rulesVersion: number }>;

export type PublishRes = Ok<{ rules: Rules }>;
export type ImportBaselineRes = Ok<{ created: Array<{ id: string; title: string }> }>;
export type BanReq = { id: string; banned: boolean };
export type BanRes = Ok<{ user: PublicUser }>;
