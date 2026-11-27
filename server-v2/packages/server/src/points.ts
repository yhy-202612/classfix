import { db, persist, settings, type UserRecord } from './store';
import { now, todayBJ } from './util';

const LEDGER_MAX = 5000;
const LEDGER_KEEP = 4000;

function trimLedger(): void {
  if (db.ledger.length > LEDGER_MAX) db.ledger = db.ledger.slice(-LEDGER_KEEP);
}

/** 每日赠分：同一北京日期只发一次，返回本次实发数量（0 表示今天已发过）。 */
export function grantDaily(user: UserRecord): number {
  const today = todayBJ();
  if (user.dailyGranted === today) return 0;
  user.dailyGranted = today;
  const amount = Number(settings.dailyGrant) || 0;
  if (amount > 0) {
    user.points = (user.points || 0) + amount;
    db.ledger.push({ uid: user.id, delta: amount, reason: 'daily', at: now() });
    trimLedger();
  }
  persist();
  return amount;
}

/** 扣分：积分不足返回 false（调用方据此返回 402 与原文案）。 */
export function spend(user: UserRecord, amount: number, reason: string, meta?: unknown): boolean {
  if ((user.points || 0) < amount) return false;
  user.points -= amount;
  db.ledger.push({ uid: user.id, delta: -amount, reason, at: now(), meta: meta || null });
  trimLedger();
  persist();
  return true;
}

/** 退款（AI 调用失败时回滚）。 */
export function refund(user: UserRecord, amount: number): void {
  user.points = (user.points || 0) + amount;
  db.ledger.push({ uid: user.id, delta: amount, reason: 'refund', at: now() });
  trimLedger();
  persist();
}

export function diagnosisCountToday(user: UserRecord): number {
  const today = todayBJ();
  return db.diagnoses.filter((item) => item.uid === user.id && item.at.slice(0, 10) === today).length;
}
