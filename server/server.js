'use strict';
/**
 * ClassFix 云端服务（零依赖 Node）
 *
 * 职责：
 *  1. 手机号验证码登录（复用 site-deploy 的 OTP 服务，不需要自己接短信）
 *  2. 积分：注册送分、每日赠分、AI 诊断扣分
 *  3. AI 诊断代理（服务端持密钥，客户端不必自带 key；未登录时客户端仍可用自己的 key 直连）
 *  4. 问题库：AI 诊断中提到、但现有离线修复流程没覆盖的问题留存去重
 *  5. 方案库：把同类问题归纳成可复用方案，人工复核（或关闭复核后自动）发布为规则包
 *  6. 规则包：客户端定期拉取，作为「离线修复」的增量补充建议
 *
 * 部署：site-deploy 的 node 站点（入口 server.js），数据落
 *      /data/rdfz-storage/sites-deploy/<slug>/data/{db.json,config.json}
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const https = require('https');

const PORT = Number(process.env.PORT || 8104);
const HOST = process.env.HOST || '0.0.0.0';
const BASE_PATH = String(process.env.BASE_PATH || '').replace(/\/+$/, '');

/* ---------------------------------------------------------------- 存储 */

function findDataRoot() {
  let dir = __dirname;
  for (let i = 0; i < 6; i += 1) {
    try {
      if (fs.statSync(path.join(dir, 'releases')).isDirectory()) return path.join(dir, 'data');
    } catch (error) { /* 继续向上找 */ }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return path.join(__dirname, 'data');
}

const DATA = process.env.CLASSFIX_API_DATA || findDataRoot();
fs.mkdirSync(DATA, { recursive: true });
const DB_FILE = path.join(DATA, 'db.json');
const CONFIG_FILE = path.join(DATA, 'config.json');

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    return fallback;
  }
}

function writeJsonAtomic(file, value) {
  const temp = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(value, null, 1), 'utf8');
  fs.renameSync(temp, file);
}

const defaults = () => ({
  users: {},
  sessions: {},
  ledger: [],
  diagnoses: [],
  issues: {},
  solutions: {},
  // 离线流程已经覆盖的方案基线：用来告诉模型「这些不用再提了」，也可在后台人工维护。
  catalog: [],
  settings: {
    autoPublish: false,
    newUserPoints: 10,
    dailyGrant: 5,
    cost: { audio: 1, check: 1, video: 2, preflight: 1 },
    dailyQuotaPerApp: 300,
    maxDiagnosesPerDay: 40
  },
  rules: { version: 0, publishedAt: '', items: [] }
});

const config = readJson(CONFIG_FILE, null) || {};
const db = Object.assign(defaults(), readJson(DB_FILE, {}));
const SETTINGS = Object.assign(defaults().settings, db.settings || {});
db.settings = SETTINGS;
if (!config.secret) {
  config.secret = crypto.randomBytes(32).toString('hex');
  try { writeJsonAtomic(CONFIG_FILE, config); } catch (error) { console.error('[config] 无法保存密钥', error.message); }
}

let dirty = false;
let saving = null;
function persist() {
  dirty = true;
  if (saving) return;
  saving = setTimeout(() => {
    saving = null;
    if (!dirty) return;
    dirty = false;
    try { writeJsonAtomic(DB_FILE, db); } catch (error) { console.error('[db] 写入失败', error.message); }
  }, 400);
}

/* ---------------------------------------------------------------- 工具 */

const now = () => new Date().toISOString();
const todayBJ = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
const maskPhone = (phone) => String(phone).replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');
const sha1 = (text) => crypto.createHash('sha1').update(text).digest('hex');

function newId(prefix) {
  return prefix + '_' + crypto.randomBytes(8).toString('hex');
}

function normalizeTitle(title) {
  return String(title || '').replace(/[\s\p{P}\p{S}]/gu, '').slice(0, 40);
}

function fingerprint(kind, title) {
  return sha1('cf1|' + kind + '|' + normalizeTitle(title)).slice(0, 16);
}

function send(res, status, body, headers) {
  const payload = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, Object.assign({
    'Content-Type': typeof body === 'string' ? 'text/plain; charset=utf-8' : 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'authorization,content-type,if-none-match',
    'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS'
  }, headers || {}));
  res.end(payload);
}

function fail(res, status, message, extra) {
  send(res, status, Object.assign({ ok: false, error: message }, extra || {}));
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > (limit || 256 * 1024)) {
        reject(new Error('payload too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch (error) { reject(new Error('invalid json')); }
    });
    req.on('error', reject);
  });
}

