import { useTranslation } from 'react-i18next';
import { matchRoute, useRouter } from '../router';
import { useAsync } from '../hooks/useAsync';
import { InstanceApi } from '../services/api';

type NavItem = {
  key: string;
  pathPrefix: string;
  href: string;
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
  title?: string;
  isActive?: (path: string) => boolean;
};

export function Sidebar() {
  const { t } = useTranslation();
  const { path, navigate } = useRouter();

  const { data: instancesPage } = useAsync(() => InstanceApi.list({ page: 1, page_size: 50 }), []);
  const firstInstanceId = instancesPage?.items?.[0]?.jiuwenclaw_id;
  const currentInstanceId =
    matchRoute('/instances/:id/policies', path)?.id ?? matchRoute('/instances/:id', path)?.id;
  const policiesTarget = currentInstanceId
    ? `/instances/${currentInstanceId}/policies`
    : firstInstanceId
      ? `/instances/${firstInstanceId}/policies`
      : null;

  const platformItems: NavItem[] = [
    {
      key: 'overview',
      pathPrefix: '/overview',
      href: '/overview',
      label: t('nav.overview'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13h2v8H3v-8zm6-6h2v14H9V7zm6 3h2v11h-2V10zm6-6h2v17h-2V4z" />
        </svg>
      ),
    },
    {
      key: 'topology',
      pathPrefix: '/topology',
      href: '/topology',
      label: t('nav.topology'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <circle cx="6" cy="6" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="12" cy="18" r="2" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 8v3a2 2 0 002 2h8a2 2 0 002-2V8M12 13v3" />
        </svg>
      ),
    },
    {
      key: 'instances',
      pathPrefix: '/instances/',
      href: policiesTarget ?? '/topology',
      disabled: !policiesTarget,
      title: !policiesTarget ? t('policies.noInstanceHint') : undefined,
      isActive: (p) => p.endsWith('/policies'),
      label: t('nav.policies'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      ),
    },
  ];

  const configItems: NavItem[] = [
    {
      key: 'model-templates',
      pathPrefix: '/model-templates',
      href: '/model-templates',
      label: t('nav.modelTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 7l8-4 8 4-8 4-8-4zm0 6l8 4 8-4M4 19l8 4 8-4" />
        </svg>
      ),
    },
    {
      key: 'extension-templates',
      pathPrefix: '/extension-config-templates',
      href: '/extension-config-templates',
      label: t('nav.extensionTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14 4l6 6m0 0l-6 6m6-6H4" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 20h7" />
        </svg>
      ),
    },
  ];

  const renderItem = (item: NavItem) => {
    const active = item.isActive
      ? item.isActive(path)
      : path === item.pathPrefix || path.startsWith(`${item.pathPrefix}/`);
    return (
      <button
        key={item.key}
        onClick={() => {
          if (item.disabled) return;
          navigate(item.href);
        }}
        disabled={item.disabled}
        title={item.title}
        className={`nav-item ${active ? 'active' : ''}`}
      >
        {item.icon}
        {item.label}
      </button>
    );
  };

  const instanceCount = instancesPage?.total ?? instancesPage?.items?.length ?? 0;

  return (
    <aside className="nav flex flex-col">
      <div className="nav-group-title nav-group-title--uppercase">{t('nav.platform')}</div>
      <div className="space-y-1">{platformItems.map(renderItem)}</div>

      <div className="nav-group-title nav-group-title--with-top-gap">{t('nav.config')}</div>
      <div className="space-y-1">{configItems.map(renderItem)}</div>

      <div className="flex-1" />
      <div className="nav-footer">
        <div className="nav-footer__row">
          <span className="nav-footer__label">{t('overview.totalInstances')}</span>
          <span className="nav-footer__value">{instanceCount}</span>
        </div>
        <div className="nav-footer__row">
          <span className="nav-footer__label">manager</span>
          <span className="nav-footer__value">v0.1.0</span>
        </div>
      </div>
    </aside>
  );
}
