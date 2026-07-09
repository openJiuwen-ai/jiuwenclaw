# Generic Parallel Agent Dispatch

Use outside the Aidlc pipeline when multiple **independent** problems can be investigated or fixed concurrently without shared mutable state.

**Core principle:** One agent per independent domain; dispatch concurrently; integrate after all return.

## When to Use

**Use when:**

- Multiple unrelated failures (different test files, subsystems, or bugs)
- Each problem is understandable without context from the others
- Agents will not edit the same files or share fixtures/resources

**Do not use when:**

- Failures may share a root cause (fix one may fix others)
- You need full-system context first
- Still in exploratory debugging
- Agents would interfere (same files, env, or ordering deps)

## Pattern

1. **Partition** — Group work by domain (e.g. one test file or subsystem per group).
2. **Scope each agent** — Specific goal, touch boundaries, expected summary output.
3. **Dispatch in parallel** — One focused task per agent; do not pass your session history; supply only needed context.
4. **Integrate** — Read summaries, check for overlapping edits, run full verification.

## Prompt Checklist

Each worker prompt should be:

1. **Focused** — One clear domain (not "fix all tests").
2. **Self-contained** — Errors, test names, paths, constraints included.
3. **Bounded** — Explicit "do not change X" or "touch only Y".
4. **Verifiable output** — Require a short summary: root cause, files changed, command + exit code.

Example skeleton:

```markdown
Fix failing tests in `<path/to/file.test.ts>`:

Failures:
1. "<test name>" — <error or expectation>
2. ...

Constraints:
- Touch only this file and directly related production code
- Do not refactor unrelated modules

Return: root cause, changes made, verification command and result.
```

## Common Mistakes

| Avoid | Prefer |
|-------|--------|
| "Fix all the tests" | One file or subsystem per agent |
| "Fix the race condition" (no location) | Paste errors, test names, paths |
| No scope limits | Explicit touch / no-touch constraints |
| "Fix it" (no report) | Structured summary for integration |

## Integration

After workers return:

1. Read every summary.
2. Check for conflicting edits on the same paths.
3. Run the full test suite or agreed smoke command.
4. Spot-check — parallel agents can make correlated mistakes.

## Minimal Example

Three independent test files after a refactor → three parallel agents, one file each → integrate → full suite green. Time saved vs sequential investigation when domains truly do not overlap.
