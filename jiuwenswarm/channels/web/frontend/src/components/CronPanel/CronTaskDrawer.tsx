import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Pencil } from 'lucide-react';
import ScheduleEditor from './ScheduleEditor';
import ModelPicker from './ModelPicker';
import DatePicker from './DatePicker';
import SimpleSelect from './SimpleSelect';
import TemplateClusterIcon from './TemplateClusterIcon';
import { validateCronExpr } from './cronExprValidation';
import { cronExprToSchedule, isOnceScheduleExpired } from './scheduleConvert';
import { TIMEZONE_OPTIONS } from './constants';
import type { CronTaskUI, CronTemplateUI } from '../../types/cron';
import type { ProjectInfo } from '../../features/workspace/projectTypes';
import { getProjectDisplayName } from '../../stores/workspaceStore';

// "生效周期"依赖后端 effective_from/effective_until（见 backend-requests.md 需求3），目前后端还
// 没有这个概念，选了也不下发。之前的方案是保留字段但标注"即将上线"，用户后来觉得不如先整个隐藏，
// 等后端接口交付后再打开——代码/state（`CronTaskFormValue.effectiveDate`）都保留，不用重写
const CRON_EFFECTIVE_DATE_UI_ENABLED = false;

export interface CronTaskFormValue {
  name: string;
  projectDir: string | null; // 仅创建/模板创建模式使用；编辑模式不展示项目字段，不参与提交
  modelName: string | null;
  description: string;
  targets: string; // 推送频道，对应后端 CronJob.targets
  cronExpr: string;
  timezone: string;
  effectiveDate: string | null; // 【backend-requests.md #3】仅前端展示，不下发
  enabled: boolean;
}

function emptyForm(): CronTaskFormValue {
  return {
    name: '',
    projectDir: null,
    modelName: null,
    description: '',
    targets: 'web',
    cronExpr: '',
    timezone: 'Asia/Shanghai',
    effectiveDate: null,
    enabled: true,
  };
}

export function jobToForm(job: CronTaskUI): CronTaskFormValue {
  return {
    name: job.name,
    projectDir: null,
    modelName: job.modelName,
    description: job.description,
    targets: job.deliveryChannel,
    cronExpr: job.cronExpr,
    timezone: job.timezone,
    effectiveDate: null,
    enabled: job.enabled,
  };
}

export function templateToForm(tpl: CronTemplateUI, title: string, description: string): CronTaskFormValue {
  return {
    name: title,
    projectDir: null,
    modelName: null,
    description,
    targets: 'web',
    cronExpr: tpl.cronExpr,
    timezone: 'Asia/Shanghai',
    effectiveDate: null,
    enabled: true,
  };
}

interface CronTaskDrawerProps {
  mode: 'create' | 'edit' | 'template';
  initial?: CronTaskFormValue;
  projects: ProjectInfo[];
  targetOptions: { value: string; label: string; disabled?: boolean }[];
  // 主动推荐自动维护的 job（proactive-tick-auto）编辑时锁定：只能改执行计划(cron表达式)和时区，
  // 其余字段（名称/模型/描述/推送频道/启用）由 ConfigPanel/cron_sync 管理，只读展示
  // （沿用 upstream 提交 59cf6de7 的约束，见 index.tsx handleEditSubmit）
  proactiveLocked?: boolean;
  onClose: () => void;
  onSubmit: (value: CronTaskFormValue) => void;
  onSwitchToManual?: () => void;
  onSwitchToTemplate?: () => void;
}

const fieldClass = 'w-full rounded-md border border-border bg-card px-3 py-1.5 text-sm text-text outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-50';

