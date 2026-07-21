// 7段式 cron 表达式校验（croniter 语法，second_at_beginning=True），原样搬自旧 CronPanel/index.tsx，
// i18n.errors.cron* key 沿用；周字段范围已按 croniter 实测结果修正（见下方说明）。

function isValidCronField(value: string, min: number, max: number, stepDivisor: number | null, allowQuestion: boolean = false, allowLast: boolean = false): { valid: boolean; error?: string } {
  if (value === '*') return { valid: true };
  if (allowQuestion && value === '?') return { valid: true };
  if (allowLast && value === 'L') return { valid: true };
  const parts = value.split(',');
  for (const part of parts) {
    if (part.includes('/')) {
      const [range, stepStr] = part.split('/');
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0) return { valid: false, error: getStepRangeError(min, max) };
      if (stepDivisor !== null && stepDivisor % step !== 0) return { valid: false, error: getStepRangeError(min, max) };
      if (range === '*') continue;
      const rangeValid = isValidCronRange(range, min, max);
      if (!rangeValid) return { valid: false, error: getFieldError(min, max) };
    } else if (part.includes('-')) {
      if (!isValidCronRange(part, min, max)) return { valid: false, error: getFieldError(min, max) };
    } else {
      const num = parseInt(part, 10);
      if (isNaN(num) || num < min || num > max) return { valid: false, error: getFieldError(min, max) };
    }
  }
  return { valid: true };
}

function getFieldError(min: number, max: number): string {
  if (min === 0 && max === 59) return 'cron.errors.cronSecondOrMinute';
  if (min === 0 && max === 23) return 'cron.errors.cronHour';
  if (min === 1 && max === 31) return 'cron.errors.cronDay';
  if (min === 1 && max === 12) return 'cron.errors.cronMonth';
  if (min === 0 && max === 6) return 'cron.errors.cronWeek';
  return 'cron.errors.cronFormat';
}

function getStepRangeError(min: number, max: number): string {
  if (min === 0 && max === 59) return 'cron.errors.cronSecondOrMinuteStep';
  if (min === 0 && max === 23) return 'cron.errors.cronHourStep';
  return getFieldError(min, max);
}

function isValidCronRange(range: string, min: number, max: number): boolean {
  const [startStr, endStr] = range.split('-');
  if (!startStr || !endStr) return false;
  const start = parseInt(startStr, 10);
  const end = parseInt(endStr, 10);
  if (isNaN(start) || isNaN(end)) return false;
  if (start < min || end > max || start > end) return false;
  return true;
}

// 周字段专用校验：在普通 isValidCronField 的基础上，额外接受"每月第几周星期几"用到的
// `{dow}#{1-5}`（第几周）和 `L{dow}`（最后一周）形状（见 scheduleConvert.ts / plan.md §2.3.8）。
// 这两种形状是"整段"匹配（不像普通数字/区间/步长那样可以被通用逻辑复用），所以单独判断。
function isValidWeekField(value: string): { valid: boolean; error?: string } {
  if (value === '*' || value === '?') return { valid: true };
  const parts = value.split(',');
  for (const part of parts) {
    // 含 # 或以 L 开头的段必须严格匹配"第几周"/"最后一周"形状，不能落到下面的通用数字解析——
    // 否则像 "1#9"（n 超出 1-5 合法范围）会被 parseInt 只认前面的 "1" 而误判成合法
    if (part.includes('#') || part.startsWith('L')) {
      const nthMatch = part.match(/^(\d)#([1-5])$/);
      if (nthMatch && Number(nthMatch[1]) <= 6) continue;
      const lastMatch = part.match(/^L(\d)$/);
      if (lastMatch && Number(lastMatch[1]) <= 6) continue;
      return { valid: false, error: 'cron.errors.cronWeek' };
    }
    const plainResult = isValidCronField(part, 0, 6, null);
    if (!plainResult.valid) return { valid: false, error: 'cron.errors.cronWeek' };
  }
  return { valid: true };
}

export function validateCronExpr(expr: string): { valid: boolean; error?: string } {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 7) {
    return { valid: false, error: 'cron.errors.cronFormat' };
  }
  const [second, minute, hour, day, month, week, year] = parts;
  const secondResult = isValidCronField(second, 0, 59, 60);
  if (!secondResult.valid) return { valid: false, error: secondResult.error };
  const minuteResult = isValidCronField(minute, 0, 59, 60);
  if (!minuteResult.valid) return { valid: false, error: minuteResult.error };
  const hourResult = isValidCronField(hour, 0, 23, 24);
  if (!hourResult.valid) return { valid: false, error: hourResult.error };
  // day 字段允许 'L'（月末最后一天，croniter 支持，见 plan.md §2.3.1 第3点）
  const dayResult = isValidCronField(day, 1, 31, null, true, true);
  if (!dayResult.valid) return { valid: false, error: dayResult.error };
  const monthResult = isValidCronField(month, 1, 12, null);
  if (!monthResult.valid) return { valid: false, error: monthResult.error };
  // 周字段实测范围是 0-6（0=周日...6=周六），不是 Quartz 的 1-7；旧文案/校验此前写反了。
  // 用专门的 isValidWeekField（支持 ?/*、普通值、以及"每月第几周"的 #N / L 形状）
  const weekResult = isValidWeekField(week);
  if (!weekResult.valid) return { valid: false, error: weekResult.error };
  if (year !== '*') {
    const yearNum = parseInt(year, 10);
    if (isNaN(yearNum) || yearNum < 1970 || yearNum > 2099) {
      return { valid: false, error: 'cron.errors.cronYear' };
    }
  }
  return { valid: true };
}
