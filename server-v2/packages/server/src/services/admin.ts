import type {
  AdminCatalogRes,
  AdminIssueListRes,
  AdminOverviewRes,
  AdminSettingsRes,
  AdminSolutionListRes,
  AdminUserListRes,
  BanRes,
  GenerateAllRes,
  GenerateRes,
  ImportBaselineRes,
  PublishRes,
  ReviewRes,
  SolutionSaveRes,
} from '@classfix/shared';
import { askModel, SOLUTION_PROMPT } from '../ai';
import { publishRules } from '../knowledge';
import { db, persist, settings } from '../store';
import { publicUser } from './public';
import { ApiError, newId, now, todayBJ } from '../util';

/* ---------------------------------------------------------------- 概览 */

export function overview(): AdminOverviewRes {
  const issues = Object.values(db.issues);
  const solutions = Object.values(db.solutions);
  const today = todayBJ();
  return {
    ok: true,
    stats: {
      users: Object.keys(db.users).length,
      diagnoses: db.diagnoses.length,
      diagnosesToday: db.diagnoses.filter((item) => item.at.slice(0, 10) === today).length,
      issues: issues.length,
      issuesNew: issues.filter((item) => item.status === 'new').length,
      solutions: solutions.length,
      solutionsDraft: solutions.filter((item) => item.status === 'draft').length,
      solutionsPublished: solutions.filter((item) => item.status === 'published').length,
      solutionsBaseline: solutions.filter((item) => item.status === 'baseline').length,
      rulesVersion: db.rules.version,
      catalog: (db.catalog || []).length,
      autoPublish: Boolean(settings.autoPublish),
    },
    settings,
    recent: db.diagnoses
      .slice(-10)
      .reverse()
      .map((item) => ({ id: item.id, kind: item.kind, at: item.at, advice: item.advice.slice(0, 120) })),
  };
}

/* ---------------------------------------------------------------- 离线方案基线 */

export function catalogGet(): AdminCatalogRes {
  return { ok: true, items: db.catalog || [] };
}

export function catalogSave(body: { items?: unknown }): { ok: true; count: number } {
  const incoming = Array.isArray(body.items) ? body.items : null;
  if (!incoming) throw new ApiError(400, 'items 必须是数组。');

  const cleaned: typeof db.catalog = [];
  const seen = new Set<string>();
  for (const raw of incoming.slice(0, 300) as any[]) {
    const title = String((raw && raw.title) || '')
      .trim()
      .slice(0, 60);
    if (!title) continue;
    const key = String((raw && raw.key) || '')
      .trim()
      .slice(0, 40);
    // 去重按标题：key 只是溯源标记（同一文件里的多处 Finding 可能共用一个 key），
    // 用它去重会把不同问题当成同一条丢掉。
    if (seen.has(title)) continue;
    seen.add(title);
    cleaned.push({
      key,
      title,
      action: String((raw && raw.action) || '')
        .trim()
        .slice(0, 200),
      origin: String((raw && raw.origin) || 'offline').slice(0, 20),
      at: now(),
    });
  }

  db.catalog = cleaned;
  persist();
  return { ok: true, count: cleaned.length };
}

/* ---------------------------------------------------------------- 问题库 */

export function issueList(status?: string): AdminIssueListRes {
  const items = Object.values(db.issues)
    .filter((item) => !status || item.status === status)
    .sort((a, b) => b.count - a.count)
    .slice(0, 200);
  return { ok: true, items };
}

/* ---------------------------------------------------------------- 方案库 */

export function solutionList(): AdminSolutionListRes {
  const items = Object.values(db.solutions).sort((a, b) =>
    (b.createdAt || '').localeCompare(a.createdAt || ''),
  );
  return { ok: true, items };
}

export function settingsSave(body: any): AdminSettingsRes {
  if (typeof body.autoPublish === 'boolean') settings.autoPublish = body.autoPublish;
  for (const key of ['newUserPoints', 'dailyGrant', 'maxDiagnosesPerDay'] as const) {
    if (body[key] !== undefined && Number.isFinite(Number(body[key]))) settings[key] = Number(body[key]);
  }
  if (body.cost && typeof body.cost === 'object') {
    for (const key of Object.keys(settings.cost) as Array<keyof typeof settings.cost>) {
      if (Number.isFinite(Number(body.cost[key]))) settings.cost[key] = Number(body.cost[key]);
    }
  }
  db.settings = settings;
  persist();
  return { ok: true, settings };
}

