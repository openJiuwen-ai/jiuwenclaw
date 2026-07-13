// 7段式 Quartz cron 表达式校验，原样搬自旧 CronPanel/index.tsx（i18n.errors.cron* key 沿用，未改动）。

function isValidCronField(value: string, min: number, max: number, stepDivisor: number | null, allowQuestion: boolean = false): { valid: boolean; error?: string } {
  if (value === '*') return { valid: true };
  if (allowQuestion && value === '?') return { valid: true };
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
  if (min === 1 && max === 7) return 'cron.errors.cronWeek';
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
  const dayResult = isValidCronField(day, 1, 31, null, true);
  if (!dayResult.valid) return { valid: false, error: dayResult.error };
  const monthResult = isValidCronField(month, 1, 12, null);
  if (!monthResult.valid) return { valid: false, error: monthResult.error };
  const weekResult = isValidCronField(week, 1, 7, null, true);
  if (!weekResult.valid) return { valid: false, error: weekResult.error };
  if (year !== '*') {
    const yearNum = parseInt(year, 10);
    if (isNaN(yearNum) || yearNum < 1970 || yearNum > 2099) {
      return { valid: false, error: 'cron.errors.cronYear' };
    }
  }
  return { valid: true };
}
