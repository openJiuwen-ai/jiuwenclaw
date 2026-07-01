import { Component, ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Sidebar } from './components/Sidebar';
import { Toaster } from './components/Toaster';
import { ThemeToggle } from './components/ThemeToggle';
import { LanguageSwitcher } from './components/LanguageSwitcher';
import { OverviewPage } from './pages/OverviewPage';
import { InstanceListPage } from './pages/instance/InstanceListPage';
import { InstanceDetailPage } from './pages/instance/InstanceDetailPage';
import { ModelTemplatesPage } from './pages/templates/ModelTemplatesPage';
import { ExtensionTemplatesPage } from './pages/templates/ExtensionTemplatesPage';
import { SkillWhitelistTemplatesPage } from './pages/templates/SkillWhitelistTemplatesPage';
import { ServiceConfigTemplatesPage } from './pages/templates/ServiceConfigTemplatesPage';
import { matchRoute, RouterProvider, useRouter } from './router';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { UsersPage } from './pages/iam/UsersPage';
import { OrgsPage } from './pages/iam/OrgsPage';
import { BotsPage } from './pages/iam/BotsPage';
import { UserConsole } from './pages/user/UserConsole';

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('React Error:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-screen p-8">
          <div className="card max-w-xl">
            <div className="text-lg font-semibold text-danger mb-2">Application Error</div>
            <pre className="text-xs mono whitespace-pre-wrap text-muted">
              {this.state.error?.stack ?? this.state.error?.message ?? 'unknown'}
            </pre>
            <button className="btn primary mt-3" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function RouteView() {
  const { path } = useRouter();

  if (path === '/overview' || path === '/') {
    return <OverviewPage />;
  }
  if (path === '/instances' || path === '/topology') {
    return <InstanceListPage />;
  }
  if (path === '/model-templates') {
    return <ModelTemplatesPage />;
  }
  if (path === '/extension-config-templates') {
    return <ExtensionTemplatesPage />;
  }
  if (path === '/skill-whitelist-templates') {
    return <SkillWhitelistTemplatesPage />;
  }
  if (path === '/service-config-templates') {
    return <ServiceConfigTemplatesPage />;
  }
  if (path === '/users') {
    return <UsersPage />;
  }
  if (path === '/orgs') {
    return <OrgsPage />;
  }
  if (path === '/bots') {
    return <BotsPage />;
  }
  const instancePolicies = matchRoute('/instances/:id/policies', path);
  if (instancePolicies) {
    return <InstanceDetailPage instanceId={instancePolicies.id} tab="policies" />;
  }
  const instanceConfig = matchRoute('/instances/:id/config', path);
  if (instanceConfig) {
    return <InstanceDetailPage instanceId={instanceConfig.id} tab="config" />;
  }
  const detail = matchRoute('/instances/:id', path);
  if (detail) {
    return <InstanceDetailPage instanceId={detail.id} tab="detail" />;
  }
  return <OverviewPage />;
}

function Shell() {
  const { t } = useTranslation();
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img src="/logo.png" alt="JiuwenClaw" className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">
              {t('brand.title')}
              <span className="brand-version">v0.1.0</span>
            </span>
            <span className="brand-sub">{t('brand.sub')}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <UserMenu />
        </div>
      </header>
      <Sidebar />
      <main className="content">
        <RouteView />
      </main>
      <Toaster />
    </div>
  );
}

function UserMenu() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted">
        {user.display_name}
        <span className="badge ml-1">{user.is_admin ? t('iam.roleAdmin') : t('iam.roleUser')}</span>
      </span>
      <button className="btn" onClick={() => void logout()}>{t('auth.logout')}</button>
    </div>
  );
}

/** 已登录用户的默认落地页:管理员→/manager,普通用户→/user。 */
function roleHome(isAdmin: boolean): string {
  return isAdmin ? '/manager' : '/user';
}

/** /auth:已登录则按角色跳走,否则展示登录页。 */
function AuthRoute() {
  const { user } = useAuth();
  if (user) return <Navigate to={roleHome(user.is_admin)} replace />;
  return <LoginPage />;
}

/** 登录 + 角色守卫:未登录→/auth;要求 admin 但非 admin→/user。 */
function RequireAuth({ admin, children }: { admin?: boolean; children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/auth" replace />;
  if (admin && !user.is_admin) return <Navigate to="/user" replace />;
  return <>{children}</>;
}

/** 根/未知路径:按登录态与角色重定向。 */
function RootRedirect() {
  const { user } = useAuth();
  return <Navigate to={user ? roleHome(user.is_admin) : '/auth'} replace />;
}

function Gate() {
  const { t } = useTranslation();
  const { ready } = useAuth();
  if (!ready) {
    return <div className="flex items-center justify-center h-screen text-muted">{t('auth.loading')}</div>;
  }
  return (
    <Routes>
      {/* 认证面 */}
      <Route path="/auth" element={<AuthRoute />} />
      {/* 管理面(admin):内部页面在 /manager basename 下,既有页面零改动 */}
      <Route
        path="/manager/*"
        element={
          <RequireAuth admin>
            <RouterProvider basename="/manager">
              <Shell />
            </RouterProvider>
          </RequireAuth>
        }
      />
      {/* 用户面 */}
      <Route path="/user/*" element={<RequireAuth><UserConsole /></RequireAuth>} />
      {/* 根/未知 → 按角色落地 */}
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </ErrorBoundary>
  );
}
