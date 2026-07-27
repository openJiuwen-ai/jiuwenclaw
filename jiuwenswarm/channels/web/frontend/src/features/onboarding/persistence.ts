/**
 * 配置引导（onboarding）本机持久化。
 *
 * 走 localStorage，仅在本机生效。是否自动弹出「唯一」由 dismiss 开关控制：
 * - dismiss=1：用户勾选了「不再自动显示」，之后永久不再自动弹出。
 * - 未置位：每次启动初始化完成后都会自动弹出一次（意外关闭不影响下次弹出）。
 *
 * 命名沿用现有前缀风格（如 `jiuwenswarm_work_mode`）。
 */

const DISMISS_KEY = 'jiuwenswarm_onboarding_dismiss';

function readFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    // localStorage 不可用（隐私模式 / 禁用）时静默降级，视为未置位。
    return false;
  }
}

function writeFlag(key: string, value: boolean): void {
  try {
    if (value) {
      window.localStorage.setItem(key, '1');
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // 忽略写入失败，不影响引导流程。
  }
}

export function isOnboardingDismissed(): boolean {
  return readFlag(DISMISS_KEY);
}

export function setOnboardingDismissed(value: boolean): void {
  writeFlag(DISMISS_KEY, value);
}

/**
 * 是否应在初始化后自动弹出引导：仅当未被「不再自动显示」开关关闭时。
 * 意外关闭 / 未勾选开关都会保持自动弹出，直到用户显式勾选关闭。
 */
export function shouldAutoOpenOnboarding(): boolean {
  return !isOnboardingDismissed();
}