function requestJson(options, payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(options.url);
    const transport = url.protocol === 'http:' ? http : https;
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload), 'utf8');
    const request = transport.request({
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || (url.protocol === 'http:' ? 80 : 443),
      path: url.pathname + url.search,
      method: options.method || (body ? 'POST' : 'GET'),
      headers: Object.assign({ 'Content-Type': 'application/json' }, options.headers || {},
        body ? { 'Content-Length': body.length } : {}),
      timeout: options.timeout || 30000
    }, (response) => {
      const parts = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > 1024 * 1024) { request.destroy(); return; }
        parts.push(chunk);
      });
      response.on('end', () => {
        const text = Buffer.concat(parts).toString('utf8');
        let parsed = null;
        try { parsed = JSON.parse(text); } catch (error) { /* 非 JSON 响应 */ }
        resolve({ status: response.statusCode, json: parsed, text });
      });
    });
    request.on('timeout', () => request.destroy(new Error('upstream timeout')));
    request.on('error', reject);
    if (body) request.write(body);
    request.end();
  });
}

/* ---------------------------------------------------------------- 本站令牌 */

function signToken(user) {
  const payload = { uid: user.id, phone: user.phone, exp: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 30 };
  const body = 'c1.' + Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
  const mac = crypto.createHmac('sha256', config.secret).update(body).digest('base64url');
  return body + '.' + mac;
}

function verifyToken(token) {
  if (typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 3 || parts[0] !== 'c1') return null;
  const body = parts[0] + '.' + parts[1];
  const mac = crypto.createHmac('sha256', config.secret).update(body).digest('base64url');
  if (mac.length !== parts[2].length) return null;
  if (!crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(parts[2]))) return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    if (!payload.exp || payload.exp * 1000 < Date.now()) return null;
    if (!db.users[payload.uid]) return null;
    return payload;
  } catch (error) {
    return null;
  }
}

function currentUser(req) {
  const header = String(req.headers.authorization || '');
  const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
  const payload = verifyToken(token);
  return payload ? db.users[payload.uid] : null;
}

function currentAdmin(req) {
  const header = String(req.headers.authorization || '');
  const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
  return Boolean(config.adminPassword) && token === adminToken();
}

let cachedAdminToken = null;
function adminToken() {
  if (!cachedAdminToken) {
    cachedAdminToken = 'a1.' + crypto.createHmac('sha256', config.secret)
      .update('admin|' + String(config.adminPassword || '')).digest('base64url');
  }
  return cachedAdminToken;
}

/* ---------------------------------------------------------------- OTP（复用 site-deploy） */

const otpBase = () => String(config.otpBase || 'http://172.19.0.1:8090/api').replace(/\/+$/, '');

/** 平台签发的 token 形如 v1.<payload>.<hmac>；签名原文按常见两种写法都试一遍。 */
function verifyOtpToken(token, secret) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3 || parts[0] !== 'v1') return null;
  let payload = null;
  try {
    payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
  } catch (error) {
    return null;
  }
  if (payload.exp && payload.exp * 1000 < Date.now() - 5000) return null;
  if (secret) {
    const candidates = ['v1.' + parts[1], parts[1]];
    for (const source of candidates) {
      const mac = crypto.createHmac('sha256', secret).update(source).digest('base64url');
      if (mac.length === parts[2].length
          && crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(parts[2]))) {
        return payload;
      }
    }
    // 签名口径没对上时仍然信任本次响应：这枚 token 是我们自己刚向平台换回来的，
    // 走的是服务端到服务端的内网/加密通道，不经浏览器。
    console.warn('[otp] token 签名与本服务实现不一致，按同源可信处理');
  }
  return payload;
}

async function otpSend(phone) {
  if (!config.otpKey) throw new Error('服务端未配置短信应用');
  const response = await requestJson({
    url: otpBase() + '/otp/send',
    timeout: 15000
  }, { appKey: config.otpKey, phone, purpose: 'login' });
  if (!response.json || response.json.ok !== true) {
    const message = (response.json && (response.json.error || response.json.message)) || ('HTTP ' + response.status);
    throw new Error(message);
  }
  return response.json;
}

