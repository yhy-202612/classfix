import * as fs from 'node:fs';
import * as path from 'node:path';
import cors from '@fastify/cors';
import fastifyStatic from '@fastify/static';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import { isAdmin, userFromAuth } from './auth';
import { ADMIN_DIST, BASE_PATH } from './env';
import { flushSync } from './store';
import { ApiError } from './util';
import * as admin from './services/admin';
import * as pub from './services/public';

declare module 'fastify' {
  interface FastifyRequest {
    /** 去掉 BASE_PATH 后的路径，用于路由判断 */
    routePath: string;
  }
}

const ok = <T>(reply: FastifyReply, body: T, status = 200) => reply.code(status).send(body);

export function createApp(): FastifyInstance {
  const app = Fastify({
    logger: false,
    bodyLimit: 1024 * 1024,
    // 站点可能挂在 /classfix-api/ 子路径下，保留原始 URL 自行处理
    ignoreTrailingSlash: true,
  });

  app.register(cors, {
    origin: true,
    methods: ['GET', 'POST', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['authorization', 'content-type', 'if-none-match'],
    exposedHeaders: ['etag'],
  });

  /* ------------------------------------------------ 公共钩子 */

  app.addHook('onRequest', async (req) => {
    let pathname = req.url.split('?')[0];
    if (BASE_PATH && pathname.startsWith(BASE_PATH)) pathname = pathname.slice(BASE_PATH.length) || '/';
    pathname = pathname.replace(/\/+$/, '') || '/';
    req.routePath = pathname;
  });

  app.addHook('onSend', async (req, reply, payload) => {
    // 管理页的构建产物（带 hash）允许缓存，其余接口一律不缓存。
    // 注意：CORS 预检由 @fastify/cors 在 onRequest 里直接返回，不会经过上面的 onRequest hook，
    // 所以这里必须容忍 routePath 为空。
    if (!req.routePath || !req.routePath.startsWith('/admin/')) reply.header('cache-control', 'no-store');
    return payload;
  });

  app.setErrorHandler((error, _req, reply) => {
    if (error instanceof ApiError) {
      return reply.code(error.status).send({ ok: false, error: error.message, ...error.extra });
    }
    const status = (error as any).statusCode;
    if (status === 413) return reply.code(413).send({ ok: false, error: '请求体过大。' });
    console.error('[classfix-api]', (error as Error).stack || error);
    return reply.code(500).send({ ok: false, error: '服务内部错误：' + (error as Error).message });
  });

  app.setNotFoundHandler((req, reply) => {
    if (req.routePath.startsWith('/admin')) {
      const index = path.join(ADMIN_DIST, 'index.html');
      if (fs.existsSync(index)) return reply.type('text/html; charset=utf-8').send(fs.readFileSync(index));
    }
    return reply.code(404).send({ ok: false, error: '没有这个接口。' });
  });

  /* ------------------------------------------------ 管理页静态资源 */

  app.register(fastifyStatic, {
    root: ADMIN_DIST,
    prefix: '/admin/',
    decorateReply: false,
    cacheControl: true,
    maxAge: '7d',
  });

  /* ------------------------------------------------ 公共接口 */

  app.get('/', async (_req, reply) => ok(reply, pub.health()));
  app.get('/api/health', async (_req, reply) => ok(reply, pub.health()));

  app.get('/admin', async (_req, reply) => {
    const index = path.join(ADMIN_DIST, 'index.html');
    if (!fs.existsSync(index)) throw new ApiError(404, '管理页未随包部署。');
    return reply.type('text/html; charset=utf-8').send(fs.readFileSync(index));
  });
  app.get('/admin.html', async (_req, reply) => {
    const index = path.join(ADMIN_DIST, 'index.html');
    if (!fs.existsSync(index)) throw new ApiError(404, '管理页未随包部署。');
    return reply.type('text/html; charset=utf-8').send(fs.readFileSync(index));
  });

  app.get('/api/rules', async (req, reply) => {
    const result = pub.rules();
    reply.header('etag', result.etag);
    if (req.headers['if-none-match'] === result.etag) return reply.code(304).send();
    return ok(reply, result.body);
  });

  app.post('/api/auth/code', async (req, reply) => ok(reply, await pub.sendCode((req.body || {}) as any)));
  app.post('/api/auth/login', async (req, reply) => ok(reply, await pub.login((req.body || {}) as any)));

  app.get('/api/me', async (req, reply) => ok(reply, pub.me(userFromAuth(req.headers.authorization))));

  app.post(
    '/api/diagnose',
    { bodyLimit: 512 * 1024 },
    async (req, reply) => ok(reply, await pub.diagnose(userFromAuth(req.headers.authorization), req.body || {})),
  );

  app.post('/api/report', async (req, reply) => ok(reply, pub.report(userFromAuth(req.headers.authorization), req.body || {})));
  app.get('/api/solutions', async (req, reply) =>
    ok(reply, pub.publishedSolutions(userFromAuth(req.headers.authorization))),
  );

  /* ------------------------------------------------ 管理端 */

  app.post('/api/admin/login', async (req, reply) => {
    const { config } = await import('./store');
    const crypto = await import('node:crypto');
    if (!config.adminPassword) throw new ApiError(503, '服务端未设置管理口令。');
    const given = String(((req.body || {}) as any).password || '');
    const a = crypto.createHash('sha256').update(given).digest();
    const b = crypto.createHash('sha256').update(String(config.adminPassword)).digest();
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) throw new ApiError(401, '口令不正确。');
    const { adminToken } = await import('./auth');
    return ok(reply, { ok: true, token: adminToken() });
  });

  /** 其余 /api/admin/* 统一鉴权 */
  const guard = async (req: FastifyRequest, reply: FastifyReply) => {
    if (!isAdmin(req.headers.authorization)) {
      return reply.code(401).send({ ok: false, error: '需要管理权限。' });
    }
  };

  app.get('/api/admin/overview', { preHandler: guard }, async (_req, reply) => ok(reply, admin.overview()));
  app.get('/api/admin/catalog', { preHandler: guard }, async (_req, reply) => ok(reply, admin.catalogGet()));
  app.post('/api/admin/catalog', { preHandler: guard }, async (req, reply) => ok(reply, admin.catalogSave((req.body || {}) as any)));

  app.get('/api/admin/issues', { preHandler: guard }, async (req, reply) => {
    const status = String((req.query as any)?.status || '');
    return ok(reply, admin.issueList(status));
  });

  app.get('/api/admin/solutions', { preHandler: guard }, async (_req, reply) => ok(reply, admin.solutionList()));
  app.post('/api/admin/settings', { preHandler: guard }, async (req, reply) => ok(reply, admin.settingsSave(req.body || {})));
  app.post('/api/admin/solution/save', { preHandler: guard }, async (req, reply) => ok(reply, admin.solutionSave(req.body || {})));
  app.post('/api/admin/solutions/import-baseline', { preHandler: guard }, async (_req, reply) => ok(reply, admin.importBaseline()));
  app.post('/api/admin/solution/review', { preHandler: guard }, async (req, reply) => ok(reply, admin.review((req.body || {}) as any)));
  app.post('/api/admin/solution/generate', { preHandler: guard }, async (req, reply) => ok(reply, await admin.generate((req.body || {}) as any)));
  app.post('/api/admin/solution/generate-all', { preHandler: guard }, async (_req, reply) => ok(reply, await admin.generateAll()));
  app.post('/api/admin/publish', { preHandler: guard }, async (_req, reply) => ok(reply, admin.publish()));
  app.get('/api/admin/users', { preHandler: guard }, async (_req, reply) => ok(reply, admin.userList()));
  app.post('/api/admin/user/ban', { preHandler: guard }, async (req, reply) => ok(reply, admin.banUser((req.body || {}) as any)));

  return app;
}

export function shutdown(): void {
  flushSync();
}
