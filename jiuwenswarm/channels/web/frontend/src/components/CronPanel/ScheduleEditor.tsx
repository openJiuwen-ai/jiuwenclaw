import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import SimpleSelect from './SimpleSelect';
import TimePicker from './TimePicker';
import DatePicker from './DatePicker';
import { validateCronExpr } from './cronExprValidation';
import { scheduleToCronExpr, cronExprToSchedule } from './scheduleConvert';
import type { CronSchedule, CronScheduleKind } from '../../types/cron';

interface ScheduleEditorProps {
  value: string; // cron_expr 原文，唯一提交给后端的数据
  onChange: (v: string) => void;
}

type TopMode = 'period' | 'interval' | 'once' | 'cronExpr';

const PERIOD_KINDS: Extract<CronScheduleKind, 'daily' | 'weekly' | 'monthly' | 'yearly'>[] = [
  'daily', 'weekly', 'monthly', 'yearly',
];

// croniter 实测的真实星期编号：0=周日...6=周六（见 plan.md §2.3.1），按钮显示顺序用"一二三四五六日"
const WEEKDAY_ITEMS: { value: number; key: string }[] = [
  { value: 1, key: 'mon' }, { value: 2, key: 'tue' }, { value: 3, key: 'wed' },
  { value: 4, key: 'thu' }, { value: 5, key: 'fri' }, { value: 6, key: 'sat' },
  { value: 0, key: 'sun' },
];

// "每月第几周"选项：不提供"第五周"（croniter 的 #5 在没有第5次出现的月份会整月跳过，不可靠，
// 见 plan.md §2.3.8 第1点），只给第一~四周 + 最后一周（后者是独立的 L{dow} 语法，行为可靠）
const WEEK_OF_MONTH_OPTIONS: { value: string; key: string }[] = [
  { value: '1', key: '1' }, { value: '2', key: '2' }, { value: '3', key: '3' }, { value: '4', key: '4' },
  { value: 'L', key: 'last' },
];

function topModeOf(kind: CronScheduleKind): TopMode {
  if (kind === 'interval') return 'interval';
  if (kind === 'once') return 'once';
  return 'period';
}

function defaultForTopMode(mode: 'period' | 'interval' | 'once'): CronSchedule {
  if (mode === 'interval') return { kind: 'interval', everyHours: 1 };
  if (mode === 'once') return { kind: 'once', time: '', date: '' };
  return { kind: 'daily', time: '' };
}

function everyHoursTextOf(schedule: CronSchedule): string {
  return schedule.kind === 'interval' && schedule.everyHours !== undefined
    ? String(schedule.everyHours)
    : '';
}

function WeekdayPicker({ selected, onToggle }: { selected: number[]; onToggle: (day: number) => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-1 gap-1.5">
      {WEEKDAY_ITEMS.map(({ value, key }) => {
        const active = selected.includes(value);
        return (
          <button
            key={value}
            type="button"
            onClick={() => onToggle(value)}
            className={`h-9 min-w-0 flex-1 rounded-md border text-sm transition-colors ${
              active
                ? 'border-accent bg-accent-subtle text-accent'
                : 'border-border bg-card text-text hover:border-border-strong'
            }`}
          >
            {t(`cron.schedule.weekday.${key}`)}
          </button>
        );
      })}
    </div>
  );
}

