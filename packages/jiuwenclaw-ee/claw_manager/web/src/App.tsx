import { Component, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Sidebar } from './components/Sidebar';
import { Toaster } from './components/Toaster';
import { ThemeToggle } from './components/ThemeToggle';
import { LanguageSwitcher } from './components/LanguageSwitcher';
import { getProductName } from './utils/env';
import { OverviewPage } from './pages/OverviewPage';
import { InstanceListPage } from './pages/instance/InstanceListPage';
import { InstanceDetailPage } from './pages/instance/InstanceDetailPage';
import { ModelTemplatesPage } from './pages/templates/ModelTemplatesPage';
import { ExtensionTemplatesPage } from './pages/templates/ExtensionTemplatesPage';
import { SkillWhitelistTemplatesPage } from './pages/templates/SkillWhitelistTemplatesPage';
import { ServiceConfigTemplatesPage } from './pages/templates/ServiceConfigTemplatesPage';
import { matchRoute, RouterProvider, useRouter } from './router';

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
          <img src="/logo.png" alt={getProductName()} className="brand-logo-img" />
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

export default function App() {
  return (
    <ErrorBoundary>
      <RouterProvider>
        <Shell />
      </RouterProvider>
    </ErrorBoundary>
  );
}