export function solutionSave(body: any): SolutionSaveRes {
  const id = String(body.id || '');
  const item = id ? db.solutions[id] : null;
  if (id && !item) throw new ApiError(404, '方案不存在。');

  const target: any = item || { id: newId('s'), createdAt: now(), status: 'draft', fromIssues: [] };

  if (body.kind !== undefined) target.kind = String(body.kind).slice(0, 20);
  if (body.title !== undefined) target.title = String(body.title).slice(0, 80);
  if (body.appliesTo !== undefined) {
    target.appliesTo = (Array.isArray(body.appliesTo) ? body.appliesTo : [])
      .slice(0, 3)
      .map((x: unknown) => String(x).slice(0, 40));
  }
  if (body.steps !== undefined) {
    target.steps = (Array.isArray(body.steps) ? body.steps : [])
      .slice(0, 10)
      .map((x: unknown) => String(x).slice(0, 240));
  }
  if (body.verify !== undefined) target.verify = String(body.verify).slice(0, 400);
  if (body.confidence !== undefined) target.confidence = String(body.confidence).slice(0, 12);

  if (!target.title) throw new ApiError(400, '方案名称不能为空。');
  if (!Array.isArray(target.appliesTo)) target.appliesTo = [];
  if (!Array.isArray(target.steps)) target.steps = [];

  db.solutions[target.id] = target;
  persist();
  return { ok: true, item: target };
}

/**
 * 把「离线已覆盖的方案」归档进方案库：status=baseline 不进规则包（客户端本来就有），
 * 只是让后台能在一处看到全部知识，AI 归纳新方案时也好对照。
 */
export function importBaseline(): ImportBaselineRes {
  const existing = new Set(Object.values(db.solutions).map((item) => String(item.title || '').trim()));
  const created: Array<{ id: string; title: string }> = [];

  for (const item of db.catalog || []) {
    const title = String(item.title || '').trim();
    if (!title || existing.has(title)) continue;
    existing.add(title);
    const id = newId('s');
    db.solutions[id] = {
      id,
      kind: '',
      title,
      appliesTo: [],
      steps: [String(item.action || '按离线流程处置。').trim()].filter(Boolean),
      verify: '按报告页复测结果确认是否恢复',
      confidence: 'high',
      status: 'baseline',
      origin: 'offline',
      createdAt: now(),
      reviewedAt: now(),
      reviewNote: '来自客户端离线流程基线',
    };
    created.push({ id, title });
  }

  persist();
  return { ok: true, created };
}

export function review(body: { id?: unknown; status?: unknown; note?: unknown }): ReviewRes {
  const item = db.solutions[String(body.id || '')];
  if (!item) throw new ApiError(404, '方案不存在。');

  const status = String(body.status || '');
  if (!['draft', 'published', 'rejected'].includes(status)) throw new ApiError(400, '状态不合法。');

  item.status = status as typeof item.status;
  item.reviewedAt = now();
  item.reviewNote = String(body.note || '').slice(0, 200);

  if (status === 'published') {
    item.publishedAt = now();
    for (const issueId of item.fromIssues || []) {
      const issue = Object.values(db.issues).find((entry) => entry.id === issueId);
      if (issue) issue.status = 'adopted';
    }
    db.rules = publishRules();
  }

  persist();
  return { ok: true, item: item as any, rulesVersion: db.rules.version };
}

function issueBriefLines(issue: (typeof db.issues)[string]): string[] {
  const lines = [
    '问题类型：' + issue.kind,
    '问题名称：' + issue.title,
    '出现次数：' + issue.count,
    '相关检测项：' + (issue.checkKey || '未标注'),
    '现场片段：',
  ];
  for (const sample of issue.samples) lines.push('- 依据：' + sample.evidence + ' / 处置：' + sample.action);
  return lines;
}

