import { db, persist, type IssueRecord, type RuleItemRecord } from './store';
import { fingerprint, newId, now } from './util';

export type IssueInput = {
  title?: unknown;
  checkKey?: unknown;
  evidence?: unknown;
  action?: unknown;
};

/**
 * 问题库：同一 (kind, 归一化标题) 归为一条，累计次数并保留最近 5 条现场片段。
 * 返回入库记录；标题为空则不入库（返回 null）。
 */
export function recordIssue(kind: string, issue: IssueInput): IssueRecord | null {
  const title = String(issue.title || '').trim();
  if (!title) return null;

  const key = fingerprint(kind, title);
  const stamp = now();
  let item = db.issues[key];

  if (!item) {
    item = db.issues[key] = {
      id: newId('i'),
      fingerprint: key,
      kind,
      title: title.slice(0, 60),
      checkKey: String(issue.checkKey || '').slice(0, 40),
      count: 0,
      firstSeen: stamp,
      lastSeen: stamp,
      status: 'new',
      samples: [],
    };
  }

  item.count += 1;
  item.lastSeen = stamp;
  if (item.checkKey === '' && issue.checkKey) item.checkKey = String(issue.checkKey).slice(0, 40);
  item.samples.push({
    evidence: String(issue.evidence || '').slice(0, 300),
    action: String(issue.action || '').slice(0, 300),
    at: stamp,
  });
  if (item.samples.length > 5) item.samples = item.samples.slice(-5);

  persist();
  return item;
}

/**
 * 生成规则包：只收 status=published 的方案。
 * appliesTo 是「最相关的检测项 key」列表（不是 kind/checkKey 的位置数组）——
 * 早期版本把 appliesTo[0] 当 kind，导致客户端 kind 过滤永远匹配不上，这里保持修正后的口径。
 */
export function publishRules(): { version: number; publishedAt: string; items: RuleItemRecord[] } {
  const items: RuleItemRecord[] = Object.values(db.solutions)
    .filter((item) => item.status === 'published')
    .map((item) => ({
      id: item.id,
      match: { kind: item.kind || '', checkKeys: (item.appliesTo || []).slice(0, 3) },
      title: item.title,
      steps: item.steps,
      verify: item.verify,
    }));

  db.rules = { version: (db.rules.version || 0) + 1, publishedAt: now(), items };
  persist();
  return db.rules;
}
