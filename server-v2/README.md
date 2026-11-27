# ClassFix 云端服务（v2）

ClassFix 的云端 API 与运营后台，重写为规范的 TypeScript 工程：**API 契约、数据格式、错误文案与旧版逐字一致**，客户端 1.6.x/1.6.8 无需改动即可接入。

- `packages/server` —— Fastify + TypeScript 的 API 服务（原 `server/server.js` 1000 行单文件拆分而来）
- `packages/admin` —— React + Vite + Tailwind 的运营后台（替换原 `server/admin.html` 单页）
- `packages/shared` —— 前后端共用的 API 类型（纯类型包）

> 只重写了服务端。`client/`（Windows 桌面端 Python）、`site/`、`site-eeo/` 保持原样。

## 快速开始

```bash
corepack enable && corepack prepare pnpm@9.12.0 --activate
pnpm install

# 后端（默认 8104，数据目录 ./packages/server/data）
pnpm dev

# 前端（5173，/api 代理到 8104）
pnpm --filter @classfix/admin dev

# 构建
pnpm build                       # server → dist/ ；admin → packages/admin/dist
pnpm typecheck
pnpm lint
pnpm --filter @classfix/server test   # 契约测试（见下）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | 8104 | 监听端口 |
| `HOST` | 0.0.0.0 | 监听地址 |
| `BASE_PATH` | 空 | 子路径部署（如 `/classfix-api`） |
| `CLASSFIX_API_DATA` | 自动探测 | 数据目录；若上级存在 `releases/` 则落同级 `data/`（兼容 site-deploy） |
| `CLASSFIX_ADMIN_DIST` | `packages/server/admin` | 后台静态资源目录 |

## 数据与密钥

- `data/db.json`：用户、积分流水、诊断记录、问题库、方案库、规则包、离线基线。**JSON 文件 + 400ms 合并写 + 临时文件 rename 原子写**（与旧版一致，量级不需要数据库）。
- `data/config.json`：`secret`（本站令牌签名）、`adminPassword`、`aiKey/aiBase/aiModel/aiTimeout`、`otpKey/otpSecret/otpBase`。
- **AI 密钥只在服务端**：客户端调 `/api/diagnose`，服务端代理上游模型并扣积分；密钥永不下发。
- 登录复用 site-deploy 的 OTP 服务，本服务不直连短信通道。
- ⚠️ 手工改 `db.json` 前必须先停服务，否则内存里的旧副本会在 400ms 合并写时覆盖回去。

## API 一览

公共：`GET /`、`GET /api/health`、`GET /api/rules`（ETag/304）、`POST /api/auth/code`、`POST /api/auth/login`、`GET /api/me`、`POST /api/diagnose`、`POST /api/report`、`GET /api/solutions`

管理（`Authorization: Bearer <adminToken>`）：`POST /api/admin/login`、`GET /api/admin/overview`、`GET|POST /api/admin/catalog`、`GET /api/admin/issues`、`GET /api/admin/solutions`、`POST /api/admin/settings`、`POST /api/admin/solution/save`、`POST /api/admin/solutions/import-baseline`、`POST /api/admin/solution/review`、`POST /api/admin/solution/generate`、`POST /api/admin/solution/generate-all`、`POST /api/admin/publish`、`GET /api/admin/users`、`POST /api/admin/user/ban`

后台页面：`GET /admin`（构建产物由服务端托管，静态资源在 `/admin/assets/*`）。

## 契约测试（改造不改变行为的证据）

`packages/server/test/contract.test.mjs` 会**同时启动旧实现与新实现**，对同一组请求比对状态码、顶层字段集合与错误文案：

```bash
pnpm --filter @classfix/server build
OLD_SERVER=/path/to/old/server.js pnpm --filter @classfix/server test
```

覆盖：健康检查、规则包 ETag/304、手机号与验证码校验文案、未登录 401、未知路径 404、管理口令缺失 503、管理鉴权 401、CORS 预检、`Cache-Control: no-store`。当前 **15/15 通过**。

## 部署（site-deploy node 站点）

```bash
pnpm install && pnpm build
# 把后台产物放进服务端的静态目录
cp -r packages/admin/dist packages/server/admin
# 发布：packages/server/{dist,admin,package.json,node_modules 由平台安装}
```

站点 entry 指向 `packages/server/dist/index.js`（或把 `dist` 内容放站点根，entry `index.js`）。数据仍落 `<slug>/data/{db.json,config.json}`，跨 release 保留。

## 与旧版的差异

| 方面 | 旧版 | 新版 |
|---|---|---|
| 结构 | `server.js` 1000 行单文件 | 按职责拆为 `store/auth/otp/points/knowledge/ai/services/http` |
| 语言 | 纯 JS | TypeScript（strict） |
| HTTP | 手写 `http.createServer` 路由 | Fastify + `@fastify/cors` + `@fastify/static` |
| 后台 | `admin.html` 408 行单文件（内联 CSS/JS） | React + Vite + Tailwind + lucide |
| 质量门禁 | 无 | typecheck / lint / build / 契约测试 / CI |

行为差异（有意为之，均为纯增益）：进程退出前刷新未落盘数据；CORS 预检由框架统一处理；`/admin` 找不到构建产物时返回 404 而非 500。

## 回滚

旧的 `server/server.js` 仍在仓库里，站点 entry 指回它即可（配置 `config.json` 与 `db.json` 结构完全兼容，可互换）。
