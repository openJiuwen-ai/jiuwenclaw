import assert from "node:assert/strict";
import {
  applyPatchSegmentEntries,
  findLivePatchSegmentTargetIndex,
} from "../dist/core/history-parser.js";

const entries = [
  { kind: "user", id: "u1", sessionId: "s", content: "hello", at: "2026-01-01T00:00:00.000Z" },
  {
    kind: "assistant",
    id: "a1",
    sessionId: "s",
    content: "I will create a hello_world.py file.\n\n",
    at: "2026-01-01T00:00:01.000Z",
    eventType: "chat.final",
  },
  {
    kind: "assistant",
    id: "a2",
    sessionId: "s",
    content:
      "Permission was denied, so the operation was not performed. You noted: do a math calculator",
    at: "2026-01-01T00:00:02.000Z",
    eventType: "chat.final",
    finalMode: "patch_segment",
  },
];

const patched = applyPatchSegmentEntries(entries);
assert.equal(patched.length, 2);
assert.equal(patched[1].kind, "assistant");
assert.ok(patched[1].content.includes("do a math calculator"));
assert.ok(!patched[1].content.includes("I will create"));

const streamingIntent = [
  { kind: "user", id: "u1", sessionId: "s", content: "hello", at: "2026-01-01T00:00:00.000Z" },
  {
    kind: "assistant",
    id: "a1",
    sessionId: "s",
    content: "I will create a hello_world.py file.\n\n",
    at: "2026-01-01T00:00:01.000Z",
    streaming: true,
    eventType: "chat.delta",
  },
];

assert.equal(findLivePatchSegmentTargetIndex(streamingIntent), 1);
