# Compliance Checklist

> Read in **Stage 6** after the validator script passes. The script catches structural / frontmatter / section-presence / cross-file errors. This checklist catches **judgment calls the script cannot automate** — the responsibility-attribution tests that decide whether new fields belong in the spec, plus content-quality checks.

## What the validator script catches (do not duplicate here)

The validator handles:
- Five-file structure (SKILL.md / roles/ / workflow.md / bind.md / dependencies.yaml)
- Frontmatter required fields (`name`, `description`, `version`, `kind: team-skill`, `roles[]`)
- Each `roles[].id` has matching `roles/<id>.md`
- Each role file has the 5 mandatory sections
- Each Identity starts with `> *"..."*`
- `workflow.md` has Overview/Detailed Steps/Acceptance Criteria + at least one mermaid block
- `bind.md` has Resource Constraints / Behavioral Constraints / Failure Handling
- `dependencies.yaml` has both `skills:` and `tools:` segments
- Every `roles[].skills` and `roles[].tools` declared in SKILL.md appears in dependencies.yaml

If the script reports failures, fix them first. **This checklist starts where the script ends.**

---

## Part A: Responsibility-attribution tests

For each new section / field / rule you introduce beyond the templates, run these tests. Any candidate that fails one or more tests should be rejected or moved out of the spec.

### Test 1 — Consumer-and-time check (run BEFORE any redundancy claim)

Before declaring two things "redundant", verify their **consumers and timing** are the same.

- `description` (system-prompt-time, decides trigger) vs `## Workflow` (already-triggered Agent reading body) → different consumers → NOT redundant.
- `## Workflow` in SKILL.md (prose summary) vs `workflow.md` (full mermaid + steps) → same consumer (Leader), different depth → NOT redundant; SKILL.md links to workflow.md.
- mermaid in SKILL.md body vs mermaid in workflow.md → SAME consumer (reader visualizing flow) → REDUNDANT → keep only in workflow.md.

### Test 2 — Registry / store concern?

If the candidate field is about discovery, recommendation, download counts, tier, popularity → **registry concern, not Teamskill spec**. Examples: `tier`, `downloads`, `category` for browsing.

### Test 3 — Runtime / framework concern?

If the candidate field is about how the framework wires dependencies, dispatches teammates, or implements messaging → **framework concern, not Teamskill spec**. Examples: `teammate_mode`, `install_command`, message format. The Teamskill MAY declare a **preference order** for inter-member communication (e.g., direct peer-to-peer > shared blackboard > Leader-relay), but MUST NOT mandate a specific mechanism.

### Test 4 — Tutorial / docs concern?

If the candidate content is long-form examples, anti-patterns, common pitfalls — and it's NOT actionable at runtime → **docs concern**. Move to `examples/` directory or external README. **Exception**: short bullet-style guidance in templates is fine.

### Test 5 — Already in git / filesystem metadata?

If the candidate field is mtime, author, commit history, file size → **already in git / FS**. Do not duplicate.

### Test 6 — Already defined elsewhere?

After Test 1 (consumer-and-time check), if two locations carry the same content for the same consumer at the same time → keep one, delete the other.

### Test 7 — Internal implementation leaking?

If the candidate field exposes session IDs, internal trace handles, framework-specific identifiers → **leak**. Hide it.

### Test 8 — Machine-verifiable?

If the field is purely human-written prose with no machine-checkable correctness → **risky**. Keep only if the value-to-verifiability ratio is high. Most natural-language sections are accepted because their value is high; new fields should justify themselves.

### Test 9 — Future-use safety net

Before deleting a long-unused field, consider whether a planned future field would give it new purpose. Leave it as **optional** if uncertain. Do not judge a field in isolation when the surrounding spec is still evolving.

---

## Part B: Content-quality manual checks (judgement calls)

The validator confirms structure exists. These checks confirm the structure is **good**:

### B1. Identity mottos — anti-convergence

Read all role mottos as a list. Apply this test:

> *"If I shuffled these mottos and asked a reader to assign each motto to a role file, could they do it correctly without context?"*

If yes → mottos are distinctive. If no → roles will converge in output. Rewrite the offending mottos using [role-design.md](role-design.md) § Anti-convergence techniques.

