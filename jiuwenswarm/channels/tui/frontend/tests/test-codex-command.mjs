import assert from "node:assert/strict";

import { createCodexCommand } from "../dist/core/commands/builtins/codex.js";

const USER_CODE = "CODE-CANARY-7A91";
const VERIFICATION_URL = "https://auth.openai.com/codex/device";

function createHarness(responses, copyToClipboard = async () => true) {
  const requests = [];
  const items = [];
  const entries = [];
  const responseQueues = new Map(
    Object.entries(responses).map(([method, values]) => [method, [...values]]),
  );
  const ctx = {
    sessionId: "session-codex-command-test",
    entries,
    request: async (method, params, timeoutMs) => {
      requests.push({ method, params, timeoutMs });
      const queue = responseQueues.get(method);
      assert.ok(queue?.length, `Unexpected request: ${method}`);
      return queue.shift();
    },
    addItem: (item) => {
      items.push(item);
      entries.push(item);
    },
  };
  const command = createCodexCommand({ copyToClipboard });
  return { command, ctx, requests, items, entries };
}

function serializedHistory(harness) {
  return JSON.stringify({ items: harness.items, entries: harness.entries });
}

{
  const copied = [];
  const harness = createHarness(
    {
      "provider.codex.auth.start": [
        {
          available: true,
          connected: false,
          state: "waiting_for_user",
          operation_id: "operation-connect",
          verification_url: VERIFICATION_URL,
          user_code: USER_CODE,
        },
      ],
    },
    async (text) => {
      copied.push(text);
      return true;
    },
  );

  await harness.command.action(harness.ctx, "connect");

  assert.deepEqual(copied, [USER_CODE]);
  assert.equal(serializedHistory(harness).includes(USER_CODE), false);
  assert.match(serializedHistory(harness), /copied to clipboard \(hidden\)/);
  assert.deepEqual(
    harness.requests.map(({ method }) => method),
    ["provider.codex.auth.start"],
  );
}

{
  const harness = createHarness(
    {
      "provider.codex.auth.start": [
        {
          available: true,
          connected: false,
          state: "waiting_for_user",
          operation_id: "operation-clipboard-failure",
          verification_url: VERIFICATION_URL,
          user_code: USER_CODE,
        },
      ],
      "provider.codex.auth.cancel": [{ available: true, connected: false, state: "not_connected" }],
    },
    async () => false,
  );

  await harness.command.action(harness.ctx, "connect");

  assert.equal(serializedHistory(harness).includes(USER_CODE), false);
  assert.deepEqual(harness.requests, [
    { method: "provider.codex.auth.start", params: {}, timeoutMs: 45_000 },
    {
      method: "provider.codex.auth.cancel",
      params: { operation_id: "operation-clipboard-failure" },
      timeoutMs: undefined,
    },
  ]);
  assert.match(serializedHistory(harness), /login was canceled/);
}

{
  const harness = createHarness({
    "provider.codex.auth.start": [
      {
        available: true,
        connected: false,
        state: "waiting_for_user",
        operation_id: "operation-missing-handoff",
        verification_url: VERIFICATION_URL,
      },
    ],
    "provider.codex.auth.cancel": [{ available: true, connected: false, state: "not_connected" }],
  });

  await harness.command.action(harness.ctx, "connect");

  assert.deepEqual(
    harness.requests.map(({ method, params }) => ({ method, params })),
    [
      { method: "provider.codex.auth.start", params: {} },
      {
        method: "provider.codex.auth.cancel",
        params: { operation_id: "operation-missing-handoff" },
      },
    ],
  );
  assert.match(serializedHistory(harness), /did not return a device login handoff/);
}

{
  const harness = createHarness({
    "provider.codex.auth.status": [
      {
        available: true,
        connected: false,
        state: "waiting_for_user",
        operation_id: "operation-status",
      },
    ],
    "provider.codex.auth.cancel": [{ available: true, connected: false, state: "not_connected" }],
    "provider.codex.auth.logout": [{ available: true, connected: false, state: "not_connected" }],
  });

  await harness.command.action(harness.ctx, "status");
  await harness.command.action(harness.ctx, "cancel");
  await harness.command.action(harness.ctx, "logout");

  assert.deepEqual(
    harness.requests.map(({ method, params }) => ({ method, params })),
    [
      { method: "provider.codex.auth.status", params: {} },
      {
        method: "provider.codex.auth.cancel",
        params: { operation_id: "operation-status" },
      },
      { method: "provider.codex.auth.logout", params: {} },
    ],
  );
  assert.match(serializedHistory(harness), /Codex state: waiting_for_user/);
  assert.match(serializedHistory(harness), /Codex login canceled/);
  assert.match(serializedHistory(harness), /Codex disconnected/);
}

console.log("codex command tests passed");
