import { useTranslation } from 'react-i18next';
import { validateCronExpr } from './cronExprValidation';

interface ScheduleEditorProps {
  value: string; // cron_expr 原文
  onChange: (v: string) => void;
}

const DISABLED_MODE_KEYS = ['modePeriod', 'modeInterval', 'modeOnce'] as const;

// 高保真设计的执行计划编辑器本是"周期/按间隔/单次"三种结构化模式，但这三种模式转成后端
// 7段式 cron_expr 涉及的语义问题（每月与每周结构重复、间隔+星期组合、星期编号对齐等，
// 见 _migration/plan.md §2.3）用户已确认本轮不做。改为新增"Cron表达式"tab 直接编辑
// cron_expr 原文，所见即所得，天然兼容任何历史任务；其余三个 tab 保留视觉占位但禁用。
export default function ScheduleEditor({ value, onChange }: ScheduleEditorProps) {
  const { t } = useTranslation();
  const validation = value.trim() ? validateCronExpr(value) : { valid: true };

  return (
    <div className="relative">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-bold text-text-strong">
        {t('cron.schedule.title')}
        <span
          className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-border text-[10px] font-normal text-text-muted cursor-help"
          title={t('cron.schedule.help') ?? undefined}
        >
          ?
        </span>
      </div>

      <div className="mb-3 inline-flex rounded-md bg-bg-muted p-0.5">
        {DISABLED_MODE_KEYS.map((key) => (
          <button
            key={key}
            type="button"
            disabled
            title={t('cron.schedule.modeComingSoon') ?? undefined}
            className="cursor-not-allowed rounded px-4 py-1.5 text-sm text-text-muted/50"
          >
            {t(`cron.schedule.${key}`)}
          </button>
        ))}
        <button
          type="button"
          className="rounded bg-card px-4 py-1.5 text-sm font-bold text-text-strong shadow-sm"
        >
          {t('cron.schedule.modeCronExpr')}
        </button>
      </div>

      <div className="relative">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('cron.schedule.cronExprPlaceholder') ?? undefined}
          className={`w-full rounded-md border bg-card px-3 py-1.5 pr-8 text-sm text-text outline-none mono ${
            !validation.valid ? 'border-danger' : 'border-border focus:border-accent'
          }`}
        />
        {/* 沿用旧 CronPanel 的问号提示图标与 cron.placeholders.cron 长文案 key（未改动） */}
        <span
          className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text cursor-help"
          title={t('cron.placeholders.cron') ?? undefined}
        >
          <svg width="16" height="16" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
            <circle cx="20" cy="20" r="18" fill="transparent" stroke="currentColor" strokeWidth="2" />
            <text x="20" y="22" fontSize="24" fill="currentColor" textAnchor="middle" dominantBaseline="middle">?</text>
          </svg>
        </span>
      </div>
      {!validation.valid && (
        <p className="mt-1 text-xs text-danger">{t(validation.error || 'cron.errors.cronFormat')}</p>
      )}
    </div>
  );
}
