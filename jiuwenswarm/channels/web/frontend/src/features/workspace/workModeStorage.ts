import type { WorkMode } from './projectTypes';

export const WORK_MODE_STORAGE_KEY = 'jiuwenswarm_work_mode';
export const DEFAULT_WORK_MODE: WorkMode = 'work';

/** 只接受已知的 work mode，其余（含 null / 历史脏数据）一律回退为默认值 */
export function normalizeWorkMode(raw: unknown): WorkMode {
  return raw === 'code' ? 'code' : DEFAULT_WORK_MODE;
}

/** 部分浏览器在禁用存储时，读取 window.localStorage 本身就会抛 SecurityError */
export function getWorkModeStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage ?? null;
  } catch (error) {
    console.error('Error accessing localStorage:', error);
    return null;
  }
}

/**
 * localStorage 在隐私模式、被禁用或跨域受限时，getItem/setItem 都可能直接抛异常。
 * 读失败时退回默认模式，写失败时忽略——两者都不应该影响 Work / Code 切换。
 */
export function readWorkMode(storage: Pick<Storage, 'getItem'> | null | undefined): WorkMode {
  if (!storage) return DEFAULT_WORK_MODE;
  try {
    return normalizeWorkMode(storage.getItem(WORK_MODE_STORAGE_KEY));
  } catch (error) {
    console.error('Error loading work mode from storage:', error);
    return DEFAULT_WORK_MODE;
  }
}

export function writeWorkMode(
  storage: Pick<Storage, 'setItem'> | null | undefined,
  workMode: WorkMode,
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(WORK_MODE_STORAGE_KEY, workMode);
    return true;
  } catch (error) {
    console.error('Error saving work mode to storage:', error);
    return false;
  }
}
