/**
 * 契约测试：新实现（TS/Fastify）与旧实现（零依赖 server.js）对同一请求必须给出
 * 相同状态码、相同字段集合、相同错误文案 —— 这是「改造不改变行为」的硬证据。
 *
 * 运行：pnpm --filter @classfix/server test
 * 旧实现路径可用 OLD_SERVER 覆盖。
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';

const OLD_SERVER = process.env.OLD_SERVER || '/sandbox/workspace/classfix-api/server.js';
const NEW_SERVER = path.join(import.meta.dirname, '..', 'dist', 'index.js');
const OLD_PORT = 19101;
const NEW_PORT = 19102;

const children = [];

function waitPort(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 800 }, (res) => {
        res.resume();
        resolve();
      });
      req.on('error', () => {
        if (Date.now() > deadline) reject(new Error('port not ready: ' + port));
        else setTimeout(tick, 150);
      });
      req.on('timeout', () => req.destroy());
    };
    tick();
  });
}

function start(entry, port, tag) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cf-' + tag + '-'));
  const child = spawn(process.execPath, [entry], {
    env: { ...process.env, PORT: String(port), HOST: '127.0.0.1', CLASSFIX_API_DATA: dataDir },
    stdio: 'ignore',
  });
  children.push(child);
  return waitPort(port);
}

function request(port, method, urlPath, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        method,
        path: urlPath,
        headers: {
          ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {}),
          ...headers,
        },
        timeout: 5000,
      },
      (res) => {
        const parts = [];
        res.on('data', (c) => parts.push(c));
        res.on('end', () => {
          const text = Buffer.concat(parts).toString('utf8');
          let json = null;
          try {
            json = JSON.parse(text);
          } catch {
            /* 非 JSON */
          }
          resolve({ status: res.statusCode, text, json, headers: res.headers });
        });
      },
    );
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
    if (payload) req.write(payload);
    req.end();
  });
}

before(async () => {
  if (!fs.existsSync(OLD_SERVER)) throw new Error('找不到旧实现: ' + OLD_SERVER + '（可用 OLD_SERVER 指定）');
  if (!fs.existsSync(NEW_SERVER)) throw new Error('找不到新实现 dist/index.js，请先 pnpm build');
  await Promise.all([start(OLD_SERVER, OLD_PORT, 'old'), start(NEW_SERVER, NEW_PORT, 'new')]);
});

after(() => {
  for (const child of children) child.kill('SIGTERM');
});

/** 比对：状态码 + 顶层字段集合 + error 文案 */
async function compare(method, urlPath, body) {
  const [oldRes, newRes] = await Promise.all([
    request(OLD_PORT, method, urlPath, body),
    request(NEW_PORT, method, urlPath, body),
  ]);
  assert.equal(newRes.status, oldRes.status, `${method} ${urlPath} 状态码不一致`);
  if (oldRes.json && newRes.json) {
    assert.deepEqual(
      Object.keys(newRes.json).sort(),
      Object.keys(oldRes.json).sort(),
      `${method} ${urlPath} 顶层字段集合不一致`,
    );
    if (oldRes.json.ok === false) {
      assert.equal(newRes.json.error, oldRes.json.error, `${method} ${urlPath} 错误文案不一致`);
    }
  }
  return { oldRes, newRes };
}

test('GET /api/health 字段与状态一致', async () => {
  const { newRes } = await compare('GET', '/api/health');
  assert.equal(newRes.json.service, 'classfix-api');
  assert.equal(newRes.json.version, 1);
});

test('GET / 也是健康检查', async () => {
  await compare('GET', '/');
});

test('GET /api/rules 结构一致（带 ETag）', async () => {
  const { oldRes, newRes } = await compare('GET', '/api/rules');
  assert.equal(newRes.json.version, oldRes.json.version);
  assert.ok(newRes.headers.etag, '新实现必须下发 ETag（客户端靠它做 304）');
});

test('GET /api/rules 命中 If-None-Match 返回 304', async () => {
  const first = await request(NEW_PORT, 'GET', '/api/rules');
  const second = await request(NEW_PORT, 'GET', '/api/rules', undefined, { 'If-None-Match': first.headers.etag });
  assert.equal(second.status, 304);
});

test('POST /api/auth/code 手机号非法时文案一致', async () => {
  const { newRes } = await compare('POST', '/api/auth/code', { phone: '123' });
  assert.equal(newRes.json.error, '请输入 11 位手机号。');
});

test('POST /api/auth/login 验证码格式非法时文案一致', async () => {
  await compare('POST', '/api/auth/login', { phone: '13800138000', code: 'x' });
});

test('GET /api/me 未登录 401 文案一致', async () => {
  const { newRes } = await compare('GET', '/api/me');
  assert.equal(newRes.json.error, '请先登录。');
});

test('POST /api/diagnose 未登录 401 文案一致', async () => {
  await compare('POST', '/api/diagnose', { kind: 'audio' });
});

test('POST /api/report 未登录 401 文案一致', async () => {
  await compare('POST', '/api/report', {});
});

test('GET /api/solutions 未登录 401 文案一致', async () => {
  await compare('GET', '/api/solutions');
});

test('未知路径 404 文案一致', async () => {
  const { newRes } = await compare('GET', '/nope');
  assert.equal(newRes.json.error, '没有这个接口。');
});

test('未配置管理口令时 POST /api/admin/login 返回 503', async () => {
  await compare('POST', '/api/admin/login', { password: 'x' });
});

test('管理接口未带令牌 401 文案一致', async () => {
  const { newRes } = await compare('GET', '/api/admin/overview');
  assert.equal(newRes.json.error, '需要管理权限。');
});

test('CORS 预检返回 204 且带允许头', async () => {
  const res = await request(NEW_PORT, 'OPTIONS', '/api/health', undefined, {
    Origin: 'http://localhost:5173',
    'Access-Control-Request-Method': 'GET',
  });
  assert.equal(res.status, 204);
  assert.equal(res.headers['access-control-allow-origin'], 'http://localhost:5173');
});

test('响应带 Cache-Control: no-store', async () => {
  const res = await request(NEW_PORT, 'GET', '/api/health');
  assert.equal(res.headers['cache-control'], 'no-store');
});
