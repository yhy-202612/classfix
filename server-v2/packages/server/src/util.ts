import * as crypto from 'node:crypto';
import * as http from 'node:http';
import * as https from 'node:https';

export const now = (): string => new Date().toISOString();

/** 北京时间「今天」（YYYY-MM-DD）。日配额/每日赠分都按北京日期算。 */
export const todayBJ = (): string => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);

export const maskPhone = (phone: string): string =>
  String(phone).replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');

export const sha1 = (text: string): string => crypto.createHash('sha1').update(text).digest('hex');

export const newId = (prefix: string): string => prefix + '_' + crypto.randomBytes(8).toString('hex');

export const normalizeTitle = (title: unknown): string =>
  String(title || '')
    .replace(/[\s\p{P}\p{S}]/gu, '')
    .slice(0, 40);

export const fingerprint = (kind: string, title: string): string =>
  sha1('cf1|' + kind + '|' + normalizeTitle(title)).slice(0, 16);

/** 业务错误：http 状态 + 原样文案（客户端依赖这些中文提示）。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type UpstreamResponse = { status: number; json: any | null; text: string };

export type UpstreamOptions = {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  timeout?: number;
};

/**
 * 极简 JSON 请求（不引 axios/got：旧实现如此，行为已被线上验证）。
 * 超过 1MB 的响应体直接中断，避免上游异常把内存打满。
 */
export function requestJson(options: UpstreamOptions, payload?: unknown): Promise<UpstreamResponse> {
  return new Promise((resolve, reject) => {
    const url = new URL(options.url);
    const transport = url.protocol === 'http:' ? http : https;
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload), 'utf8');

    const request = transport.request(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === 'http:' ? 80 : 443),
        path: url.pathname + url.search,
        method: options.method || (body ? 'POST' : 'GET'),
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers || {}),
          ...(body ? { 'Content-Length': body.length } : {}),
        },
        timeout: options.timeout || 30000,
      },
      (response) => {
        const parts: Buffer[] = [];
        let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > 1024 * 1024) {
            request.destroy();
            return;
          }
          parts.push(chunk);
        });
        response.on('end', () => {
          const text = Buffer.concat(parts).toString('utf8');
          let parsed: any = null;
          try {
            parsed = JSON.parse(text);
          } catch {
            /* 非 JSON 响应 */
          }
          resolve({ status: response.statusCode || 0, json: parsed, text });
        });
      },
    );

    request.on('timeout', () => request.destroy(new Error('upstream timeout')));
    request.on('error', reject);
    if (body) request.write(body);
    request.end();
  });
}

/** 模型输出容错解析：去掉 ```json 围栏，再从首尾大括号截取。 */
export function parseJsonLoose(text: string): any | null {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const cleaned = raw.replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  try {
    return JSON.parse(cleaned);
  } catch {
    /* 继续尝试截取 */
  }
  const start = cleaned.indexOf('{');
  const end = cleaned.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(cleaned.slice(start, end + 1));
    } catch {
      return null;
    }
  }
  return null;
}
