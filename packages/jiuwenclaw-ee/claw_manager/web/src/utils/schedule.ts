/**
 * 与后端 croniter 对齐：校验 5/6/7 段 cron，并检查各字段取值范围。
 * 段序：分 时 日 月 周 [秒] [年]（6/7 段时秒、年在末尾）。
 */

const FIELD_COUNTS = new Set([5, 6, 7]);

const MONTH_NAMES: Record<string, number> = {
  jan: 1,
  feb: 2,
  mar: 3,
  apr: 4,
  may: 5,
  jun: 6,
  jul: 7,
  aug: 8,
  sep: 9,
  oct: 10,
  nov: 11,
  dec: 12,
};

const DOW_NAMES: Record<string, number> = {
  sun: 0,
  mon: 1,
  tue: 2,
  wed: 3,
  thu: 4,
  fri: 5,
  sat: 6,
};

type FieldKind = 'minute' | 'hour' | 'day' | 'month' | 'dow' | 'second' | 'year';

const FIELD_KIND_BY_COUNT: Record<number, FieldKind[]> = {
  5: ['minute', 'hour', 'day', 'month', 'dow'],
  6: ['minute', 'hour', 'day', 'month', 'dow', 'second'],
  7: ['minute', 'hour', 'day', 'month', 'dow', 'second', 'year'],
};

const FIELD_RANGE: Record<FieldKind, [number, number]> = {
  minute: [0, 59],
  hour: [0, 23],
  day: [1, 31],
  month: [1, 12],
  dow: [0, 7],
  second: [0, 59],
  year: [1970, 2099],
};

function resolveToken(token: string, kind: FieldKind): number | null {
  const lower = token.toLowerCase();
  if (kind === 'month' && lower in MONTH_NAMES) return MONTH_NAMES[lower];
  if (kind === 'dow' && lower in DOW_NAMES) return DOW_NAMES[lower];
  if (!/^\d+$/.test(token)) return null;
  return Number(token);
}

function inRange(value: number, kind: FieldKind): boolean {
  const [min, max] = FIELD_RANGE[kind];
  return value >= min && value <= max;
}

/** 校验单个字段片段（支持 * ? 数字 名 范围 列表 /步长，以及日字段 L/W、周字段 #）。 */
function isValidCronField(part: string, kind: FieldKind): boolean {
  if (!part) return false;
  if (part === '*' || part === '?') return true;

  // 日：L / 15W；周：5#3 / L
  if (kind === 'day') {
    if (/^L$/i.test(part) || /^(\d{1,2})W$/i.test(part)) {
      const m = part.match(/^(\d{1,2})W$/i);
      if (m) {
        const day = Number(m[1]);
        return inRange(day, 'day');
      }
      return true;
    }
  }
  if (kind === 'dow') {
    if (/^L$/i.test(part)) return true;
    if (/^(\d|[A-Za-z]{3})#([1-5])$/i.test(part)) {
      const m = part.match(/^(\d|[A-Za-z]{3})#([1-5])$/i);
      if (!m) return false;
      const dow = resolveToken(m[1], 'dow');
      return dow !== null && inRange(dow, 'dow');
    }
  }

  for (const item of part.split(',')) {
    if (!item) return false;
    const [rangePart, stepPart] = item.split('/');
    if (stepPart !== undefined) {
      if (!/^\d+$/.test(stepPart) || Number(stepPart) < 1) return false;
    }
    if (!rangePart) return false;
    if (rangePart === '*' || rangePart === '?') continue;

    if (rangePart.includes('-')) {
      const [startRaw, endRaw] = rangePart.split('-');
      if (!startRaw || !endRaw || rangePart.split('-').length !== 2) return false;
      const start = resolveToken(startRaw, kind);
      const end = resolveToken(endRaw, kind);
      if (start === null || end === null) return false;
      if (!inRange(start, kind) || !inRange(end, kind) || start > end) return false;
      continue;
    }

    const value = resolveToken(rangePart, kind);
    if (value === null || !inRange(value, kind)) return false;
  }
  return true;
}

export function isValidHookSchedule(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  const parts = text.split(/\s+/);
  if (!FIELD_COUNTS.has(parts.length)) return false;
  const kinds = FIELD_KIND_BY_COUNT[parts.length];
  return parts.every((part, index) => isValidCronField(part, kinds[index]));
}
