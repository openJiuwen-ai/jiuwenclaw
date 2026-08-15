/**
 * Reading a `chat.steer_applied` payload.
 *
 * The event says which steers reached model context (`applied`) and which a rail
 * removed (`dropped`). It exists because the ACK cannot answer that question:
 * `accepted` means the text was queued, and a rail can still drop it before the
 * model sees it. Without this event a client shows a message in the transcript
 * that the agent never read.
 *
 * Kept as a pure function, out of the socket hook, so the malformed-payload cases
 * can be tested -- these values come off the wire, so "dropped is a list" is an
 * assumption rather than a guarantee.
 */

export interface SteerAppliedPayload {
  readonly applied?: unknown;
  readonly dropped?: unknown;
}

/**
 * Stable steer request ids a rail removed.
 *
 * Wire shape is normally `string[]`, but object markers with `{ id }` are
 * accepted so a future payload change does not silently report zero drops.
 */
export function listDroppedSteerIds(
  payload: SteerAppliedPayload | undefined | null,
): string[] {
  const dropped = payload?.dropped;
  if (!Array.isArray(dropped)) return [];
  const ids: string[] = [];
  for (const item of dropped) {
    if (typeof item === 'string' && item.trim()) {
      ids.push(item.trim());
      continue;
    }
    if (item && typeof item === 'object' && typeof (item as { id?: unknown }).id === 'string') {
      const id = (item as { id: string }).id.trim();
      if (id) ids.push(id);
    }
  }
  return ids;
}

/**
 * How many steers a rail removed, or 0 when the payload does not say.
 *
 * Prefer parsed ids when any exist; otherwise fall back to array length for
 * opaque drop markers. Anything that is not an array counts as zero rather
 * than as an error. A malformed payload should leave the transcript alone, not
 * announce a drop that may not have happened -- and the alternative, throwing
 * inside a socket handler, would take down the events that follow it.
 */
export function countDroppedSteers(payload: SteerAppliedPayload | undefined | null): number {
  const ids = listDroppedSteerIds(payload);
  if (ids.length > 0) return ids.length;
  return Array.isArray(payload?.dropped) ? payload!.dropped!.length : 0;
}

/**
 * Transcript bubble id stamped when the steer ACK lands.
 * Must stay aligned with `sendSteer` and with dropped ids from the core
 * (`SteeringInput.id` == steer `request_id`).
 */
export function steerUserBubbleId(steerRequestId: string): string {
  return `user-steer-${steerRequestId}`;
}

/**
 * Whether this event is worth telling the user about.
 *
 * Silent on the happy path by design: the user's message is already in the
 * transcript from the ACK, and confirming every steer would be noise. Only a
 * drop is news.
 */
export function shouldReportSteerApplied(
  payload: SteerAppliedPayload | undefined | null,
): boolean {
  return countDroppedSteers(payload) > 0;
}
