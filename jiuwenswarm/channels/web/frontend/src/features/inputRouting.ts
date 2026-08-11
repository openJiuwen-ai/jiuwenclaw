/**
 * Where a composed message goes when the user presses send.
 *
 * Extracted from InputArea's submit handler so the decision can be tested on
 * its own. This module is deliberately **behaviour-preserving**: it returns the
 * routes the component chooses today, including the ones steer intends to replace.
 * Switching a case to real steering changes lease ownership on the server, so
 * each one is moved deliberately, with its own reasoning — not as a side effect
 * of the extraction.
 */

export type InputRoute =
  /** Ordinary `chat.send`. */
  | 'send'
  /** `chat.steer` — text injected into the round that is streaming right now. */
  | 'steer'
  /** Append to the session task queue; drained when the agent goes idle. */
  | 'queue'
  /** Queue is paused: ask the user whether to clear it before sending. */
  | 'queue-paused-prompt'
  /** `chat.interrupt` with intent=supplement — the legacy steering stand-in. */
  | 'interrupt';

export interface InputRoutingContext {
  /** Resolved conversation mode: 'agent', 'team', 'code…', 'auto_harness'. */
  mode: string;
  /** A turn is streaming right now. */
  isProcessing: boolean;
  /** The agent is paused at a checkpoint. */
  isPaused: boolean;
  /** A Goal is ACTIVE for this session. */
  isGoalActive: boolean;
  /** The user paused the task queue. */
  queuePaused: boolean;
  /** The next message arms goal-setting instead of being ordinary input. */
  goalArmed: boolean;
  /** False on the welcome screen, before session.create has returned an id. */
  hasSession: boolean;
  /**
   * Composer carries ready media. Steering is text-only
   * (`attachments_not_supported`), so attachments keep the legacy path.
   */
  hasAttachments?: boolean;
  /**
   * Informational: the session task queue is non-empty. Steering intentionally
   * jumps the queue (immediacy is the point); this flag is for tests/docs, not
   * a `canSteer` exclusion.
   */
  hasQueuedWork?: boolean;
  /**
   * User asked to queue as a follow-up instead of steering (Web: Alt+Enter).
   * Only meaningful while `canSteer` would otherwise be true.
   */
  preferQueue?: boolean;
}

/**
 * True when input cannot simply be sent: something is already running, held, or
 * being accumulated toward a Goal.
 *
 * Note that `isGoalActive` belongs here even though no turn may be streaming.
 * An ACTIVE Goal keeps the interaction open between attempts, so a plain send
 * would start a competing turn.
 */
export function isInterruptible(ctx: InputRoutingContext): boolean {
  return ctx.isProcessing || ctx.isPaused || ctx.isGoalActive;
}

/**
 * Whether this input can be steered into the round that is running.
 *
 * Steering is only correct while a turn is genuinely streaming, and the reason
 * is server-side lease ownership. `handle_steer` never calls `attach_output` —
 * by design, so it cannot replace the stream the user is watching. That is safe
 * exactly when a stream already exists and already has a reader.
 *
 * The excluded states are excluded for concrete reasons, not caution:
 *
 * - **An ACTIVE Goal between attempts.** Nothing is streaming, so no reader
 *   exists. A steer would be queued as a follow-up on the Goal and nobody would
 *   consume its output. This input keeps riding the legacy path, whose
 *   `attach_output()` is what makes the sender the reader when idle.
 * - **Paused at a checkpoint.** The round exists but is held; queue and HITL
 *   behaviour own that state, and B2 owns changing it.
 * - **Team mode.** Leader steering is implemented server-side
 *   (`steer_leader` / `_process_team_steer`) but the composer stays on ordinary
 *   Team interact until product enables mid-round leader steers from typing.
 * - **`auto_harness`.** The run is driven by `AutoHarnessService`, not a
 *   DeepAgent interaction round, so `send_input(STEER)` has nothing to inject
 *   into. Keep the interrupt/supplement path that actually delivers.
 * - **Attachments.** `handle_steer` rejects them (`attachments_not_supported`);
 *   routing here would clear the composer and lose the file.
 *
 * A non-empty task queue does **not** block steering: immediacy is the point,
 * so a steer may overtake earlier queued follow-ups. That order is deliberate
 * and covered by tests — not accidental.
 */
export function canSteer(ctx: InputRoutingContext): boolean {
  if (ctx.mode === 'team' || ctx.mode === 'auto_harness') return false;
  if (ctx.hasAttachments) return false;
  if (!ctx.hasSession) return false;
  if (ctx.queuePaused && ctx.mode === 'agent') return false;
  return ctx.isProcessing && !ctx.isPaused;
}

export function resolveInputRoute(ctx: InputRoutingContext): InputRoute {
  // Goal arming on the welcome screen: there is no session yet, so the message
  // is handed up and landed once session.create returns an id.
  if (ctx.goalArmed && !ctx.hasSession) return 'send';

  // Team input is never interrupted or queued here. Member-addressed and
  // broadcast messages are ordinary Team interaction, routed server-side.
  if (ctx.mode === 'team') return 'send';

  const agent = ctx.mode === 'agent';

  // A paused queue is a user decision, so it outranks everything below: the
  // component asks whether to discard the queued work before sending.
  if (ctx.queuePaused && agent && ctx.hasSession) return 'queue-paused-prompt';

  // Explicit follow-up while a turn is streaming (Alt+Enter). Enter still steers;
  // this is the Codex Tab equivalent without stealing Tab from composer chips.
  // Agent-only: code mode has no task-queue drain UI, so preferQueue there would
  // clear the composer and strand the text.
  if (
    ctx.preferQueue &&
    agent &&
    canSteer({ ...ctx, preferQueue: false })
  ) {
    return ctx.hasSession ? 'queue' : 'send';
  }

  // A turn is streaming: there is a live round to steer, and it already owns a
  // reader. This is the only state where chat.steer is safe, because
  // handle_steer deliberately never claims the output lease.
  if (canSteer(ctx)) return 'steer';

  if (isInterruptible(ctx)) {
    // Reached when the session is held rather than running: paused at a
    // checkpoint, or an ACTIVE Goal between attempts. Both keep the legacy
    // route — see canSteer for why steering them would be wrong.
    // Also: attachments / auto_harness while processing land here instead of
    // steer, so delivery stays on a path that can carry them.
    if (agent) return ctx.hasSession ? 'queue' : 'send';
    // Everything else — code mode held, auto_harness, today — supplements
    // through an interrupt.
    return 'interrupt';
  }

  return 'send';
}

/**
 * Whether this route needs the idle-drain nudge.
 *
 * An ACTIVE Goal with nothing currently processing never hits the normal drain
 * trigger, which fires on terminal processing events. Without this the queued
 * message waits for a turn that is not coming.
 *
 * Steer removes this once queued input becomes a real steer; it is kept here so
 * the extraction changes nothing, and so the workaround is visible rather than
 * buried in a component.
 */
export function needsIdleDrain(ctx: InputRoutingContext, route: InputRoute): boolean {
  return route === 'queue' && ctx.isGoalActive && !ctx.isProcessing;
}
