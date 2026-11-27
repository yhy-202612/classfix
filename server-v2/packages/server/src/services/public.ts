import type {
  AuthCodeRes,
  AuthLoginRes,
  DiagnoseRes,
  HealthRes,
  MeRes,
  PublicUser,
  ReportRes,
  RulesRes,
  SolutionsRes,
} from '@classfix/shared';
import { config, db, persist, settings, type UserRecord } from '../store';
import { diagnosePrompt, askModel } from '../ai';
import { otpSend, otpVerify } from '../otp';
import { diagnosisCountToday, grantDaily, refund, spend } from '../points';
import { recordIssue, publishRules } from '../knowledge';
import { signToken } from '../auth';
import { ApiError, maskPhone, newId, now, todayBJ } from '../util';

export function publicUser(user: UserRecord): PublicUser {
  return {
    id: user.id,
    phone: user.maskedPhone || maskPhone(user.phone),
    points: user.points || 0,
    dailyGranted: user.dailyGranted || '',
    createdAt: user.createdAt,
    banned: Boolean(user.banned),
  };
}

const costOf = (kind: string): number => Number(settings.cost[kind as keyof typeof settings.cost] || settings.cost.audio || 1);

const PHONE_RE = /^1[3-9]\d{9}$/;

/* ---------------------------------------------------------------- 健康 */

export function health(): HealthRes {
  return {
    ok: true,
    service: 'classfix-api',
    version: 1,
    users: Object.keys(db.users).length,
    rules: db.rules.version,
    hasAi: Boolean(config.aiKey),
    hasOtp: Boolean(config.otpKey),
    autoPublish: Boolean(settings.autoPublish),
    time: now(),
  };
}

/* ---------------------------------------------------------------- 规则包（免登录） */

export function rules(): { etag: string; body: RulesRes } {
  return {
    etag: 'W/"cf-rules-' + db.rules.version + '"',
    body: { ok: true, ...db.rules },
  };
}

/* ---------------------------------------------------------------- 验证码 */

export async function sendCode(body: { phone?: unknown }): Promise<AuthCodeRes> {
  const phone = String(body.phone || '').trim();
  if (!PHONE_RE.test(phone)) throw new ApiError(400, '请输入 11 位手机号。');
  try {
    const result = await otpSend(phone);
    return {
      ok: true,
      masked: result.phone || maskPhone(phone),
      expiresIn: result.expiresIn || 300,
      retryAfter: result.retryAfter || 60,
      debugCode: result.debug_code || '',
    };
  } catch (error) {
    throw new ApiError(502, '验证码发送失败：' + (error as Error).message);
  }
}

/* ---------------------------------------------------------------- 验证码登录 / 注册 */

export async function login(body: { phone?: unknown; code?: unknown }): Promise<AuthLoginRes> {
  const phone = String(body.phone || '').trim();
  const code = String(body.code || '').trim();
  if (!PHONE_RE.test(phone)) throw new ApiError(400, '请输入 11 位手机号。');
  if (!/^\d{4,8}$/.test(code)) throw new ApiError(400, '请输入收到的验证码。');

  let payload: { phone?: string };
  try {
    payload = await otpVerify(phone, code);
  } catch (error) {
    throw new ApiError(401, (error as Error).message);
  }

  const realPhone = String(payload.phone || phone);
  let user = Object.values(db.users).find((item) => item.phone === realPhone);
  let created = false;

  if (!user) {
    user = {
      id: newId('u'),
      phone: realPhone,
      maskedPhone: maskPhone(realPhone),
      points: Number(settings.newUserPoints) || 0,
      // 注册当天已给新用户积分，不再叠加当日赠分，避免「新用户 10 分」变成 15 分让人困惑。
      dailyGranted: todayBJ(),
      createdAt: now(),
      lastLoginAt: now(),
      banned: false,
    };
    db.users[user.id] = user;
    db.ledger.push({ uid: user.id, delta: user.points, reason: 'register', at: now() });
    created = true;
  }

  if (user.banned) throw new ApiError(403, '该账号已被停用。');

  user.lastLoginAt = now();
  grantDaily(user);
  persist();

  return {
    ok: true,
    created,
    token: signToken(user),
    user: publicUser(user),
    settings: { cost: settings.cost, dailyGrant: settings.dailyGrant, newUserPoints: settings.newUserPoints },
  };
}