export default function CronTaskDrawer({ mode, initial, projects, targetOptions, proactiveLocked = false, onClose, onSubmit, onSwitchToManual, onSwitchToTemplate }: CronTaskDrawerProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<CronTaskFormValue>(initial ?? emptyForm());

  const title = mode === 'edit' ? t('cron.drawer.titleEdit') : mode === 'template' ? t('cron.drawer.titleTemplate') : t('cron.drawer.titleCreate');
  const projectOptions = projects.map((p) => ({ value: p.project_dir, label: getProjectDisplayName(p) }));
  const timezoneOptions = TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }));
  // 必填项缺失时，收集清单用来在"确定"按钮旁给出具体提示（而不是只让按钮变灰、不说原因）
  const missingFieldLabels: string[] = [];
  if (!form.name.trim()) missingFieldLabels.push(t('cron.drawer.fieldName'));
  if (!form.description.trim()) missingFieldLabels.push(t('cron.drawer.fieldDescription'));
  if (!form.cronExpr.trim() || !validateCronExpr(form.cronExpr).valid) missingFieldLabels.push(t('cron.schedule.title'));
  // "单次"排班选的日期时间已经过去：字段本身不是"没填"，属于另一类校验失败，
  // 单独提示（而不是塞进"还需要填写"的缺失字段列表里，语义对不上）
  const parsedSchedule = form.cronExpr.trim() ? cronExprToSchedule(form.cronExpr) : null;
  const scheduleAlreadyExpired = parsedSchedule ? isOnceScheduleExpired(parsedSchedule, form.timezone) : false;
  const canSubmit = missingFieldLabels.length === 0 && !scheduleAlreadyExpired;
  const missingFieldsHint = missingFieldLabels.length > 0
    ? t('cron.drawer.missingFieldsHint', { fields: missingFieldLabels.join(t('cron.schedule.listSeparator')) })
    : scheduleAlreadyExpired
      ? t('cron.drawer.scheduleAlreadyExpiredHint')
      : undefined;
  const lockedTitle = proactiveLocked ? t('cron.autoManagedToggleDisabled') ?? undefined : undefined;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-overlay-cron-drawer" onClick={onClose}>
      <div
        className="relative flex h-full w-[560px] flex-col overflow-y-auto bg-card p-6 shadow-xl animate-slide-in-right"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-6 flex items-center justify-between">
          <h3 className="text-2xl font-bold text-text-strong">{title}</h3>
          <div className="flex items-center gap-3">
            {mode === 'template' && onSwitchToManual && (
              <button
                type="button"
                onClick={onSwitchToManual}
                className="flex items-center gap-1 text-sm text-text hover:opacity-70"
              >
                <Pencil size={14} /> {t('cron.drawer.switchToManual')}
              </button>
            )}
            {mode === 'create' && onSwitchToTemplate && (
              <button
                type="button"
                onClick={onSwitchToTemplate}
                className="flex items-center gap-1 text-sm text-text hover:opacity-70"
              >
                <TemplateClusterIcon size={14} /> {t('cron.drawer.switchToTemplate')}
              </button>
            )}
            <button onClick={onClose} className="text-text-muted hover:text-text">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-5">
          {proactiveLocked && (
            <p className="rounded-md bg-bg-muted px-3 py-2 text-xs text-text-muted">{t('cron.autoManagedHint')}</p>
          )}

          <div>
            <label className="mb-1.5 block text-sm font-bold text-text-strong">
              {t('cron.drawer.fieldName')} <span className="text-danger">*</span>
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t('cron.drawer.placeholderInput') ?? undefined}
              disabled={proactiveLocked}
              title={lockedTitle}
              className={fieldClass}
            />
          </div>

          {mode !== 'edit' && (
            <div>
              <label className="mb-1.5 block text-sm font-bold text-text-strong">{t('cron.drawer.fieldProject')}</label>
              <SimpleSelect
                value={form.projectDir ?? ''}
                onChange={(v) => setForm({ ...form, projectDir: v || null })}
                options={projectOptions}
                placeholder={t('cron.drawer.placeholderSelect') ?? undefined}
              />
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-sm font-bold text-text-strong">{t('cron.drawer.fieldModel')}</label>
            <ModelPicker value={form.modelName} onChange={(modelName) => setForm({ ...form, modelName })} disabled={proactiveLocked} />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-bold text-text-strong">
              {t('cron.drawer.fieldDescription')} <span className="text-danger">*</span>
            </label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={t('cron.drawer.placeholderInput') ?? undefined}
              rows={4}
              disabled={proactiveLocked}
              title={lockedTitle}
              className={`${fieldClass} resize-none`}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-bold text-text-strong">{t('cron.drawer.fieldChannel')}</label>
            <SimpleSelect
              value={form.targets}
              onChange={(v) => setForm({ ...form, targets: v })}
              options={targetOptions}
              disabled={proactiveLocked}
            />
          </div>

          <ScheduleEditor value={form.cronExpr} onChange={(cronExpr) => setForm({ ...form, cronExpr })} timezone={form.timezone} />

          <div>
            <label className="mb-1.5 block text-sm font-bold text-text-strong">{t('cron.drawer.fieldTimezone')}</label>
            <SimpleSelect
              value={form.timezone}
              onChange={(v) => setForm({ ...form, timezone: v })}
              options={timezoneOptions}
            />
          </div>

          {CRON_EFFECTIVE_DATE_UI_ENABLED && (
            <div>
              <label className="mb-1.5 block text-sm font-bold text-text-strong">{t('cron.drawer.fieldEffectiveDate')}</label>
              <DatePicker
                value={form.effectiveDate ?? ''}
                onChange={(v) => setForm({ ...form, effectiveDate: v || null })}
                placeholder={t('cron.drawer.placeholderEffectiveDate') ?? undefined}
              />
              <p className="mt-1 text-xs text-text-muted">{t('cron.drawer.effectiveDateComingSoon')}</p>
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={proactiveLocked}
              title={lockedTitle}
              onClick={() => setForm({ ...form, enabled: !form.enabled })}
              className={`inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${form.enabled ? 'bg-accent' : 'bg-border-strong'}`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-card transition-transform ${form.enabled ? 'translate-x-6' : 'translate-x-1'}`}
              />
            </button>
            <span className="text-sm font-bold text-text">
              {form.enabled ? t('cron.status.enabled') : t('cron.status.disabled')}
            </span>
          </div>
        </div>

        <div className="mt-8 flex justify-center gap-3">
          <button
            onClick={() => onSubmit(form)}
            disabled={!canSubmit}
            title={missingFieldsHint}
            className="rounded-full bg-cron-action px-10 py-1.5 text-sm font-bold text-cron-action-foreground hover:bg-cron-action-hover disabled:opacity-50"
          >
            {t('cron.actions.confirm')}
          </button>
          <button
            onClick={onClose}
            className="rounded-full border border-border bg-card px-10 py-1.5 text-sm font-bold text-text hover:bg-bg-hover"
          >
            {t('common.cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}
