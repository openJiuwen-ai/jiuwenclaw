---
name: dispatch-parallel
description: Use when 2+ independent tasks have no shared state or sequential dependencies. In Aidlc, Leader loads this at G4/G5 when dev_plan.md or test_plan.md contains 2–3 non-overlapping PG-* groups. PG-* plan writing belongs to dev-planner (G3), not this skill.
metadata:
  short-description: Leader parallel dispatch for G4/G5 PG-* shards via partition_check.
  category: orchestration
  load_policy: on-gate
  depends_on: []
  gates:
    - G4
    - G5
---

# Parallel Dispatch

## Aidlc (G4/G5)

Pipeline stays stage-serial: G4 → G5 → G6. Parallelism only accelerates work **inside** G4 or G5.

**Read before dispatch:** [`references/aidlc-pipeline.md`](references/aidlc-pipeline.md)

| Step | Actor | Action |
|------|-------|--------|
| G3 | `dev-planner` | Writes `## 可并行组（G4/G5）` PG-* blocks — rules in `dev_principles` / `test_principles` |
| G4-P / G5-P | Leader | Read PG-* → write `doc/<module>/dispatch/manifest.yaml` → `partition_check.py --phase g4\|g5` |
| G4-W / G5-W | Leader | Parallel spawn `dev-coder` / `dev-tester`; each prompt includes Shard Contract |
| G4-I / G5-I | Leader | Collect summaries → `partition_check.py --phase integrate` → Gate verify |

**Use parallel when all are true:**

- Plan has **2–3** `PG-*` groups with non-empty `items` and `touch`
- Group `touch` paths do not overlap
- No cross-group critical-path dependency

**Fallback to serial:** no PG-*, one group only, `partition_check` failure, overlapping touch, unclear deps, or shared state.

**Script:** `scripts/partition_check.py`

Operational detail (manifest, Shard Contract, worker summary, safety rules) lives in `aidlc-pipeline.md`. Leader spawn steps also mirror `dev-leader/references/spawn.md`.

## Non-Aidlc

For ad-hoc parallel work (unrelated failures, isolated scopes, no plan PG-* blocks), read [`references/generic-parallel.md`](references/generic-parallel.md).
