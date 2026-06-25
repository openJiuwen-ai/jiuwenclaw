/**
 * 请求扩展字段（透传给后端）的设置状态管理。
 *
 * 包含两类：
 *   - 固定字段：user_id / group_id / bot_id
 *   - 自定义键值对：key 从白名单选择，value 任意
 *
 * 持久化到 localStorage；保存时 dispatch DOM 事件触发 WS 重连，新设置生效。
 */

import { create } from 'zustand';

const STORAGE_KEY = 'jiuwenclaw_ext_settings';
const RECONNECT_EVENT = 'jiuwenclaw:ws-reconnect-request';
/** user_id / group_id / bot_id 变更时派发，触发前端新建 Web 会话（与「新建会话」同等效果）。 */
export const EXT_ROUTING_CHANGED_EVENT = 'jiuwenclaw:ext-routing-changed';

/** 自定义键值对：key 限制在白名单内，value 由用户填写。 */
export interface ExtCustomKV {
  key: string;
  value: string;
}

/** 持久化在 localStorage 的形态。 */
export interface ExtSettingsSnapshot {
  userId: string;
  groupId: string;
  botId: string;
  customKVs: ExtCustomKV[];
}

/** 自定义 key 白名单（与后端 forward_headers 对齐）。 */
export const EXT_CUSTOM_KEY_WHITELIST = [
  'Authorization',
  'X-Tenant-Id',
  'X-Trace-Id',
  'X-Locale',
] as const;

export type ExtCustomKey = (typeof EXT_CUSTOM_KEY_WHITELIST)[number];

const EMPTY_SNAPSHOT: ExtSettingsSnapshot = {
  userId: '',
  groupId: '',
  botId: '',
  customKVs: [],
};

function loadFromStorage(): ExtSettingsSnapshot {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...EMPTY_SNAPSHOT };
    const parsed = JSON.parse(raw);
    return {
      userId: typeof parsed.userId === 'string' ? parsed.userId : '',
      groupId: typeof parsed.groupId === 'string' ? parsed.groupId : '',
      botId: typeof parsed.botId === 'string' ? parsed.botId : '',
      customKVs: Array.isArray(parsed.customKVs)
        ? parsed.customKVs
            .filter(
              (kv: unknown) =>
                kv !== null &&
                typeof kv === 'object' &&
                typeof (kv as ExtCustomKV).key === 'string' &&
                typeof (kv as ExtCustomKV).value === 'string',
            )
            .map((kv: ExtCustomKV) => ({ key: kv.key, value: kv.value }))
        : [],
    };
  } catch (error) {
    console.error('[extSettings] load failed:', error);
    return { ...EMPTY_SNAPSHOT };
  }
}

function saveToStorage(snapshot: ExtSettingsSnapshot): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch (error) {
    console.error('[extSettings] save failed:', error);
  }
}

function routingFieldsEqual(
  a: Pick<ExtSettingsSnapshot, 'userId' | 'groupId' | 'botId'>,
  b: Pick<ExtSettingsSnapshot, 'userId' | 'groupId' | 'botId'>,
): boolean {
  return a.userId === b.userId && a.groupId === b.groupId && a.botId === b.botId;
}

interface ExtSettingsState extends ExtSettingsSnapshot {
  /** 是否有未保存的草稿改动（仅在 Modal 打开期间使用）。当前实现：Modal 内部用 useState 管理草稿，store 只保存"已生效"快照。 */
  /** 用一份新快照覆盖并触发 WS 重连。 */
  saveAndApply: (next: ExtSettingsSnapshot) => void;
}

export const useExtSettingsStore = create<ExtSettingsState>((set) => ({
  ...loadFromStorage(),
  saveAndApply: (next) => {
    const prev = useExtSettingsStore.getState();
    const sanitized: ExtSettingsSnapshot = {
      userId: next.userId.trim(),
      groupId: next.groupId.trim(),
      botId: next.botId.trim(),
      customKVs: next.customKVs
        .filter((kv) => kv.key.trim() && kv.value.trim())
        .map((kv) => ({ key: kv.key.trim(), value: kv.value.trim() })),
    };
    const routingChanged = !routingFieldsEqual(prev, sanitized);
    saveToStorage(sanitized);
    set(sanitized);
    if (typeof window !== 'undefined') {
      if (routingChanged) {
        window.dispatchEvent(new Event(EXT_ROUTING_CHANGED_EVENT));
      }
      window.dispatchEvent(new Event(RECONNECT_EVENT));
    }
  },
}));

/** 把当前快照拍平成 WS query 字段（webClient.buildWsUrl 调用）。 */
export function extSettingsToQueryFields(
  snapshot: Pick<ExtSettingsSnapshot, 'userId' | 'groupId' | 'botId' | 'customKVs'>,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (snapshot.userId) out.user_id = snapshot.userId;
  if (snapshot.groupId) out.group_id = snapshot.groupId;
  if (snapshot.botId) out.bot_id = snapshot.botId;
  for (const kv of snapshot.customKVs) {
    if (kv.key && kv.value) {
      out[kv.key] = kv.value;
    }
  }
  return out;
}

/** 企业策略路由字段（写入 ``chat.send`` params，与 enterprise_config_chat 对齐）。 */
export function extSettingsToRoutingParams(
  snapshot: Pick<ExtSettingsSnapshot, 'userId' | 'groupId' | 'botId'>,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (snapshot.userId) out.user_id = snapshot.userId;
  if (snapshot.groupId) out.group_id = snapshot.groupId;
  if (snapshot.botId) out.bot_id = snapshot.botId;
  return out;
}