export async function generate(body: { issueId?: unknown }): Promise<GenerateRes> {
  const issue = Object.values(db.issues).find((item) => item.id === String(body.issueId || ''));
  if (!issue) throw new ApiError(404, '问题不存在。');

  let parsed: any;
  try {
    const result = await askModel(SOLUTION_PROMPT, issueBriefLines(issue).join('\n'), 900);
    parsed = result.parsed || {};
  } catch (error) {
    throw new ApiError(502, (error as Error).message);
  }

  const solution: any = {
    id: newId('s'),
    kind: issue.kind,
    title: String(parsed.title || issue.title).slice(0, 80),
    appliesTo: (Array.isArray(parsed.appliesTo) ? parsed.appliesTo : [issue.checkKey])
      .filter(Boolean)
      .slice(0, 3)
      .map((x: unknown) => String(x).slice(0, 40)),
    steps: (Array.isArray(parsed.steps) ? parsed.steps : []).slice(0, 10).map((x: unknown) => String(x).slice(0, 240)),
    verify: String(parsed.verify || '').slice(0, 400),
    confidence: String(parsed.confidence || 'medium').slice(0, 12),
    source: 'ai',
    fromIssues: [issue.id],
    createdAt: now(),
    status: settings.autoPublish ? 'published' : 'draft',
  };

  if (settings.autoPublish) {
    solution.publishedAt = now();
    solution.reviewedAt = now();
    solution.reviewNote = '自动发布（未启用人工复核）';
  }

  db.solutions[solution.id] = solution;
  issue.status = settings.autoPublish ? 'adopted' : 'reviewed';
  if (settings.autoPublish) db.rules = publishRules();
  persist();

  return { ok: true, item: solution, rulesVersion: db.rules.version };
}

export async function generateAll(): Promise<GenerateAllRes> {
  const pending = Object.values(db.issues)
    .filter((item) => item.status === 'new')
    .slice(0, 10);

  const created: string[] = [];
  const failed: Array<{ issueId: string; error: string }> = [];

  for (const issue of pending) {
    try {
      const result = await askModel(SOLUTION_PROMPT, issueBriefLines(issue).join('\n'), 900);
      const parsed = result.parsed || {};
      const solution: any = {
        id: newId('s'),
        title: String(parsed.title || issue.title).slice(0, 80),
        appliesTo: (Array.isArray(parsed.appliesTo) ? parsed.appliesTo : [issue.checkKey])
          .filter(Boolean)
          .slice(0, 3)
          .map((x: unknown) => String(x).slice(0, 40)),
        steps: (Array.isArray(parsed.steps) ? parsed.steps : [])
          .slice(0, 10)
          .map((x: unknown) => String(x).slice(0, 240)),
        verify: String(parsed.verify || '').slice(0, 400),
        confidence: String(parsed.confidence || 'medium').slice(0, 12),
        source: 'ai',
        fromIssues: [issue.id],
        createdAt: now(),
        status: settings.autoPublish ? 'published' : 'draft',
      };
      if (settings.autoPublish) solution.publishedAt = now();
      db.solutions[solution.id] = solution;
      issue.status = settings.autoPublish ? 'adopted' : 'reviewed';
      created.push(solution.id);
    } catch (error) {
      failed.push({ issueId: issue.id, error: (error as Error).message });
    }
  }

  if (settings.autoPublish && created.length) db.rules = publishRules();
  persist();
  return { ok: true, created, failed, rulesVersion: db.rules.version };
}

export function publish(): PublishRes {
  return { ok: true, rules: publishRules() };
}

/* ---------------------------------------------------------------- 用户 */

export function userList(): AdminUserListRes {
  const items = Object.values(db.users)
    .slice(0, 500)
    .map((user) =>
      Object.assign(publicUser(user), {
        lastLoginAt: user.lastLoginAt || '',
        diagnoses: db.diagnoses.filter((item) => item.uid === user.id).length,
      }),
    );
  return { ok: true, items };
}

export function banUser(body: { id?: unknown; banned?: unknown }): BanRes {
  const target = db.users[String(body.id || '')];
  if (!target) throw new ApiError(404, '用户不存在。');
  target.banned = Boolean(body.banned);
  persist();
  return { ok: true, user: publicUser(target) };
}
