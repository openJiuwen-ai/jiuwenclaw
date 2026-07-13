import { useTranslation } from 'react-i18next';

interface StatusBadgeProps {
  enabled: boolean;
  expired: boolean;
}

// 高保真设计的"运行中/已暂停/运行失败"三态徽标依赖后端 last_run_status（见
// _migration/backend-requests.md #1），本轮用户已确认暂不做，先复用旧面板的
// 启用/禁用（/过期）二态 pill 展示，等后端接口交付后再换成三态圆环徽标。
export default function StatusBadge({ enabled, expired }: StatusBadgeProps) {
  const { t } = useTranslation();
  const label = expired ? t('cron.status.expired') : enabled ? t('cron.status.enabled') : t('cron.status.disabled');
  const className = expired
    ? 'bg-amber-100 text-amber-700'
    : enabled
      ? 'bg-green-100 text-green-700'
      : 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}