// 高保真设计的执行计划编辑器有 4 个 tab：周期/按间隔/单次/Cron表达式。前 3 个是结构化编辑，
// 最后一个是直接编辑 cron_expr 原文的兜底/高级模式（编辑任务时若原表达式无法结构化识别，
// 或用户手动切到这个 tab，都以它为准，见 scheduleConvert.ts 的反向解析策略）。
export default function ScheduleEditor({ value, onChange }: ScheduleEditorProps) {
  const { t } = useTranslation();
  const initialParsed = cronExprToSchedule(value);
  const initialSchedule = initialParsed ?? { kind: 'daily', time: '' };
  const [schedule, setSchedule] = useState<CronSchedule>(initialSchedule);
  // 默认 tab：能解析出结构化 schedule 就跟它走；解析不出来时，创建任务（value 为空）默认落在
  // "周期"而不是"Cron表达式"（更符合大多数人的心智，表达式 tab 留给"手写/编辑一条解析不了的旧
  // 表达式"这种进阶场景）；编辑一条解析不出来的已有表达式（value 非空但 parse 失败）则仍然落在
  // "Cron表达式"tab，让用户能看到并编辑原文，不能默默换成一个和原表达式无关的默认周期排班。
  const [topMode, setTopMode] = useState<TopMode>(
    initialParsed ? topModeOf(initialParsed.kind) : value.trim() ? 'cronExpr' : 'period',
  );
  // "按间隔"小时数输入框单独存一份文本状态：如果直接把 <input> 的 value 绑定到 schedule.everyHours
  // （number 类型），每次 onChange 都会把输入值转成数字再写回 schedule，一旦用户刚打出的是"5."这种
  // 还没打完小数部分的中间态，Number("5.") 会被转成 5，进而把输入框的 value 强制改回"5"，把用户刚
  // 敲的小数点瞬间吞掉，导致小数点根本打不进去。用独立的文本状态只做"显示"，只有能解析成数字时才
  // 同步进 schedule，从根上避免这种"受控输入反噬"。
  const [everyHoursText, setEveryHoursText] = useState(() => everyHoursTextOf(initialSchedule));

  const validation = value.trim() ? validateCronExpr(value) : { valid: true };

  function updateSchedule(next: CronSchedule) {
    setSchedule(next);
    onChange(scheduleToCronExpr(next));
  }

  function switchTopMode(mode: 'period' | 'interval' | 'once') {
    if (mode === topMode) return; // 已经在这个 tab 上，不要用当前（可能不完整）的 value 重新解析覆盖
    const parsed = cronExprToSchedule(value);
    const next = parsed && topModeOf(parsed.kind) === mode ? parsed : defaultForTopMode(mode);
    setTopMode(mode);
    if (mode === 'interval') setEveryHoursText(everyHoursTextOf(next));
    updateSchedule(next);
  }

  function setPeriodKind(kind: Extract<CronScheduleKind, 'daily' | 'weekly' | 'monthly' | 'yearly'>) {
    const time = 'time' in schedule ? schedule.time ?? '' : '';
    if (kind === 'daily') updateSchedule({ kind: 'daily', time });
    else if (kind === 'weekly') updateSchedule({ kind: 'weekly', time, weekdays: [] });
    // 从其它类型切到"每月"，默认停在"按日期"子模式（不管切之前是不是"按星期"）
    else if (kind === 'monthly') updateSchedule({ kind: 'monthly', time, day: 1 });
    else updateSchedule({ kind: 'yearly', time, month: 1, day: 1 });
  }

  // "每月"内部的"按日期/按星期"二级切换；只在 schedule.kind 是 monthly/monthlyWeekday 时会被调用
  function setMonthlySubMode(mode: 'date' | 'week') {
    const time = schedule.time ?? '';
    if (mode === 'date') updateSchedule({ kind: 'monthly', time, day: 1 });
    else updateSchedule({ kind: 'monthlyWeekday', time, weekOfMonth: 1, weekdays: [] });
  }

  function toggleWeekday(day: number) {
    if (schedule.kind !== 'weekly' && schedule.kind !== 'interval' && schedule.kind !== 'monthlyWeekday') return;
    const current = schedule.weekdays ?? [];
    const next = current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort((a, b) => a - b);
    updateSchedule({ ...schedule, weekdays: next });
  }

  const dayOptions = [
    ...Array.from({ length: 31 }, (_, i) => ({ value: String(i + 1), label: t('cron.schedule.dayOption', { day: i + 1 }) })),
    { value: 'L', label: t('cron.schedule.lastDayOfMonth') },
  ];
  const monthOptions = Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: t('cron.schedule.monthOption', { month: i + 1 }) }));
  const periodKindOptions = PERIOD_KINDS.map((k) => ({ value: k, label: t(`cron.schedule.${k}`) }));
  const weekOfMonthOptions = WEEK_OF_MONTH_OPTIONS.map((o) => ({ value: o.value, label: t(`cron.schedule.weekOfMonth.${o.key}`) }));

  return (
    <div className="relative">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-bold text-text-strong">
        {t('cron.schedule.title')} <span className="text-danger">*</span>
        <span
          className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-border text-[10px] font-normal text-text-muted cursor-help"
          title={t('cron.schedule.help') ?? undefined}
        >
          ?
        </span>
      </div>

      <div className="mb-3 inline-flex rounded-md bg-bg-muted p-0.5">
        {(['period', 'interval', 'once'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => switchTopMode(mode)}
            className={`rounded px-4 py-1.5 text-sm transition-colors ${
              topMode === mode ? 'bg-card font-bold text-text-strong shadow-sm' : 'text-text-muted hover:text-text'
            }`}
          >
            {t(`cron.schedule.mode${mode === 'period' ? 'Period' : mode === 'interval' ? 'Interval' : 'Once'}`)}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setTopMode('cronExpr')}
          className={`rounded px-4 py-1.5 text-sm transition-colors ${
            topMode === 'cronExpr' ? 'bg-card font-bold text-text-strong shadow-sm' : 'text-text-muted hover:text-text'
          }`}
        >
          {t('cron.schedule.modeCronExpr')}
        </button>
      </div>

      {topMode === 'period' && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-nowrap items-center gap-2">
            <SimpleSelect
              value={schedule.kind === 'interval' || schedule.kind === 'once' ? 'daily' : schedule.kind === 'monthlyWeekday' ? 'monthly' : schedule.kind}
              onChange={(v) => setPeriodKind(v as Extract<CronScheduleKind, 'daily' | 'weekly' | 'monthly' | 'yearly'>)}
              options={periodKindOptions}
              className="w-24 shrink-0"
            />

            {/* "每月"的二级切换：按日期（已有）/ 按星期（"每月第几周星期几"，见 plan.md §2.3.8），
                跟周期细分选择器放同一行，节省纵向空间 */}
            {(schedule.kind === 'monthly' || schedule.kind === 'monthlyWeekday') && (
              <div className="inline-flex w-fit shrink-0 rounded-md bg-bg-muted p-0.5">
                {(['date', 'week'] as const).map((subMode) => {
                  const active = subMode === 'date' ? schedule.kind === 'monthly' : schedule.kind === 'monthlyWeekday';
                  return (
                    <button
                      key={subMode}
                      type="button"
                      onClick={() => setMonthlySubMode(subMode)}
                      className={`rounded px-3 py-1 text-xs transition-colors ${
                        active ? 'bg-card font-bold text-text-strong shadow-sm' : 'text-text-muted hover:text-text'
                      }`}
                    >
                      {t(`cron.schedule.monthlySubMode.${subMode}`)}
                    </button>
                  );
                })}
              </div>
            )}

            {schedule.kind === 'weekly' && (
              <>
                <WeekdayPicker selected={schedule.weekdays ?? []} onToggle={toggleWeekday} />
                <TimePicker
                  value={schedule.time ?? ''}
                  onChange={(v) => updateSchedule({ ...schedule, time: v })}
                  className="w-28 shrink-0"
                  align="right"
                  placeholder={t('cron.schedule.selectTime') ?? undefined}
                />
              </>
            )}

            {schedule.kind === 'yearly' && (
              <>
                <SimpleSelect
                  value={schedule.month ? String(schedule.month) : ''}
                  onChange={(v) => updateSchedule({ ...schedule, month: Number(v) })}
                  options={monthOptions}
                  placeholder={t('cron.schedule.selectMonth') ?? undefined}
                  className="min-w-0 flex-1"
                />
                <SimpleSelect
                  value={schedule.day !== undefined ? String(schedule.day) : ''}
                  onChange={(v) => updateSchedule({ ...schedule, day: Number(v) })}
                  options={dayOptions.filter((o) => o.value !== 'L')}
                  placeholder={t('cron.schedule.selectDay') ?? undefined}
                  className="min-w-0 flex-1"
                />
                <TimePicker
                  value={schedule.time ?? ''}
                  onChange={(v) => updateSchedule({ ...schedule, time: v })}
                  className="min-w-0 flex-1"
                  align="right"
                  placeholder={t('cron.schedule.selectTime') ?? undefined}
                />
              </>
            )}

            {schedule.kind === 'daily' && (
              <TimePicker
                value={schedule.time ?? ''}
                onChange={(v) => updateSchedule({ ...schedule, time: v })}
                className="min-w-0 flex-1"
                placeholder={t('cron.schedule.selectTime') ?? undefined}
              />
            )}
          </div>

          {(schedule.kind === 'monthly' || schedule.kind === 'monthlyWeekday') && (
            <div className="flex flex-nowrap items-center gap-2">
              {schedule.kind === 'monthly' && (
                <>
                  <SimpleSelect
                    value={schedule.day !== undefined ? String(schedule.day) : ''}
                    onChange={(v) => updateSchedule({ ...schedule, day: v === 'L' ? 'L' : Number(v) })}
                    options={dayOptions}
                    placeholder={t('cron.schedule.selectDay') ?? undefined}
                    className="min-w-0 flex-1"
                  />
                  <TimePicker
                    value={schedule.time ?? ''}
                    onChange={(v) => updateSchedule({ ...schedule, time: v })}
                    className="min-w-0 flex-1"
                    align="right"
                    placeholder={t('cron.schedule.selectTime') ?? undefined}
                  />
                </>
              )}

              {schedule.kind === 'monthlyWeekday' && (
                <>
                  <SimpleSelect
                    value={schedule.weekOfMonth !== undefined ? String(schedule.weekOfMonth) : ''}
                    onChange={(v) => updateSchedule({ ...schedule, weekOfMonth: v === 'L' ? 'L' : Number(v) })}
                    options={weekOfMonthOptions}
                    className="w-24 shrink-0"
                  />
                  <WeekdayPicker selected={schedule.weekdays ?? []} onToggle={toggleWeekday} />
                  <TimePicker
                    value={schedule.time ?? ''}
                    onChange={(v) => updateSchedule({ ...schedule, time: v })}
                    className="w-28 shrink-0"
                    align="right"
                    placeholder={t('cron.schedule.selectTime') ?? undefined}
                  />
                </>
              )}
            </div>
          )}
        </div>
      )}

      {topMode === 'interval' && schedule.kind === 'interval' && (
        <div className="flex flex-nowrap items-center gap-2">
          <span className="shrink-0 text-sm text-text-muted">{t('cron.schedule.every')}</span>
          <input
            type="number"
            step="any"
            value={everyHoursText}
            onChange={(e) => {
              const raw = e.target.value;
              setEveryHoursText(raw);
              if (raw.trim() === '') {
                updateSchedule({ ...schedule, everyHours: undefined });
                return;
              }
              const n = Number(raw);
              if (!Number.isNaN(n)) updateSchedule({ ...schedule, everyHours: n });
            }}
            placeholder={t('cron.schedule.everyHoursPlaceholder') ?? undefined}
            className="w-16 shrink-0 rounded-md border border-border bg-card px-2 py-1.5 text-sm text-text outline-none focus:border-accent"
          />
          <span className="shrink-0 text-sm text-text-muted">{t('cron.schedule.hoursUnit')}</span>
          <WeekdayPicker selected={schedule.weekdays ?? []} onToggle={toggleWeekday} />
        </div>
      )}

      {topMode === 'once' && schedule.kind === 'once' && (
        <div className="flex flex-nowrap items-center gap-2">
          <TimePicker
            value={schedule.time ?? ''}
            onChange={(v) => updateSchedule({ ...schedule, time: v })}
            className="w-32 shrink-0"
            placeholder={t('cron.schedule.selectTime') ?? undefined}
          />
          <DatePicker
            value={schedule.date ?? ''}
            onChange={(v) => updateSchedule({ ...schedule, date: v })}
            className="min-w-0 flex-1"
          />
        </div>
      )}

      {topMode === 'cronExpr' && (
        <div>
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
      )}
    </div>
  );
}
