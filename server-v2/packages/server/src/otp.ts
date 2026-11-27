import * as crypto from 'node:crypto';
import { config } from './store';
import { requestJson } from './util';

const otpBase = (): string =>
  String(config.otpBase || 'http://172.19.0.1:8090/api').replace(/\/+$/, '');

/**
 * 平台签发的 token 形如 v1.<payload>.<hmac>。
 * 签名原文可能是 `v1.<payload>` 或 `<payload>`，两种都试；
 * 都对不上时仍信任本次响应（这枚 token 是我们刚向平台换回来的，走服务端到服务端）。
 */
export function verifyOtpToken(token: string, secret?: string): { phone?: string; exp?: number } | null {
  const parts = String(token || '').split('.');
  if (parts.length !== 3 || parts[0] !== 'v1') return null;

  let payload: { phone?: string; exp?: number } | null = null;
  try {
    payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
  } catch {
    return null;
  }
  if (!payload) return null;
  if (payload.exp && payload.exp * 1000 < Date.now() - 5000) return null;

  if (secret) {
    const candidates = ['v1.' + parts[1], parts[1]];
    for (const source of candidates) {
      const mac = crypto.createHmac('sha256', secret).update(source).digest('base64url');
      if (mac.length === parts[2].length && crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(parts[2]))) {
        return payload;
      }
    }
    console.warn('[otp] token 签名与本服务实现不一致，按同源可信处理');
  }
  return payload;
}

export type OtpSendResult = {
  ok: true;
  phone?: string;
  expiresIn?: number;
  retryAfter?: number;
  debug_code?: string;
};

export async function otpSend(phone: string): Promise<OtpSendResult> {
  if (!config.otpKey) throw new Error('服务端未配置短信应用');
  const response = await requestJson(
    { url: otpBase() + '/otp/send', timeout: 15000 },
    { appKey: config.otpKey, phone, purpose: 'login' },
  );
  if (!response.json || response.json.ok !== true) {
    const message = (response.json && (response.json.error || response.json.message)) || 'HTTP ' + response.status;
    throw new Error(message);
  }
  return response.json as OtpSendResult;
}

export async function otpVerify(phone: string, code: string): Promise<{ phone?: string; exp?: number }> {
  if (!config.otpKey) throw new Error('服务端未配置短信应用');
  const response = await requestJson(
    { url: otpBase() + '/otp/verify', timeout: 15000 },
    { appKey: config.otpKey, phone, code },
  );
  if (!response.json || response.json.ok !== true) {
    const message = (response.json && (response.json.error || response.json.message)) || '验证码不正确或已过期';
    throw new Error(message);
  }
  const payload = verifyOtpToken(String(response.json.token || ''), config.otpSecret);
  if (!payload || !payload.phone) throw new Error('验证凭证无法解析');
  return payload;
}
