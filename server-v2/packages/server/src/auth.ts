import * as crypto from 'node:crypto';
import { config, db, type UserRecord } from './store';

export type TokenPayload = { uid: string; phone: string; exp: number };

/** 本站令牌：c1.<base64url(payload)>.<hmac>，30 天有效。 */
export function signToken(user: UserRecord): string {
  const payload: TokenPayload = {
    uid: user.id,
    phone: user.phone,
    exp: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 30,
  };
  const body = 'c1.' + Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
  const mac = crypto.createHmac('sha256', config.secret as string).update(body).digest('base64url');
  return body + '.' + mac;
}

export function verifyToken(token: string): TokenPayload | null {
  if (typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 3 || parts[0] !== 'c1') return null;
  const body = parts[0] + '.' + parts[1];
  const mac = crypto.createHmac('sha256', config.secret as string).update(body).digest('base64url');
  if (mac.length !== parts[2].length) return null;
  if (!crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(parts[2]))) return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as TokenPayload;
    if (!payload.exp || payload.exp * 1000 < Date.now()) return null;
    if (!db.users[payload.uid]) return null;
    return payload;
  } catch {
    return null;
  }
}

export function bearer(header: string | undefined): string {
  const value = String(header || '');
  return value.startsWith('Bearer ') ? value.slice(7).trim() : '';
}

/** 从 Authorization 头解析当前用户；未登录返回 null。 */
export function userFromAuth(header: string | undefined): UserRecord | null {
  const payload = verifyToken(bearer(header));
  return payload ? db.users[payload.uid] || null : null;
}

/**
 * 管理令牌：a1.<hmac('admin|' + 口令)>。
 * 旧版把结果缓存在模块变量里，运行期改口令不生效；这里按口令派生，天然跟随配置。
 */
export function adminToken(): string {
  return (
    'a1.' +
    crypto
      .createHmac('sha256', config.secret as string)
      .update('admin|' + String(config.adminPassword || ''))
      .digest('base64url')
  );
}

export function isAdmin(header: string | undefined): boolean {
  if (!config.adminPassword) return false;
  return bearer(header) === adminToken();
}