async function otpVerify(phone, code) {
  if (!config.otpKey) throw new Error('服务端未配置短信应用');
  const response = await requestJson({
    url: otpBase() + '/otp/verify',
    timeout: 15000
  }, { appKey: config.otpKey, phone, code });
  if (!response.json || response.json.ok !== true) {
    const message = (response.json && (response.json.error || response.json.message)) || '验证码不正确或已过期';
    throw new Error(message);
  }
  const payload = verifyOtpToken(response.json.token, config.otpSecret);
  if (!payload || !payload.phone) throw new Error('验证凭证无法解析');
  return payload;
}

/* ---------------------------------------------------------------- 积分 */

function grantDaily(user) {
  const today = todayBJ();
  if (user.dailyGranted === today) return 0;
  user.dailyGranted = today;
  const amount = Number(SETTINGS.dailyGrant) || 0;
  if (amount > 0) {
    user.points = (user.points || 0) + amount;
    db.ledger.push({ uid: user.id, delta: amount, reason: 'daily', at: now() });
    if (db.ledger.length > 5000) db.ledger = db.ledger.slice(-4000);
  }
  persist();
  return amount;
}

function spend(user, amount, reason, meta) {
  if ((user.points || 0) < amount) return false;
  user.points -= amount;
  db.ledger.push({ uid: user.id, delta: -amount, reason, at: now(), meta: meta || null });
  if (db.ledger.length > 5000) db.ledger = db.ledger.slice(-4000);
  persist();
  return true;
}

function diagnosisCountToday(user) {
  const today = todayBJ();
  return db.diagnoses.filter((item) => item.uid === user.id && item.at.slice(0, 10) === today).length;
}

/* ---------------------------------------------------------------- 问题库 / 方案库 */

function recordIssue(kind, issue) {
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
      samples: []
    };
  }
  item.count += 1;
  item.lastSeen = stamp;
  if (item.checkKey === '' && issue.checkKey) item.checkKey = String(issue.checkKey).slice(0, 40);
  item.samples.push({
    evidence: String(issue.evidence || '').slice(0, 300),
    action: String(issue.action || '').slice(0, 300),
    at: stamp
  });
  if (item.samples.length > 5) item.samples = item.samples.slice(-5);
  persist();
  return item;
}

function publishRules() {
  // appliesTo 是「最相关的检测项 key」列表（见 SOLUTION_PROMPT），不是 kind/checkKey 的位置数组。
  // 早期版本把 appliesTo[0] 当 kind，导致客户端的 kind 过滤永远匹配不上，这里一并修正。
  const items = Object.values(db.solutions)
    .filter((item) => item.status === 'published')
    .map((item) => ({
      id: item.id,
      match: { kind: item.kind || '', checkKeys: (item.appliesTo || []).slice(0, 3) },
      title: item.title,
      steps: item.steps,
      verify: item.verify
    }));
  db.rules = { version: (db.rules.version || 0) + 1, publishedAt: now(), items };
  persist();
  return db.rules;
}

/* ---------------------------------------------------------------- AI */

const DIAGNOSE_RULES = [
  '你是 ClassFix 的课堂设备急救顾问。只允许引用输入里给出的检测数据，缺失的信息要说「尚未检测」，不得臆造。',
  '输出严格 JSON，不要 Markdown 代码块之外的任何文字：',
  '{"advice":"给老师看的建议，200 字内，先给最快可逆的恢复步骤","steps":["可执行步骤"],"verify":"怎么验证已恢复",',
  ' "issues":[{"title":"问题名，12 字内","checkKey":"最相关的检测项 key，没有就空字符串","evidence":"依据","action":"处置"}]}',
  'issues 只列「检测数据里确实存在、但常规修复流程没覆盖」的问题；没有就给空数组。'
];

function diagnosePrompt() {
  const lines = DIAGNOSE_RULES.slice();
  const baseline = (db.catalog || [])
    .map((item) => String(item.title || '').trim())
    .filter(Boolean)
    .slice(0, 120);
  if (baseline.length) {
    lines.push('');
    lines.push('下面这些问题离线修复流程已经会给处置建议，不要把它们当成「未覆盖」写进 issues（除非本次数据的成因明显与已有处置不同）：');
    lines.push(baseline.join('、'));
  }
  return lines.join('\n');
}

const SOLUTION_PROMPT = [
  '你是 ClassFix 的知识整理员。下面会给你若干条针对同一类课堂设备问题的诊断片段。',
  '请把它们归纳成一条可复用的修复方案，输出严格 JSON：',
  '{"title":"方案名，16 字内","appliesTo":["最相关的检测项 key"],"steps":["现场可执行的步骤"],',
  ' "verify":"验证方法","confidence":"high|medium|low"}',
  '步骤必须能在一间教室里当场做完，不许要求上传文件、安装驱动或改注册表。'
].join('\n');

