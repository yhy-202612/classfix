import type { AdminUser, AdminUserListRes } from '@classfix/shared';
import { Ban, Search, ShieldCheck, Users } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Input } from '../components/Field';
import { ResponsiveTable, type Column } from '../components/ResponsiveTable';
import { EmptyState, ErrorState, LoadingBlock } from '../components/States';
import { Tag } from '../components/Tag';
import { useToast } from '../components/Toast';
import { adminApi } from '../lib/api';
import { errorMessage, fmtDateTime, maskPhone } from '../lib/format';
import { useResource } from '../lib/hooks';

export function UsersPage({ refreshKey }: { refreshKey: number }) {
  const toast = useToast();
  const { data, loading, error, reload } = useResource<AdminUserListRes>(() => adminApi.users(), [refreshKey]);
  const [keyword, setKeyword] = useState('');
  const [busyId, setBusyId] = useState('');

  const items = useMemo(() => data?.items ?? [], [data]);
  const filtered = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    if (!query) return items;
    return items.filter(
      (user) => user.id.toLowerCase().includes(query) || user.phone.toLowerCase().includes(query),
    );
  }, [items, keyword]);

  async function toggleBan(user: AdminUser) {
    const next = !user.banned;
    setBusyId(user.id);
    try {
      await adminApi.banUser(user.id, next);
      toast.success(next ? `已禁用 ${maskPhone(user.phone)}。` : `已解除 ${maskPhone(user.phone)} 的禁用。`);
      await reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId('');
    }
  }

  const columns: Array<Column<AdminUser>> = [
    {
      key: 'id',
      header: 'ID',
      primary: true,
      render: (user) => <span className="break-all font-mono text-[12px] text-ink">{user.id}</span>,
    },
    {
      key: 'phone',
      header: '手机号',
      render: (user) => <span className="font-mono">{maskPhone(user.phone)}</span>,
    },
    {
      key: 'points',
      header: '积分',
      render: (user) => <span className="font-semibold text-brand">{user.points}</span>,
    },
    {
      key: 'createdAt',
      header: '注册时间',
      render: (user) => <span className="text-muted">{fmtDateTime(user.createdAt)}</span>,
    },
    {
      key: 'lastLoginAt',
      header: '最近登录',
      render: (user) => <span className="text-muted">{fmtDateTime(user.lastLoginAt)}</span>,
    },
    { key: 'diagnoses', header: '诊断次数', render: (user) => user.diagnoses },
    {
      key: 'status',
      header: '状态',
      render: (user) => <Tag tone={user.banned ? 'bad' : 'good'}>{user.banned ? '已禁用' : '正常'}</Tag>,
    },
  ];

  return (
    <Card
      title="用户"
      subtitle={data ? `共 ${items.length} 个用户，手机号已打码` : '手机号已打码展示'}
      actions={
        <div className="relative w-full sm:w-[240px]">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            className="pl-8"
            placeholder="搜索 ID / 手机号"
            aria-label="搜索用户"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
        </div>
      }
    >
      {error ? (
        <ErrorState message={error} onRetry={() => void reload()} />
      ) : loading && !data ? (
        <LoadingBlock />
      ) : (
        <ResponsiveTable
          columns={columns}
          rows={filtered}
          getKey={(user) => user.id}
          empty={
            items.length ? (
              <EmptyState icon={Search} title="没有匹配的用户" description="换个关键字试试。" />
            ) : (
              <EmptyState
                icon={Users}
                title="还没有用户"
                description="客户端首次用手机号验证码登录后，会自动创建用户。"
              />
            )
          }
          actions={(user) => (
            <Button
              size="sm"
              variant={user.banned ? 'secondary' : 'danger'}
              icon={user.banned ? ShieldCheck : Ban}
              loading={busyId === user.id}
              onClick={() => void toggleBan(user)}
            >
              {user.banned ? '解除禁用' : '禁用'}
            </Button>
          )}
        />
      )}
    </Card>
  );
}