### B2. Boundary completeness

For each role, read its `Boundary` and ask:

- **Forbidden**: does the list mention every sibling role's territory? (For A/B patterns where overlap is the risk.)
- **Mandatory**: does the list prevent silent under-delivery? (For roles that might output "looks fine" or skip slices.)

A `Boundary` with only Forbidden tends to produce empty findings. A `Boundary` with only Mandatory tends to produce role overlap. Both halves work as opposing forces.

### B3. Inline Persona self-containment

For each role's `## Inline Persona for Teammate`, do this test:

> *"If I copy-pasted this prompt verbatim into a fresh agent with no other context, could it produce a valid output that matches the schema?"*

Common gaps:
- Persona references "the team" or "the Leader" without explaining → opaque to a teammate
- Persona mentions methodology (STRIDE, SCQA) without defining it → teammate might not know it
- Placeholders like `{INPUT}` are present but not labeled → unclear what to fill in
- Output format described in prose but not shown as a literal template → teammate guesses structure

### B4. Workflow gates have teeth

For each step in `workflow.md > Detailed Steps`, verify:

- The step has a **quality gate** declared (pass criteria + fail action).
- The fail action is **specific** ("retry once, then mark missing in report" — not "handle failure").
- For C-pattern stage transitions, `bind.md` declares "Stage N+1 MUST refuse to start if gate FAIL".

A workflow without enforceable gates is just prose; the team will skip steps under load.

### B5. Bind numbers are real

Check `bind.md > Resource Constraints` numbers against the workflow:

- `max_parallel_teammates` ≥ the largest parallel fan-out in the mermaid diagram
- `total_wall_clock_budget` ≥ sum of sequential stage budgets
- `total_token_budget` ≥ rough estimate of (per-role tokens × N roles)

Numbers that don't match the workflow are theater.

### B6. Failure handling covers both required scenarios

`Failure Handling` MUST cover:

- (a) **Teammate failure** — timeout, malformed output, retry policy, missing-output reporting
- (b) **Input over-scale degradation** — what triggers degraded mode, what the degraded mode does, how the user is warned

Re-read your `Failure Handling` and confirm both are present. Most authoring errors omit (b).

### B7. dependencies.yaml has user-facing `purpose` lines

Each `skills[]` and `tools[]` entry has a `purpose` field that is **human-readable** and **explains the consequence of missing**:

- Good: `purpose: locate symbols and call sites in the diff`
- Bad: `purpose: code-search dependency`
- Bad: `purpose: required for the skill`

The `purpose` field is shown to the user during pre-flight checks when a dependency is missing. Write it for that audience.

### B8. SKILL.md description is a real trigger

Read the `description` and ask:

- Does it state **WHEN** to use? ("Use when: ...")
- Does it state **WHEN NOT** to use? ("DO NOT use for: ...")
- Does it list 1–3 **trigger phrases** that the agent should recognize?
- Is it 1–5 lines (system prompts hard-cap descriptions)?

A description that omits "when not to use" causes the skill to over-trigger. A description without trigger phrases causes the skill to under-trigger.

---

## Part C: Cross-Teamskill consistency (only when publishing multiple together)

When publishing a batch of Teamskills as a coherent set:

- [ ] Naming convention: all Teamskills end with `-team` suffix
- [ ] Frontmatter `version` consistent across the batch
- [ ] Description structure follows the same template ("Use when..." / "DO NOT use for..." / "Triggers...")
- [ ] No two Teamskills overlap in trigger conditions (they would compete in the trigger system)
- [ ] All Teamskills produce structurally similar Final Reports (tiered, schema-based) so users learn one mental model

Cross-Teamskill consistency is a usability concern, not a per-skill compliance rule.

---

## Final sign-off ritual

Once all above checks pass, write a 2-line sign-off in the conversation (not in any file):

```
Teamskill: <name>
Pattern: <A | B | C | A+B | B+C | C+A | C+B+A | Debate>
Roles: <count> (<list of role ids>)
Validator: PASS
Manual checks: PASS (all responsibility-attribution and content-quality tests)
Justification: <1-line Stage 0 reason this team beats single-agent>
```

This forces a final articulation of the Teamskill's reason to exist.
