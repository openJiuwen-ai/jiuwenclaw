import assert from "node:assert/strict";

import {
  countDroppedSteers,
  formatSteerDropped,
  formatSteerQueuedNote,
  formatSteerRejection,
  listDroppedSteerIds,
  resolveActiveInputRoute,
} from "../dist/core/steering.js";

// --- resolveActiveInputRoute: the three-way split ------------------------------
//
// This is the risky part of the change. Input typed while a round is busy used
// to have one destination; now it has three, and each test below pins one
// exclusion so it cannot be "simplified" away later.

const idle = { isProcessing: false, isPaused: false, isTeamMode: false, hasAttachments: false };

assert.equal(resolveActiveInputRoute(idle), "normal", "idle input is an ordinary send");

assert.equal(
  resolveActiveInputRoute({ ...idle, isProcessing: true }),
  "steer",
  "a genuinely streaming, text-only Agent round is the one case that steers",
);

// Team keeps its conversation on interact so member-addressed and broadcast
// messages reach the right agent. This held before the change and must still.
assert.equal(
  resolveActiveInputRoute({ ...idle, isProcessing: true, isTeamMode: true }),
  "normal",
  "Team input never steers from the composer",
);
assert.equal(
  resolveActiveInputRoute({ ...idle, isPaused: true, isTeamMode: true }),
  "normal",
  "a paused Team is still ordinary Team interaction",
);
assert.equal(
  resolveActiveInputRoute({
    ...idle,
    isProcessing: true,
    isTeamMode: true,
    hasAttachments: true,
  }),
  "normal",
  "Team wins over the attachment rule -- it is not a supplement either",
);

// A paused round has no in-flight model call for a steer to precede.
assert.equal(
  resolveActiveInputRoute({ ...idle, isPaused: true }),
  "supplement",
  "paused input keeps the legacy path",
);
assert.equal(
  resolveActiveInputRoute({ ...idle, isProcessing: true, isPaused: true }),
  "supplement",
  "paused wins when both flags are set",
);

// handle_steer rejects attachments outright, so routing them to steer would
// break input that works today.
assert.equal(
  resolveActiveInputRoute({ ...idle, isProcessing: true, hasAttachments: true }),
  "supplement",
  "attachments keep the legacy path",
);
assert.equal(
  resolveActiveInputRoute({ ...idle, isPaused: true, hasAttachments: true }),
  "supplement",
  "paused plus attachments is still the legacy path",
);

// --- formatSteerRejection -----------------------------------------------------

assert.equal(
  formatSteerRejection("en", "interaction_terminated"),
  "That round already finished, so this message was not injected",
);
assert.equal(formatSteerRejection("zh", "interaction_terminated"), "当前回合已结束，未能插入这条消息");

// An unknown token surfaces itself rather than collapsing into a generic
// failure, so a reason added on the server stays visible here.
assert.match(formatSteerRejection("en", "brand_new_reason"), /brand_new_reason/);
assert.match(formatSteerRejection("zh", "brand_new_reason"), /brand_new_reason/);

// No reason at all still produces something a person can read.
assert.equal(formatSteerRejection("en", undefined), "Steering was rejected");
assert.equal(formatSteerRejection("en", "   "), "Steering was rejected");
assert.equal(formatSteerRejection("zh", undefined), "插入消息被拒绝");

// Guard: every rejection token the backend can actually emit has a written
// message. Sources, verified 2026-08-07:
//   interface_deep.handle_steer   -> empty_query, attachments_not_supported,
//                                    no_agent_instance, interaction_terminated
//   SendInputResult.rejected      -> no_active_round
//   agent-core steer_leader       -> not_active, no_active_round, gate_closed,
//                                    unsupported_runtime
//   Runner.steer_agent_team       -> missing_target
//   jiuwenswarm team_manager      -> exception, runner_failed
//   interface._team_steer_ack     -> steer_failed (fallback when the runtime
//                                    fails without saying why)
//
// A token missing from the map still renders -- it falls through to the echo
// branch -- so this guard keeps the fallback from quietly becoming the normal
// case as the backend grows reasons.
//
// The list is hand-maintained, which is its weakness: `unsupported_runtime` and
// `runner_failed` were both live on the backend for a while and absent here, and
// this guard did not notice because nobody had added them. Grep the two
// steer_leader implementations and _team_steer_ack when touching this.
const BACKEND_REASON_TOKENS = [
  "empty_query",
  "attachments_not_supported",
  "no_agent_instance",
  "interaction_terminated",
  "no_active_round",
  "not_active",
  "gate_closed",
  "missing_target",
  "exception",
  "steer_failed",
  "unsupported_runtime",
  "runner_failed",
  "round_mismatch",
];
for (const token of BACKEND_REASON_TOKENS) {
  for (const lang of ["en", "zh"]) {
    const message = formatSteerRejection(lang, token);
    assert.ok(message.length > 0, `${token} (${lang}) produced no message`);
    assert.ok(
      !message.includes(token),
      `${token} (${lang}) fell through to the echo branch -- it needs a written message`,
    );
  }
}

// --- formatSteerQueuedNote ----------------------------------------------------
//
// accepted ≠ applied to the tokens streaming now.

assert.match(formatSteerQueuedNote("en", "steer_queued"), /next model step/i);
assert.match(formatSteerQueuedNote("zh", "steer_queued"), /下一步模型调用/);
assert.match(formatSteerQueuedNote("en", "follow_up_queued"), /next attempt/i);
assert.equal(formatSteerQueuedNote("en", "turn_queued"), null);
assert.equal(formatSteerQueuedNote("en", undefined), null);

// --- formatSteerDropped -------------------------------------------------------
//
// This message exists because the ACK promises only that the text was queued.
// A steer can be acknowledged, shown in the transcript, and still never read.

assert.match(formatSteerDropped("en", 1), /not delivered to the model/);
assert.doesNotMatch(
  formatSteerDropped("en", 1),
  /1 of your messages/,
  "one dropped steer reads as singular, not '1 of your messages'",
);
assert.match(formatSteerDropped("en", 3), /^3 of your messages/);
assert.match(formatSteerDropped("zh", 1), /未送达模型/);
assert.match(formatSteerDropped("zh", 4), /4 条/);

assert.deepEqual(listDroppedSteerIds(["a", { id: "b" }, { noid: 1 }]), ["a", "b"]);
assert.equal(countDroppedSteers([{ id: "x" }]), 1);
assert.equal(countDroppedSteers("abcdef"), 0);

console.log("steering.test.mjs: all assertions passed");
