export type ClawhubTokenIntent = 'save' | 'clear' | 'none';

export interface ClawhubTokenAction {
  intent: ClawhubTokenIntent;
  /** 实际提交给后端的值；clear 时为空字符串 */
  token: string;
  canSubmit: boolean;
}

/**
 * 清空输入框是删除已保存 Token 的唯一入口，因此输入为空且已配置过 Token 时
 * 仍然允许提交（intent=clear）；从未配置过才真正无操作。
 */
export function resolveClawhubTokenAction(input: string, hasToken: boolean): ClawhubTokenAction {
  const token = String(input ?? '').trim();
  if (token) {
    return { intent: 'save', token, canSubmit: true };
  }
  if (hasToken) {
    return { intent: 'clear', token: '', canSubmit: true };
  }
  return { intent: 'none', token: '', canSubmit: false };
}
