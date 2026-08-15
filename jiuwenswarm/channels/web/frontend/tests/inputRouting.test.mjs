import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canSteer,
  isInterruptible,
  needsIdleDrain,
  resolveInputRoute,
} from '../node_modules/.cache/input-routing/features/inputRouting.js';

/** Idle agent session with a real session id and nothing running. */
const base = {
  mode: 'agent',
  isProcessing: false,
  isPaused: false,
  isGoalActive: false,
  queuePaused: false,
  goalArmed: false,
  hasSession: true,
  hasAttachments: false,
};

const ctx = (over = {}) => ({ ...base, ...over });

test('idle input is sent', () => {
  assert.equal(resolveInputRoute(ctx()), 'send');
  assert.equal(resolveInputRoute(ctx({ mode: 'code.normal' })), 'send');
});

test('team input is always ordinary interaction', () => {
  // Member-addressed and broadcast messages are routed server-side; the client
  // must not queue or interrupt them.
  assert.equal(resolveInputRoute(ctx({ mode: 'team' })), 'send');
  assert.equal(resolveInputRoute(ctx({ mode: 'team', isProcessing: true })), 'send');
  assert.equal(resolveInputRoute(ctx({ mode: 'team', isGoalActive: true })), 'send');
});

test('a streaming turn is steered, in agent and code alike', () => {
  assert.equal(resolveInputRoute(ctx({ isProcessing: true })), 'steer');
  assert.equal(resolveInputRoute(ctx({ mode: 'code.normal', isProcessing: true })), 'steer');
});

test('preferQueue forces follow-up while a turn is streaming', () => {
  assert.equal(
    resolveInputRoute(ctx({ isProcessing: true, preferQueue: true })),
    'queue',
  );
  // Code mode has no draining task queue UI. Alt+Enter must not park text in a
  // queue that never runs — fall through to steer like plain Enter.
  assert.equal(
    resolveInputRoute(
      ctx({ mode: 'code.normal', isProcessing: true, preferQueue: true }),
    ),
    'steer',
  );
  assert.equal(resolveInputRoute(ctx({ isProcessing: true })), 'steer');
});

test('a held session keeps its legacy supplement route', () => {
  // Paused is held, not running: queue and HITL own that state.
  assert.equal(resolveInputRoute(ctx({ isPaused: true })), 'queue');
  assert.equal(resolveInputRoute(ctx({ mode: 'code.normal', isPaused: true })), 'interrupt');
  // Paused wins even while a turn is nominally streaming.
  assert.equal(resolveInputRoute(ctx({ isProcessing: true, isPaused: true })), 'queue');
});

test('a paused queue asks before discarding it', () => {
  assert.equal(resolveInputRoute(ctx({ queuePaused: true })), 'queue-paused-prompt');
  // The prompt outranks a running turn: the user already made a decision about
  // the queue, and it must not be silently overridden.
  assert.equal(
    resolveInputRoute(ctx({ queuePaused: true, isProcessing: true })),
    'queue-paused-prompt',
  );
  // Only agent mode has that queue.
  assert.equal(resolveInputRoute(ctx({ queuePaused: true, mode: 'team' })), 'send');
});

test('goal arming on the welcome screen is sent, not queued', () => {
  assert.equal(resolveInputRoute(ctx({ goalArmed: true, hasSession: false })), 'send');
  // Arming only special-cases the welcome screen, where no session id exists
  // yet. With a session the message is ordinary input, so a streaming turn
  // steers it like any other.
  assert.equal(resolveInputRoute(ctx({ goalArmed: true, isProcessing: true })), 'steer');
  assert.equal(resolveInputRoute(ctx({ goalArmed: true, isPaused: true })), 'queue');
});

// --------------------------------------------------------- the Goal supplement

test('an ACTIVE goal makes ordinary input interruptible even when idle', () => {
  // The load-bearing case. A Goal spans several attempts, so between them
  // nothing is processing — yet a plain send would start a competing turn.
  const goal = ctx({ isGoalActive: true, isProcessing: false });
  assert.equal(isInterruptible(goal), true);
  assert.equal(resolveInputRoute(goal), 'queue');
});

