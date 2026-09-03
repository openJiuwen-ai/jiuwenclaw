---
id: eternal-conversation-memory
name: Eternal Conversation Memory
confidence: confirmed
last_updated: 2026-09-03
read_when: "Changing eternal conversation, context replacement, durable memory, or session adapter cleanup."
---

# Eternal Conversation Memory

## Flow

1. On beta2, Web accepts plain text `/persist <first task>` on the new-conversation page, strips the prefix, and sends `persist_session=true` in the first `session.create`. No Web slash-command picker is required or added. TUI and controlled IM channels accept the same creation-time command. Controlled IM allocates a new mapped Session and forwards the stripped task; digital-avatar first tasks bypass relevance rewriting but still receive the physical sender identity envelope. AgentServer stores the boolean in Session metadata and returns it through create/list/restore. The value is immutable afterward; each chat turn derives the internal `eternal_conversation_enabled` adapter key from this authoritative metadata.
2. Work/Code Adapter configures the common Rail and disables overlapping semantic MemoryRail behavior. Code project/coding memory remains independent.
3. The Rail records the post-ContextProcessor model envelope, user/task events, tool calls/results, and final result into an append-only cursor/hash-chain Raw History. Foreground history is a byte-equivalent mirror. Large fields are content-addressed by canonical JSON digest, and a cursor/hash-linked `search.jsonl` plus task-local indexes provide bounded mechanical views without becoming semantic sources of truth.
4. At a completed natural-user-task boundary, the Session coordinator freezes at most four complete task boundaries and starts background Agent 1 (Extractor). The model input carries the latest Final Visible Context plus a non-semantic Raw event/response/hash ledger; complete evidence remains unchanged in Raw History.
5. Extractor alone performs semantic selection, Snapshot/UT maintenance, exact-name UT creation, and conflict resolution. Harness validates structure/evidence/revisions and atomically publishes Pending UTs through the vendored dynamic-memory-cli.
6. Background Agent 2 (Builder) reviews only whether the frozen Pending batch is structurally buildable; it cannot reject semantic omissions or rewrite meaning. Harness then performs deterministic Pending-to-Built construction. Search returns both states.
7. At a later foreground boundary, Harness prepares a projection in `BEFORE_INVOKE`, then replaces old working context in `ON_USER_MESSAGE` after ModelContext initialization and before the new query is admitted. Replacement requires published `covered_through == requested_cursor`; events added while extraction runs remain in the next uncovered range. Existing ContextProcessor behavior runs afterward, and the Rail records its final model-visible result.

## Ownership and Recovery

- Coordinator identity is `(feature root, Session id)` and outlives Web/TUI Adapter cleanup.
- `persist_session` participates in the `create_token` idempotency signature but not the prewarm `WarmKey`. Prewarm creates no metadata and performs no model turn, so the claimed Agent can enable the Rail from the real Session value without duplicating warm slots.
- Raw History is authoritative; foreground mirror and cursor state can only recover by replaying byte-equivalent durable records.
- Any cursor/hash/session divergence fails closed.
- On a later Session turn after restart, persisted requested cursor and Pending rows reschedule Extractor/Builder work.
- Large foreground/background inputs are content-addressed JSON blobs; JSONL retains path/hash/bytes metadata so exact model input is reconstructable without duplicate growth. Search/task views carry canonical cursor/hash backlinks and are recoverable from Raw History.
- Extractor/Builder terminal errors are durable Session state. The next natural-task boundary clears and retries them; acceptance barriers fail fast when a worker has exited unsuccessfully. Structural retry prompts carry the exact prior invalid JSON and precise field/index/length failure so semantic compression remains an Extractor decision.
- A structural Builder rejection is audited and promoted to the same durable fail-fast state; Pending remains published and unchanged until a later Builder retry.
- Final proof generation replays canonical cursor/previous-hash/event-hash validation and checks every search/task derived-view backlink before matrix acceptance.
- Automatic search prefetch failures are logged without blocking the foreground task. Non-dictionary background history payloads are wrapped before persistence; user-visible creation failures omit internal exception details.
- Foreground and background model selection follows the active request model. Interaction resumes do not start another natural task. Group identities survive digital-avatar rewriting; Feishu prefers a resolved display name and falls back to an Open ID suffix.

## Evidence

- Regression scope: `tests/unit_tests/agentserver/rails/test_eternal_conversation_rail.py`, creation/metadata/warm-pool tests, and Gateway identity, persist-routing, and Feishu-name tests.
- Real model: `scripts/acceptance/eternal_conversation_200.py` drives Web/TUI × Work/Code with an explicitly selected configured model and records revisions/cursors/full evidence. No 200-task real-model acceptance has been completed on this beta2 migration; earlier results from other branches are not beta2 evidence.
- Migration provenance and local verification: `docs/zh/persist-session-beta2-migration.md`.