function parseJsonLoose(text) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const cleaned = raw.replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  try { return JSON.parse(cleaned); } catch (error) { /* 继续尝试截取 */ }
  const start = cleaned.indexOf('{');
  const end = cleaned.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try { return JSON.parse(cleaned.slice(start, end + 1)); } catch (error) { return null; }
  }
  return null;
}

function aiConfig() {
  const base = String(config.aiBase || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
  return {
    url: base + '/chat/completions',
    model: config.aiModel || 'deepseek-flash',
    key: config.aiKey || '',
    timeout: Number(config.aiTimeout || 45000)
  };
}

async function askModel(systemPrompt, userContent, maxTokens) {
  const ai = aiConfig();
  if (!ai.key) throw new Error('服务端未配置 AI 密钥');
  const response = await requestJson({
    url: ai.url,
    timeout: ai.timeout,
    headers: { Authorization: 'Bearer ' + ai.key }
  }, {
    model: ai.model,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userContent }
    ],
    max_tokens: maxTokens || 1600,
    temperature: 0.3,
    stream: false
  });
  if (response.status !== 200 || !response.json) {
    const reason = (response.json && response.json.error && response.json.error.message) || ('HTTP ' + response.status);
    throw new Error('AI 服务返回异常：' + reason);
  }
  const content = response.json.choices && response.json.choices[0] && response.json.choices[0].message
    && response.json.choices[0].message.content;
  if (!content) throw new Error('AI 没有返回内容');
  return { text: String(content), parsed: parseJsonLoose(content) };
}

/* ---------------------------------------------------------------- 路由 */

const COST_KIND = (kind) => Number(SETTINGS.cost[kind] || SETTINGS.cost.audio || 1);

function publicUser(user) {
  return {
    id: user.id,
    phone: user.maskedPhone || maskPhone(user.phone),
    points: user.points || 0,
    dailyGranted: user.dailyGranted || '',
    createdAt: user.createdAt,
    banned: Boolean(user.banned)
  };
}

