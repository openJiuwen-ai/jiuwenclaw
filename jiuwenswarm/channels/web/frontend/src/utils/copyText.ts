interface ClipboardLike {
  writeText: (text: string) => Promise<void>;
}

export interface CopyTextOptions {
  clipboard?: ClipboardLike | null;
  /** 覆盖 execCommand 回退实现，便于在无 DOM 环境下测试 */
  legacyCopy?: (text: string) => boolean;
}

/** document.execCommand 已废弃，但仍是非安全上下文下唯一可用的回退方案 */
export function legacyCopyText(text: string): boolean {
  if (typeof document === 'undefined') return false;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand('copy');
  } finally {
    document.body.removeChild(textarea);
  }
}

/**
 * 复制文本，返回是否真正复制成功。
 * Clipboard API 在非 HTTPS、无权限或失焦时会抛异常，此时回退到 execCommand；
 * 两者都失败时必须返回 false，调用方不应展示「已复制」。
 */
export async function copyText(text: string, options: CopyTextOptions = {}): Promise<boolean> {
  if (!text) return false;

  const clipboard = options.clipboard !== undefined
    ? options.clipboard
    : (typeof navigator !== 'undefined' ? navigator.clipboard : null);
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // 继续走 execCommand 回退
    }
  }

  const legacyCopy = options.legacyCopy ?? legacyCopyText;
  try {
    return legacyCopy(text);
  } catch {
    return false;
  }
}
