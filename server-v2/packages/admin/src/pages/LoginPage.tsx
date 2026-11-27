import { KeyRound, ShieldCheck } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Button } from '../components/Button';
import { Input } from '../components/Field';
import { useToast } from '../components/Toast';
import { ApiError, adminApi, setToken } from '../lib/api';
import { errorMessage } from '../lib/format';

export function LoginPage({ onSuccess }: { onSuccess: (token: string) => void }) {
  const toast = useToast();
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!password.trim()) {
      setFormError('请输入管理口令。');
      return;
    }
    setPending(true);
    setFormError('');
    try {
      const result = await adminApi.login(password);
      setToken(result.token);
      setPassword('');
      toast.success('登录成功。');
      onSuccess(result.token);
    } catch (error) {
      const message =
        error instanceof ApiError && error.status === 401
          ? '口令不正确。'
          : errorMessage(error, '登录失败，请稍后重试。');
      setFormError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-brand-50 px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <span className="flex size-12 items-center justify-center rounded-2xl bg-brand text-white shadow-card">
            <ShieldCheck size={24} />
          </span>
          <h1 className="text-lg font-bold text-ink">ClassFix 云端后台</h1>
          <p className="text-[12.5px] text-muted">管理诊断数据、问题库、方案库与规则包</p>
        </div>

        <form onSubmit={submit} className="rounded-2xl border border-line bg-white p-5 shadow-card">
          <h2 className="mb-3 text-[15px] font-semibold text-ink">管理口令</h2>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              type="password"
              autoComplete="current-password"
              autoFocus
              placeholder="输入管理口令"
              aria-label="管理口令"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="sm:flex-1"
            />
            <Button type="submit" variant="primary" icon={KeyRound} loading={pending} className="sm:shrink-0">
              进入
            </Button>
          </div>
          <p className="mt-3 min-h-[20px] text-[12.5px] leading-5 text-bad" aria-live="polite">
            {formError}
          </p>
          <p className="mt-1 text-[12px] leading-5 text-muted">
            口令由服务端环境变量提供；登录状态保存在本机浏览器，退出后需重新输入。
          </p>
        </form>

        <p className="mt-4 text-center text-[11.5px] text-muted">仅限内部使用 · 数据实时来自云端接口</p>
      </div>
    </div>
  );
}