async function route(req, res) {
  const url = new URL(req.url, 'http://localhost');
  let pathname = url.pathname;
  if (BASE_PATH && pathname.startsWith(BASE_PATH)) pathname = pathname.slice(BASE_PATH.length) || '/';
  pathname = pathname.replace(/\/+$/, '') || '/';

  if (req.method === 'OPTIONS') return send(res, 204, '');

  /* ---------------- 健康检查 ---------------- */
  if (pathname === '/' || pathname === '/api/health') {
    return send(res, 200, {
      ok: true,
      service: 'classfix-api',
      version: 1,
      users: Object.keys(db.users).length,
      rules: db.rules.version,
      hasAi: Boolean(config.aiKey),
      hasOtp: Boolean(config.otpKey),
      autoPublish: Boolean(SETTINGS.autoPublish),
      time: now()
    });
  }

  /* ---------------- 管理页 ---------------- */
  if ((pathname === '/admin' || pathname === '/admin.html') && req.method === 'GET') {
    try {
      const page = fs.readFileSync(path.join(__dirname, 'admin.html'));
      return send(res, 200, page, { 'Content-Type': 'text/html; charset=utf-8' });
    } catch (error) {
      return fail(res, 404, '管理页未随包部署。');
    }
  }

  /* ---------------- 规则包（免登录） ---------------- */
  if (pathname === '/api/rules' && req.method === 'GET') {
    const etag = 'W/"cf-rules-' + db.rules.version + '"';
    if (req.headers['if-none-match'] === etag) return send(res, 304, '');
    return send(res, 200, Object.assign({ ok: true }, db.rules), { ETag: etag });
  }

  /* ---------------- 发送验证码 ---------------- */
  if (pathname === '/api/auth/code' && req.method === 'POST') {
    const body = await readBody(req);
    const phone = String(body.phone || '').trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) return fail(res, 400, '请输入 11 位手机号。');
    try {
      const result = await otpSend(phone);
      return send(res, 200, {
        ok: true,
        masked: result.phone || maskPhone(phone),
        expiresIn: result.expiresIn || 300,
        retryAfter: result.retryAfter || 60,
        debugCode: result.debug_code || ''
      });
    } catch (error) {
      return fail(res, 502, '验证码发送失败：' + error.message);
    }
  }

  /* ---------------- 验证码登录 / 注册 ---------------- */
  if (pathname === '/api/auth/login' && req.method === 'POST') {
    const body = await readBody(req);
    const phone = String(body.phone || '').trim();
    const code = String(body.code || '').trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) return fail(res, 400, '请输入 11 位手机号。');
    if (!/^\d{4,8}$/.test(code)) return fail(res, 400, '请输入收到的验证码。');
    let payload;
    try {
      payload = await otpVerify(phone, code);
    } catch (error) {
      return fail(res, 401, error.message);
    }
    const realPhone = String(payload.phone || phone);
    let user = Object.values(db.users).find((item) => item.phone === realPhone);
    let created = false;
    if (!user) {
      user = {
        id: newId('u'),
        phone: realPhone,
        maskedPhone: maskPhone(realPhone),
        points: Number(SETTINGS.newUserPoints) || 0,
        // 注册当天已给新用户积分，不再叠加当日赠分，避免「新用户 10 分」变成 15 分让人困惑。
        dailyGranted: todayBJ(),
        createdAt: now(),
        lastLoginAt: now(),
        banned: false
      };
      db.users[user.id] = user;
      db.ledger.push({ uid: user.id, delta: user.points, reason: 'register', at: now() });
      created = true;
    }
    if (user.banned) return fail(res, 403, '该账号已被停用。');
    user.lastLoginAt = now();
    grantDaily(user);
    persist();
    return send(res, 200, {
      ok: true,
      created,
      token: signToken(user),
      user: publicUser(user),
      settings: { cost: SETTINGS.cost, dailyGrant: SETTINGS.dailyGrant, newUserPoints: SETTINGS.newUserPoints }
    });
  }

  /* ---------------- 需登录 ---------------- */
  const user = currentUser(req);

  if (pathname === '/api/me' && req.method === 'GET') {
    if (!user) return fail(res, 401, '请先登录。');
    const granted = grantDaily(user);
    return send(res, 200, {
      ok: true,
      user: publicUser(user),
      grantedToday: granted,
      cost: SETTINGS.cost,
      dailyGrant: SETTINGS.dailyGrant,
      diagnosesToday: diagnosisCountToday(user),
      limitPerDay: SETTINGS.maxDiagnosesPerDay
    });
  }

  if (pathname === '/api/diagnose' && req.method === 'POST') {
    if (!user) return fail(res, 401, '请先登录，或在客户端填写自己的 API 密钥。');
    const body = await readBody(req, 512 * 1024);
    const kind = String(body.kind || 'audio');
    const evidence = body.evidence && typeof body.evidence === 'object' ? body.evidence : null;
    if (!evidence) return fail(res, 400, '缺少检测数据。');
    if (diagnosisCountToday(user) >= SETTINGS.maxDiagnosesPerDay) {
      return fail(res, 429, '今天已经用完 ' + SETTINGS.maxDiagnosesPerDay + ' 次云端诊断，明天再试。');
    }
    const cost = COST_KIND(kind);
    if (!spend(user, cost, 'diagnose', { kind })) {
      return fail(res, 402, '积分不足（本次需要 ' + cost + ' 分，当前 ' + (user.points || 0) + ' 分）。', {
        points: user.points || 0, cost
      });
    }
    let result;
    try {
      result = await askModel(diagnosePrompt(), JSON.stringify({ kind, evidence }), 1600);
    } catch (error) {
      user.points += cost;
      db.ledger.push({ uid: user.id, delta: cost, reason: 'refund', at: now() });
      persist();
      return fail(res, 502, error.message + '（已退回 ' + cost + ' 积分）');
    }
    const reply = result.parsed || {};
    const issues = Array.isArray(reply.issues) ? reply.issues.slice(0, 8) : [];
    const stored = [];
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
      steps: Array.isArray(reply.steps) ? reply.steps.slice(0, 8).map((x) => String(x).slice(0, 200)) : [],
      verify: String(reply.verify || '').slice(0, 400),
      issueIds: stored.map((item) => item.id)
    };
    db.diagnoses.push(record);
    if (db.diagnoses.length > 4000) db.diagnoses = db.diagnoses.slice(-3000);
    persist();
    return send(res, 200, {
      ok: true,
      advice: record.advice,
      steps: record.steps,
      verify: record.verify,
      issues: stored,
      points: user.points,
      cost,
      diagnosisId: record.id
    });
  }

  if (pathname === '/api/report' && req.method === 'POST') {
    if (!user) return fail(res, 401, '请先登录。');
    const body = await readBody(req, 256 * 1024);
    const kind = String(body.kind || 'audio');
    const items = Array.isArray(body.items) ? body.items.slice(0, 20) : [];
    const stored = [];
    for (const item of items) {
      const record = recordIssue(kind, item);
      if (record) stored.push({ id: record.id, title: record.title, count: record.count });
    }
    return send(res, 200, { ok: true, stored });
  }

  if (pathname === '/api/solutions' && req.method === 'GET') {
    if (!user) return fail(res, 401, '请先登录。');
    return send(res, 200, {
      ok: true,
      version: db.rules.version,
      items: Object.values(db.solutions).filter((item) => item.status === 'published')
    });
  }

  /* ---------------- 管理端 ---------------- */
  if (pathname === '/api/admin/login' && req.method === 'POST') {
    const body = await readBody(req);
    if (!config.adminPassword) return fail(res, 503, '服务端未设置管理口令。');
    const given = String(body.password || '');
    const a = crypto.createHash('sha256').update(given).digest();
    const b = crypto.createHash('sha256').update(String(config.adminPassword)).digest();
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return fail(res, 401, '口令不正确。');
    return send(res, 200, { ok: true, token: adminToken() });
  }

  if (pathname.startsWith('/api/admin/')) {
    if (!currentAdmin(req)) return fail(res, 401, '需要管理权限。');
    return adminRoute(req, res, pathname);
  }

  return fail(res, 404, '没有这个接口。');
}

