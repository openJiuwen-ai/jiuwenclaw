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

/** 各槽位至多一条引用：默认/视频/音频/视觉模型、服务配置。 */
export const SINGLE_VALUE_TEMPLATE_REF_SLOTS = new Set<string>([
  'default_model',
  'video_model',
  'audio_model',
  'vision_model',
  'service_config',
]);

export function isSingleValueTemplateRefSlot(slot: string): boolean {
  return SINGLE_VALUE_TEMPLATE_REF_SLOTS.has(slot.trim());
}

/** 校验单值槽位引用条数；返回首个违规槽位名，无违规则返回 null。 */
export function findSingleValueTemplateRefViolation(
  map: TemplateRefMap,
): string | null {
  for (const [slot, refs] of Object.entries(map)) {
    if (!isSingleValueTemplateRefSlot(slot)) continue;
    const nonEmpty = refs.filter((r) => r.trim()).length;
    if (nonEmpty > 1) return slot;
  }
  return null;
}

export type TemplateRefSlot = (typeof TEMPLATE_REF_SLOTS)[number];

export type TemplateRefMap = Record<string, string[]>;

export interface TemplateRefSlotRow {
  key: string;
  slot: string;
  refs: string[];
}

function dedupePreserveOrder(refs: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const ref of refs) {
    if (seen.has(ref)) continue;
    seen.add(ref);
    out.push(ref);
  }
  return out;
}

function normalizeSlotRefs(raw: unknown): string[] {
  if (raw == null) return [];
  if (typeof raw === 'string') {
    const text = raw.trim();
    return text ? [text] : [];
  }
  if (Array.isArray(raw)) {
    return dedupePreserveOrder(
      raw
        .map((item) => (item == null ? '' : String(item).trim()))
        .filter(Boolean),
    );
  }
  const text = String(raw).trim();
  return text ? [text] : [];
}

function clampSingleValueSlotRefs(slot: string, refs: string[]): string[] {
  if (!isSingleValueTemplateRefSlot(slot) || refs.length <= 1) return refs;
  return [refs[0]];
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
    const refs = clampSingleValueSlotRefs(slot, normalizeSlotRefs(value));
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
    const refs = clampSingleValueSlotRefs(
      slot,
      dedupePreserveOrder(row.refs.map((r) => r.trim()).filter(Boolean)),
    );
    if (refs.length) out[slot] = refs;
  }
  return out;
}

export function templateRefRowsFromMap(map: TemplateRefMap): TemplateRefSlotRow[] {
  return Object.entries(map).map(([slot, refs]) => {
    const normalized = clampSingleValueSlotRefs(slot, refs.length ? [...refs] : ['']);
    return {
      key: slot,
      slot,
      refs: normalized.length ? normalized : [''],
    };
  });
}

export function newTemplateRefRow(slot = 'default_model'): TemplateRefSlotRow {
  return {
    key: `new-${Math.random().toString(36).slice(2, 9)}`,
    slot,
    refs: [''],
  };
}

const OR_SPLIT_RE = /\s+or\s+/i;
const USER_SEGMENT_RE = /^\$\{user::([^}]+)\}$/i;
const GROUP_SEGMENT_RE = /^\$\{group::([^}]+)\}$/i;

const BOT_SEGMENT_RE = /^\$\{bot::([^}]+)\}$/i;

export type RefSegmentMode = 'template' | 'user' | 'group' | 'bot';

export interface RefSegment {
  mode: RefSegmentMode;
  templateId: string;
  userId: string;
  groupId: string;
  botId: string;
}

export interface ParsedRefChain {
  segments: RefSegment[];
}

export function newRefSegment(mode: RefSegmentMode = 'template'): RefSegment {
  return {
    mode,
    templateId: '',
    userId: '',
    groupId: '',
    botId: '',
  };
}

function parseRefSegment(text: string): RefSegment | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const userMatch = trimmed.match(USER_SEGMENT_RE);
  if (userMatch) {
    return {
      mode: 'user',
      templateId: '',
      userId: userMatch[1].trim(),
      groupId: '',
      botId: '',
    };
  }
  const groupMatch = trimmed.match(GROUP_SEGMENT_RE);
  if (groupMatch) {
    return {
      mode: 'group',
      templateId: '',
      userId: '',
      groupId: groupMatch[1].trim(),
      botId: '',
    };
  }
  const botMatch = trimmed.match(BOT_SEGMENT_RE);
  if (botMatch) {
    return {
      mode: 'bot',
      templateId: '',
      userId: '',
      groupId: '',
      botId: botMatch[1].trim(),
    };
  }
  if (!trimmed.includes('${')) {
    return {
      mode: 'template',
      templateId: trimmed,
      userId: '',
      groupId: '',
      botId: '',
    };
  }
  return null;
}

function refSegmentToString(segment: RefSegment): string {
  if (segment.mode === 'user') {
    const id = segment.userId.trim();
    return id ? `\${user::${id}}` : '';
  }
  if (segment.mode === 'group') {
    const id = segment.groupId.trim();
    return id ? `\${group::${id}}` : '';
  }
  if (segment.mode === 'bot') {
    const id = segment.botId.trim();
    return id ? `\${bot::${id}}` : '';
  }
  return segment.templateId.trim();
}

export function buildRefChain(chain: ParsedRefChain): string {
  const parts = chain.segments.map(refSegmentToString).filter(Boolean);
  return parts.join(' or ');
}

export function parseRefChain(value: string): ParsedRefChain {
  const text = value.trim();
  if (!text) {
    return { segments: [newRefSegment('template')] };
  }

  const parts = text.split(OR_SPLIT_RE);
  const segments = parts.map((part) => parseRefSegment(part) ?? newRefSegment('template'));
  return { segments: segments.length ? segments : [newRefSegment('template')] };
}