test('an idle ACTIVE goal is never steered', () => {
  // Server-side lease ownership, not caution. handle_steer never calls
  // attach_output, so steering a Goal between attempts would queue the text as
  // a follow-up with nobody consuming the output. The legacy path's
  // attach_output() is what makes the sender the reader when idle.
  const goalIdle = ctx({ isGoalActive: true, isProcessing: false });
  assert.equal(canSteer(goalIdle), false);
  assert.equal(resolveInputRoute(goalIdle), 'queue');

  // But a Goal attempt that *is* streaming has a live round and a reader, so it
  // steers like any other running turn. The discriminator is isProcessing, not
  // the presence of a Goal.
  const goalRunning = ctx({ isGoalActive: true, isProcessing: true });
  assert.equal(canSteer(goalRunning), true);
  assert.equal(resolveInputRoute(goalRunning), 'steer');
});

test('team input is never steered; Team steer is server-ready but UI-deferred', () => {
  // Backend _process_team_steer / steer_leader exist; the composer stays on
  // ordinary Team interact until product enables leader steering from typing.
  assert.equal(canSteer(ctx({ mode: 'team', isProcessing: true })), false);
  assert.equal(resolveInputRoute(ctx({ mode: 'team', isProcessing: true })), 'send');
});

test('auto_harness input is never steered', () => {
  // AutoHarnessService drives the run; there is no DeepAgent interaction round
  // for send_input(STEER) to inject into. Keep the legacy interrupt/supplement path.
  assert.equal(canSteer(ctx({ mode: 'auto_harness', isProcessing: true })), false);
  assert.equal(
    resolveInputRoute(ctx({ mode: 'auto_harness', isProcessing: true })),
    'interrupt',
  );
});

test('attachments keep the legacy path instead of steer', () => {
  // handle_steer rejects attachments; TUI already routes them to supplement.
  assert.equal(
    canSteer(ctx({ isProcessing: true, hasAttachments: true })),
    false,
  );
  // Agent mode queues text+media together rather than interrupting.
  assert.equal(
    resolveInputRoute(ctx({ isProcessing: true, hasAttachments: true })),
    'queue',
  );
  assert.equal(
    resolveInputRoute(
      ctx({ mode: 'code.normal', isProcessing: true, hasAttachments: true }),
    ),
    'interrupt',
  );
});

test('a steer may jump ahead of a non-empty task queue', () => {
  // Immediacy is the point of steering: later text that adjusts the live round
  // reaches the model before earlier queued follow-ups. Documented and pinned
  // so the order is not accidental. hasQueuedWork is informational only today.
  assert.equal(
    canSteer(ctx({ isProcessing: true, hasQueuedWork: true })),
    true,
  );
  assert.equal(
    resolveInputRoute(ctx({ isProcessing: true, hasQueuedWork: true })),
    'steer',
  );
});

test('a paused queue is not steered around', () => {
  // The user paused the queue; a steer would bypass that decision silently.
  assert.equal(canSteer(ctx({ queuePaused: true, isProcessing: true })), false);
  assert.equal(
    resolveInputRoute(ctx({ queuePaused: true, isProcessing: true })),
    'queue-paused-prompt',
  );
});

test('goal-active input asks for the idle drain, ordinary queueing does not', () => {
  // The normal drain fires on terminal processing events. With an ACTIVE goal
  // and nothing running, that trigger never arrives and the queued message
  // would wait for a turn that is not coming.
  const goalIdle = ctx({ isGoalActive: true, isProcessing: false });
  assert.equal(needsIdleDrain(goalIdle, 'queue'), true);

  const busy = ctx({ isProcessing: true });
  assert.equal(needsIdleDrain(busy, 'queue'), false);

  // The nudge belongs to queueing only.
  assert.equal(needsIdleDrain(goalIdle, 'send'), false);
});

test('an agent with no session yet sends rather than queueing into nothing', () => {
  assert.equal(
    resolveInputRoute(ctx({ isProcessing: true, hasSession: false })),
    'send',
  );
});