/* ---------------------------------------------------------------- 我的信息 */

export function me(user: UserRecord | null): MeRes {
  if (!user || !db.users[user.id]) throw new ApiError(401, '请先登录。');
  const granted = grantDaily(user);
  return {
    ok: true,
    user: publicUser(user),
    grantedToday: granted,
    cost: settings.cost,
    dailyGrant: settings.dailyGrant,
    diagnosesToday: diagnosisCountToday(user),
    limitPerDay: settings.maxDiagnosesPerDay,
  };
}

/* ---------------------------------------------------------------- AI 诊断 */

export async function diagnose(user: UserRecord | null, body: any): Promise<DiagnoseRes> {
  if (!user) throw new ApiError(401, '请先登录，或在客户端填写自己的 API 密钥。');

  const kind = String(body.kind || 'audio');
  const evidence = body.evidence && typeof body.evidence === 'object' ? body.evidence : null;
  if (!evidence) throw new ApiError(400, '缺少检测数据。');

  if (diagnosisCountToday(user) >= settings.maxDiagnosesPerDay) {
    throw new ApiError(429, '今天已经用完 ' + settings.maxDiagnosesPerDay + ' 次云端诊断，明天再试。');
  }

  const cost = costOf(kind);
  if (!spend(user, cost, 'diagnose', { kind })) {
    throw new ApiError(402, '积分不足（本次需要 ' + cost + ' 分，当前 ' + (user.points || 0) + ' 分）。', {
      points: user.points || 0,
      cost,
    });
  }

  let result: { text: string; parsed: any | null };
  try {
    result = await askModel(diagnosePrompt(), JSON.stringify({ kind, evidence }), 1600);
  } catch (error) {
    refund(user, cost);
    throw new ApiError(502, (error as Error).message + '（已退回 ' + cost + ' 积分）');
  }

  const reply = result.parsed || {};
  const issues = Array.isArray(reply.issues) ? reply.issues.slice(0, 8) : [];
  const stored: Array<{ id: string; title: string; count: number; status: string }> = [];
  for (const issue of issues) {
    const item = recordIssue(kind, issue);
    if (item) stored.push({ id: item.id, title: item.title, count: item.count, status: item.status });
  }

  const record = {
    id: newId('d'),
    uid: user.id,
    kind,
    cost,
    at: now(),
    advice: String(reply.advice || result.text).slice(0, 4000),
    steps: Array.isArray(reply.steps) ? reply.steps.slice(0, 8).map((x: unknown) => String(x).slice(0, 200)) : [],
    verify: String(reply.verify || '').slice(0, 400),
    issueIds: stored.map((item) => item.id),
  };
  db.diagnoses.push(record);
  if (db.diagnoses.length > 4000) db.diagnoses = db.diagnoses.slice(-3000);
  persist();

  return {
    ok: true,
    advice: record.advice,
    steps: record.steps,
    verify: record.verify,
    issues: stored,
    points: user.points,
    cost,
    diagnosisId: record.id,
  };
}

/* ---------------------------------------------------------------- 客户端上报 */

export function report(user: UserRecord | null, body: any): ReportRes {
  if (!user) throw new ApiError(401, '请先登录。');
  const kind = String(body.kind || 'audio');
  const items = Array.isArray(body.items) ? body.items.slice(0, 20) : [];
  const stored: Array<{ id: string; title: string; count: number }> = [];
  for (const item of items) {
    const record = recordIssue(kind, item);
    if (record) stored.push({ id: record.id, title: record.title, count: record.count });
  }
  return { ok: true, stored };
}

/* ---------------------------------------------------------------- 已发布方案（客户端拉） */

export function publishedSolutions(user: UserRecord | null): SolutionsRes {
  if (!user) throw new ApiError(401, '请先登录。');
  return {
    ok: true,
    version: db.rules.version,
    items: Object.values(db.solutions).filter((item) => item.status === 'published') as any,
  };
}

/** 管理端点「发布规则包」时也走这里，保证版本号只增一次。 */
export { publishRules };
