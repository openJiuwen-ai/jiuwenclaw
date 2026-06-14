/** 策略 template_ref 槽位与序列化（与后端 normalize_template_ref 对齐）。 */

export const TEMPLATE_REF_SLOTS = [
  'default_model',
  'video_model',
  'audio_model',
  'vision_model',
  'skill_whitelist',
  'extension_config',
  'service_config',
] as const;

export type TemplateRefSlot = (typeof TEMPLATE_REF_SLOTS)[number];

export type TemplateRefMap = Record<string, string[]>;

export interface TemplateRefSlotRow {
  key: string;
  slot: string;
  refs: string[];
}

function normalizeSlotRefs(raw: unknown): string[] {
  if (raw == null) return [];
  if (typeof raw === 'string') {
    const text = raw.trim();
    return text ? [text] : [];
  }
  if (Array.isArray(raw)) {
    return raw
      .map((item) => (item == null ? '' : String(item).trim()))
      .filter(Boolean);
  }
  const text = String(raw).trim();
  return text ? [text] : [];
}

/** 将 API 返回的 template_ref 规范为 { slot: string[] }。 */
export function normalizeTemplateRefFromApi(
  raw: Record<string, string | string[]> | null | undefined,
): TemplateRefMap {
  if (!raw || typeof raw !== 'object') return {};
  const out: TemplateRefMap = {};
  for (const [key, value] of Object.entries(raw)) {
    const slot = key.trim();
    if (!slot) continue;
    const refs = normalizeSlotRefs(value);
    if (refs.length) out[slot] = refs;
  }
  return out;
}

/** 将编辑态行列表序列化为 API 请求体。 */
/** 是否至少配置了一条非空模板引用（用于表单必填校验）。 */
export function hasTemplateRefContent(map: TemplateRefMap): boolean {
  return Object.values(map).some((refs) => refs.some((r) => r.trim()));
}

export function serializeTemplateRef(rows: TemplateRefSlotRow[]): TemplateRefMap {
  const out: TemplateRefMap = {};
  for (const row of rows) {
    const slot = row.slot.trim();
    if (!slot) continue;
    const refs = row.refs.map((r) => r.trim()).filter(Boolean);
    if (refs.length) out[slot] = refs;
  }
  return out;
}

export function templateRefRowsFromMap(map: TemplateRefMap): TemplateRefSlotRow[] {
  return Object.entries(map).map(([slot, refs]) => ({
    key: slot,
    slot,
    refs: refs.length ? [...refs] : [''],
  }));
}

export function newTemplateRefRow(slot = 'default_model'): TemplateRefSlotRow {
  return {
    key: `new-${Math.random().toString(36).slice(2, 9)}`,
    slot,
    refs: [''],
  };
}

const USER_EXPR = /^\$\{user::([^}]+)\}(?:\s+or\s+(.+))?$/i;
const GROUP_EXPR = /^\$\{group::([^}]+)\}(?:\s+or\s+(.+))?$/i;

export type RefExprMode = 'template' | 'custom' | 'user' | 'group';

export interface ParsedRefExpr {
  mode: RefExprMode;
  templateId: string;
  custom: string;
  userId: string;
  groupId: string;
  fallbackId: string;
}

export function buildRefExpr(state: ParsedRefExpr): string {
  const fb = state.fallbackId.trim();
  if (state.mode === 'user') {
    const id = state.userId.trim();
    if (!id) return '';
    const base = `\${user::${id}}`;
    return fb ? `${base} or ${fb}` : base;
  }
  if (state.mode === 'group') {
    const id = state.groupId.trim();
    if (!id) return '';
    const base = `\${group::${id}}`;
    return fb ? `${base} or ${fb}` : base;
  }
  if (state.mode === 'template') return state.templateId.trim();
  return state.custom.trim();
}

export function parseRefExpr(value: string): ParsedRefExpr {
  const text = value.trim();
  const empty: ParsedRefExpr = {
    mode: 'custom',
    templateId: '',
    custom: text,
    userId: '',
    groupId: '',
    fallbackId: '',
  };
  if (!text) return { ...empty, mode: 'template' };

  if (!text.includes('${')) {
    return {
      mode: 'template',
      templateId: text,
      custom: '',
      userId: '',
      groupId: '',
      fallbackId: '',
    };
  }

  const userMatch = text.match(USER_EXPR);
  if (userMatch) {
    return {
      mode: 'user',
      templateId: '',
      custom: '',
      userId: userMatch[1].trim(),
      groupId: '',
      fallbackId: (userMatch[2] ?? '').trim(),
    };
  }

  const groupMatch = text.match(GROUP_EXPR);
  if (groupMatch) {
    return {
      mode: 'group',
      templateId: '',
      custom: '',
      userId: '',
      groupId: groupMatch[1].trim(),
      fallbackId: (groupMatch[2] ?? '').trim(),
    };
  }

  return {
    mode: 'custom',
    templateId: '',
    custom: text,
    userId: '',
    groupId: '',
    fallbackId: '',
  };
}
