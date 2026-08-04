import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
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
      key: 'instances',
      pathPrefix: '/instances',
      href: '/instances',
      label: t('nav.instances'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <circle cx="6" cy="6" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="12" cy="18" r="2" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 8v3a2 2 0 002 2h8a2 2 0 002-2V8M12 13v3" />
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
      key: 'embedding-templates',
      pathPrefix: '/embedding-templates',
      href: '/embedding-templates',
      label: t('nav.embeddingTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <circle cx="6" cy="12" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="18" cy="18" r="2" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 11l8-4M8 13l8 4" />
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
    {
      key: 'skill-whitelist-templates',
      pathPrefix: '/skill-whitelist-templates',
      href: '/skill-whitelist-templates',
      label: t('nav.skillWhitelistTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      ),
    },
    {
      key: 'service-config-templates',
      pathPrefix: '/service-config-templates',
      href: '/service-config-templates',
      label: t('nav.serviceConfigTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
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
