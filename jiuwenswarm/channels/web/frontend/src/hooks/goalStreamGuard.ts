/**
 * Goal 与普通问答共流时，前端用单一 currentStreamId 承接 chat.delta/final。
 * command.goal set/resume 下发 goal.snapshot 后，若不封存当前气泡，后续 Goal 输出
 * 会继续写入作文等前一轮气泡，最终被 chat.final 整段覆盖（#2671）。
 *
 * 仅在 set/resume 的 snapshot 上封存；get/pause/clear/completed 更新不应打断当前流。
 */
export function shouldFreezeChatStreamOnGoalSnapshot(payload: {
  action?: unknown;
}): boolean {
  const action = String(payload.action ?? '').trim().toLowerCase();
  return action === 'set' || action === 'resume';
}