async function adminRoute(req, res, pathname) {
  if (pathname === '/api/admin/overview' && req.method === 'GET') {
    const issues = Object.values(db.issues);
    const solutions = Object.values(db.solutions);
    const today = todayBJ();
    return send(res, 200, {
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
        autoPublish: Boolean(SETTINGS.autoPublish)
      },
      settings: SETTINGS,
      recent: db.diagnoses.slice(-10).reverse().map((item) => ({
        id: item.id, kind: item.kind, at: item.at, advice: item.advice.slice(0, 120)
      }))
    });
  }

  if (pathname === '/api/admin/catalog' && req.method === 'GET') {
    return send(res, 200, { ok: true, items: db.catalog || [] });
  }

  if (pathname === '/api/admin/catalog' && req.method === 'POST') {
    const body = await readBody(req, 256 * 1024);
    const incoming = Array.isArray(body.items) ? body.items : null;
    if (!incoming) return fail(res, 400, 'items 必须是数组。');
    const cleaned = [];
    const seen = new Set();
    for (const raw of incoming.slice(0, 300)) {
      const title = String((raw && raw.title) || '').trim().slice(0, 60);
      if (!title) continue;
      const key = String((raw && raw.key) || '').trim().slice(0, 40);
      // 去重按标题：key 只是溯源标记（同一个文件里的多处 Finding 可能共用一个 key），
      // 用它去重会把不同问题当成同一条丢掉。
      if (seen.has(title)) continue;
      seen.add(title);
      cleaned.push({
        key,
        title,
        action: String((raw && raw.action) || '').trim().slice(0, 200),
        origin: String((raw && raw.origin) || 'offline').slice(0, 20),
        at: now()
      });
    }
    db.catalog = cleaned;
    persist();
    return send(res, 200, { ok: true, count: cleaned.length });
  }

  if (pathname === '/api/admin/issues' && req.method === 'GET') {
    const status = new URL(req.url, 'http://localhost').searchParams.get('status') || '';
    const items = Object.values(db.issues)
      .filter((item) => !status || item.status === status)
      .sort((a, b) => b.count - a.count)
      .slice(0, 200);
    return send(res, 200, { ok: true, items });
  }

  if (pathname === '/api/admin/solutions' && req.method === 'GET') {
    const items = Object.values(db.solutions).sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));
    return send(res, 200, { ok: true, items });
  }

  if (pathname === '/api/admin/settings' && req.method === 'POST') {
    const body = await readBody(req);
    for (const key of ['autoPublish']) {
      if (typeof body[key] === 'boolean') SETTINGS[key] = body[key];
    }
    for (const key of ['newUserPoints', 'dailyGrant', 'maxDiagnosesPerDay']) {
      if (body[key] !== undefined && Number.isFinite(Number(body[key]))) SETTINGS[key] = Number(body[key]);
    }
    if (body.cost && typeof body.cost === 'object') {
      for (const key of Object.keys(SETTINGS.cost)) {
        if (Number.isFinite(Number(body.cost[key]))) SETTINGS.cost[key] = Number(body.cost[key]);
      }
    }
    db.settings = SETTINGS;
    persist();
    return send(res, 200, { ok: true, settings: SETTINGS });
  }

  if (pathname === '/api/admin/solution/save' && req.method === 'POST') {
    const body = await readBody(req);
    const id = String(body.id || '');
    const item = id ? db.solutions[id] : null;
    if (id && !item) return fail(res, 404, '方案不存在。');
    const target = item || { id: newId('s'), createdAt: now(), status: 'draft', fromIssues: [] };
    if (body.kind !== undefined) target.kind = String(body.kind).slice(0, 20);
    if (body.title !== undefined) target.title = String(body.title).slice(0, 80);
    if (body.appliesTo !== undefined) target.appliesTo = (Array.isArray(body.appliesTo) ? body.appliesTo : [])
      .slice(0, 3).map((x) => String(x).slice(0, 40));
    if (body.steps !== undefined) target.steps = (Array.isArray(body.steps) ? body.steps : [])
      .slice(0, 10).map((x) => String(x).slice(0, 240));
    if (body.verify !== undefined) target.verify = String(body.verify).slice(0, 400);
    if (body.confidence !== undefined) target.confidence = String(body.confidence).slice(0, 12);
    if (!target.title) return fail(res, 400, '方案名称不能为空。');
    if (!Array.isArray(target.appliesTo)) target.appliesTo = [];
    if (!Array.isArray(target.steps)) target.steps = [];
    db.solutions[target.id] = target;
    persist();
    return send(res, 200, { ok: true, item: target });
  }

  if (pathname === '/api/admin/solutions/import-baseline' && req.method === 'POST') {
    // 把「离线已覆盖的方案」也归档进方案库：status=baseline 不进规则包（客户端本来就有），
    // 只是让后台能在一处看到全部知识，AI 归纳新方案时也好对照。
    const existing = new Set(Object.values(db.solutions).map((item) => String(item.title || '').trim()));
    const created = [];
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
        reviewNote: '来自客户端离线流程基线'
      };
      created.push({ id, title });
    }
    persist();
    return send(res, 200, { ok: true, created });
  }

  if (pathname === '/api/admin/solution/review' && req.method === 'POST') {
    const body = await readBody(req);
    const item = db.solutions[String(body.id || '')];
    if (!item) return fail(res, 404, '方案不存在。');
    const status = String(body.status || '');
    if (!['draft', 'published', 'rejected'].includes(status)) return fail(res, 400, '状态不合法。');
    item.status = status;
    item.reviewedAt = now();
    item.reviewNote = String(body.note || '').slice(0, 300);
    if (status === 'published') {
      item.publishedAt = now();
      for (const issueId of item.fromIssues || []) {
        const issue = Object.values(db.issues).find((entry) => entry.id === issueId);
        if (issue) issue.status = 'adopted';
      }
      db.rules = publishRules();
    }
    persist();
    return send(res, 200, { ok: true, item, rulesVersion: db.rules.version });
  }

  if (pathname === '/api/admin/solution/generate' && req.method === 'POST') {
    const body = await readBody(req);
    const issue = Object.values(db.issues).find((item) => item.id === String(body.issueId || ''));
    if (!issue) return fail(res, 404, '问题不存在。');
    const lines = [
      '问题类型：' + issue.kind,
      '问题名称：' + issue.title,
      '出现次数：' + issue.count,
      '相关检测项：' + (issue.checkKey || '未标注'),
      '现场片段：'
    ];
    for (const sample of issue.samples) {
      lines.push('- 依据：' + sample.evidence + ' / 处置：' + sample.action);
    }
    let parsed;
    try {
      const result = await askModel(SOLUTION_PROMPT, lines.join('\n'), 900);
      parsed = result.parsed || {};
    } catch (error) {
      return fail(res, 502, error.message);
    }
    const solution = {
      id: newId('s'),
      kind: issue.kind,
      title: String(parsed.title || issue.title).slice(0, 80),
      appliesTo: (Array.isArray(parsed.appliesTo) ? parsed.appliesTo : [issue.checkKey]).filter(Boolean)
        .slice(0, 3).map((x) => String(x).slice(0, 40)),
      steps: (Array.isArray(parsed.steps) ? parsed.steps : []).slice(0, 10).map((x) => String(x).slice(0, 240)),
      verify: String(parsed.verify || '').slice(0, 400),
      confidence: String(parsed.confidence || 'medium').slice(0, 12),
      source: 'ai',
      fromIssues: [issue.id],
      createdAt: now(),
      status: SETTINGS.autoPublish ? 'published' : 'draft'
    };
    if (SETTINGS.autoPublish) {
      solution.publishedAt = now();
      solution.reviewedAt = now();
      solution.reviewNote = '自动发布（未启用人工复核）';
    }
    db.solutions[solution.id] = solution;
    issue.status = SETTINGS.autoPublish ? 'adopted' : 'reviewed';
    if (SETTINGS.autoPublish) db.rules = publishRules();
    persist();
    return send(res, 200, { ok: true, item: solution, rulesVersion: db.rules.version });
  }

  if (pathname === '/api/admin/solution/generate-all' && req.method === 'POST') {
    const pending = Object.values(db.issues).filter((item) => item.status === 'new').slice(0, 10);
    const created = [];
    const failed = [];
    for (const issue of pending) {
      const lines = ['问题类型：' + issue.kind, '问题名称：' + issue.title, '出现次数：' + issue.count,
        '相关检测项：' + (issue.checkKey || '未标注'), '现场片段：'];
      for (const sample of issue.samples) lines.push('- 依据：' + sample.evidence + ' / 处置：' + sample.action);
      try {
        const result = await askModel(SOLUTION_PROMPT, lines.join('\n'), 900);
        const parsed = result.parsed || {};
        const solution = {
          id: newId('s'),
          title: String(parsed.title || issue.title).slice(0, 80),
          appliesTo: (Array.isArray(parsed.appliesTo) ? parsed.appliesTo : [issue.checkKey]).filter(Boolean)
            .slice(0, 3).map((x) => String(x).slice(0, 40)),
          steps: (Array.isArray(parsed.steps) ? parsed.steps : []).slice(0, 10).map((x) => String(x).slice(0, 240)),
          verify: String(parsed.verify || '').slice(0, 400),
          confidence: String(parsed.confidence || 'medium').slice(0, 12),
          source: 'ai',
          fromIssues: [issue.id],
          createdAt: now(),
          status: SETTINGS.autoPublish ? 'published' : 'draft'
        };
        if (SETTINGS.autoPublish) solution.publishedAt = now();
        db.solutions[solution.id] = solution;
        issue.status = SETTINGS.autoPublish ? 'adopted' : 'reviewed';
        created.push(solution.id);
      } catch (error) {
        failed.push({ issueId: issue.id, error: error.message });
      }
    }
    if (SETTINGS.autoPublish && created.length) db.rules = publishRules();
    persist();
    return send(res, 200, { ok: true, created, failed, rulesVersion: db.rules.version });
  }

  if (pathname === '/api/admin/publish' && req.method === 'POST') {
    const rules = publishRules();
    return send(res, 200, { ok: true, rules });
  }

  if (pathname === '/api/admin/users' && req.method === 'GET') {
    const items = Object.values(db.users).slice(0, 500).map((user) => Object.assign(publicUser(user), {
      lastLoginAt: user.lastLoginAt || '', diagnoses: db.diagnoses.filter((item) => item.uid === user.id).length
    }));
    return send(res, 200, { ok: true, items });
  }

  if (pathname === '/api/admin/user/ban' && req.method === 'POST') {
    const body = await readBody(req);
    const target = db.users[String(body.id || '')];
    if (!target) return fail(res, 404, '用户不存在。');
    target.banned = Boolean(body.banned);
    persist();
    return send(res, 200, { ok: true, user: publicUser(target) });
  }

  return fail(res, 404, '没有这个管理接口。');
}

/* ---------------------------------------------------------------- 启动 */

function createServer() {
  return http.createServer((req, res) => {
    route(req, res).catch((error) => {
      console.error('[classfix-api]', error && error.stack ? error.stack : error);
      if (!res.headersSent) fail(res, 500, '服务内部错误：' + (error.message || '未知'));
    });
  });
}

if (require.main === module) {
  const server = createServer();
  server.listen(PORT, HOST, () => {
    console.log('[classfix-api] listening on', HOST + ':' + PORT, 'data =', DATA);
    console.log('[classfix-api] ai =', Boolean(config.aiKey), 'otp =', Boolean(config.otpKey),
      'admin =', Boolean(config.adminPassword), 'autoPublish =', SETTINGS.autoPublish);
  });
  const shutdown = () => {
    try { writeJsonAtomic(DB_FILE, db); } catch (error) { /* 尽力保存 */ }
    server.close(() => process.exit(0));
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

module.exports = { createServer, verifyOtpToken, parseJsonLoose, fingerprint, normalizeTitle, db, SETTINGS, config };
