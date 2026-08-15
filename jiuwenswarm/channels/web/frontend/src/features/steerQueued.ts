/**
 * Copy for a successful chat.steer ACK.
 *
 * `accepted` only means the text was queued. For `steer_queued` it still waits
 * for the next model call; for `follow_up_queued` it landed on a later attempt.
 * Without this note users read the ACK as "already applied to the tokens
 * streaming now".
 */

export type SteerLang = 'en' | 'zh';

/**
 * Short system note for an accepted disposition, or null when nothing extra
 * should be shown (unknown / other dispositions).
 */
export function formatSteerQueuedNote(
  language: SteerLang,
  disposition: string | undefined,
): string | null {
  if (disposition === 'steer_queued') {
    return language === 'zh'
      ? '插入已接受 — 将在下一步模型调用时生效。当前回复或工具可能先结束。'
      : 'Steer accepted — applies on the next model step. The current reply or tool may finish first.';
  }
  if (disposition === 'follow_up_queued') {
    return language === 'zh'
      ? '已排队到下一次尝试（未注入到当前回答）。'
      : 'Queued for the next attempt (not injected into the live answer).';
  }
  return null;
}
