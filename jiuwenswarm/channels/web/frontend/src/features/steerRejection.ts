/**
 * Turn a chat.steer ACK rejection into copy a person can act on.
 *
 * Mirrors channels/tui/frontend/src/core/steering.ts `formatSteerRejection`.
 * Kept pure so every backend token can be pinned in a unit test without the
 * socket hook.
 */

export type SteerRejectLanguage = 'zh' | 'en';

const MESSAGES: Record<string, { zh: string; en: string }> = {
  interaction_terminated: {
    zh: '当前回合已结束，未能插入这条消息',
    en: 'That round already finished, so this message was not injected',
  },
  no_active_round: {
    zh: '当前没有正在运行的回合',
    en: 'No round is running right now',
  },
  round_mismatch: {
    zh: '回合已结束或切换，未能插入这条消息',
    en: 'That round already finished or changed, so this steer was not injected',
  },
  attachments_not_supported: {
    zh: '插入消息不支持附件',
    en: 'Steering cannot carry attachments',
  },
  empty_query: {
    zh: '消息内容为空',
    en: 'Nothing to send',
  },
  no_agent_instance: {
    zh: '会话尚未就绪',
    en: 'The session is not ready yet',
  },
  not_active: {
    zh: '该 Team 未在运行',
    en: 'That Team is not running',
  },
  gate_closed: {
    zh: '该回合暂时不接受插入消息',
    en: 'That round is not accepting steering right now',
  },
  missing_target: {
    zh: '未能定位到目标 agent',
    en: 'Could not resolve the agent to steer',
  },
  steer_failed: {
    zh: '插入消息失败，原因未知',
    en: 'Steering failed, with no reason given',
  },
  exception: {
    zh: '插入消息时服务端出错',
    en: 'The server errored while steering',
  },
  unsupported_runtime: {
    zh: '该成员不支持插入消息',
    en: 'This member cannot take mid-round messages',
  },
  runner_failed: {
    zh: '插入消息未被运行时接受',
    en: 'The runtime did not accept the message',
  },
};

/**
 * Human-readable rejection for a steer ACK reason token.
 */
export function formatSteerRejection(
  language: SteerRejectLanguage,
  reason: string | undefined,
): string {
  const token = (reason ?? '').trim();
  const known = MESSAGES[token];
  if (known) return language === 'en' ? known.en : known.zh;
  if (token) {
    return language === 'en' ? `Steering was rejected: ${token}` : `插入消息被拒绝：${token}`;
  }
  return language === 'en' ? 'Steering was rejected' : '插入消息被拒绝';
}

/**
 * Append the draft-restore sentence only when the text was actually written back.
 */
export function withDraftRestoredNote(
  language: SteerRejectLanguage,
  message: string,
  restored: boolean,
): string {
  if (!restored) return message;
  return language === 'en'
    ? `${message} Your text is back in the composer.`
    : `${message} 文本已放回输入框。`;
}
