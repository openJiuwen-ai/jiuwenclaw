/** 开发环境下输出 history 恢复链路日志，便于排查分页/空消息等问题 */
export function logHistoryRestore(phase: string, detail?: Record<string, unknown>): void {
  if (!import.meta.env.DEV) {
    return;
  }
  if (detail) {
    console.info(`[history.restore] ${phase}`, detail);
  } else {
    console.info(`[history.restore] ${phase}`);
  }
}
