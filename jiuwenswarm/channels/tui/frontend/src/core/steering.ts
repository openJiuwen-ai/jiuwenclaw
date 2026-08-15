// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/**
 * Pure decisions for steering the round that is running right now.
 *
 * Steering is text injected into the current round's context before its next
 * model call. It is not an interrupt: the stream the user is watching keeps
 * running. The TUI reached the same effect until now by sending
 * `chat.interrupt` with `intent: "supplement"`, which really did interrupt --
 * the false processing transitions that produced are what `chat.steer`
 * removes.
 *
 * These helpers hold no app state and touch no socket, so the routing split --
 * the actual risk in this change -- can be tested directly. Same reason
 * `features/inputRouting.ts` exists on the Web side.
 */

/**
 * Declared locally rather than imported so this stays a leaf module with no
 * runtime imports at all -- `ui/welcome.ts` and `core/event-handlers.ts` declare
 * it the same way. It also lets the test import this file on its own.
 */
type PreferredLanguage = "zh" | "en";

/** Where input typed while a round is busy should go. */
export type ActiveInputRoute =
  /** Inject into the running round via `chat.steer`. */
  | "steer"
  /** The legacy `chat.interrupt`/`intent=supplement` path. */
  | "supplement"
  /** Not busy -- the caller's ordinary `chat.send` path. */
  | "normal";

export interface ActiveInputState {
  readonly isProcessing: boolean;
  readonly isPaused: boolean;
  readonly isTeamMode: boolean;
  readonly hasAttachments: boolean;
}

/**
 * Decide where input typed during an active round goes.
 *
 * Three destinations, and each exclusion below is load-bearing rather than
 * conservative:
 *
 * - **Team mode** never steered here even before this change: a Team keeps its
 *   conversation on `chat.send`/interact so member-addressed and broadcast
 *   messages reach the right agent. Leader steering exists (section 4) but is
 *   reached deliberately, not by typing into a busy Team round.
 * - **Attachments** cannot be steered. `handle_steer` rejects them outright
 *   (`attachments_not_supported`) because the steering queue carries strings
 *   and the injection site joins them into one message. Routing them here
 *   would break input that works today, so they keep the legacy path.
 * - **Paused** has no in-flight model call to reach, so there is nothing for a
 *   steer to be injected before. It keeps the legacy path too.
 *
 * Force-queue while streaming (Web Alt+Enter / Codex Tab) is out of scope here:
 * the TUI has no taskQueue yet.
 */
export function resolveActiveInputRoute(state: ActiveInputState): ActiveInputRoute {
  if (!state.isProcessing && !state.isPaused) return "normal";
  if (state.isTeamMode) return "normal";
  if (state.isPaused || state.hasAttachments) return "supplement";
  return "steer";
}

/**
 * Say when an accepted steer actually takes effect.
 *
 * `accepted` only means queued. `steer_queued` still waits for the next model
 * call; `follow_up_queued` landed on a later attempt. Without this note the ACK
 * is easy to read as "already applied to the tokens streaming now".
 */
export function formatSteerQueuedNote(
  language: PreferredLanguage,
  disposition: string | undefined,
): string | null {
  if (disposition === "steer_queued") {
    return language === "zh"
      ? "插入已接受 — 将在下一步模型调用时生效。当前回复或工具可能先结束。"
      : "Steer accepted — applies on the next model step. The current reply or tool may finish first.";
  }
  if (disposition === "follow_up_queued") {
    return language === "zh"
      ? "已排队到下一次尝试（未注入到当前回答）。"
      : "Queued for the next attempt (not injected into the live answer).";
  }
  return null;
}

/**
 * Stable steer request ids a rail removed from a `chat.steer_applied` payload.
 *
 * Wire shape is normally `string[]`; `{ id }` objects are accepted defensively.
 */
export function listDroppedSteerIds(dropped: unknown): string[] {
  if (!Array.isArray(dropped)) return [];
  const ids: string[] = [];
  for (const item of dropped) {
    if (typeof item === "string" && item.trim()) {
      ids.push(item.trim());
      continue;
    }
    if (item && typeof item === "object" && typeof (item as { id?: unknown }).id === "string") {
      const id = (item as { id: string }).id.trim();
      if (id) ids.push(id);
    }
  }
  return ids;
}

