# Aidlc G4/G5 Parallel Dispatch

This reference binds `dispatch-parallel` to the Aidlc pipeline. The pipeline
remains stage-serial: G4 must finish before G5, and G5 must finish before G6.
Parallelism is only an optional acceleration path inside G4 and G5.

## Decision Rule

Use parallel dispatch only when all conditions are true:

- `dev_plan.md` or `test_plan.md` contains two or three `PG-*` groups.
- Each group has non-empty `items` and `touch` fields.
- Group `touch` paths do not overlap.
- No group contains critical-path work that depends on another group.

Fallback to serial dispatch when the plan has no parallel groups, only one
group, overlapping touch paths, unclear dependencies, or exploratory debugging.

## Plan PG-* Blocks (written at G3)

`dev-planner` writes `## 可并行组（G4）` in `dev_plan.md` and `## 可并行组（G5）` in `test_plan.md`.

**Authoritative format and rules:** `skills/dev-planner/references/dev_principles.md` (§4.2) and `test_principles.md`. Templates: `dev_plan_template.md` / `test_plan_template.md`. G3 validation: `check_plan.py`.

Leader and workers **consume** these blocks at G4/G5; they do not redefine PG-* syntax here.

## Manifest

Leader writes the shard manifest under `doc/<module>/dispatch/manifest.yaml`.
The manifest is intentionally simple so it can be inspected and validated
without extra dependencies.

```yaml
version: 1
module: user
phase: g4
mode: parallel
max_shards: 3
shards:
  - id: S1
    items: [3.1, 3.2]
    touch_allow:
      - src/pkg/infra/gateway.py
    worker_summary: doc/user/dispatch/g4-S1-summary.md
  - id: S2
    items: [4.1, 4.2]
    touch_allow:
      - src/pkg/core/service.py
    worker_summary: doc/user/dispatch/g4-S2-summary.md
```

Use `phase: g5` and `g5-Sn-summary.md` for tester shards.

## Leader Workflow

### G4-P / G5-P: Partition

1. Read this reference (and `skills/dispatch-parallel/SKILL.md` if needed for entry context).
2. Read the plan `PG-*` block.
3. Write `doc/<module>/dispatch/manifest.yaml`.
4. Run `partition_check.py --phase g4` or `--phase g5`.
5. If validation fails, fallback to the existing serial single-agent dispatch.

### G4-W / G5-W: Worker Dispatch

Dispatch one same-name worker per shard:

- G4 uses `dev-coder`.
- G5 uses `dev-tester`.
- Do not dispatch G5 workers while G4 workers are still running.
- Do not mix G4 and G5 work in one prompt.

Each worker prompt must include this shard contract:

```markdown
## Shard Contract

- shard_id: S1
- phase: g4
- items: [3.1, 3.2]
- touch_allow:
  - src/pkg/infra/gateway.py
- touch_forbid:
  - src/pkg/core/service.py
- worker_summary: doc/user/dispatch/g4-S1-summary.md
```

The worker may edit only the listed plan items and `touch_allow` paths, plus its
own summary file. If it discovers a necessary change outside the shard, it must
stop and report the mismatch to Leader instead of broadening scope.

### G4-I / G5-I: Integrate

Leader declares G4/G5 PASS only after all workers return and integration passes:

1. Read every worker summary.
2. Write `doc/<module>/dispatch/g4-integration.md` or `g5-integration.md`: plan items still `[ ]` outside dispatch scope, reason, owner Gate.
3. Run `partition_check.py --phase integrate`.
4. Re-run the existing Gate verify command from `dev-leader/references/gates.md`.
5. Check `reviewer_plan_check.py status --plan dev|test --format json`.
6. Run a focused smoke or full verification command when the change risk requires it.

Worker self-reports are evidence, not Gate decisions.

Uncheck plan items outside scope do not block G4/G5. Scope items must all be `[x]` before PASS.

## Worker Summary

Each worker writes:

```markdown
# G4 Shard S1 Summary

## Scope
- items: 3.1, 3.2
- touch_allow: src/pkg/infra/gateway.py

## Changes
- <changed behavior and files>

## Verification
- `<command>` → exit <code>; <short result>

## Done
- item ids completed in plan

## Deferred in shard
- item id, reason; or none

## Out of shard
- items not dispatched; do not mark in plan
```

Use `G5` for tester summaries.

## Safety Rules

- `max_shards` defaults to 3.
- Plan files are shared; workers may only mark their own checklist items.
- The manifest and summaries live under `doc/<module>/dispatch/`; temporary; Leader deletes `dispatch/` at **G7a**, same as `review/`.
- A shard may not modify another shard's files.
- G4/G5 parallel dispatch does not change G6 review or G7 Git packaging; **do not commit** `dispatch/` contents.
