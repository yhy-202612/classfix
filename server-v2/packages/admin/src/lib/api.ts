import type {
  AdminCatalogRes,
  AdminIssueListRes,
  AdminLoginRes,
  AdminOverviewRes,
  AdminSettingsReq,
  AdminSettingsRes,
  AdminSolutionListRes,
  AdminUserListRes,
  BanRes,
  GenerateAllRes,
  GenerateRes,
  ImportBaselineRes,
  PublishRes,
  ReviewReq,
  ReviewRes,
  SolutionSaveReq,
  SolutionSaveRes,
} from '@classfix/shared';

/** 与旧 admin.html 保持一致的 storage key */
export const TOKEN_KEY = 'cf_admin_token';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

let memoryToken = '';

export function getToken(): string {
  if (memoryToken) return memoryToken;
  try {
    memoryToken = window.localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    memoryToken = '';
  }
  return memoryToken;
}

export function setToken(token: string): void {
  memoryToken = token;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* localStorage 不可用时仅保留内存态 */
  }
}

export function clearToken(): void {
  memoryToken = '';
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略 */
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** 401 统一处理：清 token 后由 App 切回登录页 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** 允许用环境变量兜底（默认同源，dev 走 vite proxy） */
const API_BASE = String(import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '');

type RequestOptions = {
  method?: 'GET' | 'POST';
  body?: unknown;
  /** false 时表示这是登录这类不需要/不应该触发全局 401 处理的请求 */
  auth?: boolean;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const withAuth = options.auth !== false;
  const method = options.method ?? (options.body === undefined ? 'GET' : 'POST');
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (withAuth && token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError('网络异常，请检查网络后重试。', 0);
  }

  const text = await response.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text) as unknown;
    } catch {
      json = null;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && withAuth) {
      clearToken();
      unauthorizedHandler?.();
    }
    const fallback = `请求失败（HTTP ${response.status}）`;
    const payload = (json ?? {}) as { error?: unknown };
    const message = typeof payload.error === 'string' && payload.error ? payload.error : fallback;
    throw new ApiError(message, response.status);
  }

  if (json === null) throw new ApiError('服务端返回了无法解析的响应。', response.status);
  return json as T;
}

/** POST /api/admin/catalog 的响应体（契约里未声明，服务端返回 { ok, count }） */
export type CatalogSaveRes = { ok: true; count: number };

export const adminApi = {
  /** 登录：口令校验，成功返回 token */
  login: (password: string) => request<AdminLoginRes>('/api/admin/login', { body: { password }, auth: false }),

  overview: () => request<AdminOverviewRes>('/api/admin/overview'),

  users: () => request<AdminUserListRes>('/api/admin/users'),
  banUser: (id: string, banned: boolean) => request<BanRes>('/api/admin/user/ban', { body: { id, banned } }),

  issues: (status?: string) =>
    request<AdminIssueListRes>(`/api/admin/issues${status ? `?status=${encodeURIComponent(status)}` : ''}`),

  solutions: () => request<AdminSolutionListRes>('/api/admin/solutions'),
  saveSolution: (body: SolutionSaveReq) => request<SolutionSaveRes>('/api/admin/solution/save', { body }),
  reviewSolution: (body: ReviewReq) => request<ReviewRes>('/api/admin/solution/review', { body }),
  generateSolution: (issueId: string) =>
    request<GenerateRes>('/api/admin/solution/generate', { body: { issueId } }),
  generateAllSolutions: () => request<GenerateAllRes>('/api/admin/solution/generate-all', { body: {} }),
  importBaseline: () => request<ImportBaselineRes>('/api/admin/solutions/import-baseline', { body: {} }),

  catalog: () => request<AdminCatalogRes>('/api/admin/catalog'),
  saveCatalog: (items: Array<{ key: string; title: string; action: string; origin: string }>) =>
    request<CatalogSaveRes>('/api/admin/catalog', { body: { items } }),

  publish: () => request<PublishRes>('/api/admin/publish', { body: {} }),

  saveSettings: (body: AdminSettingsReq) => request<AdminSettingsRes>('/api/admin/settings', { body }),
};
