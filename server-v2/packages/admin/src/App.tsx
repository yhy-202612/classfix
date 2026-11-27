import {
  BookOpen,
  Bug,
  Database,
  LayoutDashboard,
  Package,
  Settings as SettingsIcon,
  Users,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Layout, type NavItem } from './components/Layout';
import { useToast } from './components/Toast';
import { clearToken, getToken, setUnauthorizedHandler } from './lib/api';
import { CatalogPage } from './pages/CatalogPage';
import { IssuesPage } from './pages/IssuesPage';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { RulesPage } from './pages/RulesPage';
import { SettingsPage } from './pages/SettingsPage';
import { SolutionsPage } from './pages/SolutionsPage';
import { UsersPage } from './pages/UsersPage';

type PageKey = 'overview' | 'users' | 'issues' | 'solutions' | 'catalog' | 'rules' | 'settings';

const NAV: Array<NavItem<PageKey>> = [
  { key: 'overview', label: '概览', icon: LayoutDashboard, description: '云端累计数据与最近诊断' },
  { key: 'users', label: '用户', icon: Users, description: '用户列表、积分与禁用状态' },
  { key: 'issues', label: '问题库', icon: Bug, description: 'AI 诊断提出、离线未覆盖的问题' },
  { key: 'solutions', label: '方案库', icon: BookOpen, description: '方案编辑、复核与发布' },
  { key: 'catalog', label: '离线基线', icon: Database, description: '客户端已覆盖的方案基线' },
  { key: 'rules', label: '规则包', icon: Package, description: '发布规则包并查看打包明细' },
  { key: 'settings', label: '设置', icon: SettingsIcon, description: '自动发布、积分与诊断成本' },
];

const PAGE_KEYS = NAV.map((item) => item.key);

function readHash(): PageKey {
  const raw = window.location.hash.replace(/^#\/?/, '').trim();
  return (PAGE_KEYS as string[]).includes(raw) ? (raw as PageKey) : 'overview';
}

function writeHash(page: PageKey) {
  const next = `#/${page}`;
  if (window.location.hash !== next) window.location.hash = next;
}

export default function App() {
  const toast = useToast();
  const [token, setToken] = useState(() => getToken());
  const [page, setPage] = useState<PageKey>(() => readHash());
  const [refreshKey, setRefreshKey] = useState(0);

  /** 401 统一处理：清 token → 回登录页 */
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken('');
      toast.error('登录已过期，请重新登录。');
    });
    return () => setUnauthorizedHandler(null);
  }, [toast]);

  useEffect(() => {
    const onHashChange = () => setPage(readHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = useCallback((next: PageKey) => {
    setPage(next);
    writeHash(next);
  }, []);

  const handleLogin = useCallback((nextToken: string) => {
    setToken(nextToken);
    setRefreshKey((value) => value + 1);
    navigate('overview');
  }, [navigate]);

  const handleLogout = useCallback(() => {
    clearToken();
    setToken('');
    toast.info('已退出登录。');
  }, [toast]);

  if (!token) return <LoginPage onSuccess={handleLogin} />;

  return (
    <Layout
      nav={NAV}
      active={page}
      onNavigate={navigate}
      onRefresh={() => setRefreshKey((value) => value + 1)}
      onLogout={handleLogout}
      refreshing={false}
    >
      {page === 'overview' ? <OverviewPage refreshKey={refreshKey} /> : null}
      {page === 'users' ? <UsersPage refreshKey={refreshKey} /> : null}
      {page === 'issues' ? <IssuesPage refreshKey={refreshKey} /> : null}
      {page === 'solutions' ? <SolutionsPage refreshKey={refreshKey} /> : null}
      {page === 'catalog' ? <CatalogPage refreshKey={refreshKey} /> : null}
      {page === 'rules' ? <RulesPage refreshKey={refreshKey} /> : null}
      {page === 'settings' ? <SettingsPage refreshKey={refreshKey} /> : null}
    </Layout>
  );
}
