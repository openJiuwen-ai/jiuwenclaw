import type { CronTemplateUI } from '../../types/cron';

// 沿用旧 CronPanel/index.tsx 的时区选项列表
export const TIMEZONE_OPTIONS = [
  'Asia/Shanghai',
  'Asia/Bangkok',
  'Asia/Tokyo',
  'Asia/Seoul',
  'Asia/Singapore',
  'Europe/London',
  'Europe/Paris',
  'America/New_York',
  'America/Los_Angeles',
  'America/Chicago',
];

// 任务模板：后端没有"模板"概念（见 _migration/backend-requests.md），本轮先用前端静态常量，
// 不做后端持久化/用户自定义模板。cron_expr 只是预填的初始值，用户可在"Cron表达式"输入框里改。
// 周字段编号假设 Quartz 惯例 1=周日...7=周六（待验证，见 plan.md §2.3 风险③，不影响本轮功能）。
export const CRON_TEMPLATES: CronTemplateUI[] = [
  {
    id: 'tpl-daily-news',
    icon: 'trend',
    titleKey: 'cron.template.trend.title',
    descriptionKey: 'cron.template.trend.description',
    cronExpr: '0 0 8 * * ? *',
  },
  {
    id: 'tpl-market-watch',
    icon: 'newspaper',
    titleKey: 'cron.template.newspaper.title',
    descriptionKey: 'cron.template.newspaper.description',
    cronExpr: '0 0 12 * * ? *',
  },
  {
    id: 'tpl-weekly-report',
    icon: 'briefcase',
    titleKey: 'cron.template.briefcase.title',
    descriptionKey: 'cron.template.briefcase.description',
    cronExpr: '0 0 18 ? * 6 *',
  },
];