/** Count drops; prefer parsed ids, else opaque array length. */
export function countDroppedSteers(dropped: unknown): number {
  const ids = listDroppedSteerIds(dropped);
  if (ids.length > 0) return ids.length;
  return Array.isArray(dropped) ? dropped.length : 0;
}

/**
 * Say that steering was acknowledged but never reached the model.
 *
 * `chat.steer_ack` only promises the text was queued. A rail can still remove
 * it before the next model call, and `chat.steer_applied` is the only event that
 * reports so. Without this the user has a message in their transcript that the
 * agent never saw, and no way to know.
 */
export function formatSteerDropped(language: PreferredLanguage, dropped: number): string {
  if (language === "en") {
    return dropped === 1
      ? "Your last message was not delivered to the model -- a rule removed it"
      : `${dropped} of your messages were not delivered to the model -- a rule removed them`;
  }
  return dropped === 1
    ? "上一条插入消息未送达模型（被规则移除）"
    : `有 ${dropped} 条插入消息未送达模型（被规则移除）`;
}

/**
 * Turn a `chat.steer_ack` rejection reason into something a person can act on.
 *
 * Every token below is one the backend actually emits -- they come from
 * `handle_steer` and `_process_team_steer`. An unknown token falls back to the
 * token itself rather than to a generic failure, so a reason added on the
 * server is visible here instead of silently flattened.
 */
export function formatSteerRejection(
  language: PreferredLanguage,
  reason: string | undefined,
): string {
  const token = (reason ?? "").trim();
  const messages: Record<string, { zh: string; en: string }> = {
    // The round ended between the keystroke and the frame arriving. Common and
    // benign: the answer the user wanted to redirect is already written.
    interaction_terminated: {
      zh: "当前回合已结束，未能插入这条消息",
      en: "That round already finished, so this message was not injected",
    },
    no_active_round: {
      zh: "当前没有正在运行的回合",
      en: "No round is running right now",
    },
    round_mismatch: {
      zh: "回合已结束或切换，未能插入这条消息",
      en: "That round already finished or changed, so this steer was not injected",
    },
    attachments_not_supported: {
      zh: "插入消息不支持附件",
      en: "Steering cannot carry attachments",
    },
    empty_query: {
      zh: "消息内容为空",
      en: "Nothing to send",
    },
    no_agent_instance: {
      zh: "会话尚未就绪",
      en: "The session is not ready yet",
    },
    not_active: {
      zh: "该 Team 未在运行",
      en: "That Team is not running",
    },
    gate_closed: {
      zh: "该回合暂时不接受插入消息",
      en: "That round is not accepting steering right now",
    },
    missing_target: {
      zh: "未能定位到目标 agent",
      en: "Could not resolve the agent to steer",
    },
    // The runtime failed without saying why, so neither can this. Better to
    // admit that than to invent a cause the user might act on.
    steer_failed: {
      zh: "插入消息失败，原因未知",
      en: "Steering failed, with no reason given",
    },
    exception: {
      zh: "插入消息时服务端出错",
      en: "The server errored while steering",
    },
    // This member's runtime cannot inject into a round at all -- a CLI-backed
    // leader, for instance. Deliberately not phrased as a timing problem: the
    // user was not too late, and telling them so would send them retrying
    // something that can never work.
    unsupported_runtime: {
      zh: "该成员不支持插入消息",
      en: "This member cannot take mid-round messages",
    },
    runner_failed: {
      zh: "插入消息未被运行时接受",
      en: "The runtime did not accept the message",
    },
  };
  const known = messages[token];
  if (known) return language === "en" ? known.en : known.zh;
  if (token) {
    return language === "en" ? `Steering was rejected: ${token}` : `插入消息被拒绝：${token}`;
  }
  return language === "en" ? "Steering was rejected" : "插入消息被拒绝";
}
